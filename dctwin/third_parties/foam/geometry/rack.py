"""
Rack entity and its sub-entities.

This module defines the RackEntity class and related sub-entities
for server inlets, outlets, and air leakage regions.

The new leakage modeling approach uses volume mesh (hollow rack with
bool difference) instead of porousBafflePressure boundary condition.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

import numpy as np

from .base import (
    Entity, SubEntity, Vec3, BoundingBox, SolidRegion,
    FaceType, PatchType, Triangle,
    rect_to_triangles, orient_triangles_outward,
)

# Try to import trimesh for boolean operations
try:
    import trimesh
    HAS_TRIMESH = True
except ImportError:
    HAS_TRIMESH = False
    warnings.warn("trimesh not available, hollow rack generation will be disabled")


@dataclass
class ServerConfig:
    """Configuration for a server within a rack."""
    uid: str
    slot_position: int  # Starting slot (0-indexed)
    slot_occupation: int  # Number of U slots occupied
    width: float  # Server width (usually = rack width)
    depth: float  # Server depth (usually = rack depth)
    inlet_height_ratio: float = 0.33  # Fraction of server height for inlet
    outlet_height_ratio: float = 0.33  # Fraction of server height for outlet
    inlet_height: Optional[float] = None  # Absolute inlet height (overrides ratio)
    outlet_height: Optional[float] = None  # Absolute outlet height (overrides ratio)
    inlet_width: Optional[float] = None  # Optional override for inlet width
    outlet_width: Optional[float] = None  # Optional override for outlet width
    inlet_offset_x: float = 0.0  # Offset from rack local x origin
    outlet_offset_x: float = 0.0  # Offset from rack local x origin
    inlet_offset_z: float = 0.0  # Vertical offset within server (from bottom)
    outlet_offset_z: float = 0.0  # Vertical offset within server (from bottom)
    power: float = 0.0  # Input power in watts
    volume_flow_rate: float = 0.0  # Air volume flow rate in m³/s


@dataclass
class OpeningInfo:
    """Information about a server inlet/outlet opening for face extraction.
    
    This is used to identify faces from the boolean result that belong to
    specific inlet/outlet patches. The faces are identified by their position
    (center point must be within the opening bounds) and normal direction.
    """
    name: str  # Patch name (e.g., "Rack_1_Server_1_inlet")
    # Bounding box of the opening (slightly expanded for tolerance)
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    z_min: float
    z_max: float
    # Expected normal direction (unit vector components)
    normal_x: float
    normal_y: float
    normal_z: float


@dataclass
class AirLeakageConfig:
    """Configuration for air leakage regions (volume mesh approach).

    Air leakage is now modeled as volume mesh regions using trimesh boolean
    difference to create hollow areas in the rack. These regions are then
    selected as cellZones and have Darcy-Forchheimer source terms applied
    via fvOptions.

    The leakage regions are automatically computed based on server positions:
    - Safety margin slots at top and bottom are preserved as wall
    - All slots not occupied by servers (excluding safety margins) become leakage zones
    - Leakage zone width equals server width (centered in rack)
    - Leakage zone depth equals rack depth (through-penetration)
    """
    enabled: bool = True
    sigma: float = 0.1  # Porosity/leakage rate (default 10% open)
    safety_margin_slots: int = 1  # Number of U slots for safety margin at top/bottom
    # Legacy fields (kept for backward compatibility, not used in new logic)
    side_width: float = 0.0
    top_height: float = 0.0
    position: str = "top"


@dataclass
class LeakageZone:
    """Represents a leakage zone region in the rack.

    A leakage zone is a contiguous vertical region where air can leak through
    the rack (not occupied by servers, excluding safety margins).
    """
    uid: str  # Unique identifier for this zone
    slot_start: int  # Starting slot (0-indexed)
    slot_end: int  # Ending slot (exclusive)
    width: float  # Width of the zone (same as server width)
    depth: float  # Depth of the zone (same as rack depth)
    rack_uid: str  # Parent rack UID

    @property
    def height(self) -> float:
        """Calculate height based on slot count (assuming 0.05m per slot)."""
        return (self.slot_end - self.slot_start) * 0.05

    def get_slot_height(self, slot_height: float = 0.05) -> float:
        """Calculate height based on slot count with custom slot height."""
        return (self.slot_end - self.slot_start) * slot_height

    @property
    def cellzone_name(self) -> str:
        """Get the cellZone name for this leakage zone.

        Note:
        - Hyphens are replaced with underscores to avoid OpenFOAM parsing issues.
        - A 'z_' prefix is added to ensure the name doesn't start with a digit,
          which would cause OpenFOAM to parse it as a number.
        """
        # Replace hyphens with underscores to avoid OpenFOAM parsing issues
        safe_rack_uid = self.rack_uid.replace("-", "_")
        # Add 'z_' prefix to ensure name doesn't start with a digit
        return f"z_{safe_rack_uid}_leakage_{self.slot_start}_{self.slot_end}"


@dataclass
class ColumnConfig:
    """Configuration for a rack column (for multi-column racks)."""
    uid: str
    width: float
    offset_x: float


class ServerInletSubEntity(SubEntity):
    """
    Sub-entity representing a server's air inlet face.

    The inlet is typically on the front face of the rack where cold air enters.
    """

    def __init__(
        self,
        uid: str,
        slot_position: int,
        slot_occupation: int,
        width: float,
        depth: float,
        inlet_height_ratio: float,
        inlet_height: Optional[float],
        offset_x: float,
        offset_z: float = 0.0,
        slot_height: float = 0.05,
    ):
        super().__init__(
            uid=uid,
            patch_type=PatchType.PATCH,  # Server inlet is a regular patch
            neighbour_patch=None,
            refinement_level=1,
        )
        self.slot_position = slot_position
        self.slot_occupation = slot_occupation
        self.width = width
        self.depth = depth
        self.inlet_height_ratio = inlet_height_ratio
        self.inlet_height = inlet_height
        self.offset_x = offset_x
        self.offset_z = offset_z
        self.slot_height = slot_height

    def get_solid_region(
        self,
        parent_bbox: BoundingBox,
        orientation: float,
    ) -> SolidRegion:
        """Generate the solid region for the server inlet."""
        region = SolidRegion(
            name=self.uid,
            patch_type=self.patch_type,
            neighbour_patch=self.neighbour_patch,
            refinement_level=self.refinement_level,
        )

        # Calculate server dimensions
        server_z_start = parent_bbox.v_min.z + self.slot_position * self.slot_height
        server_height = self.slot_occupation * self.slot_height
        
        # Use full server height for inlet patch to align with recess cutout
        # This ensures no gap between inlet patch and wall boundaries
        inlet_height = server_height

        # Apply vertical offset within server (usually 0)
        z_start = server_z_start + self.offset_z

        # Clamp inlet top to rack top (prevent floating above rack)
        z_top = z_start + inlet_height
        if z_top > parent_bbox.v_max.z:
            z_top = parent_bbox.v_max.z
        inlet_height = z_top - z_start
        if inlet_height <= 0:
            return region  # Skip if no valid inlet region

        # Inlet is on the front face (where air enters)
        # The inlet typically spans the lower portion of the server
        rack_size = parent_bbox.size

        if orientation == 0:
            # Front is -Y direction
            x0 = parent_bbox.v_min.x + self.offset_x
            v00 = Vec3(x0, parent_bbox.v_min.y, z_start)
            v10 = Vec3(x0 + self.width, parent_bbox.v_min.y, z_start)
            v11 = Vec3(x0 + self.width, parent_bbox.v_min.y, z_start + inlet_height)
            v01 = Vec3(x0, parent_bbox.v_min.y, z_start + inlet_height)
            normal = Vec3(0, -1, 0)
        elif orientation == 90:
            # Front is +X direction
            y0 = parent_bbox.v_min.y + self.offset_x
            v00 = Vec3(parent_bbox.v_max.x, y0, z_start)
            v10 = Vec3(parent_bbox.v_max.x, y0 + self.width, z_start)
            v11 = Vec3(parent_bbox.v_max.x, y0 + self.width, z_start + inlet_height)
            v01 = Vec3(parent_bbox.v_max.x, y0, z_start + inlet_height)
            normal = Vec3(1, 0, 0)
        elif orientation == 180:
            # Front is +Y direction
            x0 = parent_bbox.v_max.x - self.offset_x
            v00 = Vec3(x0, parent_bbox.v_max.y, z_start)
            v10 = Vec3(x0 - self.width, parent_bbox.v_max.y, z_start)
            v11 = Vec3(x0 - self.width, parent_bbox.v_max.y, z_start + inlet_height)
            v01 = Vec3(x0, parent_bbox.v_max.y, z_start + inlet_height)
            normal = Vec3(0, 1, 0)
        else:  # orientation == 270
            # Front is -X direction
            y0 = parent_bbox.v_max.y - self.offset_x
            v00 = Vec3(parent_bbox.v_min.x, y0, z_start)
            v10 = Vec3(parent_bbox.v_min.x, y0 - self.width, z_start)
            v11 = Vec3(parent_bbox.v_min.x, y0 - self.width, z_start + inlet_height)
            v01 = Vec3(parent_bbox.v_min.x, y0, z_start + inlet_height)
            normal = Vec3(-1, 0, 0)

        triangles = rect_to_triangles(v00, v10, v11, v01)
        triangles = orient_triangles_outward(triangles, normal)
        region.add_triangles(triangles)

        return region


class ServerOutletSubEntity(SubEntity):
    """
    Sub-entity representing a server's air outlet face.

    The outlet is typically on the rear face of the rack where hot air exits.
    """

    def __init__(
        self,
        uid: str,
        slot_position: int,
        slot_occupation: int,
        width: float,
        depth: float,
        outlet_height_ratio: float,
        outlet_height: Optional[float],
        offset_x: float,
        offset_z: float = 0.0,
        slot_height: float = 0.05,
    ):
        super().__init__(
            uid=uid,
            patch_type=PatchType.PATCH,  # Server outlet is a regular patch
            neighbour_patch=None,
            refinement_level=1,
        )
        self.slot_position = slot_position
        self.slot_occupation = slot_occupation
        self.width = width
        self.depth = depth
        self.outlet_height_ratio = outlet_height_ratio
        self.outlet_height = outlet_height
        self.offset_x = offset_x
        self.offset_z = offset_z
        self.slot_height = slot_height

    def get_solid_region(
        self,
        parent_bbox: BoundingBox,
        orientation: float,
    ) -> SolidRegion:
        """Generate the solid region for the server outlet."""
        region = SolidRegion(
            name=self.uid,
            patch_type=self.patch_type,
            neighbour_patch=self.neighbour_patch,
            refinement_level=self.refinement_level,
        )

        # Calculate server dimensions
        server_z_start = parent_bbox.v_min.z + self.slot_position * self.slot_height
        server_height = self.slot_occupation * self.slot_height
        
        # Use full server height for outlet patch to align with recess cutout
        # This ensures no gap between outlet patch and wall boundaries
        outlet_height = server_height

        # Apply vertical offset within server (usually 0)
        z_start = server_z_start + self.offset_z

        # Clamp outlet top to rack top (prevent floating above rack)
        z_top = z_start + outlet_height
        if z_top > parent_bbox.v_max.z:
            z_top = parent_bbox.v_max.z
        outlet_height = z_top - z_start
        if outlet_height <= 0:
            return region  # Skip if no valid outlet region

        if orientation == 0:
            # Rear is +Y direction
            x0 = parent_bbox.v_max.x - self.offset_x
            v00 = Vec3(x0, parent_bbox.v_max.y, z_start)
            v10 = Vec3(x0 - self.width, parent_bbox.v_max.y, z_start)
            v11 = Vec3(x0 - self.width, parent_bbox.v_max.y, z_start + outlet_height)
            v01 = Vec3(x0, parent_bbox.v_max.y, z_start + outlet_height)
            normal = Vec3(0, 1, 0)
        elif orientation == 90:
            # Rear is -X direction
            y0 = parent_bbox.v_max.y - self.offset_x
            v00 = Vec3(parent_bbox.v_min.x, y0, z_start)
            v10 = Vec3(parent_bbox.v_min.x, y0 - self.width, z_start)
            v11 = Vec3(parent_bbox.v_min.x, y0 - self.width, z_start + outlet_height)
            v01 = Vec3(parent_bbox.v_min.x, y0, z_start + outlet_height)
            normal = Vec3(-1, 0, 0)
        elif orientation == 180:
            # Rear is -Y direction
            x0 = parent_bbox.v_min.x + self.offset_x
            v00 = Vec3(x0, parent_bbox.v_min.y, z_start)
            v10 = Vec3(x0 + self.width, parent_bbox.v_min.y, z_start)
            v11 = Vec3(x0 + self.width, parent_bbox.v_min.y, z_start + outlet_height)
            v01 = Vec3(x0, parent_bbox.v_min.y, z_start + outlet_height)
            normal = Vec3(0, -1, 0)
        else:  # orientation == 270
            # Rear is +X direction
            y0 = parent_bbox.v_min.y + self.offset_x
            v00 = Vec3(parent_bbox.v_max.x, y0, z_start)
            v10 = Vec3(parent_bbox.v_max.x, y0 + self.width, z_start)
            v11 = Vec3(parent_bbox.v_max.x, y0 + self.width, z_start + outlet_height)
            v01 = Vec3(parent_bbox.v_max.x, y0, z_start + outlet_height)
            normal = Vec3(1, 0, 0)

        triangles = rect_to_triangles(v00, v10, v11, v01)
        triangles = orient_triangles_outward(triangles, normal)
        region.add_triangles(triangles)

        return region


class AirLeakageSubEntity(SubEntity):
    """
    Sub-entity representing air leakage regions on rack inlet/outlet faces.

    Air leakage allows bypass airflow around servers, typically at the
    top or sides of the rack face.
    """

    def __init__(
        self,
        uid: str,
        width: float,
        face: str,  # "inlet" or "outlet"
        position: str,  # "top", "left", "right"
        z_start: float,
        z_end: float,
        rack_width: float,
        is_master: bool = True,
    ):
        neighbour = uid.replace("_master", "_slave") if is_master else uid.replace("_slave", "_master")
        super().__init__(
            uid=uid,
            patch_type=PatchType.CYCLIC,
            neighbour_patch=neighbour,
            refinement_level=1,  # Keep simple refinement for leakage surfaces
        )
        self.width = width
        self.face = face
        self.position = position
        self.z_start = z_start
        self.z_end = z_end
        self.rack_width = rack_width
        self.is_master = is_master

    def get_solid_region(
        self,
        parent_bbox: BoundingBox,
        orientation: float,
    ) -> SolidRegion:
        """Generate the solid region for the air leakage area."""
        region = SolidRegion(
            name=self.uid,
            patch_type=self.patch_type,
            neighbour_patch=self.neighbour_patch,
            refinement_level=self.refinement_level,
        )

        # Determine face position based on orientation and face type
        is_inlet = (self.face == "inlet")

        # Calculate leakage region bounds
        if self.position == "top":
            # Horizontal band between z_start and z_end
            leak_width = self.rack_width
            x_offset = 0.0
        elif self.position == "left":
            # Vertical strip on the left side of rack face
            leak_width = self.width
            x_offset = 0.0
        elif self.position == "right":
            # Vertical strip on the right side of rack face
            leak_width = self.width
            x_offset = self.rack_width - leak_width
        else:
            leak_width = self.rack_width
            x_offset = 0.0

        z_bottom = parent_bbox.v_min.z + self.z_start
        z_top = parent_bbox.v_min.z + self.z_end
        if z_top <= z_bottom or leak_width <= 0:
            return region

        if orientation == 0:
            if is_inlet:
                # Front face (-Y)
                y = parent_bbox.v_min.y
                normal = Vec3(0, -1, 0)
                v00 = Vec3(parent_bbox.v_min.x + x_offset, y, z_bottom)
                v10 = Vec3(parent_bbox.v_min.x + x_offset + leak_width, y, z_bottom)
                v11 = Vec3(parent_bbox.v_min.x + x_offset + leak_width, y, z_top)
                v01 = Vec3(parent_bbox.v_min.x + x_offset, y, z_top)
            else:
                # Rear face (+Y) - same x position as inlet for cyclicAMI matching
                y = parent_bbox.v_max.y
                normal = Vec3(0, 1, 0)
                v00 = Vec3(parent_bbox.v_min.x + x_offset + leak_width, y, z_bottom)
                v10 = Vec3(parent_bbox.v_min.x + x_offset, y, z_bottom)
                v11 = Vec3(parent_bbox.v_min.x + x_offset, y, z_top)
                v01 = Vec3(parent_bbox.v_min.x + x_offset + leak_width, y, z_top)
        elif orientation == 90:
            if is_inlet:
                # Front face (+X)
                x = parent_bbox.v_max.x
                normal = Vec3(1, 0, 0)
                v00 = Vec3(x, parent_bbox.v_min.y + x_offset, z_bottom)
                v10 = Vec3(x, parent_bbox.v_min.y + x_offset + leak_width, z_bottom)
                v11 = Vec3(x, parent_bbox.v_min.y + x_offset + leak_width, z_top)
                v01 = Vec3(x, parent_bbox.v_min.y + x_offset, z_top)
            else:
                # Rear face (-X) - same y position as inlet for cyclicAMI matching
                x = parent_bbox.v_min.x
                normal = Vec3(-1, 0, 0)
                v00 = Vec3(x, parent_bbox.v_min.y + x_offset + leak_width, z_bottom)
                v10 = Vec3(x, parent_bbox.v_min.y + x_offset, z_bottom)
                v11 = Vec3(x, parent_bbox.v_min.y + x_offset, z_top)
                v01 = Vec3(x, parent_bbox.v_min.y + x_offset + leak_width, z_top)
        elif orientation == 180:
            if is_inlet:
                # Front face (+Y)
                y = parent_bbox.v_max.y
                normal = Vec3(0, 1, 0)
                v00 = Vec3(parent_bbox.v_max.x - x_offset, y, z_bottom)
                v10 = Vec3(parent_bbox.v_max.x - x_offset - leak_width, y, z_bottom)
                v11 = Vec3(parent_bbox.v_max.x - x_offset - leak_width, y, z_top)
                v01 = Vec3(parent_bbox.v_max.x - x_offset, y, z_top)
            else:
                # Rear face (-Y) - same x position as inlet for cyclicAMI matching
                y = parent_bbox.v_min.y
                normal = Vec3(0, -1, 0)
                v00 = Vec3(parent_bbox.v_max.x - x_offset - leak_width, y, z_bottom)
                v10 = Vec3(parent_bbox.v_max.x - x_offset, y, z_bottom)
                v11 = Vec3(parent_bbox.v_max.x - x_offset, y, z_top)
                v01 = Vec3(parent_bbox.v_max.x - x_offset - leak_width, y, z_top)
        else:  # orientation == 270
            if is_inlet:
                # Front face (-X)
                x = parent_bbox.v_min.x
                normal = Vec3(-1, 0, 0)
                v00 = Vec3(x, parent_bbox.v_max.y - x_offset, z_bottom)
                v10 = Vec3(x, parent_bbox.v_max.y - x_offset - leak_width, z_bottom)
                v11 = Vec3(x, parent_bbox.v_max.y - x_offset - leak_width, z_top)
                v01 = Vec3(x, parent_bbox.v_max.y - x_offset, z_top)
            else:
                # Rear face (+X) - same y position as inlet for cyclicAMI matching
                x = parent_bbox.v_max.x
                normal = Vec3(1, 0, 0)
                v00 = Vec3(x, parent_bbox.v_max.y - x_offset - leak_width, z_bottom)
                v10 = Vec3(x, parent_bbox.v_max.y - x_offset, z_bottom)
                v11 = Vec3(x, parent_bbox.v_max.y - x_offset, z_top)
                v01 = Vec3(x, parent_bbox.v_max.y - x_offset - leak_width, z_top)

        triangles = rect_to_triangles(v00, v10, v11, v01)
        triangles = orient_triangles_outward(triangles, normal)
        region.add_triangles(triangles)

        return region


class RackEntity(Entity):
    """
    Entity representing a server rack.

    A rack contains servers with inlets and outlets, and may have
    air leakage regions around the server faces.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
        slot_count: int = 46,
        slot_height: float = 0.05,  # 1U = 0.05m
        first_slot_offset: float = 0.0,
        servers: Optional[List[ServerConfig]] = None,
        air_leakage: Optional[AirLeakageConfig] = None,
        has_blanking_panel: bool = True,
        columns: Optional[List[ColumnConfig]] = None,
    ):
        super().__init__(uid, location, size, orientation)
        self.slot_count = slot_count
        self.slot_height = slot_height
        self.first_slot_offset = first_slot_offset
        self.servers = servers or []
        self.air_leakage = air_leakage or AirLeakageConfig()
        self.has_blanking_panel = has_blanking_panel
        self.columns = columns or []

        # IMPORTANT: validate() MUST be called BEFORE _create_sub_entities()
        # to ensure server width constraints are applied before sub-entities use them
        self._validate_and_fix_server_widths()
        self._create_sub_entities()

    def _create_sub_entities(self) -> None:
        """Create sub-entities for servers.

        Note: Air leakage is NOT modeled as STL holes (sub-entities) anymore.
        Instead, it's modeled using createBaffles to create internal cyclic patches
        that work correctly with porousBafflePressure boundary condition.
        The left/right walls will have gaps at air_leak positions (see get_solid_regions).
        """
        # Create server inlet/outlet sub-entities
        for server in self.servers:
            inlet_width = server.inlet_width if server.inlet_width is not None else server.width
            outlet_width = server.outlet_width if server.outlet_width is not None else server.width

            # Create inlet sub-entity
            inlet = ServerInletSubEntity(
                uid=f"{self.uid}_server_{server.uid}_inlet",
                slot_position=server.slot_position,
                slot_occupation=server.slot_occupation,
                width=inlet_width,
                depth=server.depth,
                inlet_height_ratio=server.inlet_height_ratio,
                inlet_height=server.inlet_height,
                offset_x=server.inlet_offset_x,
                offset_z=server.inlet_offset_z + self.first_slot_offset,
                slot_height=self.slot_height,
            )
            self.add_sub_entity(inlet)

            # Create outlet sub-entity
            outlet = ServerOutletSubEntity(
                uid=f"{self.uid}_server_{server.uid}_outlet",
                slot_position=server.slot_position,
                slot_occupation=server.slot_occupation,
                width=outlet_width,
                depth=server.depth,
                outlet_height_ratio=server.outlet_height_ratio,
                outlet_height=server.outlet_height,
                offset_x=server.outlet_offset_x,
                offset_z=server.outlet_offset_z + self.first_slot_offset,
                slot_height=self.slot_height,
            )
            self.add_sub_entity(outlet)

        # Air leakage sub-entities are NO LONGER created here.
        # Air leakage is modeled through gaps in left/right walls + createBaffles.

    def _validate_and_fix_server_widths(self) -> None:
        """Validate and fix server widths and offsets before creating sub-entities.
        
        This MUST be called BEFORE _create_sub_entities() to ensure:
        1. Server width constraints (max 80% of rack width) are applied
        2. Server is centered in the rack (offset_x is corrected)
        """
        max_allowed_width = self.size.x * 0.8  # Server width cannot exceed 80% of rack width

        for server in self.servers:
            # Check and enforce server width constraint (max 80% of rack width)
            if server.width > max_allowed_width:
                warnings.warn(
                    f"Server '{server.uid}' width ({server.width}) exceeds 80% of rack width "
                    f"({max_allowed_width}). Forcing to {max_allowed_width}."
                )
                server.width = max_allowed_width
            
            # Also adjust inlet/outlet width if they exceed the limit
            if server.inlet_width is not None and server.inlet_width > max_allowed_width:
                warnings.warn(
                    f"Server '{server.uid}' inlet_width ({server.inlet_width}) exceeds 80% of rack width. "
                    f"Forcing to {max_allowed_width}."
                )
                server.inlet_width = max_allowed_width
            if server.outlet_width is not None and server.outlet_width > max_allowed_width:
                warnings.warn(
                    f"Server '{server.uid}' outlet_width ({server.outlet_width}) exceeds 80% of rack width. "
                    f"Forcing to {max_allowed_width}."
                )
                server.outlet_width = max_allowed_width

            # Calculate centered offset for server inlet/outlet
            # Server should be centered in the rack front/back face
            effective_inlet_width = server.inlet_width if server.inlet_width is not None else server.width
            effective_outlet_width = server.outlet_width if server.outlet_width is not None else server.width
            
            # Centered offset = (rack_width - server_width) / 2
            centered_inlet_offset = (self.size.x - effective_inlet_width) / 2
            centered_outlet_offset = (self.size.x - effective_outlet_width) / 2
            
            # Force server to be centered (override JSON offset)
            if abs(server.inlet_offset_x - centered_inlet_offset) > 0.001:
                server.inlet_offset_x = centered_inlet_offset
            if abs(server.outlet_offset_x - centered_outlet_offset) > 0.001:
                server.outlet_offset_x = centered_outlet_offset

    def validate(self) -> None:
        """Validate rack configuration."""
        super().validate()

        # Safety boundary: slot 0 and slot (slot_count - 1) are reserved
        # Servers can only occupy slots 1 to (slot_count - 2)
        safety_slot_bottom = 0  # Reserved slot at bottom
        safety_slot_top = self.slot_count - 1  # Reserved slot at top

        # Validate server configurations
        max_allowed_width = self.size.x * 0.8

        for server in self.servers:
            # Check slot bounds
            if server.slot_position < 0:
                raise ValueError(
                    f"Server '{server.uid}' in rack '{self.uid}' has invalid "
                    f"slot_position: {server.slot_position}"
                )
            if server.slot_position + server.slot_occupation > self.slot_count:
                raise ValueError(
                    f"Server '{server.uid}' in rack '{self.uid}' exceeds slot count: "
                    f"position {server.slot_position} + occupation {server.slot_occupation} "
                    f"> slot_count {self.slot_count}"
                )
            
            # Check safety boundary: server cannot occupy slot 0 or last slot
            server_slot_end = server.slot_position + server.slot_occupation - 1
            if server.slot_position <= safety_slot_bottom:
                raise ValueError(
                    f"Server '{server.uid}' in rack '{self.uid}' occupies safety boundary: "
                    f"slot_position={server.slot_position} <= {safety_slot_bottom} (bottom safety slot). "
                    f"Server must start at slot >= 1."
                )
            if server_slot_end >= safety_slot_top:
                raise ValueError(
                    f"Server '{server.uid}' in rack '{self.uid}' occupies safety boundary: "
                    f"slot_end={server_slot_end} >= {safety_slot_top} (top safety slot). "
                    f"Server must end at slot <= {safety_slot_top - 1}."
                )

            # Verify server width is within limits (should already be fixed by _validate_and_fix_server_widths)
            max_width = server.width
            inlet_width = server.inlet_width if server.inlet_width is not None else server.width
            outlet_width = server.outlet_width if server.outlet_width is not None else server.width
            max_width = max(max_width, inlet_width, outlet_width)
            if max_width > self.size.x:
                raise ValueError(
                    f"Server '{server.uid}' width ({max_width}) exceeds "
                    f"rack width ({self.size.x})"
                )

            if server.inlet_height is not None:
                server_height = server.slot_occupation * self.slot_height
                if server.inlet_height > server_height:
                    raise ValueError(
                        f"Server '{server.uid}' inlet height ({server.inlet_height}) exceeds "
                        f"server height ({server_height})"
                    )
            if server.outlet_height is not None:
                server_height = server.slot_occupation * self.slot_height
                if server.outlet_height > server_height:
                    raise ValueError(
                        f"Server '{server.uid}' outlet height ({server.outlet_height}) exceeds "
                        f"server height ({server_height})"
                    )

    def get_server_width(self) -> float:
        """Get the server width (all servers must have same width for leakage zones).

        Returns:
            Server width, or default to 80% of rack width if no servers.
        """
        if not self.servers:
            return self.size.x * 0.8

        # All servers should have the same width for consistent leakage zones
        widths = set()
        for server in self.servers:
            width = server.inlet_width if server.inlet_width is not None else server.width
            widths.add(width)

        if len(widths) > 1:
            warnings.warn(
                f"Rack '{self.uid}' has servers with different widths: {widths}. "
                f"Using minimum width for leakage zones."
            )
            return min(widths)

        return widths.pop() if widths else self.size.x * 0.8

    def get_leakage_zones(self) -> List[LeakageZone]:
        """Calculate leakage zones based on server positions.

        Leakage zones are all slots not occupied by servers, excluding
        safety margin slots at top and bottom.

        Returns:
            List of LeakageZone objects.
        """
        if not self.air_leakage.enabled:
            return []

        safety_margin = self.air_leakage.safety_margin_slots
        server_width = self.get_server_width()

        # Effective slot range (excluding safety margins)
        effective_start = safety_margin
        effective_end = self.slot_count - safety_margin

        if effective_end <= effective_start:
            return []  # No space for leakage zones

        # Create a bitmap of occupied slots
        occupied = [False] * self.slot_count
        for server in self.servers:
            for i in range(server.slot_position, server.slot_position + server.slot_occupation):
                if 0 <= i < self.slot_count:
                    occupied[i] = True

        # Find contiguous unoccupied regions within effective range
        zones = []
        zone_start = None

        for slot in range(effective_start, effective_end):
            if not occupied[slot]:
                if zone_start is None:
                    zone_start = slot
            else:
                if zone_start is not None:
                    # End of a leakage zone
                    zones.append(LeakageZone(
                        uid=f"{self.uid}_leakage_{zone_start}_{slot}",
                        slot_start=zone_start,
                        slot_end=slot,
                        width=server_width,
                        depth=self.size.y,
                        rack_uid=self.uid,
                    ))
                    zone_start = None

        # Handle zone at the end
        if zone_start is not None:
            zones.append(LeakageZone(
                uid=f"{self.uid}_leakage_{zone_start}_{effective_end}",
                slot_start=zone_start,
                slot_end=effective_end,
                width=server_width,
                depth=self.size.y,
                rack_uid=self.uid,
            ))

        return zones

    def _get_leakage_zone_bbox(self, zone: LeakageZone) -> Tuple[Vec3, Vec3]:
        """Get the bounding box of a leakage zone in global coordinates.

        Args:
            zone: The LeakageZone object.

        Returns:
            Tuple of (v_min, v_max) in global coordinates.
        """
        bbox = self.bounding_box

        # Calculate zone dimensions
        zone_height = zone.get_slot_height(self.slot_height)
        zone_z_start = bbox.v_min.z + self.first_slot_offset + zone.slot_start * self.slot_height

        # Center the zone width in the rack
        width_offset = (self.size.x - zone.width) / 2

        if self.orientation == 0:
            # Front is -Y, Rear is +Y
            v_min = Vec3(
                bbox.v_min.x + width_offset,
                bbox.v_min.y,
                zone_z_start,
            )
            v_max = Vec3(
                bbox.v_min.x + width_offset + zone.width,
                bbox.v_max.y,
                zone_z_start + zone_height,
            )
        elif self.orientation == 90:
            # Front is +X, Rear is -X
            v_min = Vec3(
                bbox.v_min.x,
                bbox.v_min.y + width_offset,
                zone_z_start,
            )
            v_max = Vec3(
                bbox.v_max.x,
                bbox.v_min.y + width_offset + zone.width,
                zone_z_start + zone_height,
            )
        elif self.orientation == 180:
            # Front is +Y, Rear is -Y
            v_min = Vec3(
                bbox.v_max.x - width_offset - zone.width,
                bbox.v_min.y,
                zone_z_start,
            )
            v_max = Vec3(
                bbox.v_max.x - width_offset,
                bbox.v_max.y,
                zone_z_start + zone_height,
            )
        else:  # 270
            # Front is -X, Rear is +X
            v_min = Vec3(
                bbox.v_min.x,
                bbox.v_max.y - width_offset - zone.width,
                zone_z_start,
            )
            v_max = Vec3(
                bbox.v_max.x,
                bbox.v_max.y - width_offset,
                zone_z_start + zone_height,
            )

        return v_min, v_max

    def generate_hollow_rack_stl(self, output_dir: Path) -> Tuple[Optional[Path], List[Path]]:
        """Generate hollow rack STL using trimesh boolean difference.

        This creates:
        1. A hollow rack STL with leakage zones carved out (watertight from boolean)
        2. Separate STL files for each leakage zone (for cellZone selection)

        The hollow rack STL contains:
        - Wall solid: the boolean difference result (rack - leakage zones)
        - Server inlet/outlet solids: separate patches for boundary conditions

        Args:
            output_dir: Directory to write STL files.

        Returns:
            Tuple of (hollow_rack_stl_path, list_of_leakage_stl_paths).
            Returns (None, []) if trimesh is not available or no leakage zones.
        """
        if not HAS_TRIMESH:
            warnings.warn("trimesh not available, cannot generate hollow rack STL")
            return None, []

        leakage_zones = self.get_leakage_zones()
        if not leakage_zones:
            return None, []

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        bbox = self.bounding_box

        # Create the base rack box
        rack_center = (
            (bbox.v_min.x + bbox.v_max.x) / 2,
            (bbox.v_min.y + bbox.v_max.y) / 2,
            (bbox.v_min.z + bbox.v_max.z) / 2,
        )
        rack_extents = (
            bbox.v_max.x - bbox.v_min.x,
            bbox.v_max.y - bbox.v_min.y,
            bbox.v_max.z - bbox.v_min.z,
        )
        rack_mesh = trimesh.creation.box(extents=rack_extents)
        rack_mesh.apply_translation(rack_center)

        # Clean the rack mesh
        rack_mesh.merge_vertices()
        rack_mesh.update_faces(rack_mesh.nondegenerate_faces())
        rack_mesh.fix_normals()

        # Create leakage zone boxes and subtract them
        leakage_stl_paths = []
        leakage_meshes = []

        for zone in leakage_zones:
            v_min, v_max = self._get_leakage_zone_bbox(zone)

            zone_center = (
                (v_min.x + v_max.x) / 2,
                (v_min.y + v_max.y) / 2,
                (v_min.z + v_max.z) / 2,
            )
            zone_extents = (
                v_max.x - v_min.x,
                v_max.y - v_min.y,
                v_max.z - v_min.z,
            )

            # Extend slightly in the through-direction to ensure clean boolean
            epsilon = 1e-4
            if self.orientation in [0, 180]:
                zone_extents = (zone_extents[0], zone_extents[1] + 2 * epsilon, zone_extents[2])
            else:
                zone_extents = (zone_extents[0] + 2 * epsilon, zone_extents[1], zone_extents[2])

            zone_mesh = trimesh.creation.box(extents=zone_extents)
            zone_mesh.apply_translation(zone_center)
            
            # Clean the zone mesh
            zone_mesh.merge_vertices()
            zone_mesh.update_faces(zone_mesh.nondegenerate_faces())
            zone_mesh.fix_normals()
            
            leakage_meshes.append(zone_mesh)

            # Save leakage zone STL (for cellZone selection) - use original size without epsilon
            original_zone_mesh = trimesh.creation.box(extents=(
                v_max.x - v_min.x,
                v_max.y - v_min.y,
                v_max.z - v_min.z,
            ))
            original_zone_mesh.apply_translation(zone_center)
            zone_stl_path = output_dir / f"{zone.uid}.stl"
            original_zone_mesh.export(zone_stl_path, file_type='stl_ascii')
            leakage_stl_paths.append(zone_stl_path)

        # For each server, create inlet/outlet recesses based on actual server configuration
        # The recess depth is calculated to ensure inlet and outlet don't connect through the rack
        # 
        # Key principle: Use server.depth as the actual server depth, and create recesses
        # that are deep enough to reach the server body but don't penetrate through
        
        server_recess_meshes = []
        opening_infos: List[OpeningInfo] = []  # For extracting faces from boolean result
        
        for server in self.servers:
            # Calculate server position in Z direction
            slot_bottom_z = bbox.v_min.z + server.slot_position * self.slot_height
            slot_top_z = slot_bottom_z + server.slot_occupation * self.slot_height
            server_height = slot_top_z - slot_bottom_z
            
            # Get server inlet/outlet dimensions from configuration
            inlet_width = server.inlet_width if server.inlet_width is not None else server.width
            outlet_width = server.outlet_width if server.outlet_width is not None else server.width
            
            # Calculate inlet/outlet heights based on configured ratio or explicit height
            if server.inlet_height is not None:
                inlet_height = server.inlet_height
            else:
                inlet_height = server_height * server.inlet_height_ratio
            
            if server.outlet_height is not None:
                outlet_height = server.outlet_height
            else:
                outlet_height = server_height * server.outlet_height_ratio
            
            # Inlet/outlet Z positions (starting from bottom of server, plus any offset)
            inlet_offset_z = server.inlet_offset_z if server.inlet_offset_z else 0
            outlet_offset_z = server.outlet_offset_z if server.outlet_offset_z else 0
            
            inlet_z_start = slot_bottom_z + inlet_offset_z
            inlet_z_end = inlet_z_start + inlet_height
            outlet_z_start = slot_bottom_z + outlet_offset_z
            outlet_z_end = outlet_z_start + outlet_height
            
            epsilon = 1e-4
            
            # Get rack depth based on orientation
            # Orientation defines which axis is the "through" direction (front to rear)
            if self.orientation in [90, 270]:
                rack_depth = self.size.y  # Y is the depth direction
            else:  # 0, 180
                rack_depth = self.size.x  # X is the depth direction
            
            # SERVER = BLACK BOX MODEL
            # Server interior stays SOLID
            # But we need SHALLOW recesses on front/rear faces to create openings
            # for inlet/outlet patches. These recesses are very thin (just enough
            # to create surface openings, not penetrating the server volume).
            
            # Shallow recess depth - just enough to create a surface opening
            shallow_depth = 0.001  # 1mm - just for surface opening
            
            # Calculate positions based on orientation
            # Create TWO shallow recesses: one for inlet (front), one for outlet (rear)
            # Also track normal directions for face extraction from boolean result
            if self.orientation == 90:
                # Front at X_max, Rear at X_min, width along Y
                center_y = (bbox.v_min.y + bbox.v_max.y) / 2
                channel_z_center = (slot_bottom_z + slot_top_z) / 2
                
                # Inlet recess (at front face, X_max)
                inlet_center = (
                    bbox.v_max.x - shallow_depth / 2 + epsilon,
                    center_y,
                    channel_z_center,
                )
                inlet_extents = (shallow_depth + 2 * epsilon, inlet_width, server_height)
                inlet_normal = (1.0, 0.0, 0.0)  # Outward normal at X_max
                
                # Outlet recess (at rear face, X_min)
                outlet_center = (
                    bbox.v_min.x + shallow_depth / 2 - epsilon,
                    center_y,
                    channel_z_center,
                )
                outlet_extents = (shallow_depth + 2 * epsilon, outlet_width, server_height)
                outlet_normal = (-1.0, 0.0, 0.0)  # Outward normal at X_min
                
            elif self.orientation == 270:
                # Front at X_min, Rear at X_max, width along Y
                center_y = (bbox.v_min.y + bbox.v_max.y) / 2
                channel_z_center = (slot_bottom_z + slot_top_z) / 2
                
                inlet_center = (
                    bbox.v_min.x + shallow_depth / 2 - epsilon,
                    center_y,
                    channel_z_center,
                )
                inlet_extents = (shallow_depth + 2 * epsilon, inlet_width, server_height)
                inlet_normal = (-1.0, 0.0, 0.0)  # Outward normal at X_min
                
                outlet_center = (
                    bbox.v_max.x - shallow_depth / 2 + epsilon,
                    center_y,
                    channel_z_center,
                )
                outlet_extents = (shallow_depth + 2 * epsilon, outlet_width, server_height)
                outlet_normal = (1.0, 0.0, 0.0)  # Outward normal at X_max
                
            elif self.orientation == 0:
                # Front at Y_min, Rear at Y_max, width along X
                center_x = (bbox.v_min.x + bbox.v_max.x) / 2
                channel_z_center = (slot_bottom_z + slot_top_z) / 2
                
                inlet_center = (
                    center_x,
                    bbox.v_min.y + shallow_depth / 2 - epsilon,
                    channel_z_center,
                )
                inlet_extents = (inlet_width, shallow_depth + 2 * epsilon, server_height)
                inlet_normal = (0.0, -1.0, 0.0)  # Outward normal at Y_min
                
                outlet_center = (
                    center_x,
                    bbox.v_max.y - shallow_depth / 2 + epsilon,
                    channel_z_center,
                )
                outlet_extents = (outlet_width, shallow_depth + 2 * epsilon, server_height)
                outlet_normal = (0.0, 1.0, 0.0)  # Outward normal at Y_max
                
            else:  # 180
                # Front at Y_max, Rear at Y_min, width along X
                center_x = (bbox.v_min.x + bbox.v_max.x) / 2
                channel_z_center = (slot_bottom_z + slot_top_z) / 2
                
                inlet_center = (
                    center_x,
                    bbox.v_max.y - shallow_depth / 2 + epsilon,
                    channel_z_center,
                )
                inlet_extents = (inlet_width, shallow_depth + 2 * epsilon, server_height)
                inlet_normal = (0.0, 1.0, 0.0)  # Outward normal at Y_max
                
                outlet_center = (
                    center_x,
                    bbox.v_min.y + shallow_depth / 2 - epsilon,
                    channel_z_center,
                )
                outlet_extents = (outlet_width, shallow_depth + 2 * epsilon, server_height)
                outlet_normal = (0.0, -1.0, 0.0)  # Outward normal at Y_min
            
            # Create shallow recess meshes for surface openings
            inlet_recess = trimesh.creation.box(extents=inlet_extents)
            inlet_recess.apply_translation(inlet_center)
            inlet_recess.merge_vertices()
            inlet_recess.fix_normals()
            server_recess_meshes.append(inlet_recess)
            
            outlet_recess = trimesh.creation.box(extents=outlet_extents)
            outlet_recess.apply_translation(outlet_center)
            outlet_recess.merge_vertices()
            outlet_recess.fix_normals()
            server_recess_meshes.append(outlet_recess)
            
            # Create OpeningInfo for face extraction from boolean result
            # Use a small tolerance for face matching
            face_tol = 0.01  # 1cm tolerance for face center matching
            
            # Find the inlet sub-entity to get its name (use solid_name for sanitized version)
            inlet_name = None
            outlet_name = None
            for sub_entity in self.sub_entities:
                if isinstance(sub_entity, ServerInletSubEntity):
                    if sub_entity.slot_position == server.slot_position:
                        inlet_name = sub_entity.solid_name  # Use sanitized name
                elif isinstance(sub_entity, ServerOutletSubEntity):
                    if sub_entity.slot_position == server.slot_position:
                        outlet_name = sub_entity.solid_name  # Use sanitized name
            
            if inlet_name:
                opening_infos.append(OpeningInfo(
                    name=inlet_name,
                    x_min=inlet_center[0] - inlet_extents[0] / 2 - face_tol,
                    x_max=inlet_center[0] + inlet_extents[0] / 2 + face_tol,
                    y_min=inlet_center[1] - inlet_extents[1] / 2 - face_tol,
                    y_max=inlet_center[1] + inlet_extents[1] / 2 + face_tol,
                    z_min=inlet_center[2] - inlet_extents[2] / 2 - face_tol,
                    z_max=inlet_center[2] + inlet_extents[2] / 2 + face_tol,
                    normal_x=inlet_normal[0],
                    normal_y=inlet_normal[1],
                    normal_z=inlet_normal[2],
                ))
            
            if outlet_name:
                opening_infos.append(OpeningInfo(
                    name=outlet_name,
                    x_min=outlet_center[0] - outlet_extents[0] / 2 - face_tol,
                    x_max=outlet_center[0] + outlet_extents[0] / 2 + face_tol,
                    y_min=outlet_center[1] - outlet_extents[1] / 2 - face_tol,
                    y_max=outlet_center[1] + outlet_extents[1] / 2 + face_tol,
                    z_min=outlet_center[2] - outlet_extents[2] / 2 - face_tol,
                    z_max=outlet_center[2] + outlet_extents[2] / 2 + face_tol,
                    normal_x=outlet_normal[0],
                    normal_y=outlet_normal[1],
                    normal_z=outlet_normal[2],
                ))

        # Perform boolean operations:
        # 1. rack - leakage zones - server recesses
        try:
            hollow_mesh = rack_mesh.copy()
            
            # Subtract leakage zones
            for zone_mesh in leakage_meshes:
                hollow_mesh = trimesh.boolean.difference([hollow_mesh, zone_mesh], engine="manifold")
            
            # Subtract server inlet/outlet recesses
            for recess_mesh in server_recess_meshes:
                hollow_mesh = trimesh.boolean.difference([hollow_mesh, recess_mesh], engine="manifold")

            # Clean result
            hollow_mesh.merge_vertices()
            hollow_mesh.update_faces(hollow_mesh.nondegenerate_faces())
            hollow_mesh.fix_normals()

            if not hollow_mesh.is_watertight:
                warnings.warn(f"Hollow rack mesh for '{self.uid}' is not watertight after boolean")

        except Exception as e:
            warnings.warn(f"Boolean difference failed for rack '{self.uid}': {e}")
            return None, leakage_stl_paths

        # Write hollow rack STL with multiple solids:
        # 1. Wall solid from boolean result
        # 2. Server inlet/outlet solids (extracted from boolean result based on opening_infos)
        hollow_stl_path = output_dir / f"{self.uid}_hollow.stl"
        self._write_hollow_rack_stl_from_boolean(hollow_stl_path, hollow_mesh, leakage_zones, opening_infos)

        return hollow_stl_path, leakage_stl_paths

    def _write_hollow_rack_stl_from_boolean(
        self, filepath: Path, hollow_mesh: "trimesh.Trimesh", leakage_zones: List[LeakageZone],
        opening_infos: List[OpeningInfo]
    ) -> None:
        """Write hollow rack STL using boolean result, extracting inlet/outlet faces.

        This method extracts server inlet/outlet faces directly from the boolean
        result mesh, ensuring that the face vertices are exactly the same as the
        wall hole boundaries. This eliminates coordinate mismatches that cause
        sawtooth patch edges.

        Args:
            filepath: Path to write the STL file.
            hollow_mesh: The trimesh result from boolean difference.
            leakage_zones: List of leakage zones (for reference).
            opening_infos: List of OpeningInfo defining inlet/outlet locations and normals.
        """
        from .stl_writer import STLWriter

        solids: Dict[str, List[Triangle]] = {}
        
        # Initialize solid lists for each opening
        for info in opening_infos:
            solids[info.name] = []
        
        wall_triangles: List[Triangle] = []
        vertices = hollow_mesh.vertices
        faces = hollow_mesh.faces
        face_normals = hollow_mesh.face_normals
        
        # Normal matching tolerance (dot product threshold for ~15 degree tolerance)
        normal_tol = 0.95
        
        for i, face in enumerate(faces):
            v0, v1, v2 = vertices[face]
            tri: Triangle = (
                Vec3(float(v0[0]), float(v0[1]), float(v0[2])),
                Vec3(float(v1[0]), float(v1[1]), float(v1[2])),
                Vec3(float(v2[0]), float(v2[1]), float(v2[2])),
            )
            
            # Calculate face center
            center_x = (v0[0] + v1[0] + v2[0]) / 3
            center_y = (v0[1] + v1[1] + v2[1]) / 3
            center_z = (v0[2] + v1[2] + v2[2]) / 3
            
            # Get face normal
            fn = face_normals[i]
            
            # Check if this face belongs to any opening
            matched_opening = None
            for info in opening_infos:
                # Check if face center is within opening bounds
                if (info.x_min <= center_x <= info.x_max and
                    info.y_min <= center_y <= info.y_max and
                    info.z_min <= center_z <= info.z_max):
                    # Check if face normal matches expected direction
                    dot = fn[0] * info.normal_x + fn[1] * info.normal_y + fn[2] * info.normal_z
                    if dot > normal_tol:
                        matched_opening = info
                        break
            
            if matched_opening:
                solids[matched_opening.name].append(tri)
            else:
                wall_triangles.append(tri)
        
        solids[f"{self.safe_uid}_wall"] = wall_triangles
        
        # Log extracted face counts for debugging
        for info in opening_infos:
            face_count = len(solids[info.name])
            if face_count == 0:
                warnings.warn(f"No faces extracted for opening '{info.name}' - check bounds/normal")
        
        # Write STL file
        STLWriter.write_ascii_stl(filepath, solids)

    def _weld_vertices_across_solids(
        self, solids: Dict[str, List[Triangle]], tolerance: float = 1e-4
    ) -> Dict[str, List[Triangle]]:
        """Weld vertices across all solid regions to ensure shared boundaries.

        This is critical for surfaceFeatureExtract to detect region boundaries as
        feature lines. Without welding, vertices at the same location may have
        slightly different coordinates due to floating point precision.

        Args:
            solids: Dictionary of solid name to list of triangles.
            tolerance: Maximum distance between vertices to consider them the same.

        Returns:
            Dictionary with welded triangles.
        """
        # Collect all unique vertices from all solids
        all_vertices: List[Vec3] = []
        vertex_to_index: Dict[tuple, int] = {}

        def get_or_add_vertex(v: Vec3) -> int:
            """Get existing vertex index or add new one."""
            # Round to tolerance to find nearby vertices
            key = (
                round(v.x / tolerance) * tolerance,
                round(v.y / tolerance) * tolerance,
                round(v.z / tolerance) * tolerance,
            )
            if key in vertex_to_index:
                return vertex_to_index[key]
            idx = len(all_vertices)
            all_vertices.append(Vec3(key[0], key[1], key[2]))
            vertex_to_index[key] = idx
            return idx

        # Build indexed triangles for each solid
        indexed_solids: Dict[str, List[tuple]] = {}
        for name, triangles in solids.items():
            indexed_tris = []
            for tri in triangles:
                idx0 = get_or_add_vertex(tri[0])
                idx1 = get_or_add_vertex(tri[1])
                idx2 = get_or_add_vertex(tri[2])
                indexed_tris.append((idx0, idx1, idx2))
            indexed_solids[name] = indexed_tris

        # Rebuild triangles with welded vertices
        welded_solids: Dict[str, List[Triangle]] = {}
        for name, indexed_tris in indexed_solids.items():
            welded_tris = []
            for idx0, idx1, idx2 in indexed_tris:
                tri: Triangle = (
                    all_vertices[idx0],
                    all_vertices[idx1],
                    all_vertices[idx2],
                )
                welded_tris.append(tri)
            welded_solids[name] = welded_tris

        return welded_solids

    def _write_hollow_rack_stl(self, filepath: Path, leakage_zones: List[LeakageZone]) -> None:
        """Write hollow rack STL with multiple solid regions.

        Creates wall regions for all rack faces, with holes cut out for:
        - Server inlet/outlet regions
        - Leakage zone openings on front/back faces

        Left/right walls have rectangular holes where leakage zones penetrate.

        Args:
            filepath: Path to write the STL file.
            leakage_zones: List of leakage zones.
        """
        from .stl_writer import STLWriter

        solids: Dict[str, List[Triangle]] = {}
        bbox = self.bounding_box

        # Get all holes for front/back faces
        front_holes = []  # (x0, x1, z0, z1) in local coords
        rear_holes = []

        # Server inlet/outlet holes
        for server in self.servers:
            slot_z_start = self.first_slot_offset + server.slot_position * self.slot_height
            server_height = server.slot_occupation * self.slot_height

            # Inlet hole (front face)
            inlet_height = server.inlet_height or (server_height * server.inlet_height_ratio)
            inlet_width = server.inlet_width if server.inlet_width is not None else server.width
            inlet_x0 = server.inlet_offset_x
            inlet_z0 = slot_z_start + server.inlet_offset_z
            front_holes.append((inlet_x0, inlet_x0 + inlet_width, inlet_z0, inlet_z0 + inlet_height))

            # Outlet hole (rear face)
            outlet_height = server.outlet_height or (server_height * server.outlet_height_ratio)
            outlet_width = server.outlet_width if server.outlet_width is not None else server.width
            outlet_x0 = server.outlet_offset_x
            outlet_z0 = slot_z_start + server.outlet_offset_z
            rear_holes.append((outlet_x0, outlet_x0 + outlet_width, outlet_z0, outlet_z0 + outlet_height))

        # Leakage zone holes (on front and back faces)
        server_width = self.get_server_width()
        width_offset = (self.size.x - server_width) / 2

        for zone in leakage_zones:
            zone_height = zone.get_slot_height(self.slot_height)
            zone_z_start = self.first_slot_offset + zone.slot_start * self.slot_height
            zone_x0 = width_offset
            zone_x1 = width_offset + server_width
            zone_z1 = zone_z_start + zone_height

            front_holes.append((zone_x0, zone_x1, zone_z_start, zone_z1))
            rear_holes.append((zone_x0, zone_x1, zone_z_start, zone_z1))

        # Generate wall regions with holes
        # Top wall (full)
        top_tris = self._create_face_triangles(FaceType.TOP)
        solids[f"{self.uid}_wall_top"] = top_tris

        # Bottom wall (full)
        bottom_tris = self._create_face_triangles(FaceType.BOTTOM)
        solids[f"{self.uid}_wall_bottom"] = bottom_tris

        # Front wall with holes
        front_rects = _subtract_rectangles((0.0, self.size.x, 0.0, self.size.z), front_holes)
        for idx, (x0, x1, z0, z1) in enumerate(front_rects):
            tris = self._create_face_triangles(
                FaceType.FRONT,
                offset=Vec3(x0, 0, z0),
                size=Vec3(x1 - x0, 0, z1 - z0),
            )
            solids[f"{self.uid}_wall_front_{idx}"] = tris

        # Rear wall with holes
        rear_rects = _subtract_rectangles((0.0, self.size.x, 0.0, self.size.z), rear_holes)
        for idx, (x0, x1, z0, z1) in enumerate(rear_rects):
            tris = self._create_face_triangles(
                FaceType.REAR,
                offset=Vec3(x0, 0, z0),
                size=Vec3(x1 - x0, 0, z1 - z0),
            )
            solids[f"{self.uid}_wall_rear_{idx}"] = tris

        # Left wall with holes (leakage zones cut through)
        left_holes = []
        for zone in leakage_zones:
            zone_height = zone.get_slot_height(self.slot_height)
            zone_z_start = self.first_slot_offset + zone.slot_start * self.slot_height
            # Hole spans full depth (y direction) of rack
            left_holes.append((0.0, self.size.y, zone_z_start, zone_z_start + zone_height))

        left_rects = _subtract_rectangles((0.0, self.size.y, 0.0, self.size.z), left_holes)
        for idx, (y0, y1, z0, z1) in enumerate(left_rects):
            tris = self._create_face_triangles(
                FaceType.LEFT,
                offset=Vec3(0, y0, z0),
                size=Vec3(0, y1 - y0, z1 - z0),
            )
            solids[f"{self.uid}_wall_left_{idx}"] = tris

        # Right wall with holes (leakage zones cut through)
        right_holes = left_holes  # Same holes as left wall
        right_rects = _subtract_rectangles((0.0, self.size.y, 0.0, self.size.z), right_holes)
        for idx, (y0, y1, z0, z1) in enumerate(right_rects):
            tris = self._create_face_triangles(
                FaceType.RIGHT,
                offset=Vec3(0, y0, z0),
                size=Vec3(0, y1 - y0, z1 - z0),
            )
            solids[f"{self.uid}_wall_right_{idx}"] = tris

        # Server inlet/outlet patches
        for sub_entity in self.sub_entities:
            region = sub_entity.get_solid_region(bbox, self.orientation)
            if region.triangles:
                solids[region.name] = region.triangles

        # Write STL file
        STLWriter.write_ascii_stl(filepath, solids)

    def get_leakage_fvoptions_config(self) -> Dict[str, Dict[str, Any]]:
        """Get fvOptions configuration for leakage zones.

        Returns:
            Dictionary mapping zone names to their fvOptions config.
        """
        if not self.air_leakage.enabled:
            return {}

        zones = self.get_leakage_zones()
        config = {}

        for zone in zones:
            # Calculate Forchheimer coefficient: f = (1/L) * ((1/(0.6*σ)) - 1)^2
            # where L is the rack depth, σ is the leakage rate
            L = self.size.y  # Rack depth
            sigma = self.air_leakage.sigma
            if sigma <= 0 or sigma >= 1:
                sigma = 0.1  # Default to 10%

            f = (1.0 / L) * ((1.0 / (0.6 * sigma)) - 1.0) ** 2

            config[zone.cellzone_name] = {
                "type": "explicitPorositySource",
                "cellZone": zone.cellzone_name,
                "d": (0, 0, 0),  # Darcy coefficient (zero for pure Forchheimer)
                "f": (f, f, f),  # Forchheimer coefficient (isotropic)
                "sigma": sigma,
                "depth": L,
            }

        return config

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate all solid regions for the rack.

        For air leakage, the left/right walls have gaps at air_leak positions.
        This allows air to flow through from front to back, and createBaffles
        will create a cyclic baffle in the middle of this gap.
        """
        regions = []
        bbox = self.bounding_box

        # Create wall regions for top and bottom (full faces)
        for face in [FaceType.TOP, FaceType.BOTTOM]:
            region = SolidRegion(
                name=f"{self.uid}_wall_{face.value}",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )
            region.add_triangles(self._create_face_triangles(face))
            regions.append(region)

        # Create left/right walls with gaps for air leakage
        has_sides = self.air_leakage.enabled and self.air_leakage.position in ["sides", "top_sides"]
        side_width = self.air_leakage.side_width if has_sides else 0.0

        if side_width > 0:
            # Left wall with gap: create wall sections around the air leakage zone
            # The air leakage zone spans x from 0 to side_width, full height
            # We don't create a wall in this zone - it's an open gap for air flow
            # (The gap will be closed by a cyclic baffle created via createBaffles)
            pass  # No left wall at air_leak position - it's an open gap
        else:
            # Full left wall
            region = SolidRegion(
                name=f"{self.uid}_wall_left",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )
            region.add_triangles(self._create_face_triangles(FaceType.LEFT))
            regions.append(region)

        if side_width > 0:
            # Right wall with gap: same as left
            pass  # No right wall at air_leak position - it's an open gap
        else:
            # Full right wall
            region = SolidRegion(
                name=f"{self.uid}_wall_right",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )
            region.add_triangles(self._create_face_triangles(FaceType.RIGHT))
            regions.append(region)

        # Generate sub-entity regions (server inlets, outlets only - no air leakage)
        for sub_entity in self.sub_entities:
            region = sub_entity.get_solid_region(bbox, self.orientation)
            regions.append(region)

        # Create remaining wall regions for front/rear (excluding sub-entity areas).
        # Note: air_leak holes are no longer created here - only server inlet/outlet holes
        front_rects = self._get_wall_face_rects(face="front")
        rear_rects = self._get_wall_face_rects(face="rear")

        for idx, (x0, x1, z0, z1) in enumerate(front_rects):
            region = SolidRegion(
                name=f"{self.uid}_wall_front_{idx}",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )
            region.add_triangles(
                self._create_face_triangles(
                    FaceType.FRONT,
                    offset=Vec3(x0, 0, z0),
                    size=Vec3(x1 - x0, 0, z1 - z0),
                )
            )
            regions.append(region)

        for idx, (x0, x1, z0, z1) in enumerate(rear_rects):
            region = SolidRegion(
                name=f"{self.uid}_wall_rear_{idx}",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )
            region.add_triangles(
                self._create_face_triangles(
                    FaceType.REAR,
                    offset=Vec3(x0, 0, z0),
                    size=Vec3(x1 - x0, 0, z1 - z0),
                )
            )
            regions.append(region)

        return regions

    def _get_wall_face_rects(self, face: str) -> List[Tuple[float, float, float, float]]:
        """
        Compute remaining wall rectangles for the given face in local coordinates.

        Returns list of (x0, x1, z0, z1) rectangles in rack local space.

        Note: Air leakage is NOT modeled as holes in front/rear faces anymore.
        Instead, left/right walls have gaps and createBaffles creates cyclic patches.
        """
        face_rect = (0.0, self.size.x, 0.0, self.size.z)
        holes: List[Tuple[float, float, float, float]] = []

        # Server inlet/outlet holes only
        for server in self.servers:
            slot_z_start = self.first_slot_offset + server.slot_position * self.slot_height
            server_height = server.slot_occupation * self.slot_height
            if face == "front":
                height = server.inlet_height or (server_height * server.inlet_height_ratio)
                width = server.inlet_width if server.inlet_width is not None else server.width
                x0 = server.inlet_offset_x
                # Account for inlet vertical offset within the server
                z_offset = server.inlet_offset_z
            else:
                height = server.outlet_height or (server_height * server.outlet_height_ratio)
                width = server.outlet_width if server.outlet_width is not None else server.width
                x0 = server.outlet_offset_x
                # Account for outlet vertical offset within the server
                z_offset = server.outlet_offset_z
            x1 = x0 + width
            z0 = slot_z_start + z_offset
            z1 = z0 + height
            holes.append((x0, x1, z0, z1))

        # Air leakage holes are NO LONGER created on front/rear faces.
        # Air leakage is modeled through gaps in left/right walls + createBaffles.

        return _subtract_rectangles(face_rect, holes)

    def get_createBaffle_cmd(self) -> str:
        """Generate createBafflesDict commands for air leakage.

        Air leakage is modeled as internal cyclic baffles placed in the middle
        of the rack depth (y direction). This ensures matching faces for the
        cyclic pair, which is required for porousBafflePressure BC.

        Returns:
            createBafflesDict commands for air leakage baffles
        """
        if not self.air_leakage.enabled:
            return ""

        has_sides = self.air_leakage.position in ["sides", "top_sides"]
        side_width = self.air_leakage.side_width if has_sides else 0.0

        if side_width <= 0:
            return ""

        cmd = ""
        bbox = self.bounding_box

        # Baffle is placed at the middle of the rack depth
        y_mid = (bbox.v_min.y + bbox.v_max.y) / 2

        # Left side air leakage baffle
        # Origin at left-front-bottom of air leak zone, span in X-Z plane
        left_name = f"{self.uid}_air_leak_left"
        left_origin_x = bbox.v_min.x
        left_origin_z = bbox.v_min.z
        left_span_x = side_width
        left_span_z = bbox.v_max.z - bbox.v_min.z

        cmd += f"""
    {left_name}
    {{
        type        searchableSurface;
        surface     searchablePlate;
        origin      ({left_origin_x} {y_mid} {left_origin_z});
        span        ({left_span_x} 0 {left_span_z});
        patches
        {{
            master
            {{
                name            {left_name}_master;
                type            cyclic;
                neighbourPatch  {left_name}_slave;
            }}
            slave
            {{
                name            {left_name}_slave;
                type            cyclic;
                neighbourPatch  {left_name}_master;
            }}
        }}
    }}
