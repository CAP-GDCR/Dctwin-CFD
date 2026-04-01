from typing import Optional, OrderedDict
from pydantic import Field

from dclib.models.base import BaseModel
from .pv_performance import PhotovoltaicPerformanceModel


class ElectricalModel(BaseModel):

    pv_performance: Optional[OrderedDict[str, PhotovoltaicPerformanceModel]] = None
