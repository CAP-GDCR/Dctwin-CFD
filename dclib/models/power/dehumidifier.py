from typing import Optional, List
from dclib.models.base import BaseModel


class DehumidifierPowerModel(BaseModel):
    """ Model of dehumidifier power properties
    Please refer to the following link for more information:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-zone-forced-air-units.html#zonehvacdehumidifierdx
    """
    rated_energy_factor: float | str = 3.412  # unit(L/kWh)
    off_cycle_parasitic_electric_load: float | str = 0.0  # unit(W)

    energy_factor_curve_model: Optional[str] = ""
    energy_factor_curve: Optional[List[float]] = [1, 0, 0, 0, 0, 0]

    part_load_fraction_correlation_curve_model: Optional[str] = ""
    part_load_fraction_correlation_curve: Optional[List[float]] = [1, 0, 0]
