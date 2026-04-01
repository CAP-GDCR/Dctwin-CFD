"""
Baffle geometry entities for data center CFD simulations.

This module defines Entity classes for baffle structures (raised floor,
false ceiling, containment panels, etc.) that require STL-based geometry
with faceZone handling in snappyHexMesh.

Key concepts:
- BaffleType.WALL: Solid baffle with porosity=0, becomes wall boundary
- BaffleType.PERFORATED: Porous baffle with 0 < porosity < 1, uses porousBafflePressure
- BaffleType.TRANSPARENT: Fully open with porosity=1, uses cyclic boundary

The workflow is:
1. Generate STL for each baffle
2. Extract feature edges (eMesh)
3. snappyHexMesh with faceZone mode (prevents space splitting)
4. createBaffles converts faceZone to wall/cyclic boundaries
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Optional, Any, Tuple

from .base import (
    Entity, Vec3, BoundingBox, SolidRegion, Triangle,
    FaceType, PatchType, sanitize_name,
    rect_to_triangles, orient_triangles_outward,
)


class BaffleType(Enum):
    """Types of baffle surfaces."""
    WALL = "wall"                # Solid wall, porosity=0
    PERFORATED = "perforated"    # Porous baffle, 0 < porosity < 1
    TRANSPARENT = "transparent"  # Fully open, porosity=1 (pure cyclic)


@dataclass
class BaffleConfig:
    """Configuration for a baffle surface."""
    baffle_type: BaffleType = BaffleType.WALL
    porosity: float = 0.0  # 0.0 = solid wall, 0.8 = 80% open, 1.0 = fully open

    def __post_init__(self):
        # Validate porosity
        if self.porosity < 0.0 or self.porosity > 1.0:
            raise ValueError(f"Porosity must be between 0.0 and 1.0, got {self.porosity}")
        
        # Auto-determine baffle type from porosity if not explicitly set
        if self.porosity == 0.0:
            self.baffle_type = BaffleType.WALL
        elif self.porosity == 1.0:
            self.baffle_type = BaffleType.TRANSPARENT
        elif self.porosity > 0.0:
            self.baffle_type = BaffleType.PERFORATED

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BaffleConfig":
        """Create BaffleConfig from dictionary."""
        baffle_type_str = data.get("baffle_type", data.get("baffleType", "wall"))
        porosity = data.get("porosity", 0.0)
        
        # Map string to enum
        type_map = {
            "wall": BaffleType.WALL,
            "perforated": BaffleType.PERFORATED,
            "transparent": BaffleType.TRANSPARENT,
        }
        baffle_type = type_map.get(baffle_type_str.lower(), BaffleType.WALL)
        
        return cls(baffle_type=baffle_type, porosity=porosity)


class BaffleEntity(Entity):
    """
    Base class for baffle entities.
    
    Baffles are thin surfaces (zero thickness) that create internal boundaries.
    Unlike solid entities (Rack, ACU), baffles generate single-layer triangulated
    surfaces for use with snappyHexMesh faceZone mode.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
        baffle_config: Optional[BaffleConfig] = None,
    ):
        super().__init__(uid, location, size, orientation)
        self.baffle_config = baffle_config or BaffleConfig()

    @property
    def is_wall(self) -> bool:
        """Check if this is a solid wall baffle."""
        return self.baffle_config.baffle_type == BaffleType.WALL

    @property
    def is_perforated(self) -> bool:
        """Check if this is a perforated baffle."""
        return self.baffle_config.baffle_type == BaffleType.PERFORATED

    @property
    def is_transparent(self) -> bool:
        """Check if this is a fully transparent baffle."""
        return self.baffle_config.baffle_type == BaffleType.TRANSPARENT

    @property
    def porosity(self) -> float:
        """Get the porosity value."""
        return self.baffle_config.porosity

    @property
    def face_zone_name(self) -> str:
        """Get the faceZone name for snappyHexMesh."""
        return f"{self.safe_uid}_faceZone"

    @property
    def master_patch_name(self) -> str:
        """Get the master patch name after createBaffles."""
        return f"{self.safe_uid}_master"

    @property
    def slave_patch_name(self) -> str:
        """Get the slave patch name after createBaffles."""
        return f"{self.safe_uid}_slave"


