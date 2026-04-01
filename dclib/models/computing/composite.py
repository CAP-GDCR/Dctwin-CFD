from typing import Optional, OrderedDict
from .server import ServerComputingModel

from dclib.models.base import BaseModel


class ComputingModel(BaseModel):
    """ Object standard compute property """
    servers: Optional[OrderedDict[str, ServerComputingModel]] = None
