from typing import Optional, List, OrderedDict

from dclib.models.base import BaseModel
from .curves import CurveModel

class PumpCurveModels(BaseModel):
    part_load_performance_curve_models: Optional[OrderedDict[str, CurveModel]] = None