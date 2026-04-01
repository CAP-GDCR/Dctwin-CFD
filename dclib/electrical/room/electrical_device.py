""" Zone Electrical Devices (Excluding IT Equipment)
Heat gains from ITE are major source of internal gains in a data center.
Other sources of internal gains are lighting, people and electrical equipment
"""
from dclib.models.heat_gains.lightning import LightModel
from dclib.models.heat_gains.electric_equipment import ElectricEquipmentModel
from dclib.models.heat_gains.people import PeopleModel
from typing import List, Optional


class Light(LightModel):
    """
    Please refer to the following link for more details:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-demand-limiting-controls.html#demandmanagerlights
    """
    uid: Optional[str] = ""
    name: Optional[str] = ""
    zone: str
    lightning_power: float = None
    model: Optional[str] = None

class ElectricEquipment(ElectricEquipmentModel):
    """
    Please refer to the following link for more details:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-internal-gains-people-lights-other.html#electricequipment
    """
    uid: Optional[str] = ""
    name: Optional[str] = ""
    zone: str
    model: Optional[str] = None

class People(PeopleModel):
    """
    Please refer to the following link for more details:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-internal-gains-people-lights-other.html#people
    """
    uid: Optional[str] = ""
    name: Optional[str] = ""
    zone: str
    number_of_people: int = None
    model: Optional[str] = None
