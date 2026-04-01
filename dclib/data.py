"""
Inputs values for the CFD model
"""

from typing import OrderedDict, Optional, Dict

from dclib.models.base import BaseModel


class ACUInputs(BaseModel):
    supply_air_temperature: Optional[float] = None  # unit(C)
    supply_air_volume_flow_rate: Optional[float] = None  # unit(m3/s)
    acu_switch: Optional[bool] = True
    valid_id: Optional[str] = None # Only letters, digits, and underscores are allowed. 


class DehumidifierInputs(BaseModel):
    supply_air_temperature: Optional[float] = None  # unit(C)
    supply_air_volume_flow_rate: Optional[float] = None  # unit(m3/s)
    supply_air_relative_humidity: Optional[float] = None  # unit(%)


class ServerInputs(BaseModel):
    input_power: Optional[float] = None  # unit(W)
    input_power_percentage: Optional[float] = None  # unit(%)
    air_volume_flow_rate: Optional[float] = None  # unit(m3/s)
    liquid_percentage: Optional[float] = None  # unit(%)
    liquid_mass_flow_rate: Optional[float] = None  # unit(m3/s)
    server_switch: Optional[bool] = True
    valid_id: Optional[str] = None # Only letters, digits, and underscores are allowed. 

class CDUInputs(BaseModel):
    cooling_water_supply_temperature_sp: Optional[float] = None  # unit(C)
    chilled_water_supply_temperature: Optional[float] = None  # unit(C)
    chilled_water_mass_flow_rate: Optional[float] = None  # unit(m3/s)


class SensorMeasurements(BaseModel):
    temperature: Optional[float] = None  # unit(C)
    relative_humidity: Optional[float] = None  # unit(%)


class Inputs(BaseModel):
    acus: Optional[OrderedDict[str, ACUInputs]] = None
    dehumidifiers: Optional[OrderedDict[str, DehumidifierInputs]] = None
    servers: Optional[OrderedDict[str, ServerInputs]] = None
    cdus: Optional[OrderedDict[str, CDUInputs]] = None

    @property
    def format(self) -> Dict:
        data = {
            "supply_air_temperatures": {},
            "supply_air_volume_flow_rates": {},
            "supply_air_relative_humidities": {},
            "server_powers": {}
        }
        if self.acus is not None:
            for acu, val in self.acus.items():
                data["supply_air_temperatures"].update({acu: val.supply_air_temperature})
                data["supply_air_volume_flow_rates"].update({acu: val.supply_air_volume_flow_rate})

        if self.servers is not None:
            for server, val in self.servers.items():
                data["server_powers"].update({server: val.input_power})

        if self.dehumidifiers is not None:
            for dehumidifier, val in self.dehumidifiers.items():
                data["supply_air_temperatures"].update({dehumidifier: val.supply_air_temperature})
                data["supply_air_volume_flow_rates"].update({dehumidifier: val.supply_air_volume_flow_rate})
                data["supply_air_relative_humidities"].update({dehumidifier: val.supply_air_relative_humidity})

        return data


class Labels(BaseModel):

    sensor_measurements: OrderedDict[str, SensorMeasurements]

    @property
    def format(self) -> Dict:
        data = {
            "temperatures": {},
            "relative_humidities": {}
        }
        for sensor, val in self.sensor_measurements.items():
            if val.temperature is not None:
                data["temperatures"].update({sensor: val.temperature})
            if val.relative_humidity is not None:
                data["relative_humidities"].update({sensor: val.relative_humidity})
        return data


class CFDData(BaseModel):
    inputs: Inputs
    labels: Labels

    @property
    def format(self) -> Dict:
        data = {
            "inputs": self.inputs.format,
            "labels": self.labels.format
        }
        return data
