from typing import OrderedDict

from dclib.models.composite import Models
from dclib.models.heat_gains import LightModel, ElectricEquipmentModel, PeopleModel

from ..electrical_device import Light, ElectricEquipment, People

class LightValidator:
    """ Validate the E+ light objects in a zone"""

    @classmethod
    def validate(
        cls,
        light: Light,
        models: OrderedDict[str, LightModel]
    ) -> None:
        light.uid = light.uid if light.uid else f"{light.zone} light"
        light.name = f"{light.zone} light"
        light_model = models.get(light.model, None)

        """Fill in light model parameters"""
        light.schedule_name = light_model.schedule_name
        light.design_level_calculation_method = light_model.design_level_calculation_method
        light.watts_per_zone_floor_area = light_model.watts_per_zone_floor_area
        light.watts_per_person = light_model.watts_per_person
        light.return_air_fraction = light_model.return_air_fraction
        light.fraction_radiant = light_model.fraction_radiant
        light.fraction_visible = light_model.fraction_visible
        light.fraction_replaceable = light_model.fraction_replaceable
        light.end_use_subcategory = light_model.end_use_subcategory
        light.return_air_fraction_calculated_from_plenum_temperature = light_model.return_air_fraction_calculated_from_plenum_temperature
        light.return_air_fraction_function_of_plenum_temperature_coefficient_1 = light_model.return_air_fraction_function_of_plenum_temperature_coefficient_1
        light.return_air_fraction_function_of_plenum_temperature_coefficient_2 = light_model.return_air_fraction_function_of_plenum_temperature_coefficient_2


class ElectricEquipmentValidator:
    """ Validate the E+ electrical equipment objects in a zone"""

    @classmethod
    def validate(
        cls,
        electric_equipment: ElectricEquipment,
        models: OrderedDict[str, ElectricEquipmentModel]
    ) -> None:
        electric_equipment.uid = electric_equipment.uid if electric_equipment.uid else f"{electric_equipment.zone} electric_equipment"
        electric_equipment.name = f"{electric_equipment.zone} electric_equipment"
        electric_equipment_model = models.get(electric_equipment.model, None)

        """Fill in electric_equipment model parameters"""
        electric_equipment.schedule_name = electric_equipment_model.schedule_name
        electric_equipment.design_level_calculation_method = electric_equipment_model.design_level_calculation_method
        electric_equipment.design_level = electric_equipment_model.design_level
        electric_equipment.watts_per_zone_floor_area = electric_equipment_model.watts_per_zone_floor_area
        electric_equipment.watts_per_person = electric_equipment_model.watts_per_person
        electric_equipment.fraction_latent = electric_equipment_model.fraction_latent
        electric_equipment.fraction_radiant = electric_equipment_model.fraction_radiant
        electric_equipment.fraction_lost = electric_equipment_model.fraction_lost
        electric_equipment.end_use_subcategory = electric_equipment_model.end_use_subcategory


class PeopleValidator:
    """ Validate the E+ people objects in a zone"""

    @classmethod
    def validate(
        cls,
        people: People,
        models: OrderedDict[str, PeopleModel]
    ) -> None:
        people.uid = people.uid if people.uid else f"{people.zone} people"
        people.name = f"{people.zone} people"
        people_model = models.get(people.model, None)

        """Fill in people model parameters"""
        people.number_of_people_schedule_name = people_model.number_of_people_schedule_name
        people.number_of_people_calculation_method = people_model.number_of_people_calculation_method
        people.people_per_zone_floor_area = people_model.people_per_zone_floor_area
        people.zone_floor_area_per_person = people_model.zone_floor_area_per_person
        people.fraction_radiant = people_model.fraction_radiant
        people.sensible_heat_fraction = people_model.sensible_heat_fraction
        people.activity_level_schedule_name = people_model.activity_level_schedule_name
        people.carbon_dioxide_generation_rate = people_model.carbon_dioxide_generation_rate
        people.enable_ashrae_55_comfort_warnings = people_model.enable_ashrae_55_comfort_warnings
        people.mean_radiant_temperature_calculation_type = people_model.mean_radiant_temperature_calculation_type
        people.surface_name_angle_factor_list_name = people_model.surface_name_angle_factor_list_name
        people.work_efficiency_schedule_name = people_model.work_efficiency_schedule_name
        people.clothing_insulation_calculation_method = people_model.clothing_insulation_calculation_method
        