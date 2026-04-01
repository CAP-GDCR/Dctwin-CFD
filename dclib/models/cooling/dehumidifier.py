from typing import Optional
from dclib.models.base import BaseModel


class DehumidifierCoolingModel(BaseModel):
    """ Model of dehumidifier cooling properties
    Please refer to the following link for more information:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-zone-forced-air-units.html#zonehvacdehumidifierdx
    """
    rated_water_removal: Optional[float | str] = 50.16  # unit(L/day)
    rated_air_flow_rate: Optional[float | str] = 0.12036  # unit(m3/s)
    minimum_dry_bulb_temperature: Optional[float | str] = 10.0  # unit(C)
    maximum_dry_bulb_temperature: Optional[float | str] = 32.0  # unit(C)

    water_removal_curve_model: Optional[str] = ""
    water_removal_curve: Optional[list[float]] = [1, 0, 0, 0, 0, 0]
