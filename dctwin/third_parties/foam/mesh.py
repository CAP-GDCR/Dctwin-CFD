from typing import Dict, List, Union, Any
from pathlib import Path
from loguru import logger
import math

from dclib import Room
from dclib.ite.servers.server import Server
from dclib.construction.surfaces import Panel
from dclib.cooling.room.facilities import ACU
from dclib.construction.entities import Box
from dclib.ite.racks import Rack
from dclib.models.geometry import Vertex
from dclib.room import Row

from dctwin.utils import template_env
from dctwin.third_parties.foam.utils import generate_control_dict

from .utils import is_closed, round_to_base, rotate_rectangular


def get_face_mapping(orientation):
    if orientation == 0:
        return {
            'left': 'left',
            'right': 'right',
            'front': 'front',
            'rear': 'rear',
            'top': 'top',
            'bottom': 'bottom'
        }
    elif orientation == 90:
        return {
            'left': 'front',
            'right': 'rear',
            'front': 'right',
            'rear': 'left',
            'top': 'top',
            'bottom': 'bottom'
        }
    elif orientation == 180:
        return {
            'left': 'right',
            'right': 'left',
            'front': 'rear',
            'rear': 'front',
            'top': 'top',
            'bottom': 'bottom'
        }
    elif orientation == 270:
        return {
            'left': 'rear',
            'right': 'front',
            'front': 'left',
            'rear': 'right',
            'top': 'top',
            'bottom': 'bottom'
        }
    else:
        raise ValueError("Invalid orientation")


class BoxModel:
    name: str
    v_min: list[float]
    v_max: list[float]
    is_refinement_box: False
    refinement_level: int = 0

    def __init__(
            self,
            name: str,
            v_min: List[float],
            v_max: List[float],
            is_refinement_box: False,
            refinement_level: int = 0
    ):
        self.name = name
        self.v_min = v_min
        self.v_max = v_max
        self.is_refinement_box = is_refinement_box
        self.refinement_level = refinement_level

    @property
    def snappyHex_cmd(self):
        searchable_box_cmd = f"""
        {self.name}
        {{
            type searchableBox;
            min ({self.v_min[0]} {self.v_min[1]} {self.v_min[2]});
            max ({self.v_max[0]} {self.v_max[1]} {self.v_max[2]});
        }}
        """
        refinement_surface_cmd = f"""
        {self.name}
        {{
            level (0 0);
            patchInfo {{
                type wall;
            }}
        }}
        """
        # if the box defines the region for refinement, add the refinement box command to densify the mesh inside the
        # region defined by the box
        if self.is_refinement_box:
            refinement_box_cmd = f"""
            {self.name}
            {{
                mode inside;
                levels ((0 {self.refinement_level}));
            }}
            """
        else:
            refinement_box_cmd = ""
        return searchable_box_cmd, refinement_surface_cmd, refinement_box_cmd


class PlaneModel:
    name: str
    origin: List[float]
    span: List[float]

    def __init__(
            self,
            name: str,
            origin: List[float],
            span: List[float],
    ):
        self.name = name
        self.origin = origin
        self.span = span

    @property
    def createBaffles_cmd(self):
        if "opening" in self.name:
            _createBaffles_cmd = f"""
                {self.name}
                {{
                    type        searchableSurface;
                    surface     searchablePlate;
                    origin      ({self.origin[0]} {self.origin[1]} {self.origin[2]});
                    span        ({self.span[0]} {self.span[1]} {self.span[2]});
                    patches
                    {{
                        master
                        {{
                            name            {self.name}_master_patches;
                            type            cyclic;
                            neighbourPatch  {self.name}_slave_patches;
                        }}
                        slave
                        {{
                            name            {self.name}_slave_patches;
                            type            cyclic;
                            neighbourPatch  {self.name}_master_patches;
                        }}
                    }}
                }}
                """
        else:
            _createBaffles_cmd = f"""
                {self.name}
                {{
                    type        searchableSurface;
                    surface     searchablePlate;
                    origin      ({self.origin[0]} {self.origin[1]} {self.origin[2]});
                    span        ({self.span[0]} {self.span[1]} {self.span[2]});
                    patches
                    {{
                        master
                        {{
                            name            {self.name}_master_patches;
                            type            wall;
                        }}
                        slave
                        {{
                            name            {self.name}_slave_patches;
                            type            wall;
                        }}
                    }}
                }}
                """

        return _createBaffles_cmd


