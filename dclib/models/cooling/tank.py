from enum import Enum
from typing import Optional
from dclib.models.base import BaseModel


class Indicator(str, Enum):
    """
    Face of a 3D object. Used to define the surface id of a 3D object
    """
    outdoors = "Outdoors"
    zone = "Zone"


class TankCoolingModel(BaseModel):
    """
    Storage Tank Model.
    This class is a simplified representation and may not include all features of physical storage tanks.
    """
    tank_volume: str | float = 4.0
    deadband_temperature_difference: Optional[str | float] = 3.5
    minimum_temperature_limit: Optional[str | float] = ""
    nominal_cooling_capacity: Optional[str | float] = None
    heat_gain_coefficient_from_ambient_temperature: Optional[str | float] = 5.0
    use_side_heat_transfer_effectiveness: Optional[float] = 1.0
    use_side_design_flow_rate: Optional[str | float] = "autosize"
    source_side_heat_transfer_effectiveness: Optional[float] = 1.0
    source_side_design_flow_rate: Optional[str | float] = "autosize"
    tank_recovery_time: Optional[str | float] = 4.0
