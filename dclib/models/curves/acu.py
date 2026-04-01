from enum import Enum
from typing import Optional, List, OrderedDict

from dclib.models.base import BaseModel
from .curves import CurveModel


class ACUCurveModels(BaseModel):
    fan_power_curve_models: Optional[OrderedDict[str, CurveModel]] = None