class PatchModel:
    name: str
    bounding_box_min: List[float]
    bounding_box_max: List[float]
    face_set_name: str
    patch_name: str
    neighbour_patch_name: str

    def __init__(
            self,
            name: str,
            bounding_box_min: list,
            bounding_box_max: list,
            neighbour_patch_name: str = None
    ):
        self.name = name
        self.bounding_box_min = bounding_box_min
        self.bounding_box_max = bounding_box_max
        self.face_set_name = f"{name}_patches"
        self.patch_name = f"{name}"
        self.neighbour_patch_name = neighbour_patch_name if neighbour_patch_name else None

    @property
    def topoSet_cmd(self):
        if "rack_air_leak" in self.name or "rack_cyclic" in self.name:
            top_set_cmd = f"""
                {{
                    name    {self.face_set_name};
                    type    faceSet;
                    action  new;
                    source  boxToFace;
                    sourceInfo
                    {{
                        box  ({self.bounding_box_min[0]} {self.bounding_box_min[1]} {self.bounding_box_min[2]}) ({self.bounding_box_max[0]} {self.bounding_box_max[1]} {self.bounding_box_max[2]});
                    }}
                }}
                {{
                    name {self.face_set_name};
                    type faceSet;
                    action subset;
                    source boundaryToFace;
                    sourceInfo
                    {{
                    }}
                }}
                """
        else:
            top_set_cmd = f"""
                {{
                    name    {self.face_set_name};
                    type    faceSet;
                    action  new;
                    source  boxToFace;
                    sourceInfo
                    {{
                        box  ({self.bounding_box_min[0]} {self.bounding_box_min[1]} {self.bounding_box_min[2]}) ({self.bounding_box_max[0]} {self.bounding_box_max[1]} {self.bounding_box_max[2]});
                    }}
                }}
                {{
                    name {self.face_set_name};
                    type faceSet;
                    action subset;
                    source boundaryToFace;
                    sourceInfo
                    {{
                    }}
                }}
                """
        return top_set_cmd

    @property
    def createPatch_cmd(self):
        if "rack_air_leak" in self.name:
            create_patch_cmd = f"""
                {{
                    name {self.patch_name};
                    patchInfo
                    {{
                        type cyclic;
                        neighbourPatch {self.neighbour_patch_name};
                    }}
                    constructFrom set;
                    set {self.face_set_name};
                }}
                """
        elif "rack_cyclic" in self.name:
            create_patch_cmd = f"""
                {{
                    name {self.patch_name};
                    patchInfo
                    {{
                        type cyclic;
                        neighbourPatch {self.neighbour_patch_name};
                    }}
                    constructFrom set;
                    set {self.face_set_name};
                }}
                """
        else:
            create_patch_cmd = f"""
                {{
                    name {self.patch_name};
                    patchInfo
                    {{
                        type patch;
                    }}
                    constructFrom set;
                    set {self.face_set_name};
                }}
                """
        return create_patch_cmd


