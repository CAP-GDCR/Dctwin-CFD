from typing import OrderedDict

from dclib.cooling.room.validators import CDUValidator

from dclib.cooling.room.liquid_loops import LiquidFlowLoops
from dclib.data import Inputs
from dclib.ite.servers.validators import ServerValidator
from dclib.models import Models


class LiquidFlowLoopsValidator:
    """Validator for liquid flow loops."""
    @classmethod
    def validate(
        cls,
        loops: OrderedDict[str, LiquidFlowLoops],
        models: Models,
        inputs: Inputs,
    ) -> None:
        if loops is not None:
            for loop_name, loop in loops.items():
                loop.uid = loop_name if loop.uid == "" else loop.uid
                cls._validate_supply_branches(loop.supply_branches, models)
                cls._validate_demand_branches(loop.demand_branches, models, inputs)

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
            CDUValidator.validate(
                cdus=supply_branch.components.cdus,
                models=models,
            ) if supply_branch.components.cdus else None

    @classmethod
    def _validate_demand_branches(cls, demand_branches: OrderedDict, models: Models, inputs: Inputs) -> None:
        """
        Validate the demand branches of the loop.
        """
        cls._validate_branches_sequences(demand_branches)
        for demand_branch in demand_branches.values():
            ServerValidator.validate(
                servers=demand_branch.components.servers,
                models=models,
                inputs=inputs.servers,
                context="demand_branches",
            ) if demand_branch.components.servers else None
