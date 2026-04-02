""" Zone Composite ITE Model
"""
from typing import Optional, OrderedDict

from dclib.models.curves.ite import ITECurveModels
from dclib.models.heat_gains.ite import ITEModel


class ITE(ITEModel):
    """
    Composite ITE model for a zone. It is used to model the heat gains from the ITEs in a
    zone. Here in our library, as we model the fine-grained server configurations and their heat gains, we use this
    object to serve as a "composite" ITE model for a zone. The heat gains from the servers in a zone are modeled as an
    "Air-cooled IT equipment thermal model". The parameters of this object are defined in this class. This
    class inherits from the ITEModel class, which defines the detailed parameters of the air-cooled ITE
    object. In this class, we define the key parameters only.

    :param watts_per_unit: The power consumption of a single server in the zone. Unit: W
    :param zone: The name of the zone that the composite ITE model belongs to
    :param number_of_units: The number of servers in the zone
    :param model: The name of the ITE model. It should be defined in the ITEModel class. If not defined, the default
    values defined in the input file will be used.
    :param supply_air_node_name: The name of the supply air node of the zone.
    :param air_inlet_room_air_model_node_name: The name of the air inlet room air model node of the zone.
    :param air_outlet_room_air_model_node_name: The name of the air outlet room air model node of the zone.
    """
    uid: Optional[str] = ""
    zone: str
    watts_per_unit: float
    number_of_units: int
    model: Optional[str] = None
    supply_air_node_name: str = ""
    associated_acu: str = ""
    air_inlet_room_air_model_node_name: Optional[str] = ""
    air_outlet_room_air_model_node_name: Optional[str] = ""

    @property
    def rated_power(self):
        """
        The rated power of the composite ITE model. Unit: W
        Note: the related power must be equal to the sum of the rated power consumption of all the servers in the zone,
        if the servers are defined in the zone construction object.
        :return:
        """
        return self.watts_per_unit * self.number_of_units


