""" Room object
"""
import json
import numpy as np
from typing import Optional, OrderedDict, List, Tuple
import re

from loguru import logger
from pydantic import Field, field_validator
from pathlib import Path

from pydantic_core.core_schema import ValidationInfo

from dclib.ite.servers.server import Server
from dclib.ite.racks import Rack, Row

from dclib.construction.surfaces import Surface, Panel
from dclib.construction.entities import Box
from dclib.ite.composite import ITE
from dclib.electrical.room import Light, ElectricEquipment, People
from dclib.models.geometry.basics import Vertex, Face
from dclib.models.geometry.acu import ACUFace
from dclib.cooling.room.facilities import ACU
from dclib.cooling.room.facilities import Dehumidifier
from dclib.cooling.room.facilities import Sensor
from dclib.cooling.room.liquid_loops import LiquidFlowLoops
from dclib.cooling.room.validators.liquid_loops import LiquidFlowLoopsValidator


from dclib.cooling.room.sizing import Sizing

from dclib.data import Inputs
from dclib.models.geometry.utils import rotate, euclidean_distance

from dclib.construction.entities.validators import BoxValidator
from dclib.ite.racks.validators import RackValidator, RowValidator
from dclib.cooling.room.validators import ACUValidator
from dclib.cooling.room.validators import DehumidifierValidator
from dclib.cooling.room.validators import SensorValidator
from dclib.ite.composite import ITEValidator
from dclib.electrical.room.validators import LightValidator, ElectricEquipmentValidator, PeopleValidator

from dclib.models.base import BaseModel
from dclib.models.composite import Models


class HeatGains(BaseModel):
    ites: OrderedDict[str, ITE] = None
    light: Optional[Light] = None
    electric_equipment: Optional[ElectricEquipment] = None
    people: Optional[People] = None


class Thermostats(BaseModel):
    cooling_setpoint: float
    heating_setpoint: Optional[float] = 15


class Humidistats(BaseModel):
    humidifying_setpoint: Optional[float]
    dehumidifying_setpoint: Optional[float] = None


class RoomStateControl(BaseModel):
    thermostats: Optional[Thermostats] = None
    humidistats: Optional[Humidistats] = None


class RoomGeometry(BaseModel):
    height: float
    plane: List[Vertex]
    tile_size: float = 0.6

    @property
    def area(self):
        area = 0.0
        n = len(self.plane)  # Number of coordinates/points

        for i in range(n):
            j = (i + 1) % n
            area += self.plane[i].x * self.plane[j].y
            area -= self.plane[j].x * self.plane[i].y

        area = abs(area) / 2.0
        return area

    @property
    def volume(self):
        return self.area * self.height