class HorizontalBaffleEntity(BaffleEntity):
    """
    A horizontal baffle surface (parallel to XY plane).
    
    Used for raised floors, false ceilings, and horizontal containment panels.
    The baffle is defined by its Z height and XY extent.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,  # (x_min, y_min, z_height)
        size: Vec3,      # (width_x, depth_y, 0) - z is ignored
        orientation: float = 0.0,
        baffle_config: Optional[BaffleConfig] = None,
        normal_direction: int = 1,  # +1 for upward normal, -1 for downward
    ):
        # Force z size to 0 for horizontal baffles
        size = Vec3(size.x, size.y, 0.0)
        super().__init__(uid, location, size, orientation, baffle_config)
        self.normal_direction = normal_direction

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate a single horizontal face as solid region."""
        region = SolidRegion(
            name=self.safe_uid,
            patch_type=PatchType.WALL,
            refinement_level=1,
        )

        # Create the 4 corners of the horizontal rectangle
        z = self.location.z
        
        # Local coordinates before rotation
        v00_local = Vec3(0, 0, 0)
        v10_local = Vec3(self.size.x, 0, 0)
        v11_local = Vec3(self.size.x, self.size.y, 0)
        v01_local = Vec3(0, self.size.y, 0)

        # Transform to global coordinates
        v00 = self._transform_point(v00_local)
        v10 = self._transform_point(v10_local)
        v11 = self._transform_point(v11_local)
        v01 = self._transform_point(v01_local)

        # Set z coordinate
        v00 = Vec3(v00.x, v00.y, z)
        v10 = Vec3(v10.x, v10.y, z)
        v11 = Vec3(v11.x, v11.y, z)
        v01 = Vec3(v01.x, v01.y, z)

        # Create triangles
        triangles = rect_to_triangles(v00, v10, v11, v01)

        # Orient based on normal direction
        expected_normal = Vec3(0, 0, self.normal_direction)
        triangles = orient_triangles_outward(triangles, expected_normal)

        region.add_triangles(triangles)
        return [region]

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str) -> "HorizontalBaffleEntity":
        """Create from dictionary configuration."""
        location = Vec3(
            x=data.get("location", {}).get("x", 0),
            y=data.get("location", {}).get("y", 0),
            z=data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=data.get("size", {}).get("x", 1.0),
            y=data.get("size", {}).get("y", 1.0),
            z=0.0,
        )
        orientation = data.get("orientation", 0)
        
        baffle_config = BaffleConfig.from_dict(data)
        normal_direction = data.get("normal_direction", 1)

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
            baffle_config=baffle_config,
            normal_direction=normal_direction,
        )


