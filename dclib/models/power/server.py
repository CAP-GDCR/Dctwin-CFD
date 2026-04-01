from typing import Optional
from dclib.models.base import BaseModel


class ServerPowerModel(BaseModel):
    """
    Model of server power properties. This model assumes that the server power is a linear function of the CPU, memory
    and disk power. Thus, only the rated power of each component is needed.
    """
    rated_power: Optional[float] = None # unit(W)
    rated_cpu_power: Optional[float] = None  # unit(W)
    rated_mem_power: Optional[float] = None  # unit(W)
    rated_disk_power: Optional[float] = None  # unit(W)
