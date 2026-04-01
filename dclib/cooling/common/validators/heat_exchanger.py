from typing import OrderedDict

from dclib.cooling.common.heat_exchanger import HeatExchanger
from dclib.models.cooling.heat_exchanger import HeatExchangerCoolingModel
from dclib.models.geometry.heat_exchanger import HeatExchangerGeometryModel


class HeatExchangerValidator:
    """ A class to validate heat exchanger """
    @classmethod
    def validate(
        cls,
        heat_exchangers: OrderedDict[str, HeatExchanger],
        geometry_models: OrderedDict[str, HeatExchangerGeometryModel],
        cooling_models: OrderedDict[str, HeatExchangerCoolingModel],
    ) -> None:
        """
        Validate the heat exchangers of the loop. In the validation, we fill in the parameters of the heat exchangers by using the
        parameters defined in the cooling and geometry models.
        """
        for heat_exchanger_name, heat_exchanger in heat_exchangers.items():
            heat_exchanger.uid = heat_exchanger_name if heat_exchanger.uid == "" else heat_exchanger.uid
            heat_exchanger.other_loop_side = heat_exchanger.other_loop_side
            heat_exchanger.upstream_component = heat_exchanger.upstream_component
            cls._validate_geometry_models(heat_exchanger, geometry_models) if geometry_models else None
            cls._validate_cooling_models(heat_exchanger, cooling_models) if cooling_models else None
        
    @classmethod
    def _validate_cooling_models(
        cls,
        heat_exchanger: HeatExchanger,
        models: OrderedDict[str, HeatExchangerCoolingModel]
    ) -> None:
        model_name = heat_exchanger.cooling.model
        if isinstance(heat_exchanger, HeatExchanger) and model_name is not None:
            heat_exchanger.cooling.loop_supply_side_design_flow_rate = models.get(model_name).loop_supply_side_design_flow_rate
            heat_exchanger.cooling.loop_demand_side_design_flow_rate = models.get(model_name).loop_demand_side_design_flow_rate
            heat_exchanger.cooling.loop_supply_side_design_temperature = models.get(model_name).loop_supply_side_design_temperature
            heat_exchanger.cooling.loop_demand_side_design_temperature = models.get(model_name).loop_demand_side_design_temperature

            heat_exchanger.cooling.heat_exchanger_model_type = models.get(model_name).heat_exchanger_model_type
            heat_exchanger.cooling.heat_exchanger_u_factor_times_area_value = \
                models.get(model_name).heat_exchanger_u_factor_times_area_value
            heat_exchanger.cooling.control_type = models.get(model_name).control_type
            heat_exchanger.cooling.minimum_temperature_difference_to_activate_heat_exchanger = \
                models.get(model_name).minimum_temperature_difference_to_activate_heat_exchanger
            heat_exchanger.cooling.heat_transfer_metering_end_use_type \
                = models.get(model_name).heat_transfer_metering_end_use_type
            heat_exchanger.cooling.component_override_cooling_control_temperature_mode \
                = models.get(model_name).component_override_cooling_control_temperature_mode
            heat_exchanger.cooling.sizing_factor = models.get(model_name).sizing_factor
            heat_exchanger.cooling.operation_minimum_temperature_limit \
                = models.get(model_name).operation_minimum_temperature_limit
            heat_exchanger.cooling.operation_maximum_temperature_limit \
                = models.get(model_name).operation_maximum_temperature_limit

            heat_exchanger.cooling.material = models.get(model_name).material
            heat_exchanger.cooling.thermal_conductivity = models.get(model_name).thermal_conductivity

    @classmethod
    def _validate_geometry_models(
        cls,
        heat_exchanger: HeatExchanger,
        models: OrderedDict[str, HeatExchangerGeometryModel]
    ) -> None:
        model_name = heat_exchanger.geometry.model
        if isinstance(heat_exchanger, HeatExchanger) and model_name is not None:
            heat_exchanger.geometry.tube_diameter = models.get(model_name).tube_diameter
            heat_exchanger.geometry.tube_length = models.get(model_name).tube_length
            heat_exchanger.geometry.tube_wall_thickness = models.get(model_name).tube_wall_thickness
            heat_exchanger.geometry.row_number = models.get(model_name).row_number
            heat_exchanger.geometry.row_pitch = models.get(model_name).row_pitch
            heat_exchanger.geometry.transverse_number = models.get(model_name).transverse_number
            heat_exchanger.geometry.transverse_pitch = models.get(model_name).transverse_pitch
            heat_exchanger.geometry.tube_roughness = models.get(model_name).tube_roughness
