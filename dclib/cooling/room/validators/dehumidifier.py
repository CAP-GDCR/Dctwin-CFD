from typing import OrderedDict

from dclib.cooling.room.facilities.dehumidifier import Dehumidifier

from dclib.models.curves.dehumidier import DehumidifierCurveModels
from dclib.models.geometry import DehumidifierGeometryModel
from dclib.models.cooling import DehumidifierCoolingModel
from dclib.models.power import DehumidifierPowerModel
from dclib.models.control import DehumidifierControlModel

from dclib.data import DehumidifierInputs


class DehumidifierValidator:
    """ A class to validate the dehumidifier component
    """
    
    @classmethod
    def validate(
        cls,
        dehumidifiers: OrderedDict[str, Dehumidifier],
        geometry_models: OrderedDict[str, DehumidifierGeometryModel],
        cooling_models: OrderedDict[str, DehumidifierCoolingModel],
        power_models: OrderedDict[str, DehumidifierPowerModel],
        control_models: OrderedDict[str, DehumidifierControlModel],
        curve_models: DehumidifierCurveModels,
        inputs: OrderedDict[str, DehumidifierInputs],
    ) -> None:
        """ Validate the dehumidifier component
        """
        for dehumidifier_id, dehumidifier in dehumidifiers.items():
            dehumidifier.uid = dehumidifier_id if dehumidifier.uid == "" else dehumidifier.uid
            if geometry_models is not None:
                cls._validate_geometry_models(
                    dehumidifier=dehumidifier,
                    geometry_models=geometry_models,
                )

            if cooling_models is not None:
                cls._validate_cooling_models(
                    dehumidifier=dehumidifier,
                    cooling_models=cooling_models,
                    curve_models=curve_models,
                )

            if control_models is not None:
                cls._validate_controller_models(
                    dehumidifier=dehumidifier,
                    control_models=control_models,
                )

            if power_models is not None:
                cls._validate_power_models(
                    dehumidifier=dehumidifier,
                    power_models=power_models,
                    curve_models=curve_models,
                )

            if inputs is not None:
                cls._validate_inputs(dehumidifier, inputs)


    @classmethod
    def _validate_geometry_models(
        cls,
        dehumidifier: Dehumidifier,
        geometry_models: OrderedDict[str, DehumidifierGeometryModel],
    ) -> None:
        model_name = dehumidifier.geometry.model
        if isinstance(dehumidifier, Dehumidifier) and model_name is not None:
            dehumidifier.geometry.size = geometry_models.get(model_name).size
            dehumidifier.geometry.supply_face = geometry_models.get(model_name).supply_face
            dehumidifier.geometry.return_face = geometry_models.get(model_name).return_face
        else:
            raise ValueError(
                f"Invalid model name for: {type(dehumidifier)} {dehumidifier.uid}: "
                f"The object geometry model is not defined."
            )

    @classmethod
    def _validate_cooling_models(
        cls,
        dehumidifier: Dehumidifier,
        cooling_models: OrderedDict[str, DehumidifierCoolingModel],
        curve_models: DehumidifierCurveModels,
    ) -> None:
        model_name = dehumidifier.cooling.model
        if isinstance(dehumidifier, Dehumidifier) and model_name is not None:
            cooling_model = cooling_models.get(model_name)
            dehumidifier.cooling.rated_water_removal = cooling_model.rated_water_removal
            dehumidifier.cooling.rated_air_flow_rate = cooling_model.rated_air_flow_rate
            dehumidifier.cooling.minimum_dry_bulb_temperature = cooling_model.minimum_dry_bulb_temperature
            dehumidifier.cooling.maximum_dry_bulb_temperature = cooling_model.maximum_dry_bulb_temperature

            if curve_models and curve_models.water_removal_curve_models:
                curve_model = curve_models.water_removal_curve_models.get(
                    cooling_model.water_removal_curve_model, None
                )
                dehumidifier.cooling.water_removal_curve = \
                    curve_model.coefficients if curve_model != None else cooling_model.water_removal_curve

        else:
            raise ValueError(
                f"Invalid model name for: {type(dehumidifier)} {dehumidifier.uid}: "
                f"The object cooling model is not defined."
            )

    @classmethod
    def _validate_power_models(
        cls,
        dehumidifier: Dehumidifier,
        power_models: OrderedDict[str, DehumidifierPowerModel],
        curve_models: DehumidifierCurveModels,
    ) -> None:
        model_name = dehumidifier.power.model
        if isinstance(dehumidifier, Dehumidifier) and model_name is not None:
            power_model = power_models.get(model_name)
            dehumidifier.power.rated_energy_factor = power_model.rated_energy_factor
            dehumidifier.power.off_cycle_parasitic_electric_load = power_model.off_cycle_parasitic_electric_load

            if curve_models and curve_models.energy_factor_curve_models:
                curve_model = curve_models.energy_factor_curve_models.get(
                    power_model.energy_factor_curve_model, None
                )
                dehumidifier.power.energy_factor_curve = \
                    curve_model.coefficients if curve_model != None else power_model.energy_factor_curve
                
            if curve_models and curve_models.part_load_fraction_correlation_curve_models:
                curve_model = curve_models.part_load_fraction_correlation_curve_models.get(
                    power_model.part_load_fraction_correlation_curve_model, None
                )
                dehumidifier.power.part_load_fraction_correlation_curve = \
                    curve_model.coefficients if curve_model != None else power_model.part_load_fraction_correlation_curve

        else:
            raise ValueError(
                f"Invalid model name for: {type(dehumidifier)} {dehumidifier.uid}: "
                f"The object power model is not defined."
            )

    @classmethod
    def _validate_controller_models(
        cls,
        dehumidifier: Dehumidifier,
        control_models: OrderedDict[str, DehumidifierControlModel]
    ) -> None:
        model_name = dehumidifier.controller.model
        if isinstance(dehumidifier, Dehumidifier) and model_name is not None:
            pass
        else:
            raise ValueError(
                f"Invalid model name for: {type(dehumidifier)} {dehumidifier.uid}: "
                f"The object controller model is not defined."
            )

    @classmethod
    def _validate_inputs(
        cls,
        dehumidifier: Dehumidifier,
        inputs: OrderedDict[str, DehumidifierInputs]
    ) -> None:
        input_ = inputs.get(dehumidifier.uid)
        if isinstance(dehumidifier, Dehumidifier) and input_ is not None:
            pass
        else:
            raise ValueError(
                f"Invalid model name for: {type(dehumidifier)} {dehumidifier.uid}: "
                f"The object inputs model is not defined."
            )
