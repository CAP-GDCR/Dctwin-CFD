"""
Other geometry entities: ACU, Pillar, Containment, etc.

This module defines Entity classes for various data center equipment
other than racks.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Dict, Any, Tuple

from .base import (
    Entity, SubEntity, Vec3, BoundingBox, SolidRegion,
    FaceType, PatchType, Triangle,
    rect_to_triangles, orient_triangles_outward,
)


def _subtract_rectangles_2d(
    base: Tuple[float, float, float, float],
    holes: List[Tuple[float, float, float, float]],
) -> List[Tuple[float, float, float, float]]:
    """
    Subtract hole rectangles from a base rectangle, returning remaining rects.

    This is a 2D version for horizontal faces (x, y coordinates).

    Args:
        base: (x0, x1, y0, y1) base rectangle
        holes: List of (x0, x1, y0, y1) hole rectangles

    Returns:
        List of (x0, x1, y0, y1) remaining rectangles
    """
    x0, x1, y0, y1 = base
    xs = {x0, x1}
    ys = {y0, y1}
    clipped_holes = []

    for hx0, hx1, hy0, hy1 in holes:
        # Clip hole to base rectangle
        cx0 = max(x0, hx0)
        cx1 = min(x1, hx1)
        cy0 = max(y0, hy0)
        cy1 = min(y1, hy1)
        if cx1 > cx0 and cy1 > cy0:
            clipped_holes.append((cx0, cx1, cy0, cy1))
            xs.update([cx0, cx1])
            ys.update([cy0, cy1])

    xs_sorted = sorted(xs)
    ys_sorted = sorted(ys)
    remaining = []

    for i in range(len(xs_sorted) - 1):
        for j in range(len(ys_sorted) - 1):
            rx0, rx1 = xs_sorted[i], xs_sorted[i + 1]
            ry0, ry1 = ys_sorted[j], ys_sorted[j + 1]
            if rx1 <= rx0 or ry1 <= ry0:
                continue
            center_x = (rx0 + rx1) * 0.5
            center_y = (ry0 + ry1) * 0.5
            inside_hole = False
            for hx0, hx1, hy0, hy1 in clipped_holes:
                if hx0 <= center_x <= hx1 and hy0 <= center_y <= hy1:
                    inside_hole = True
                    break
            if not inside_hole:
                remaining.append((rx0, rx1, ry0, ry1))

    return remaining


@dataclass
class FaceConfig:
    """Configuration for a face (supply/return) on an ACU."""
    side: str  # "top", "bottom", "front", "rear", "left", "right"
    width: float
    length: float
    offset_x: float = 0.0
    offset_y: float = 0.0
    offset_z: float = 0.0


class ACUSupplySubEntity(SubEntity):
    """Sub-entity representing an ACU's supply air outlet."""

    def __init__(
        self,
        uid: str,
        face_config: FaceConfig,
        parent_size: Vec3,
    ):
        super().__init__(
            uid=uid,
            patch_type=PatchType.PATCH,
            refinement_level=1,
        )
        self.face_config = face_config
        self.parent_size = parent_size

    def get_solid_region(
        self,
        parent_bbox: BoundingBox,
        orientation: float,
    ) -> SolidRegion:
        """Generate the solid region for the ACU supply face."""
        region = SolidRegion(
            name=self.uid,
            patch_type=self.patch_type,
            refinement_level=self.refinement_level,
        )

        triangles = self._create_face(parent_bbox, orientation)
        region.add_triangles(triangles)
        return region

    def _create_face(
        self,
        parent_bbox: BoundingBox,
        orientation: float,
    ) -> List[Triangle]:
        """Create triangles for the supply face."""
        fc = self.face_config
        ps = self.parent_size

        # Determine face vertices based on side and orientation
        if fc.side == "bottom":
            z = parent_bbox.v_min.z
            normal = Vec3(0, 0, -1)
            if orientation in [0, 180]:
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_x, parent_bbox.v_min.y + fc.offset_y, z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, parent_bbox.v_min.y + fc.offset_y, z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, parent_bbox.v_min.y + fc.offset_y + fc.length, z)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_x, parent_bbox.v_min.y + fc.offset_y + fc.length, z)
            else:  # 90, 270
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_y, parent_bbox.v_min.y + fc.offset_x, z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_y + fc.length, parent_bbox.v_min.y + fc.offset_x, z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_y + fc.length, parent_bbox.v_min.y + fc.offset_x + fc.width, z)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_y, parent_bbox.v_min.y + fc.offset_x + fc.width, z)
        elif fc.side == "top":
            z = parent_bbox.v_max.z
            normal = Vec3(0, 0, 1)
            if orientation in [0, 180]:
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_x, parent_bbox.v_min.y + fc.offset_y, z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, parent_bbox.v_min.y + fc.offset_y, z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, parent_bbox.v_min.y + fc.offset_y + fc.length, z)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_x, parent_bbox.v_min.y + fc.offset_y + fc.length, z)
            else:
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_y, parent_bbox.v_min.y + fc.offset_x, z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_y + fc.length, parent_bbox.v_min.y + fc.offset_x, z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_y + fc.length, parent_bbox.v_min.y + fc.offset_x + fc.width, z)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_y, parent_bbox.v_min.y + fc.offset_x + fc.width, z)
        elif fc.side == "front":
            normal = Vec3(0, -1, 0).rotate_z(orientation)
            if orientation == 0:
                y = parent_bbox.v_min.y
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_x, y, parent_bbox.v_min.z + fc.offset_z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, y, parent_bbox.v_min.z + fc.offset_z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, y, parent_bbox.v_min.z + fc.offset_z + fc.length)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_x, y, parent_bbox.v_min.z + fc.offset_z + fc.length)
            elif orientation == 90:
                x = parent_bbox.v_max.x
                v00 = Vec3(x, parent_bbox.v_min.y + fc.offset_x, parent_bbox.v_min.z + fc.offset_z)
                v10 = Vec3(x, parent_bbox.v_min.y + fc.offset_x + fc.width, parent_bbox.v_min.z + fc.offset_z)
                v11 = Vec3(x, parent_bbox.v_min.y + fc.offset_x + fc.width, parent_bbox.v_min.z + fc.offset_z + fc.length)
                v01 = Vec3(x, parent_bbox.v_min.y + fc.offset_x, parent_bbox.v_min.z + fc.offset_z + fc.length)
            elif orientation == 180:
                y = parent_bbox.v_max.y
                v00 = Vec3(parent_bbox.v_max.x - fc.offset_x, y, parent_bbox.v_min.z + fc.offset_z)
                v10 = Vec3(parent_bbox.v_max.x - fc.offset_x - fc.width, y, parent_bbox.v_min.z + fc.offset_z)
                v11 = Vec3(parent_bbox.v_max.x - fc.offset_x - fc.width, y, parent_bbox.v_min.z + fc.offset_z + fc.length)
                v01 = Vec3(parent_bbox.v_max.x - fc.offset_x, y, parent_bbox.v_min.z + fc.offset_z + fc.length)
            else:  # 270
                x = parent_bbox.v_min.x
                v00 = Vec3(x, parent_bbox.v_max.y - fc.offset_x, parent_bbox.v_min.z + fc.offset_z)
                v10 = Vec3(x, parent_bbox.v_max.y - fc.offset_x - fc.width, parent_bbox.v_min.z + fc.offset_z)
                v11 = Vec3(x, parent_bbox.v_max.y - fc.offset_x - fc.width, parent_bbox.v_min.z + fc.offset_z + fc.length)
                v01 = Vec3(x, parent_bbox.v_max.y - fc.offset_x, parent_bbox.v_min.z + fc.offset_z + fc.length)
        else:
            # Default case - handle other sides similarly
            z = parent_bbox.v_min.z
            normal = Vec3(0, 0, -1)
            v00 = Vec3(parent_bbox.v_min.x, parent_bbox.v_min.y, z)
            v10 = Vec3(parent_bbox.v_max.x, parent_bbox.v_min.y, z)
            v11 = Vec3(parent_bbox.v_max.x, parent_bbox.v_max.y, z)
            v01 = Vec3(parent_bbox.v_min.x, parent_bbox.v_max.y, z)

        triangles = rect_to_triangles(v00, v10, v11, v01)
        return orient_triangles_outward(triangles, normal)


