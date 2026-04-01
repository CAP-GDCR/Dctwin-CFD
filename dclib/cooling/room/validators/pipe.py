from typing import OrderedDict

from dclib.models.composite import Models
from dclib.cooling.room.facilities import Pipe


class PipeValidator:
    """ A class to validate pipes of a building """
    @classmethod
    def validate(
        cls,
        pipes: OrderedDict[str, Pipe],
        models: Models,
    ) -> None:
        for pipe_name, pipe in pipes.items():
            pipe.uid = pipe_name if pipe.uid == "" else pipe.uid
            geometry_model = models.geometry_models.pipes.get(pipe.geometry.model, None)
            """Fill in geometry model parameters"""
            if geometry_model is not None:
                pipe.geometry.radius = geometry_model.radius
                pipe.geometry.pipe_diameter = geometry_model.pipe_diameter
                pipe.geometry.pipe_length = geometry_model.pipe_length
                pipe.geometry.channel_type = geometry_model.channel_type
                pipe.geometry.turning_radius = geometry_model.turning_radius
                pipe.geometry.height_difference = geometry_model.height_difference
                pipe.geometry.roughness = geometry_model.roughness
                pipe.geometry.fluid_name = geometry_model.fluid_name
