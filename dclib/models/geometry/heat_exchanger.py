from typing import Optional

from dclib.models.base import BaseModel


class HeatExchangerGeometryModel(BaseModel):
    """ liquid-to-liquid heat exchanger geometry model"""
    tube_diameter: Optional[float] = 0.01
    tube_length: Optional[float] = 0.01
    tube_wall_thickness: Optional[float] = 0.002
    row_number: Optional[int] = 20
    row_pitch: Optional[float] = 0.03
    transverse_number: Optional[int] = 20
    transverse_pitch: Optional[float] = 0.03
    tube_roughness: Optional[float] = 1e-6
