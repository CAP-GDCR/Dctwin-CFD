""" Building object
"""
import json
from typing import Optional, OrderedDict, List

from pydantic import Field, field_validator, ValidationInfo
from pathlib import Path

from dclib.room import Room, Surface
from dclib.cooling.plant.plant import Plant
from dclib.models.geometry.basics import Geometry

from dclib.data import Inputs
from dclib.electrical import ElectricalLoadCenter
from dclib.validators.room import RoomValidator
from dclib.validators.plant import PlantValidator
from dclib.electrical.validators import ElectricalLoadCenterValidator

from dclib.models.base import BaseModel
from dclib.models.composite import Models


class BuildingConstruction(BaseModel):
    """ RoomConstruction is used to build the objects in a room
    """
    surfaces: Optional[OrderedDict[str, Surface]] = None
    zones: OrderedDict[str, Room]
    plant: Optional[Plant] = {}
    electrical_load_centers: Optional[OrderedDict[str, ElectricalLoadCenter]] = {}

    @property
    def cdus_keys(self) -> List[str]:
        res = []
        for zone_name, zone in self.zones.items():
            for loop in zone.constructions.liquid_flow_networks.values():
                for branch in loop.supply_branches.values():
                    if branch.components.cdus:
                        for cdu_name, cdu in branch.components.cdus.items():
                            res.append(cdu.uid)
        return res

    @property
    def ite_keys(self) -> List[str]:
        res = []
        for zone_name, zone in self.zones.items():
            if zone.constructions.heat_gains is not None and zone.constructions.heat_gains.ites is not None:
                for ite_name, ite in zone.constructions.heat_gains.ites.items():
                    res.append(ite.uid)
        return res

    @property
    def acu_keys(self) -> List[str]:
        res = []
        for zone_name, zone in self.zones.items():
            for acu_name, acu in zone.constructions.acus.items():
                res.append(acu.uid)
        return res

    @property
    def dehumidifier_keys(self) -> List[str]:
        res = []
        for zone_name, zone in self.zones.items():
            if zone.constructions.dehumidifiers:
                for dehumidifier_name, dehumidifier in zone.constructions.dehumidifiers.items():
                    res.append(dehumidifier.uid)
        return res

    @property
    def thermal_storage_tank_keys(self) -> List[str]:
        res = []
        if self.plant.secondary_chilled_water_loops:
            for loop in self.plant.secondary_chilled_water_loops.values():
                for branch in loop.supply_branches.values():
                    if branch.components.tanks:
                        for tank_name, tank in branch.components.tanks.items():
                            res.append(tank.uid)
        return res

    @property
    def chilled_water_pump_keys(self) -> List[str]:
        pumps = []
        for loop in self.plant.chilled_water_loops.values():
            for branch in loop.supply_branches.values():
                if branch.components.pumps:
                    for pump_name, pump in branch.components.pumps.items():
                        pumps.append(pump.uid)
            for branch in loop.demand_branches.values():
                if branch.components.pumps:
                    for pump_name, pump in branch.components.pumps.items():
                        pumps.append(pump.uid)
        return pumps

    @property
    def secondary_chilled_water_pump_keys(self) -> List[str]:
        pumps = []
        if self.plant.secondary_chilled_water_loops:
            for loop in self.plant.secondary_chilled_water_loops.values():
                for branch in loop.supply_branches.values():
                    if branch.components.pumps:
                        for pump_name, pump in branch.components.pumps.items():
                            pumps.append(pump.uid)
                for branch in loop.demand_branches.values():
                    if branch.components.pumps:
                        for pump_name, pump in branch.components.pumps.items():
                            pumps.append(pump.uid)
        return pumps

    @property
    def chiller_keys(self) -> List[str]:
        chillers = []
        for loop in self.plant.chilled_water_loops.values():
            for branch in loop.supply_branches.values():
                if branch.components.chillers:
                    for chiller_name, chiller in branch.components.chillers.items():
                        chillers.append(chiller.uid)
        return chillers

    @property
    def tank_keys(self) -> List[str]:
        tanks = []
        if self.plant.secondary_chilled_water_loops:
            for loop in self.plant.secondary_chilled_water_loops.values():
                for branch in loop.supply_branches.values():
                    if branch.components.tanks:
                        for tank_name, tank in branch.components.tanks.items():
                            tanks.append(tank.uid)
        return tanks

    @property
    def heat_exchanger_keys(self) -> List[str]:
        heat_exchangers = []
        for loop in self.plant.chilled_water_loops.values():
            for branch in loop.supply_branches.values():
                if branch.components.heat_exchangers:
                    for heat_exchanger_name, heat_exchanger in branch.components.heat_exchangers.items():
                        heat_exchangers.append(heat_exchanger.uid)
        return heat_exchangers
    
    @property
    def condenser_water_pump_keys(self) -> List[str]:
        pumps = []
        if self.plant.condenser_water_loops:
            for loop in self.plant.condenser_water_loops.values():
                for branch in loop.supply_branches.values():
                    if branch.components.pumps:
                        for pump_name, pump in branch.components.pumps.items():
                            pumps.append(pump.uid)
                for branch in loop.demand_branches.values():
                    if branch.components.pumps:
                        for pump_name, pump in branch.components.pumps.items():
                            pumps.append(pump.uid)
        return pumps

    @property
    def cooling_tower_keys(self) -> List[str]:
        towers = []
        if self.plant.condenser_water_loops:
            for loop in self.plant.condenser_water_loops.values():
                for branch in loop.supply_branches.values():
                    if branch.components.cooling_towers:
                        for tower_name, tower in branch.components.cooling_towers.items():
                            towers.append(tower.uid)
        return towers

    @property
    def generator_keys(self) -> List[str]:
        generators = []
        for center_name, center in self.electrical_load_centers.items():
            for generator_name, generator in center.generators.items():
                generators.append(generator.uid)
        return generators

    @property
    def inverter_keys(self) -> List[str]:
        inverters = []
        for center_name, center in self.electrical_load_centers.items():
            inverters.append(center.inverter.uid) 
        return inverters

    @property
    def num_zones(self) -> int:
        return len(self.zones)

    @property
    def num_acus(self) -> int:
        res = 0
        for zone_name, zone in self.zones.items():
            res += len(zone.constructions.acus) if zone.constructions.acus else 0
        return res

    @property
    def num_chillers(self) -> int:
        num_chillers = 0
        for loop in self.plant.chilled_water_loops.values():
            for branch in loop.supply_branches.values():
                num_chillers += len(branch.components.chillers) if branch.components.chillers else 0
        return num_chillers

    @property
    def num_tanks(self) -> int:
        num_tanks = 0
        if self.plant.secondary_chilled_water_loops:
            for loop in self.plant.secondary_chilled_water_loops.values():
                for branch in loop.supply_branches.values():
                    num_tanks += len(branch.components.tanks) if branch.components.tanks else 0
        return num_tanks

    @property
    def num_cooling_towers(self) -> int:
        num_towers = 0
        for loop in self.plant.condenser_water_loops.values():
            for branch in loop.supply_branches.values():
                num_towers += len(branch.components.cooling_towers) if branch.components.cooling_towers else 0
        return num_towers

    @property
    def num_pumps(self) -> int:
        num_pumps = 0
        for loop in self.plant.chilled_water_loops.values():
            for branch in loop.supply_branches.values():
                num_pumps += len(branch.components.pumps) if branch.components.pumps else 0
        for loop in self.plant.condenser_water_loops.values():
            for branch in loop.supply_branches.values():
                num_pumps += len(branch.components.pumps) if branch.components.pumps else 0
        return num_pumps


class Building(BaseModel):
    """ A class to represent a building and the facilities in it """
    uid: Optional[str] = ""
    meta: Optional[OrderedDict] = Field(default_factory=dict)
    models: Optional[Models] = Field(default_factory=Models)
    geometry: Optional[Geometry] = None
    inputs: Optional[Inputs] = Field(default_factory=Inputs)
    constructions: BuildingConstruction

    @field_validator("constructions")
    def _validate_constructions(
        cls,
        building_constructions: BuildingConstruction,
        values: ValidationInfo
    ) -> BuildingConstruction:
        """ Validate the construction of the building """
        RoomValidator.validate(
            rooms=building_constructions.zones,
            values=values
        )
        PlantValidator.validate(
            plant=building_constructions.plant,
            rooms=building_constructions.zones,
            values=values
        )
        ElectricalLoadCenterValidator.validate(
            rooms=building_constructions.zones,
            electric_load_centers=building_constructions.electrical_load_centers,
            values=values
        )
        return building_constructions

    @classmethod
    def load(cls, path: str | Path) -> "Building":
        with open(path, "r") as f:
            data = json.load(f)
        return cls(**data)
