"""Wall object for the construction
"""
from typing import Optional
from enum import Enum
from pydantic import field_validator

from dclib.models.base import BaseModel


class OutsideBoundary(str, Enum):
    outdoors = "Outdoors"
    ground = "Ground"
    surface = "Surface"
    adiabatic = "Adiabatic"


class SurfaceType(str, Enum):
    wall = "Wall"
    roof = "Roof"
    floor = "Floor"
    ceiling = "Ceiling"


class Surface(BaseModel):
    uid: Optional[str] = ""
    type: SurfaceType = SurfaceType.wall
    construction_name: str
    zone_name: str
    outside_boundary_condition: Optional[OutsideBoundary] = OutsideBoundary.outdoors
    outside_boundary_condition_object: Optional[str] = ""
    sun_exposure: Optional[str] = "SunExposed" or "NoSun"
    wind_exposure: Optional[str] = "WindExposed" or "NoWind"
    view_factor_to_ground: Optional[float | str] = "autocalculate"  # (1-cos(SurfTilt))/2
    number_of_vertices: Optional[int] = 4

    @field_validator("type")
    def validate_type(cls, v) -> SurfaceType:
        if v.value != "Wall" and v.value != "Floor" and v.value != "Roof" and v.value != "Ceiling":
            raise ValueError("Surface type must be either Wall, Floor, Roof or Ceiling")
        return v
    
    @field_validator("outside_boundary_condition")
    def validate_outside_boundary_condition(cls, v) -> OutsideBoundary:
        if v.value != "Outdoors" and v.value != "Ground" and v.value != "Surface" and v.value != "Adiabatic":
            raise ValueError("Outside boundary condition must be either Outdoors, Ground, Surface or Adiabatic")
        return v
