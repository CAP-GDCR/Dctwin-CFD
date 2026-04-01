from typing import Optional
from dclib.models.base import BaseModel


class PumpControlModel(BaseModel):
    """
    Parameters that defines the control-related parameters of a pump. For more information, see:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-pumps.html#pumpvariablespeed
    """
    pump_control_type: Optional[str] = "Intermittent"
    vfd_control_type: Optional[str] = ""
    pump_flow_rate_schedule_name: Optional[str] = ""
    pump_rpm_schedule_name: Optional[str] = ""
    minimum_pressure_schedule: Optional[str] = ""
    maximum_pressure_schedule: Optional[str] = ""
    minimum_rpm_schedule: Optional[str] = ""
    maximum_rpm_schedule: Optional[str] = ""
