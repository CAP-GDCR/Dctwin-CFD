from typing import Optional, OrderedDict

from dclib.models.base import BaseModel

from .server import ServerPowerModel
from .pump import PumpPowerModel
from .chiller import ChillerPowerModel
from .cooling_tower import CoolingTowerPowerModel
from .acu import ACUPowerModel
from .dehumidifier import DehumidifierPowerModel


class PowerModel(BaseModel):
    """ Object standard power property """
    acus: Optional[OrderedDict[str, ACUPowerModel]] = None
    dehumidifiers: Optional[OrderedDict[str, DehumidifierPowerModel]] = None
    chillers: Optional[OrderedDict[str, ChillerPowerModel]] = None
    servers: Optional[OrderedDict[str, ServerPowerModel]] = None
    pumps: Optional[OrderedDict[str, PumpPowerModel]] = None
    cooling_towers: Optional[OrderedDict[str, CoolingTowerPowerModel]] = None
