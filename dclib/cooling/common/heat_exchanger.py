from typing import Optional, Any
from pydantic import Field

from dclib.models.base import BaseModel
from dclib.models.cooling.heat_exchanger import HeatExchangerCoolingModel
from dclib.models.geometry.heat_exchanger import HeatExchangerGeometryModel


class HXCooling(HeatExchangerCoolingModel):
    """
    Object that represents the cooling state of a fluid-to-fluid heat exchanger.
    :param model: The cooling model of the heat exchanger.
    """
    model: Optional[str] = None


class HXGeometry(HeatExchangerGeometryModel):
    """
    Object that represents the geometry of a fluid-to-fluid heat exchanger.
    :param model: The geometry model of the heat exchanger.
    """
    model: Optional[str] = None


class HeatExchanger(BaseModel):
    """
    Object that represents a fluid-to-fluid heat exchanger. It inherits from the BaseModel class. It can be used to
    model the water-side economizer. It can provide free cooling when the outdoor air temperature is lower than the
    return water temperature. The cooling object must be defined.
    :param cooling: The cooling object of the heat exchanger.
    :param meta: The meta parameters of the chiller.
    """
    uid: Optional[str] = ""
    other_loop_side: Optional[str] = ""
    upstream_component: Optional[str] = None
    cooling: Optional[HXCooling] = Field(default_factory=HXCooling)
    geometry: Optional[HXGeometry] = Field(default_factory=HXGeometry)
    model: Optional[Any] = None
    meta: Optional[dict] = Field(default_factory=dict)
