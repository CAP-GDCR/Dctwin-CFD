"""Rack object in a data center
"""
from typing import Optional, OrderedDict
from pydantic import Field
from dclib.models.base import BaseModel

from dclib.models.geometry.basics import Vertex
from dclib.models.geometry.rack import RackGeometryModel
from dclib.ite.servers.server import Server


class RackGeometry(RackGeometryModel):
    """
    Object that defines the geometry of a rack. The rack is defined by a location, orientation, and its geometry model.
    """
    model: Optional[str]
    location: Vertex
    orientation: int
    hasBlankingPanel: bool


class RackConstruction(BaseModel):
    """ Rack construction is used to define the servers in a rack
    """
    servers: OrderedDict[str, Server]


class Rack(BaseModel):
    """ Rack object in a data center """
    uid: Optional[str] = ""
    valid_id: Optional[str] = None # Only letters, digits, and underscores are allowed. 
    geometry: RackGeometry
    constructions: Optional[RackConstruction] = None
    meta: Optional[OrderedDict] = Field(default_factory=dict)
