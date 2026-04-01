from typing import OrderedDict, Optional
from dclib.models.base import BaseModel
from pydantic import Field
from .generators import PhotovoltaicGenerator, WindTurbineGenerator
from .auxiliaries import Inverter, Transformer
from .storages import SimpleBattery


class ElectricalLoadCenter(BaseModel):
    """ Electrical load center that contains generators, storage, and auxiliaries
    Please refer to the following link for more details:
    https://bigladdersoftware.com/epx/docs/9-5/input-output-reference/group-electric-load-center-generator.html#electricloadcenterdistribution

    :param generators: the generators in the electrical load center
    :param inverter: the inverter in the electrical load center
    :param generator_operation_scheme_name: the generator operation scheme name
    :param generator_demand_limit_scheme_purchased_electric_demand_limit: the generator demand limit scheme purchased
    electric demand limit
    :param generator_track_schedule_name_scheme_schedule_name: the generator track schedule name scheme schedule name
    :param generator_track_meter_scheme_meter_name: the generator track meter scheme meter name
    :param electrical_buss_type: the electrical buss type
    :param electrical_storage: the electrical storage
    :param transformer: the transformer
    """
    uid: Optional[str] = ""
    generators: OrderedDict[str, PhotovoltaicGenerator | WindTurbineGenerator]
    inverter: Inverter = Field(default_factory=Inverter)
    generator_operation_scheme_name: Optional[str] = "TrackElectrical"
    generator_demand_limit_scheme_purchased_electric_demand_limit: Optional[float | str] = ""
    generator_track_schedule_name_scheme_schedule_name: Optional[float | str] = ""
    generator_track_meter_scheme_meter_name: Optional[float | str] = ""
    electrical_buss_type: Optional[str] = "DirectCurrentWithInverter"
    electrical_storage: Optional[SimpleBattery] = None
    transformer: Optional[Transformer] = None
