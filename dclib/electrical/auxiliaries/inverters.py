from typing import Optional
from dclib.models.base import BaseModel


class Inverter(BaseModel):
    """
    Inverter is a device that converts direct current (DC) to alternating current (AC).
    Please refer to the following link for more information:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-electric-load-center-generator.html#electricloadcenterinvertersimple

    :param uid: uid of the inverter
    :param availability_schedule_name: Name of the availability schedule
    :param zone_name: Name of the zone
    :param radiant_fraction: Fraction of the heat loss from the inverter that is radiant
    :param inverter_efficiency: Efficiency of the inverter
    """
    uid: Optional[str] = ""
    availability_schedule_name: Optional[str] = "ALWAYS ON"
    zone_name: Optional[str] = ""
    radiant_fraction: Optional[float] = 0.6
    inverter_efficiency: Optional[float] = 0.9