class ITEValidator:
    """ A class to validate the composite ITE object for a zone """

    @classmethod
    def validate(
        cls,
        ites: OrderedDict[str, ITE],
        ite_models: OrderedDict[str, ITEModel],
        curve_models: ITECurveModels,
    ) -> None:
        """
        Fill in the parameters of the composite ITE object for a zone with the data stored in ITE model.
        :param ites:  The composite ITE object for a zone
        :param ite_models: The ITE model defined in the input file
        :param curve_models: The curve models defined in the input file
        :return:
        """
        for ite_name, ite in ites.items():
            ite.uid = ite_name if ite.uid == "" else ite.uid
            # see if the ite model is defined, otherwise using the values defined in the input file
            ite_model = ite_models.get(ite.model, None)
            if isinstance(ite_model, ITEModel) and ite_model is not None:

                ite.air_flow_calculation_method = \
                    cls._validate_air_flow_calculation_method(ite_model.air_flow_calculation_method)
                ite.air_inlet_connection_type = ite_model.air_inlet_connection_type

                ite.cpu_enduse_subcategory = ite_model.cpu_enduse_subcategory
                ite.fan_enduse_subcategory = ite_model.fan_enduse_subcategory
                ite.electric_power_supply_enduse_subcategory = ite_model.electric_power_supply_enduse_subcategory

                ite.environmental_class = ite_model.environmental_class

                ite.cpu_loading_schedule_name = ite_model.cpu_loading_schedule_name

                ite.design_electric_power_supply_efficiency = ite_model.design_electric_power_supply_efficiency
                ite.design_entering_air_temperature = ite_model.design_entering_air_temperature
                ite.design_fan_air_flow_rate_per_power_input = ite_model.design_fan_air_flow_rate_per_power_input
                ite.design_fan_power_input_fraction = ite_model.design_fan_power_input_fraction
                ite.design_power_input_calculation_method = ite_model.design_power_input_calculation_method
                ite.design_power_input_schedule_name = ite_model.design_power_input_schedule_name
                ite.design_recirculation_fraction = ite_model.design_recirculation_fraction
                ite.fraction_of_electric_power_supply_losses_to_zone = (
                    ite_model.fraction_of_electric_power_supply_losses_to_zone)

                """ Fill in curve parameters """
                if ite_model.cpu_power_input_function_of_loading_and_air_temperature_curve_model != "":
                    assert curve_models and curve_models.cpu_power_input_function_of_loading_and_air_temperature_curve_models, \
                        f"{ite_model.cpu_power_input_function_of_loading_and_air_temperature_curve_model} is not defined in the ites curve models."
                    curve_model = curve_models.cpu_power_input_function_of_loading_and_air_temperature_curve_models
                    ite.cpu_power_input_function_of_loading_and_air_temperature_curve = [
                        curve_model[
                            ite_model.cpu_power_input_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[0],
                        curve_model[
                            ite_model.cpu_power_input_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[1],
                        curve_model[
                            ite_model.cpu_power_input_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[2],
                        curve_model[
                            ite_model.cpu_power_input_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[3],
                        curve_model[
                            ite_model.cpu_power_input_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[4],
                        curve_model[
                            ite_model.cpu_power_input_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[5],
                    ]
                
                if ite_model.air_flow_function_of_loading_and_air_temperature_curve_model != "":
                    assert curve_models and curve_models.air_flow_function_of_loading_and_air_temperature_curve_models, \
                        f"{ite_model.air_flow_function_of_loading_and_air_temperature_curve_model} is not defined in the ites curve models."
                    curve_model = curve_models.air_flow_function_of_loading_and_air_temperature_curve_models
                    ite.air_flow_function_of_loading_and_air_temperature_curve = [
                        curve_model[
                            ite_model.air_flow_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[0],
                        curve_model[
                            ite_model.air_flow_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[1],
                        curve_model[
                            ite_model.air_flow_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[2],
                        curve_model[
                            ite_model.air_flow_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[3],
                        curve_model[
                            ite_model.air_flow_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[4],
                        curve_model[
                            ite_model.air_flow_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[5],
                    ]

                if ite_model.electric_power_supply_efficiency_function_of_part_load_ratio_curve_model != "":
                    assert curve_models and curve_models.electric_power_supply_efficiency_function_of_part_load_ratio_curve_models, \
                        f"{ite_model.electric_power_supply_efficiency_function_of_part_load_ratio_curve_model} is not defined in the ites curve models."
                    curve_model = curve_models.electric_power_supply_efficiency_function_of_part_load_ratio_curve_models
                    ite.electric_power_supply_efficiency_function_of_part_load_ratio_curve = [
                        curve_model[
                            ite_model.electric_power_supply_efficiency_function_of_part_load_ratio_curve_model
                        ].coefficients[0],
                        curve_model[
                            ite_model.electric_power_supply_efficiency_function_of_part_load_ratio_curve_model
                        ].coefficients[1],
                        curve_model[
                            ite_model.electric_power_supply_efficiency_function_of_part_load_ratio_curve_model
                        ].coefficients[2],
                    ]

                if ite_model.fan_power_input_function_of_flow_curve_model != "":
                    assert curve_models and curve_models.fan_power_input_function_of_flow_curve_models, \
                        f"{ite_model.fan_power_input_function_of_flow_curve_model} is not defined in the ites curve models."
                    curve_model = curve_models.fan_power_input_function_of_flow_curve_models
                    ite.fan_power_input_function_of_flow_curve = [
                        curve_model[
                            ite_model.fan_power_input_function_of_flow_curve_model
                        ].coefficients[0],
                        curve_model[
                            ite_model.fan_power_input_function_of_flow_curve_model
                        ].coefficients[1],
                        curve_model[
                            ite_model.fan_power_input_function_of_flow_curve_model
                        ].coefficients[2],
                    ]

                if ite_model.recirculation_fraction_function_of_loading_and_air_temperature_curve_model != "":
                    assert curve_models and curve_models.recirculation_fraction_function_of_loading_and_air_temperature_curve_models, \
                        f"{ite_model.recirculation_fraction_function_of_loading_and_air_temperature_curve_model} is not defined in the ites curve models."
                    curve_model = \
                        curve_models.recirculation_fraction_function_of_loading_and_air_temperature_curve_models
                    ite.recirculation_fraction_function_of_loading_and_air_temperature_curve = [
                        curve_model[
                            ite_model.recirculation_fraction_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[0],
                        curve_model[
                            ite_model.recirculation_fraction_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[1],
                        curve_model[
                            ite_model.recirculation_fraction_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[2],
                        curve_model[
                            ite_model.recirculation_fraction_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[3],
                        curve_model[
                            ite_model.recirculation_fraction_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[4],
                        curve_model[
                            ite_model.recirculation_fraction_function_of_loading_and_air_temperature_curve_model
                        ].coefficients[5],
                    ]

    @classmethod
    def _validate_air_flow_calculation_method(cls, v) -> str:
        if v != "FlowFromSystem" and v != "FlowControlWithApproachTemperatures":
            raise ValueError("ITE Air flow calculation method must be either FlowFromSystem or FlowControlWithApproachTemperatures")
        return v