class ACUModel:
    config: ACU
    base_size: float
    box: BoxModel
    supply_face: PatchModel
    return_face: PatchModel

    def __init__(
            self,
            acu: ACU,
            base_size: float = 0.2
    ):
        self.config = acu
        self.base_size = base_size
        self.cheek_config()
        self._make_box()
        self._make_supply_face()
        self._make_return_face()

    def cheek_config(self):
        if self.config.geometry.orientation < 0:
            self.config.geometry.orientation = int(self.config.geometry.orientation + 360)

        if not (self.config.geometry.orientation == 0 or self.config.geometry.orientation == 90 or
                self.config.geometry.orientation == 180 or self.config.geometry.orientation == 270):
            raise ValueError(f"Invalid orientation: {self.config.geometry.orientation} for ACU '{self.config.uid}'")

        if not (self.config.geometry.supply_face.side.name in ["top", "bottom", "left", "right", "front", "rear"]):
            raise ValueError(f"Invalid supply face side: {self.config.geometry.supply_face.side.name} for ACU '"
                             f"{self.config.uid}'")

        if not (self.config.geometry.return_face.side.name in ["top", "bottom", "left", "right", "front", "rear"]):
            raise ValueError(f"Invalid return face side: {self.config.geometry.return_face.side.name} for ACU '"
                             f"{self.config.uid}'")

    def _make_box(self):
        # compute the rotated vertex of the ACU object
        v_min, v_max = rotate_rectangular(
            abs_vertex_1=Vertex(
                x=self.config.geometry.location.x,
                y=self.config.geometry.location.y,
                z=self.config.geometry.location.z
            ),
            abs_vertex_2=Vertex(
                x=self.config.geometry.location.x + self.config.geometry.size.x,
                y=self.config.geometry.location.y,
                z=self.config.geometry.location.z
            ),
            abs_vertex_3=Vertex(
                x=self.config.geometry.location.x + self.config.geometry.size.x,
                y=self.config.geometry.location.y + self.config.geometry.size.y,
                z=self.config.geometry.location.z
            ),
            abs_vertex_4=Vertex(
                x=self.config.geometry.location.x,
                y=self.config.geometry.location.y + self.config.geometry.size.y,
                z=self.config.geometry.location.z
            ),
            angle=self.config.geometry.orientation,
        )
        v_min.z = self.config.geometry.location.z
        v_max.z = self.config.geometry.location.z + self.config.geometry.size.z
        # create a box object that represents the ACU
        self.box = BoxModel(
            name=f"acu_wall_{self.config.valid_id}",
            v_min=[v_min.x, v_min.y, v_min.z],
            v_max=[v_max.x, v_max.y, v_max.z],
            is_refinement_box=False
        )

    def _make_supply_face(self):
        acu_supply_face = self.config.geometry.supply_face
        bounding_box_min, bounding_box_max = self._get_supply_or_return_face_bounding_box(face=acu_supply_face)
        self.supply_face = PatchModel(
            name=f"acu_supply_{self.config.valid_id}",
            bounding_box_min=bounding_box_min,
            bounding_box_max=bounding_box_max
        )

    def _make_return_face(self):
        acu_return_face = self.config.geometry.return_face
        bounding_box_min, bounding_box_max = self._get_supply_or_return_face_bounding_box(face=acu_return_face)
        self.return_face = PatchModel(
            name=f"acu_return_{self.config.valid_id}",
            bounding_box_min=bounding_box_min,
            bounding_box_max=bounding_box_max
        )

    def _get_supply_or_return_face_bounding_box(self, face):
        orientation = int(self.config.geometry.orientation)
        if face.side.name == "bottom" or face.side.name == "top":
            bounding_box_min = [
                self.box.v_min[0] + face.offset.x
                if orientation in [0, 180] else self.box.v_min[0] + face.offset.y,

                self.box.v_min[1] + face.offset.y
                if orientation in [0, 180] else self.box.v_min[1] + face.offset.x,

                self.box.v_min[2] - 0.1 * self.base_size if face.side.name == "bottom"
                else self.box.v_min[2] + self.config.geometry.size.z - 0.1 * self.base_size
            ]
            bounding_box_max = [
                self.box.v_min[0] + face.offset.x + face.width
                if orientation in [0, 180] else self.box.v_min[0] + face.offset.y + face.length,

                self.box.v_min[1] + face.offset.y + face.length
                if orientation in [0, 180] else self.box.v_min[1] + face.offset.x + face.width,

                self.box.v_min[2] + 0.1 * self.base_size if face.side.name == "bottom"
                else self.box.v_min[2] + self.config.geometry.size.z + 0.1 * self.base_size
            ]
        elif face.side.name == "left" or face.side.name == "right":
            if self.config.geometry.orientation == 0 or self.config.geometry.orientation == 180:
                bounding_box_min = [
                    self.box.v_min[0] - 0.1 * self.base_size
                    if (face.side.name == "left" and self.config.geometry.orientation == 0) or
                       (face.side.name == "right" and self.config.geometry.orientation == 180)
                    else self.box.v_min[0] + self.config.geometry.size.x - 0.1 * self.base_size,
                    self.box.v_min[1] + face.offset.x,
                    self.box.v_min[2] + face.offset.y
                ]
                bounding_box_max = [
                    self.box.v_min[0] + 0.1 * self.base_size
                    if (face.side.name == "left" and self.config.geometry.orientation == 0) or
                       (face.side.name == "right" and self.config.geometry.orientation == 180)
                    else self.box.v_min[0] + self.config.geometry.size.x + 0.1 * self.base_size,
                    self.box.v_min[1] + face.offset.x + face.width,
                    self.box.v_min[2] + face.offset.y + face.length
                ]
            else:
                bounding_box_min = [
                    self.box.v_min[0] + face.offset.x,
                    self.box.v_min[1] - 0.1 * self.base_size
                    if (face.side.name == "left" and self.config.geometry.orientation == 270) or
                       (face.side.name == "right" and self.config.geometry.orientation == 90)
                    else self.box.v_min[1] + self.config.geometry.size.y - 0.1 * self.base_size,
                    self.box.v_min[2] + face.offset.y
                ]
                bounding_box_max = [
                    self.box.v_min[0] + face.offset.x + face.width,
                    self.box.v_min[1] + 0.1 * self.base_size
                    if (face.side.name == "left" and self.config.geometry.orientation == 270) or
                       (face.side.name == "right" and self.config.geometry.orientation == 90)
                    else self.box.v_min[1] + self.config.geometry.size.y + 0.1 * self.base_size,
                    self.box.v_min[2] + face.offset.y + face.length
                ]
        else:  # face.side.name == "front" or face.side.name == "rear":
            if self.config.geometry.orientation == 0 or self.config.geometry.orientation == 180:
                bounding_box_min = [
                    self.box.v_min[0] + face.offset.x,
                    self.box.v_min[1] - 0.1 * self.base_size
                    if (face.side.name == "front" and self.config.geometry.orientation == 0)
                       or (face.side.name == "rear" and self.config.geometry.orientation == 180)
                    else self.box.v_min[1] + self.config.geometry.size.y - 0.1 * self.base_size,
                    self.box.v_min[2] + face.offset.y
                ]
                bounding_box_max = [
                    self.box.v_min[0] + face.offset.x + face.width,
                    self.box.v_min[1] + 0.1 * self.base_size
                    if (face.side.name == "front" and self.config.geometry.orientation == 0)
                       or (face.side.name == "rear" and self.config.geometry.orientation == 180)
                    else self.box.v_min[1] + self.config.geometry.size.y + 0.1 * self.base_size,
                    self.box.v_min[2] + face.offset.y + face.length
                ]
            else:
                bounding_box_min = [
                    self.box.v_min[0] + self.config.geometry.size.y - 0.1 * self.base_size
                    if (face.side.name == "front" and self.config.geometry.orientation == 90)
                       or (face.side.name == "rear" and self.config.geometry.orientation == 270)
                    else self.box.v_min[0] - 0.1 * self.base_size,
                    self.box.v_min[1] + face.offset.x,
                    self.box.v_min[2] + face.offset.y
                ]
                bounding_box_max = [
                    self.box.v_min[0] + self.config.geometry.size.y + 0.1 * self.base_size
                    if (face.side.name == "front" and self.config.geometry.orientation == 90)
                       or (face.side.name == "rear" and self.config.geometry.orientation == 270)
                    else self.box.v_min[0] + 0.1 * self.base_size,
                    self.box.v_min[1] + face.offset.x + face.width,
                    self.box.v_min[2] + face.offset.y + face.length
                ]
        return bounding_box_min, bounding_box_max

    @property
    def snappyHex_cmd(self):
        return self.box.snappyHex_cmd

    @property
    def topoSet_cmd(self):
        return self.supply_face.topoSet_cmd + self.return_face.topoSet_cmd

    @property
    def createPatch_cmd(self):
        return self.supply_face.createPatch_cmd + self.return_face.createPatch_cmd

