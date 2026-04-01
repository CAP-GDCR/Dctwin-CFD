from typing import OrderedDict

from dclib.cooling.room.facilities.cdu import CDU

from dclib.models.composite import Models
from dclib.models.geometry.cdu import CDUGeometryModel
from dclib.cooling.room.facilities.cdu import CDUConstructions

from dclib.cooling.common.validators.heat_exchanger import HeatExchangerValidator
from dclib.cooling.common.validators.pump import PumpValidator


class CDUValidator:
    """ A class to validate CDU of a building"""
    @classmethod
    def validate(
        cls,
        cdus: OrderedDict[str, CDU],
        models: Models,
    ) -> None:
        """
        Validate the CDUs of the loop. In the validation, we fill in the parameters of the CDUs by using the
        parameters defined in the cooling and geometry models.
        """
        for cdu_name, cdu in cdus.items():
            cdu.uid = cdu_name if cdu.uid == "" else cdu.uid
            if models.geometry_models is not None and models.geometry_models.cdus is not None:
                cls._validate_geometry_models(cdu, models.geometry_models.cdus)
            cls._validate_constructions(
                cdu_name,
                cdu.constructions,
                models
            )

    @classmethod
    def _validate_geometry_models(
        cls,
        cdu: CDU,
        models: OrderedDict[str, CDUGeometryModel]
    ) -> None:
        model_name = cdu.geometry.model
        if isinstance(cdu, CDU) and model_name is not None:
            cdu.geometry.size = models.get(model_name).size
            cdu.geometry.main_pipe_diameter = models.get(model_name).main_pipe_diameter
            
    @classmethod
    def _validate_constructions(
        cls,
        cdu_name: str,
        cdu_constructions: CDUConstructions,
        models: Models
    ) -> None:
        HeatExchangerValidator.validate(
            heat_exchangers={cdu_name: cdu_constructions.heat_exchanger},
            geometry_models=models.geometry_models.heat_exchangers,
            cooling_models=models.cooling_models.heat_exchangers
        )
        PumpValidator.validate(
            pumps={cdu_name: cdu_constructions.pump},
            cooling_models=models.cooling_models.pumps,
            power_models=models.power_models.pumps,
            control_models=models.control_models.pumps,
            curve_models=models.curve_models.pumps
        )
