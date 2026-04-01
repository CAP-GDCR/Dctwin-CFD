"""Panel object
"""
from typing import Optional, OrderedDict
from pydantic import Field

from dclib.models.geometry import Opening
from dclib.models.base import BaseModel


class PanelGeometry(BaseModel):
    """
    Panel geometry object. For a panel, its height and openings are required.
    """
    height: float
    openings: OrderedDict[str, Opening] = Field(default_factory=dict)


class Panel(BaseModel):
    """
    Panel object. For a panel, its geometry info is required.
    """
    uid: Optional[str] = ""
    geometry: PanelGeometry
    meta: Optional[OrderedDict] = Field(default_factory=dict)
