from typing import OrderedDict

from ..wall import Surface

from dclib.models.composite import Models


class WallValidator:
    """ A class to validate wall of a room """

    @classmethod
    def validate(
        cls,
        walls: OrderedDict[str, Surface],
        models: Models,
    ) -> None:
        # TODO: validate wall
        pass
