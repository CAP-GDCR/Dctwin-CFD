""" Cylinder Pipe Components
"""
from pydantic import Field

from typing import Optional, Any

from dclib.models.base import BaseModel
from dclib.models.geometry import PipeGeometryModel


class PipeGeometry(PipeGeometryModel):
    """
    Pipe geometry model object. This object is used to define the geometry of a pipe. The geometry is defined by a
    series of vertices. The vertices are defined by their x, y, and z coordinates. The vertices are connected by
    segments. Each segment is defined by a start and end vertex. The segments are ordered in a dictionary. The key of
    the dictionary is the name of the segment. The value of the dictionary is a list of vertices that define the
    segment.

    :param model: The model type of the pipe geometry. This is used to determine the type of pipe geometry model to
        use. The default value is an empty string.
    """
    model: Optional[str] = None


class PipeMeta(BaseModel):
    """
    Pipe metamodel.
    :param server: name of the server, "" means main pipe
    """
    server: str = ""


class Pipe(BaseModel):
    """
    Pipe model object. This object is used to define a pipe. The pipe is defined by a pipe geometry model. The pipe
    geometry model defines the geometry of the pipe.

    :param geometry: The geometry of the pipe. This field is required.
    """
    uid: Optional[str] = ""
    geometry: Optional[PipeGeometry] = Field(default_factory=PipeGeometry)
    model: Optional[Any] = None
    meta: Optional[PipeMeta] = Field(default_factory=PipeMeta)
