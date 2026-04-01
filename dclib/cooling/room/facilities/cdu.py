from typing import Any
from typing import Optional
from pydantic import Field

from dclib.models.base import BaseModel
from dclib.models.geometry import Vertex
from dclib.models.geometry.cdu import CDUGeometryModel

from dclib.cooling.common.pump import Pump
from dclib.cooling.common.heat_exchanger import HeatExchanger


class CDUGeometry(CDUGeometryModel):
    """
    CDU geometry model. 
    :param model: The model type of the CDU geometry. This is used to determine the type of CDU geometry model to use.
    """
    model: Optional[str] = None
    orientation: int = 0
    location: Vertex = None


class CDUConstructions(BaseModel):
    """
    CDU construction model.
    :param heat_exchanger: The heat exchangers of the CDU.
    :param pump: The pump of the CDU.
    """
    heat_exchanger: Optional[HeatExchanger] = Field(default_factory=HeatExchanger)
    pump: Optional[Pump] = Field(default_factory=Pump)


class CDUMeta(BaseModel):
    """
    CDU metamodel.
    :param racks: The racks that CDU is connected to.
    :param room: The room that CDU is located in.
    """
    racks: list[str] = []
    room: str = ""


class CDU(BaseModel):
    """
    Object that represents a CDU. It inherits from the BaseModel class.
    :param uid: The unique identifier of the CDU.
    :param geometry: The geometry of the CDU.
    :param constructions: The constructions of the CDU.
    :param model: The model of the CDU.
    :param meta: The meta parameters of the CDU.
    """
    uid: Optional[str] = ""
    geometry: Optional[CDUGeometry] = Field(default_factory=CDUGeometry)
    constructions: Optional[CDUConstructions] = Field(default_factory=CDUConstructions)
    model: Optional[Any] = None
    meta: Optional[CDUMeta] = Field(default_factory=CDUMeta)
