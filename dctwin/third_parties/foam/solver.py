"""
form.k.value = 1.5 * Math.pow(form.Tu.value/100,2) * Math.pow(form.u_freestream.value,2)
form.epsilon.value = 0.09 * Math.pow(form.k.value,1.5) / form.Tu_L.value
form.omega.value = form.epsilon.value / (0.09 * form.k.value)
"""
import abc
import os
import shutil
import time
from pathlib import Path
from typing import Tuple, Dict

from loguru import logger

from dclib.room import Room

from dctwin.third_parties.foam.boundary import ACUBoundary, RoomBoundary, ServerBoundary

from dctwin.third_parties.foam.utils import generate_control_dict, read_internal_field
from dctwin.utils import template_env, config


class Builder:
    """
    A class to render the templates of the foam configuration file

    :param room: the room to be simulated
    :param last_state_case: the last state of the case, if it is None,
        the state will be reset to the initial state
    """

    def __init__(self, room: Room, last_state_case=None) -> None:
        self.room = room
        self.room_dz = room.geometry.height
        self.acu_dict = room.constructions.acus
        server_dict = {}
        for rack in room.constructions.racks.values():
            for server_key, server in rack.constructions.servers.items():
                server_dict[server_key] = server
        for row in room.constructions.rows.values():
            for rack in row.constructions.racks.values():
                for server_key, server in rack.constructions.servers.items():
                    server_dict[server_key] = server
        self.server_dict = server_dict
        self.last_state_case = last_state_case

    def run(self) -> None:
        self.render("alphat", "alphat")
        self.render("epsilon", "epsilon")
        self.render("nut", "nut")
        self.render("k", "k")
        self.render("p", "p")
        self.render("p_rgh", "p_rgh")

        if self.last_state_case is not None:
            self.render(
                "T", "T", "".join(read_internal_field(Path(self.last_state_case, "T")))
            )
            self.render(
                "U",
                "U",
                "".join(read_internal_field(Path(self.last_state_case, "U"))),
            )
        else:
            self.render("T", "T")
            self.render("U", "U")

    @classmethod
    def get_k_and_epsilon(cls, obj_dict: Dict) -> Tuple[float, float]:
        """Get the minimum value greater than 0"""
        _obj_list = [acu for acu in obj_dict.values() if acu.k != 0]
        if len(_obj_list) == 0:
            raise ValueError("Please check the ACU or Server flow rate value")
        obj = min(_obj_list, key=lambda x: x.k)
        return obj.k, obj.epsilon

    def render(self, source_filename, write_filename, internal_field=None) -> None:
        acu_k, acu_epsilon = self.get_k_and_epsilon(self.acu_dict)
        server_k, server_epsilon = self.get_k_and_epsilon(self.server_dict)
        init_temperature = 24 + 273.15
        with open(Path(config.cfd.case_dir, f"0/{write_filename}"), "w") as f:
            f.write(
                template_env.get_template(f"foam/template/0/{source_filename}.j2").render(
                    init_temperature=init_temperature,
                    p_rgh=round(self.room_dz * 9.81, 10),
                    acu_boundaries=[
                        ACUBoundary(acu, init_temperature=init_temperature)
                        for acu in self.acu_dict.values()
                        ],
                    server_boundaries=[
                        ServerBoundary(server, init_temperature=init_temperature) 
                        for server in self.server_dict.values()
                        ],
                    room_boundary=RoomBoundary(self.room),
                    acu_k=acu_k,
                    acu_epsilon=acu_epsilon,
                    server_k=server_k,
                    server_epsilon=server_epsilon,
                    internal_field=internal_field,
                )
            )


