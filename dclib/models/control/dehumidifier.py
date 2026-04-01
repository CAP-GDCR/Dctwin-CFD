from typing import Optional
from dclib.models.base import BaseModel


class DehumidifierControlModel(BaseModel):
    """
    Dehumidifier control model.
    Please refer to the documentation for more information.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-zone-forced-air-units.html#zonehvacdehumidifierdx
    """
    availability_schedule_name: Optional[str] = ""
    supply_air_relative_humidity_setpoint: Optional[float] = 50.0 # unit(%)
