from dclib.models.base import BaseModel
from typing import  Optional


class ElectricEquipmentModel(BaseModel):
    schedule_name: Optional[str] = ""
    design_level_calculation_method: Optional[str] = "EquipmentLevel"
    design_level: Optional[float] = 0.0
    watts_per_zone_floor_area: Optional[float] = 0.0
    watts_per_person: Optional[float] = 0.0
    fraction_latent: Optional[float] = 0.0
    fraction_radiant: Optional[float] = 0.0
    fraction_lost: Optional[float] = 0.0
    end_use_subcategory: Optional[str] = "General"
