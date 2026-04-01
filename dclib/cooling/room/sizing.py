from typing import Optional
from dclib.models.base import BaseModel
from pydantic import Field


class SizingZone(BaseModel):
    """
    Please refer to the following link for more details on the sizing zone object:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-design-objects.html#sizingzone
    """
    zone_or_zonelist_name: str
    zone_cooling_design_supply_air_temperature_input_method: Optional[
        str
    ] = "SupplyAirTemperature"
    zone_cooling_design_supply_air_temperature: Optional[float] = 18.0
    zone_cooling_design_supply_air_temperature_difference: Optional[float] = 11.0
    zone_heating_design_supply_air_temperature_input_method: Optional[
        str
    ] = "SupplyAirTemperature"
    zone_heating_design_supply_air_temperature: Optional[float] = 40.0
    zone_heating_design_supply_air_temperature_difference: Optional[float] = 30.0
    zone_cooling_design_supply_air_humidity_ratio: Optional[float] = 0.0075  # \minimum 0.0
    zone_heating_design_supply_air_humidity_ratio: Optional[float] = 0.004  # \minimum 0.0
    design_specification_outdoor_air_object_name: Optional[str] = "Outdoor Air Inlet"
    zone_heating_sizing_factor: Optional[float] = 1.25
    zone_cooling_sizing_factor: Optional[float] = 1.15
    cooling_design_air_flow_method: Optional[str] = "DesignDay"
    cooling_design_air_flow_rate: Optional[float] = 0.0
    cooling_minimum_air_flow_per_zone_floor_area: Optional[float] = 0.000762
    cooling_minimum_air_flow: Optional[float] = 0.0
    cooling_minimum_air_flow_fraction: Optional[float] = 0.0
    heating_design_air_flow_method: Optional[str] = "DesignDay"
    heating_design_air_flow_rate: Optional[float] = 0.0
    heating_maximum_air_flow_per_zone_floor_area: Optional[float] = 0.002032
    heating_maximum_air_flow: Optional[float] = 0.1415762
    heating_maximum_air_flow_fraction: Optional[float] = 0.3
    design_specification_zone_air_distribution_object_name: Optional[str] = ""
    account_for_dedicated_outdoor_air_system: Optional[str] = "No"
    dedicated_outdoor_air_system_control_strategy: Optional[str] = "NeutralSupplyAir"
    dedicated_outdoor_air_low_setpoint_temperature_for_design: Optional[str] = "autosize"
    dedicated_outdoor_air_high_setpoint_temperature_for_design: Optional[str] = "autosize"

    # DesignSpecification:OutdoorAir
    design_specification_outdoor_air_method: Optional[float] = "flow/person"
    design_specification_outdoor_air_flow_per_person: Optional[float] = 0.0125
    design_specification_outdoor_air_flow_per_zone_floor_area: Optional[float] = 0.0
    design_specification_outdoor_air_flow_per_zone: Optional[float] = 0.0
    design_specification_outdoor_air_flow_air_changes_per_hour: Optional[float] = 0.0


class SizingSystem(BaseModel):
    """
    Please refer to the following link for more details on the sizing of air cooling system:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-design-objects.html#sizingsystem
    """
    type_of_load_to_size_on: str = "Sensible"
    design_outdoor_air_flow_rate: Optional[float | str] = "autosize"
    central_heating_maximum_system_air_flow_ratio: Optional[float | str] = 0.4
    preheat_design_temperature: Optional[float] = 7.0
    preheat_design_humidity_ratio: Optional[float] = 0.0085
    precool_design_temperature: Optional[float] = 11.0
    precool_design_humidity_ratio: Optional[float] = 0.0085
    central_cooling_design_supply_air_temperature: Optional[float] = 16.0
    central_heating_design_supply_air_temperature: Optional[float] = 25.0
    type_of_zone_sum_to_use: Optional[str] = "NonCoincident"
    outdoor_air_in_cooling_100: Optional[str] = "Yes"
    outdoor_air_in_heating_100: Optional[str] = "No"
    central_cooling_design_supply_air_humidity_ratio: Optional[float] = 0.0085
    central_heating_design_supply_air_humidity_ratio: Optional[float] = 0.0085
    cooling_supply_air_flow_rate_method: Optional[str] = "DesignDay"
    cooling_supply_air_flow_rate: Optional[float | str] = ""
    cooling_supply_air_flow_rate_per_floor_area: Optional[float | str] = ""
    cooling_fraction_of_autosized_cooling_supply_air_flow_rate: Optional[float | str] = ""
    cooling_supply_air_flow_rate_per_unit_cooling_capacity: Optional[float | str] = ""
    heating_supply_air_flow_rate_method: Optional[str] = "DesignDay"
    heating_supply_air_flow_rate: Optional[float | str] = ""
    heating_supply_air_flow_rate_per_floor_area: Optional[float | str] = ""
    heating_fraction_of_autosized_cooling_supply_air_flow_rate: Optional[float | str] = ""
    heating_fraction_of_autosized_heating_supply_air_flow_rate: Optional[float | str] = ""
    heating_supply_air_flow_rate_per_unit_heating_capacity: Optional[float | str] = ""
    system_outdoor_air_method: Optional[float | str] = ""
    zone_maximum_outdoor_air_fraction: Optional[float | str] = ""
    cooling_design_capacity_method: Optional[float | str] = ""
    cooling_design_capacity: Optional[float | str] = ""
    cooling_design_capacity_per_floor_area: Optional[float | str] = ""
    fraction_of_autosized_cooling_design_capacity: Optional[float | str] = ""
    heating_design_capacity_method: Optional[float | str] = ""
    heating_design_capacity: Optional[float | str] = ""
    heating_design_capacity_per_floor_area: Optional[float | str] = ""
    fraction_of_autosized_heating_design_capacity: Optional[float | str] = ""
    central_cooling_capacity_control_method: Optional[float | str] = ""
    supply_air_temperature_setpoint: Optional[float | str] = 20
    design_supply_air_flow_rate: Optional[float | str] = 'autosize'
    supply_air_maximum_humidity_ratio_setpoint: Optional[float | str] = 0.012
    supply_air_minimum_humidity_ratio_setpoint: Optional[float | str] = 0.010


class Sizing(BaseModel):
    sizing_system: Optional[SizingSystem] = Field(default_factory=SizingSystem)
    sizing_zone: Optional[SizingZone] = Field(default_factory=SizingZone)
