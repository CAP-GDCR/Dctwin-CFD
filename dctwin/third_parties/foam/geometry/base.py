"""
Base classes for geometry entities and sub-entities.

This module defines the abstract base classes Entity and SubEntity,
along with fundamental data structures for 3D geometry.
"""

from __future__ import annotations

import abc
import math
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Tuple, Dict, Optional, Sequence


@dataclass(frozen=True)
class Vec3:
    """
    A 3D vector/point with x, y, z coordinates.

    This is an immutable dataclass used for representing vertices,
    normals, and other 3D coordinates.
    """
    x: float
    y: float
    z: float

    def __add__(self, other: Vec3) -> Vec3:
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: Vec3) -> Vec3:
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def __mul__(self, scalar: float) -> Vec3:
        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)

    def __rmul__(self, scalar: float) -> Vec3:
        return self.__mul__(scalar)

    def __neg__(self) -> Vec3:
        return Vec3(-self.x, -self.y, -self.z)

    def dot(self, other: Vec3) -> float:
        """Compute dot product with another vector."""
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: Vec3) -> Vec3:
        """Compute cross product with another vector."""
        return Vec3(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
        )

    def length(self) -> float:
        """Compute the length/magnitude of the vector."""
        return math.sqrt(self.x * self.x + self.y * self.y + self.z * self.z)

    def normalize(self) -> Vec3:
        """Return a unit vector in the same direction."""
        length = self.length()
        if length < 1e-10:
            return Vec3(0, 0, 0)
        return Vec3(self.x / length, self.y / length, self.z / length)

    def to_tuple(self) -> Tuple[float, float, float]:
        """Convert to tuple."""
        return (self.x, self.y, self.z)

    def rotate_z(self, angle_deg: float) -> Vec3:
        """Rotate the vector around the Z axis by the given angle in degrees."""
        angle_rad = math.radians(angle_deg)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        return Vec3(
            self.x * cos_a - self.y * sin_a,
            self.x * sin_a + self.y * cos_a,
            self.z
        )


# Triangle is a tuple of three vertices
Triangle = Tuple[Vec3, Vec3, Vec3]


@dataclass
class BoundingBox:
    """Axis-aligned bounding box defined by min and max corners."""
    v_min: Vec3
    v_max: Vec3

    @property
    def size(self) -> Vec3:
        """Get the size of the bounding box."""
        return self.v_max - self.v_min

    @property
    def center(self) -> Vec3:
        """Get the center point of the bounding box."""
        return Vec3(
            (self.v_min.x + self.v_max.x) / 2,
            (self.v_min.y + self.v_max.y) / 2,
            (self.v_min.z + self.v_max.z) / 2,
        )

    def contains(self, point: Vec3) -> bool:
        """Check if a point is inside the bounding box."""
        return (
            self.v_min.x <= point.x <= self.v_max.x and
            self.v_min.y <= point.y <= self.v_max.y and
            self.v_min.z <= point.z <= self.v_max.z
        )


class FaceType(Enum):
    """Types of faces on a hexahedral entity."""
    FRONT = "front"      # -Y direction (orientation 0)
    REAR = "rear"        # +Y direction (orientation 0)
    LEFT = "left"        # -X direction (orientation 0)
    RIGHT = "right"      # +X direction (orientation 0)
    TOP = "top"          # +Z direction
    BOTTOM = "bottom"    # -Z direction


class PatchType(Enum):
    """OpenFOAM patch types."""
    WALL = "wall"
    PATCH = "patch"
    CYCLIC = "cyclic"
    SYMMETRY_PLANE = "symmetryPlane"
    EMPTY = "empty"


_SAFE_NAME_RE = re.compile(r"[^0-9A-Za-z_]+")


def sanitize_name(name: Optional[str], prefix: str = "p") -> str:
    """Return a safe OpenFOAM patch name from an arbitrary string."""
    text = "" if name is None else str(name)
    text = text.strip()
    if not text:
        return prefix
    safe = _SAFE_NAME_RE.sub("_", text).strip("_")
    if not safe:
        safe = prefix
    if safe[0].isdigit():
        safe = f"{prefix}_{safe}"
    return safe


@dataclass
class SolidRegion:
    """
    Represents a named solid region within an STL file.

    Each solid region corresponds to an OpenFOAM patch.
    """
    name: str
    triangles: List[Triangle] = field(default_factory=list)
    patch_type: PatchType = PatchType.WALL
    neighbour_patch: Optional[str] = None  # For cyclic patches
    refinement_level: int = 1

    def __post_init__(self) -> None:
        self.name = sanitize_name(self.name)
        if self.neighbour_patch:
            self.neighbour_patch = sanitize_name(self.neighbour_patch)

    def add_triangle(self, tri: Triangle) -> None:
        """Add a triangle to this solid region."""
        self.triangles.append(tri)

    def add_triangles(self, tris: Sequence[Triangle]) -> None:
        """Add multiple triangles to this solid region."""
        self.triangles.extend(tris)