"""

        # Right side air leakage baffle
        right_name = f"{self.uid}_air_leak_right"
        right_origin_x = bbox.v_max.x - side_width
        right_origin_z = bbox.v_min.z
        right_span_x = side_width
        right_span_z = bbox.v_max.z - bbox.v_min.z

        cmd += f"""
    {right_name}
    {{
        type        searchableSurface;
        surface     searchablePlate;
        origin      ({right_origin_x} {y_mid} {right_origin_z});
        span        ({right_span_x} 0 {right_span_z});
        patches
        {{
            master
            {{
                name            {right_name}_master;
                type            cyclic;
                neighbourPatch  {right_name}_slave;
            }}
            slave
            {{
                name            {right_name}_slave;
                type            cyclic;
                neighbourPatch  {right_name}_master;
            }}
        }}
    }}
"""

        return cmd

    def get_cyclic_patch_pairs(self) -> List[Tuple[str, str]]:
        """Get list of cyclic patch pairs for air leakage.

        Returns:
            List of (master_name, slave_name) tuples
        """
        pairs = []
        if not self.air_leakage.enabled:
            return pairs

        has_sides = self.air_leakage.position in ["sides", "top_sides"]
        side_width = self.air_leakage.side_width if has_sides else 0.0

        if side_width > 0:
            pairs.append((f"{self.uid}_air_leak_left_master", f"{self.uid}_air_leak_left_slave"))
            pairs.append((f"{self.uid}_air_leak_right_master", f"{self.uid}_air_leak_right_slave"))

        return pairs

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str) -> "RackEntity":
        """
        Create a RackEntity from a dictionary configuration.

        Args:
            data: Dictionary containing rack configuration
            uid: Unique identifier for the rack

        Returns:
            A new RackEntity instance
        """
        location = Vec3(
            x=data.get("location", {}).get("x", 0),
            y=data.get("location", {}).get("y", 0),
            z=data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=data.get("size", {}).get("x", 0.6),
            y=data.get("size", {}).get("y", 1.2),
            z=data.get("size", {}).get("z", 2.2),
        )
        orientation = data.get("orientation", 0)
        
        # Get slot_count from config (support both "slotCount" and "slot" keys)
        slot_count = data.get("slotCount", data.get("slot"))
        
        # Priority for rack height and slot_height calculation:
        # 1. If size.z is explicitly defined, use it as rack height, calculate slot_height = height / slot_count
        # 2. If size.z is not defined but slotHeight is, use rack_height = slot_count * slot_height
        # This ensures rack height can match containment height exactly
        explicit_height = data.get("size", {}).get("z")
        slot_height_from_config = data.get("slotHeight", data.get("slot_height"))
        
        if explicit_height is not None and slot_count is not None:
            # New approach: rack height is explicitly specified, calculate slot_height
            size = Vec3(x=size.x, y=size.y, z=explicit_height)
            slot_height = explicit_height / slot_count
        elif slot_count is not None and slot_height_from_config is not None:
            # Legacy approach: rack height = slot_count * slot_height
            slot_height = slot_height_from_config
            size = Vec3(x=size.x, y=size.y, z=slot_count * slot_height)
        elif slot_count is None and slot_height_from_config is not None:
            # Fallback: calculate slot_count from rack height
            slot_height = slot_height_from_config
            slot_count = int(size.z / slot_height)
        else:
            # Default values
            slot_height = 0.05  # Default 1U = 0.05m
            slot_count = int(size.z / slot_height) if size.z > 0 else 46
        
        has_blanking_panel = data.get("hasBlankingPanel", True)

        # Parse air leakage config
        leak_data = data.get("airLeakage", {})
        leak_position = leak_data.get("position", "top")
        leak_width = leak_data.get("width", 0.0)
        air_leakage = AirLeakageConfig(
            enabled=leak_data.get("enabled", True),
            side_width=leak_data.get(
                "sideWidth",
                leak_width if leak_position in ["sides", "top_sides"] else 0.0,
            ),
            top_height=leak_data.get(
                "topHeight",
                leak_width if leak_position in ["top", "top_sides"] else 0.0,
            ),
            position=leak_position,
            sigma=leak_data.get("sigma", 0.1),
        )

        leak_left = air_leakage.side_width if air_leakage.position in ["sides", "top_sides"] else 0.0
        leak_right = leak_left

        # Parse column layout
        columns = []
        column_data = data.get("columns", [])
        if column_data:
            offset = leak_left
            for idx, col_data in enumerate(column_data):
                width = col_data.get("width", 0.0)
                offset_x = col_data.get("offsetX")
                if offset_x is None:
                    offset_x = offset
                    offset += width
                columns.append(
                    ColumnConfig(
                        uid=col_data.get("uid", f"col_{idx}"),
                        width=width,
                        offset_x=offset_x,
                    )
                )
        else:
            usable_width = max(size.x - leak_left - leak_right, 0.0)
            columns.append(
                ColumnConfig(uid="col_0", width=usable_width, offset_x=leak_left)
            )

        # Parse servers
        servers = []
        for server_data in data.get("servers", []):
            column_ref = server_data.get("column")
            column = None
            if column_ref is not None:
                if isinstance(column_ref, int):
                    if 0 <= column_ref < len(columns):
                        column = columns[column_ref]
                elif isinstance(column_ref, str):
                    for col in columns:
                        if col.uid == column_ref:
                            column = col
                            break
            if column is None:
                column = columns[0]

            inlet_data = server_data.get("inlet", {})
            outlet_data = server_data.get("outlet", {})

            inlet_offset = inlet_data.get("offsetX", column.offset_x)
            outlet_offset = outlet_data.get("offsetX", column.offset_x)

            inlet_width = inlet_data.get("width", column.width)
            outlet_width = outlet_data.get("width", column.width)

            server = ServerConfig(
                uid=server_data.get("uid", ""),
                slot_position=server_data.get("slotPosition", 0),
                slot_occupation=server_data.get("slotOccupation", 1),
                width=server_data.get("width", column.width),
                depth=server_data.get("depth", size.y),
                inlet_height_ratio=inlet_data.get("heightRatio", 0.33),
                outlet_height_ratio=outlet_data.get("heightRatio", 0.33),
                inlet_height=inlet_data.get("height"),
                outlet_height=outlet_data.get("height"),
                inlet_width=inlet_width,
                outlet_width=outlet_width,
                inlet_offset_x=inlet_offset,
                outlet_offset_x=outlet_offset,
                power=server_data.get("power", 0),
                volume_flow_rate=server_data.get("volumeFlowRate", 0),
            )
            servers.append(server)

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
            slot_count=slot_count,
            slot_height=slot_height,
            first_slot_offset=data.get("firstSlotOffset", data.get("first_slot_offset", 0.0)),
            servers=servers,
            air_leakage=air_leakage,
            has_blanking_panel=has_blanking_panel,
            columns=columns,
        )


def _subtract_rectangles(
    base: Tuple[float, float, float, float],
    holes: List[Tuple[float, float, float, float]],
) -> List[Tuple[float, float, float, float]]:
    """Subtract hole rectangles from a base rectangle, returning remaining rects."""
    x0, x1, z0, z1 = base
    xs = {x0, x1}
    zs = {z0, z1}
    clipped_holes = []

    for hx0, hx1, hz0, hz1 in holes:
        cx0 = max(x0, hx0)
        cx1 = min(x1, hx1)
        cz0 = max(z0, hz0)
        cz1 = min(z1, hz1)
        if cx1 > cx0 and cz1 > cz0:
            clipped_holes.append((cx0, cx1, cz0, cz1))
            xs.update([cx0, cx1])
            zs.update([cz0, cz1])

    xs_sorted = sorted(xs)
    zs_sorted = sorted(zs)
    remaining = []

    for i in range(len(xs_sorted) - 1):
        for j in range(len(zs_sorted) - 1):
            rx0, rx1 = xs_sorted[i], xs_sorted[i + 1]
            rz0, rz1 = zs_sorted[j], zs_sorted[j + 1]
            if rx1 <= rx0 or rz1 <= rz0:
                continue
            center_x = (rx0 + rx1) * 0.5
            center_z = (rz0 + rz1) * 0.5
            inside_hole = False
            for hx0, hx1, hz0, hz1 in clipped_holes:
                if hx0 <= center_x <= hx1 and hz0 <= center_z <= hz1:
                    inside_hole = True
                    break
            if not inside_hole:
                remaining.append((rx0, rx1, rz0, rz1))

    return remaining
