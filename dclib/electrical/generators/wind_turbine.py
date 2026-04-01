from typing import Optional

from dclib.models.base import BaseModel


class WindTurbineGenerator(BaseModel):
    """
    TODO: WindTurbineGenerator is a generator that converts wind speed to electricity.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-electric-load-center-generator.html#generatorwindturbine
    """
    uid: Optional[str] = ""
