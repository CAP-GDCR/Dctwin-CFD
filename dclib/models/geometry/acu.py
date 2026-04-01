from typing import Optional
from pydantic import Field
from dclib.models.base import BaseModel
from dclib.models.geometry.heat_exchanger import HeatExchangerGeometryModel
from .basics import Size, Face, Vertex


class ACUFace(BaseModel):
    """
    ACU face geometry model.
    """
    side: Face = Field(default=Face)
    width: float = None
    length: float = None
    offset: Vertex = Field(default=Vertex)


class ACUGeometryModel(BaseModel):
    """
    ACU geometry model. The ACU geometry is defined by the size of the ACU and the supply and return face of the ACU.
    """
    size: Optional[Size] = Field(default_factory=Size)
    supply_face: Optional[ACUFace] = Field(default_factory=ACUFace)
    return_face: Optional[ACUFace] = Field(default_factory=ACUFace)
    coil_geometry: Optional[HeatExchangerGeometryModel] = Field(default_factory=HeatExchangerGeometryModel)
