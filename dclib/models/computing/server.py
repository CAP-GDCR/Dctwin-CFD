from typing import Optional
from dclib.models.base import BaseModel


class ServerComputingModel(BaseModel):
    """
    Server computing model. Here we define a common CPU server.
    """
    cpu_capacity: Optional[float] = 64
    mem_capacity: Optional[float] = 128
    disk_capacity: Optional[float] = 1000
