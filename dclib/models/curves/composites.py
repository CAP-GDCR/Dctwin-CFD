from typing import Optional

from dclib.models.base import BaseModel

from .chiller import ChillerCurveModels
from .ite import ITECurveModels
from .dehumidier import DehumidifierCurveModels
from .cooling_tower import CoolingTowerCurveModels
from .acu import ACUCurveModels
from .pump import PumpCurveModels

class CurveModels(BaseModel):
    ites: Optional[ITECurveModels] = None
    chillers: Optional[ChillerCurveModels] = None
    cooling_towers: Optional[CoolingTowerCurveModels] = None
    dehumidifiers: Optional[DehumidifierCurveModels] = None
    acus: Optional[ACUCurveModels] = None
    pumps: Optional[PumpCurveModels] = None