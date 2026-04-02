from typing import Optional, List
from dclib.models.base import BaseModel


class ITEModel(BaseModel):
    """
    ITEModel is a model for Information Technology Equipment (ITE) for thermal simulation. For more information, please refer to
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-internal-gains-people-lights-other.html#electricequipmentiteaircooled
    """
    air_flow_calculation_method: Optional[str] = "FlowFromSystem"
    design_power_input_calculation_method: Optional[str] = "Watts/Unit"
    watts_per_zone_floor_area: Optional[float | str] = 0
    design_power_input_schedule_name: Optional[str] = "ALWAYS ON"
    cpu_loading_schedule_name: Optional[str] = ""

    cpu_power_input_function_of_loading_and_air_temperature_curve_model: Optional[str] = ""
    cpu_power_input_function_of_loading_and_air_temperature_curve: Optional[List[float]] = [0, 1, 0, 0, 0, 0]
    air_flow_function_of_loading_and_air_temperature_curve_model: Optional[str] = ""
    air_flow_function_of_loading_and_air_temperature_curve: Optional[List[float]] = [0, 1, 0, 0, 0, 0]
    fan_power_input_function_of_flow_curve_model: Optional[str] = ""
    fan_power_input_function_of_flow_curve: Optional[List[float]] = [0, 1, 0]
    recirculation_fraction_function_of_loading_and_air_temperature_curve_model: Optional[str] = ""
    recirculation_fraction_function_of_loading_and_air_temperature_curve: Optional[List[float]] = [1, 0, 0, 0, 0, 0]
    electric_power_supply_efficiency_function_of_part_load_ratio_curve_model: Optional[str] = ""
    electric_power_supply_efficiency_function_of_part_load_ratio_curve: Optional[List[float]] = [1, 0, 0]

    design_fan_power_input_fraction: Optional[float] = 0.4
    design_fan_air_flow_rate_per_power_input: Optional[float] = 0.0001
    design_entering_air_temperature: Optional[float] = 18.0
    environmental_class: Optional[str] = "A1"
    air_inlet_connection_type: Optional[str] = "AdjustedSupply"

    design_recirculation_fraction: Optional[float] = 0.1
    design_electric_power_supply_efficiency: Optional[float] = 1.0
    fraction_of_electric_power_supply_losses_to_zone: Optional[float] = 1.0
    cpu_enduse_subcategory: Optional[str] = "ITE-CPU"
    fan_enduse_subcategory: Optional[str] = "ITE-Fans"
    electric_power_supply_enduse_subcategory: Optional[str] = "ITE-UPS"
