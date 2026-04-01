""" Dehumidifier Component
"""

from typing import Optional, OrderedDict, Any
from pydantic import Field

from dclib.models.base import BaseModel
from dclib.models.geometry import Vertex, DehumidifierGeometryModel
from dclib.models.cooling import DehumidifierCoolingModel
from dclib.models.power import DehumidifierPowerModel
from dclib.models.control import DehumidifierControlModel


class DehumidifierGeometry(DehumidifierGeometryModel):
    """ Dehumidifier geometry properties
    """
    model: Optional[str] = None
    orientation: int = 0
    location: Vertex = None


class DehumidifierCoolingOperating(BaseModel):
    """ Dehumidifier cooling operating properties
    """
    supply_air_temperature: Optional[float] = 20.0  # unit(C)
    supply_air_volume_flow_rate: Optional[float] = 0.0  # unit(m3/s)
    supply_air_relative_humidity: Optional[float] = 50.0  # unit(%)


class DehumidifierCooling(DehumidifierCoolingModel):
    """
    Dehumidifier cooling properties
    """
    model: Optional[str] = None
    operating: DehumidifierCoolingOperating = Field(default_factory=DehumidifierCoolingOperating)


class DehumidifierPowerOperating(BaseModel):
    """ Dehumidifier power operating properties
    """
    power: Optional[float] = 1000  # unit(W)


class DehumidifierPower(DehumidifierPowerModel):
    """
    Dehumidifier power properties
    """
    model: Optional[str] = None
    operating: DehumidifierPowerOperating = Field(default_factory=DehumidifierPowerOperating)


class Dehumidifier(BaseModel):
    """ Dehumidifier Component
    """
    uid: Optional[str] = ""
    geometry: DehumidifierGeometry = Field(default_factory=DehumidifierGeometry)
    cooling: DehumidifierCooling = Field(default_factory=DehumidifierCooling)
    power: DehumidifierPower = Field(default_factory=DehumidifierPower)
    control: DehumidifierControlModel = Field(default_factory=DehumidifierControlModel)
    model: Optional[Any] = None
    meta: Optional[OrderedDict] = Field(default_factory=dict)