def compute_triangle_normal(tri: Triangle) -> Vec3:
    """
    Compute the normal vector of a triangle using the right-hand rule.

    The vertices should be ordered counter-clockwise when viewed from
    the direction the normal points.
    """
    a, b, c = tri
    edge1 = b - a
    edge2 = c - a
    return edge1.cross(edge2).normalize()


def rect_to_triangles(v00: Vec3, v10: Vec3, v11: Vec3, v01: Vec3) -> List[Triangle]:
    """
    Convert a rectangle (4 vertices) to 2 triangles.

    Vertices should be ordered:
        v01 --- v11
         |       |
        v00 --- v10

    The triangles are created with vertices in counter-clockwise order
    when viewed from outside (positive normal direction).
    """
    return [
        (v00, v10, v11),
        (v00, v11, v01),
    ]


def orient_triangles_outward(
    triangles: List[Triangle],
    expected_normal: Vec3
) -> List[Triangle]:
    """
    Ensure all triangles have normals pointing in the expected direction.

    If a triangle's computed normal points in the opposite direction,
    its vertices are reordered to flip the normal.
    """
    oriented = []
    for tri in triangles:
        normal = compute_triangle_normal(tri)
        if normal.dot(expected_normal) < 0:
            # Flip triangle by swapping two vertices
            a, b, c = tri
            oriented.append((a, c, b))
        else:
            oriented.append(tri)
    return oriented


class SubEntity(abc.ABC):
    """
    Abstract base class for sub-entities.

    SubEntities are parts of an Entity that require separate patch definitions,
    such as server inlets/outlets or air leakage regions on a rack.

    SubEntities do not generate separate STL files; instead, they define
    solid regions within their parent Entity's STL file.
    """

    def __init__(
        self,
        uid: str,
        patch_type: PatchType = PatchType.WALL,
        neighbour_patch: Optional[str] = None,
        refinement_level: int = 1,
    ):
        self.uid = uid
        self.patch_type = patch_type
        self.neighbour_patch = neighbour_patch
        self.refinement_level = refinement_level

    @abc.abstractmethod
    def get_solid_region(self, parent_bbox: BoundingBox, orientation: float) -> SolidRegion:
        """
        Generate the solid region for this sub-entity.

        Args:
            parent_bbox: The bounding box of the parent entity
            orientation: The orientation angle of the parent entity in degrees

        Returns:
            A SolidRegion containing the triangles for this sub-entity
        """
        pass

    @property
    def solid_name(self) -> str:
        """Get the name for this sub-entity's solid region."""
        return sanitize_name(self.uid)


