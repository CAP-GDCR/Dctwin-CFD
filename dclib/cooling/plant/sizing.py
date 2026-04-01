from typing import Optional

from dclib.models.base import BaseModel


class SizingPlant(BaseModel):
    """
    Object that store the chiller plant sizing information. Please refer to the EnergyPlus 9.5 Input Output Reference for
    more information.
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-design-objects.html#sizingplant

    :param loop_type: The type of the loop. Default to "Water".
    :param design_loop_exit_temperature: The design loop exit temperature. Default to 6.67.
    :param loop_design_temperature_difference: The loop design temperature difference. Default to 5.
    :param sizing_option: The sizing option. Default to "NonCoincident".
    :param zone_timestep_in_averaging_window: The zone timestep in averaging window. Default to 1.
    :param coincident_sizing_factor_mode: The coincident sizing factor mode. Default to "None".
    """
    loop_type: str = "Water"
    design_loop_exit_temperature: float = 6.67
    loop_design_temperature_difference: float = 5
    sizing_option: Optional[str] = "NonCoincident"
    zone_timestep_in_averaging_window: Optional[int] = 1
    coincident_sizing_factor_mode: Optional[str] = "None"
