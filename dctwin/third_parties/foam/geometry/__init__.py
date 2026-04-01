"""
Geometry module for data center CFD simulation.

This module provides classes for defining geometry entities (Rack, ACU, Pillar, etc.)
and generating STL files for OpenFOAM snappyHexMesh.

New baffle-based workflow:
- BaffleEntity classes generate STL for thin surfaces (floors, ceilings, containments)
- RoomWallEntity generates polygon room outer walls
- faceZone mode in snappyHexMesh avoids sawteeth edges
- createBaffles converts faceZone to wall/cyclic boundaries
"""

from .base import Entity, SubEntity, Vec3, Triangle, BoundingBox
from .stl_writer import STLWriter
from .rack import RackEntity, ServerInletSubEntity, ServerOutletSubEntity, AirLeakageSubEntity
from .entities import ACUEntity, PillarEntity, ContainmentEntity, BoxEntity, ImportedSTLEntity, ImportedSTLBoundaryConfig
from .room_boundary import (
    RoomBoundary, RaisedFloorBoundary, FalseCeilingBoundary,
    RoomWallEntity, RoomGeometry, ContainmentBoundary,
)
from .baffle import (
    BaffleType, BaffleConfig, BaffleEntity,
    HorizontalBaffleEntity, VerticalBaffleEntity,
    ContainmentBaffleEntity, RaisedFloorBaffleEntity,
    FalseCeilingBaffleEntity, OpeningBaffleEntity,
    get_createBaffles_faceZone_cmd, get_snappyHexMesh_faceZone_cmd,
)
from .manager import GeometryManager

__all__ = [
    # Base classes
    "Entity",
    "SubEntity",
    "Vec3",
    "Triangle",
    "BoundingBox",
    # STL utilities
    "STLWriter",
    # Entities
    "RackEntity",
    "ACUEntity",
    "PillarEntity",
    "ContainmentEntity",
    "BoxEntity",
    "ImportedSTLEntity",
    "ImportedSTLBoundaryConfig",
    # SubEntities
    "ServerInletSubEntity",
    "ServerOutletSubEntity",
    "AirLeakageSubEntity",
    # Room boundaries
    "RoomBoundary",
    "RaisedFloorBoundary",
    "FalseCeilingBoundary",
    "RoomWallEntity",
    "RoomGeometry",
    "ContainmentBoundary",
    # Baffle entities
    "BaffleType",
    "BaffleConfig",
    "BaffleEntity",
    "HorizontalBaffleEntity",
    "VerticalBaffleEntity",
    "ContainmentBaffleEntity",
    "RaisedFloorBaffleEntity",
    "FalseCeilingBaffleEntity",
    "OpeningBaffleEntity",
    # Baffle utilities
    "get_createBaffles_faceZone_cmd",
    "get_snappyHexMesh_faceZone_cmd",
    # Manager
    "GeometryManager",
]
