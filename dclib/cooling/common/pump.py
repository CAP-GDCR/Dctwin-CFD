from typing import Optional, Any
from pydantic import Field

from dclib.models.base import BaseModel
from dclib.models.cooling.pump import PumpCoolingModel
from dclib.models.power.pump import PumpPowerModel
from dclib.models.control.pump import PumpControlModel


class PumpCoolingOperating(BaseModel):
    """
    Object that represents the operational cooling status of a pump.
    :param water_mass_flow_rate: the operating water mass flow rate of the pump
    """
    water_mass_flow_rate: Optional[float] = 1  # unit(kg/s)


class PumpPowerOperating(BaseModel):
    """
    Object that represents the operational power status of a pump.
    :param pump_power: the operating power of the pump
    """
    pump_power: Optional[float] = 1000  # unit(W)
    pump_status: Optional[int] = 1  # 1: on, 0: off


class PumpCooling(PumpCoolingModel):
    """
    Object that represents the cooling status of a pump. It inherits from the PumpCoolingModel class, where the design
    cooling parameters are defined. The operating attribute defines the operational cooling parameters of the pump.
    :param model: cooling model name of the pump
    :param operating: operating cooling parameters of the pump
    """
    model: Optional[str] = None
    operating: PumpCoolingOperating = Field(default_factory=PumpCoolingOperating)


class PumpPower(PumpPowerModel):
    """
    Object that represents the power status of a pump. It inherits from the PumpPowerModel class, where the design
    power parameters are defined. The operating attribute defines the operational power parameters of the pump.

    :param model: power model name of the pump
    :param operating: operating power parameters of the pump
    """
    model: Optional[str] = None
    operating: PumpPowerOperating = Field(default_factory=PumpPowerOperating)


class PumpControl(PumpControlModel):
    """
    Object that represents the control status of a pump. It inherits from the PumpControlModel class, where the design
    parameters related to how the pump is controlled are defined.
    :param model: control model name of the pump
    """
    model: Optional[str] = None


class Pump(BaseModel):
    """
    Object that represents a pump. It contains the cooling, power and control attributes, which are objects that
    represent the cooling, power and control status of the pump, respectively. The meta attribute is a dictionary that
    contains additional information about the pump.
    :param uid: unique identifier of the pump
    :param upstream_component: the upstream component of the pump
    :param cooling: cooling status of the pump
    :param power: power status of the pump
    :param control: control status of the pump
    :param meta: additional information about the pump
    """
    uid: Optional[str] = ""
    upstream_component: Optional[str] = None
    cooling: PumpCooling = Field(default_factory=PumpCooling)
    power: PumpPower = Field(default_factory=PumpPower)
    control: PumpControl = Field(default_factory=PumpControl)
    model: Optional[Any] = None
    meta: Optional[dict] = None
