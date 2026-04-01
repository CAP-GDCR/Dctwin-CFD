from typing import Optional, List
from dclib.models.base import BaseModel


class ChillerCoolingModel(BaseModel):
    """
    Please refer to the EnergyPlus Chiller documentation for more information.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-plant-equipment.html#chillerelectriceir
    """
    reference_capacity: Optional[float | str] = None  # required
    reference_cop: Optional[float | str] = None  # required
    reference_leaving_chilled_water_temperature: Optional[float] = 6.7
    reference_entering_condenser_fluid_temperature: Optional[float] = 29.4
    reference_chilled_water_flow_rate: Optional[float | str] = "autosize"  # checked
    reference_condenser_fluid_flow_rate: Optional[float | str] = "autosize"  # checked
    cooling_capacity_function_of_temperature_curve_model: Optional[str] = ""
    cooling_capacity_function_of_temperature_curve: Optional[List[float]] = [1, 0, 0, 0, 0, 0]

    leaving_chilled_water_lower_temperature_limit: Optional[float] = 5.0
    design_heat_recovery_water_flow_rate: Optional[float] = 0.0
    basin_heater_capacity: Optional[float | str] = ""
    basin_heater_setpoint_temperature: Optional[float | str] = ""

    condenser_type: Optional[str] = "WaterCooled"
    chiller_flow_mode: Optional[str] = "NotModulated"

    sizing_factor: Optional[float] = 1.0
    minimum_part_load_ratio: Optional[float] = 0.0
    maximum_part_load_ratio: Optional[float] = 1.0
    optimum_part_load_ratio: Optional[float] = 1.0
    minimum_unloading_ratio: Optional[float] = 0.0