class RoomConstruction(BaseModel):
    """ RoomConstruction is used to build the objects in a room
    """
    raised_floor: Optional[Panel] = None
    false_ceiling: Optional[Panel] = None
    boxes: Optional[OrderedDict[str, Box]] = None
    surfaces: Optional[OrderedDict[str, Surface]] = None
    heat_gains: Optional[HeatGains] = None
    racks: Optional[OrderedDict[str, Rack]] = {}
    rows: Optional[OrderedDict[str,Row]] = {}
    servers: Optional[OrderedDict[str, Server]] = {}  # only for the cases without rack
    acus: Optional[OrderedDict[str, ACU]] = {}
    dehumidifiers: Optional[OrderedDict[str, Dehumidifier]] = {}
    sensors: Optional[OrderedDict[str, Sensor]] = {}
    liquid_flow_networks: Optional[OrderedDict[str, LiquidFlowLoops]] = {}

    @property
    def check_sealed(self):
        sealed = True
        for _, rack in self.racks.items():
            if not rack.geometry.hasBlankingPanel:
                sealed = False
        return sealed
    
    @property
    def num_acu(self) -> int:
        return len(self.acus.items())

    @property
    def num_rack(self) -> int:
        return len(self.racks.items())

    @property
    def num_ser(self) -> int:
        count = 0
        if self.racks is not None:
            for _, rack in self.racks.items():
                for _ in rack.constructions.servers.values():
                    count += 1
        elif self.servers is not None:
            for _ in self.servers.values():
                count += 1
        return count

    @property
    def num_sen(self) -> int:
        return len(self.sensors.items())

    @property
    def acu_keys(self) -> List[str]:
        return list(self.acus.keys()) if self.acus is not None else []

    @property
    def dehumidifier_keys(self) -> List[str]:
        return list(self.dehumidifiers.keys()) if self.dehumidifiers is not None else []

    @property
    def rack_keys(self) -> List[str]:
        return list(self.racks.keys()) if self.racks is not None else []

    @property
    def server_keys(self) -> List[str]:
        server_key_list = []
        for _, rack in self.racks.items():
            for server_key, _ in rack.constructions.servers.items():
                server_key_list.append(server_key)
        return server_key_list

    @property
    def cdu_keys(self) -> List[str]:
        cdus = []
        for loop in self.liquid_flow_networks.values():
            for branch in loop.supply_branches.values():
                if branch.components.cdus:
                    for cdu_name, cdu in branch.components.cdus.items():
                        cdus.append(cdu.uid)
        return cdus

    @property
    def sensor_keys(self) -> List[str]:
        return list(self.sensors.keys()) if self.sensors is not None else []

    @property
    def acu2sen(self) -> np.ndarray:
        """ Calculate the spatial distance matrix of the ACU inlet to sensor connections
        """
        acu2sen_dis = np.zeros([self.num_acu, self.num_sen])
        for acu_idx, (acu_id, acu) in enumerate(self.acus.items()):
            for sen_idx, (sen_id, sen) in enumerate(self.sensors.items()):
                acu_return, acu_supply, acu_center = self.acu_patch_positions(acu_id)
                sen_loc = sen.geometry.location
                acu2sen_dis[acu_idx][sen_idx] = euclidean_distance(
                    loc_1=(acu_center.x, acu_center.y, acu_center.z),
                    loc_2=(sen_loc.x, sen_loc.y, sen_loc.z),
                )
        return acu2sen_dis

    @property
    def acu2ser(self) -> np.ndarray:
        """ Calculate the spatial distance matrix of the ACU inlet to server connections
        """
        acu2ser_dis = np.zeros([self.num_acu, self.num_ser])
        for acu_idx, (acu_id, acu) in enumerate(self.acus.items()):
            ser_idx: int = 0
            for rack_id, rack in self.racks.items():
                for server_id, ser in rack.constructions.servers.items():
                    acu_return, acu_supply, acu_center = self.acu_patch_positions(acu_id)
                    ser_inlet, ser_outlet, ser_center = self.server_patch_positions(server_id)
                    acu2ser_dis[acu_idx][ser_idx] = euclidean_distance(
                        loc_1=(acu_center.x, acu_center.y, acu_center.z),
                        loc_2=(ser_center.x, ser_center.y, ser_center.z),
                    )
                    ser_idx += 1
        return acu2ser_dis

    @property
    def acu2rack(self) -> np.ndarray:
        """ Calculate the spatial distance matrix of the ACU inlet to rack
        """
        acu2rack_dis = np.zeros([self.num_acu, self.num_rack])
        for acu_idx, (acu_id, acu) in enumerate(self.acus.items()):
            rack_idx: int = 0
            for rack_id, rack in self.racks.items():
                acu_return, acu_supply, acu_center = self.acu_patch_positions(acu_id)
                rack_inlet, rack_outlet, rack_center = self.rack_patch_positions(rack_id)
                acu2rack_dis[acu_idx][rack_idx] = euclidean_distance(
                    loc_1=(acu_center.x, acu_center.y, acu_center.z),
                    loc_2=(rack_center.x, rack_center.y, rack_center.z),
                )
                rack_idx += 1
        return acu2rack_dis

    @property
    def rack2acu(self) -> np.ndarray:
        """ Calculate the spatial distance matrix of the ACU inlet to rack
        """
        rack2acu_dis = np.zeros([self.num_rack, self.num_acu])
        for rack_idx, (rack_id, rack) in enumerate(self.racks.items()):
            acu_idx: int = 0
            for acu_id, acu in self.acus.items():
                rack_inlet, rack_outlet, rack_center = self.rack_patch_positions(rack_id)
                acu_return, acu_supply, acu_center = self.acu_patch_positions(acu_id)
                rack2acu_dis[rack_idx][acu_idx] = euclidean_distance(
                    loc_1=(rack_center.x, rack_center.y, rack_center.z),
                    loc_2=(acu_center.x, acu_center.y, acu_center.z),
                )
                acu_idx += 1
        return rack2acu_dis

    @property
    def ser2sen(self) -> np.ndarray:
        """ Calculate the spatial distance matrix of the server outlet to sensor connections
        """
        ser2sen_dis = np.zeros([self.num_ser, self.num_sen])
        ser_idx: int = 0
        for rack_id, rack in self.racks.items():
            for server_id, ser in rack.constructions.servers.items():
                for sen_idx, (sen_id, sen) in enumerate(self.sensors.items()):
                    ser_inlet, ser_outlet, ser_center = self.server_patch_positions(server_id)
                    sen_loc = sen.geometry.location
                    ser2sen_dis[ser_idx][sen_idx] = euclidean_distance(
                        loc_1=(ser_center.x, ser_center.y, ser_center.z),
                        loc_2=(sen_loc.x, sen_loc.y, sen_loc.z),
                    )
                ser_idx += 1
        return ser2sen_dis

    def rack_patch_positions(self, rack_id: str) -> Tuple[Vertex, Vertex, Vertex]:
        """Get the coordinate of rack inlet, outlet and center"""
        try:
            rack: Rack = self.racks.get(rack_id)

            z = rack.geometry.location.z + rack.geometry.first_slot_offset
            z = round(z, 3)

            # inlet
            inlet_x = rack.geometry.location.x + rack.geometry.size.x / 2
            inlet_y = rack.geometry.location.y
            inlet_x, inlet_y = rotate(
                (rack.geometry.location.x, rack.geometry.location.y),
                (inlet_x, inlet_y), rack.geometry.orientation
            )
            inlet = Vertex(x=round(inlet_x, 3), y=round(inlet_y, 3), z=z)

            # outlet
            outlet_x = rack.geometry.location.x + rack.geometry.size.x / 2
            outlet_y = rack.geometry.location.y
            outlet_x, outlet_y = rotate(
                (rack.geometry.location.x, rack.geometry.location.y),
                (outlet_x, outlet_y), rack.geometry.orientation
            )
            outlet = Vertex(x=round(outlet_x, 3), y=round(outlet_y, 3), z=z)

            # center
            center_x = rack.geometry.location.x + rack.geometry.size.x / 2
            center_y = rack.geometry.location.y
            center_x, center_y = rotate(
                (rack.geometry.location.x, rack.geometry.location.y),
                (center_x, center_y), rack.geometry.orientation
            )
            center = Vertex(x=round(center_x, 3), y=round(center_y, 3), z=z)
            return inlet, outlet, center

        except ValueError:
            raise ValueError(f"Rack positions not found")

    def acu_patch_positions(self, acu_id: str) -> Tuple[Vertex, Vertex, Vertex]:
        """Get the coordinate of acu return, supply and center"""
        acu: ACU = self.acus.get(acu_id)
        
        def get_center_point_by_face(face: ACUFace, orientation: int, offset: float = 0.01) -> Tuple[float, float, float]:
            if face.side == Face.front:
                x = acu.geometry.location.x + acu.geometry.size.x / 2 + face.offset.x
                y = acu.geometry.location.y
                z = acu.geometry.location.z + acu.geometry.size.z / 2 + face.offset.z
                
                if orientation == 90:
                    x += offset
                elif orientation == 270:
                    x -= offset
                elif orientation == 0:
                    y -= offset
                elif orientation == 180:
                    y += offset
            elif face.side == Face.rear:
                x = acu.geometry.location.x + acu.geometry.size.x / 2 + face.offset.x
                y = acu.geometry.location.y + acu.geometry.size.y
                z = acu.geometry.location.z + acu.geometry.size.z / 2 + face.offset.z

                if orientation == 90:
                    x -= offset
                elif orientation == 270:
                    x += offset
                elif orientation == 0:
                    y += offset
                elif orientation == 180:
                    y -= offset
            elif face.side == Face.left:
                x = acu.geometry.location.x
                y = acu.geometry.location.y + acu.geometry.size.y / 2 - face.offset.x
                z = acu.geometry.location.z + acu.geometry.size.z / 2 + face.offset.z
            elif face.side == Face.right:
                x = acu.geometry.location.x + acu.geometry.size.x
                y = acu.geometry.location.y + acu.geometry.size.y / 2 - face.offset.x
                z = acu.geometry.location.z + acu.geometry.size.z / 2 + face.offset.z
            elif face.side == Face.top:
                x = acu.geometry.location.x + acu.geometry.size.x / 2 + face.offset.x
                y = acu.geometry.location.y + acu.geometry.size.y / 2 + face.offset.y
                z = acu.geometry.location.z + acu.geometry.size.z + offset
            elif face.side == Face.bottom:
                x = acu.geometry.location.x + acu.geometry.size.x / 2 + face.offset.x
                y = acu.geometry.location.y + acu.geometry.size.y / 2 + face.offset.y
                z = acu.geometry.location.z - offset
            else:
                raise ValueError(f"not supported: face.side={face.side}")
            return round(x, 3), round(y, 3), round(z, 3)

        def get_center_coordinate(face: ACUFace = None, orientation: int = 0) -> Vertex:
            if face is not None:
                x, y, z = get_center_point_by_face(face, orientation)
            else:
                x = acu.geometry.location.x + acu.geometry.size.x / 2
                y = acu.geometry.location.y + acu.geometry.size.y / 2
                z = acu.geometry.location.z + acu.geometry.size.z / 2
            x, y = rotate(
                (acu.geometry.location.x, acu.geometry.location.y), (x, y), acu.geometry.orientation
            )
            return Vertex(x=round(x, 3), y=round(y, 3), z=z)

        return_center = get_center_coordinate(acu.geometry.return_face, acu.geometry.orientation)
        supply_center = get_center_coordinate(acu.geometry.supply_face, acu.geometry.orientation)
        center = get_center_coordinate()

        return return_center, supply_center, center

    def server_patch_positions(self, server_id: str) -> Tuple[Vertex, Vertex, Vertex]:
        """Get the coordinate of server inlet, outlet and center"""

        for rack_id, rack in self.racks.items():
            if server_id in rack.constructions.servers:
                server = rack.constructions.servers.get(server_id)
                server_rack = rack
                rack: Rack = server_rack

                z = rack.geometry.location.z + rack.geometry.first_slot_offset + 0.045 * (
                        server.geometry.slot_position + server.geometry.slot_occupation / 2 - 1
                )
                z = round(z, 3)

                # inlet
                inlet_x = rack.geometry.location.x + rack.geometry.size.x / 2
                inlet_y = rack.geometry.location.y
                inlet_x, inlet_y = rotate(
                    (rack.geometry.location.x, rack.geometry.location.y),
                    (inlet_x, inlet_y), rack.geometry.orientation
                )
                inlet = Vertex(x=round(inlet_x, 3), y=round(inlet_y, 3), z=z)

                # outlet
                outlet_x = rack.geometry.location.x + rack.geometry.size.x / 2
                outlet_y = rack.geometry.location.y + server.geometry.depth
                outlet_x, outlet_y = rotate(
                    (rack.geometry.location.x, rack.geometry.location.y),
                    (outlet_x, outlet_y), rack.geometry.orientation
                )
                outlet = Vertex(x=round(outlet_x, 3), y=round(outlet_y, 3), z=z)

                # center
                center_x = rack.geometry.location.x + rack.geometry.size.x / 2
                center_y = rack.geometry.location.y + server.geometry.depth / 2
                center_x, center_y = rotate(
                    (rack.geometry.location.x, rack.geometry.location.y),
                    (center_x, center_y), rack.geometry.orientation
                )
                center = Vertex(x=round(center_x, 3), y=round(center_y, 3), z=z)
                return inlet, outlet, center

        else:
            raise ValueError(f"Server {server_id} not found")

    @property
    def total_server_rated_power(self) -> None | float:
        if self.num_ser == 0:
            return None
        else:
            total_power = 0
            if self.servers is not None:
                for _, server in self.servers.items():
                    total_power += server.power.rated_power
            if self.racks is not None:
                for _, rack in self.racks.items():
                    for _, server in rack.constructions.servers.items():
                        total_power += server.power.rated_power
            return total_power

    @property
    def total_ite_power(self) -> None | float:
        if self.heat_gains is None:
            return None
        else:
            total_power = 0
            for _, ite in self.heat_gains.ites.items():
                total_power += ite.rated_power
            return float(total_power)

    def ite2rack(self, room_name) -> OrderedDict:
        # Use nearest racks to ACU to build ITE heat gain models
        res = OrderedDict()
        if self.num_acu > 0:
            for acu_index, (acu_name, acu) in enumerate(self.acus.items()):
                res[f"{room_name} ite-{acu_index+1}"] = OrderedDict()
                res[f"{room_name} ite-{acu_index+1}"]["racks"] = []
        if self.racks is not None:
            for rack_index, (rack_name, rack) in enumerate(self.racks.items()):
                min_dis_acu_index = np.argmin(self.rack2acu[rack_index])
                res[f"{room_name} ite-{min_dis_acu_index+1}"]["racks"].append(rack_name)
        return res


