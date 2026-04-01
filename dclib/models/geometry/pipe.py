from typing import Optional

from dclib.models.base import BaseModel


class PipeGeometryModel(BaseModel):
    """
    Pipe geometry model. For a pipe, the radius is the only parameter.
    """
    radius: Optional[float] = 0.01
    pipe_diameter: Optional[float] = 0.01
    pipe_length: Optional[float] = 0.01
    channel_type: Optional[str] = "tee"
    turning_radius: Optional[float] = 0.006
    height_difference: Optional[float] = 0.1
    roughness: Optional[float] = 1e-6
    fluid_name: Optional[str] = "water"
