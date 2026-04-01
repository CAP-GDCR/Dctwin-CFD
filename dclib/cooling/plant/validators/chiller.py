from typing import OrderedDict
from dclib.cooling.plant.facilities.chiller import Chiller
from dclib.models.cooling.chiller import ChillerCoolingModel
from dclib.models.power.chiller import ChillerPowerModel
from dclib.models.curves.chiller import ChillerCurveModels


class ChillerValidator:
    """A class to validate chillers of a building/plant"""

    @classmethod
    def validate(
        cls,
        chillers: OrderedDict[str, Chiller],
        cooling_models: OrderedDict[str, ChillerCoolingModel],
        power_models: OrderedDict[str, ChillerPowerModel],
        # Set to Optional to cater both plants and building file
        curve_models: ChillerCurveModels = None,
    ) -> None:
        """
        Validate the chillers of the loop. In the validation, we fill in the parameters of the chillers by using the
        parameters defined in the cooling and power models.
        """
        for chiller_name, chiller in chillers.items():
            chiller.uid = chiller_name if chiller.uid == "" else chiller.uid
            chiller.other_loop_side = chiller.other_loop_side
            chiller.upstream_component = chiller.upstream_component

            cls._validate_cooling_models(chiller, cooling_models, curve_models) if cooling_models else None
            cls._validate_power_models(chiller, power_models, curve_models) if power_models else None

    @classmethod
    def _validate_cooling_models(
        cls,
        chiller: Chiller,
        cooling_models: OrderedDict[str, ChillerCoolingModel],
        curve_models: ChillerCurveModels,
    ) -> None:
        cooling_model = cooling_models.get(chiller.cooling.model, None)
        if cooling_model is not None:
            chiller.cooling.reference_capacity = cooling_model.reference_capacity
            chiller.cooling.reference_cop = cooling_model.reference_cop
            chiller.cooling.reference_leaving_chilled_water_temperature = (
                cooling_model.reference_leaving_chilled_water_temperature
            )
            chiller.cooling.reference_entering_condenser_fluid_temperature = (
                cooling_model.reference_entering_condenser_fluid_temperature
            )

            chiller.cooling.reference_chilled_water_flow_rate = cooling_model.reference_chilled_water_flow_rate
            chiller.cooling.reference_condenser_fluid_flow_rate = cooling_model.reference_condenser_fluid_flow_rate

            if curve_models is not None:
                curve_model = curve_models.cooling_capacity_function_of_temperature_curve_models.get(
                    cooling_model.cooling_capacity_function_of_temperature_curve_model, None
                )
                chiller.cooling.cooling_capacity_function_of_temperature_curve = (
                    curve_model.coefficients
                    if curve_model is not None
                    else cooling_model.cooling_capacity_function_of_temperature_curve
                )
            else:
                chiller.cooling.cooling_capacity_function_of_temperature_curve = (
                    cooling_model.cooling_capacity_function_of_temperature_curve
                )

            chiller.cooling.minimum_part_load_ratio = cooling_model.minimum_part_load_ratio
            chiller.cooling.maximum_part_load_ratio = cooling_model.maximum_part_load_ratio
            chiller.cooling.optimum_part_load_ratio = cooling_model.optimum_part_load_ratio
            chiller.cooling.minimum_unloading_ratio = cooling_model.minimum_unloading_ratio

            chiller.cooling.condenser_type = cooling_model.condenser_type

            chiller.cooling.leaving_chilled_water_lower_temperature_limit = (
                cooling_model.leaving_chilled_water_lower_temperature_limit
            )
            chiller.cooling.chiller_flow_mode = cooling_model.chiller_flow_mode
            chiller.cooling.design_heat_recovery_water_flow_rate = cooling_model.design_heat_recovery_water_flow_rate

            chiller.cooling.basin_heater_capacity = cooling_model.basin_heater_capacity
            chiller.cooling.basin_heater_setpoint_temperature = cooling_model.basin_heater_setpoint_temperature
            chiller.cooling.sizing_factor = cooling_model.sizing_factor

    @classmethod
    def _validate_power_models(
        cls,
        chiller: Chiller,
        power_models: OrderedDict[str, ChillerPowerModel],
        curve_models: ChillerCurveModels,
    ) -> None:
        power_model = power_models.get(chiller.power.model, None)
        if power_model is not None:
            if curve_models is not None:
                curve_model = (
                    curve_models.electric_input_to_cooling_output_ratio_function_of_temperature_curve_models.get(
                        power_model.electric_input_to_cooling_output_ratio_function_of_temperature_curve_model, None
                    )
                )
                chiller.power.electric_input_to_cooling_output_ratio_function_of_temperature_curve = (
                    curve_model.coefficients
                    if curve_model is not None
                    else power_model.electric_input_to_cooling_output_ratio_function_of_temperature_curve
                )

                curve_model = (
                    curve_models.electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve_models.get(
                        power_model.electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve_model, None
                    )
                )
                chiller.power.electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve = (
                    curve_model.coefficients
                    if curve_model is not None
                    else power_model.electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve
                )
            else:
                chiller.power.electric_input_to_cooling_output_ratio_function_of_temperature_curve = (
                    power_model.electric_input_to_cooling_output_ratio_function_of_temperature_curve
                )
                chiller.power.electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve = (
                    power_model.electric_input_to_cooling_output_ratio_function_of_part_load_ratio_curve
                )