class ServerModel:
    config: Server
    v_min: Vertex
    v_max: Vertex
    slot_height: float
    base_size: float
    refine_size: float
    box: BoxModel
    inlet_face: PatchModel
    outlet_face: PatchModel

    def __init__(
            self,
            config: Server,
            v_min: Vertex,
            v_max: Vertex,
            slot_height: float = 0.05,
            base_size: float = 0.2,
            scale: int = 3
    ):
        self.config = config
        self.v_min = v_min
        self.v_max = v_max
        self.slot_height = slot_height
        self.base_size = base_size
        self.refine_size = base_size / (2 ** scale)
        self._make_inlet_outlet_face()

    def _get_bounding_box_min_max(self, x: float = None, y: float = None):
        orientation = int(self.config.geometry.orientation)
        if orientation == 0 or orientation == 180:
            bounding_box_min = [
                self.v_min.x,
                y - 0.1 * self.refine_size,
                self.v_min.z
            ]
            bounding_box_max = [
                round(self.v_min.x + (self.v_max.x - self.v_min.x) / 3, 3),
                y + 0.1 * self.refine_size,
                self.v_max.z
            ]
        elif orientation == 90 or orientation == 270:
            bounding_box_min = [
                x - 0.1 * self.refine_size,
                self.v_min.y,
                self.v_min.z
            ]
            bounding_box_max = [
                x + 0.1 * self.refine_size,
                round(self.v_min.y + (self.v_max.y - self.v_min.y) / 3, 3),
                self.v_max.z
            ]
        else:
            raise ValueError(f"Invalid orientation: {orientation} for server '{self.config.uid}'")

        return bounding_box_min, bounding_box_max

    def _make_inlet_outlet_face(self):
        orientation = int(self.config.geometry.orientation)
        if orientation in [0, 180]:
            y_inlet_face = self.v_min.y if orientation == 0 else self.v_max.y
            y_outlet_face = self.v_max.y if orientation == 0 else self.v_min.y
            x_inlet_face = None
            x_outlet_face = None
        else:  # orientation in [90, 270]
            y_inlet_face = None
            y_outlet_face = None
            x_inlet_face = self.v_max.x if orientation == 90 else self.v_min.x
            x_outlet_face = self.v_min.x if orientation == 90 else self.v_max.x

        bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(x=x_inlet_face, y=y_inlet_face)
        self.inlet_face = PatchModel(
            name=f"server_inlet_{self.config.valid_id}",
            neighbour_patch_name=f"server_outlet_{self.config.valid_id}",
            bounding_box_max=bounding_box_max,
            bounding_box_min=bounding_box_min
        )

        bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(x=x_outlet_face, y=y_outlet_face)
        self.outlet_face = PatchModel(
            name=f"server_outlet_{self.config.valid_id}",
            neighbour_patch_name=f"server_inlet_{self.config.valid_id}",
            bounding_box_max=bounding_box_max,
            bounding_box_min=bounding_box_min
        )

    @property
    def topoSet_cmd(self):
        return self.inlet_face.topoSet_cmd + self.outlet_face.topoSet_cmd

    @property
    def createPatch_cmd(self):
        return self.inlet_face.createPatch_cmd + self.outlet_face.createPatch_cmd


