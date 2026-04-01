from typing import OrderedDict
from loguru import logger
from pydantic import ValidationInfo

from dclib.electrical import ElectricalLoadCenter
from dclib.room import Room

from .photovoltaic import PhotovoltaicGeneratorValidator


class ElectricalLoadCenterValidator:

    valid_generator_operation_scheme_name = [
        "Baseload", "TrackElectrical", "DemandLimit", "TrackSchedule", "TrackMeter", "FollowThermal",
        "FollowThermalLimitElectrical"
    ]

    valid_electrical_buss_type = [
        "AlternatingCurrent",
        "AlternatingCurrentWithStorage",
        "DirectCurrentWithInverter",
        "DirectCurrentWithInverterDCStorage",
        "DirectCurrentWithInverterACStorage"
    ]

    electric_buss_type_with_storage = [
        "AlternatingCurrentWithStorage",
        "DirectCurrentWithInverterDCStorage",
        "DirectCurrentWithInverterACStorage"
    ]

    @classmethod
    def validate(
        cls,
        rooms: OrderedDict[str, Room],
        electric_load_centers: OrderedDict[str, ElectricalLoadCenter],
        values: ValidationInfo,
    ) -> None:
        """
        Validate electrical load center. For a valid electrical load center, the following conditions must be met:
        1. generator operation scheme name must be one of the valid values
        2. electrical buss type must be one of the valid values
        3. if electrical buss type is one of the types with storage, electrical storage object must be defined
        4. all generators must be valid

        :param rooms: the room objects in the building
        :param electric_load_centers: the electrical load center objects in the building. A building can have multiple
        electrical load centers. For example, one wind farm and one solar farm.
        :param values: ValidationInfo that contains the model info.
        """
        for electric_load_center_name, electric_load_center in electric_load_centers.items():
            electric_load_center.uid = electric_load_center_name if electric_load_center.uid == "" \
                else electric_load_center.uid
            if electric_load_center.generator_operation_scheme_name not in cls.valid_generator_operation_scheme_name:
                logger.critical(
                    f"Invalid generator operation scheme name: {electric_load_center.generator_operation_scheme_name} "
                    f"for ElectricalLoadCenter: {electric_load_center_name}.\n Valid values are: "
                    f"{cls.valid_generator_operation_scheme_name}"
                )
            if electric_load_center.electrical_buss_type not in cls.valid_electrical_buss_type:
                logger.critical(
                    f"Invalid buss type: {electric_load_center.electrical_buss_type} for ElectricalLoadCenter: "
                    f"{electric_load_center_name}. Valid values are: {cls.valid_electrical_buss_type}"
                )
            if electric_load_center.electrical_buss_type in cls.electric_buss_type_with_storage and \
                    electric_load_center.electrical_storage_object is None:
                logger.critical(
                    f"ElectricalLoadCenter: {electric_load_center_name} has electrical buss type:"
                    f" {electric_load_center.electrical_buss_type} while electrical storage object is not defined."
                )
            # validate pv generators
            PhotovoltaicGeneratorValidator.validate(
                rooms=rooms,
                generators=electric_load_center.generators,
                values=values
            )
