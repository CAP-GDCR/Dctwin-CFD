from typing import Optional
from dclib.models.base import BaseModel

class PumpPowerCoolingModel(BaseModel):
    power_consumption: Optional[float] = 0.0

class PumpPowerModel(BaseModel):
    """
    See documentation for parameter details.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-pumps.html
    """
    design_power_consumption: Optional[str | float] = "autosize"
    motor_efficiency: Optional[float] = 0.8
    fraction_of_motor_inefficiencies_to_fluid_stream: Optional[float] = 0.0
    coefficient_1_of_the_part_load_performance_curve: Optional[float] = 0.0
    coefficient_2_of_the_part_load_performance_curve: Optional[float] = 1.0
    coefficient_3_of_the_part_load_performance_curve: Optional[float] = 0.0
    coefficient_4_of_the_part_load_performance_curve: Optional[float] = 0.0
    part_load_performance_curve_model: Optional[str] = ""
    pump_curve_name: Optional[str] = ""
    design_power_sizing_method: Optional[str] = "PowerPerFlowPerPressure"
    design_electric_power_per_unit_flow_rate: Optional[float | str] = 348701.1
    design_shaft_power_per_unit_flow_rate_per_unit_head: Optional[float | str] = 1.282051282