class RowRackModel:
    config: Row
    v_min: Vertex
    v_max: Vertex
    base_size: float
    refine_size: float
    refinement_level: int = 1
    box: BoxModel

    def __init__(
            self,
            row_rack: Row,
            base_size: float = 0.2,
            scale: int = 3,
            refinement_level: int = 1
    ):
        self.config = row_rack
        self.base_size = base_size
        self.refine_size = base_size / (2 ** scale)
        self.refine_region = None
        self.servers = []
        self.surrounding_planes = []
        self.blanking_panel = []
        self.slot_height = 0.05
        self.rack_air_leak_patch_list = []
        self.cheek_config()
        # compute the absolute coordinates of the rack object
        self.v_min, self.v_max = rotate_rectangular(
            abs_vertex_1=Vertex(
                x=row_rack.geometry.location.x,
                y=row_rack.geometry.location.y,
                z=row_rack.geometry.location.z
            ),
            abs_vertex_2=Vertex(
                x=row_rack.geometry.location.x + row_rack.geometry.size.x,
                y=row_rack.geometry.location.y,
                z=row_rack.geometry.location.z
            ),
            abs_vertex_3=Vertex(
                x=row_rack.geometry.location.x + row_rack.geometry.size.x,
                y=row_rack.geometry.location.y + row_rack.geometry.size.y,
                z=row_rack.geometry.location.z
            ),
            abs_vertex_4=Vertex(
                x=row_rack.geometry.location.x,
                y=row_rack.geometry.location.y + row_rack.geometry.size.y,
                z=row_rack.geometry.location.z
            ),
            angle=row_rack.geometry.orientation,
        )
        self.v_min.z = row_rack.geometry.location.z
        self.v_max.z = row_rack.geometry.location.z + row_rack.geometry.size.z
        # make the refinement level of the rack
        self.refinement_level = refinement_level
        # make the box of the rack
        self._make_box()
        self._make_blanking_plane()
        # make the servers of the rack
        self._make_servers()
        # make the surrounding planes of the rack
        self.make_air_leak_patch()

    def cheek_config(self):
        if self.config.geometry.orientation < 0:
            self.config.geometry.orientation = int(self.config.geometry.orientation + 360)

        if not (self.config.geometry.orientation == 0 or self.config.geometry.orientation == 90 or
                self.config.geometry.orientation == 180 or self.config.geometry.orientation == 270):
            raise ValueError(f"Invalid orientation: {self.config.geometry.orientation} for rack '{self}'")

        for rack in self.config.constructions.racks.values():
            if rack.geometry.orientation != self.config.geometry.orientation:
                rack.geometry.orientation = self.config.geometry.orientation
                logger.warning(f"Rack '{rack.uid}' orientation is not consistent with the row orientation. "
                               f"Now the orientation of the rack is set to {self.config.geometry.orientation}")
            RackModel.cheek_config(rack)

    def _make_blanking_plane(self):
        for rack in self.config.constructions.racks.values():
            if not rack.geometry.hasBlankingPanel:
                continue
            rack_v_min, rack_v_max = rotate_rectangular(
                abs_vertex_1=Vertex(
                    x=rack.geometry.location.x,
                    y=rack.geometry.location.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_2=Vertex(
                    x=rack.geometry.location.x + rack.geometry.size.x,
                    y=rack.geometry.location.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_3=Vertex(
                    x=rack.geometry.location.x + rack.geometry.size.x,
                    y=rack.geometry.location.y + rack.geometry.size.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_4=Vertex(
                    x=rack.geometry.location.x,
                    y=rack.geometry.location.y + rack.geometry.size.y,
                    z=rack.geometry.location.z
                ),
                angle=rack.geometry.orientation,
            )
            rack_v_min.z = rack.geometry.location.z
            rack_v_max.z = rack.geometry.location.z + rack.geometry.size.z

            orientation = self.config.geometry.orientation

            if orientation in [0, 180]:
                y_inlet_face = rack_v_min.y if orientation == 0 else rack_v_max.y
                y_outlet_face = rack_v_max.y if orientation == 0 else rack_v_min.y
                x_inlet_face = None
                x_outlet_face = None
            else:  # orientation in [90, 270]
                y_inlet_face = None
                y_outlet_face = None
                x_inlet_face = rack_v_max.x if orientation == 90 else rack_v_min.x
                x_outlet_face = rack_v_min.x if orientation == 90 else rack_v_max.x

            bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(
                x=x_inlet_face,
                y=y_inlet_face,
                rack_v_max=rack_v_max,
                rack_v_min=rack_v_min,
                orientation=orientation,
                ratio=0
            )
            self.blanking_panel.append(
                PatchModel(
                    name=f"rack_cyclic_{rack.valid_id}_master",
                    neighbour_patch_name=f"rack_cyclic_{rack.valid_id}_slave",
                    bounding_box_max=bounding_box_max,
                    bounding_box_min=bounding_box_min
                )
            )

            bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(
                x=x_outlet_face,
                y=y_outlet_face,
                rack_v_max=rack_v_max,
                rack_v_min=rack_v_min,
                orientation=orientation,
                ratio=0
            )
            self.blanking_panel.append(
                PatchModel(
                    name=f"rack_cyclic_{rack.valid_id}_slave",
                    neighbour_patch_name=f"rack_cyclic_{rack.valid_id}_master",
                    bounding_box_max=bounding_box_max,
                    bounding_box_min=bounding_box_min
                )
            )

    def _get_bounding_box_min_max(self, x: int = None, y: int = None, rack_v_max: Vertex = None,
                                  rack_v_min: Vertex = None, orientation: int = None, ratio: float = 2/3):
        if orientation == 0 or orientation == 180:
            bounding_box_min = [
                round(rack_v_min.x + (rack_v_max.x - rack_v_min.x) * ratio, 3),
                y - 0.1 * self.refine_size,
                rack_v_min.z
            ]
            bounding_box_max = [
                rack_v_max.x,
                y + 0.1 * self.refine_size,
                rack_v_max.z
            ]
        elif orientation == 90 or orientation == 270:
            bounding_box_min = [
                x - 0.1 * self.refine_size,
                round(rack_v_min.y + (rack_v_max.y - rack_v_min.y) * ratio, 3),
                rack_v_min.z
            ]
            bounding_box_max = [
                x + 0.1 * self.refine_size,
                rack_v_max.y,
                rack_v_max.z
            ]
        else:
            raise ValueError(f"Invalid orientation: {orientation} for RowRack '{self.config.uid}'")

        return bounding_box_min, bounding_box_max

    def make_air_leak_patch(self):
        for rack in self.config.constructions.racks.values():
            # compute the absolute coordinates of the rack object
            rack_v_min, rack_v_max = rotate_rectangular(
                abs_vertex_1=Vertex(
                    x=rack.geometry.location.x,
                    y=rack.geometry.location.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_2=Vertex(
                    x=rack.geometry.location.x + rack.geometry.size.x,
                    y=rack.geometry.location.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_3=Vertex(
                    x=rack.geometry.location.x + rack.geometry.size.x,
                    y=rack.geometry.location.y + rack.geometry.size.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_4=Vertex(
                    x=rack.geometry.location.x,
                    y=rack.geometry.location.y + rack.geometry.size.y,
                    z=rack.geometry.location.z
                ),
                angle=rack.geometry.orientation,
            )
            rack_v_min.z = rack.geometry.location.z
            rack_v_max.z = rack.geometry.location.z + rack.geometry.size.z

            orientation = rack.geometry.orientation
            if orientation in [0, 180]:
                y_inlet_face = rack_v_min.y if orientation == 0 else rack_v_max.y
                y_outlet_face = rack_v_max.y if orientation == 0 else rack_v_min.y
                x_inlet_face = None
                x_outlet_face = None
            else:  # orientation in [90, 270]
                y_inlet_face = None
                y_outlet_face = None
                x_inlet_face = rack_v_max.x if orientation == 90 else rack_v_min.x
                x_outlet_face = rack_v_min.x if orientation == 90 else rack_v_max.x

            bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(
                x=x_inlet_face,
                y=y_inlet_face,
                rack_v_max=rack_v_max,
                rack_v_min=rack_v_min,
                orientation=orientation
            )
            self.rack_air_leak_patch_list.append(
                PatchModel(
                    name=f"rack_air_leak_{rack.valid_id}_master",
                    neighbour_patch_name=f"rack_air_leak_{rack.valid_id}_slave",
                    bounding_box_max=bounding_box_max,
                    bounding_box_min=bounding_box_min
                )
            )

            bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(
                x=x_outlet_face,
                y=y_outlet_face,
                rack_v_max=rack_v_max,
                rack_v_min=rack_v_min,
                orientation=orientation
            )
            self.rack_air_leak_patch_list.append(
                PatchModel(
                    name=f"rack_air_leak_{rack.valid_id}_slave",
                    neighbour_patch_name=f"rack_air_leak_{rack.valid_id}_master",
                    bounding_box_max=bounding_box_max,
                    bounding_box_min=bounding_box_min
                )
            )

    def _make_box(self):
        self.box = BoxModel(
            name=f'rack_wall_row_{self.config.uid}',
            v_min=[
                self.v_min.x,
                self.v_min.y,
                self.v_min.z,
            ],
            v_max=[
                self.v_max.x,
                self.v_max.y,
                self.v_max.z,
            ],
            is_refinement_box=True,
            refinement_level=self.refinement_level
        )

    def _make_servers(self):
        for rack in self.config.constructions.racks.values():
            rack_v_min, rack_v_max = rotate_rectangular(
                abs_vertex_1=Vertex(
                    x=rack.geometry.location.x,
                    y=rack.geometry.location.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_2=Vertex(
                    x=rack.geometry.location.x + rack.geometry.size.x,
                    y=rack.geometry.location.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_3=Vertex(
                    x=rack.geometry.location.x + rack.geometry.size.x,
                    y=rack.geometry.location.y + rack.geometry.size.y,
                    z=rack.geometry.location.z
                ),
                abs_vertex_4=Vertex(
                    x=rack.geometry.location.x,
                    y=rack.geometry.location.y + rack.geometry.size.y,
                    z=rack.geometry.location.z
                ),
                angle=rack.geometry.orientation,
            )
            rack_v_min.z = rack.geometry.location.z
            rack_v_max.z = rack.geometry.location.z + rack.geometry.size.z

            for server_name, server in rack.constructions.servers.items():
                if server.geometry.orientation != rack.geometry.orientation:
                    server.geometry.orientation = rack.geometry.orientation
                    logger.warning(f"Server '{server_name}' orientation is changed to {rack.geometry.orientation}, "
                                   f"because it is not the same as the rack orientation")
                orientation = server.geometry.orientation
                if orientation == 0 or orientation == 270:
                    server_v_min = Vertex(
                        x=rack_v_min.x,
                        y=rack_v_min.y,
                        z=rack_v_min.z + server.geometry.slot_position * self.slot_height
                    )
                    server_v_max = Vertex(
                        x=rack_v_min.x + server.geometry.width
                        if rack.geometry.orientation == 0 else rack_v_min.x + server.geometry.depth,

                        y=rack_v_min.y + server.geometry.depth
                        if rack.geometry.orientation == 0 else rack_v_min.y + server.geometry.width,

                        z=rack_v_min.z + (
                                    server.geometry.slot_position + server.geometry.slot_occupation) * self.slot_height
                    )
                else:  # orientation == 90 or orientation == 180:
                    server_v_min = Vertex(
                        x=rack_v_max.x - server.geometry.depth
                        if orientation == 90 else rack_v_min.x,

                        y=rack_v_min.y if orientation == 90 else rack_v_max.y - server.geometry.depth,
                        z=rack_v_min.z + server.geometry.slot_position * self.slot_height
                    )
                    server_v_max = Vertex(
                        x=rack_v_max.x,
                        y=rack_v_max.y,
                        z=rack_v_min.z + (server.geometry.slot_position + server.geometry.slot_occupation) * self.slot_height
                    )

                self.servers.append(
                    ServerModel(
                        config=server,
                        v_min=server_v_min,
                        v_max=server_v_max,
                        base_size=self.base_size,
                        slot_height=self.slot_height,
                    )
                )

    @property
    def snappyHex_cmd(self):
        return self.box.snappyHex_cmd

    @property
    def createBaffles_cmd(self):
        create_baffles_cmd = ""
        for plane in self.surrounding_planes:
            create_baffles_cmd += plane.createBaffles_cmd
        return create_baffles_cmd

    @property
    def topoSet_cmd(self):
        topo_set_cmd = ""
        for blanking_panel in self.blanking_panel:
            topo_set_cmd += blanking_panel.topoSet_cmd
        for cyclic_patch in self.rack_air_leak_patch_list:
            topo_set_cmd += cyclic_patch.topoSet_cmd
        for server in self.servers:
            topo_set_cmd += server.topoSet_cmd
        return topo_set_cmd

    @property
    def createPatch_cmd(self):
        create_patch_cmd = ""
        for blanking_panel in self.blanking_panel:
            create_patch_cmd += blanking_panel.createPatch_cmd
        for cyclic_patch in self.rack_air_leak_patch_list:
            create_patch_cmd += cyclic_patch.createPatch_cmd
        for server in self.servers:
            create_patch_cmd += server.createPatch_cmd
        return create_patch_cmd


class RackModel:
    config: Rack
    v_min: Vertex
    v_max: Vertex
    base_size: float
    refine_size: float
    refinement_level: int = 1
    box: BoxModel
    slot_height: float = 0.05

    def __init__(
            self,
            rack: Rack,
            base_size: float = 0.2,
            scale: int = 3,
            refinement_level: int = 1
    ):
        self.config = rack
        self.base_size = base_size
        self.refine_size = base_size / (2 ** scale)
        self.refine_region = None
        self.servers = []
        self.surrounding_planes = []
        self.hasBlankingPanel = self.config.geometry.hasBlankingPanel
        self.blanking_panel = []
        self.slot_height = 0.05
        self.rack_air_leak_patch_list = []
        self.cheek_config()
        # compute the absolute coordinates of the rack object
        self.v_min, self.v_max = rotate_rectangular(
            abs_vertex_1=Vertex(
                x=rack.geometry.location.x,
                y=rack.geometry.location.y,
                z=rack.geometry.location.z
            ),
            abs_vertex_2=Vertex(
                x=rack.geometry.location.x + rack.geometry.size.x,
                y=rack.geometry.location.y,
                z=rack.geometry.location.z
            ),
            abs_vertex_3=Vertex(
                x=rack.geometry.location.x + rack.geometry.size.x,
                y=rack.geometry.location.y + rack.geometry.size.y,
                z=rack.geometry.location.z
            ),
            abs_vertex_4=Vertex(
                x=rack.geometry.location.x,
                y=rack.geometry.location.y + rack.geometry.size.y,
                z=rack.geometry.location.z
            ),
            angle=rack.geometry.orientation,
        )
        self.v_min.z = rack.geometry.location.z
        self.v_max.z = rack.geometry.location.z + rack.geometry.size.z
        # make the servers of the rack
        self._make_servers()
        # make the refinement region of the rack
        self.refinement_level = refinement_level
        # self._make_refinement_region()
        self._make_box()
        if not self.hasBlankingPanel:
            # 该函数用于添加循环边界，因此在机架没有面板的情况下需要调用
            self._make_blanking_plane()
        self.make_air_leak_patch()

    def _make_box(self):
        self.box = BoxModel(
            name=f'rack_wall_{self.config.valid_id}',
            v_min=[
                self.v_min.x,
                self.v_min.y,
                self.v_min.z,
            ],
            v_max=[
                self.v_max.x,
                self.v_max.y,
                self.v_max.z,
            ],
            is_refinement_box=True,
            refinement_level=self.refinement_level
        )

    def _get_bounding_box_min_max(self, x: int = None, y: int = None, ratio: float = 2 / 3):
        orientation = int(self.config.geometry.orientation)
        if orientation == 0 or orientation == 180:
            bounding_box_min = [
                round(self.v_min.x + (self.v_max.x - self.v_min.x) * ratio, 3),
                y - 0.1 * self.refine_size,
                self.v_min.z
            ]
            bounding_box_max = [
                self.v_max.x,
                y + 0.1 * self.refine_size,
                self.v_max.z
            ]
        elif orientation == 90 or orientation == 270:
            bounding_box_min = [
                x - 0.1 * self.refine_size,
                round(self.v_min.y + (self.v_max.y - self.v_min.y) * ratio, 3),
                self.v_min.z
            ]
            bounding_box_max = [
                x + 0.1 * self.refine_size,
                self.v_max.y,
                self.v_max.z
            ]
        else:
            raise ValueError(f"Invalid orientation: {orientation} for rack '{self.config.uid}'")

        return bounding_box_min, bounding_box_max

    def make_air_leak_patch(self):
        orientation = self.config.geometry.orientation
        if orientation in [0, 180]:
            y_inlet_face = self.v_min.y if orientation == 0 else self.v_max.y
            y_outlet_face = self.v_max.y if orientation == 0 else self.v_min.y
            x_inlet_face = None
            x_outlet_face = None
        else:  # orientation in [90, 270]
            y_inlet_face = None
            y_outlet_face = None
            x_inlet_face = self.v_max.x if orientation == 90 else self.v_min.x
            x_outlet_face = self.v_min.x if orientation == 90 else self.v_max.x

        bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(x=x_inlet_face, y=y_inlet_face)
        self.rack_air_leak_patch_list.append(
            PatchModel(
                name=f"rack_air_leak_{self.config.valid_id}_master",
                neighbour_patch_name=f"rack_air_leak_{self.config.valid_id}_slave",
                bounding_box_max=bounding_box_max,
                bounding_box_min=bounding_box_min
            )
        )

        bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(x=x_outlet_face, y=y_outlet_face)
        self.rack_air_leak_patch_list.append(
            PatchModel(
                name=f"rack_air_leak_{self.config.valid_id}_slave",
                neighbour_patch_name=f"rack_air_leak_{self.config.valid_id}_master",
                bounding_box_max=bounding_box_max,
                bounding_box_min=bounding_box_min
            )
        )

    def cheek_config(self: Union[Rack, 'RackModel']):
        # If self is a Rack instance (not a RackModel)
        if isinstance(self, Rack):
            rack_config = self
        else:
            rack_config = self.config

        if rack_config.geometry.orientation < 0:
            rack_config.geometry.orientation = int(rack_config.geometry.orientation + 360)

        if not (rack_config.geometry.orientation == 0 or rack_config.geometry.orientation == 90 or
                rack_config.geometry.orientation == 180 or rack_config.geometry.orientation == 270):
            raise ValueError(f"Invalid orientation: {rack_config.geometry.orientation} for rack '{rack_config}'")

    def _make_surrounding_plane(self):
        orientation = self.config.geometry.orientation
        if orientation == 0 or orientation == 180:
            self.surrounding_planes = [
                PlaneModel(
                    name=f"rack_wall_{self.config.valid_id}_left_plane",
                    origin=[self.v_min.x if orientation == 0 else self.v_max.x, self.v_min.y, self.v_min.z],
                    span=[0, self.config.geometry.size.y, self.config.geometry.size.z],
                ),
                PlaneModel(
                    name=f"rack_wall_{self.config.valid_id}_right_plane",
                    origin=[self.v_max.x if orientation == 0 else self.v_min.x, self.v_min.y, self.v_min.z],
                    span=[0, self.config.geometry.size.y, self.config.geometry.size.z],
                ),
                PlaneModel(
                    name=f"rack_wall_{self.config.valid_id}_top_plane",
                    origin=[self.v_min.x, self.v_min.y, self.v_min.z + self.config.geometry.size.z],
                    span=[self.config.geometry.size.x, self.config.geometry.size.y, 0],
                )
            ]
        elif orientation == 90 or orientation == 270:
            self.surrounding_planes = [
                PlaneModel(
                    name=f"rack_wall_{self.config.valid_id}_left_plane",
                    origin=[self.v_min.x, self.v_min.y if orientation == 90 else self.v_max.y, self.v_min.z],
                    span=[self.config.geometry.size.y, 0, self.config.geometry.size.z],
                ),
                PlaneModel(
                    name=f"rack_wall_{self.config.valid_id}_right_plane",
                    origin=[self.v_min.x, self.v_max.y if orientation == 90 else self.v_min.y, self.v_min.z],
                    span=[self.config.geometry.size.y, 0, self.config.geometry.size.z],
                ),
                PlaneModel(
                    name=f"rack_wall_{self.config.valid_id}_top_plane",
                    origin=[self.v_min.x, self.v_min.y, self.v_min.z + self.config.geometry.size.z],
                    span=[self.config.geometry.size.y, self.config.geometry.size.x, 0],
                )
            ]

    def _make_blanking_plane(self):
        orientation = self.config.geometry.orientation
        if orientation in [0, 180]:
            y_inlet_face = self.v_min.y if orientation == 0 else self.v_max.y
            y_outlet_face = self.v_max.y if orientation == 0 else self.v_min.y
            x_inlet_face = None
            x_outlet_face = None
        else:  # orientation in [90, 270]
            y_inlet_face = None
            y_outlet_face = None
            x_inlet_face = self.v_max.x if orientation == 90 else self.v_min.x
            x_outlet_face = self.v_min.x if orientation == 90 else self.v_max.x

        bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(x=x_inlet_face, y=y_inlet_face, ratio=0)
        self.blanking_panel.append(
            PatchModel(
                name=f"rack_cyclic_{self.config.valid_id}_master",
                neighbour_patch_name=f"rack_cyclic_{self.config.valid_id}_slave",
                bounding_box_max=bounding_box_max,
                bounding_box_min=bounding_box_min
            )
        )

        bounding_box_min, bounding_box_max = self._get_bounding_box_min_max(x=x_outlet_face, y=y_outlet_face, ratio=0)
        self.blanking_panel.append(
            PatchModel(
                name=f"rack_cyclic_{self.config.valid_id}_slave",
                neighbour_patch_name=f"rack_cyclic_{self.config.valid_id}_master",
                bounding_box_max=bounding_box_max,
                bounding_box_min=bounding_box_min
            )
        )

    def _make_servers(self):
        for server_name, server in self.config.constructions.servers.items():
            if server.geometry.orientation != self.config.geometry.orientation:
                server.geometry.orientation = self.config.geometry.orientation
                logger.warning(f"Server '{server_name}' orientation is changed to {self.config.geometry.orientation}, "
                               f"because it is not the same as the rack orientation")
            orientation = server.geometry.orientation
            if orientation == 0 or orientation == 270:
                server_v_min = Vertex(
                    x=self.v_min.x,
                    y=self.v_min.y,
                    z=self.v_min.z + server.geometry.slot_position * self.slot_height
                )
                server_v_max = Vertex(
                    x=self.v_min.x + server.geometry.width
                    if self.config.geometry.orientation == 0 else self.v_min.x + server.geometry.depth,

                    y=self.v_min.y + server.geometry.depth
                    if self.config.geometry.orientation == 0 else self.v_min.y + server.geometry.width,

                    z=self.v_min.z + (
                                server.geometry.slot_position + server.geometry.slot_occupation) * self.slot_height
                )
            else:  # orientation == 90 or orientation == 180:
                server_v_min = Vertex(
                    x=self.v_max.x - server.geometry.depth
                    if orientation == 90 else self.v_min.x,

                    y=self.v_min.y if orientation == 90 else self.v_max.y - server.geometry.depth,
                    z=self.v_min.z + server.geometry.slot_position * self.slot_height
                )
                server_v_max = Vertex(
                    x=self.v_max.x,
                    y=self.v_max.y,
                    z=self.v_min.z + (
                                server.geometry.slot_position + server.geometry.slot_occupation) * self.slot_height
                )

            self.servers.append(
                ServerModel(
                    config=server,
                    v_min=server_v_min,
                    v_max=server_v_max,
                    base_size=self.base_size,
                    slot_height=self.slot_height,
                )
            )

    def _make_refinement_region(self):
        orientation = int(self.config.geometry.orientation)
        _v_min = [
            self.v_min.x if orientation in [0, 180] else self.v_min.x - self.base_size,
            self.v_min.y - self.base_size if orientation in [0, 180] else self.v_min.y,
            self.v_min.z
        ]
        _v_max = [
            self.v_max.x if orientation in [0, 180] else self.v_max.x + self.base_size,
            self.v_max.y + self.base_size if orientation in [0, 180] else self.v_max.y,
            self.v_max.z
        ]
        self.refine_region = BoxModel(
            name=f"{self.config.valid_id}_box",
            v_min=_v_min,
            v_max=_v_max,
            is_refinement_box=True,
            refinement_level=self.refinement_level
        )

    @property
    def snappyHex_cmd(self):
        return self.box.snappyHex_cmd

    @property
    def createBaffles_cmd(self):
        create_baffles_cmd = ""
        for plane in self.surrounding_planes:
            create_baffles_cmd += plane.createBaffles_cmd
        return create_baffles_cmd

    @property
    def topoSet_cmd(self):
        topo_set_cmd = ""
        for blanking_panel in self.blanking_panel:
            topo_set_cmd += blanking_panel.topoSet_cmd
        for cyclic_patch in self.rack_air_leak_patch_list:
            topo_set_cmd += cyclic_patch.topoSet_cmd
        for server in self.servers:
            topo_set_cmd += server.topoSet_cmd
        return topo_set_cmd

    @property
    def createPatch_cmd(self):
        create_patch_cmd = ""
        for blanking_panel in self.blanking_panel:
            create_patch_cmd += blanking_panel.createPatch_cmd
        for cyclic_patch in self.rack_air_leak_patch_list:
            create_patch_cmd += cyclic_patch.createPatch_cmd
        for server in self.servers:
            create_patch_cmd += server.createPatch_cmd
        return create_patch_cmd


