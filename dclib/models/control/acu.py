from typing import Optional
from dclib.models.base import BaseModel


class ACUControlModel(BaseModel):
    """
    Air Conditioning Unit Control Model. This is used for Temperature and Humidity control.
    Please refer to the documentation for more information.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-zone-controls-thermostats.html#zonecontrolthermostat
    """
    control_variable: Optional[str] = "Temperature"
    supply_air_temperature_setpoint: Optional[float] = 20.0
    supply_air_humidity_ratio_setpoint: Optional[float] = 0.0085 if control_variable == "HumidityRatio" or control_variable == "TemperatureAndHumidityRatio" else None
    supply_air_maximum_humidity_ratio_setpoint: Optional[float] = 0.009 if control_variable == "HumidityRatio" or control_variable == "TemperatureAndHumidityRatio" else None
    supply_air_minimum_humidity_ratio_setpoint: Optional[float] = 0.008 if control_variable == "HumidityRatio" or control_variable == "TemperatureAndHumidityRatio" else None
    