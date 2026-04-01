from typing import Optional

from dclib.cooling.common.loop import Loop
from dclib.models.base import BaseModel


class MetaLoop(BaseModel):
    """
    Metadata for defining a chiller plant. For the meta info, we define the following:
    - load_distribution_scheme: the scheme for distributing the load among the chillers
    - plant_loop_demand_calculation_scheme: the scheme for calculating the plant loop demand
    - common_pipe_simulation: the scheme for simulating the common pipe
    - setpoint_manager: whether to use setpoint manager
    """
    maximum_loop_flow_rate: Optional[float | str] = "autosize"
    minimum_loop_flow_rate: Optional[float | str] = 0.0


class LiquidFlowLoops(Loop):
    """
    Liquid flow loops are used to define the liquid flow loop. It contains the following attributes:
    - supply_branches: the supply branches of the liquid flow loop
    - demand_branches: the demand branches of the liquid flow loop
    - sizing: the sizing parameters of the liquid flow loop
    """
    meta: Optional[MetaLoop] = MetaLoop()
