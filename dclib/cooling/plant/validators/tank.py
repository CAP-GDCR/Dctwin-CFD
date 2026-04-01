from typing import OrderedDict

from dclib.cooling.plant.facilities.tank import Tank
from dclib.models.cooling.tank import TankCoolingModel, Indicator
from dclib.models.control.tank import TankControlModel


class TankValidator:
    """ A class to validate tank of a building """

    @classmethod
    def _validate_cooling_models(cls, tank: Tank, cooling_models: OrderedDict[str, TankCoolingModel]) -> None:
        """
        Validate the cooling models of the tank.
        """
        cooling_model = cooling_models.get(tank.cooling.model, None)
        if cooling_model is not None:
            tank.cooling.tank_volume = cooling_model.tank_volume
            tank.cooling.deadband_temperature_difference = cooling_model.deadband_temperature_difference
            tank.cooling.minimum_temperature_limit = cooling_model.minimum_temperature_limit
            tank.cooling.nominal_cooling_capacity = cooling_model.nominal_cooling_capacity
            tank.cooling.heat_gain_coefficient_from_ambient_temperature = \
                cooling_model.heat_gain_coefficient_from_ambient_temperature
            tank.cooling.use_side_heat_transfer_effectiveness = cooling_model.use_side_heat_transfer_effectiveness
            tank.cooling.use_side_design_flow_rate = cooling_model.use_side_design_flow_rate
            tank.cooling.source_side_heat_transfer_effectiveness = cooling_model.source_side_heat_transfer_effectiveness
            tank.cooling.source_side_design_flow_rate = cooling_model.source_side_design_flow_rate
            tank.cooling.tank_recovery_time = cooling_model.tank_recovery_time

    @classmethod
    def _validate_control_models(
        cls,
        tank: Tank,
        control_models: OrderedDict[str, TankControlModel]
    ) -> None:
        """
        Validate the control models of the tank.
        """
        control_model = control_models.get(tank.control.model, None)
        if control_model is not None:
            """Fill in control model parameters"""
            tank.control.use_side_availability_schedule_name = control_model.use_side_availability_schedule_name
            tank.control.source_side_availability_schedule_name = control_model.source_side_availability_schedule_name
            tank.control.ambient_temperature_schedule_name = control_model.ambient_temperature_schedule_name

    @classmethod
    def validate(
        cls,
        tanks: OrderedDict[str, Tank],
        cooling_models: OrderedDict[str, TankCoolingModel],
        control_models: OrderedDict[str, TankControlModel]
    ) -> None:
        """
        Validate the tanks of the loop. In the validation, we fill in the parameters of the tanks by
        using the parameters defined in the cooling and power models.
        """
        for tank_name, tank in tanks.items():
            tank.uid = tank_name if tank.uid == "" else tank.uid
            tank.other_loop_side = tank.other_loop_side
            tank.upstream_component = tank.upstream_component
            if tank.cooling.ambient_temperature_indicator == Indicator.zone:
                assert tank.cooling.ambient_temperature_zone_name != "", \
                    "The ambient temperature zone name should not be empty when the ambient temperature indicator is zone."
            elif tank.cooling.ambient_temperature_indicator == Indicator.outdoors:
                assert tank.cooling.ambient_temperature_acu_name != "", \
                    "The ambient temperature outdoor air node name should not be empty when the ambient temperature indicator is outdoors."
            else:
                raise ValueError(
                    "The ambient temperature indicator should be either 'zone' or 'outdoors'."
                )
            cls._validate_cooling_models(tank, cooling_models) if cooling_models else None
            cls._validate_control_models(tank, control_models) if control_models else None