class RoomMeta(BaseModel):
    """ RoomMeta is used to store the meta information of a room
    """
    name: Optional[str] = ""
    description: Optional[str] = ""
    load_distribution_scheme: Optional[str] = "SequentialLoad"
    is_chat_twin: Optional[bool] = False
    is_completed: Optional[bool] = False
    has_quick_simulation: Optional[bool] = False


class Room(BaseModel):
    """ Room object (Data Hall) of a data center
    """
    uid: Optional[str] = ""
    name: Optional[str] = ""
    inputs: Optional[Inputs] = Field(default_factory=Inputs)
    geometry: RoomGeometry
    meta: Optional[RoomMeta] = Field(default_factory=RoomMeta)
    control_states: Optional[RoomStateControl] = None
    sizing: Optional[Sizing] = None
    models: Optional[Models] = None
    constructions: RoomConstruction = Field(default_factory=RoomConstruction)

    @field_validator("inputs")
    def _validate_room_inputs(cls, room_inputs: Inputs):
        # Verify ACUs
        for acu_id, acu in list(room_inputs.acus.items()):
            valid_acu_id = re.sub(r'[^a-zA-Z0-9_]', '_', acu_id)
            room_inputs.acus[acu_id].valid_id = valid_acu_id

            if not acu.acu_switch:
                if acu.supply_air_volume_flow_rate != 0.0:
                    acu.supply_air_volume_flow_rate = 0
                    logger.warning(
                        f"ACU '{acu_id}' is turned off, supply_air_volume_flow_rate is set to 0."
                    )

        # Verify Servers
        for server_id, server in list(room_inputs.servers.items()):
            valid_server_id = re.sub(r'[^a-zA-Z0-9_]', '_', server_id)
            room_inputs.servers[server_id].valid_id = valid_server_id
            if not server.server_switch:
                if server.input_power != 0.0:
                    server.input_power = 0
                if server.air_volume_flow_rate != 0.0:
                    server.air_volume_flow_rate = 0
                logger.warning(
                    f"Server '{server_id}' is turned off, input_power and air_volume_flow_rate are set to 0."
                )
            if server.input_power and server.input_power_percentage:
                logger.error(
                    f"Server '{server_id}' has both input_power and input_power_percentage. "
                )
                exit(1)

        return room_inputs

    @field_validator("constructions")
    def _validate_room_constructions(
        cls,
        room_construction: RoomConstruction,
        values: ValidationInfo,
    ) -> RoomConstruction:
        if values.data["models"] == None:
            return room_construction
        models = values.data["models"]
        inputs = values.data["inputs"]
        # Validate models in the room-level, whereas no validation is needed for the building-level
        if room_construction.acus is not None:
            ACUValidator.validate(
                acus=room_construction.acus,
                geometry_models=models.geometry_models.acus,
                cooling_models=models.cooling_models.acus,
                power_models=models.power_models.acus,
                control_models=models.control_models.acus,
                curve_models=models.curve_models.acus,
                inputs=inputs.acus,
                context="room",
            )
        if room_construction.dehumidifiers is not None:
            DehumidifierValidator.validate(
                dehumidifiers=room_construction.dehumidifiers,
                geometry_models=models.geometry_models.dehumidifiers,
                cooling_models=models.cooling_models.dehumidifiers,
                power_models=models.power_models.dehumidifiers,
                control_models=models.control_models.dehumidifiers,
                curve_models=models.curve_models.dehumidifiers,
                inputs=inputs.dehumidifiers
            )
        if room_construction.boxes is not None:
            BoxValidator.validate(
                boxes=room_construction.boxes,
                geometry_models=models.geometry_models.boxes,
            )
        if room_construction.liquid_flow_networks is not None:
            LiquidFlowLoopsValidator.validate(
                loops=room_construction.liquid_flow_networks,
                models=models,
                inputs=inputs,
            )
        if room_construction.racks is not None:
            RackValidator.validate(
                racks=room_construction.racks,
                models=models,
                inputs=inputs.servers
            )
        if room_construction.rows is not None:
            RowValidator.validate(
                rows=room_construction.rows,
                models=models,
                inputs=inputs.servers
            )
        if room_construction.sensors is not None:
            SensorValidator.validate(
                sensors=room_construction.sensors,
            )
        if room_construction.heat_gains is not None:
            if room_construction.heat_gains.ites is not None:
                ITEValidator.validate(
                    ites=room_construction.heat_gains.ites,
                    ite_models=models.heat_gain_models.ite,
                    curve_models=models.curve_models.ites
                )
            if room_construction.heat_gains.electric_equipment is not None:
                ElectricEquipmentValidator.validate(
                    electric_equipment=room_construction.heat_gains.electric_equipment,
                    models=models.heat_gain_models.electric_equipment
                )
            if room_construction.heat_gains.light is not None:
                LightValidator.validate(
                    light=room_construction.heat_gains.light,
                    models=models.heat_gain_models.light
                )
            if room_construction.heat_gains.people is not None:
                PeopleValidator.validate(
                    people=room_construction.heat_gains.people,
                    models=models.heat_gain_models.people,
                )
            if room_construction.heat_gains.ites is not None and room_construction.num_ser > 0:
                if room_construction.total_server_rated_power != room_construction.total_ite_power:
                    logger.warning(
                        f"Total server rated power {room_construction.total_server_rated_power} W does not equal to total ITE power "
                        f"{room_construction.total_ite_power} W in the Room"
                    )
        return room_construction

    def dump(self, file_path: str | Path) -> None:
        with open(file_path, "w") as f:
            f.write(self.model_dump_json(indent=4))

    @classmethod
    def load(cls, file_path: str | Path) -> "Room":
        with open(file_path) as f:
            data = json.load(f)
            assert "models" in data.keys(), \
                f"Models is not found in the room file {file_path}"
        return cls(**data)
