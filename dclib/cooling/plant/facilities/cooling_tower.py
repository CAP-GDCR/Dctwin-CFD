from typing import Optional, Any
from pydantic import Field
from dclib.models.base import BaseModel

from dclib.models.cooling.cooling_tower import CoolingTowerCoolingModel
from dclib.models.power.cooling_tower import CoolingTowerPowerModel


class CoolingTowerCoolingOperating(CoolingTowerCoolingModel):
    """
    Object that represents the operational cooling status of a cooling tower.
    :param water_mass_flow_rate: the operating water mass flow rate of the cooling tower
    """
    water_mass_flow_rate: Optional[float] = 1  # unit(kg/s)


class CoolingTowerPowerOperating(CoolingTowerPowerModel):
    """
    Object that represents the operational power status of a cooling tower.
    :param cooling_tower_fan_power: the operating power of the cooling tower
    """
    cooling_tower_fan_power: Optional[float] = 1000  # unit(W)
    cooling_tower_status: Optional[int] = 1  # 1: on, 0: off


class CoolingTowerCooling(CoolingTowerCoolingModel):
    """
    Cooling tower cooling status object. It inherits from CoolingTowerCoolingModel where the cooling parameters of
    the cooling tower are defined.
    :param model: the cooling model name of the cooling tower
    """
    model: Optional[str] = "YorkCalcUserDefined"
    operating: CoolingTowerCoolingOperating = Field(default_factory=CoolingTowerCoolingOperating)


class CoolingTowerPower(CoolingTowerPowerModel):
    """
    Cooling tower power status object. It inherits from CoolingTowerPowerModel where the power parameters of the
    cooling tower are defined.
    :param model: the power model name of the cooling tower
    """
    model: Optional[str] = None
    operating: CoolingTowerPowerOperating = Field(default_factory=CoolingTowerPowerOperating)


class CoolingTower(BaseModel):
    """
    Cooling tower object. It inherits from CoolingTowerCoolingModel and CoolingTowerPowerModel where the cooling and
    power parameters of the cooling tower are defined.
    :param cooling: the cooling object of the cooling tower
    :param power: the power object of the cooling tower
    """
    uid: Optional[str] = ""
    upstream_component: Optional[str] = None
    cooling: Optional[CoolingTowerCooling] = Field(default_factory=CoolingTowerCooling)
    power: Optional[CoolingTowerPower] = Field(default_factory=CoolingTowerPower)
    model: Optional[Any] = None
    meta: Optional[dict] = None
