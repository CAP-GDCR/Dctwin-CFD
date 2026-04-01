""" Box object
"""
from typing import Optional, OrderedDict
from pydantic import Field
from dclib.models.base import BaseModel

from dclib.models.geometry import (
    Vertex,
    Size,
    Face,
    Opening,
    BoxGeometryModel,
)


class BoxGeometry(BoxGeometryModel):
    """
    Box geometry object. It is used to define the geometry of a box.
    :param model: The model of the box.
    :param location: The location of the box.
    :param size: The size of the box.
    :param openings_side: The side of the box where the openings are located.
    :param openings: The openings of the box.
    """
    model: Optional[str] = None
    location: Vertex
    size: Size
    openings_side: Optional[Face] = None
    openings: Optional[OrderedDict[str, Opening]] = None
    orientation: Optional[int] = 0


class Box(BaseModel):
    """
    Box object inside a data hall. A box is an abstract 3D object with a size and location in space.
    The box can be used to define rectangular objects such as pillars, containers, ducts, etc.
    The box can have openings on its faces. The openings are used to define the airflow between boxes.
    """
    uid: Optional[str] = ""
    geometry: BoxGeometry
    meta: Optional[OrderedDict] = Field(default_factory=dict)
