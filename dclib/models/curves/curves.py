from enum import Enum
from typing import Optional, List

from dclib.models.base import BaseModel


class CurveType(str, Enum):
    linear = "Linear"
    cubic = "Cubic"
    quadratic = "Quadratic"
    biquadratic = "Biquadratic"
    exponential = "Exponent"


class CurveModel(BaseModel):
    """
    Parametric curve model in EnergyPlus. The coefficients are in the order of [c0, c1, c2, c3, c4, ...].
    Min and max are the minimum and maximum values of the input variable. The input type is the type of the
    input variable. The output type is the type of the dependent variable. For more information, please refer to
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-performance-curves.html#group---performance-curves
    """
    type: CurveType
    coefficients: Optional[List[float]]
    min_x: Optional[float] = ""
    max_x: Optional[float] = ""
    min_y: Optional[float] = ""
    max_y: Optional[float] = ""
    input_type_of_x: Optional[str] = ""
    input_type_of_y: Optional[str] = ""
    output_type: Optional[str] = ""
