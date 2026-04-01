from typing import Optional, OrderedDict
from dclib.models.base import BaseModel

from dclib.cooling.room.facilities.acu import ACU
from dclib.ite.servers.server import Server

from dclib.cooling.common.pump import Pump
from dclib.cooling.common.heat_exchanger import HeatExchanger
from dclib.cooling.common.pipe import Pipe

from dclib.cooling.room.facilities.cdu import CDU
from dclib.cooling.plant.facilities.chiller import Chiller
from dclib.cooling.plant.facilities.cooling_tower import CoolingTower
from dclib.cooling.plant.facilities.tank import Tank


class Components(BaseModel):
    """
    Components are the devices installed on the branch.
    They are used to define the devices in the plant loops or liquid cooling loops.
    """
    pipes: Optional[OrderedDict[str, Pipe]] = None
    chillers: Optional[OrderedDict[str, Chiller]] = None
    pumps: Optional[OrderedDict[str, Pump]] = None
    cooling_towers: Optional[OrderedDict[str, CoolingTower]] = None
    acu: Optional[OrderedDict[str, ACU]] = None  # can only have one ACU in a branch
    heat_exchangers: Optional[OrderedDict[str, HeatExchanger]] = None
    tanks: Optional[OrderedDict[str, Tank]] = None
    servers: Optional[OrderedDict[str, Server]] = None
    cdus: Optional[OrderedDict[str, CDU]] = None
