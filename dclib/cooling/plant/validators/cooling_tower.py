from typing import OrderedDict

from dclib.cooling.plant.facilities.cooling_tower import CoolingTower
from dclib.models.cooling.cooling_tower import CoolingTowerCoolingModel
from dclib.models.power.cooling_tower import CoolingTowerPowerModel
from dclib.models.curves.cooling_tower import CoolingTowerCurveModels


class CoolingTowerValidator:
    """A class to validate cooling towers of a building/plant"""

    @classmethod
    def validate(
        cls,
        cooling_towers: OrderedDict[str, CoolingTower],
        cooling_models: OrderedDict[str, CoolingTowerCoolingModel],
        power_models: OrderedDict[str, CoolingTowerPowerModel],
        curve_models: CoolingTowerCurveModels,
    ) -> None:
        """
        Validate the cooling towers of the loop. In the validation, we fill in the parameters of the cooling towers by
        using the parameters defined in the cooling and power models.
        """
        for cooling_tower_name, cooling_tower in cooling_towers.items():
            cooling_tower.uid = cooling_tower_name if cooling_tower.uid == "" else cooling_tower.uid
            cooling_tower.upstream_component = cooling_tower.upstream_component

            cooling_model_name = cooling_tower.cooling.model
            power_model_name = cooling_tower.power.model

            # Validate Cooling Model
            if cooling_model_name:
                cooling_model = cooling_models.get(cooling_model_name)
                if cooling_model is None:
                    raise ValueError(
                        f"Cooling model '{cooling_model_name}' not found for Cooling Tower '{cooling_tower.uid}'."
                    )
                cls._validate_cooling_model(cooling_tower, cooling_model)

            # Validate Power Model
            if power_model_name:
                power_model = power_models.get(power_model_name)
                if power_model is None:
                    raise ValueError(
                        f"Power model '{power_model_name}' not found for Cooling Tower '{cooling_tower.uid}'."
                    )
                cls._validate_power_model(cooling_tower, power_model, curve_models)

    @classmethod
    def _validate_cooling_model(
        cls, cooling_tower: CoolingTower, cooling_model: CoolingTowerCoolingModel
    ) -> None:
        # Validate model_coefficient_name
        cls._validate_required_field(
            cooling_model.model_coefficient_name, "modelCoefficientName"
        )
        cooling_tower.cooling.model_coefficient_name = (
            cooling_model.model_coefficient_name
        )

        # Validate design_inlet_air_wet_bulb_temperature
        cls._validate_float_or_autosize(
            cooling_model.design_inlet_air_wet_bulb_temperature,
            20,
            40,
            "design_inlet_air_wet_bulb_temperature",
        )
        cooling_tower.cooling.design_inlet_air_wet_bulb_temperature = (
            cooling_model.design_inlet_air_wet_bulb_temperature
        )

        # Validate design_water_flow_rate
        cls._validate_float_or_autosize(
            cooling_model.design_water_flow_rate,
            0,
            float("inf"),
            "design_water_flow_rate",
        )
        cooling_tower.cooling.design_water_flow_rate = (
            cooling_model.design_water_flow_rate
        )

        # Validate design_air_flow_rate
        cls._validate_float_or_autosize(
            cooling_model.design_air_flow_rate,
            0,
            float("inf"),
            "design_air_flow_rate",
        )
        cooling_tower.cooling.design_air_flow_rate = cooling_model.design_air_flow_rate

        # Validate number_of_cells
        cls._validate_float_or_autosize(
            cooling_model.number_of_cells, 1, 10, "number_of_cells"
        )
        cooling_tower.cooling.number_of_cells = cooling_model.number_of_cells

        # Validate minimum_air_flow_rate_ratio
        cls._validate_float(
            cooling_model.minimum_air_flow_rate_ratio,
            0.2,
            0.5,
            "minimum_air_flow_rate_ratio",
        )
        cooling_tower.cooling.minimum_air_flow_rate_ratio = (
            cooling_model.minimum_air_flow_rate_ratio
        )

        cooling_tower.cooling.model_type = cooling_model.model_type
        cooling_tower.cooling.design_range_temperature = (
            cooling_model.design_range_temperature
        )
        cooling_tower.cooling.fraction_of_tower_capacity_in_free_convection_regime = (
            cooling_model.fraction_of_tower_capacity_in_free_convection_regime
        )
        cooling_tower.cooling.basin_heater_capacity = (
            cooling_model.basin_heater_capacity
        )
        cooling_tower.cooling.basin_heater_setpoint_temperature = (
            cooling_model.basin_heater_setpoint_temperature
        )
        cooling_tower.cooling.basin_heater_operating_schedule_name = (
            cooling_model.basin_heater_operating_schedule_name
        )
        cooling_tower.cooling.evaporation_loss_mode = (
            cooling_model.evaporation_loss_mode
        )
        cooling_tower.cooling.evaporation_loss_factor = (
            cooling_model.evaporation_loss_factor
        )
        cooling_tower.cooling.drift_loss_percent = cooling_model.drift_loss_percent
        cooling_tower.cooling.blowdown_calculation_mode = (
            cooling_model.blowdown_calculation_mode
        )
        cooling_tower.cooling.blowdown_concentration_ratio = (
            cooling_model.blowdown_concentration_ratio
        )
        cooling_tower.cooling.blowdown_makeup_water_usage_schedule_name = (
            cooling_model.blowdown_makeup_water_usage_schedule_name
        )
        cooling_tower.cooling.supply_water_storage_tank_name = (
            cooling_model.supply_water_storage_tank_name
        )
        cooling_tower.cooling.outdoor_air_inlet_node_name = (
            cooling_model.outdoor_air_inlet_node_name
        )
        cooling_tower.cooling.cell_control = cooling_model.cell_control
        cooling_tower.cooling.cell_minimum_water_flow_rate_fraction = (
            cooling_model.cell_minimum_water_flow_rate_fraction
        )
        cooling_tower.cooling.cell_maximum_water_flow_rate_fraction = (
            cooling_model.cell_maximum_water_flow_rate_fraction
        )
        cooling_tower.cooling.sizing_factor = cooling_model.sizing_factor
        cooling_tower.cooling.end_use_subcategory = cooling_model.end_use_subcategory
        cooling_tower.cooling.design_approach_temperature = (
            cooling_model.design_approach_temperature
        )

    @classmethod
    def _validate_power_model(
        cls,
        cooling_tower: CoolingTower,
        power_model: CoolingTowerPowerModel,
        curve_models: CoolingTowerCurveModels
    ) -> None:
        # Validate design_fan_power (can be autosize or a float within range)
        cls._validate_float_or_autosize(
            power_model.design_fan_power, 0, float("inf"), "design_fan_power"
        )
        cooling_tower.power.design_fan_power = power_model.design_fan_power

        """Fill in curve parameters"""
        if curve_models and curve_models.fan_power_ratio_function_of_air_flow_rate_ratio_curve_models:
            curve_model = curve_models.fan_power_ratio_function_of_air_flow_rate_ratio_curve_models.get(
                power_model.fan_power_ratio_function_of_air_flow_rate_ratio_curve_model, None
            )
            cooling_tower.power.fan_power_ratio_function_of_air_flow_rate_ratio_curve = \
                curve_model.coefficients if curve_model is not None else\
                    power_model.fan_power_ratio_function_of_air_flow_rate_ratio_curve

    @classmethod
    def _validate_required_field(cls, value, field_name: str) -> None:
        camel_case_field_name = cls._to_camel_case(field_name)
        if not value:
            raise ValueError(f"{camel_case_field_name} is required.")

    @classmethod
    def _validate_float_or_autosize(cls, value, min_value, max_value, field_name: str) -> None:
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
    def _validate_float(cls, value, min_value, max_value, field_name: str) -> None:
        camel_case_field_name = cls._to_camel_case(field_name)
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