class ACUReturnSubEntity(SubEntity):
    """Sub-entity representing an ACU's return air inlet."""

    def __init__(
        self,
        uid: str,
        face_config: FaceConfig,
        parent_size: Vec3,
    ):
        super().__init__(
            uid=uid,
            patch_type=PatchType.PATCH,
            refinement_level=1,
        )
        self.face_config = face_config
        self.parent_size = parent_size

    def get_solid_region(
        self,
        parent_bbox: BoundingBox,
        orientation: float,
    ) -> SolidRegion:
        """Generate the solid region for the ACU return face."""
        region = SolidRegion(
            name=self.uid,
            patch_type=self.patch_type,
            refinement_level=self.refinement_level,
        )

        # Similar logic to ACUSupplySubEntity
        fc = self.face_config

        if fc.side == "top":
            z = parent_bbox.v_max.z
            normal = Vec3(0, 0, 1)
            if orientation in [0, 180]:
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_x, parent_bbox.v_min.y + fc.offset_y, z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, parent_bbox.v_min.y + fc.offset_y, z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, parent_bbox.v_min.y + fc.offset_y + fc.length, z)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_x, parent_bbox.v_min.y + fc.offset_y + fc.length, z)
            else:
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_y, parent_bbox.v_min.y + fc.offset_x, z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_y + fc.length, parent_bbox.v_min.y + fc.offset_x, z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_y + fc.length, parent_bbox.v_min.y + fc.offset_x + fc.width, z)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_y, parent_bbox.v_min.y + fc.offset_x + fc.width, z)
        elif fc.side == "bottom":
            z = parent_bbox.v_min.z
            normal = Vec3(0, 0, -1)
            if orientation in [0, 180]:
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_x, parent_bbox.v_min.y + fc.offset_y, z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, parent_bbox.v_min.y + fc.offset_y, z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_x + fc.width, parent_bbox.v_min.y + fc.offset_y + fc.length, z)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_x, parent_bbox.v_min.y + fc.offset_y + fc.length, z)
            else:
                v00 = Vec3(parent_bbox.v_min.x + fc.offset_y, parent_bbox.v_min.y + fc.offset_x, z)
                v10 = Vec3(parent_bbox.v_min.x + fc.offset_y + fc.length, parent_bbox.v_min.y + fc.offset_x, z)
                v11 = Vec3(parent_bbox.v_min.x + fc.offset_y + fc.length, parent_bbox.v_min.y + fc.offset_x + fc.width, z)
                v01 = Vec3(parent_bbox.v_min.x + fc.offset_y, parent_bbox.v_min.y + fc.offset_x + fc.width, z)
        else:
            # Handle other sides
            z = parent_bbox.v_max.z
            normal = Vec3(0, 0, 1)
            v00 = Vec3(parent_bbox.v_min.x, parent_bbox.v_min.y, z)
            v10 = Vec3(parent_bbox.v_max.x, parent_bbox.v_min.y, z)
            v11 = Vec3(parent_bbox.v_max.x, parent_bbox.v_max.y, z)
            v01 = Vec3(parent_bbox.v_min.x, parent_bbox.v_max.y, z)

        triangles = rect_to_triangles(v00, v10, v11, v01)
        triangles = orient_triangles_outward(triangles, normal)
        region.add_triangles(triangles)
        return region


