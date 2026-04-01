from typing import Optional
from dclib.models.base import BaseModel


class BoxFaces(BaseModel):
    top: bool
    bottom: bool
    front: bool
    rear: bool
    left: bool
    right: bool


class BoxGeometryModel(BaseModel):
    faces: Optional[BoxFaces] = None
