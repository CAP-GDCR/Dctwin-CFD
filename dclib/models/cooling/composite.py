from typing import Optional, OrderedDict
from .acu import ACUCoolingModel
from .dehumidifier import DehumidifierCoolingModel
from .chiller import ChillerCoolingModel
from .pump import PumpCoolingModel
from .cooling_tower import CoolingTowerCoolingModel
from .server import ServerCoolingModel
from .heat_exchanger import HeatExchangerCoolingModel
from .tank import TankCoolingModel

from dclib.models.base import BaseModel


class CoolingModels(BaseModel):
    """Object standard cooling property"""

    acus: Optional[OrderedDict[str, ACUCoolingModel]] = None
    tanks: Optional[OrderedDict[str, TankCoolingModel]] = None
    dehumidifiers: Optional[OrderedDict[str, DehumidifierCoolingModel]] = None
    servers: Optional[OrderedDict[str, ServerCoolingModel]] = None
    chillers: Optional[OrderedDict[str, ChillerCoolingModel]] = None
    heat_exchangers: Optional[OrderedDict[str, HeatExchangerCoolingModel]] = None
    pumps: Optional[OrderedDict[str, PumpCoolingModel]] = None
    cooling_towers: Optional[OrderedDict[str, CoolingTowerCoolingModel]] = None
