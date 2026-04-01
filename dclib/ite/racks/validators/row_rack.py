from typing import Dict
import copy
from collections import OrderedDict

from dclib.data import ServerInputs
from dclib.models.composite import Models
from dclib.ite.racks.validators import RackValidator


class RowValidator:

    @classmethod
    def validate(
            cls,
            rows: Dict,
            models: Models,
            inputs: OrderedDict[str, ServerInputs]
    ) -> None:
        """
        Validate the geometry of rows and constructions for each row.

        :param rows: Row objects in a data hall.
        :param models: Models for geometry, power, computing, and cooling.
        :param inputs: Inputs for server models.
        """
        for row_id, row in rows.items():
            row.uid = row_id if row.uid == "" else row.uid

            RackValidator.validate(
                racks=row.constructions.racks,
                models=models,
                inputs=inputs
            )