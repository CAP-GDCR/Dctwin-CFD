from typing import Optional, OrderedDict
from pydantic import Field
from .curves.composites import CurveModels
from .cooling.composite import CoolingModels
from .computing.composite import ComputingModel
from .geometry.composite import GeometryModels
from .power.composite import PowerModel
from .heat_gains.composite import HeatGainsModel
from .constructions.construction import ConstructionModel, MaterialModel
from .electrical.composite import ElectricalModel
from .control.composite import ControlModel

from .base import BaseModel


class Models(BaseModel):
    """ Models is used to define the object models of the building """
    curve_models: Optional[CurveModels] = Field(default_factory=CurveModels)
    material_models: Optional[OrderedDict[str, MaterialModel]] = Field(default_factory=dict)
    construction_models: Optional[OrderedDict[str, ConstructionModel]] = Field(default_factory=dict)
    geometry_models: Optional[GeometryModels] = Field(default_factory=GeometryModels)
    cooling_models: Optional[CoolingModels] = Field(default_factory=CoolingModels)
    power_models: Optional[PowerModel] = Field(default_factory=PowerModel)
    heat_gain_models: Optional[HeatGainsModel] = Field(default_factory=HeatGainsModel)
    computing_models: Optional[ComputingModel] = Field(default_factory=ComputingModel)
    electrical_models: Optional[ElectricalModel] = Field(default_factory=ElectricalModel)
    control_models: Optional[ControlModel] = Field(default_factory=ControlModel)
