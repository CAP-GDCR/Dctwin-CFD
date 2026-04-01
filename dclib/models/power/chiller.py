from typing import Optional, List
from dclib.models.base import BaseModel


class ChillerPowerModel(BaseModel):
    """
    Please refer to the EnergyPlus Chiller documentation for more information.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-plant-equipment.html#chillerelectriceir
    """
    electric_input_to_cooling_output_ratio_function_of_temperature_curve_model: Optional[str] = ""
    electric_input_to_cooling_output_ratio_function_of_temperature_curve: Optional[List[float]] = [
        0.47449676, -0.08900207, 0.00214058, 0.06906117, -0.00112497, 0.00060898
    ]
    electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve_model: Optional[str] = ""
    electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve: Optional[List[float]] = [0, 1, 0]
    condenser_fan_power_ratio: Optional[float] = 0.0
    fraction_of_compressor_electric_consumption_rejected_by_condenser: Optional[float] = 1.0
