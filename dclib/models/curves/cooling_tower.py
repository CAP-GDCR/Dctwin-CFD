from enum import Enum
from typing import Optional, List, OrderedDict

from dclib.models.base import BaseModel
from .curves import CurveModel

class CoolingTowerCurveModels(BaseModel):
    fan_power_ratio_function_of_air_flow_rate_ratio_curve_models: Optional[OrderedDict[str, CurveModel]] = None