from dclib.models.base import BaseModel
from typing import Optional


class HeatExchangerCoolingModel(BaseModel):
    """
    Please refer to the EnergyPlus Heat Exchanger documentation for more information.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-condenser-equipment.html#heatexchangerfluidtofluid
    """
    loop_demand_side_design_flow_rate: float | str = "autosize"
    loop_supply_side_design_flow_rate: float | str = "autosize"
    loop_supply_side_design_temperature: float | str = "autosize"
    loop_demand_side_design_temperature: float | str = "autosize"
    heat_exchanger_model_type: str = "CrossFlow"
    heat_exchanger_u_factor_times_area_value: float | str = "autosize"
    control_type: str = "CoolingSetpointModulated"
    minimum_temperature_difference_to_activate_heat_exchanger: float = 0.0
    heat_transfer_metering_end_use_type: str = "FreeCooling"
    component_override_cooling_control_temperature_mode: str = ""
    sizing_factor: float | str = 1.0
    operation_minimum_temperature_limit: float | str = ""
    operation_maximum_temperature_limit: float | str = ""

    """ heat exchanger in self-defined heat exchanger model """
    material: Optional[str] = "copper"
    thermal_conductivity: Optional[float] = 400.0