class ACUEntity(Entity):
    """
    Entity representing an Air Conditioning Unit (ACU/CRAH).

    An ACU has a supply face (cold air outlet) and a return face
    (hot air inlet), with the remaining faces being walls.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
        supply_face: Optional[FaceConfig] = None,
        return_face: Optional[FaceConfig] = None,
    ):
        super().__init__(uid, location, size, orientation)

        # Default face configurations
        self.supply_face = supply_face or FaceConfig(
            side="bottom",
            width=size.x,
            length=size.y * 0.6,
        )
        self.return_face = return_face or FaceConfig(
            side="top",
            width=size.x,
            length=size.y,
        )

        self._create_sub_entities()

    def _create_sub_entities(self) -> None:
        """Create sub-entities for supply and return faces."""
        # Supply face
        supply = ACUSupplySubEntity(
            uid=f"acu_supply_{self.uid}",
            face_config=self.supply_face,
            parent_size=self.size,
        )
        self.add_sub_entity(supply)

        # Return face
        return_sub = ACUReturnSubEntity(
            uid=f"acu_return_{self.uid}",
            face_config=self.return_face,
            parent_size=self.size,
        )
        self.add_sub_entity(return_sub)

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate all solid regions for the ACU."""
        regions = []
        bbox = self.bounding_box

        # Determine which faces have supply/return
        supply_side = self.supply_face.side
        return_side = self.return_face.side

        # Create wall regions for faces without supply/return
        for face in FaceType:
            face_name = face.value
            if face_name not in [supply_side, return_side]:
                region = SolidRegion(
                    name=f"acu_wall_{self.uid}_{face_name}",
                    patch_type=PatchType.WALL,
                    refinement_level=1,
                )
                region.add_triangles(self._create_face_triangles(face))
                regions.append(region)

        # Add sub-entity regions (supply, return)
        for sub_entity in self.sub_entities:
            region = sub_entity.get_solid_region(bbox, self.orientation)
            regions.append(region)

        # Add wall portions of supply/return face sides
        # (the area not covered by supply/return patches)
        # We need to create remaining wall regions for bottom/top faces
        # that have supply/return patches that don't cover the full face
        self._add_remaining_wall_regions(regions, supply_side, self.supply_face)
        self._add_remaining_wall_regions(regions, return_side, self.return_face)

        return regions

    def _add_remaining_wall_regions(
        self,
        regions: List[SolidRegion],
        side: str,
        face_config: FaceConfig,
    ) -> None:
        """Add remaining wall regions for a face with a partial subentity region."""
        # Only handle horizontal faces (bottom, top) for now
        # since ACU typically has supply on bottom and return on top
        if side not in ["bottom", "top"]:
            return

        # Get face dimensions
        face_x = self.size.x
        face_y = self.size.y

        # Get the subentity region dimensions (in local coordinates)
        sub_x0 = face_config.offset_x
        sub_y0 = face_config.offset_y
        sub_x1 = sub_x0 + face_config.width
        sub_y1 = sub_y0 + face_config.length

        # Check if subentity covers full face - if so, no remaining wall needed
        if sub_x0 <= 0 and sub_x1 >= face_x and sub_y0 <= 0 and sub_y1 >= face_y:
            return

        # Compute remaining rectangles using subtraction
        face_rect = (0.0, face_x, 0.0, face_y)
        hole = (sub_x0, sub_x1, sub_y0, sub_y1)
        remaining_rects = _subtract_rectangles_2d(face_rect, [hole])

        # Create wall regions for remaining rectangles
        for idx, (x0, x1, y0, y1) in enumerate(remaining_rects):
            if x1 <= x0 or y1 <= y0:
                continue

            region = SolidRegion(
                name=f"acu_wall_{self.uid}_{side}_{idx}",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )

            # Create triangles for this rectangle on the horizontal face
            if side == "bottom":
                z = 0  # local z
                normal = Vec3(0, 0, -1)
            else:  # top
                z = self.size.z  # local z
                normal = Vec3(0, 0, 1)

            # Create vertices in local coordinates
            v00 = Vec3(x0, y0, z)
            v10 = Vec3(x1, y0, z)
            v11 = Vec3(x1, y1, z)
            v01 = Vec3(x0, y1, z)

            # Transform to global coordinates
            v00 = self._transform_point(v00)
            v10 = self._transform_point(v10)
            v11 = self._transform_point(v11)
            v01 = self._transform_point(v01)

            triangles = rect_to_triangles(v00, v10, v11, v01)
            triangles = orient_triangles_outward(triangles, normal.rotate_z(self.orientation))
            region.add_triangles(triangles)
            regions.append(region)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str) -> "ACUEntity":
        """Create an ACUEntity from a dictionary configuration."""
        location = Vec3(
            x=data.get("location", {}).get("x", 0),
            y=data.get("location", {}).get("y", 0),
            z=data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=data.get("size", {}).get("x", 1.8),
            y=data.get("size", {}).get("y", 0.99),
            z=data.get("size", {}).get("z", 1.38),
        )
        orientation = data.get("orientation", 0)

        # Parse supply face config
        supply_data = data.get("supplyFace", {})
        supply_face = FaceConfig(
            side=supply_data.get("side", "bottom"),
            width=supply_data.get("width", size.x),
            length=supply_data.get("length", size.y * 0.6),
            offset_x=supply_data.get("offset", {}).get("x", 0),
            offset_y=supply_data.get("offset", {}).get("y", 0),
            offset_z=supply_data.get("offset", {}).get("z", 0),
        )

        # Parse return face config
        return_data = data.get("returnFace", {})
        return_face = FaceConfig(
            side=return_data.get("side", "top"),
            width=return_data.get("width", size.x),
            length=return_data.get("length", size.y),
            offset_x=return_data.get("offset", {}).get("x", 0),
            offset_y=return_data.get("offset", {}).get("y", 0),
            offset_z=return_data.get("offset", {}).get("z", 0),
        )

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
            supply_face=supply_face,
            return_face=return_face,
        )


