"""
BridgeBCRegistry: maps sanitized STL solid names → OpenFOAM boundary condition specs.

This module is the handoff mechanism between the CFDProjectImporter (which reads
cfd_export.json from STLGeneratorForOpenFoam) and the FieldGenerator (which writes
OpenFOAM 0/ field files).

The registry is serialized to `model/bridge_bc.json` after import and loaded by
FieldGenerator during field generation.

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
import json
import re
from pathlib import Path
from typing import Dict, Optional
from pydantic import BaseModel


def _sanitize(name: str) -> str:
    """Sanitize a solid name to a valid OpenFOAM patch name (same logic as HTML tool)."""
    return re.sub(r'[^A-Za-z0-9_]', '_', name)


class BCSpec(BaseModel):
    """Boundary condition specification for a single STL solid patch."""
    bc_type: str = "wall"
    temperature: Optional[float] = None   # Celsius; used for supply/fixedTemp patches
    flow_rate: Optional[float] = None     # m³/s; used for supply/return/fan patches
    power: Optional[float] = None         # W; used for fan_outlet temperature computation
    sigma: Optional[float] = None         # porosity coefficient; used for leakage_zone cellZones
    heat_flux: Optional[float] = None     # W/m²; used for heat_source_flux BC


class BridgeBCRegistry:
    """Maps sanitized patch solid names → BCSpec for the bridge pipeline.

    Usage in importer:
        registry = BridgeBCRegistry()
        registry.register("acu1_supply_patch_1", BCSpec(bc_type="acu_supply", temperature=15.0, flow_rate=4.0))
        registry.save(case_dir / "model" / "bridge_bc.json")

    Usage in field generator:
        registry = BridgeBCRegistry.load(case_dir / "model" / "bridge_bc.json")
        spec = registry.get("acu1_supply_patch_1")  # BCSpec or None
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

    def save(self, path: Path) -> None:
        """Serialize registry to a JSON file."""
        data = {k: v.model_dump() for k, v in self._registry.items()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> "BridgeBCRegistry":
        """Load registry from a JSON file. Returns empty registry if file not found."""
        obj = cls()
        if path.exists():
            data = json.loads(path.read_text())
            for k, v in data.items():
                obj._registry[k] = BCSpec(**v)
        return obj
