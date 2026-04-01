"""
Pydantic v2 validation models for the CFD Export bridge JSON (cfd_export.json).

This schema validates the JSON file exported by STLGeneratorForOpenFoam,
which bridges the visual modeling tool with the dctwin CFD engine.
"""
from typing import Dict, List, Optional, Literal
from pydantic import BaseModel, ConfigDict, Field


class CFDBaseModel(BaseModel):
    """Base schema model that preserves forward-compatible extra fields."""

    model_config = ConfigDict(extra="allow")


class Point3D(CFDBaseModel):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class SensorPoint(CFDBaseModel):
    id: str = ""
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class CFDExportRoom(CFDBaseModel):
    height: float = 3.6
    plane: List[Point3D] = Field(default_factory=list)
    locationInMesh: Point3D = Field(default_factory=Point3D)


class ServerPhysics(CFDBaseModel):
    volumeFlowRate: Optional[float] = 0.5
    power: Optional[float] = 1000.0
    fixedTemperature: Optional[float] = None
    fixedTempFace: Optional[Literal["inlet", "outlet"]] = "inlet"  # for nonFan servers; None for fanServer


class RackServer(CFDBaseModel):
    id: str
    type: Literal["fanServer", "nonFan"] = "fanServer"
    startSlot: int = 0
    spanSlots: int = 1
    inletSolidName: str = ""
    outletSolidName: str = ""
    topSolidName: Optional[str] = None     # nonFan only: top plate solid (fixedTemperature BC)
    bottomSolidName: Optional[str] = None  # nonFan only: bottom plate solid (fixedTemperature BC)
    physics: ServerPhysics = Field(default_factory=ServerPhysics)


class LeakageGroup(CFDBaseModel):
    startUnit: int = 0
    endUnit: int = 0
    sigma: float = 0.05
    stlSolidName: str = ""


class LeakageConfig(CFDBaseModel):
    enabled: bool = False
    sigma: float = 0.05          # legacy global sigma (v1.0 compat)
    throughSlots: List[int] = Field(default_factory=list)
    groups: List[LeakageGroup] = Field(default_factory=list)  # per-group sigma (v1.1+)


class RackGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=lambda: {"W": 0.6, "D": 1.0, "H": 1.867})
    orientation: float = 0.0
    unitCount: int = 42
    unitHeight: float = 0.04445
    slotWidthRatio: float = 0.8


class CFDExportRack(CFDBaseModel):
    stlFile: str
    leakageStlFile: Optional[str] = None
    geometry: RackGeometry = Field(default_factory=RackGeometry)
    servers: List[RackServer] = Field(default_factory=list)
    leakage: LeakageConfig = Field(default_factory=LeakageConfig)
    wallSolidNames: List[str] = Field(default_factory=list)


# ---- ACU multi-face support (new arrays) ----

class ACUFacePhysics(CFDBaseModel):
    temperature: Optional[float] = None   # supply face only
    volumeFlowRate: float = 4.0
    velocity: float = 2.0


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


class BafflePhysics(CFDBaseModel):
    baffleType: Literal["wall", "perforated"] = "wall"
    porosity: float = 0.0


class BaffleGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=dict)
    orientation: float = 0.0


class CFDExportBaffle(CFDBaseModel):
    stlFile: str
    geometry: BaffleGeometry = Field(default_factory=BaffleGeometry)
    physics: BafflePhysics = Field(default_factory=BafflePhysics)


class FloorOpeningPhysics(CFDBaseModel):
    type: Literal["wall", "wallBaffle", "perforated"] = "wall"
    porosity: float = 0.0


class FloorOpening(CFDBaseModel):
    solidName: str
    patchId: Optional[str] = None
    physics: FloorOpeningPhysics = Field(default_factory=FloorOpeningPhysics)


class FloorGeometry(CFDBaseModel):
    anchor: Point3D = Field(default_factory=Point3D)
    size: Dict[str, float] = Field(default_factory=dict)


class CFDExportFloor(CFDBaseModel):
    stlFile: str
    geometry: FloorGeometry = Field(default_factory=FloorGeometry)
    defaultBC: Literal["wall", "wallBaffle", "perforated"] = "wall"
    defaultBCPorosity: float = 0.0
    openings: List[FloorOpening] = Field(default_factory=list)
    wallSolidNames: List[str] = Field(default_factory=list)


class CFDExportWall(CFDBaseModel):
    stlFile: Optional[str] = None
    geometry: SimpleGeometry = Field(default_factory=SimpleGeometry)
    solidNames: List[str] = Field(default_factory=list)


class MeshConfig(CFDBaseModel):
    baseSize: float = 0.2
    refinementLevel: List[int] = Field(default_factory=lambda: [1, 1])
    rackRefinementLevel: List[int] = Field(default_factory=lambda: [2, 3])
    serverFaceRefinementLevel: List[int] = Field(default_factory=lambda: [2, 3])
    baffleRefinementLevel: List[int] = Field(default_factory=lambda: [1, 3])
    parallelMesh: bool = True
    meshProcess: int = 16


class ResidualControl(CFDBaseModel):
    p_rgh: float | str = "1e-4"
    U: float | str = "1e-5"
    T: float | str = "1e-5"
    k_epsilon: float | str = "1e-4"


class SimulationConfig(CFDBaseModel):
    endTime: int = 500
    writeInterval: int = 100
    solveProcess: int = 1
    solverType: str = "steady"
    roomTemperature: float = 22.0
    residualControl: ResidualControl = Field(default_factory=ResidualControl)


class CFDExport(CFDBaseModel):
    """Root schema for cfd_export.json from STLGeneratorForOpenFoam."""
    version: str = "1.0"
    source: str = "STLGeneratorForOpenFoam"
    projectName: str = "CFD_Project"
    room: CFDExportRoom = Field(default_factory=CFDExportRoom)
    racks: Dict[str, CFDExportRack] = Field(default_factory=dict)
    acus: Dict[str, CFDExportACU] = Field(default_factory=dict)
    wallObjects: Dict[str, CFDExportWallObject] = Field(default_factory=dict)
    heatSources: Dict[str, CFDExportHeatSource] = Field(default_factory=dict)
    baffles: Dict[str, CFDExportBaffle] = Field(default_factory=dict)
    floors: Dict[str, CFDExportFloor] = Field(default_factory=dict)
    walls: Dict[str, CFDExportWall] = Field(default_factory=dict)
    sensorPoints: List[SensorPoint] = Field(default_factory=list)
    meshConfig: MeshConfig = Field(default_factory=MeshConfig)
    simulationConfig: SimulationConfig = Field(default_factory=SimulationConfig)
