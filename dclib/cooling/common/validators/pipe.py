from typing import OrderedDict

from dclib.cooling.common.pipe import Pipe


class PipeValidator:
    """ A class to validate pipes of a building """
    @classmethod
    def validate(
        cls,
        pipes: OrderedDict[str, Pipe],
    ) -> None:
        for pipe_name, pipe in pipes.items():
            pipe.uid = pipe_name if pipe.uid == "" else pipe.uid
