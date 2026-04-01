from typing import Optional, Any
from pydantic import Field
from dclib.models.base import BaseModel
from dclib.models.cooling.tank import TankCoolingModel, Indicator
from dclib.models.control.tank import TankControlModel


class TankCooling(TankCoolingModel):
    """
    Cooling tank cooling status object. It inherits from TankCoolingModel where the cooling parameters of
    the cooling tank are defined.
    :param model: the cooling model name of the cooling tank
    """
    model: Optional[str] = None
    tank_temperature: Optional[float] = 21.0  # unit(C)
    ambient_temperature_indicator: Optional[Indicator] = Indicator.outdoors
    ambient_temperature_zone_name: Optional[str] = ""
    ambient_temperature_acu_name: Optional[str] = ""


class TankControl(TankControlModel):

    model: Optional[str] = None


class Tank(BaseModel):
    """
    Cooling tank object.
    power parameters of the cooling tank are defined.
    :param uid: the unique identifier of the cooling tank
    :param other_loop_side: the other loop side of the cooling tank
    :param upstream_component: the upstream component of the cooling tank
    :param cooling: the cooling object of the cooling tank
    :param control: the control object of the cooling tank
    :param model: the model of the cooling tank
    :param meta: the metadata of the cooling tank
    """
    uid: Optional[str] = ""
    other_loop_side: Optional[str] = ""
    upstream_component: Optional[str] = None
    cooling: Optional[TankCooling] = Field(default_factory=TankCooling)
    control: Optional[TankControl] = Field(default_factory=TankControl)
    model: Optional[Any] = None
    meta: Optional[dict] = None
