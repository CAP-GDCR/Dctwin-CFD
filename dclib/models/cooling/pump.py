from typing import Optional
from dclib.models.base import BaseModel


class PumpCoolingModel(BaseModel):
    """
    See documentation for parameter details.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-pumps.html
    """

    design_maximum_flow_rate: Optional[str | float] = "autosize"
    design_pump_head: Optional[str | float] = 160000
    pump_flow_rate_schedule_name: Optional[str] = ""
    design_minimum_flow_rate_fraction: Optional[float] = 0.0
    zone_name: Optional[str] = ""
    skin_loss_radiative_fraction: Optional[float] = 0.5
    impeller_diameter: Optional[str | float] = ""