class VerticalBaffleEntity(BaffleEntity):
    """
    A vertical baffle surface (parallel to XZ or YZ plane).
    
    Used for containment side panels, doors, and vertical partitions.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,  # For vertical: one of x or y should be 0
        orientation: float = 0.0,
        baffle_config: Optional[BaffleConfig] = None,
        face_direction: str = "front",  # front, rear, left, right
    ):
        super().__init__(uid, location, size, orientation, baffle_config)
        self.face_direction = face_direction

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate a single vertical face as solid region."""
        region = SolidRegion(
            name=self.safe_uid,
            patch_type=PatchType.WALL,
            refinement_level=1,
        )

        x0, y0, z0 = self.location.x, self.location.y, self.location.z
        sx, sy, sz = self.size.x, self.size.y, self.size.z

        # Determine vertices and normal based on face direction
        if self.face_direction == "front":  # -Y face
            v00 = Vec3(x0, y0, z0)
            v10 = Vec3(x0 + sx, y0, z0)
            v11 = Vec3(x0 + sx, y0, z0 + sz)
            v01 = Vec3(x0, y0, z0 + sz)
            normal = Vec3(0, -1, 0)
        elif self.face_direction == "rear":  # +Y face
            v00 = Vec3(x0 + sx, y0 + sy, z0)
            v10 = Vec3(x0, y0 + sy, z0)
            v11 = Vec3(x0, y0 + sy, z0 + sz)
            v01 = Vec3(x0 + sx, y0 + sy, z0 + sz)
            normal = Vec3(0, 1, 0)
        elif self.face_direction == "left":  # -X face
            v00 = Vec3(x0, y0 + sy, z0)
            v10 = Vec3(x0, y0, z0)
            v11 = Vec3(x0, y0, z0 + sz)
            v01 = Vec3(x0, y0 + sy, z0 + sz)
            normal = Vec3(-1, 0, 0)
        elif self.face_direction == "right":  # +X face
            v00 = Vec3(x0 + sx, y0, z0)
            v10 = Vec3(x0 + sx, y0 + sy, z0)
            v11 = Vec3(x0 + sx, y0 + sy, z0 + sz)
            v01 = Vec3(x0 + sx, y0, z0 + sz)
            normal = Vec3(1, 0, 0)
        else:
            raise ValueError(f"Unknown face direction: {self.face_direction}")

        # Apply rotation if needed
        if self.orientation != 0:
            normal = normal.rotate_z(self.orientation)
            center = self.location
            v00 = Vec3(v00.x - center.x, v00.y - center.y, v00.z).rotate_z(self.orientation) + center
            v10 = Vec3(v10.x - center.x, v10.y - center.y, v10.z).rotate_z(self.orientation) + center
            v11 = Vec3(v11.x - center.x, v11.y - center.y, v11.z).rotate_z(self.orientation) + center
            v01 = Vec3(v01.x - center.x, v01.y - center.y, v01.z).rotate_z(self.orientation) + center

        triangles = rect_to_triangles(v00, v10, v11, v01)
        triangles = orient_triangles_outward(triangles, normal)

        region.add_triangles(triangles)
        return [region]


@dataclass
class ContainmentOpeningInfo:
    """Opening configuration for containment baffle."""
    uid: str
    location: Vec3  # Global coordinates
    size: Vec3  # Width and height


