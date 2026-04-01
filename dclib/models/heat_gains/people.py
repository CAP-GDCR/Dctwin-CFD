from dclib.models.base import BaseModel
from typing import  Optional


class PeopleModel(BaseModel):
    number_of_people_schedule_name: Optional[str] = ""
    number_of_people_calculation_method: Optional[str] = "People"
    number_of_people: Optional[float] = 0.0
    people_per_zone_floor_area: Optional[float] = 0.0
    zone_floor_area_per_person: Optional[float] = 0.0
    fraction_radiant: Optional[float] = 0.3
    sensible_heat_fraction: Optional[str] = "Autocalculate"
    activity_level_schedule_name: Optional[str] = ""
    carbon_dioxide_generation_rate: Optional[float] = 3.82e-08
    enable_ashrae_55_comfort_warnings: Optional[str] = "No"
    mean_radiant_temperature_calculation_type: Optional[str] = "ZoneAveraged"
    surface_name_angle_factor_list_name: Optional[str] = ""
    work_efficiency_schedule_name: Optional[str] = ""
    clothing_insulation_calculation_method: Optional[str] = "ClothingInsulationSchedule"
