""" Room object
"""
import copy
from collections import OrderedDict
from typing import Optional, Union
from pydantic import Field
from dclib.models.geometry.basics import Size
from dclib.models.geometry.basics import Vertex
from dclib.ite.racks import Rack
from dclib.models.base import BaseModel
from dclib.models.geometry.rack import RackGeometryModel


class RowRackGeometry(RackGeometryModel):
    """ RowRackGeometry object in a data center """
    location: Vertex
    size: Optional[Size] = None
    orientation: int

class RowConstruction(BaseModel):
    """ Row construction is used to define the racks in a row """
    racks: OrderedDict[str, Rack]

class Row(BaseModel):
    """ Row object in a data center """
    uid: Optional[str] = ""
    geometry: RowRackGeometry
    constructions: Optional[RowConstruction] = None
    meta: Optional[OrderedDict] = Field(default_factory=dict)