from typing import Optional

from dclib.models.base import BaseModel
from dclib.cooling.common.loop import Loop
from .sizing import SizingPlant


class MetaLoop(BaseModel):
    """
    Metadata for defining a chiller plant. For the meta info, we define the following:
    - load_distribution_scheme: the scheme for distributing the load among the chillers
    - plant_loop_demand_calculation_scheme: the scheme for calculating the plant loop demand
    - common_pipe_simulation: the scheme for simulating the common pipe
    - setpoint_manager: whether to use setpoint manager
    """
    load_distribution_scheme: Optional[str] = "SequentialLoad"
    plant_loop_demand_calculation_scheme: Optional[str] = "SingleSetpoint"
    common_pipe_simulation: Optional[str] = "None"
    setpoint_manager: Optional[bool] = True
    minimum_setpoint_temperature: Optional[float] = 10.0
    maximum_setpoint_temperature: Optional[float] = 50.0
    offset_temperature_difference: Optional[float] = 0.0
    maximum_loop_flow_rate: Optional[float | str] = "autosize"
    minimum_loop_flow_rate: Optional[float | str] = 0.0


class ChilledWaterLoops(Loop):
    """
    Chilled water loops are used to define the chilled water loop. It contains the following attributes:
    - supply_branches: the supply branches of the chilled water loop
    - demand_branches: the demand branches of the chilled water loop
    - sizing: the sizing parameters of the chilled water loop
    """
    sizing: SizingPlant
    meta: Optional[MetaLoop] = MetaLoop()


class CondenserWaterLoops(Loop):
    """
    Condenser water loops are used to define the condenser water loop. It contains the following attributes:
    - supply_branches: the supply branches of the condenser water loop
    - demand_branches: the demand branches of the condenser water loop
    - sizing: the sizing parameters of the condenser water loop
    """
    sizing: SizingPlant
    meta: Optional[MetaLoop] = MetaLoop()


class SecondaryChilledWaterLoops(Loop):
    """
    Secondary Chilled water loops are used to define the secondary chilled water loop. It contains the following attributes:
    - supply_branches: the supply branches of the secondary chilled water loop
    - demand_branches: the demand branches of the secondary chilled water loop
    - sizing: the sizing parameters of the secondary chilled water loop
    """
    sizing: SizingPlant
    meta: Optional[MetaLoop] = MetaLoop()
