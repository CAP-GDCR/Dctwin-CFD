""" Cylinder Pipe Components
"""
from typing import Optional, List, OrderedDict, Any

from dclib.models.base import BaseModel
from dclib.models.geometry import Vertex, PipeGeometryModel


class PipeGeometry(PipeGeometryModel):
    """
    Pipe geometry model object. This object is used to define the geometry of a pipe. The geometry is defined by a
    series of vertices. The vertices are defined by their x, y, and z coordinates. The vertices are connected by
    segments. Each segment is defined by a start and end vertex. The segments are ordered in a dictionary. The key of
    the dictionary is the name of the segment. The value of the dictionary is a list of vertices that define the
    segment.

    :param model: The model type of the pipe geometry. This is used to determine the type of pipe geometry model to
        use. The default value is an empty string.
    :param start_vertex: The start vertex of the pipe geometry. This field is required.
    :param end_vertex: The end vertex of the pipe geometry. This field is required.
    :param segments: The segments of the pipe geometry. This field is required.
    """
    model: Optional[str]
    start_vertex: Vertex
    end_vertex: Vertex
    segments: OrderedDict[str, List[Vertex]]


class Pipe(BaseModel):
    """
    Pipe model object. This object is used to define a pipe. The pipe is defined by a pipe geometry model. The pipe
    geometry model defines the geometry of the pipe.

    :param geometry: The geometry of the pipe. This field is required.
    """
    uid: Optional[str] = ""
    geometry: Optional[PipeGeometry] = None
    model: Optional[Any] = None
    meta: Optional[dict] = None
