from enum import Enum
from typing import Optional, List, OrderedDict

from dclib.models.base import BaseModel
from .curves import CurveModel

class ChillerCurveModels(BaseModel):
    cooling_capacity_function_of_temperature_curve_models: Optional[OrderedDict[str, CurveModel]] = None
    electric_input_to_cooling_output_ratio_function_of_temperature_curve_models: Optional[OrderedDict[str, CurveModel]] = None
    electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve_models: Optional[OrderedDict[str, CurveModel]] = None
