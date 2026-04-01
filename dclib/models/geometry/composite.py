from typing import Optional, OrderedDict
from .acu import ACUGeometryModel
from .dehumidifier import DehumidifierGeometryModel
from .cdu import CDUGeometryModel
from .heat_exchanger import HeatExchangerGeometryModel
from .rack import RackGeometryModel
from .server import ServerGeometryModel
from .box import BoxGeometryModel
from .pipe import PipeGeometryModel

from dclib.models.base import BaseModel


class GeometryModels(BaseModel):
    """ Object standard geometry """
    acus: Optional[OrderedDict[str, ACUGeometryModel]] = None
    dehumidifiers: Optional[OrderedDict[str, DehumidifierGeometryModel]] = None
    cdus: Optional[OrderedDict[str, CDUGeometryModel]] = None
    heat_exchangers: Optional[OrderedDict[str, HeatExchangerGeometryModel]] = None
    racks: Optional[OrderedDict[str, RackGeometryModel]] = None
    servers: Optional[OrderedDict[str, ServerGeometryModel]] = None
    boxes: Optional[OrderedDict[str, BoxGeometryModel]] = None
    pipes: Optional[OrderedDict[str, PipeGeometryModel]] = None
