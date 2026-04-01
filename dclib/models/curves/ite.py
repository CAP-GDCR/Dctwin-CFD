from typing import Optional, OrderedDict

from dclib.models.base import BaseModel
from .curves import CurveModel


class ITECurveModels(BaseModel):
    cpu_power_input_function_of_loading_and_air_temperature_curve_models: Optional[OrderedDict[str, CurveModel]] = None
    air_flow_function_of_loading_and_air_temperature_curve_models: Optional[OrderedDict[str, CurveModel]] = None
    electric_power_supply_efficiency_function_of_part_load_ratio_curve_models: Optional[OrderedDict[str, CurveModel]] = None
    fan_power_input_function_of_flow_curve_models: Optional[OrderedDict[str, CurveModel]] = None
    recirculation_fraction_function_of_loading_and_air_temperature_curve_models: Optional[OrderedDict[str, CurveModel]] = None

