from collections import OrderedDict
from typing import Dict, Union
import re

from dclib.ite.servers.server import Server
from dclib.cooling.room.facilities.acu import ACU
from dclib.construction.entities.box import Box
from dclib.ite.racks.rack import Rack, RackConstruction

from dclib.ite.servers.validators import ServerValidator

from dclib.models.composite import Models
from dclib.data import ServerInputs

from dclib.validators.utils import validate_model_block


class RackValidator:
    """A class to validate zone-related aspects of a building."""

    @classmethod
    def validate(cls, racks: Dict, models: Models, inputs: OrderedDict[str, ServerInputs]) -> None:
        """
        Validate the geometry of racks and constructions for each rack.

        :param racks: Rack objects in a data hall.
        :param models: Models for geometry, power, computing, and cooling.
        :param inputs: Inputs for server models.
        """
        all_servers, invalid_occupation = {}, {}
        changes = []  # Used to record keys that need to be changed

        for rack_id, rack in racks.items():
            # Set initial UID
            rack.uid = rack_id if rack.uid == "" else rack.uid

            # Replace illegal characters using regular expression
            valid_uid = re.sub(r"[^a-zA-Z0-9_]", "_", rack.uid)

            rack.valid_id = valid_uid

            if rack_id != rack.uid:
                changes.append((rack_id, rack.uid))

            # --- Geometry Model ---
            if validate_model_block(
                component=rack,
                model_type="geometry",
                model_definitions=(models.geometry_models.racks if models.geometry_models else None),
                uid=rack.uid,
                required=True,
            ):
                cls._validate_geometry_models(rack, models.geometry_models.racks)

            # --- Rack Constructions ---
            if rack.constructions:
                cls._validate_rack_constructions(rack.constructions, models=models, inputs=inputs)
            else:
                raise ValueError(f"Rack constructions are missing for rack'{rack_id}'.")

        if invalid_occupation:
            msg = "Server collision error:"
            for server_a, server_b in invalid_occupation.items():
                msg += f"\n{server_a} collides with {server_b}"
            raise ValueError(msg)

        for old_id, new_id in changes:
            racks[new_id] = racks.pop(old_id)

    @classmethod
    def _validate_rack_constructions(
        cls,
        rack_constructions: RackConstruction,
        models: Models,
        inputs: OrderedDict[str, ServerInputs],
    ) -> None:
        """
        Validate the geometry of servers and constructions for each server.

        :param rack_constructions: Rack constructions objects in a rack.
        :param models: Models for geometry, power, computing, and cooling.
        :param inputs: Inputs for server models.
        """
        ServerValidator.validate(
            servers=rack_constructions.servers,
            models=models,
            inputs=inputs,
            context="rack",
        )

    @classmethod
    def _validate_server_occupation(
        cls,
        rack: Rack,
        server: Server,
        server_id: str,
        occupied_rack_slot: Dict,
        invalid_occupation: Dict,
    ) -> None:
        """
        Validate the occupation of a server in a rack. If the server is valid, update the occupied rack slot.
        Otherwise, raise an error.

        :param rack: Rack object to be validated.
        :param server: Server object to be validated.
        :param server_id: Name of the server.
        :param occupied_rack_slot: Rack slot that is occupied by a server.
        :param invalid_occupation: Indicators to identify the invalid occupation.
        """
        server.geometry.orientation = rack.geometry.orientation
        slot_position = int(server.geometry.slot_position)
        slot_occupation = int(server.geometry.slot_occupation)
        num_slots = int(rack.geometry.slot)

        if slot_position < 1 or slot_occupation + slot_position > num_slots + 1:
            raise ValueError(
                f"Invalid server slot/occupation: Server({server_id}, slot={slot_position}, occupation={slot_occupation})"
            )

        for i in range(slot_position, slot_position + slot_occupation):
            if i not in occupied_rack_slot:
                occupied_rack_slot[i] = server_id
            else:
                invalid_occupation[server_id] = occupied_rack_slot[i]

    @classmethod
    def _validate_geometry_models(cls, obj: Union[ACU, Server, Rack, Box], models: Dict) -> None:
        """
        Validate the geometry of a server, rack, or box.

        :param obj: The object to validate.
        :param models: The dictionary of models.
        """
        model_name = obj.geometry.model

        model = models.get(model_name)

        obj.geometry.size = model.size
        obj.geometry.slot = model.slot
        obj.geometry.first_slot_offset = model.first_slot_offset
        obj.geometry.faces = model.faces
