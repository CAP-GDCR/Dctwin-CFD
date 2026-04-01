from typing import Optional
from dclib.models.base import BaseModel


class TankControlModel(BaseModel):
    """
    Parameters that defines the control-related parameters of a pump. For more information, see:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-tanks.html#tankvariablespeed
    """
    use_side_availability_schedule_name: Optional[str] = ""
    source_side_availability_schedule_name: Optional[str] = ""
    ambient_temperature_schedule_name: Optional[str] = ""
