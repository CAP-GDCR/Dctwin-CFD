from typing import Optional, Any

from pydantic import Field

from dclib.models.base import BaseModel
from dclib.models.cooling.chiller import ChillerCoolingModel
from dclib.models.power.chiller import ChillerPowerModel


class ChillerCoolingOperating(BaseModel):
    """
    Object that represents the operational cooling status of a chiller.
    :param partial_load: The fraction of the chiller's reference capacity that is currently being used.
    :param leaving_water_temperature: The temperature of the water leaving the chiller.
    """
    partial_load: Optional[float] = 1.0
    leaving_water_temperature: Optional[float] = 6.7


class ChillerPowerOperating(BaseModel):
    """
    Object that represents the operational power consumption status of a chiller.
    :param chiller_power: The power consumption of the chiller.
    """
    chiller_power: Optional[float] = 10000.0  # unit(W)
    chiller_status: Optional[int] = 1  # 1: on, 0: off


class ChillerCooling(ChillerCoolingModel):
    """
    Object that represents the cooling state of a chiller. It inherits from the ChillerCoolingModel class, where the
    design cooling parameters of the chiller are defined. The reference_capacity and reference_cop parameters are
    required to be defined here. The operating parameters are optional.
    :param model: The cooling model name of the chiller.
    :param reference_capacity: The reference capacity of the chiller.
    :param reference_cop: The reference COP of the chiller.
    :param operating: The operating parameters of the chiller.
    """
    model: Optional[str] = None
    operating: ChillerCoolingOperating = Field(default_factory=ChillerCoolingOperating)


class ChillerPower(ChillerPowerModel):
    """
    Object that represents the power state of a chiller. It inherits from the ChillerPowerModel class, where the design
    power parameters of the chiller are defined. The operating parameters are optional.
    :param model: The power model name of the chiller.
    :param operating: The operating parameters of the chiller.
    """
    model: Optional[str] = None
    operating: ChillerPowerOperating = Field(default_factory=ChillerPowerOperating)


class Chiller(BaseModel):
    """
    Object that represents a chiller. It inherits from the BaseModel class, where the meta parameters of the chiller are
    defined. The cooling and power objects must also be defined.
    :param uid: The unique identifier of the chiller.
    :param other_loop_side: The other loop side of the chiller.
        e.g. if the chiller is on the supply side of the chilled water loop, the other loop side is the demand side of condensed water loop
    :param upstream_component: The upstream component of the chiller, e.g. pump, heat exchanger.
    :param cooling: The cooling object of the chiller.
    :param power: The power object of the chiller.
    :param meta: The meta parameters of the chiller.
    """
    uid: Optional[str] = ""
    other_loop_side: Optional[str] = ""
    upstream_component: Optional[str] = None
    cooling: Optional[ChillerCooling] = Field(default_factory=ChillerCooling)
    power: Optional[ChillerPower] = Field(default_factory=ChillerPower)
    model: Optional[Any] = None
    meta: Optional[dict] = Field(default_factory=dict)
