from dclib.models.base import BaseModel
from typing import  Optional


class LightModel(BaseModel):
    schedule_name: Optional[str] = ""
    design_level_calculation_method: Optional[str] = "LightingLevel"
    lighting_power: Optional[float] = 284.0
    watts_per_zone_floor_area: Optional[float] = ""
    watts_per_person: Optional[float] = ""
    return_air_fraction: Optional[float] = 0.0
    fraction_radiant: Optional[float] = 0.0
    fraction_visible: Optional[float] = 0.0
    fraction_replaceable: Optional[float] = 1.0
    end_use_subcategory: Optional[str] = "General"
    return_air_fraction_calculated_from_plenum_temperature: Optional[str] = "No"
    return_air_fraction_function_of_plenum_temperature_coefficient_1: Optional[float] = 0.0
    return_air_fraction_function_of_plenum_temperature_coefficient_2: Optional[float] = 0.0