from typing import OrderedDict, Literal
import re

from ..server import Server

from dclib.models.composite import Models
from dclib.models.cooling.server import ServerCoolingModel
from dclib.models.computing.server import ServerComputingModel
from dclib.models.geometry.server import ServerGeometryModel
from dclib.models.power.server import ServerPowerModel

from dclib.data import ServerInputs

from dclib.validators.utils import validate_model_block


class ServerValidator:
    """A class to validate zone-related aspects of a building"""

    @classmethod
    def validate(
        cls,
        servers: OrderedDict[str, Server],
        models: Models,
        inputs: OrderedDict[str, ServerInputs],
        context: Literal["rack", "demand_branches"] = "rack",
    ) -> None:
        """
        Validates and processes server configurations.

        Args:
            servers (OrderedDict[str, Server]): Dictionary of servers to validate
            models (Models): Collection of model definitions for different aspects
            inputs (OrderedDict[str, ServerInputs]): Server input configurations
            context (Literal["rack", "demand_branches"], optional): Validation context.
                Defaults to "rack".

        Raises:
            ValueError: If inputs are not defined for a server
        """
        changes = []

        for server_id, server in servers.items():
            # Set initial UID
            server.uid = server_id if server.uid == "" else server.uid
            server.valid_id = re.sub(r"[^a-zA-Z0-9_]", "_", server.uid)

            if server_id != server.uid:
                changes.append((server_id, server.uid))

            # --- Geometry Model ---
            is_geometry_required = context == "rack"

            if validate_model_block(
                component=server,
                model_type="geometry",
                model_definitions=(models.geometry_models.servers if models.geometry_models else None),
                uid=server.uid,
                required=is_geometry_required,
            ):
                cls._validate_geometry_models(server, models.geometry_models.servers)

            # --- Cooling Model---
            if validate_model_block(
                component=server,
                model_type="cooling",
                model_definitions=(models.cooling_models.servers if models.cooling_models else None),
                uid=server.uid,
                required=True,
            ):
                cls._validate_cooling_models(server, models.cooling_models.servers)

            # --- Power Model---
            if validate_model_block(
                component=server,
                model_type="power",
                model_definitions=(models.power_models.servers if models.power_models else None),
                uid=server.uid,
                required=False,
            ):
                cls._validate_power_models(server, models.power_models.servers, inputs)

            # --- Computing Model---
            if validate_model_block(
                component=server,
                model_type="computing",
                model_definitions=(models.computing_models.servers if models.computing_models else None),
                uid=server.uid,
                required=False,
            ):
                cls._validate_computing_models(server, models.computing_models.servers)

            # --- Inputs ---
            if inputs is not None:
                cls._validate_inputs(server, inputs)
            else:
                raise ValueError(f"Inputs not defined for server '{server.uid}'.")

        for old_id, new_id in changes:
            servers[new_id] = servers.pop(old_id)

    @classmethod
    def _validate_geometry_models(cls, server: Server, models: OrderedDict[str, ServerGeometryModel]) -> None:
        model_name = server.geometry.model

        model = models.get(model_name)

        server.geometry.slot_occupation = model.slot_occupation
        server.geometry.width = model.width
        server.geometry.depth = model.depth
        server.geometry.inlet_face = model.inlet_face
        server.geometry.outlet_face = model.outlet_face

    @classmethod
    def _validate_power_models(
        cls,
        server: Server,
        models: OrderedDict[str, ServerPowerModel],
        inputs: OrderedDict[str, ServerInputs] | None,
    ) -> None:
        model_name = server.power.model

        model = models.get(model_name)

        if model.rated_power is None:
            raise ValueError(f"Rated power is not defined for power model '{model_name}'.")
        server.power.rated_power = model.rated_power
        server.power.rated_cpu_power = model.rated_cpu_power
        server.power.rated_mem_power = model.rated_mem_power
        server.power.rated_disk_power = model.rated_disk_power

        server_input = inputs.get(server.uid)
        if server_input and hasattr(server_input, "input_power_percentage"):
            input_power_percentage = server_input.input_power_percentage
            if input_power_percentage:
                server_input.input_power = server.power.rated_power * input_power_percentage * 0.01

    @classmethod
    def _validate_computing_models(cls, server: Server, models: OrderedDict[str, ServerComputingModel]) -> None:
        model_name = server.computing.model
        model = models.get(model_name)

        server.computing.cpu_capacity = model.cpu_capacity
        server.computing.mem_capacity = model.mem_capacity
        server.computing.disk_capacity = model.disk_capacity
        server.computing.remaining_cpu = model.cpu_capacity
        server.computing.remaining_mem = model.mem_capacity
        server.computing.remaining_disk = model.disk_capacity
        server.computing.task_instances = {}

    @classmethod
    def _validate_cooling_models(cls, server: Server, models: OrderedDict[str, ServerCoolingModel]) -> None:
        model_name = server.cooling.model

        model = models.get(model_name)

        server.cooling.fan_type = model.fan_type
        server.cooling.volume_flow_rate = model.volume_flow_rate
        server.cooling.volume_flow_rate_ratio = model.volume_flow_rate_ratio
        server.cooling.liquid_cooling = model.liquid_cooling
        server.cooling.liquid_cooling_percentage = model.liquid_cooling_percentage
        server.cooling.pipe_diameter = model.pipe_diameter

    @classmethod
    def _validate_inputs(cls, server: Server, inputs: OrderedDict[str, ServerInputs]) -> None:
        input = inputs.get(server.uid)
        if isinstance(server, Server) and input:
            server.power.input_power = input.input_power
        else:
            raise ValueError(f"Inputs not found for server '{server.uid}'.")