class ContainmentBaffleEntity(BaffleEntity):
    """
    A containment structure with multiple baffle faces.
    
    Unlike the old ContainmentBoundary that used createBaffles directly from blockMesh,
    this entity generates STL geometry for each enabled face.
    
    Supports openings (holes) in faces for airflow (e.g., ACU return air).
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
        baffle_config: Optional[BaffleConfig] = None,
        faces: Optional[Dict[str, bool]] = None,
        openings: Optional[List] = None,
        openings_side: Optional[str] = None,
    ):
        super().__init__(uid, location, size, orientation, baffle_config)
        self.faces = faces or {
            "top": True,
            "bottom": False,
            "front": True,
            "rear": True,
            "left": False,
            "right": True,
        }
        self.openings = openings or []
        self.openings_side = openings_side

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate solid regions for all enabled faces, with openings cut out."""
        regions = []
        
        x0, y0, z0 = self.location.x, self.location.y, self.location.z
        sx, sy, sz = self.size.x, self.size.y, self.size.z

        face_configs = {
            "top": {
                "vertices": (
                    Vec3(x0, y0, z0 + sz),
                    Vec3(x0 + sx, y0, z0 + sz),
                    Vec3(x0 + sx, y0 + sy, z0 + sz),
                    Vec3(x0, y0 + sy, z0 + sz),
                ),
                "normal": Vec3(0, 0, 1),
            },
            "bottom": {
                "vertices": (
                    Vec3(x0, y0 + sy, z0),
                    Vec3(x0 + sx, y0 + sy, z0),
                    Vec3(x0 + sx, y0, z0),
                    Vec3(x0, y0, z0),
                ),
                "normal": Vec3(0, 0, -1),
            },
            "front": {
                "vertices": (
                    Vec3(x0, y0, z0),
                    Vec3(x0 + sx, y0, z0),
                    Vec3(x0 + sx, y0, z0 + sz),
                    Vec3(x0, y0, z0 + sz),
                ),
                "normal": Vec3(0, -1, 0),
            },
            "rear": {
                "vertices": (
                    Vec3(x0 + sx, y0 + sy, z0),
                    Vec3(x0, y0 + sy, z0),
                    Vec3(x0, y0 + sy, z0 + sz),
                    Vec3(x0 + sx, y0 + sy, z0 + sz),
                ),
                "normal": Vec3(0, 1, 0),
            },
            "left": {
                "vertices": (
                    Vec3(x0, y0 + sy, z0),
                    Vec3(x0, y0, z0),
                    Vec3(x0, y0, z0 + sz),
                    Vec3(x0, y0 + sy, z0 + sz),
                ),
                "normal": Vec3(-1, 0, 0),
            },
            "right": {
                "vertices": (
                    Vec3(x0 + sx, y0, z0),
                    Vec3(x0 + sx, y0 + sy, z0),
                    Vec3(x0 + sx, y0 + sy, z0 + sz),
                    Vec3(x0 + sx, y0, z0 + sz),
                ),
                "normal": Vec3(1, 0, 0),
            },
        }

        for face_name, enabled in self.faces.items():
            if not enabled:
                continue

            config = face_configs.get(face_name)
            if not config:
                continue

            region = SolidRegion(
                name=f"{self.safe_uid}_{face_name}",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )

            v00, v10, v11, v01 = config["vertices"]
            normal = config["normal"]

            # Apply rotation if needed
            if self.orientation != 0:
                center = self.location
                v00 = self._rotate_point(v00, center)
                v10 = self._rotate_point(v10, center)
                v11 = self._rotate_point(v11, center)
                v01 = self._rotate_point(v01, center)
                normal = normal.rotate_z(self.orientation)

            # Check if this face has openings
            if self.openings and self.openings_side == face_name:
                triangles = self._create_face_with_openings(face_name, v00, v10, v11, v01, normal)
            else:
                triangles = rect_to_triangles(v00, v10, v11, v01)
                triangles = orient_triangles_outward(triangles, normal)
            region.add_triangles(triangles)
            regions.append(region)

        return regions

    def _create_face_with_openings(
        self,
        face_name: str,
        v00: Vec3, v10: Vec3, v11: Vec3, v01: Vec3,
        normal: Vec3
    ) -> List[Triangle]:
        """Create face triangles with openings cut out.
        
        Uses grid-based subtraction to create holes in the face.
        """
        # Determine the coordinate system based on face orientation
        if face_name == "left":
            # YZ plane - holes defined by (y, z)
            base_y0 = min(v00.y, v10.y, v11.y, v01.y)
            base_y1 = max(v00.y, v10.y, v11.y, v01.y)
            base_z0 = min(v00.z, v10.z, v11.z, v01.z)
            base_z1 = max(v00.z, v10.z, v11.z, v01.z)
            x = v00.x
            
            holes = []
            for opening in self.openings:
                # Opening location and size
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
                rv00 = Vec3(x, ry0, rz0)
                rv10 = Vec3(x, ry1, rz0)
                rv11 = Vec3(x, ry1, rz1)
                rv01 = Vec3(x, ry0, rz1)
                tris = rect_to_triangles(rv00, rv10, rv11, rv01)
                tris = orient_triangles_outward(tris, normal)
                triangles.extend(tris)
            return triangles
            
        elif face_name == "right":
            # YZ plane at x_max
            base_y0 = min(v00.y, v10.y, v11.y, v01.y)
            base_y1 = max(v00.y, v10.y, v11.y, v01.y)
            base_z0 = min(v00.z, v10.z, v11.z, v01.z)
            base_z1 = max(v00.z, v10.z, v11.z, v01.z)
            x = v00.x
            
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
                rv00 = Vec3(x, ry1, rz0)
                rv10 = Vec3(x, ry0, rz0)
                rv11 = Vec3(x, ry0, rz1)
                rv01 = Vec3(x, ry1, rz1)
                tris = rect_to_triangles(rv00, rv10, rv11, rv01)
                tris = orient_triangles_outward(tris, normal)
                triangles.extend(tris)
            return triangles
            
        elif face_name == "front":
            # XZ plane at y_min
            base_x0 = min(v00.x, v10.x, v11.x, v01.x)
            base_x1 = max(v00.x, v10.x, v11.x, v01.x)
            base_z0 = min(v00.z, v10.z, v11.z, v01.z)
            base_z1 = max(v00.z, v10.z, v11.z, v01.z)
            y = v00.y
            
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
                rv00 = Vec3(rx0, y, rz0)
                rv10 = Vec3(rx1, y, rz0)
                rv11 = Vec3(rx1, y, rz1)
                rv01 = Vec3(rx0, y, rz1)
                tris = rect_to_triangles(rv00, rv10, rv11, rv01)
                tris = orient_triangles_outward(tris, normal)
                triangles.extend(tris)
            return triangles
            
        elif face_name == "rear":
            # XZ plane at y_max
            base_x0 = min(v00.x, v10.x, v11.x, v01.x)
            base_x1 = max(v00.x, v10.x, v11.x, v01.x)
            base_z0 = min(v00.z, v10.z, v11.z, v01.z)
            base_z1 = max(v00.z, v10.z, v11.z, v01.z)
            y = v00.y
            
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
                rv00 = Vec3(rx1, y, rz0)
                rv10 = Vec3(rx0, y, rz0)
                rv11 = Vec3(rx0, y, rz1)
                rv01 = Vec3(rx1, y, rz1)
                tris = rect_to_triangles(rv00, rv10, rv11, rv01)
                tris = orient_triangles_outward(tris, normal)
                triangles.extend(tris)
            return triangles
        
        # For top/bottom or unknown, just return the full face
        triangles = rect_to_triangles(v00, v10, v11, v01)
        return orient_triangles_outward(triangles, normal)

    def _subtract_rectangles_yz(
        self,
        base: tuple,
        holes: List[tuple],
    ) -> List[tuple]:
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
        base: tuple,
        holes: List[tuple],
    ) -> List[tuple]:
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

    def _rotate_point(self, point: Vec3, center: Vec3) -> Vec3:
        """Rotate a point around the center by self.orientation degrees."""
        offset = Vec3(point.x - center.x, point.y - center.y, 0)
        rotated = offset.rotate_z(self.orientation)
        return Vec3(center.x + rotated.x, center.y + rotated.y, point.z)

    @property
    def face_zone_names(self) -> List[str]:
        """Get all faceZone names for enabled faces."""
        names = []
        for face_name, enabled in self.faces.items():
            if enabled:
                names.append(f"{self.safe_uid}_{face_name}_faceZone")
        return names

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str) -> "ContainmentBaffleEntity":
        """Create from dictionary configuration."""
        location = Vec3(
            x=data.get("location", {}).get("x", 0),
            y=data.get("location", {}).get("y", 0),
            z=data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=data.get("size", {}).get("x", 1.0),
            y=data.get("size", {}).get("y", 1.0),
            z=data.get("size", {}).get("z", 2.0),
        )
        orientation = data.get("orientation", 0)
        
        baffle_config = BaffleConfig.from_dict(data)
        
        faces = data.get("faces", {
            "top": True,
            "bottom": False,
            "front": True,
            "rear": True,
            "left": False,
            "right": True,
        })

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
            baffle_config=baffle_config,
            faces=faces,
        )