class PillarEntity(Entity):
    """
    Entity representing a structural pillar/column.

    All faces of a pillar are walls.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
    ):
        super().__init__(uid, location, size, orientation)

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate all solid regions for the pillar."""
        regions = []

        # All 6 faces are walls
        for face in FaceType:
            region = SolidRegion(
                name=f"pillar_{self.uid}_{face.value}",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )
            region.add_triangles(self._create_face_triangles(face))
            regions.append(region)

        return regions

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str) -> "PillarEntity":
        """Create a PillarEntity from a dictionary configuration."""
        location = Vec3(
            x=data.get("location", {}).get("x", 0),
            y=data.get("location", {}).get("y", 0),
            z=data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=data.get("size", {}).get("x", 0.4),
            y=data.get("size", {}).get("y", 0.4),
            z=data.get("size", {}).get("z", 3.0),
        )
        orientation = data.get("orientation", 0)

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
        )


@dataclass
class BoxFaces:
    """Configuration for which faces of a box are solid."""
    top: bool = True
    bottom: bool = False
    front: bool = False
    rear: bool = False
    left: bool = True
    right: bool = True


class ContainmentEntity(Entity):
    """
    Entity representing a containment structure (hot/cold aisle containment).

    A containment typically has top, left, and right walls, with
    front and rear open for air flow.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
        faces: Optional[BoxFaces] = None,
    ):
        super().__init__(uid, location, size, orientation)
        self.faces = faces or BoxFaces()

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate solid regions only for the enabled faces."""
        regions = []

        face_enabled = {
            FaceType.TOP: self.faces.top,
            FaceType.BOTTOM: self.faces.bottom,
            FaceType.FRONT: self.faces.front,
            FaceType.REAR: self.faces.rear,
            FaceType.LEFT: self.faces.left,
            FaceType.RIGHT: self.faces.right,
        }

        for face, enabled in face_enabled.items():
            if enabled:
                region = SolidRegion(
                    name=f"containment_{self.uid}_{face.value}",
                    patch_type=PatchType.WALL,
                    refinement_level=1,
                )
                region.add_triangles(self._create_face_triangles(face))
                regions.append(region)

        return regions

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str) -> "ContainmentEntity":
        """Create a ContainmentEntity from a dictionary configuration."""
        location = Vec3(
            x=data.get("location", {}).get("x", 0),
            y=data.get("location", {}).get("y", 0),
            z=data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=data.get("size", {}).get("x", 1.0),
            y=data.get("size", {}).get("y", 2.0),
            z=data.get("size", {}).get("z", 1.0),
        )
        orientation = data.get("orientation", 0)

        faces_data = data.get("faces", {})
        faces = BoxFaces(
            top=faces_data.get("top", True),
            bottom=faces_data.get("bottom", False),
            front=faces_data.get("front", False),
            rear=faces_data.get("rear", False),
            left=faces_data.get("left", True),
            right=faces_data.get("right", True),
        )

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
            faces=faces,
        )


