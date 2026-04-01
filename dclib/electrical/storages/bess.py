from typing import Optional

from dclib.models.base import BaseModel


class SimpleBattery(BaseModel):
    """
    Please refer to the following link for more details:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-electric-load-center-generator.html#electricloadcenterstorage-battery

    :param uid: uid of the battery
    :param maximum_storage_capacity: Maximum storage capacity of the battery in Joules
    :param maximum_power_for_discharging: Maximum power for discharging in Watts
    :param maximum_power_for_charging: Maximum power for charging in Watts
    :param initial_state_of_charge: Initial state of charge of the battery
    :param availability_schedule_name: Availability schedule name
    :param zone_name: Zone name
    :param radiative_fraction_for_zone_heat_gains: Radiative fraction for zone heat gains
    :param nominal_energetic_efficiency_for_charging: Nominal energetic efficiency for charging
    :param nominal_discharging_energetic_efficiency: Nominal discharging energetic efficiency
    """
    uid: Optional[str] = ""
    maximum_storage_capacity: float
    maximum_power_for_discharging: float
    maximum_power_for_charging: float
    initial_state_of_charge: float
    availability_schedule_name: Optional[str] = "ALWAYS ON"
    zone_name: Optional[str] = ""
    radiative_fraction_for_zone_heat_gains: Optional[float] = 0.0
    nominal_energetic_efficiency_for_charging: Optional[float] = 0.7
    nominal_discharging_energetic_efficiency: Optional[float] = 0.7
