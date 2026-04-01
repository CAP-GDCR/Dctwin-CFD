"""
Room boundary handling.

This module handles room boundaries including raised floors, false ceilings,
and their openings.

New STL-based workflow:
- RoomWallEntity: Generates STL for polygon room outer walls
- Baffles use faceZone mode in snappyHexMesh to avoid sawteeth edges
- createBaffles converts faceZone to wall/cyclic boundaries
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Tuple
import math

from .base import (
    Entity, Vec3, BoundingBox, SolidRegion, Triangle,
    PatchType, sanitize_name,
    rect_to_triangles, orient_triangles_outward,
)
from .baffle import (
    BaffleType, BaffleConfig, BaffleEntity,
    RaisedFloorBaffleEntity, FalseCeilingBaffleEntity,
    OpeningBaffleEntity, ContainmentBaffleEntity,
)


class RoomWallEntity(Entity):
    """
    Entity representing the room outer walls as a polygon enclosure.
    
    This generates STL geometry for:
    - Floor (bottom face)
    - Ceiling (top face)
    - Side walls (one face per polygon edge)
    
    The STL is used by snappyHexMesh to remove cells outside the room.
    BlockMesh still defines the bounding box, but cells outside the
    room polygon are deleted during mesh generation.
    """

    def __init__(
        self,
        uid: str,
        plane: List[Vec3],  # Polygon vertices (XY plane, Z=0)
        height: float,
    ):
        """
        Initialize room wall entity.
        
        Args:
            uid: Unique identifier
            plane: List of Vec3 defining the room outline (Z is ignored)
            height: Room height
        """
        # Compute bounding box
        xs = [p.x for p in plane]
        ys = [p.y for p in plane]
        v_min = Vec3(min(xs), min(ys), 0)
        v_max = Vec3(max(xs), max(ys), height)
        
        size = Vec3(v_max.x - v_min.x, v_max.y - v_min.y, height)
        super().__init__(uid, v_min, size, 0.0)
        
        self.plane = plane
        self.height = height
        self.v_min = v_min
        self.v_max = v_max

    @property
    def stl_filename(self) -> str:
        return "room_wall.stl"

    def get_solid_regions(self) -> List[SolidRegion]:
        """Generate solid regions for floor, ceiling, and side walls."""
        regions = []
        
        # Floor (Z = 0)
        floor_region = self._create_polygon_face("room_wall_floor", 0, normal_up=False)
        regions.append(floor_region)
        
        # Ceiling (Z = height)
        ceiling_region = self._create_polygon_face("room_wall_ceiling", self.height, normal_up=True)
        regions.append(ceiling_region)
        
        # Side walls
        wall_regions = self._create_side_walls()
        regions.extend(wall_regions)
        
        return regions

    def _create_polygon_face(self, name: str, z: float, normal_up: bool) -> SolidRegion:
        """
        Create a horizontal polygon face (floor or ceiling).
        
        Uses ear-clipping triangulation for convex/concave polygons.
        """
        region = SolidRegion(
            name=name,
            patch_type=PatchType.WALL,
            refinement_level=1,
        )
        
        # Get 2D polygon vertices
        vertices_2d = [(p.x, p.y) for p in self.plane]
        
        # Triangulate the polygon
        triangles_2d = self._triangulate_polygon(vertices_2d)
        
        # Convert to 3D triangles
        expected_normal = Vec3(0, 0, 1 if normal_up else -1)
        
        for tri_2d in triangles_2d:
            v0 = Vec3(tri_2d[0][0], tri_2d[0][1], z)
            v1 = Vec3(tri_2d[1][0], tri_2d[1][1], z)
            v2 = Vec3(tri_2d[2][0], tri_2d[2][1], z)
            
            tri = (v0, v1, v2)
            # Orient correctly
            oriented = orient_triangles_outward([tri], expected_normal)
            region.add_triangles(oriented)
        
        return region

    def _create_side_walls(self) -> List[SolidRegion]:
        """Create vertical wall faces for each polygon edge."""
        regions = []
        n = len(self.plane)
        
        for i in range(n):
            p0 = self.plane[i]
            p1 = self.plane[(i + 1) % n]
            
            region = SolidRegion(
                name=f"room_wall_side_{i}",
                patch_type=PatchType.WALL,
                refinement_level=1,
            )
            
            # Four corners of the wall quad
            v00 = Vec3(p0.x, p0.y, 0)
            v10 = Vec3(p1.x, p1.y, 0)
            v11 = Vec3(p1.x, p1.y, self.height)
            v01 = Vec3(p0.x, p0.y, self.height)
            
            # Compute outward normal (perpendicular to edge, pointing outward)
            edge = Vec3(p1.x - p0.x, p1.y - p0.y, 0)
            # Outward normal is perpendicular to edge (for CCW polygon, use right-hand rule)
            # Assuming CCW ordering, outward = rotate edge 90° CW
            normal = Vec3(edge.y, -edge.x, 0).normalize()
            
            triangles = rect_to_triangles(v00, v10, v11, v01)
            triangles = orient_triangles_outward(triangles, normal)
            region.add_triangles(triangles)
            regions.append(region)
        
        return regions

    def _triangulate_polygon(self, vertices: List[Tuple[float, float]]) -> List[Tuple[Tuple[float, float], Tuple[float, float], Tuple[float, float]]]:
        """
        Triangulate a simple polygon using ear-clipping algorithm.
        
        For simple convex/concave polygons without holes.
        Returns list of triangles as ((x0,y0), (x1,y1), (x2,y2)).
        """
        if len(vertices) < 3:
            return []
        
        if len(vertices) == 3:
            return [(vertices[0], vertices[1], vertices[2])]
        
        # Simple ear-clipping for convex polygons
        # For a proper implementation, we'd need more robust triangulation
        # Here we use a fan triangulation from centroid for simplicity
        
        # Compute centroid
        cx = sum(v[0] for v in vertices) / len(vertices)
        cy = sum(v[1] for v in vertices) / len(vertices)
        center = (cx, cy)
        
        triangles = []
        n = len(vertices)
        for i in range(n):
            v0 = vertices[i]
            v1 = vertices[(i + 1) % n]
            triangles.append((center, v0, v1))
        
        return triangles

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str = "room") -> "RoomWallEntity":
        """Create from dictionary configuration."""
        geometry = data.get("geometry", {})
        plane_data = geometry.get("plane", [])
        height = geometry.get("height", 3.0)
        
        plane = []
        for point in plane_data:
            plane.append(Vec3(
                x=point.get("x", 0),
                y=point.get("y", 0),
                z=0,
            ))
        
        # Default to rectangle if no plane defined
        if not plane:
            plane = [
                Vec3(0, 0, 0),
                Vec3(10, 0, 0),
                Vec3(10, 8, 0),
                Vec3(0, 8, 0),
            ]
        
        return cls(uid=uid, plane=plane, height=height)


@dataclass
class OpeningConfig:
    """Configuration for an opening in a floor or ceiling."""
    uid: str
    location: Vec3
    size: Vec3  # x, y dimensions (z is 0 for horizontal surfaces)
    baffle_type: BaffleType = BaffleType.PERFORATED
    porosity: float = 0.8  # Default 80% open for floor openings


@dataclass
class RoomBoundary:
    """
    Room boundary configuration.

    The room boundary is defined by the blockMeshDict and does not
    require STL geometry. However, it does require proper patch naming
    for boundary conditions.
    """
    v_min: Vec3
    v_max: Vec3

    @property
    def floor_patch_name(self) -> str:
        return "room_wall_floor"

    @property
    def ceiling_patch_name(self) -> str:
        return "room_wall_ceiling"

    @property
    def wall_patch_names(self) -> List[str]:
        return [
            "room_wall_side_0",
            "room_wall_side_1",
            "room_wall_side_2",
            "room_wall_side_3",
        ]

    @property
    def all_patch_names(self) -> List[str]:
        return self.wall_patch_names + [self.floor_patch_name, self.ceiling_patch_name]


@dataclass
class RaisedFloorBoundary:
    """
    Raised floor boundary handling.

    The raised floor is an internal surface at a certain height above
    the actual floor. 
    
    New STL-based workflow:
    - Generates STL for the floor panel (with holes for openings)
    - Generates separate STL for each opening
    - Uses faceZone mode in snappyHexMesh
    - createBaffles converts to wall/cyclic boundaries
    """
    height: float
    v_min: Vec3  # Room min corner
    v_max: Vec3  # Room max corner
    openings: List[OpeningConfig] = field(default_factory=list)
    baffle_type: BaffleType = BaffleType.WALL
    porosity: float = 0.0

    @property
    def panel_name(self) -> str:
        return "raised_floor_panel"

    def get_baffle_entity(self) -> RaisedFloorBaffleEntity:
        """Get the baffle entity for STL generation."""
        baffle_config = BaffleConfig(
            baffle_type=self.baffle_type,
            porosity=self.porosity,
        )

        # Convert openings to OpeningBaffleEntity
        opening_entities = []
        for opening in self.openings:
            opening_config = BaffleConfig(
                baffle_type=opening.baffle_type,
                porosity=opening.porosity,
            )
            opening_entity = OpeningBaffleEntity(
                uid=f"opening_raised_floor_{opening.uid}",
                location=Vec3(opening.location.x, opening.location.y, self.height),
                size=opening.size,
                baffle_config=opening_config,
            )
            opening_entities.append(opening_entity)

        # Panel bounds: use openings bounding box when available so the
        # raised_floor_panel STL covers only the actual raised-floor area,
        # not the entire room floor.
        if self.openings:
            xs = []
            ys = []
            for op in self.openings:
                xs.extend([op.location.x, op.location.x + op.size.x])
                ys.extend([op.location.y, op.location.y + op.size.y])
            panel_v_min = Vec3(min(xs), min(ys), 0)
            panel_v_max = Vec3(max(xs), max(ys), self.height)
        else:
            panel_v_min = self.v_min
            panel_v_max = self.v_max

        return RaisedFloorBaffleEntity(
            uid=self.panel_name,
            height=self.height,
            v_min=panel_v_min,
            v_max=panel_v_max,
            baffle_config=baffle_config,
            openings=opening_entities,
        )

    def get_opening_entities(self) -> List[OpeningBaffleEntity]:
        """Get opening baffle entities for STL generation."""
        entities = []
        for opening in self.openings:
            opening_config = BaffleConfig(
                baffle_type=opening.baffle_type,
                porosity=opening.porosity,
            )
            entity = OpeningBaffleEntity(
                uid=f"opening_raised_floor_{opening.uid}",
                location=Vec3(opening.location.x, opening.location.y, self.height),
                size=opening.size,
                baffle_config=opening_config,
            )
            entities.append(entity)
        return entities

    def get_createBaffle_cmd(self) -> str:
        """Generate createBafflesDict commands for the raised floor.
        
        DEPRECATED: Use get_baffle_entity() for new STL-based workflow.
        This method is kept for backward compatibility.
        """
        cmd = f"""
            {self.panel_name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({self.v_min.x} {self.v_min.y} {self.height});
                span        ({self.v_max.x - self.v_min.x} {self.v_max.y - self.v_min.y} 0);
                patches
                {{
                    master
                    {{
                        name            {self.panel_name}_master;
                        type            wall;
                    }}
                    slave
                    {{
                        name            {self.panel_name}_slave;
                        type            wall;
                    }}
                }}
            }}
        """

        # Add openings as cyclic patches
        for opening in self.openings:
            opening_name = sanitize_name(f"opening_raised_floor_{opening.uid}")
            cmd += f"""
            {opening_name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({opening.location.x} {opening.location.y} {self.height});
                span        ({opening.size.x} {opening.size.y} 0);
                patches
                {{
                    master
                    {{
                        name            {opening_name}_master;
                        type            cyclic;
                        neighbourPatch  {opening_name}_slave;
                    }}
                    slave
                    {{
                        name            {opening_name}_slave;
                        type            cyclic;
                        neighbourPatch  {opening_name}_master;
                    }}
                }}
            }}
            """

        return cmd

    @classmethod
    def from_dict(cls, data: Dict[str, Any], v_min: Vec3, v_max: Vec3) -> "RaisedFloorBoundary":
        """Create a RaisedFloorBoundary from dictionary configuration."""
        height = data.get("height", 0.6)
        
        # Parse baffle type and porosity
        baffle_type_str = data.get("baffle_type", data.get("baffleType", "wall"))
        porosity = data.get("porosity", 0.0)
        
        type_map = {
            "wall": BaffleType.WALL,
            "perforated": BaffleType.PERFORATED,
            "transparent": BaffleType.TRANSPARENT,
        }
        baffle_type = type_map.get(baffle_type_str.lower(), BaffleType.WALL)

        openings = []
        for uid, opening_data in data.get("openings", {}).items():
            # Parse opening baffle type and porosity
            opening_baffle_type_str = opening_data.get("baffle_type", opening_data.get("baffleType", "perforated"))
            opening_porosity = opening_data.get("porosity", 0.8)  # Default 80% for openings
            opening_baffle_type = type_map.get(opening_baffle_type_str.lower(), BaffleType.PERFORATED)
            
            opening = OpeningConfig(
                uid=uid,
                location=Vec3(
                    x=opening_data.get("location", {}).get("x", 0),
                    y=opening_data.get("location", {}).get("y", 0),
                    z=height,
                ),
                size=Vec3(
                    x=opening_data.get("size", {}).get("x", 0.6),
                    y=opening_data.get("size", {}).get("y", 0.3),
                    z=0,
                ),
                baffle_type=opening_baffle_type,
                porosity=opening_porosity,
            )
            openings.append(opening)

        return cls(
            height=height,
            v_min=v_min,
            v_max=v_max,
            openings=openings,
            baffle_type=baffle_type,
            porosity=porosity,
        )


@dataclass
class FalseCeilingBoundary:
    """
    False ceiling boundary handling.

    Similar to raised floor, but at ceiling level.
    Uses same STL-based workflow.
    """
    height: float
    v_min: Vec3
    v_max: Vec3
    openings: List[OpeningConfig] = field(default_factory=list)
    baffle_type: BaffleType = BaffleType.WALL
    porosity: float = 0.0

    @property
    def panel_name(self) -> str:
        return "false_ceiling_panel"

    def get_baffle_entity(self) -> FalseCeilingBaffleEntity:
        """Get the baffle entity for STL generation."""
        baffle_config = BaffleConfig(
            baffle_type=self.baffle_type,
            porosity=self.porosity,
        )
        
        # Convert openings to OpeningBaffleEntity
        opening_entities = []
        for opening in self.openings:
            opening_config = BaffleConfig(
                baffle_type=opening.baffle_type,
                porosity=opening.porosity,
            )
            opening_entity = OpeningBaffleEntity(
                uid=f"opening_false_ceiling_{opening.uid}",
                location=Vec3(opening.location.x, opening.location.y, self.height),
                size=opening.size,
                baffle_config=opening_config,
            )
            opening_entities.append(opening_entity)
        
        return FalseCeilingBaffleEntity(
            uid=self.panel_name,
            height=self.height,
            v_min=self.v_min,
            v_max=self.v_max,
            baffle_config=baffle_config,
            openings=opening_entities,
        )

    def get_opening_entities(self) -> List[OpeningBaffleEntity]:
        """Get opening baffle entities for STL generation."""
        entities = []
        for opening in self.openings:
            opening_config = BaffleConfig(
                baffle_type=opening.baffle_type,
                porosity=opening.porosity,
            )
            entity = OpeningBaffleEntity(
                uid=f"opening_false_ceiling_{opening.uid}",
                location=Vec3(opening.location.x, opening.location.y, self.height),
                size=opening.size,
                baffle_config=opening_config,
            )
            entities.append(entity)
        return entities

    def get_createBaffle_cmd(self) -> str:
        """Generate createBafflesDict commands for the false ceiling.
        
        DEPRECATED: Use get_baffle_entity() for new STL-based workflow.
        """
        cmd = f"""
            {self.panel_name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({self.v_min.x} {self.v_min.y} {self.height});
                span        ({self.v_max.x - self.v_min.x} {self.v_max.y - self.v_min.y} 0);
                patches
                {{
                    master
                    {{
                        name            {self.panel_name}_master;
                        type            wall;
                    }}
                    slave
                    {{
                        name            {self.panel_name}_slave;
                        type            wall;
                    }}
                }}
            }}
        """

        # Add openings
        for opening in self.openings:
            opening_name = sanitize_name(f"opening_false_ceiling_{opening.uid}")
            cmd += f"""
            {opening_name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({opening.location.x} {opening.location.y} {self.height});
                span        ({opening.size.x} {opening.size.y} 0);
                patches
                {{
                    master
                    {{
                        name            {opening_name}_master;
                        type            cyclic;
                        neighbourPatch  {opening_name}_slave;
                    }}
                    slave
                    {{
                        name            {opening_name}_slave;
                        type            cyclic;
                        neighbourPatch  {opening_name}_master;
                    }}
                }}
            }}
            """

        return cmd

    @classmethod
    def from_dict(cls, data: Dict[str, Any], v_min: Vec3, v_max: Vec3) -> "FalseCeilingBoundary":
        """Create a FalseCeilingBoundary from dictionary configuration."""
        height = data.get("height", 2.8)
        
        # Parse baffle type and porosity
        baffle_type_str = data.get("baffle_type", data.get("baffleType", "wall"))
        porosity = data.get("porosity", 0.0)
        
        type_map = {
            "wall": BaffleType.WALL,
            "perforated": BaffleType.PERFORATED,
            "transparent": BaffleType.TRANSPARENT,
        }
        baffle_type = type_map.get(baffle_type_str.lower(), BaffleType.WALL)

        openings = []
        for uid, opening_data in data.get("openings", {}).items():
            # Parse opening baffle type and porosity
            opening_baffle_type_str = opening_data.get("baffle_type", opening_data.get("baffleType", "perforated"))
            opening_porosity = opening_data.get("porosity", 0.8)
            opening_baffle_type = type_map.get(opening_baffle_type_str.lower(), BaffleType.PERFORATED)
            
            opening = OpeningConfig(
                uid=uid,
                location=Vec3(
                    x=opening_data.get("location", {}).get("x", 0),
                    y=opening_data.get("location", {}).get("y", 0),
                    z=height,
                ),
                size=Vec3(
                    x=opening_data.get("size", {}).get("x", 0.6),
                    y=opening_data.get("size", {}).get("y", 0.6),
                    z=0,
                ),
                baffle_type=opening_baffle_type,
                porosity=opening_porosity,
            )
            openings.append(opening)

        return cls(
            height=height,
            v_min=v_min,
            v_max=v_max,
            openings=openings,
            baffle_type=baffle_type,
            porosity=porosity,
        )


@dataclass
class ContainmentOpeningConfig:
    """Configuration for an opening in a containment face."""
    uid: str
    location: Vec3  # Global coordinates
    size: Vec3  # Width and height of opening


@dataclass
class ContainmentBoundary:
    """
    Containment (cold aisle enclosure) boundary handling.

    Containments are vertical baffle structures that create enclosed regions.
    
    New STL-based workflow:
    - Generates STL for each enabled face
    - Uses faceZone mode to avoid splitting mesh space
    - createBaffles converts faceZone to wall boundaries
    """
    uid: str
    location: Vec3  # Min corner (x, y, z)
    size: Vec3  # Dimensions (width_x, depth_y, height_z)
    orientation: float = 0.0
    faces: Dict[str, bool] = field(default_factory=lambda: {
        "top": True, "bottom": False, "front": False, "rear": False,
        "left": True, "right": True
    })
    baffle_type: BaffleType = BaffleType.WALL
    porosity: float = 0.0
    openings: List[ContainmentOpeningConfig] = field(default_factory=list)
    openings_side: Optional[str] = None  # Which face has openings

    def get_baffle_entity(self) -> ContainmentBaffleEntity:
        """Get the baffle entity for STL generation."""
        baffle_config = BaffleConfig(
            baffle_type=self.baffle_type,
            porosity=self.porosity,
        )
        
        return ContainmentBaffleEntity(
            uid=f"containment_{sanitize_name(self.uid)}",
            location=self.location,
            size=self.size,
            orientation=self.orientation,
            baffle_config=baffle_config,
            faces=self.faces,
            openings=self.openings,
            openings_side=self.openings_side,
        )

    def get_createBaffle_cmd(self) -> str:
        """Generate createBafflesDict commands for the containment.
        
        DEPRECATED: Use get_baffle_entity() for new STL-based workflow.
        """
        cmd = ""

        x0, y0, z0 = self.location.x, self.location.y, self.location.z
        sx, sy, sz = self.size.x, self.size.y, self.size.z

        # Top face (horizontal, at z = z0 + sz)
        if self.faces.get("top", True):
            name = f"containment_{sanitize_name(self.uid)}_top"
            cmd += f"""
            {name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({x0} {y0} {z0 + sz});
                span        ({sx} {sy} 0);
                patches
                {{
                    master
                    {{
                        name            {name}_master;
                        type            wall;
                    }}
                    slave
                    {{
                        name            {name}_slave;
                        type            wall;
                    }}
                }}
            }}
            """

        # Bottom face (horizontal, at z = z0)
        if self.faces.get("bottom", False):
            name = f"containment_{sanitize_name(self.uid)}_bottom"
            cmd += f"""
            {name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({x0} {y0} {z0});
                span        ({sx} {sy} 0);
                patches
                {{
                    master
                    {{
                        name            {name}_master;
                        type            wall;
                    }}
                    slave
                    {{
                        name            {name}_slave;
                        type            wall;
                    }}
                }}
            }}
            """

        # Front face (at y = y0, vertical in XZ plane)
        if self.faces.get("front", False):
            name = f"containment_{sanitize_name(self.uid)}_front"
            cmd += f"""
            {name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({x0} {y0} {z0});
                span        ({sx} 0 {sz});
                patches
                {{
                    master
                    {{
                        name            {name}_master;
                        type            wall;
                    }}
                    slave
                    {{
                        name            {name}_slave;
                        type            wall;
                    }}
                }}
            }}
            """

        # Rear face (at y = y0 + sy, vertical in XZ plane)
        if self.faces.get("rear", False):
            name = f"containment_{sanitize_name(self.uid)}_rear"
            cmd += f"""
            {name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({x0} {y0 + sy} {z0});
                span        ({sx} 0 {sz});
                patches
                {{
                    master
                    {{
                        name            {name}_master;
                        type            wall;
                    }}
                    slave
                    {{
                        name            {name}_slave;
                        type            wall;
                    }}
                }}
            }}
            """

        # Left face (at x = x0, vertical in YZ plane)
        if self.faces.get("left", True):
            name = f"containment_{sanitize_name(self.uid)}_left"
            cmd += f"""
            {name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({x0} {y0} {z0});
                span        (0 {sy} {sz});
                patches
                {{
                    master
                    {{
                        name            {name}_master;
                        type            wall;
                    }}
                    slave
                    {{
                        name            {name}_slave;
                        type            wall;
                    }}
                }}
            }}
            """

        # Right face (at x = x0 + sx, vertical in YZ plane)
        if self.faces.get("right", True):
            name = f"containment_{sanitize_name(self.uid)}_right"
            cmd += f"""
            {name}
            {{
                type        searchableSurface;
                surface     searchablePlate;
                origin      ({x0 + sx} {y0} {z0});
                span        (0 {sy} {sz});
                patches
                {{
                    master
                    {{
                        name            {name}_master;
                        type            wall;
                    }}
                    slave
                    {{
                        name            {name}_slave;
                        type            wall;
                    }}
                }}
            }}
            """

        return cmd

    @classmethod
    def from_dict(cls, data: Dict[str, Any], uid: str) -> "ContainmentBoundary":
        """Create a ContainmentBoundary from dictionary configuration."""
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
        orientation = data.get("orientation", 0.0)
        faces = data.get("faces", {
            "top": True, "bottom": False, "front": False, "rear": False,
            "left": True, "right": True
        })
        
        # Parse baffle type and porosity
        baffle_type_str = data.get("baffle_type", data.get("baffleType", "wall"))
        porosity = data.get("porosity", 0.0)
        
        type_map = {
            "wall": BaffleType.WALL,
            "perforated": BaffleType.PERFORATED,
            "transparent": BaffleType.TRANSPARENT,
        }
        baffle_type = type_map.get(baffle_type_str.lower(), BaffleType.WALL)

        # Parse openings
        openings = []
        openings_data = data.get("openings", {})
        for opening_uid, opening_info in openings_data.items():
            opening_loc = opening_info.get("location", {})
            opening_size = opening_info.get("size", {})
            openings.append(ContainmentOpeningConfig(
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
        
        openings_side = data.get("openingsSide", data.get("openings_side"))

        return cls(
            uid=uid,
            location=location,
            size=size,
            orientation=orientation,
            faces=faces,
            baffle_type=baffle_type,
            porosity=porosity,
            openings=openings,
            openings_side=openings_side,
        )


class RoomGeometry:
    """
    Complete room geometry including boundaries and internal surfaces.

    This class combines the room boundary (from blockMesh) with internal
    surfaces (raised floor, false ceiling, containments).
    
    New STL-based workflow:
    - get_room_wall_entity(): Returns RoomWallEntity for polygon room outer walls
    - get_all_baffle_entities(): Returns all baffle entities for STL generation
    - Baffles use faceZone mode in snappyHexMesh to avoid sawteeth edges
    """

    def __init__(
        self,
        plane: List[Vec3],  # Room outline vertices
        height: float,
        raised_floor: Optional[RaisedFloorBoundary] = None,
        false_ceiling: Optional[FalseCeilingBoundary] = None,
        containments: Optional[List[ContainmentBoundary]] = None,
    ):
        self.plane = plane
        self.height = height

        # Compute room bounds
        xs = [p.x for p in plane]
        ys = [p.y for p in plane]
        self.v_min = Vec3(min(xs), min(ys), 0)
        self.v_max = Vec3(max(xs), max(ys), height)

        self.raised_floor = raised_floor
        self.false_ceiling = false_ceiling
        self.containments = containments or []

        # Update floor/ceiling bounds to match room
        if self.raised_floor:
            self.raised_floor.v_min = self.v_min
            self.raised_floor.v_max = self.v_max

        if self.false_ceiling:
            self.false_ceiling.v_min = self.v_min
            self.false_ceiling.v_max = self.v_max

    @property
    def boundary(self) -> RoomBoundary:
        """Get the room boundary configuration."""
        return RoomBoundary(v_min=self.v_min, v_max=self.v_max)

    @property
    def blockMesh_cells(self) -> tuple:
        """
        Calculate the number of cells for blockMesh.

        Returns (x_cells, y_cells, z_cells) based on a default base size.
        """
        base_size = 0.2  # Default, should be configurable
        x_cells = int((self.v_max.x - self.v_min.x) / base_size)
        y_cells = int((self.v_max.y - self.v_min.y) / base_size)
        z_cells = int((self.v_max.z - self.v_min.z) / base_size)
        return x_cells, y_cells, z_cells

    def get_room_wall_entity(self) -> RoomWallEntity:
        """Get the room wall entity for STL generation.
        
        This generates the polygon room outer walls that will be used
        to delete cells outside the room during snappyHexMesh.
        """
        return RoomWallEntity(
            uid="room_wall",
            plane=self.plane,
            height=self.height,
        )

    def get_all_baffle_entities(self) -> List[BaffleEntity]:
        """Get all baffle entities for STL generation.
        
        Returns a list of BaffleEntity objects including:
        - Raised floor panel and its openings
        - False ceiling panel and its openings
        - Containment panels
        """
        entities = []
        
        # Raised floor
        if self.raised_floor:
            entities.append(self.raised_floor.get_baffle_entity())
            entities.extend(self.raised_floor.get_opening_entities())
        
        # False ceiling
        if self.false_ceiling:
            entities.append(self.false_ceiling.get_baffle_entity())
            entities.extend(self.false_ceiling.get_opening_entities())
        
        # Containments
        for containment in self.containments:
            entities.append(containment.get_baffle_entity())
        
        return entities

    def get_perforated_baffle_entities(self) -> List[BaffleEntity]:
        """Get only perforated baffle entities.
        
        These need special handling with createPatch to generate
        master/slave patches for porousBafflePressure.
        """
        all_baffles = self.get_all_baffle_entities()
        return [b for b in all_baffles if b.is_perforated or b.is_transparent]

    def get_wall_baffle_entities(self) -> List[BaffleEntity]:
        """Get only wall-type baffle entities."""
        all_baffles = self.get_all_baffle_entities()
        return [b for b in all_baffles if b.is_wall]

    def get_all_createBaffle_cmd(self) -> str:
        """Get all CreateBaffle commands for internal surfaces.
        
        DEPRECATED: Use get_all_baffle_entities() for new STL-based workflow.
        This method is kept for backward compatibility.
        """
        cmd = ""
        if self.raised_floor:
            cmd += self.raised_floor.get_createBaffle_cmd()
        if self.false_ceiling:
            cmd += self.false_ceiling.get_createBaffle_cmd()
        for containment in self.containments:
            cmd += containment.get_createBaffle_cmd()
        return cmd

    @classmethod
    def from_dict(cls, data: Dict[str, Any], containments_config: Optional[Dict[str, Any]] = None) -> "RoomGeometry":
        """Create RoomGeometry from dictionary configuration.

        Args:
            data: Room configuration dictionary
            containments_config: Optional containments configuration from entities section
        """
        # Parse room plane
        plane = []
        for point in data.get("geometry", {}).get("plane", []):
            plane.append(Vec3(x=point.get("x", 0), y=point.get("y", 0), z=0))

        height = data.get("geometry", {}).get("height", 3.0)

        # Compute bounds for child objects
        xs = [p.x for p in plane] if plane else [0, 10]
        ys = [p.y for p in plane] if plane else [0, 8]
        v_min = Vec3(min(xs), min(ys), 0)
        v_max = Vec3(max(xs), max(ys), height)

        # Parse raised floor
        raised_floor = None
        rf_data = data.get("raisedFloor")
        if rf_data:
            raised_floor = RaisedFloorBoundary.from_dict(rf_data, v_min, v_max)

        # Parse false ceiling
        false_ceiling = None
        fc_data = data.get("falseCeiling")
        if fc_data:
            false_ceiling = FalseCeilingBoundary.from_dict(fc_data, v_min, v_max)

        # Parse containments (from entities section, passed in as parameter)
        containments = []
        if containments_config:
            for uid, cont_data in containments_config.items():
                containment = ContainmentBoundary.from_dict(cont_data, uid)
                containments.append(containment)

        return cls(
            plane=plane,
            height=height,
            raised_floor=raised_floor,
            false_ceiling=false_ceiling,
            containments=containments,
        )
