""" Rack geometry model.
"""
from typing import Optional

from .basics import Size
from dclib.models.base import BaseModel
from .box import BoxFaces


class RackGeometryModel(BaseModel):
    """
    Rack geometry model.
    size: Rack size.
    slot: Number of slots. usually 42.
    first_slot_offset: The distance from the first slot to the bottom of the rack.
    """
    size: Optional[Size] = None
    slot: Optional[int] = None
    first_slot_offset: Optional[float] = None
    faces: Optional[BoxFaces] = None
