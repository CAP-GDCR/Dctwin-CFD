import json
from loguru import logger
from typing import Optional, OrderedDict, List, Union
from pydantic import Field, field_validator, ValidationInfo
from pathlib import Path

from dclib.cooling.plant.facilities import Chiller, CoolingTower
from dclib.cooling.common import Pump, HeatExchanger
from dclib.cooling.common.validators import PumpValidator, HeatExchangerValidator
from dclib.cooling.plant.validators import ChillerValidator, CoolingTowerValidator
from dclib.models.composite import Models
from dclib.models.geometry.basics import Vertex
from dclib.data import Inputs
from dclib.models.base import BaseModel


class PlantsGeometry(BaseModel):
    height: float
    planes: Union[List[Vertex], List[List[Vertex]]] = Field(default_factory=list)

    @field_validator("planes")
    def validate_planes(cls, value):
        if all(isinstance(i, list) for i in value):
            if all(isinstance(j, dict) for i in value for j in i):
                return [[Vertex(**point) for point in plane] for plane in value]
            return value

        if all(isinstance(i, dict) for i in value):
            return [Vertex(**point) for point in value]

        return value


class PlantsConstruction(BaseModel):
    """PlantsConstruction is used to build the objects in a plant"""

    chiller: Optional[OrderedDict[str, Chiller]] = None
    pump: Optional[OrderedDict[str, Pump]] = None
    cooling_tower: Optional[OrderedDict[str, CoolingTower]] = Field(
        None, alias="cooling_tower"
    )


class Plants(BaseModel):
    """A class to represent a plant and the facilities in it"""

    uid: Optional[str] = ""
    meta: Optional[OrderedDict] = Field(default_factory=dict)
    models: Optional[Models] = Field(default_factory=Models)
    inputs: Optional[Inputs] = Field(default_factory=Inputs)
    geometry: PlantsGeometry
    constructions: PlantsConstruction = Field(default_factory=PlantsConstruction)

    @field_validator("constructions")
    def _validate_plants_constructions(
        cls,
        plants_construction: PlantsConstruction,
        values: ValidationInfo,
    ) -> PlantsConstruction:
        if values.data["models"] is None:
            logger.warning("No models found")
            return plants_construction

        # Validate chillers
        if plants_construction.chiller is not None:
            curve_models = values.data["models"].curve_models.chillers if values.data["models"].curve_models else None
            ChillerValidator.validate(
                chillers=plants_construction.chiller,
                cooling_models=values.data["models"].cooling_models.chillers,
                power_models=values.data["models"].power_models.chillers,
                curve_models=curve_models,
            )

        # Validate cooling towers
        if plants_construction.cooling_tower is not None:
            curve_models = values.data["models"].curve_models.cooling_towers if values.data["models"].curve_models else None
            CoolingTowerValidator.validate(
                cooling_towers=plants_construction.cooling_tower,
                cooling_models=values.data["models"].cooling_models.cooling_towers,
                power_models=values.data["models"].power_models.cooling_towers,
                curve_models=curve_models
            )

        # Validate pumps
        control_models = values.data["models"].control_models
        if plants_construction.pump is not None:
            curve_models = values.data["models"].curve_models.pumps if values.data[
                "models"].curve_models else None
            PumpValidator.validate(
                pumps=plants_construction.pump,
                cooling_models=values.data["models"].cooling_models.pumps,
                power_models=values.data["models"].power_models.pumps,
                curve_models=curve_models,
                control_models=control_models.pumps
                if control_models and hasattr(control_models, "pumps")
                else None,
            )

        logger.info("Completed validation for PlantsConstruction")
        return plants_construction

    @classmethod
    def load(cls, path: str | Path) -> "Plants":
        with open(path, "r") as f:
            data = json.load(f)
        return cls(**data)
