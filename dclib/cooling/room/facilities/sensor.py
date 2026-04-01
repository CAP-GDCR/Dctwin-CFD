"""Temperature/Humidity sensor object
"""
from typing import Optional, OrderedDict
from pydantic import Field

from dclib.models.base import BaseModel
from dclib.models.geometry import Vertex


class SensorGeometry(BaseModel):
    """ Sensor geometry that defines the location of the sensor
    """
    location: Vertex


class Sensor(BaseModel):
    """ Sensor object in a data center
    """
    uid: Optional[str] = ""
    geometry: SensorGeometry
    meta: Optional[OrderedDict] = Field(default_factory=dict)
