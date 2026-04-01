"""Basic geometry
Unit: m (meter)
"""
from enum import Enum
from typing import Optional, List

from pydantic import field_validator

from dclib.models.base import BaseModel


class Vertex(BaseModel):
    """
    Vertex is a point in 3D space
    """
    x: float = None
    y: float = None
    z: float = None

    @field_validator("x", "y", "z")
    def float_check(cls, v):
        """
        Round to 3 decimal places
        :param v:
        :return:
        """
        return round(v, 3)


class Size(BaseModel):
    """
    Object that defines the sizing of another object
    """
    x: float = None
    y: float = None
    z: float = None

    @field_validator("x", "y", "z")
    def float_check(cls, v):
        """
        Round to 5 decimal places
        :param v:
        :return:
        """
        return round(v, 5)


class Face(str, Enum):
    """
    Face of a 3D object. Used to define the surface id of a 3D object
    """
    front = "front"
    rear = "rear"
    left = "left"
    right = "right"
    top = "top"
    bottom = "bottom"


class Opening(BaseModel):
    """
    Opening is a hole in a surface. It is defined by a location, size, and velocity
    """
    location: Vertex
    size: Size
    velocity: Optional[Size] = None


class Geometry(BaseModel):
    """
    Geometry of is a 3D object. It is defined by a list of vertices and a list of faces.
    """
    height: float
    plane: List[Vertex]
    starting_vertex_position: str = "LowerLeftCorner"
    vertex_entry_direction: str = "CounterClockWise"
    coordinate_system: str = "World"
    daylighting_reference_point_coordinate_system: str = "World"
    rectangular_surface_coordinate_system: str = "World"

    @classmethod
    def validate_height(cls, v):
        """
        Validate height of a geometry object.
        :param v:
        :return:
        """
        if v <= 0:
            raise ValueError("Height must be greater than 0")
        return v
