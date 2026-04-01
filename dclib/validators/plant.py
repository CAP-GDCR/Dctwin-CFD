from typing import OrderedDict

from dclib.cooling.room.facilities import ACU
from pydantic import ValidationInfo

from dclib.cooling.plant.plant import Plant
from dclib.cooling.plant.plant_loops import ChilledWaterLoops, CondenserWaterLoops, MetaLoop, SecondaryChilledWaterLoops
from dclib.cooling.plant.sizing import SizingPlant
from dclib.cooling.room.validators import ACUValidator

from dclib.models.composite import Models

from dclib.room import Room

from dclib.cooling.plant.validators import (
    CoolingTowerValidator,
    ChillerValidator,
    TankValidator,
)
from dclib.cooling.common.validators import (
    PipeValidator,
    PumpValidator,
    HeatExchangerValidator,
)


class PlantValidator:
    """ A class to validate plant-related aspects of a building """

    @classmethod
    def validate(cls, plant: Plant, rooms: OrderedDict[str, Room], values: ValidationInfo) -> None:
        """
        Validate the plant of the building. To validate the plant, we validate the following:
        1. The chilled water loop
        2. The condenser water loop
        3. The second chilled water loop
        """
        cls._validate_acu_connection(plant.secondary_chilled_water_loops, plant.chilled_water_loops, rooms)
        cls._validate_tank_connection(plant.secondary_chilled_water_loops, plant.chilled_water_loops)
        cls._validate_hx_connection(plant.chilled_water_loops, plant.condenser_water_loops)
        cls._validate_chiller_connection(
            plant.chilled_water_loops,
            plant.condenser_water_loops,
        )
        cls._validate_loop(plant.chilled_water_loops, values.data["models"])
        cls._validate_loop(plant.secondary_chilled_water_loops, values.data["models"])
        cls._validate_loop(plant.condenser_water_loops, values.data["models"])

    @classmethod
    def _validate_loop(
        cls,
        loops: OrderedDict[str, SecondaryChilledWaterLoops | ChilledWaterLoops | CondenserWaterLoops],
        models: Models
    ) -> None:
        """
        Validate the chilled water loop of the building. To validate the loop, we validate the following:
        1. The supply branches
        2. The demand branches
        3. The sizing parameters
        4. The meta parameters
        """
        if loops is not None:
            for loop_name, loop in loops.items():
                loop.uid = loop_name if loop.uid == "" else loop.uid
                cls._validate_supply_branches(loop.supply_branches, models)
                cls._validate_demand_branches(loop.demand_branches, models)
                cls._validate_sizing(loop.sizing)
                cls._validate_meta_loop(loop.meta)

    @classmethod
    def _validate_chiller_connection(
        cls,
        chilled_water_loops: OrderedDict[str, ChilledWaterLoops],
        condenser_water_loops: OrderedDict[str, CondenserWaterLoops],
    ) -> None:
        """
        Validate the connection of chillers between the chilled water loop and the condenser water loop. To validate the connection,
        we validate the following:
        1. Chiller uid is unique in each loop
        2. Chiller uid in chilled water loop supply branches is the same as the one in condenser water loop demand branches
        """

        chiller_supply_side_uids = set()
        chiller_demand_side_uids = set()
        chillers_dict = dict()

        supply_loop_side = dict()
        demand_loop_side = dict()

        for loop_name, chilled_water_loop in chilled_water_loops.items():
            for branch_name, branch in chilled_water_loop.supply_branches.items():
                if branch.components.chillers:
                    for chiller_name, chiller in branch.components.chillers.items():
                        if chiller_name in chiller_supply_side_uids:
                            raise ValueError(
                                f"Chiller {chiller_name} is not unique in chilled water loop supply branches.")
                        chiller_supply_side_uids.add(chiller_name)
                        chillers_dict[chiller_name] = chiller
                        supply_loop_side[chiller_name] = loop_name

                        if chiller.upstream_component is not None:
                            assert (chiller.upstream_component in
                                    branch.components.pumps or branch.components.heat_exchangers
                                    ), (f"Upstream component {chiller.upstream_component} "
                                        f"is not in the {branch_name} components")

        if condenser_water_loops is not None:
            for loop_name, condenser_water_loop in condenser_water_loops.items():
                for branch_name, branch in condenser_water_loop.demand_branches.items():
                    if branch.components.chillers:
                        for chiller_name, chiller in branch.components.chillers.items():
                            if chiller_name in chiller_demand_side_uids:
                                raise ValueError(
                                    f"Chiller {chiller_name} is not unique in condenser water loop demand branches.")
                            # relocate the same chiller to the demand branch
                            if chiller_name in chillers_dict.keys():
                                branch.components.chillers[chiller_name].cooling = chillers_dict[chiller_name].cooling
                                branch.components.chillers[chiller_name].power = chillers_dict[chiller_name].power
                                branch.components.chillers[chiller_name].model = chillers_dict[chiller_name].model
                                branch.components.chillers[chiller_name].meta = chillers_dict[chiller_name].meta
                            chiller_demand_side_uids.add(chiller_name)
                            chiller.other_loop_side = supply_loop_side[chiller_name]
                            demand_loop_side[chiller_name] = loop_name

                            if chiller.upstream_component is not None:
                                assert (chiller.upstream_component in
                                        branch.components.pumps or branch.components.heat_exchangers
                                        ), (f"Upstream component {chiller.upstream_component} "
                                            f"is not in the {branch_name} components")

        for loop_name, chilled_water_loop in chilled_water_loops.items():
            for branch_name, branch in chilled_water_loop.supply_branches.items():
                if branch.components.chillers:
                    for chiller_name, chiller in branch.components.chillers.items():
                        if chiller_name in chiller_demand_side_uids:
                            chiller.other_loop_side = demand_loop_side[chiller_name]

        chiller_uids_missing_in_condenser = chiller_supply_side_uids - chiller_demand_side_uids
        chiller_uids_missing_in_chilled = chiller_demand_side_uids - chiller_supply_side_uids
        if chiller_uids_missing_in_condenser and condenser_water_loops is not None:
            raise ValueError(
                f"Chiller {chiller_uids_missing_in_condenser} is missing in condenser water loop demand branches.")
        if chiller_uids_missing_in_chilled:
            raise ValueError(
                f"Chiller {chiller_uids_missing_in_chilled} is missing in chilled water loop supply branches.")

    @classmethod
    def _validate_tank_connection(
        cls,
        secondary_chilled_water_loops: OrderedDict[str, SecondaryChilledWaterLoops],
        chilled_water_loops: OrderedDict[str, ChilledWaterLoops]
    ) -> None:
        """
        Validate the connection of tanks between the chilled water loop and the  secondary chilled water loop.
        To validate the connection, we validate the following:
        1. tank uid is unique in each loop
        2. tank uid in secondary chilled water loop supply branches is the same as the one in chilled water loop demand branches
        """
        if secondary_chilled_water_loops is not None:

            tank_supply_side_uids = set()
            tank_demand_side_uids = set()
            tank_dict = dict()

            supply_loop_side = dict()
            demand_loop_side = dict()

            for loop_name, secondary_chilled_water_loop in secondary_chilled_water_loops.items():
                for branch_name, branch in secondary_chilled_water_loop.supply_branches.items():
                    if branch.components.tanks:
                        for tank_name, tank in branch.components.tanks.items():
                            if tank_name in tank_supply_side_uids:
                                raise ValueError(
                                    f"Tank {tank_name} is not unique in secondary chilled water loop supply branches.")
                            tank_supply_side_uids.add(tank_name)
                            tank_dict[tank_name] = tank
                            supply_loop_side[tank_name] = loop_name

                            if tank.upstream_component is not None:
                                assert (tank.upstream_component in
                                        branch.components.pumps or branch.components.heat_exchangers
                                        ), (f"Upstream component {tank.upstream_component} "
                                            f"is not in the {branch_name} components")


            for loop_name, chilled_water_loop in chilled_water_loops.items():
                for branch_name, branch in chilled_water_loop.demand_branches.items():
                    if branch.components.tanks:
                        for tank_name, tank in branch.components.tanks.items():
                            if tank_name in tank_demand_side_uids:
                                raise ValueError(
                                    f"Tank {tank_name} is not unique in secondary chilled water loop demand branches.")
                            # relocate the same tank to the demand branch
                            if tank_name in tank_dict.keys():
                                branch.components.tanks[tank_name].cooling = tank_dict[tank_name].cooling
                                branch.components.tanks[tank_name].control = tank_dict[tank_name].control
                                branch.components.tanks[tank_name].model = tank_dict[tank_name].model
                                branch.components.tanks[tank_name].meta = tank_dict[tank_name].meta
                            tank_demand_side_uids.add(tank_name)
                            tank.other_loop_side = supply_loop_side[tank_name]
                            demand_loop_side[tank_name] = loop_name

                            if tank.upstream_component is not None:
                                assert (tank.upstream_component in
                                        branch.components.pumps or branch.components.heat_exchangers
                                        ), (f"Upstream component {tank.upstream_component} "
                                            f"is not in the {branch_name} components")

            for loop_name, secondary_chilled_water_loop in secondary_chilled_water_loops.items():
                for branch_name, branch in secondary_chilled_water_loop.supply_branches.items():
                    if branch.components.tanks:
                        for tank_name, tank in branch.components.tanks.items():
                            if tank_name in tank_demand_side_uids:
                                tank.other_loop_side = demand_loop_side[tank_name]

            tank_uids_missing_in_chilled = tank_supply_side_uids - tank_demand_side_uids
            tank_uids_missing_in_secondary_chilled = tank_demand_side_uids - tank_supply_side_uids

            if tank_uids_missing_in_chilled:
                raise ValueError(
                    f"Chiller {tank_uids_missing_in_chilled} is missing in chiller water loop demand branches.")

            if tank_uids_missing_in_secondary_chilled:
                raise ValueError(
                    f"Chiller {tank_uids_missing_in_secondary_chilled} is missing in secondary chilled water loop supply branches.")

    @classmethod
    def _validate_hx_connection(
        cls,
        chilled_water_loops: OrderedDict[str, ChilledWaterLoops],
        condenser_water_loops: OrderedDict[str, CondenserWaterLoops]
    ) -> None:
        """
        Validate the connection of fluid-to-fluid heat exchanger (HX) between the chilled water loop and
        the condenser water loop. To validate the connection, we validate the following:
        1. HX uid is unique in each loop
        2. HX uid in chilled water loop supply branches is the same as the one in condenser water loop demand branches
        """

        hx_supply_side_uids = set()
        hx_demand_side_uids = set()
        hx_dict = dict()

        supply_loop_side = dict()
        demand_loop_side = dict()

        for loop_name, chilled_water_loop in chilled_water_loops.items():
            for branch_name, branch in chilled_water_loop.supply_branches.items():
                if branch.components.heat_exchangers:
                    for hx_name, hx in branch.components.heat_exchangers.items():
                        if hx_name in hx_supply_side_uids:
                            raise ValueError(
                                f"Heat Exchanger {hx_name} is not unique in chilled water loop supply branches.")
                        hx_supply_side_uids.add(hx_name)
                        hx_dict[hx_name] = hx
                        supply_loop_side[hx_name] = loop_name

                        if hx.upstream_component is not None:
                            assert (hx.upstream_component in
                                    branch.components.pumps or branch.components.heat_exchangers
                                    ), (f"Upstream component {hx.upstream_component} "
                                        f"is not in the {branch_name} components")

        if condenser_water_loops is not None:
            for loop_name, condenser_water_loop in condenser_water_loops.items():
                for branch_name, branch in condenser_water_loop.demand_branches.items():
                    if branch.components.heat_exchangers:
                        for hx_name, hx in branch.components.heat_exchangers.items():
                            if hx_name in hx_demand_side_uids:
                                raise ValueError(
                                    f"Heat Exchanger {hx_name} is not unique in condenser water loop demand branches.")
                            # relocate the same hx to the demand branch
                            if hx_name in hx_dict.keys():
                                branch.components.heat_exchangers[hx_name].geometry = hx_dict[hx_name].geometry
                                branch.components.heat_exchangers[hx_name].cooling = hx_dict[hx_name].cooling
                                branch.components.heat_exchangers[hx_name].model = hx_dict[hx_name].model
                                branch.components.heat_exchangers[hx_name].meta = hx_dict[hx_name].meta
                            hx_demand_side_uids.add(hx_name)
                            hx.other_loop_side = supply_loop_side[hx_name]
                            demand_loop_side[hx_name] = loop_name

                            if hx.upstream_component is not None:
                                assert (hx.upstream_component in
                                        branch.components.pumps or branch.components.heat_exchangers
                                        ), (f"Upstream component {hx.upstream_component} "
                                            f"is not in the {branch_name} components")

        for loop_name, chilled_water_loop in chilled_water_loops.items():
            for branch_name, branch in chilled_water_loop.supply_branches.items():
                if branch.components.heat_exchangers:
                    for hx_name, hx in branch.components.heat_exchangers.items():
                        if hx_name in hx_supply_side_uids:
                            hx.other_loop_side = demand_loop_side[hx_name]

        hx_uids_missing_in_condenser = hx_supply_side_uids - hx_demand_side_uids
        hx_uids_missing_in_chilled = hx_demand_side_uids - hx_supply_side_uids

        if hx_uids_missing_in_condenser:
            raise ValueError(
                f"Heat Exchanger {hx_uids_missing_in_condenser} is missing in condenser water loop demand branches.")
        if hx_uids_missing_in_chilled:
            raise ValueError(
                f"Heat Exchanger {hx_uids_missing_in_chilled} is missing in chilled water loop supply branches.")

    @classmethod
    def _validate_acu_connection(
        cls,
        secondary_water_loops: OrderedDict[str, SecondaryChilledWaterLoops],
        chilled_water_loops: OrderedDict[str, ChilledWaterLoops],
        zones: OrderedDict[str, Room],
    ) -> None:
        """
        Validate the connection of acus between the chilled water loop and the zones. To validate the connection,
        we validate the following:
        1. ACU uid is unique in each chilled water loop
        2. ACU uid in chilled water loop demand branches is the same as the one in the zones
        """
        acu_uids_in_chilled_water_loops = set()
        acu_uids_in_zones = set()
        acu_dict = dict()

        for zone_name, zone in zones.items():
            for acu_id, acu in zone.constructions.acus.items():
                if acu_id in acu_uids_in_zones:
                    raise ValueError(f"ACU {acu_id} is not unique in zones.")
                acu_uids_in_zones.add(acu_id)
                acu_dict[acu_id] = acu

        if secondary_water_loops is not None:
            for loop_name, secondary_water_loop in secondary_water_loops.items():
                for branch_name, branch in secondary_water_loop.demand_branches.items():
                    if branch.components.acu:
                        for acu_id, acu in branch.components.acu.items():
                            if acu_id in acu_uids_in_chilled_water_loops:
                                raise ValueError(f"ACU {acu_id} is not unique in secondary water loop demand branches.")
                            acu_uids_in_chilled_water_loops.add(acu_id)
                            # relocate the same acu to the demand branch
                            if acu_id in acu_dict.keys():
                                branch.components.acu[acu_id].geometry = acu_dict[acu_id].geometry
                                branch.components.acu[acu_id].cooling = acu_dict[acu_id].cooling
                                branch.components.acu[acu_id].power = acu_dict[acu_id].power
                                branch.components.acu[acu_id].controller = acu_dict[acu_id].controller
                                branch.components.acu[acu_id].model = acu_dict[acu_id].model
                                branch.components.acu[acu_id].meta = acu_dict[acu_id].meta

        else:
            for loop_name, chilled_water_loop in chilled_water_loops.items():
                for branch_name, branch in chilled_water_loop.demand_branches.items():
                    if branch.components.acu:
                        for acu_id, acu in branch.components.acu.items():
                            if acu_id in acu_uids_in_chilled_water_loops:
                                raise ValueError(f"ACU {acu_id} is not unique in chilled water loop demand branches.")
                            acu_uids_in_chilled_water_loops.add(acu_id)
                            # relocate the same acu to the demand branch
                            if acu_id in acu_dict.keys():
                                branch.components.acu[acu_id].geometry = acu_dict[acu_id].geometry
                                branch.components.acu[acu_id].cooling = acu_dict[acu_id].cooling
                                branch.components.acu[acu_id].power = acu_dict[acu_id].power
                                branch.components.acu[acu_id].controller = acu_dict[acu_id].controller
                                branch.components.acu[acu_id].model = acu_dict[acu_id].model
                                branch.components.acu[acu_id].meta = acu_dict[acu_id].meta

        acu_uids_missing_in_zones = acu_uids_in_chilled_water_loops - acu_uids_in_zones

        acu_uids_missing_in_chilled_water_loops = acu_uids_in_zones - acu_uids_in_chilled_water_loops

        if acu_uids_missing_in_zones:
            raise ValueError(
                f"ACU {acu_uids_missing_in_zones} is missing in zones."
            )
        if acu_uids_missing_in_chilled_water_loops:
            raise ValueError(
                f"ACU {acu_uids_missing_in_chilled_water_loops} "
                f"is missing in chilled water loop demand branches."
            )

    @classmethod
    def _validate_supply_branches(cls, supply_branches: OrderedDict, models: Models) -> None:
        """
        Validate the supply branches of the loop. To validate each branch, we validate the following:
        1. The chillers
        2. The pumps
        3. The cooling towers
        4. The tanks
        """
        cls._validate_branches_sequences(supply_branches)
        for supply_branch_name, supply_branch in supply_branches.items():
            supply_branch.uid = supply_branch_name if supply_branch.uid == "" else supply_branch.uid
            ChillerValidator.validate(
                chillers=supply_branch.components.chillers,
                cooling_models=models.cooling_models.chillers,
                power_models=models.power_models.chillers,
                curve_models=models.curve_models.chillers
            ) if supply_branch.components.chillers else None
            PumpValidator.validate(
                pumps=supply_branch.components.pumps,
                cooling_models=models.cooling_models.pumps,
                power_models=models.power_models.pumps,
                control_models=models.control_models.pumps,
                curve_models=models.curve_models.pumps
            ) if supply_branch.components.pumps else None
            CoolingTowerValidator.validate(
                cooling_towers=supply_branch.components.cooling_towers,
                cooling_models=models.cooling_models.cooling_towers,
                power_models=models.power_models.cooling_towers,
                curve_models=models.curve_models.cooling_towers
            ) if supply_branch.components.cooling_towers else None
            PipeValidator.validate(
                pipes=supply_branch.components.pipes,
            ) if supply_branch.components.pipes else None
            HeatExchangerValidator.validate(
                heat_exchangers=supply_branch.components.heat_exchangers,
                geometry_models=models.geometry_models.heat_exchangers,
                cooling_models=models.cooling_models.heat_exchangers,
            ) if supply_branch.components.heat_exchangers else None
            TankValidator.validate(
                tanks=supply_branch.components.tanks,
                cooling_models=models.cooling_models.tanks,
                control_models=models.control_models.tanks,
            ) if supply_branch.components.tanks else None

    @classmethod
    def _validate_demand_branches(cls, demand_branches: OrderedDict, models: Models) -> None:
        """
        Validate the demand branches of the loop.
        """
        cls._validate_branches_sequences(demand_branches)
        for demand_branch in demand_branches.values():
            ACUValidator.validate(
                acus=demand_branch.components.acu,
                geometry_models=models.geometry_models.acus,
                cooling_models=models.cooling_models.acus,
                power_models=models.power_models.acus,
                control_models=models.control_models.acus,
                curve_models=models.curve_models.acus,
                context="demand_branches",
            ) if demand_branch.components.acu else None
            ChillerValidator.validate(
                chillers=demand_branch.components.chillers,
                cooling_models=models.cooling_models.chillers,
                power_models=models.power_models.chillers,
                curve_models=models.curve_models.chillers
            ) if demand_branch.components.chillers else None
            PumpValidator.validate(
                pumps=demand_branch.components.pumps,
                cooling_models=models.cooling_models.pumps,
                power_models=models.power_models.pumps,
                control_models=models.control_models.pumps,
                curve_models=models.curve_models.pumps,
            ) if demand_branch.components.pumps else None
            PipeValidator.validate(
                pipes=demand_branch.components.pipes
            ) if demand_branch.components.pipes else None
            HeatExchangerValidator.validate(
                heat_exchangers=demand_branch.components.heat_exchangers,
                geometry_models=models.geometry_models.heat_exchangers,
                cooling_models=models.cooling_models.heat_exchangers,
            ) if demand_branch.components.heat_exchangers else None
            TankValidator.validate(
                tanks=demand_branch.components.tanks,
                cooling_models=models.cooling_models.tanks,
                control_models=models.control_models.tanks,
            ) if demand_branch.components.tanks else None

    @classmethod
    def _validate_sizing(cls, sizing_plant: SizingPlant) -> None:
        """ Validate the sizing of the loop """
        pass

    @classmethod
    def _validate_branches_sequences(cls, supply_branches: OrderedDict) -> None:
        """ Validate the first and last branches in the supplyBranches dictionary """
        if not supply_branches:
            raise ValueError("supplyBranches cannot be empty")

        # Get the first and last branch names and their details
        first_branch = next(iter(supply_branches.values()))
        last_branch = next(reversed(supply_branches.values()))

        # Check if the first branch is an inlet branch
        if first_branch.side != "inlet":
            raise ValueError(f"The first supply branch must be an inlet branch")

        # Check if the last branch is an outlet branch
        if last_branch.side != "outlet":
            raise ValueError(f"The last supply branch must be an outlet branch")

    @classmethod
    def _validate_meta_loop(cls, meta_plant: MetaLoop) -> None:
        """ Validate the meta of the loop """
        cls._validate_load_distribution_scheme(meta_plant.load_distribution_scheme)
        cls._validate_demand_calculation_scheme(meta_plant.plant_loop_demand_calculation_scheme)
        cls._validate_common_pipe_simulation(meta_plant.common_pipe_simulation)
        cls._validate_setpoint_manager(meta_plant.setpoint_manager)

    @staticmethod
    def _validate_load_distribution_scheme(v) -> str:
        """
        Validate the load distribution scheme of the plant loop. The load distribution scheme can be one of the
        following:
        1. Optimal
        2. SequentialLoad
        3. UniformLoad
        4. UniformPLR
        5. SequentialUniformPLR
        :param v:
        :return:
        """
        scheme = ["Optimal", "SequentialLoad", "UniformLoad", "UniformPLR", "SequentialUniformPLR"]
        if v not in scheme:
            raise ValueError("Plant Load Distribution Scheme must be in {}".format(scheme))
        return v

    @staticmethod
    def _validate_demand_calculation_scheme(v) -> str:
        """
        Validate the demand calculation scheme of the plant loop. The demand calculation scheme can be one of the
        following:
        1. SingleSetpoint
        2. DualSetpointDeadband
        :param v:
        :return:
        """
        scheme = ["SingleSetpoint", "DualSetpointDeadband"]
        if v not in scheme:
            raise ValueError("Plant Loop Demand Calculation Scheme must be in {}".format(scheme))
        return v

    @staticmethod
    def _validate_common_pipe_simulation(v) -> str:
        """
        Validate the common pipe simulation of the plant loop. The common pipe simulation can be one of the following:
        1. CommonPipe
        2. TwoWayCommonPipe
        3. None
        :param v:
        :return:
        """
        pipe_simulation = ["CommonPipe", "TwoWayCommonPipe", "None"]
        if v not in pipe_simulation:
            raise ValueError("Plant Common Pipe Simulation must be in {}".format(pipe_simulation))
        return v

    @staticmethod
    def _validate_setpoint_manager(v) -> bool:
        """
        Validate the setpoint manager of the plant loop
        :param v:
        :return:
        """
        if v not in [True, False]:
            raise ValueError("Plant Setpoint Manager must be either True or False")
        return v
