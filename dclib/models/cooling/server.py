from typing import Optional
from pydantic import Field

from dclib.models.base import BaseModel


class ServerCoolingOperating(BaseModel):
    """ 
    Object that represents the operational cooling status of a server.
    """
    air_volume_flow_rate: Optional[float] = 0.02  # unit (m3/s)
    inlet_air_temperature: Optional[float] = 25. # unit (C)
    outlet_air_temperature: Optional[float] = 35. # unit (C)
    inlet_liquid_temperature: Optional[float] = 25.  # unit(C)
    outlet_liquid_temperature: Optional[float] = 40.  # unit(C)
    liquid_mass_flow_rate: Optional[float] = 0.005  # unit(kg/s)


class ServerCoolingModel(BaseModel):
    """
    Model of server cooling properties
    """
    fan_type: Optional[str] = "Variable"  # Fixed (CSD) or Variable (VSD)
    volume_flow_rate: Optional[float] = 0.02  # unit(m3/s)
    volume_flow_rate_ratio: Optional[float] = 0.00005  # unit(m3/s/W)
    liquid_cooling: Optional[bool] = False
    liquid_cooling_percentage: float = 1.0
    pipe_diameter: Optional[float] = 0.01  # unit(m)
    operating: ServerCoolingOperating = Field(default_factory=ServerCoolingOperating)
