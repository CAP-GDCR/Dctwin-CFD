from typing import Optional, OrderedDict

from dclib.models.base import BaseModel
from .curves import CurveModel

class DehumidifierCurveModels(BaseModel):
    water_removal_curve_models: Optional[OrderedDict[str, CurveModel]] = None
    energy_factor_curve_models: Optional[OrderedDict[str, CurveModel]] = None
    part_load_fraction_correlation_curve_models: Optional[OrderedDict[str, CurveModel]] = None
