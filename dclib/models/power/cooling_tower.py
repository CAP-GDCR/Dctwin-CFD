from typing import Optional, List
from dclib.models.base import BaseModel


class CoolingTowerPowerModel(BaseModel):
    """
    Please refer to the EnergyPlus CoolingTower documentation for more information.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-condenser-equipment.html#coolingtowervariablespeed
    """
    design_fan_power: Optional[float | str] = "autosize"  # unit(W)
    fan_power_ratio_function_of_air_flow_rate_ratio_curve_model: Optional[str] = ""
    fan_power_ratio_function_of_air_flow_rate_ratio_curve: Optional[List[float]] = \
        [
            -0.00931516301535329, 0.0512333965844443, -0.0838364671381841, 1.04191823356909
        ]
