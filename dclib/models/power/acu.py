from typing import Optional
from dclib.models.base import BaseModel


class ACUPowerModel(BaseModel):
    """
    Please refer to the following link for more information:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-fans.html#fanvariablevolume
    """
    fan_total_efficiency: Optional[float | str] = 0.7
    fan_power_minimum_flow_rate_input_method: Optional[str] = "Fraction"
    fan_power_minimum_flow_fraction: Optional[float | str] = 0.0
    fan_power_minimum_air_flow_rate: Optional[float | str] = ""
    motor_efficiency: Optional[float | str] = 0.9
    motor_in_airstream_fraction: Optional[float] = 1.0
    fan_power_coefficient_1: Optional[float] = 0.35071223
    fan_power_coefficient_2: Optional[float] = 0.30850535
    fan_power_coefficient_3: Optional[float] = -0.54137364
    fan_power_coefficient_4: Optional[float] = 0.87198823
    fan_power_coefficient_5: Optional[float] = 0.0
    fan_power_curve_model: Optional[str] = ""
    # for DX-type cooling coil
    rated_evaporator_fan_power_per_volume_flow_rate: Optional[float | str] = ""
    evaporative_condenser_pump_rated_power_consumption: Optional[float | str] = ""
