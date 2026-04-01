from typing import OrderedDict
from loguru import logger
from pydantic import ValidationInfo

from dclib.room import Room
from dclib.electrical.generators import PhotovoltaicGenerator
from dclib.models.electrical import PhotovoltaicPerformanceModel


class PhotovoltaicGeneratorValidator:
    """
    PhotovoltaicGeneratorValidator validates PhotovoltaicGenerator configuration.
    """
    @classmethod
    def validate(
        cls,
        rooms: OrderedDict[str, Room],
        generators: OrderedDict[str, PhotovoltaicGenerator],
        values: ValidationInfo,
    ) -> None:
        """
        Validate photovoltaic generator. For a valid photovoltaic generator, the following conditions must be met:
        1. the surface name must be a valid surface name in the room
        2. the performance model name must be a valid performance model name in the building
        In the validation process, the performance model parameters will be filled in with the data stored in the
        electrical models.
        :param rooms: room objects in the building
        :param generators: electrical generators in the building
        :param values: ValidationInfo that contains the model info.
        :return: Nones
        """
        for generator_name, generator in generators.items():
            generator.uid = generator_name if generator.uid == "" else generator.uid
            valid = False
            for room_name, room in rooms.items():
                if generator.surface_name in room.constructions.surfaces.keys() and \
                        room.constructions.surfaces[generator.surface_name].type != "Floor":
                    valid = True
                    break
            if not valid:
                logger.critical(
                    f"PhotovoltaicGenerator: {generator_name} should attach to a valid surface of a zone."
                )
                exit(-1)

            if generator.performance_model_name not in values.data["models"].electrical_models.pv_performance.keys():
                logger.critical(f"PhotovoltaicGenerator: {generator_name} tries to use performance model with name:"
                                f" {generator.performance_model_name} which is not defined in room models.")

            # fill in performance model parameters
            performance_model: PhotovoltaicPerformanceModel = values.data["models"].electrical_models.pv_performance[
                generator.performance_model_name
            ]
            generator.performance_model.cell_type = performance_model.cell_type
            generator.performance_model.number_of_cells_in_series = performance_model.number_of_cells_in_series
            generator.performance_model.active_area = performance_model.active_area
            generator.performance_model.transmittance_absorptance_product \
                = performance_model.transmittance_absorptance_product
            generator.performance_model.semiconductor_band_gap = performance_model.semiconductor_band_gap
            generator.performance_model.shunt_resistance = performance_model.shunt_resistance
            generator.performance_model.short_circuit_current = performance_model.short_circuit_current
            generator.performance_model.open_circuit_voltage = performance_model.open_circuit_voltage
            generator.performance_model.reference_temperature = performance_model.reference_temperature
            generator.performance_model.reference_irradiance = performance_model.reference_irradiance
            generator.performance_model.module_current_at_maximum_power \
                = performance_model.module_current_at_maximum_power
            generator.performance_model.module_voltage_at_maximum_power \
                = performance_model.module_voltage_at_maximum_power
            generator.performance_model.temperature_coefficient_of_short_circuit_current \
                = performance_model.temperature_coefficient_of_short_circuit_current
            generator.performance_model.temperature_coefficient_of_open_circuit_voltage \
                = performance_model.temperature_coefficient_of_open_circuit_voltage
            generator.performance_model.nominal_operating_cell_temperature_test_ambient_temperature \
                = performance_model.nominal_operating_cell_temperature_test_insolation
            generator.performance_model.nominal_operating_cell_temperature_test_insolation \
                = performance_model.nominal_operating_cell_temperature_test_insolation
            generator.performance_model.nominal_operating_cell_temperature_test_cell_temperature =\
                performance_model.nominal_operating_cell_temperature_test_cell_temperature
            generator.performance_model.module_heat_loss_coefficient = performance_model.module_heat_loss_coefficient
            generator.performance_model.total_heat_capacity = performance_model.total_heat_capacity