class RaisedFloorBaffleEntity(BaffleEntity):
    """
    Raised floor baffle with openings.
    
    The main floor panel is a wall baffle, while openings can be
    perforated (with porosity) or transparent (cyclic).
    """

    def __init__(
        self,
        uid: str,
        height: float,
        v_min: Vec3,
        v_max: Vec3,
        baffle_config: Optional[BaffleConfig] = None,
        openings: Optional[List["OpeningBaffleEntity"]] = None,
    ):
        location = Vec3(v_min.x, v_min.y, height)
        size = Vec3(v_max.x - v_min.x, v_max.y - v_min.y, 0)
        super().__init__(uid, location, size, 0.0, baffle_config)
        self.height = height
        self.v_min = v_min
        self.v_max = v_max
        self.openings = openings or []

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate the floor panel with holes for openings."""
        # For now, generate the full floor panel
        # Openings will be generated as separate entities
        region = SolidRegion(
            name=self.safe_uid,
            patch_type=PatchType.WALL,
            refinement_level=1,
        )

        z = self.height
        v00 = Vec3(self.v_min.x, self.v_min.y, z)
        v10 = Vec3(self.v_max.x, self.v_min.y, z)
        v11 = Vec3(self.v_max.x, self.v_max.y, z)
        v01 = Vec3(self.v_min.x, self.v_max.y, z)

        # If there are openings, we need to subtract them from the floor
        if self.openings:
            triangles = self._create_floor_with_openings(v00, v10, v11, v01)
        else:
            triangles = rect_to_triangles(v00, v10, v11, v01)
            triangles = orient_triangles_outward(triangles, Vec3(0, 0, 1))

        region.add_triangles(triangles)
        return [region]

    def _create_floor_with_openings(
        self,
        v00: Vec3, v10: Vec3, v11: Vec3, v01: Vec3
    ) -> List[Triangle]:
        """Create floor triangles with holes for openings.
        
        This uses a simple grid-based subtraction approach.
        """
        z = self.height
        
        # Collect all x and y coordinates
        xs = {v00.x, v10.x}
        ys = {v00.y, v01.y}
        
        # Add opening boundaries
        for opening in self.openings:
            ox, oy = opening.location.x, opening.location.y
            xs.add(ox)
            xs.add(ox + opening.size.x)
            ys.add(oy)
            ys.add(oy + opening.size.y)
        
        xs_sorted = sorted(xs)
        ys_sorted = sorted(ys)
        
        triangles = []
        
        # Generate grid cells, skipping those inside openings
        for i in range(len(xs_sorted) - 1):
            for j in range(len(ys_sorted) - 1):
                x0, x1 = xs_sorted[i], xs_sorted[i + 1]
                y0, y1 = ys_sorted[j], ys_sorted[j + 1]
                
                # Check if this cell is inside any opening
                cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                is_opening = False
                for opening in self.openings:
                    ox, oy = opening.location.x, opening.location.y
                    if (ox <= cx <= ox + opening.size.x and
                        oy <= cy <= oy + opening.size.y):
                        is_opening = True
                        break
                
                if not is_opening:
                    # Create triangles for this cell
                    cell_v00 = Vec3(x0, y0, z)
                    cell_v10 = Vec3(x1, y0, z)
                    cell_v11 = Vec3(x1, y1, z)
                    cell_v01 = Vec3(x0, y1, z)
                    
                    cell_tris = rect_to_triangles(cell_v00, cell_v10, cell_v11, cell_v01)
                    cell_tris = orient_triangles_outward(cell_tris, Vec3(0, 0, 1))
                    triangles.extend(cell_tris)
        
        return triangles


class OpeningBaffleEntity(BaffleEntity):
    """
    An opening in a floor or ceiling.
    
    Openings can be:
    - Transparent (porosity=1): Pure cyclic, air flows freely
    - Perforated (0 < porosity < 1): Uses porousBafflePressure
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,  # (x, y, z_height)
        size: Vec3,      # (width, depth, 0)
        baffle_config: Optional[BaffleConfig] = None,
    ):
        # Default to perforated with 80% porosity for openings
        if baffle_config is None:
            baffle_config = BaffleConfig(
                baffle_type=BaffleType.PERFORATED,
                porosity=0.8
            )
        super().__init__(uid, location, size, 0.0, baffle_config)

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate a horizontal surface for the opening."""
        region = SolidRegion(
            name=self.safe_uid,
            patch_type=PatchType.WALL,
            refinement_level=1,
        )

        z = self.location.z
        v00 = Vec3(self.location.x, self.location.y, z)
        v10 = Vec3(self.location.x + self.size.x, self.location.y, z)
        v11 = Vec3(self.location.x + self.size.x, self.location.y + self.size.y, z)
        v01 = Vec3(self.location.x, self.location.y + self.size.y, z)

        triangles = rect_to_triangles(v00, v10, v11, v01)
        triangles = orient_triangles_outward(triangles, Vec3(0, 0, 1))

        region.add_triangles(triangles)
        return [region]

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str, z_height: float) -> "OpeningBaffleEntity":
        """Create from dictionary configuration."""
        location = Vec3(
            x=data.get("location", {}).get("x", 0),
            y=data.get("location", {}).get("y", 0),
            z=z_height,
        )
        size = Vec3(
            x=data.get("size", {}).get("x", 0.6),
            y=data.get("size", {}).get("y", 0.6),
            z=0.0,
        )
        
        # Get baffle config, default to perforated with 80% porosity
        porosity = data.get("porosity", 0.8)
        baffle_type_str = data.get("baffle_type", data.get("baffleType", "perforated"))
        
        baffle_config = BaffleConfig(
            baffle_type=BaffleType.PERFORATED if baffle_type_str == "perforated" else BaffleType.TRANSPARENT,
            porosity=porosity,
        )

        return cls(
            uid=uid,
            location=location,
            size=size,
            baffle_config=baffle_config,
        )


class FalseCeilingBaffleEntity(RaisedFloorBaffleEntity):
    """
    False ceiling baffle with openings.
    
    Similar to raised floor but at ceiling level with downward normal.
    """
    pass  # Uses same logic as RaisedFloorBaffleEntity


def get_createBaffles_faceZone_cmd(baffle: BaffleEntity) -> str:
    """
    Generate createBafflesDict command for a baffle entity.
    
    For wall type: creates master/slave wall boundaries
    For perforated/transparent: creates master/slave cyclic boundaries
    """
    if baffle.is_wall:
        # Wall type - both sides are wall
        return f"""
    {baffle.safe_uid}
    {{
        type        faceZone;
        zoneName    {baffle.face_zone_name};
        
        patchPairs
        {{
            type        wall;
            
            patchFields
            {{
                T
                {{
                    type        zeroGradient;
                }}
                U
                {{
                    type        noSlip;
                }}
            }}
        }}
    }}
"""
    else:
        # Perforated or transparent - cyclic with optional porousBafflePressure
        return f"""
    {baffle.safe_uid}
    {{
        type        faceZone;
        zoneName    {baffle.face_zone_name};
        
        patches
        {{
            master
            {{
                name            {baffle.master_patch_name};
                type            cyclic;
                neighbourPatch  {baffle.slave_patch_name};
            }}
            slave
            {{
                name            {baffle.slave_patch_name};
                type            cyclic;
                neighbourPatch  {baffle.master_patch_name};
            }}
        }}
    }}
"""


def get_snappyHexMesh_faceZone_cmd(baffle: BaffleEntity, refinement_level: int = 1) -> str:
    """
    Generate snappyHexMeshDict refinementSurfaces entry for faceZone mode.
    
    This creates a faceZone (not splitting space) that will be converted
    to wall/cyclic boundaries by createBaffles later.
    """
    return f"""
        {baffle.safe_uid}
        {{
            level ({refinement_level} {refinement_level});
            faceZone {baffle.face_zone_name};
            faceType internal;
        }}
"""
