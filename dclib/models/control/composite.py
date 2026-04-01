from typing import Optional, OrderedDict

from dclib.models.base import BaseModel

from .pump import PumpControlModel
from .acu import ACUControlModel
from .dehumidifier import DehumidifierControlModel
from .tank import  TankControlModel


class ControlModel(BaseModel):
    """
    Control model for a building. Now only the pump has control model.
    """
    acus: Optional[OrderedDict[str, ACUControlModel]] = None
    dehumidifiers: Optional[OrderedDict[str, DehumidifierControlModel]] = None
    pumps: Optional[OrderedDict[str, PumpControlModel]] = None
    tanks: Optional[OrderedDict[str, TankControlModel]] = None
