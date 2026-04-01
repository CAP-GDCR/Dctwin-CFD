from typing import OrderedDict, Optional
from loguru import logger

from dclib.cooling.common.pump import Pump
from dclib.models.cooling.pump import PumpCoolingModel
from dclib.models.power.pump import PumpPowerModel
from dclib.models.control.pump import PumpControlModel
from dclib.models.curves.pump import PumpCurveModels


class PumpValidator:
    """A class to validate pumps of a building/plant"""

    @classmethod
    def validate(
        cls,
        pumps: OrderedDict[str, Pump],
        cooling_models: OrderedDict[str, PumpCoolingModel],
        power_models: OrderedDict[str, PumpPowerModel],
        control_models: Optional[OrderedDict[str, PumpControlModel]],
        curve_models: PumpCurveModels,
    ) -> None:
        """
        Validate the pumps of the loop. In the validation, we fill in the parameters of the pumps by using the
        parameters defined in the cooling, power and control models.
        """
        for pump_name, pump in pumps.items():
            pump.uid = pump_name if pump.uid == "" else pump.uid
            pump.upstream_component = pump.upstream_component

            cooling_model_name = pump.cooling.model
            power_model_name = pump.power.model
            control_model_name = pump.control.model if pump.control else None

            # Validate Cooling Model
            if cooling_model_name:
                cooling_model = cooling_models.get(cooling_model_name)
                if cooling_model is None:
                    raise ValueError(
                        f"Cooling model '{cooling_model_name}' not found for Pump '{pump.uid}'."
                    )
                cls._validate_cooling_model(pump, cooling_model)

            # Validate Power Model
            if power_model_name:
                power_model = power_models.get(power_model_name)
                if power_model is None:
                    raise ValueError(
                        f"Power model '{power_model_name}' not found for Pump '{pump.uid}'."
                    )
                cls._validate_power_model(pump, power_model, curve_models)

            # Validate Control Model
            if control_model_name and control_models:
                control_model = control_models.get(control_model_name)
                if control_model is None:
                    raise ValueError(
                        f"Control model '{control_model_name}' not found for Pump '{pump.uid}'."
                    )
                cls._validate_control_model(pump, control_model)

    @classmethod
    def _validate_cooling_model(cls, pump: Pump, cooling_model: PumpCoolingModel):
        # Validate design_maximum_flow_rate
        cls._validate_float_or_autosize(
            cooling_model.design_maximum_flow_rate,
            0,
            float("inf"),
            "design_maximum_flow_rate",
        )
        pump.cooling.design_maximum_flow_rate = cooling_model.design_maximum_flow_rate

        # Validate design_pump_head
        cls._validate_float_or_autosize(
            cooling_model.design_pump_head,
            0,
            float("inf"),
            "design_pump_head",
        )
        pump.cooling.design_pump_head = cooling_model.design_pump_head

        # Validate impeller_diameter
        cls._validate_float(
            cooling_model.impeller_diameter,
            0,
            float("inf"),
            "impeller_diameter",
        )
        pump.cooling.impeller_diameter = cooling_model.impeller_diameter

        # Validate skin_loss_radiative_fraction
        cls._validate_required_field(
            cooling_model.skin_loss_radiative_fraction,
            0,
            1,
            "skin_loss_radiative_fraction",
        )
        pump.cooling.skin_loss_radiative_fraction = (
            cooling_model.skin_loss_radiative_fraction
        )

        pump.cooling.design_minimum_flow_rate_fraction = (
            cooling_model.design_minimum_flow_rate_fraction
        )

    @classmethod
    def _validate_power_model(
        cls,
        pump: Pump,
        power_model: PumpPowerModel,
        pump_curve_models:PumpCurveModels = None
    ):
        # Validate design_power_consumption
        cls._validate_float_or_autosize(
            power_model.design_power_consumption,
            0,
            float("inf"),
            "design_power_consumption",
        )
        pump.power.design_power_consumption = power_model.design_power_consumption

        # Validate motor_efficiency
        cls._validate_required_field(
            power_model.motor_efficiency, 0, 1, "motor_efficiency"
        )
        pump.power.motor_efficiency = power_model.motor_efficiency

        pump.power.pump_curve_name = power_model.pump_curve_name
        pump.power.design_power_sizing_method = power_model.design_power_sizing_method
        pump.power.design_electric_power_per_unit_flow_rate = (
            power_model.design_electric_power_per_unit_flow_rate
        )
        pump.power.design_shaft_power_per_unit_flow_rate_per_unit_head = (
            power_model.design_shaft_power_per_unit_flow_rate_per_unit_head
        )
        pump.power.fraction_of_motor_inefficiencies_to_fluid_stream = (
            power_model.fraction_of_motor_inefficiencies_to_fluid_stream
        )

        """Fill in curve parameters"""
        if pump_curve_models and pump_curve_models.part_load_performance_curve_models:
            curve_model = pump_curve_models.part_load_performance_curve_models.get(
                power_model.part_load_performance_curve_model, None
            )
            pump.power.coefficient_1_of_the_part_load_performance_curve = curve_model.coefficients[0] \
                if curve_model is not None else power_model.coefficient_1_of_the_part_load_performance_curve
            pump.power.coefficient_2_of_the_part_load_performance_curve = curve_model.coefficients[1] \
                if curve_model is not None else power_model.coefficient_2_of_the_part_load_performance_curve
            pump.power.coefficient_3_of_the_part_load_performance_curve = curve_model.coefficients[2] \
                if curve_model is not None else power_model.coefficient_3_of_the_part_load_performance_curve
            pump.power.coefficient_4_of_the_part_load_performance_curve = curve_model.coefficients[3] \
                if curve_model is not None else power_model.coefficient_4_of_the_part_load_performance_curve

    @classmethod
    def _validate_control_model(cls, pump: Pump, control_model: PumpControlModel):
        pump.control.pump_control_type = control_model.pump_control_type
        pump.control.pump_flow_rate_schedule_name = (
            control_model.pump_flow_rate_schedule_name
        )
        pump.control.vfd_control_type = control_model.vfd_control_type
        pump.control.pump_rpm_schedule_name = control_model.pump_rpm_schedule_name
        pump.control.minimum_pressure_schedule = control_model.minimum_pressure_schedule
        pump.control.maximum_pressure_schedule = control_model.maximum_pressure_schedule
        pump.control.minimum_rpm_schedule = control_model.minimum_rpm_schedule
        pump.control.maximum_rpm_schedule = control_model.maximum_rpm_schedule

    @classmethod
    def _validate_float_or_autosize(cls, value, min_value, max_value, field_name: str):
        camel_case_field_name = cls._to_camel_case(field_name)
        if value is None:
            raise ValueError(f"{camel_case_field_name} is required.")
        if isinstance(value, str):
            if value != "autosize":
                raise ValueError(
                    f"{camel_case_field_name} must be a numeric value or 'autosize'."
                )
        elif isinstance(value, (float, int)):
            if not (min_value <= value <= max_value):
                raise ValueError(
                    f"{camel_case_field_name} must be between {min_value} and {max_value}."
                )
        else:
            raise ValueError(
                f"{camel_case_field_name} has an invalid type. Expected float, int, or 'autosize'."
            )

    @classmethod
    def _validate_required_field(cls, value, min_value, max_value, field_name: str):
        camel_case_field_name = cls._to_camel_case(field_name)
        if value is None:
            raise ValueError(f"{camel_case_field_name} is required.")
        cls._validate_float(value, min_value, max_value, camel_case_field_name)

    @classmethod
    def _validate_float(cls, value, min_value, max_value, field_name: str):
        camel_case_field_name = cls._to_camel_case(field_name)
        if value != "":
            if not isinstance(value, (float, int)):
                raise ValueError(f"{camel_case_field_name} must be a numeric value.")
            if not (min_value <= value <= max_value):
                raise ValueError(
                    f"{camel_case_field_name} must be between {min_value} and {max_value}."
                )

    @staticmethod
    def _to_camel_case(snake_str: str) -> str:
        components = snake_str.split("_")
        return components[0] + "".join(x.title() for x in components[1:])
