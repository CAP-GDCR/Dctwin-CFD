"""
BridgeBCRegistry: maps sanitized STL solid names → OpenFOAM boundary condition specs.

This module is the handoff mechanism between the CFDProjectImporter (which reads
the .cfdrun manifest from STLGeneratorForOpenFoam) and the FieldGenerator (which writes
OpenFOAM 0/ field files).

The registry is an in-memory object — it is built by
STLGenRunner._build_bridge_bc() and passed to FieldGenerator via constructor
injection. It does NOT persist to disk. (Previously, a `model/bridge_bc.json`
file was written and re-read within the same process; that indirection was
removed in the 2026-04-11 refactor. See spec:
docs/superpowers/specs/2026-04-11-cfdrun-rename-bridge-bc-inmemory-design.md)

Supported bc_type values:
    "wall"              - noSlip U, zeroGradient T
    "wall_baffle"       - noSlip U, zeroGradient T, fixedFluxPressure p_rgh (both sides)
    "perforated_baffle" - cyclic/porousBafflePressure (sigma field controls behavior):
                          sigma=0: degenerates to wall_baffle (no-slip)
                          0<sigma<1: cyclic U/T + porousBafflePressure p_rgh
                          sigma=1: degenerates to pure cyclic (all fields)
    "acu_supply"        - flowRateInletVelocity U, fixedValue T (temperature)
    "acu_return"        - flowRateOutletVelocity U, zeroGradient T
    "fan_inlet"         - flowRateOutletVelocity U, zeroGradient T
    "fan_outlet"        - flowRateInletVelocity U, fixedValue T (computed from power)
    "nonfan_fixed_temp" - noSlip U, fixedValue T (temperature)
    "nonfan_heat_flux"  - noSlip U, externalWallHeatFluxTemperature T (power-based heat flux)
    "fixedTemperature"  - noSlip U, fixedValue T (temperature)
    "heat_source_flux"  - noSlip U, externalWallHeatFluxTemperature T (heat_flux W/m²)
"""
import logging
import re
from typing import Dict, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)


def _sanitize(name: str) -> str:
    """Sanitize a solid name to a valid OpenFOAM patch name (same logic as HTML tool)."""
    return re.sub(r'[^A-Za-z0-9_]', '_', name)


class BCSpec(BaseModel):
    """Boundary condition specification for a single STL solid patch."""
    bc_type: str = "wall"
    temperature: Optional[float] = None   # Celsius; used for supply/fixedTemp patches
    flow_rate: Optional[float] = None     # m³/s; used for supply/return/fan patches
    power: Optional[float] = None         # W; used for fan_outlet temperature computation
    sigma: Optional[float] = None         # porosity coefficient: rack leakage_zone cellZones, and derived-from-openAreaPct for perforated_baffle wall/cyclic/porous classifier
    perforated: Optional["PerforatedSpec"] = None  # replaces sigma for perforated baffles
    heat_flux: Optional[float] = None     # W/m²; used for heat_source_flux BC
    physical_flow_area: Optional[float] = None  # m²; real server-face area for U_phys derivation (fan_outlet/fan_inlet only)
    cooling_capacity: Optional[float] = None      # W (max sensible cooling)
    min_supply_temp: Optional[float] = None        # Celsius (derived: coolant_temp + 6°C)
    set_point_temp: Optional[float] = None         # Celsius (supply air set point)
    coolant_temp: Optional[float] = None           # Celsius (chilled water inlet temperature)
    return_patch_name: Optional[str] = None        # paired return patch for exprFixedValue
    cooling_model_mode: Optional[str] = None           # "fixedTemp" | "linearCapacity" | "tableCapacity"
    rated_return_temp: Optional[float] = None           # Celsius (rated return air temperature for linear model)
    cooling_performance_table: Optional[list] = None    # [{returnTemp: float, capacity: float}, ...] for table model
    timeVarying: Optional[dict] = None             # time-varying schedule from .cfdrun JSON
    surface_area: Optional[float] = None           # m²; total surface area (heat sources: W→W/m²)


# ---------- Perforated-plate physics (Idelchik §8.2 eq. 8-4) ----------
# Shape factor φ scales the entrance-loss coefficient 0.707. Circle is
# the reference (sharp-edged holes); rect/diamond have slightly stronger
# vena-contracta losses at the corners.
_SHAPE_PHI = {"circle": 1.00, "rect": 1.10, "diamond": 1.15}


class PerforatedSpec(BaseModel):
    """Physical description of a perforated plate — the *only* params the
    user sets. Conversion to (D, I, length) for OpenFOAM's
    porousBafflePressure happens in perforated_to_porous_baffle().
    """
    shape: str = "circle"           # 'circle' | 'rect' | 'diamond'
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0


def perforated_to_porous_baffle(spec: PerforatedSpec) -> tuple[float, float, float]:
    """Map (shape, σ) → (D, I, length) using Idelchik's thin sharp-edged
    perforated-plate formula:

        K = [1 + φ·0.707·(1-σ)^0.5 - σ]² / σ²

    For thin HVAC plates (ℓ/d_h < 0.5) the viscous term in OpenFOAM's
    porousBafflePressure is negligible, so D=0 and the entire resistance
    lives on I. `length` is the OF normalization (not plate thickness).

    Returns (D [1/m²], I [dimensionless], length [m]).
    """
    sigma = max(1e-4, min(0.9999, spec.openAreaPct / 100.0))
    if spec.shape in _SHAPE_PHI:
        phi = _SHAPE_PHI[spec.shape]
    else:
        logger.warning(f"Unknown perforated shape {spec.shape!r}; defaulting to circle (φ=1.0)")
        phi = 1.0
    K = (1.0 + phi * 0.707 * (1.0 - sigma) ** 0.5 - sigma) ** 2 / sigma ** 2
    return (0.0, K, 1.0)


class BridgeBCRegistry:
    """Maps sanitized patch solid names → BCSpec for the bridge pipeline.

    Usage in importer (STLGenRunner):
        registry = BridgeBCRegistry()
        registry.register("acu1_supply_patch_1", BCSpec(bc_type="acu_supply", temperature=15.0, flow_rate=4.0))
        self._bridge_registry = registry

    Usage in field generator (FieldGenerator):
        # registry is passed in via constructor as `bridge_registry` kwarg
        spec = self.bridge_registry.get("acu1_supply_patch_1")  # BCSpec or None
    """

    def __init__(self) -> None:
        self._registry: Dict[str, BCSpec] = {}

    def register(self, patch_name: str, spec: BCSpec) -> None:
        """Register a BC spec for a patch name (auto-sanitized)."""
        key = _sanitize(patch_name)
        self._registry[key] = spec

    def get(self, patch_name: str) -> Optional[BCSpec]:
        """Look up a BC spec by patch name (auto-sanitized)."""
        return self._registry.get(_sanitize(patch_name))

    def items(self):
        """Iterate over (sanitized_name, BCSpec) pairs."""
        return self._registry.items()

    def __contains__(self, patch_name: str) -> bool:
        return _sanitize(patch_name) in self._registry

    def __len__(self) -> int:
        return len(self._registry)

