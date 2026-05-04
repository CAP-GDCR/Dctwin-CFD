"""
Pydantic v2 validation models for the .cfdrun manifest (JSON).

This schema validates the JSON file exported by STLGeneratorForOpenFoam,
which bridges the visual modeling tool with the dctwin CFD engine.
"""
import re
from collections import Counter
from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


def _sanitize_patch_name(name: str) -> str:
    """Match the OpenFOAM/STLGenerator sanitization rules for patch names."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _summarize_duplicates(counter: Counter, limit: int = 8) -> str:
    duplicates = [f"{name} x{count}" for name, count in sorted(counter.items()) if count > 1]
    if len(duplicates) <= limit:
        return ", ".join(duplicates)
    head = ", ".join(duplicates[:limit])
    return f"{head}, ... (+{len(duplicates) - limit} more)"


class CFDBaseModel(BaseModel):
    """Base schema model that preserves forward-compatible extra fields."""

    model_config = ConfigDict(extra="allow")


class TimeVaryingEntry(CFDBaseModel):
    t: float
    value: float


class Point3D(CFDBaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class SeedConfig(CFDBaseModel):
    enabled: bool = True
    anchor: Point3D = Field(default_factory=Point3D)
    direction: Point3D = Field(default_factory=lambda: Point3D(x=0.0, y=0.0, z=1.0))
    offset: float = 0.0


class SensorPoint(CFDBaseModel):
    id: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class CFDExportRoom(CFDBaseModel):
    height: float = Field(3.6, description="Room height in meters (vertical extent along Y; Y-up convention)")
    plane: List[Point3D] = Field(default_factory=list)
    locationInMesh: Point3D = Field(default_factory=Point3D)
    extraLocationsInMesh: List[Point3D] = Field(default_factory=list)


class ServerPhysics(CFDBaseModel):
    volumeFlowRate: Optional[float] = 0.5
    power: Optional[float] = 1000.0
    temperatureRise: Optional[float] = None
    serverBCMode: Optional[Literal["powerAndFlow", "tempRiseAndFlow", "powerAndTempRise"]] = "powerAndFlow"
    fixedTemperature: Optional[float] = None
    fixedTempFace: Optional[Literal["inlet", "outlet"]] = "inlet"  # for nonFan servers; None for fanServer


class RackServer(CFDBaseModel):
    id: str
    type: Literal["fanServer", "nonFan"] = "fanServer"
    startSlot: int = 0
    spanSlots: int = 1
    # Legacy single-solid names. Null/empty when the face has been split into
    # multiple patches (see `faces` dict below for the per-patch names).
    inletSolidName: Optional[str] = ""
    outletSolidName: Optional[str] = ""
    topSolidName: Optional[str] = None     # nonFan only: top plate solid (fixedTemperature BC)
    bottomSolidName: Optional[str] = None  # nonFan only: bottom plate solid (fixedTemperature BC)
    physics: ServerPhysics = Field(default_factory=ServerPhysics)


class ThroughZoneGroup(CFDBaseModel):
    # Renamed 2026-04-17 from LeakageGroup. Existing cfdproj files with the
    # old keys will silently fall back to defaults — users need to re-save
    # from the UI to get the migrated keys.
    startUnit: int = 0
    endUnit: int = 0
    sigma: float = 0.05
    stlSolidName: str = ""


# Backward-compat alias (legacy manifests used LeakageGroup)
LeakageGroup = ThroughZoneGroup


class ThroughZoneConfig(CFDBaseModel):
    # Renamed 2026-04-17 from LeakageConfig. Existing cfdproj files with the
    # old keys will silently fall back to defaults — users need to re-save
    # from the UI to get the migrated keys.
    enabled: bool = False
    sigma: float = 0.05          # legacy global sigma (v1.0 compat)
    throughSlots: List[int] = Field(default_factory=list)
    groups: List[ThroughZoneGroup] = Field(default_factory=list)  # per-group sigma (v1.1+)


# Backward-compat alias
LeakageConfig = ThroughZoneConfig


class RackGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=lambda: {"W": 0.6, "D": 1.0, "H": 1.867})
    orientation: float = 0.0
    unitCount: int = 42
    unitHeight: float = 0.04445
    slotWidthRatio: float = 0.8


class RackDoorPerforated(CFDBaseModel):
    """Perforated-plate physical description for a rack inlet/outlet door.

    Shape mirrors `bridge_bc.PerforatedSpec` so the runner can construct one
    directly from the dict. Replaces the legacy fraction-valued `porosity`
    field (2026-04-16 unification).
    """
    shape: Literal["circle", "rect", "diamond"] = "circle"
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0


class RackDoor(CFDBaseModel):
    """Rack inlet/outlet door definition (perforated baffle pair)."""
    enabled: bool = True
    perforated: RackDoorPerforated = Field(default_factory=RackDoorPerforated)
    solidName: Optional[str] = None


class CFDExportRack(CFDBaseModel):
    stlFile: str
    leakageStlFile: Optional[str] = None  # legacy schema field; already null in new exports
    geometry: RackGeometry = Field(default_factory=RackGeometry)
    servers: List[RackServer] = Field(default_factory=list)
    leakage: ThroughZoneConfig = Field(default_factory=ThroughZoneConfig)
    wallSolidNames: List[str] = Field(default_factory=list)
    inletDoor: Optional[RackDoor] = None
    outletDoor: Optional[RackDoor] = None


# ---- ACU multi-face support (new arrays) ----

class ACUFacePhysics(CFDBaseModel):
    temperature: Optional[float] = None   # supply face only
    volumeFlowRate: float = 4.0
    velocity: float = 2.0
    coolingCapacity: Optional[float] = None        # kW
    coolantTemperature: Optional[float] = None     # Celsius (chilled water inlet)
    setPointTemperature: Optional[float] = None    # Celsius
    minSupplyTemperature: Optional[float] = None   # Celsius (legacy, prefer coolantTemperature)
    ratedReturnTemp: Optional[float] = None            # Celsius (rated return air temp)
    coolingPerformanceTable: Optional[List[dict]] = None  # [{returnTemp, capacity}]
    timeVarying: Dict[str, Any] = Field(default_factory=dict)


class ACUFaceEntry(CFDBaseModel):
    solidName: str = "supply"
    patchId: Optional[str] = None
    face: Optional[str] = None
    physics: ACUFacePhysics = Field(default_factory=ACUFacePhysics)


# Legacy singular face models (kept for backward compatibility with old exports)
class SupplyFacePhysics(CFDBaseModel):
    temperature: float = 15.0
    volumeFlowRate: float = 4.0


class ReturnFacePhysics(CFDBaseModel):
    volumeFlowRate: float = 4.0


class ACUSupplyFace(CFDBaseModel):
    solidName: str = "supply"
    patchId: Optional[str] = None
    physics: SupplyFacePhysics = Field(default_factory=SupplyFacePhysics)


class ACUReturnFace(CFDBaseModel):
    solidName: str = "return"
    patchId: Optional[str] = None
    physics: ReturnFacePhysics = Field(default_factory=ReturnFacePhysics)


class SimpleGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=dict)
    orientation: float = 0.0


class Quaternion4(CFDBaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    w: float = 1.0


class CFDExportACU(CFDBaseModel):
    stlFile: str
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    # Multi-face arrays (v1.1+ format from STLGeneratorForOpenFoam)
    supplyFaces: List[ACUFaceEntry] = Field(default_factory=list)
    returnFaces: List[ACUFaceEntry] = Field(default_factory=list)
    # Legacy singular faces (v1.0 compat — only present in old exports)
    supplyFace: Optional[ACUSupplyFace] = None
    returnFace: Optional[ACUReturnFace] = None
    wallSolidNames: List[str] = Field(default_factory=list)

    def get_supply_faces(self) -> List[ACUFaceEntry]:
        """Return supply face list, falling back to legacy supplyFace singular."""
        if self.supplyFaces:
            return self.supplyFaces
        if self.supplyFace:
            return [ACUFaceEntry(
                solidName=self.supplyFace.solidName,
                patchId=self.supplyFace.patchId,
                physics=ACUFacePhysics(
                    temperature=self.supplyFace.physics.temperature,
                    volumeFlowRate=self.supplyFace.physics.volumeFlowRate,
                )
            )]
        return []

    def get_return_faces(self) -> List[ACUFaceEntry]:
        """Return return face list, falling back to legacy returnFace singular."""
        if self.returnFaces:
            return self.returnFaces
        if self.returnFace:
            return [ACUFaceEntry(
                solidName=self.returnFace.solidName,
                patchId=self.returnFace.patchId,
                physics=ACUFacePhysics(
                    volumeFlowRate=self.returnFace.physics.volumeFlowRate,
                )
            )]
        return []

    def get_primary_supply_temperature(self) -> float:
        """Return temperature of first supply face for room.json compat."""
        faces = self.get_supply_faces()
        if faces and faces[0].physics.temperature is not None:
            return faces[0].physics.temperature
        return 15.0

    def get_primary_supply_flow_rate(self) -> float:
        """Return flow rate of first supply face for room.json compat."""
        faces = self.get_supply_faces()
        if faces:
            return faces[0].physics.volumeFlowRate
        return 4.0


class HeatPatchPhysics(CFDBaseModel):
    heatPower: float = 0.0
    fixedTemperature: Optional[float] = None  # direct surface temperature BC


class HeatPatch(CFDBaseModel):
    solidName: str
    patchId: Optional[str] = None
    physics: HeatPatchPhysics = Field(default_factory=HeatPatchPhysics)


class CFDExportWallObject(CFDBaseModel):
    stlFile: str
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    heatPatches: List[HeatPatch] = Field(default_factory=list)
    wallSolidNames: List[str] = Field(default_factory=list)


class CFDExportHeatSource(CFDBaseModel):
    stlFile: str
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    heatMode: Literal["power", "temperature"] = "power"
    totalPower: float = 100.0          # W (used when heatMode = "power")
    fixedTemperature: Optional[float] = None  # °C (used when heatMode = "temperature")
    wallSolidNames: List[str] = Field(default_factory=list)
    timeVarying: Dict[str, Any] = Field(default_factory=dict)


# baffles[*].physics = {
#   "baffleType": "wall" | "perforated",
#   "perforated": {                # required when baffleType == "perforated"
#     "shape": "circle" | "rect" | "diamond",
#     "holeDiameterMm": float,
#     "openAreaPct": float         # 0..100
#   }
# }
# The `perforated` block carries the full physical description used by the
# Idelchik §8.2 eq. 8-4 pressure-loss formula (see bridge_bc.PerforatedSpec /
# perforated_to_porous_baffle).  When present it supersedes the legacy
# `porosity` scalar for perforated baffles.  `porosity` (0..100 percent) is
# retained for backward-compatibility and is still used by the classifier
# that decides "wall vs perforated" at the createBaffles stage.
class PerforatedConfig(CFDBaseModel):
    """Shared perforated-plate physical description (Idelchik §8.2 inputs).

    Used by baffle physics, floor opening physics, and rack doors. Mirrors
    `bridge_bc.PerforatedSpec` so the runner can construct one directly.
    """
    shape: Literal["circle", "rect", "diamond"] = "circle"
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0


class BafflePhysics(CFDBaseModel):
    baffleType: Literal["wall", "perforated"] = "wall"
    porosity: float = 0.0
    perforated: Optional[PerforatedConfig] = None
    seedConfig: Optional[SeedConfig] = None


class BaffleGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=dict)
    orientation: float = 0.0


class CFDExportBaffle(CFDBaseModel):
    stlFile: str
    geometry: BaffleGeometry = Field(default_factory=BaffleGeometry)
    physics: BafflePhysics = Field(default_factory=BafflePhysics)
    seedConfig: Optional[SeedConfig] = None
    wallRegionSeedConfigs: Dict[str, SeedConfig] = Field(default_factory=dict)
    patchPhysics: Dict[str, BafflePhysics] = Field(default_factory=dict)


class PartitionWallHolePhysics(CFDBaseModel):
    # 2026-04-16 simplification: the source-term gate moved entirely to
    # `leakageRate` (< 100% enables the source). `resistanceEnabled` is a
    # legacy field kept for backward compatibility with old cfdproj files
    # but is no longer the source of truth.
    resistanceEnabled: bool = False
    leakageRate: float = 100.0


class PartitionWallHole(CFDBaseModel):
    patchId: str
    shape: Literal["rectangle", "circle"] = "rectangle"
    name: str = ""
    zoneName: str = ""
    zoneStlFile: Optional[str] = None
    physics: PartitionWallHolePhysics = Field(default_factory=PartitionWallHolePhysics)


class PartitionWallGeometry(SimpleGeometry):
    transformQuaternion: Quaternion4 = Field(default_factory=Quaternion4)


class CFDExportPartitionWall(CFDBaseModel):
    stlFile: str
    geometry: PartitionWallGeometry = Field(default_factory=PartitionWallGeometry)
    wallSolidNames: List[str] = Field(default_factory=list)
    holes: List[PartitionWallHole] = Field(default_factory=list)


class FloorOpeningPhysics(CFDBaseModel):
    type: Literal["wall", "wallBaffle", "perforated"] = "wall"
    porosity: float = 0.0
    perforated: Optional[PerforatedConfig] = None


class FloorOpening(CFDBaseModel):
    solidName: str
    patchId: Optional[str] = None
    physics: FloorOpeningPhysics = Field(default_factory=FloorOpeningPhysics)
    seedConfig: Optional[SeedConfig] = None


class FloorGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=dict)


class FloorDefaultBCPerforated(CFDBaseModel):
    """Perforated-plate physical description for a floor remaining-area BC.

    Mirrors `bridge_bc.PerforatedSpec`. 2026-04-16 unification replaces the
    legacy `defaultBCPorosity` scalar.
    """
    shape: Literal["circle", "rect", "diamond"] = "circle"
    holeDiameterMm: float = 3.0
    openAreaPct: float = 64.0


class CFDExportFloor(CFDBaseModel):
    stlFile: str
    geometry: FloorGeometry = Field(default_factory=FloorGeometry)
    defaultBC: Literal["wall", "wallBaffle", "perforated"] = "wall"
    defaultBCPorosity: float = 0.0  # legacy; superseded by defaultBCPerforated
    defaultBCPerforated: Optional[FloorDefaultBCPerforated] = None
    defaultBCSeedConfig: Optional[SeedConfig] = None
    defaultBCSeedConfigs: Dict[str, SeedConfig] = Field(default_factory=dict)
    openings: List[FloorOpening] = Field(default_factory=list)
    wallSolidNames: List[str] = Field(default_factory=list)


class CFDExportWall(CFDBaseModel):
    stlFile: Optional[str] = None
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    solidNames: List[str] = Field(default_factory=list)


class MeshConfig(CFDBaseModel):
    baseSize: float = 0.34
    # Auto-detect reference scale (2026-04-19 spec). When None, auto-detect
    # uses baseSize (backward compatible). When set, the client-side
    # ceil(log2(X / (H/2))) uses this value — decouples level-picking from
    # actual mesh baseSize so the user can pick a finer background for
    # solver stability without inflating auto-detect levels.
    detectBaseSize: Optional[float] = None
    refinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackRefinementLevel: List[int] = Field(default_factory=lambda: [2, 3])
    serverFaceRefinementLevel: List[int] = Field(default_factory=lambda: [2, 3])
    baffleRefinementLevel: List[int] = Field(default_factory=lambda: [1, 3])
    # Rack perforated-door refinement (2026-04-17 spec):
    # - rackDoorRefinementLevel: unified baseline, editable in UI
    # - rackDoorPerMinUnitLevel: bucketed by each rack's min U-span across
    #   server/blanking/through-zone entries. Declared explicitly (despite
    #   extra="allow") so pydantic type-checks the value on future
    #   tightening; see maxGlobalCells comment below.
    rackDoorRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    # Door feature-line level. None = auto-fallback to surface max.
    rackDoorFeatureLevel: Optional[int] = None
    partitionWallRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    partitionWallFeatureLevel: int = 2
    # ---- 2026-04-17 refinement architecture redesign ----
    # Per-bucket FEATURE-line maps (surface per-unit already existed as
    # *PerUnitLevel elsewhere). Each STL file the frontend emits gets its
    # own feature level, so the per-unit feature map is the authoritative
    # source — unified *FeatureLevel acts as the broadcast seed only.
    serverFaceFeatureLevel: int = 2
    serverFaceFeaturePerUnitLevel: Dict[str, int] = Field(default_factory=dict)
    blankingPlateFeatureLevel: int = 2
    blankingPlateFeaturePerUnitLevel: Dict[str, int] = Field(default_factory=dict)
    # New Partition 隔板 category (interior partitions inside through-slots).
    rackPartitionRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackPartitionPerUnitLevel: Dict[str, List[int]] = Field(default_factory=dict)
    rackPartitionFeatureLevel: int = 2
    rackPartitionFeaturePerUnitLevel: Dict[str, int] = Field(default_factory=dict)
    # Through-zone "feature" level = refinementRegions volume-refinement level.
    # (formerly called rackLeakageFeatureLevel / rackLeakageFeaturePerUnitLevel —
    # renamed 2026-04-17; existing cfdproj files with the old keys will
    # silently fall back to defaults — users need to re-save from the UI.)
    rackThroughZoneFeatureLevel: int = 2
    rackThroughZoneFeaturePerUnitLevel: Dict[str, int] = Field(default_factory=dict)
    # Floor/ceiling category escaped from defaultFeatureLevel fallback.
    floorRefinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    floorFeatureLevel: int = 2
    # ---- end redesign additions ----
    parallelMesh: bool = True
    meshProcess: int = 16
    # snappyHexMesh upper bounds on cell count. Declared explicitly so pydantic
    # type-checks them even though CFDBaseModel sets extra="allow" — guards
    # against a future tightening of the extras policy silently dropping the
    # user's UI value (same class of bug as the perforated unification).
    maxGlobalCells: int = 5000000
    maxLocalCells: int = 5000000


class ResidualControl(CFDBaseModel):
    p_rgh: float | str = "1e-4"
    U: float | str = "1e-5"
    T: float | str = "1e-5"
    k_epsilon: float | str = "1e-4"


class SimulationConfig(CFDBaseModel):
    endTime: int = 500
    writeInterval: int = 100
    doublePrecisionWrite: bool = False
    writePrecision: str = "single"
    endTimeSeconds: float = 300.0
    deltaT: float = 0.1
    maxDeltaT: float = 1.0
    maxCo: float = 1.0
    adjustTimeStep: bool = True
    writeIntervalSeconds: float = 10.0
    nOuterCorrectors: int = 1
    nCorrectors: int = 2
    ddtScheme: str = "Euler"
    crankNicolsonCoeff: float = 0.9
    solveProcess: int = 1
    solverType: str = "steady"
    roomTemperature: float = 22.0
    parallelSolve: bool = False
    gravity: Dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": -9.81, "z": 0})
    monitoring: Dict[str, Any] = Field(default_factory=dict)
    residualControl: ResidualControl = Field(default_factory=ResidualControl)
    schemes: Dict[str, str] = Field(default_factory=dict)
    relaxationFactors: Dict[str, float] = Field(default_factory=dict)
    nNonOrthogonalCorrectors: int = 0
    nonOrthoCorrection: bool = False
    solverPreset: str = "balanced"
    physical_properties: Dict[str, Any] = Field(default_factory=dict)
    unitTemperature: str = "C"


class CFDExport(CFDBaseModel):
    """Root schema for the .cfdrun manifest emitted by STLGeneratorForOpenFoam."""
    version: str = "1.0"
    source: str = "STLGeneratorForOpenFoam"
    projectName: str = "CFD_Project"
    room: CFDExportRoom = Field(default_factory=CFDExportRoom)
    racks: Dict[str, CFDExportRack] = Field(default_factory=dict)
    acus: Dict[str, CFDExportACU] = Field(default_factory=dict)
    wallObjects: Dict[str, CFDExportWallObject] = Field(default_factory=dict)
    heatSources: Dict[str, CFDExportHeatSource] = Field(default_factory=dict)
    baffles: Dict[str, CFDExportBaffle] = Field(default_factory=dict)
    partitionWalls: Dict[str, CFDExportPartitionWall] = Field(default_factory=dict)
    floors: Dict[str, CFDExportFloor] = Field(default_factory=dict)
    walls: Dict[str, CFDExportWall] = Field(default_factory=dict)
    sensorPoints: List[SensorPoint] = Field(default_factory=list)
    meshConfig: MeshConfig = Field(default_factory=MeshConfig)
    simulationConfig: SimulationConfig = Field(default_factory=SimulationConfig)

    @model_validator(mode="after")
    def validate_unique_rack_server_exports(self) -> "CFDExport":
        """Rack server IDs and solid names must remain unique across the export."""
        server_ids: Counter[str] = Counter()
        server_patch_names: Counter[str] = Counter()

        for rack in self.racks.values():
            for server in rack.servers:
                if server.id:
                    server_ids[server.id] += 1
                for patch_name in (
                    server.inletSolidName,
                    server.outletSolidName,
                    server.topSolidName,
                    server.bottomSolidName,
                ):
                    if patch_name:
                        server_patch_names[_sanitize_patch_name(patch_name)] += 1

        errors: List[str] = []
        if any(count > 1 for count in server_ids.values()):
            errors.append(
                "duplicate rack server ids: "
                + _summarize_duplicates(server_ids)
            )
        if any(count > 1 for count in server_patch_names.values()):
            errors.append(
                "duplicate rack server patch names: "
                + _summarize_duplicates(server_patch_names)
            )
        if errors:
            raise ValueError("; ".join(errors))
        return self
