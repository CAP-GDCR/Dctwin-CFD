from typing import Optional
from dclib.models.base import BaseModel


class CoolingTowerCoolingModel(BaseModel):
    """
    See documentation for parameter details.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-condenser-equipment.html#coolingtowervariablespeed
    """
    model_type: Optional[str] = "YorkCalcUserDefined"
    model_coefficient_name: Optional[str] = "YorkCalc Default Tower Model"
    design_inlet_air_wet_bulb_temperature: Optional[float | str] = 25.6
    design_approach_temperature: Optional[float | str] = 3.9
    design_range_temperature: Optional[float | str] = 5.5
    design_water_flow_rate: Optional[float | str] = "autosize"
    design_air_flow_rate: Optional[float | str] = "autosize"
    minimum_air_flow_rate_ratio: Optional[float] = 0.2
    fraction_of_tower_capacity_in_free_convection_regime: Optional[float | str] = 0.125
    basin_heater_capacity: Optional[float | str] = 0.0
    basin_heater_setpoint_temperature: Optional[float | str] = 2.0
    basin_heater_operating_schedule_name: Optional[str | str] = ""
    evaporation_loss_mode: Optional[str] = "LossFactor"
    evaporation_loss_factor: Optional[float | str] = 0.2
    drift_loss_percent: Optional[float | str] = 0.008
    blowdown_calculation_mode: Optional[str] = "ConcentrationRatio"
    blowdown_concentration_ratio: Optional[float | str] = 3.0
    blowdown_makeup_water_usage_schedule_name: Optional[str] = ""
    supply_water_storage_tank_name: Optional[str] = ""
    outdoor_air_inlet_node_name: Optional[str] = ""
    number_of_cells: Optional[int] = 1
    cell_control: Optional[str] = "MinimalCell"
    cell_minimum_water_flow_rate_fraction: Optional[float] = 0.33
    cell_maximum_water_flow_rate_fraction: Optional[float] = 1.0
    sizing_factor: Optional[float] = 1.0
    end_use_subcategory: Optional[str] = "General"