@dataclass
class BoxOpeningConfig:
    """Configuration for an opening in a box face."""
    uid: str
    location: Vec3  # Relative to box origin in local coords
    size: Vec3  # Width and height of opening


class BoxEntity(Entity):
    """
    Generic box entity with configurable faces.

    This can represent various equipment like power distribution units,
    storage cabinets, partitions with openings, etc.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
        faces: Optional[BoxFaces] = None,
        has_openings: bool = False,
        openings_side: Optional[str] = None,
        openings: Optional[List[BoxOpeningConfig]] = None,
    ):
        super().__init__(uid, location, size, orientation)
        self.faces = faces or BoxFaces(
            top=True, bottom=True, front=True, rear=True, left=True, right=True
        )
        self.has_openings = has_openings
        self.openings_side = openings_side
        self.openings = openings or []

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate solid regions for enabled faces, with openings cut out."""
        regions = []

        face_enabled = {
            FaceType.TOP: self.faces.top,
            FaceType.BOTTOM: self.faces.bottom,
            FaceType.FRONT: self.faces.front,
            FaceType.REAR: self.faces.rear,
            FaceType.LEFT: self.faces.left,
            FaceType.RIGHT: self.faces.right,
        }

        for face, enabled in face_enabled.items():
            if enabled:
                region = SolidRegion(
                    name=f"box_{self.uid}_{face.value}",
                    patch_type=PatchType.WALL,
                    refinement_level=1,
                )
                # Check if this face has openings
                if self.openings and self._face_has_openings(face):
                    triangles = self._create_face_with_openings(face)
                else:
                    triangles = self._create_face_triangles(face)
                region.add_triangles(triangles)
                regions.append(region)

        return regions

    def _face_has_openings(self, face: FaceType) -> bool:
        """Check if a face should have openings."""
        if not self.openings_side:
            return False
        # Map openings_side to face type
        side_to_face = {
            "left": FaceType.LEFT,
            "right": FaceType.RIGHT,
            "front": FaceType.FRONT,
            "rear": FaceType.REAR,
            "top": FaceType.TOP,
            "bottom": FaceType.BOTTOM,
        }
        return side_to_face.get(self.openings_side) == face

    def _create_face_with_openings(self, face: FaceType) -> List[Triangle]:
        """Create face triangles with openings cut out."""
        bbox = self.bounding_box
        
        if face == FaceType.LEFT:
            # YZ plane at x_min
            x = bbox.v_min.x
            base_y0, base_y1 = bbox.v_min.y, bbox.v_max.y
            base_z0, base_z1 = bbox.v_min.z, bbox.v_max.z
            normal = Vec3(-1, 0, 0)
            
            # Convert openings to (y0, y1, z0, z1) format
            holes = []
            for opening in self.openings:
                oy0 = opening.location.y
                oy1 = opening.location.y + opening.size.y
                oz0 = opening.location.z
                oz1 = opening.location.z + opening.size.z
                holes.append((oy0, oy1, oz0, oz1))
            
            # Use grid-based subtraction
            remaining = self._subtract_rectangles_yz(
                (base_y0, base_y1, base_z0, base_z1), holes
            )
            
            triangles = []
            for ry0, ry1, rz0, rz1 in remaining:
                v00 = Vec3(x, ry0, rz0)
                v10 = Vec3(x, ry1, rz0)
                v11 = Vec3(x, ry1, rz1)
                v01 = Vec3(x, ry0, rz1)
                tris = rect_to_triangles(v00, v10, v11, v01)
                tris = orient_triangles_outward(tris, normal)
                triangles.extend(tris)
            return triangles
            
        elif face == FaceType.RIGHT:
            # YZ plane at x_max
            x = bbox.v_max.x
            base_y0, base_y1 = bbox.v_min.y, bbox.v_max.y
            base_z0, base_z1 = bbox.v_min.z, bbox.v_max.z
            normal = Vec3(1, 0, 0)
            
            holes = []
            for opening in self.openings:
                oy0 = opening.location.y
                oy1 = opening.location.y + opening.size.y
                oz0 = opening.location.z
                oz1 = opening.location.z + opening.size.z
                holes.append((oy0, oy1, oz0, oz1))
            
            remaining = self._subtract_rectangles_yz(
                (base_y0, base_y1, base_z0, base_z1), holes
            )
            
            triangles = []
            for ry0, ry1, rz0, rz1 in remaining:
                v00 = Vec3(x, ry1, rz0)
                v10 = Vec3(x, ry0, rz0)
                v11 = Vec3(x, ry0, rz1)
                v01 = Vec3(x, ry1, rz1)
                tris = rect_to_triangles(v00, v10, v11, v01)
                tris = orient_triangles_outward(tris, normal)
                triangles.extend(tris)
            return triangles
            
        elif face == FaceType.FRONT:
            # XZ plane at y_min
            y = bbox.v_min.y
            base_x0, base_x1 = bbox.v_min.x, bbox.v_max.x
            base_z0, base_z1 = bbox.v_min.z, bbox.v_max.z
            normal = Vec3(0, -1, 0)
            
            holes = []
            for opening in self.openings:
                ox0 = opening.location.x
                ox1 = opening.location.x + opening.size.x
                oz0 = opening.location.z
                oz1 = opening.location.z + opening.size.z
                holes.append((ox0, ox1, oz0, oz1))
            
            remaining = self._subtract_rectangles_xz(
                (base_x0, base_x1, base_z0, base_z1), holes
            )
            
            triangles = []
            for rx0, rx1, rz0, rz1 in remaining:
                v00 = Vec3(rx0, y, rz0)
                v10 = Vec3(rx1, y, rz0)
                v11 = Vec3(rx1, y, rz1)
                v01 = Vec3(rx0, y, rz1)
                tris = rect_to_triangles(v00, v10, v11, v01)
                tris = orient_triangles_outward(tris, normal)
                triangles.extend(tris)
            return triangles
            
        elif face == FaceType.REAR:
            # XZ plane at y_max
            y = bbox.v_max.y
            base_x0, base_x1 = bbox.v_min.x, bbox.v_max.x
            base_z0, base_z1 = bbox.v_min.z, bbox.v_max.z
            normal = Vec3(0, 1, 0)
            
            holes = []
            for opening in self.openings:
                ox0 = opening.location.x
                ox1 = opening.location.x + opening.size.x
                oz0 = opening.location.z
                oz1 = opening.location.z + opening.size.z
                holes.append((ox0, ox1, oz0, oz1))
            
            remaining = self._subtract_rectangles_xz(
                (base_x0, base_x1, base_z0, base_z1), holes
            )
            
            triangles = []
            for rx0, rx1, rz0, rz1 in remaining:
                v00 = Vec3(rx1, y, rz0)
                v10 = Vec3(rx0, y, rz0)
                v11 = Vec3(rx0, y, rz1)
                v01 = Vec3(rx1, y, rz1)
                tris = rect_to_triangles(v00, v10, v11, v01)
                tris = orient_triangles_outward(tris, normal)
                triangles.extend(tris)
            return triangles
        
        # For TOP/BOTTOM, use the 2D subtraction
        else:
            return self._create_face_triangles(face)

    def _subtract_rectangles_yz(
        self,
        base: Tuple[float, float, float, float],
        holes: List[Tuple[float, float, float, float]],
    ) -> List[Tuple[float, float, float, float]]:
        """Subtract holes from base rectangle in YZ plane."""
        y0, y1, z0, z1 = base
        ys = {y0, y1}
        zs = {z0, z1}
        clipped_holes = []
        
        for hy0, hy1, hz0, hz1 in holes:
            cy0 = max(y0, hy0)
            cy1 = min(y1, hy1)
            cz0 = max(z0, hz0)
            cz1 = min(z1, hz1)
            if cy1 > cy0 and cz1 > cz0:
                clipped_holes.append((cy0, cy1, cz0, cz1))
                ys.update([cy0, cy1])
                zs.update([cz0, cz1])
        
        ys_sorted = sorted(ys)
        zs_sorted = sorted(zs)
        
        remaining = []
        for i in range(len(ys_sorted) - 1):
            for j in range(len(zs_sorted) - 1):
                ry0, ry1 = ys_sorted[i], ys_sorted[i + 1]
                rz0, rz1 = zs_sorted[j], zs_sorted[j + 1]
                cy, cz = (ry0 + ry1) / 2, (rz0 + rz1) / 2
                
                is_hole = any(
                    hy0 <= cy <= hy1 and hz0 <= cz <= hz1
                    for hy0, hy1, hz0, hz1 in clipped_holes
                )
                if not is_hole:
                    remaining.append((ry0, ry1, rz0, rz1))
        
        return remaining

    def _subtract_rectangles_xz(
        self,
        base: Tuple[float, float, float, float],
        holes: List[Tuple[float, float, float, float]],
    ) -> List[Tuple[float, float, float, float]]:
        """Subtract holes from base rectangle in XZ plane."""
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
                cx, cz = (rx0 + rx1) / 2, (rz0 + rz1) / 2
                
                is_hole = any(
                    hx0 <= cx <= hx1 and hz0 <= cz <= hz1
                    for hx0, hx1, hz0, hz1 in clipped_holes
                )
                if not is_hole:
                    remaining.append((rx0, rx1, rz0, rz1))
        
        return remaining

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str) -> "BoxEntity":
        """Create a BoxEntity from a dictionary configuration."""
        location = Vec3(
            x=data.get("location", {}).get("x", 0),
            y=data.get("location", {}).get("y", 0),
            z=data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=data.get("size", {}).get("x", 1.0),
            y=data.get("size", {}).get("y", 1.0),
            z=data.get("size", {}).get("z", 1.0),
        )
        orientation = data.get("orientation", 0)

        faces_data = data.get("faces", {})
        faces = BoxFaces(
            top=faces_data.get("top", True),
            bottom=faces_data.get("bottom", True),
            front=faces_data.get("front", True),
            rear=faces_data.get("rear", True),
            left=faces_data.get("left", True),
            right=faces_data.get("right", True),
        )

        # Parse openings
        openings = []
        openings_data = data.get("openings", {})
        for opening_uid, opening_info in openings_data.items():
            opening_loc = opening_info.get("location", {})
            opening_size = opening_info.get("size", {})
            openings.append(BoxOpeningConfig(
                uid=opening_uid,
                location=Vec3(
                    x=opening_loc.get("x", 0),
                    y=opening_loc.get("y", 0),
                    z=opening_loc.get("z", 0),
                ),
                size=Vec3(
                    x=opening_size.get("x", 0),
                    y=opening_size.get("y", 0),
                    z=opening_size.get("z", 0),
                ),
            ))

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
            faces=faces,
            has_openings=data.get("hasOpenings", bool(openings_data)),
            openings_side=data.get("openingsSide"),
            openings=openings,
        )


