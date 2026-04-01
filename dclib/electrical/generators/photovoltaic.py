from typing import Optional
from pydantic import Field
from dclib.models.base import BaseModel
from dclib.models.electrical.pv_performance import PhotovoltaicPerformanceModel


class PhotovoltaicGenerator(BaseModel):
    """
    PhotovoltaicGenerator is a generator that converts solar irradiance to electricity.
    Please refer to the following link for more details:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-electric-load-center-generator.html#photovoltaic-generators

    :param surface_name: Name of the surface that the photovoltaic generator is attached to.
    :param number_of_series_strings_in_parallel: Number of series strings in parallel.
    :param number_of_modules_in_series: Number of modules in series.
    :param rated_electric_power_output: Rated electric power output.
    :param performance_model_name: Name of the performance model.
    :param performance_model: Performance model.
    :param heat_transfer_integration_mode: Heat transfer integration mode. Default to "IntegratedExteriorVentedCavity".
    """
    uid: Optional[str] = ""
    surface_name: str
    number_of_series_strings_in_parallel: int
    number_of_modules_in_series: int
    rated_electric_power_output: float
    performance_model_name: Optional[str] = ""
    performance_model: PhotovoltaicPerformanceModel = Field(default_factory=PhotovoltaicPerformanceModel)
    heat_transfer_integration_mode: Optional[str] = "IntegratedExteriorVentedCavity"
