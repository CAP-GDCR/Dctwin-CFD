from typing import OrderedDict

from .plant_loops import ChilledWaterLoops, CondenserWaterLoops, SecondaryChilledWaterLoops

from dclib.models.base import BaseModel


class Plant(BaseModel):
    """
    Plant is used to define the plant loops. It contains the following attributes:
    - chilled_water_loops: the chilled water loops
    - condenser_water_loops: the condenser water loops
    """
    chilled_water_loops: OrderedDict[str, ChilledWaterLoops]
    condenser_water_loops: OrderedDict[str, CondenserWaterLoops] = None
    secondary_chilled_water_loops: OrderedDict[str, SecondaryChilledWaterLoops] = None
