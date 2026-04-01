from typing import Optional, OrderedDict
from .ite import ITEModel
from .lightning import LightModel
from .electric_equipment import ElectricEquipmentModel
from .people import PeopleModel

from dclib.models.base import BaseModel


class HeatGainsModel(BaseModel):
    """ Object standard heat gains property """
    ite: Optional[OrderedDict[str, ITEModel]] = None
    light: Optional[OrderedDict[str, LightModel]] = None
    electric_equipment: Optional[OrderedDict[str, ElectricEquipmentModel]] = None
    people: Optional[OrderedDict[str, PeopleModel]] = None