class Entity(abc.ABC):
    """
    Abstract base class for geometry entities.

    An Entity represents a physical object in the data center (Rack, ACU, etc.)
    that generates an STL file for mesh generation.

    Each Entity can contain SubEntities that define different patches
    within the same STL file.
    """

    def __init__(
        self,
        uid: str,
        location: Vec3,
        size: Vec3,
        orientation: float = 0.0,
    ):
        """
        Initialize an Entity.

        Args:
            uid: Unique identifier for this entity
            location: The location of the entity's origin (min corner before rotation)
            size: The size of the entity (x, y, z dimensions)
            orientation: Rotation angle around Z axis in degrees (0, 90, 180, 270)
        """
        self.uid = uid
        self.location = location
        self.size = size
        self.orientation = self._normalize_orientation(orientation)
        self.sub_entities: List[SubEntity] = []

        # Validate orientation
        if self.orientation not in [0, 90, 180, 270]:
            raise ValueError(
                f"Invalid orientation {orientation} for entity '{uid}'. "
                f"Must be 0, 90, 180, or 270 degrees."
            )

    def _normalize_orientation(self, orientation: float) -> float:
        """Normalize orientation to 0, 90, 180, or 270."""
        orientation = orientation % 360
        if orientation < 0:
            orientation += 360
        # Round to nearest valid orientation
        valid = [0, 90, 180, 270]
        return min(valid, key=lambda x: abs(x - orientation))

    @property
    def bounding_box(self) -> BoundingBox:
        """
        Get the axis-aligned bounding box of this entity after rotation.

        The bounding box accounts for the entity's orientation.
        """
        # Compute the 4 corners of the base rectangle
        corners = [
            Vec3(0, 0, 0),
            Vec3(self.size.x, 0, 0),
            Vec3(self.size.x, self.size.y, 0),
            Vec3(0, self.size.y, 0),
        ]

        # Rotate corners around origin
        rotated_corners = [c.rotate_z(self.orientation) for c in corners]

        # Find min/max
        xs = [c.x for c in rotated_corners]
        ys = [c.y for c in rotated_corners]

        v_min = Vec3(
            self.location.x + min(xs),
            self.location.y + min(ys),
            self.location.z,
        )
        v_max = Vec3(
            self.location.x + max(xs),
            self.location.y + max(ys),
            self.location.z + self.size.z,
        )

        return BoundingBox(v_min, v_max)

    @property
    def stl_filename(self) -> str:
        """Get the STL filename for this entity."""
        return f"{sanitize_name(self.uid)}.stl"

    @property
    def safe_uid(self) -> str:
        """Get a safe identifier for OpenFOAM dictionary usage."""
        return sanitize_name(self.uid)

    def add_sub_entity(self, sub_entity: SubEntity) -> None:
        """Add a sub-entity to this entity."""
        self.sub_entities.append(sub_entity)

    def _transform_point(self, local_point: Vec3) -> Vec3:
        """
        Transform a point from local coordinates to global coordinates.

        Local coordinates have origin at the entity's location before rotation.
        """
        # First rotate around origin
        rotated = local_point.rotate_z(self.orientation)
        # Then translate to entity location
        return rotated + self.location

    def _get_face_normal(self, face: FaceType) -> Vec3:
        """
        Get the outward normal vector for a face, accounting for orientation.
        """
        base_normals = {
            FaceType.FRONT: Vec3(0, -1, 0),
            FaceType.REAR: Vec3(0, 1, 0),
            FaceType.LEFT: Vec3(-1, 0, 0),
            FaceType.RIGHT: Vec3(1, 0, 0),
            FaceType.TOP: Vec3(0, 0, 1),
            FaceType.BOTTOM: Vec3(0, 0, -1),
        }
        return base_normals[face].rotate_z(self.orientation)

    def _get_face_vertices(self, face: FaceType) -> Tuple[Vec3, Vec3, Vec3, Vec3]:
        """
        Get the 4 vertices of a face in local coordinates.

        Returns vertices in order: v00, v10, v11, v01 where:
        - For vertical faces: v00 is bottom-left, v11 is top-right
        - For horizontal faces: v00 is front-left, v11 is rear-right
        """
        sx, sy, sz = self.size.x, self.size.y, self.size.z

        face_vertices = {
            FaceType.FRONT: (
                Vec3(0, 0, 0),
                Vec3(sx, 0, 0),
                Vec3(sx, 0, sz),
                Vec3(0, 0, sz),
            ),
            FaceType.REAR: (
                Vec3(sx, sy, 0),
                Vec3(0, sy, 0),
                Vec3(0, sy, sz),
                Vec3(sx, sy, sz),
            ),
            FaceType.LEFT: (
                Vec3(0, sy, 0),
                Vec3(0, 0, 0),
                Vec3(0, 0, sz),
                Vec3(0, sy, sz),
            ),
            FaceType.RIGHT: (
                Vec3(sx, 0, 0),
                Vec3(sx, sy, 0),
                Vec3(sx, sy, sz),
                Vec3(sx, 0, sz),
            ),
            FaceType.TOP: (
                Vec3(0, 0, sz),
                Vec3(sx, 0, sz),
                Vec3(sx, sy, sz),
                Vec3(0, sy, sz),
            ),
            FaceType.BOTTOM: (
                Vec3(0, sy, 0),
                Vec3(sx, sy, 0),
                Vec3(sx, 0, 0),
                Vec3(0, 0, 0),
            ),
        }
        return face_vertices[face]

    def _create_face_triangles(
        self,
        face: FaceType,
        offset: Vec3 = Vec3(0, 0, 0),
        size: Optional[Vec3] = None,
    ) -> List[Triangle]:
        """
        Create triangles for a face (or portion of a face).

        Args:
            face: Which face of the hexahedron
            offset: Offset from the face's origin (in local coordinates)
            size: Size of the face region (None = full face size)

        Returns:
            List of triangles in global coordinates with correct normals
        """
        if size is None:
            # Use full face dimensions
            if face in [FaceType.FRONT, FaceType.REAR]:
                size = Vec3(self.size.x, 0, self.size.z)
            elif face in [FaceType.LEFT, FaceType.RIGHT]:
                size = Vec3(0, self.size.y, self.size.z)
            else:  # TOP, BOTTOM
                size = Vec3(self.size.x, self.size.y, 0)

        # Get base vertices for the face
        v00, v10, v11, v01 = self._get_face_vertices(face)

        # Apply offset and size adjustments based on face type
        if face == FaceType.FRONT:
            v00 = Vec3(offset.x, 0, offset.z)
            v10 = Vec3(offset.x + size.x, 0, offset.z)
            v11 = Vec3(offset.x + size.x, 0, offset.z + size.z)
            v01 = Vec3(offset.x, 0, offset.z + size.z)
        elif face == FaceType.REAR:
            v00 = Vec3(self.size.x - offset.x, self.size.y, offset.z)
            v10 = Vec3(self.size.x - offset.x - size.x, self.size.y, offset.z)
            v11 = Vec3(self.size.x - offset.x - size.x, self.size.y, offset.z + size.z)
            v01 = Vec3(self.size.x - offset.x, self.size.y, offset.z + size.z)
        elif face == FaceType.LEFT:
            v00 = Vec3(0, self.size.y - offset.y, offset.z)
            v10 = Vec3(0, self.size.y - offset.y - size.y, offset.z)
            v11 = Vec3(0, self.size.y - offset.y - size.y, offset.z + size.z)
            v01 = Vec3(0, self.size.y - offset.y, offset.z + size.z)
        elif face == FaceType.RIGHT:
            v00 = Vec3(self.size.x, offset.y, offset.z)
            v10 = Vec3(self.size.x, offset.y + size.y, offset.z)
            v11 = Vec3(self.size.x, offset.y + size.y, offset.z + size.z)
            v01 = Vec3(self.size.x, offset.y, offset.z + size.z)
        elif face == FaceType.TOP:
            v00 = Vec3(offset.x, offset.y, self.size.z)
            v10 = Vec3(offset.x + size.x, offset.y, self.size.z)
            v11 = Vec3(offset.x + size.x, offset.y + size.y, self.size.z)
            v01 = Vec3(offset.x, offset.y + size.y, self.size.z)
        elif face == FaceType.BOTTOM:
            v00 = Vec3(offset.x, self.size.y - offset.y, 0)
            v10 = Vec3(offset.x + size.x, self.size.y - offset.y, 0)
            v11 = Vec3(offset.x + size.x, self.size.y - offset.y - size.y, 0)
            v01 = Vec3(offset.x, self.size.y - offset.y - size.y, 0)

        # Transform to global coordinates
        v00 = self._transform_point(v00)
        v10 = self._transform_point(v10)
        v11 = self._transform_point(v11)
        v01 = self._transform_point(v01)

        # Create triangles
        triangles = rect_to_triangles(v00, v10, v11, v01)

        # Ensure correct normal direction
        expected_normal = self._get_face_normal(face)
        return orient_triangles_outward(triangles, expected_normal)

    @abc.abstractmethod
    def get_solid_regions(self) -> List[SolidRegion]:
        """
        Generate all solid regions for this entity.

        Returns a list of SolidRegion objects, each defining a named region
        (patch) within the STL file.
        """
        pass

    def get_wall_solid_regions(
        self,
        exclude_faces: Optional[List[FaceType]] = None,
    ) -> List[SolidRegion]:
        """
        Generate wall solid regions for all faces except excluded ones.

        This is a helper method for creating wall patches for the main
        body of an entity.

        Args:
            exclude_faces: List of faces to exclude (they will be handled
                          by sub-entities or other regions)

        Returns:
            List of SolidRegion objects for wall faces
        """
        if exclude_faces is None:
            exclude_faces = []

        regions = []
        for face in FaceType:
            if face not in exclude_faces:
                region = SolidRegion(
                    name=f"{self.uid}_wall_{face.value}",
                    patch_type=PatchType.WALL,
                    refinement_level=1,
                )
                region.add_triangles(self._create_face_triangles(face))
                regions.append(region)

        return regions

    def validate(self) -> None:
        """
        Validate the entity configuration.

        Raises ValueError if the configuration is invalid.
        """
        if self.size.x <= 0 or self.size.y <= 0 or self.size.z <= 0:
            raise ValueError(f"Entity '{self.uid}' has invalid size: {self.size}")


def get_face_mapping(orientation: float) -> Dict[str, str]:
    """
    Get the mapping from local face names to global face names based on orientation.

    When an entity is rotated, its local faces (front, rear, left, right)
    map to different global directions.

    Args:
        orientation: Rotation angle in degrees (0, 90, 180, 270)

    Returns:
        Dictionary mapping local face names to global face names
    """
    if orientation == 0:
        return {
            'front': 'front', 'rear': 'rear',
            'left': 'left', 'right': 'right',
            'top': 'top', 'bottom': 'bottom',
        }
    elif orientation == 90:
        return {
            'front': 'right', 'rear': 'left',
            'left': 'front', 'right': 'rear',
            'top': 'top', 'bottom': 'bottom',
        }
    elif orientation == 180:
        return {
            'front': 'rear', 'rear': 'front',
            'left': 'right', 'right': 'left',
            'top': 'top', 'bottom': 'bottom',
        }
    elif orientation == 270:
        return {
            'front': 'left', 'rear': 'right',
            'left': 'rear', 'right': 'front',
            'top': 'top', 'bottom': 'bottom',
        }
    else:
        raise ValueError(f"Invalid orientation: {orientation}")
