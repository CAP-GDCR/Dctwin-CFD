"""
Object models can be re-used to construct the same object in the building.
"""

from typing import Optional, List

from dclib.models.base import BaseModel


class MaterialModel(BaseModel):
    """
    Material model. See documentation for parameter details.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-surface-construction-elements.html#material
    """
    roughness: Optional[str] = ""
    thermal_absorptance: Optional[float] = ""
    solar_absorptance: Optional[float] = ""
    visible_absorptance: Optional[float] = ""
    thickness: Optional[float] = ""
    conductivity: Optional[float] = ""
    density: Optional[float] = ""
    specific_heat: Optional[float] = ""
    thermal_resistance: Optional[float] = ""


class ConstructionModel(BaseModel):
    """
    For a construction model, the material used for construction should be defined.
    """
    materials: List[str]
