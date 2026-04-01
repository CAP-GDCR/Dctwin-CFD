from typing import OrderedDict, Optional, Literal
import re
from loguru import logger

from dclib.cooling.room.facilities.acu import ACU
from dclib.models.curves.acu import ACUCurveModels
from dclib.models.geometry import ACUGeometryModel
from dclib.models.cooling import ACUCoolingModel
from dclib.models.power import ACUPowerModel
from dclib.models.control import ACUControlModel
from dclib.data import ACUInputs

from dclib.validators.utils import validate_model_block


class ACUValidator:
    """A class to validate ACUs of a building"""

    @classmethod
    def validate(
        cls,
        acus: OrderedDict[str, ACU],
        geometry_models: OrderedDict[str, ACUGeometryModel] = None,
        cooling_models: OrderedDict[str, ACUCoolingModel] = None,
        power_models: OrderedDict[str, ACUPowerModel] = None,
        control_models: OrderedDict[str, ACUControlModel] = None,
        curve_models: ACUCurveModels = None,
        inputs: OrderedDict[str, ACUInputs] = None,
        context: Literal["room", "demand_branches"] = "room",
    ) -> None:
        changes = []  # Used to record keys that need to be changed

        for acu_id, acu in list(acus.items()):
            # Set initial UID
            acu.uid = acu_id if acu.uid == "" else acu.uid

            # Replace illegal characters using regular expression
            valid_uid = re.sub(r"[^a-zA-Z0-9_]", "_", acu.uid)

            acu.valid_id = valid_uid

            acu.cooling.oa.uid = f"{acu_id} outdoor air system"

            if acu_id != acu.uid:
                changes.append((acu_id, acu.uid))

            is_model_required = context == "room"

            # --- Geometry Model ---
            if validate_model_block(
                component=acu,
                model_type="geometry",
                model_definitions=geometry_models,
                uid=acu.uid,
                required=is_model_required,
            ):
                cls._validate_geometry_models(acu, geometry_models)

            # --- Cooling Model ---
            if validate_model_block(
                component=acu,
                model_type="cooling",
                model_definitions=cooling_models,
                uid=acu.uid,
                required=is_model_required,
            ):
                cls._validate_cooling_models(acu, cooling_models)

            # --- Control Model ---
            if validate_model_block(
                component=acu,
                model_type="controller",
                model_definitions=control_models,
                uid=acu.uid,
                required=False,
            ):
                cls._validate_controller_models(acu, control_models)

            # --- Power Model ---
            if validate_model_block(
                component=acu,
                model_type="power",
                model_definitions=power_models,
                uid=acu.uid,
                required=False,
            ):
                cls._validate_power_models(acu, power_models, curve_models)

            # --- Inputs ---
            if is_model_required:
                if inputs is not None:
                    cls._validate_inputs(acu, inputs)
                else:
                    logger.warning(f"Inputs not defined for acu '{acu.uid}'.")

        for old_id, new_id in changes:
            acus[new_id] = acus.pop(old_id)

    @classmethod
    def _validate_geometry_models(
        cls,
        acu: ACU,
        models: OrderedDict[str, ACUGeometryModel],
    ) -> None:
        model_name = acu.geometry.model

        model = models.get(model_name)

        acu.geometry.size = model.size
        acu.geometry.supply_face = model.supply_face
        acu.geometry.return_face = model.return_face

        if model.coil_geometry is not None:
            acu.geometry.coil_geometry.tube_diameter = model.coil_geometry.tube_diameter
            acu.geometry.coil_geometry.tube_length = model.coil_geometry.tube_length
            acu.geometry.coil_geometry.tube_wall_thickness = model.coil_geometry.tube_wall_thickness
            acu.geometry.coil_geometry.transverse_number = model.coil_geometry.transverse_number
            acu.geometry.coil_geometry.transverse_pitch = model.coil_geometry.transverse_pitch
            acu.geometry.coil_geometry.row_number = model.coil_geometry.row_number
            acu.geometry.coil_geometry.row_pitch = model.coil_geometry.row_pitch
            acu.geometry.coil_geometry.tube_roughness = model.coil_geometry.tube_roughness

    @classmethod
    def _validate_cooling_models(cls, acu: ACU, acu_models: OrderedDict[str, ACUCoolingModel]) -> None:
        model_name = acu.cooling.model

        model = acu_models.get(model_name)

        cls._validate_float_or_autosize(model.cooling_capacity, 0, float("inf"), "cooling_capacity")
        cls._validate_float_or_autosize(model.pressure_rise, 0, 10000, "pressure_rise")
        cls._validate_float_or_autosize(model.maximum_flow_rate, 0, float("inf"), "maximum_flow_rate")
        cls._validate_float_or_autosize(model.design_water_flow_rate, 0, float("inf"), "design_water_flow_rate")
        cls._validate_float_or_autosize(model.design_air_flow_rate, 0, float("inf"), "design_air_flow_rate")
        cls._validate_float_or_autosize(
            model.design_inlet_water_temperature,
            0,
            model.design_outlet_air_temperature,
            "design_inlet_water_temperature",
        )
        cls._validate_float_or_autosize(
            model.design_inlet_air_temperature,
            model.design_outlet_air_temperature,
            100,
            "design_inlet_air_temperature",
        )
        cls._validate_float_or_autosize(
            model.design_outlet_air_temperature,
            model.design_inlet_water_temperature,
            model.design_inlet_air_temperature,
            "design_outlet_air_temperature",
        )
        cls._validate_float_or_autosize(
            model.design_inlet_air_humidity_ratio,
            0,
            1,
            "design_inlet_air_humidity_ratio",
        )
        cls._validate_float_or_autosize(
            model.design_outlet_air_humidity_ratio,
            0,
            1,
            "design_outlet_air_humidity_ratio",
        )

        acu.cooling.cooling_type = model.cooling_type
        acu.cooling.cooling_capacity = model.cooling_capacity
        acu.cooling.pressure_rise = model.pressure_rise
        acu.cooling.maximum_flow_rate = model.maximum_flow_rate
        acu.cooling.design_water_flow_rate = model.design_water_flow_rate
        acu.cooling.design_air_flow_rate = model.design_air_flow_rate
        acu.cooling.design_inlet_water_temperature = model.design_inlet_water_temperature
        acu.cooling.design_inlet_air_temperature = model.design_inlet_air_temperature
        acu.cooling.design_outlet_air_temperature = model.design_outlet_air_temperature
        acu.cooling.design_inlet_air_humidity_ratio = model.design_inlet_air_humidity_ratio
        acu.cooling.design_outlet_air_humidity_ratio = model.design_outlet_air_humidity_ratio
        acu.cooling.design_water_temperature_difference = model.design_water_temperature_difference
        acu.cooling.condensate_collection_water_storage_tank_name = model.condensate_collection_water_storage_tank_name
        acu.cooling.type_of_analysis = model.type_of_analysis
        acu.cooling.heat_exchanger_configuration = model.heat_exchanger_configuration
        acu.cooling.gross_rated_total_cooling_capacity = model.gross_rated_total_cooling_capacity
        acu.cooling.gross_rated_sensible_heat_ratio = model.gross_rated_sensible_heat_ratio
        acu.cooling.gross_rated_cooling_cop = model.gross_rated_cooling_cop
        acu.cooling.rated_air_flow_rate = model.rated_air_flow_rate
        acu.cooling.minimum_outdoor_dryBulb_temperature_for_compressor_operation = (
            model.minimum_outdoor_dryBulb_temperature_for_compressor_operation
        )
        acu.cooling.nominal_time_for_condensate_removal_to_begin = model.nominal_time_for_condensate_removal_to_begin
        acu.cooling.ratio_of_initial_moisture_evaporation_rate_and_steady_state_evaporation_rate = (
            model.ratio_of_initial_moisture_evaporation_rate_and_steady_state_evaporation_rate
        )
        acu.cooling.maximum_cycling_rate = model.maximum_cycling_rate
        acu.cooling.latent_capacity_time_constant = model.latent_capacity_time_constant
        acu.cooling.condenser_type = model.condenser_type
        acu.cooling.evaporative_condenser_effectiveness = model.evaporative_condenser_effectiveness
        acu.cooling.evaporative_condenser_air_flow_rate = model.evaporative_condenser_air_flow_rate
        acu.cooling.crankcase_heater_capacity = model.crankcase_heater_capacity
        acu.cooling.maximum_outdoor_dryBulb_temperature_for_crankcase_heater_operation = (
            model.maximum_outdoor_dryBulb_temperature_for_crankcase_heater_operation
        )
        acu.cooling.basin_heater_capacity = model.basin_heater_capacity
        acu.cooling.basin_heater_setpoint_temperature = model.basin_heater_setpoint_temperature
        acu.cooling.basin_heater_operating_schedule_name = model.basin_heater_operating_schedule_name

    @classmethod
    def _validate_float_or_autosize(
        cls,
        value: Optional[float | str],
        min_val: float,
        max_val: float,
        field_name: str,
    ) -> None:
        """
        Validate that the value is either a float within a specified range or the string 'autosize'.
        :param value: The value to validate.
        :param min_val: The minimum valid value.
        :param max_val: The maximum valid value.
        :param field_name: The name of the field being validated.
        :return: None. Raises ValueError if validation fails.
        """
        camel_case_field_name = cls._to_camel_case(field_name)
        if isinstance(value, str):
            if value != "autosize":
                raise ValueError(f"{camel_case_field_name} must be a float or 'autosize'.")
        elif isinstance(value, (float, int)):
            if not (min_val <= value <= max_val):
                raise ValueError(f"{camel_case_field_name} must be between {min_val} and {max_val}.")
        else:
            raise ValueError(f"{camel_case_field_name} has an invalid type. Expected float, int, or 'autosize'.")

    @staticmethod
    def _to_camel_case(snake_str: str) -> str:
        """
        Convert a snake_case string to camelCase.
        :param snake_str: The snake_case string.
        :return: The camelCase string.
        """
        components = snake_str.split("_")
        return components[0] + "".join(x.title() for x in components[1:])

    @classmethod
    def _validate_power_models(
        cls,
        acu: ACU,
        power_models: OrderedDict[str, ACUPowerModel],
        curve_models: ACUCurveModels,
    ) -> None:
        model_name = acu.power.model
        model = power_models.get(model_name)
        acu.power.fan_total_efficiency = model.fan_total_efficiency
        acu.power.fan_power_minimum_flow_rate_input_method = model.fan_power_minimum_flow_rate_input_method
        acu.power.fan_power_minimum_flow_fraction = model.fan_power_minimum_flow_fraction
        acu.power.fan_power_minimum_air_flow_rate = model.fan_power_minimum_air_flow_rate
        acu.power.motor_efficiency = model.motor_efficiency
        acu.power.motor_in_airstream_fraction = model.motor_in_airstream_fraction
        acu.power.rated_evaporator_fan_power_per_volume_flow_rate = (
            model.rated_evaporator_fan_power_per_volume_flow_rate
        )
        acu.power.evaporative_condenser_pump_rated_power_consumption = (
            model.evaporative_condenser_pump_rated_power_consumption
        )

        """Fill in curve parameters"""
        curve_model_name = model.fan_power_curve_model
        if curve_models is not None:
            curve_model = curve_models.fan_power_curve_models.get(curve_model_name, None)
        else:
            curve_model = None

        acu.power.fan_power_coefficient_1 = (
            curve_model.coefficients[0] if curve_model is not None else model.fan_power_coefficient_1
        )
        acu.power.fan_power_coefficient_2 = (
            curve_model.coefficients[1] if curve_model is not None else model.fan_power_coefficient_2
        )
        acu.power.fan_power_coefficient_3 = (
            curve_model.coefficients[2] if curve_model is not None else model.fan_power_coefficient_3
        )
        acu.power.fan_power_coefficient_4 = (
            curve_model.coefficients[3] if curve_model is not None else model.fan_power_coefficient_4
        )
        acu.power.fan_power_coefficient_5 = (
            curve_model.coefficients[4]
            if curve_model is not None and len(curve_model.coefficients) > 4
            else model.fan_power_coefficient_5
        )

    @classmethod
    def _validate_controller_models(cls, acu: ACU, acu_models: OrderedDict[str, ACUControlModel]) -> None:
        model_name = acu.controller.model
        model = acu_models.get(model_name)

        acu.controller.control_variable = model.control_variable
        acu.controller.supply_air_temperature_setpoint = model.supply_air_temperature_setpoint
        acu.controller.supply_air_humidity_ratio_setpoint = model.supply_air_humidity_ratio_setpoint
        acu.controller.supply_air_maximum_humidity_ratio_setpoint = model.supply_air_maximum_humidity_ratio_setpoint
        acu.controller.supply_air_minimum_humidity_ratio_setpoint = model.supply_air_minimum_humidity_ratio_setpoint

    @classmethod
    def _validate_inputs(cls, acu: ACU, inputs: OrderedDict[str, ACUInputs]) -> None:
        input = inputs.get(acu.uid)
        if isinstance(acu, ACU) and input is not None:
            acu.cooling.operating.supply_air_temperature = input.supply_air_temperature
            acu.cooling.operating.supply_air_volume_flow_rate = input.supply_air_volume_flow_rate
        else:
            raise ValueError(f"Inputs model not found for ACU '{acu.uid}'.")