@dataclass
class ImportedSTLBoundaryConfig:
    """Boundary condition configuration for a single solid zone in an imported STL."""
    bc_type: str = "wall"  # "wall", "fixedTemperature"
    temperature: Optional[float] = None  # Celsius, for fixedTemperature


class ImportedSTLEntity(Entity):
    """
    Entity that imports an external CAD-modeled STL file.

    Unlike other entities that generate geometry programmatically,
    this entity reads triangles from an external ASCII STL file,
    applies location/orientation transforms, and maps per-solid-zone
    boundary conditions from the JSON configuration.

    The STL file is expected to be in local coordinates (origin at 0,0,0).
    The `location` field translates the geometry, and `orientation` rotates
    around the Z axis before translation (matching existing Entity behavior).

    Solid zone naming: STL solid "front" -> mesh patch "{uid}_front".
    BC config uses original STL zone names as keys.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
        stl_path: Optional[str] = None,
        boundary_conditions: Optional[Dict[str, ImportedSTLBoundaryConfig]] = None,
    ):
        super().__init__(uid, location, size, orientation)
        self.stl_path = stl_path
        self.boundary_conditions = boundary_conditions or {}
        self._raw_solids: Optional[Dict[str, List[Triangle]]] = None
        self._transformed_solids: Optional[Dict[str, List[Triangle]]] = None

    def _load_stl(self) -> Dict[str, List[Triangle]]:
        """Load and cache the raw STL data."""
        if self._raw_solids is None:
            from .stl_writer import STLWriter
            self._raw_solids = STLWriter.read_ascii_stl(self.stl_path)
        return self._raw_solids

    def _get_transformed_solids(self) -> Dict[str, List[Triangle]]:
        """Get the STL solids with location/orientation transforms applied."""
        if self._transformed_solids is not None:
            return self._transformed_solids

        raw = self._load_stl()
        self._transformed_solids = {}

        for solid_name, triangles in raw.items():
            transformed_tris = []
            for tri in triangles:
                v0 = self._transform_point(tri[0])
                v1 = self._transform_point(tri[1])
                v2 = self._transform_point(tri[2])
                transformed_tris.append((v0, v1, v2))
            self._transformed_solids[solid_name] = transformed_tris

        return self._transformed_solids

    @property
    def bounding_box(self) -> BoundingBox:
        """Compute bounding box from actual transformed triangle vertices."""
        solids = self._get_transformed_solids()
        all_vertices = [
            v for tris in solids.values() for tri in tris for v in tri
        ]
        if not all_vertices:
            return super().bounding_box

        xs = [v.x for v in all_vertices]
        ys = [v.y for v in all_vertices]
        zs = [v.z for v in all_vertices]

        return BoundingBox(
            v_min=Vec3(min(xs), min(ys), min(zs)),
            v_max=Vec3(max(xs), max(ys), max(zs)),
        )

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate all solid regions from the imported STL."""
        solids = self._get_transformed_solids()
        regions = []

        for solid_name, triangles in solids.items():
            patch_name = f"{self.uid}_{solid_name}"

            bc_config = self.boundary_conditions.get(
                solid_name, ImportedSTLBoundaryConfig()
            )

            if bc_config.bc_type in ("fixedTemperature", "patch"):
                patch_type = PatchType.WALL
            else:
                patch_type = PatchType.WALL

            region = SolidRegion(
                name=patch_name,
                patch_type=patch_type,
                refinement_level=1,
            )
            region.add_triangles(triangles)
            regions.append(region)

        return regions

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
        uid: str,
        base_path: Optional[str] = None,
    ) -> "ImportedSTLEntity":
        """Create an ImportedSTLEntity from a dictionary configuration.

        Args:
            data: Configuration dictionary
            uid: Unique identifier
            base_path: Base path for resolving relative STL file paths
        """
        from pathlib import Path

        location_data = data.get("location", {})
        location = Vec3(
            x=location_data.get("x", 0),
            y=location_data.get("y", 0),
            z=location_data.get("z", 0),
        )
        orientation = data.get("orientation", 0)

        # Resolve STL path
        stl_file = data.get("stl_file", "")
        if base_path and not Path(stl_file).is_absolute():
            stl_file = str(Path(base_path) / stl_file)

        # Parse boundary conditions
        bc_dict: Dict[str, ImportedSTLBoundaryConfig] = {}
        for zone_name, zone_bc in data.get("boundary_conditions", {}).items():
            bc_dict[zone_name] = ImportedSTLBoundaryConfig(
                bc_type=zone_bc.get("type", "wall"),
                temperature=zone_bc.get("temperature"),
            )

        # Placeholder size - bounding_box is overridden from actual STL vertices
        size = Vec3(x=1.0, y=1.0, z=1.0)

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
            stl_path=stl_file,
            boundary_conditions=bc_dict,
        )
