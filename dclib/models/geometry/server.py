from typing import Optional

from dclib.models.base import BaseModel
from .basics import Vertex, Face

class ServerFace(BaseModel):
    side: Face
    width: float
    length: float
    offset: Vertex

class ServerGeometryModel(BaseModel):
    """
    Server geometry model.
    slot_occupation: Number of slots occupied by the server.
    depth: Server depth.
    width: Server width.
    """
    slot_occupation: Optional[int] = 0
    depth: Optional[float] = 0
    width: Optional[float] = 0
    inlet_face: Optional[ServerFace] = None
    outlet_face: Optional[ServerFace] = None
