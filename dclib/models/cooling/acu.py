from typing import Optional
from dclib.models.base import BaseModel


class ACUCoolingModel(BaseModel):
    """ Model of ACU cooling properties
    Please refer to the following link for more information:
    Fan: https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-fans.html#fanvariablevolume
    Coil: https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-heating-and-cooling-coils.html#coilcoolingwater
    """
    cooling_type: Optional[str] = "CW"  # DX, CW, etc.
    cooling_capacity: Optional[float | str] = 60  # unit(kW)
    pressure_rise: Optional[float | str] = 500.0  # unit(Pa)
    # for chilled-water type cooling coil
    maximum_flow_rate: Optional[float | str] = "autosize"
    design_water_flow_rate: Optional[float | str] = "autosize"
    design_air_flow_rate: Optional[float | str] = "autosize"
    design_inlet_water_temperature: Optional[float | str] = "autosize"
    design_inlet_air_temperature: Optional[float | str] = "autosize"
    design_outlet_air_temperature: Optional[float | str] = "autosize"
    design_inlet_air_humidity_ratio: Optional[float | str] = "autosize"
    design_outlet_air_humidity_ratio: Optional[float | str] = "autosize"
    design_water_temperature_difference: Optional[float] = 8.0
    condensate_collection_water_storage_tank_name: Optional[str] = ""
    type_of_analysis: Optional[str] = "SimpleAnalysis"
    heat_exchanger_configuration: Optional[str] = "CounterFlow"
    # for DX-type cooling coil
    gross_rated_total_cooling_capacity: Optional[float | str] = ""
    gross_rated_sensible_heat_ratio: Optional[float | str] = ""
    gross_rated_cooling_cop: Optional[float | str] = ""
    rated_air_flow_rate: Optional[float | str] = ""
    minimum_outdoor_dryBulb_temperature_for_compressor_operation: Optional[float | str] = ""
    nominal_time_for_condensate_removal_to_begin: Optional[float | str] = ""
    ratio_of_initial_moisture_evaporation_rate_and_steady_state_evaporation_rate: Optional[float | str] = ""
    maximum_cycling_rate: Optional[float | str] = ""
    latent_capacity_time_constant: Optional[float | str] = ""
    condenser_type: Optional[str] = "AirCooled"
    # for DX ACU units
    evaporative_condenser_effectiveness: Optional[float | str] = ""
    evaporative_condenser_air_flow_rate: Optional[float | str] = ""
    crankcase_heater_capacity: Optional[float | str] = ""
    maximum_outdoor_dryBulb_temperature_for_crankcase_heater_operation: Optional[float | str] = ""
    basin_heater_capacity: Optional[float | str] = ""
    basin_heater_setpoint_temperature: Optional[float | str] = ""
    basin_heater_operating_schedule_name: Optional[str] = ""
