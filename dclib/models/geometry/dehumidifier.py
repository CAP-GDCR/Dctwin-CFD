from typing import Optional
from pydantic import Field
from dclib.models.base import BaseModel

from .basics import Size, Face, Vertex


class DehumidifierFace(BaseModel):
    """
    ACU face geometry model.
    """
    side: Face = Field(default=Face)
    width: float = None
    length: float = None
    offset: Vertex = Field(default=Vertex)


class DehumidifierGeometryModel(BaseModel):
    """
    ACU geometry model. The ACU geometry is defined by the size of the ACU and the supply and return face of the ACU.
    """
    size: Optional[Size] = Field(default_factory=Size)
    supply_face: Optional[DehumidifierFace] = Field(default_factory=DehumidifierFace)
    return_face: Optional[DehumidifierFace] = Field(default_factory=DehumidifierFace)
