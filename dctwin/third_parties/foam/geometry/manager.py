"""
Geometry Manager for data center CFD simulations.

This module provides the GeometryManager class that orchestrates
STL generation and mesh configuration for all entities.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from collections import Counter

from loguru import logger

from .base import Entity, Vec3, SolidRegion, PatchType
from .stl_writer import STLWriter
from .rack import RackEntity, ServerConfig, AirLeakageConfig, ColumnConfig, LeakageZone
from .entities import ACUEntity, PillarEntity, ContainmentEntity, BoxEntity, FaceConfig, ImportedSTLEntity
from .room_boundary import RoomGeometry, RaisedFloorBoundary, FalseCeilingBoundary, RoomWallEntity
from .baffle import (
    BaffleType, BaffleConfig, BaffleEntity,
    ContainmentBaffleEntity, RaisedFloorBaffleEntity,
    OpeningBaffleEntity,
    get_createBaffles_faceZone_cmd, get_snappyHexMesh_faceZone_cmd,
)


GEOMETRY_HASH_VERSION = 5  # Bumped for ImportedSTL entity support


class GeometryManager:
    """
    Manages geometry generation for data center CFD simulations.

    This class:
    1. Parses configuration from JSON files
    2. Creates Entity objects for all equipment
    3. Generates STL files for each entity
    4. Generates OpenFOAM dictionary configurations
    """

    def __init__(
        self,
        output_dir: Path,
        base_size: float = 0.2,
        refinement_level: int = 1,
    ):
        """
        Initialize the GeometryManager.

        Args:
            output_dir: Directory for output files (STL, dictionaries)
            base_size: Base cell size for mesh generation
            refinement_level: Default refinement level for surfaces
        """
        self.output_dir = Path(output_dir)
        self.base_size = base_size
        self.refinement_level = refinement_level

        self.entities: List[Entity] = []
        self.room_geometry: Optional[RoomGeometry] = None
        self._config: Optional[Dict[str, Any]] = None
        self._json_base_path: Optional[Path] = None

        # STL output directory
        self.stl_dir = self.output_dir / "constant" / "triSurface"

    def load_from_json(self, json_path: Path) -> None:
        """
        Load geometry configuration from a JSON file.

        Args:
            json_path: Path to the JSON configuration file
        """
        self._json_base_path = Path(json_path).parent

        with open(json_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        normalized = self._normalize_config(config)
        self._config = normalized
        self._parse_config(normalized)

    def load_from_dict(self, config: Dict[str, Any]) -> None:
        """
        Load geometry configuration from a dictionary.

        Args:
            config: Configuration dictionary
        """
        normalized = self._normalize_config(config)
        self._config = normalized
        self._parse_config(normalized)

    def _normalize_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize legacy config into the STL geometry schema."""
        if "room" in config and "entities" in config:
            return config
        if "constructions" in config or "geometry" in config:
            return self._convert_legacy_config(config)
        return config

    def _convert_legacy_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Convert legacy geometry/constructions schema to room/entities schema."""
        models = config.get("models", {})
        geometry_models = models.get("geometryModels") or models.get("geometry_models") or {}
        cooling_models = models.get("coolingModels") or models.get("cooling_models") or {}

        normalized: Dict[str, Any] = {
            "meta": config.get("meta", {}),
            "models": {
                "geometryModels": self._convert_geometry_models(geometry_models),
                "coolingModels": self._convert_cooling_models(cooling_models),
            },
            "room": {},
            "entities": {
                "racks": {},
                "acus": {},
                "pillars": {},
                "containments": {},
                "boxes": {},
                "external_objects": {},
            },
        }

        if "inputs" in config:
            normalized["inputs"] = config.get("inputs")

        legacy_geometry = config.get("geometry", {})
        plane = []
        for point in legacy_geometry.get("plane", []):
            plane.append({"x": point.get("x", 0), "y": point.get("y", 0)})
        normalized["room"]["geometry"] = {
            "plane": plane,
            "height": legacy_geometry.get("height", 3.0),
        }

        constructions = config.get("constructions", {})

        raised_floor = constructions.get("raisedFloor") or constructions.get("raised_floor")
        if isinstance(raised_floor, dict):
            rf_geometry = raised_floor.get("geometry", {})
            normalized["room"]["raisedFloor"] = {
                "height": rf_geometry.get("height", 0.0),
                "openings": self._convert_openings(rf_geometry.get("openings", {})),
            }

        false_ceiling = constructions.get("falseCeiling") or constructions.get("false_ceiling")
        if isinstance(false_ceiling, dict):
            fc_geometry = false_ceiling.get("geometry", {})
            normalized["room"]["falseCeiling"] = {
                "height": fc_geometry.get("height", 0.0),
                "openings": self._convert_openings(fc_geometry.get("openings", {})),
            }

        box_models = normalized["models"]["geometryModels"].get("boxes", {})
        for uid, box_data in (constructions.get("boxes", {}) or {}).items():
            geom = box_data.get("geometry", {})
            model_name = geom.get("model")
            size = geom.get("size", {})
            sx = float(size.get("x", 0) or 0)
            sy = float(size.get("y", 0) or 0)
            sz = float(size.get("z", 0) or 0)

            if sx <= 0 or sy <= 0 or sz <= 0:
                faces = {}
                if sx <= 0 and sy > 0 and sz > 0:
                    faces = {"left": True, "right": False, "front": False, "rear": False, "top": False, "bottom": False}
                elif sy <= 0 and sx > 0 and sz > 0:
                    faces = {"front": True, "rear": False, "left": False, "right": False, "top": False, "bottom": False}
                elif sz <= 0 and sx > 0 and sy > 0:
                    faces = {"top": True, "bottom": False, "front": False, "rear": False, "left": False, "right": False}

                if faces:
                    containment_entry = {
                        "location": geom.get("location", {}),
                        "size": size,
                        "orientation": geom.get("orientation", 0),
                        "faces": faces,
                    }
                    # Pass openings and openingsSide for partition-type containments
                    openings = geom.get("openings") or box_data.get("openings")
                    if openings:
                        containment_entry["openings"] = openings
                    openings_side = geom.get("openingsSide", geom.get("openings_side"))
                    if openings_side:
                        containment_entry["openingsSide"] = openings_side
                    normalized["entities"]["containments"][uid] = containment_entry
                    continue
            faces = geom.get("faces") or box_models.get(model_name, {}).get("faces", {})
            entry = {
                "location": geom.get("location", {}),
                "size": size,
                "orientation": geom.get("orientation", 0),
            }
            if faces:
                entry["faces"] = faces

            openings = geom.get("openings") or box_data.get("openings")
            if openings:
                entry["hasOpenings"] = True
            openings_side = geom.get("openingsSide", geom.get("openings_side"))
            if openings_side:
                entry["openingsSide"] = openings_side

            if model_name == "pillar":
                normalized["entities"]["pillars"][uid] = entry
            elif model_name == "containment":
                normalized["entities"]["containments"][uid] = entry
            else:
                normalized["entities"]["boxes"][uid] = entry

        rack_models = normalized["models"]["geometryModels"].get("racks", {})
        server_models = normalized["models"]["geometryModels"].get("servers", {})
        cooling_server_models = normalized["models"]["coolingModels"].get("servers", {})
        inputs_servers = (config.get("inputs") or {}).get("servers", {})

        for uid, rack_data in (constructions.get("racks", {}) or {}).items():
            rack_geom = rack_data.get("geometry", {})
            model_name = rack_geom.get("model") or rack_data.get("geometryModel")
            rack_entry: Dict[str, Any] = {
                "geometryModel": model_name,
                "location": rack_geom.get("location", {}),
                "orientation": rack_geom.get("orientation", 0),
                "hasBlankingPanel": rack_geom.get("hasBlankingPanel", True),
            }
            if "size" in rack_geom:
                rack_entry["size"] = rack_geom.get("size")
            if "columns" in rack_geom:
                rack_entry["columns"] = rack_geom.get("columns")
            if "airLeakage" in rack_geom:
                rack_entry["airLeakage"] = rack_geom.get("airLeakage")
            if "airLeakage" in rack_data:
                rack_entry["airLeakage"] = rack_data.get("airLeakage")

            model_slot = rack_models.get(model_name, {}).get("slot")
            slot_count = (
                rack_geom.get("slot")
                or rack_geom.get("slotCount")
                or rack_data.get("slotCount")
                or model_slot
            )
            if slot_count is not None:
                rack_entry["slotCount"] = slot_count

            slot_height = rack_geom.get("slotHeight") or rack_geom.get("slot_height")
            if slot_height is not None:
                rack_entry["slotHeight"] = slot_height

            first_slot_offset = (
                rack_geom.get("firstSlotOffset")
                or rack_geom.get("first_slot_offset")
                or rack_data.get("firstSlotOffset")
                or rack_data.get("first_slot_offset")
            )
            if first_slot_offset is not None:
                rack_entry["firstSlotOffset"] = first_slot_offset

            servers_out = []
            servers_data = (
                rack_data.get("constructions", {}).get("servers", {}) or rack_data.get("servers", {})
            )
            for server_uid, server_data in servers_data.items():
                server_geom = server_data.get("geometry", {})
                server_model = server_geom.get("model") or server_data.get("model")
                model_info = server_models.get(server_model, {})

                slot_position = (
                    server_geom.get("slotPosition")
                    or server_geom.get("slot_position")
                    or server_data.get("slotPosition")
                    or server_data.get("slot_position")
                    or 0
                )
                slot_occupation = (
                    server_geom.get("slotOccupation")
                    or server_geom.get("slot_occupation")
                    or model_info.get("slotOccupation")
                    or model_info.get("slot_occupation")
                )

                server_entry: Dict[str, Any] = {
                    "uid": server_uid,
                    "model": server_model,
                    "slotPosition": slot_position,
                }
                if slot_occupation is not None:
                    server_entry["slotOccupation"] = slot_occupation

                width = server_geom.get("width") or model_info.get("width")
                depth = server_geom.get("depth") or model_info.get("depth")
                if width is not None:
                    server_entry["width"] = width
                if depth is not None:
                    server_entry["depth"] = depth

                inlet = server_geom.get("inlet") or server_data.get("inlet")
                outlet = server_geom.get("outlet") or server_data.get("outlet")
                if inlet:
                    server_entry["inlet"] = self._convert_inlet_outlet(inlet)
                if outlet:
                    server_entry["outlet"] = self._convert_inlet_outlet(outlet)

                power = self._lookup_numeric(inputs_servers.get(server_uid, {}), ["input_power", "inputPower", "power"])
                if power is not None:
                    server_entry["power"] = power

                cooling = server_data.get("cooling", {})
                cooling_model = cooling.get("model")
                cooling_info = cooling_server_models.get(cooling_model, {})
                volume_flow_rate = self._lookup_numeric(
                    cooling,
                    ["volumeFlowRate", "volume_flow_rate", "air_volume_flow_rate"],
                )
                if volume_flow_rate is None:
                    volume_flow_rate = self._lookup_numeric(
                        cooling_info,
                        ["volumeFlowRate", "volume_flow_rate", "air_volume_flow_rate"],
                    )
                if volume_flow_rate is not None:
                    server_entry["volumeFlowRate"] = volume_flow_rate

                servers_out.append(server_entry)

            rack_entry["servers"] = servers_out
            normalized["entities"]["racks"][uid] = rack_entry

        cooling_acu_models = normalized["models"]["coolingModels"].get("acus", {})
        inputs_acus = (config.get("inputs") or {}).get("acus", {})

        for uid, acu_data in (constructions.get("acus", {}) or {}).items():
            acu_geom = acu_data.get("geometry", {})
            model_name = acu_geom.get("model") or acu_data.get("geometryModel")
            entry: Dict[str, Any] = {
                "geometryModel": model_name,
                "location": acu_geom.get("location", {}),
                "orientation": acu_geom.get("orientation", 0),
            }
            if "size" in acu_geom:
                entry["size"] = acu_geom.get("size")

            supply_face = acu_geom.get("supplyFace") or acu_geom.get("supply_face")
            return_face = acu_geom.get("returnFace") or acu_geom.get("return_face")
            if supply_face:
                entry["supplyFace"] = self._convert_face(supply_face)
            if return_face:
                entry["returnFace"] = self._convert_face(return_face)

            cooling = acu_data.get("cooling", {})
            cooling_model = cooling.get("model")
            cooling_info = cooling_acu_models.get(cooling_model, {})
            supply_air_temperature = self._lookup_numeric(
                inputs_acus.get(uid, {}),
                ["supply_air_temperature", "supplyAirTemperature"],
            )
            if supply_air_temperature is None:
                supply_air_temperature = self._lookup_numeric(
                    cooling.get("operating", {}),
                    ["supply_air_temperature", "supplyAirTemperature"],
                )
            if supply_air_temperature is None:
                supply_air_temperature = self._lookup_numeric(
                    cooling_info,
                    ["supply_air_temperature", "supplyAirTemperature"],
                )

            supply_air_volume_flow_rate = self._lookup_numeric(
                inputs_acus.get(uid, {}),
                ["supply_air_volume_flow_rate", "supplyAirVolumeFlowRate"],
            )
            if supply_air_volume_flow_rate is None:
                supply_air_volume_flow_rate = self._lookup_numeric(
                    cooling.get("operating", {}),
                    ["supply_air_volume_flow_rate", "supplyAirVolumeFlowRate"],
                )
            if supply_air_volume_flow_rate is None:
                supply_air_volume_flow_rate = self._lookup_numeric(
                    cooling_info,
                    ["design_air_flow_rate", "designAirFlowRate", "maximum_flow_rate"],
                )

            cooling_capacity = self._lookup_numeric(
                cooling_info,
                ["cooling_capacity", "coolingCapacity"],
            )

            cooling_entry = {}
            if cooling_capacity is not None:
                cooling_entry["coolingCapacity"] = cooling_capacity
            if supply_air_temperature is not None:
                cooling_entry["supplyAirTemperature"] = supply_air_temperature
            if supply_air_volume_flow_rate is not None:
                cooling_entry["supplyAirVolumeFlowRate"] = supply_air_volume_flow_rate
            if cooling_entry:
                entry["cooling"] = cooling_entry

            normalized["entities"]["acus"][uid] = entry

        # Pass through external_objects from constructions
        for uid, ext_data in (constructions.get("external_objects", {}) or {}).items():
            normalized["entities"]["external_objects"][uid] = ext_data

        return normalized

    def _convert_geometry_models(self, geometry_models: Dict[str, Any]) -> Dict[str, Any]:
        """Convert legacy geometryModels keys to camelCase where needed."""
        racks_out = {}
        for name, data in (geometry_models.get("racks", {}) or {}).items():
            entry = dict(data)
            if "first_slot_offset" in entry and "firstSlotOffset" not in entry:
                entry["firstSlotOffset"] = entry.pop("first_slot_offset")
            racks_out[name] = entry

        servers_out = {}
        for name, data in (geometry_models.get("servers", {}) or {}).items():
            entry = dict(data)
            if "slot_occupation" in entry and "slotOccupation" not in entry:
                entry["slotOccupation"] = entry.pop("slot_occupation")
            servers_out[name] = entry

        acus_out = {}
        for name, data in (geometry_models.get("acus", {}) or {}).items():
            entry = dict(data)
            if "supply_face" in entry and "supplyFace" not in entry:
                entry["supplyFace"] = self._convert_face(entry.pop("supply_face"))
            if "return_face" in entry and "returnFace" not in entry:
                entry["returnFace"] = self._convert_face(entry.pop("return_face"))
            acus_out[name] = entry

        return {
            "racks": racks_out,
            "servers": servers_out,
            "acus": acus_out,
            "boxes": geometry_models.get("boxes", {}) or {},
        }

    def _convert_cooling_models(self, cooling_models: Dict[str, Any]) -> Dict[str, Any]:
        """Convert legacy coolingModels keys to camelCase where needed."""
        acus_out = {}
        for name, data in (cooling_models.get("acus", {}) or {}).items():
            entry = dict(data)
            if "cooling_type" in entry and "coolingType" not in entry:
                entry["coolingType"] = entry.pop("cooling_type")
            if "cooling_capacity" in entry and "coolingCapacity" not in entry:
                entry["coolingCapacity"] = entry.pop("cooling_capacity")
            acus_out[name] = entry

        servers_out = {}
        for name, data in (cooling_models.get("servers", {}) or {}).items():
            entry = dict(data)
            if "volume_flow_rate" in entry and "volumeFlowRate" not in entry:
                entry["volumeFlowRate"] = entry.pop("volume_flow_rate")
            servers_out[name] = entry

        return {
            "acus": acus_out,
            "servers": servers_out,
        }

    @staticmethod
    def _convert_openings(openings: Dict[str, Any]) -> Dict[str, Any]:
        converted = {}
        for uid, data in openings.items():
            loc = data.get("location", {})
            size = data.get("size", {})
            converted[uid] = {
                "location": {"x": loc.get("x", 0), "y": loc.get("y", 0)},
                "size": {"x": size.get("x", 0), "y": size.get("y", 0)},
            }
        return converted

    @staticmethod
    def _convert_face(face: Dict[str, Any]) -> Dict[str, Any]:
        offset = face.get("offset")
        if offset is None:
            offset = {
                "x": face.get("offset_x", 0),
                "y": face.get("offset_y", 0),
                "z": face.get("offset_z", 0),
            }
        return {
            "side": face.get("side", ""),
            "width": face.get("width", 0),
            "length": face.get("length", 0),
            "offset": {
                "x": offset.get("x", 0),
                "y": offset.get("y", 0),
                "z": offset.get("z", 0),
            },
        }

    @staticmethod
    def _convert_inlet_outlet(data: Dict[str, Any]) -> Dict[str, Any]:
        converted: Dict[str, Any] = {}
        if "heightRatio" in data or "height_ratio" in data:
            converted["heightRatio"] = data.get("heightRatio", data.get("height_ratio"))
        if "height" in data:
            converted["height"] = data.get("height")
        if "width" in data:
            converted["width"] = data.get("width")
        if "offsetX" in data or "offset_x" in data:
            converted["offsetX"] = data.get("offsetX", data.get("offset_x"))
        if "offsetZ" in data or "offset_z" in data:
            converted["offsetZ"] = data.get("offsetZ", data.get("offset_z"))
        return converted

    @staticmethod
    def _lookup_numeric(data: Any, keys: List[str]) -> Optional[float]:
        if not isinstance(data, dict):
            return None
        for key in keys:
            value = data.get(key)
            if value is not None:
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    def _parse_config(self, config: Dict[str, Any]) -> None:
        """Parse configuration and create entities."""
        self.entities = []

        # Get entity configurations
        entities_config = config.get("entities", {})

        # Parse containments (these will be passed to RoomGeometry for baffle handling)
        containments_config = entities_config.get("containments", {})

        # Parse room geometry with containments
        room_data = config.get("room", {})
        if room_data:
            self.room_geometry = RoomGeometry.from_dict(room_data, containments_config)

        # Parse racks
        racks_config = entities_config.get("racks", {})
        for uid, rack_data in racks_config.items():
            rack = self._create_rack_entity(uid, rack_data, config)
            self.entities.append(rack)

        # Parse ACUs
        acus_config = entities_config.get("acus", {})
        for uid, acu_data in acus_config.items():
            acu = self._create_acu_entity(uid, acu_data, config)
            self.entities.append(acu)

        # Parse Pillars
        pillars_config = entities_config.get("pillars", {})
        for uid, pillar_data in pillars_config.items():
            pillar = PillarEntity.from_dict(pillar_data, uid)
            self.entities.append(pillar)

        # NOTE: Containments are now handled as baffles in RoomGeometry,
        # not as STL entities. They use createBaffles instead of STL geometry.

        # Parse generic Boxes
        boxes_config = entities_config.get("boxes", {})
        for uid, box_data in boxes_config.items():
            box = BoxEntity.from_dict(box_data, uid)
            self.entities.append(box)

        # Parse external STL objects (imported from CAD)
        external_objects_config = entities_config.get("external_objects", {})
        for uid, ext_data in external_objects_config.items():
            base_path = str(self._json_base_path) if self._json_base_path else None
            entity = ImportedSTLEntity.from_dict(ext_data, uid, base_path=base_path)
            self.entities.append(entity)

        logger.info(f"Loaded {len(self.entities)} entities from configuration")

    def _create_rack_entity(
        self,
        uid: str,
        rack_data: Dict[str, Any],
        config: Dict[str, Any],
    ) -> RackEntity:
        """Create a RackEntity from configuration."""
        # Get geometry model if specified
        geometry_model = rack_data.get("geometryModel")
        models = config.get("models", {}).get("geometryModels", {}).get("racks", {})

        size_data = rack_data.get("size", {})
        if geometry_model and geometry_model in models:
            model_size = models[geometry_model].get("size", {})
            size_data = {**model_size, **size_data}  # Override with explicit values

        location = Vec3(
            x=rack_data.get("location", {}).get("x", 0),
            y=rack_data.get("location", {}).get("y", 0),
            z=rack_data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=size_data.get("x", 0.6),
            y=size_data.get("y", 1.2),
            z=size_data.get("z", 2.2),
        )

        # Parse air leakage config (new volume mesh approach)
        leak_data = rack_data.get("airLeakage", {})
        air_leakage = AirLeakageConfig(
            enabled=leak_data.get("enabled", True),
            sigma=leak_data.get("sigma", 0.1),
            safety_margin_slots=leak_data.get("safetyMarginSlots", 1),
            # Legacy fields for backward compatibility
            side_width=leak_data.get("sideWidth", 0.0),
            top_height=leak_data.get("topHeight", 0.0),
            position=leak_data.get("position", "top"),
        )

        # No longer using side_width for column layout
        leak_left = 0.0
        leak_right = 0.0

        # Parse columns (for multi-column racks)
        columns = []
        columns_data = rack_data.get("columns", [])
        if columns_data:
            offset = leak_left
            for idx, col_data in enumerate(columns_data):
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
        server_models = config.get("models", {}).get("geometryModels", {}).get("servers", {})

        for server_data in rack_data.get("servers", []):
            server_model = server_data.get("model")
            model_config = server_models.get(server_model, {})

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

            # Get inlet/outlet config from server_data first, then fallback to model_config
            inlet_data = server_data.get("inlet", {})
            outlet_data = server_data.get("outlet", {})
            model_inlet_data = model_config.get("inlet", {})
            model_outlet_data = model_config.get("outlet", {})

            inlet_offset = inlet_data.get("offsetX", model_inlet_data.get("offsetX", column.offset_x))
            outlet_offset = outlet_data.get("offsetX", model_outlet_data.get("offsetX", column.offset_x))

            # For inlet/outlet width, priority: server_data > model_config > column.width
            inlet_width = inlet_data.get("width", model_inlet_data.get("width", column.width))
            outlet_width = outlet_data.get("width", model_outlet_data.get("width", column.width))

            inlet_offset_z = inlet_data.get("offsetZ", model_inlet_data.get("offsetZ", 0.0))
            outlet_offset_z = outlet_data.get("offsetZ", model_outlet_data.get("offsetZ", 0.0))

            # Get height ratio from server_data > model_config > default
            inlet_height_ratio = inlet_data.get("heightRatio", model_inlet_data.get("heightRatio", 0.33))
            outlet_height_ratio = outlet_data.get("heightRatio", model_outlet_data.get("heightRatio", 0.33))
            inlet_height = inlet_data.get("height", model_inlet_data.get("height"))
            outlet_height = outlet_data.get("height", model_outlet_data.get("height"))

            server = ServerConfig(
                uid=server_data.get("uid", f"server_{len(servers)}"),
                slot_position=server_data.get("slotPosition", 0),
                slot_occupation=server_data.get(
                    "slotOccupation",
                    model_config.get("slotOccupation", 1),
                ),
                width=server_data.get("width", model_config.get("width", column.width)),
                depth=server_data.get("depth", model_config.get("depth", size.y)),
                inlet_height_ratio=inlet_height_ratio,
                outlet_height_ratio=outlet_height_ratio,
                inlet_height=inlet_height,
                outlet_height=outlet_height,
                inlet_width=inlet_width,
                outlet_width=outlet_width,
                inlet_offset_x=inlet_offset,
                outlet_offset_x=outlet_offset,
                inlet_offset_z=inlet_offset_z,
                outlet_offset_z=outlet_offset_z,
                power=server_data.get("power", 0),
                volume_flow_rate=server_data.get("volumeFlowRate", 0),
            )
            servers.append(server)

        model_config = models.get(geometry_model, {}) if geometry_model else {}
        first_slot_offset = rack_data.get(
            "firstSlotOffset",
            rack_data.get("first_slot_offset", model_config.get("firstSlotOffset", model_config.get("first_slot_offset", 0.0))),
        )

        # Get slot configuration - support both "slotCount" and "slot" keys
        # Fallback to model_config if not specified in rack instance
        # Priority for rack height and slot_height calculation:
        # 1. If size.z is explicitly defined, use it as rack height, calculate slot_height = height / slot_count
        # 2. If size.z is not defined but slotHeight is, use rack_height = slot_count * slot_height
        # This ensures rack height can match containment height exactly
        slot_count = rack_data.get("slotCount", rack_data.get("slot", 
            model_config.get("slotCount", model_config.get("slot"))))
        explicit_height = size_data.get("z") if size_data else model_config.get("size", {}).get("z")
        slot_height_from_config = rack_data.get("slotHeight", rack_data.get("slot_height",
            model_config.get("slotHeight", model_config.get("slot_height"))))
        
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

        return RackEntity(
            uid=uid,
            location=location,
            size=size,
            orientation=rack_data.get("orientation", 0),
            slot_count=slot_count,
            slot_height=slot_height,
            first_slot_offset=first_slot_offset,
            servers=servers,
            air_leakage=air_leakage,
            has_blanking_panel=rack_data.get("hasBlankingPanel", True),
            columns=columns,
        )

    def _create_acu_entity(
        self,
        uid: str,
        acu_data: Dict[str, Any],
        config: Dict[str, Any],
    ) -> ACUEntity:
        """Create an ACUEntity from configuration."""
        # Get geometry model if specified
        geometry_model = acu_data.get("geometryModel")
        models = config.get("models", {}).get("geometryModels", {}).get("acus", {})

        size_data = acu_data.get("size", {})
        supply_data = acu_data.get("supplyFace", {})
        return_data = acu_data.get("returnFace", {})

        if geometry_model and geometry_model in models:
            model_config = models[geometry_model]
            size_data = {**model_config.get("size", {}), **size_data}
            supply_data = {**model_config.get("supplyFace", {}), **supply_data}
            return_data = {**model_config.get("returnFace", {}), **return_data}

        location = Vec3(
            x=acu_data.get("location", {}).get("x", 0),
            y=acu_data.get("location", {}).get("y", 0),
            z=acu_data.get("location", {}).get("z", 0),
        )
        size = Vec3(
            x=size_data.get("x", 1.8),
            y=size_data.get("y", 0.99),
            z=size_data.get("z", 1.38),
        )

        supply_face = FaceConfig(
            side=supply_data.get("side", "bottom"),
            width=supply_data.get("width", size.x),
            length=supply_data.get("length", size.y * 0.6),
            offset_x=supply_data.get("offset", {}).get("x", 0),
            offset_y=supply_data.get("offset", {}).get("y", 0),
            offset_z=supply_data.get("offset", {}).get("z", 0),
        )

        return_face = FaceConfig(
            side=return_data.get("side", "top"),
            width=return_data.get("width", size.x),
            length=return_data.get("length", size.y),
            offset_x=return_data.get("offset", {}).get("x", 0),
            offset_y=return_data.get("offset", {}).get("y", 0),
            offset_z=return_data.get("offset", {}).get("z", 0),
        )

        return ACUEntity(
            uid=uid,
            location=location,
            size=size,
            orientation=acu_data.get("orientation", 0),
            supply_face=supply_face,
            return_face=return_face,
        )

    def generate_stl_files(self, force: bool = False) -> Dict[str, Path]:
        """
        Generate STL files for all entities.

        For RackEntity with leakage zones enabled, generates hollow rack STL
        using trimesh boolean difference, plus separate leakage zone STLs.

        Also generates:
        - Room wall STL for polygon room outer walls
        - Baffle STL files for raised floor, false ceiling, containments

        Args:
            force: If True, regenerate even if files exist

        Returns:
            Dictionary mapping entity UIDs to STL file paths
        """
        self.stl_dir.mkdir(parents=True, exist_ok=True)

        stl_files = {}
        self._leakage_stl_files: Dict[str, List[Path]] = {}  # Track leakage STLs per rack
        self._baffle_stl_files: Dict[str, Path] = {}  # Track baffle STLs

        # Generate room wall STL (polygon room outer walls)
        if self.room_geometry:
            room_wall_stl = self._generate_room_wall_stl(force)
            if room_wall_stl:
                stl_files["room_wall"] = room_wall_stl

            # Generate baffle STL files
            baffle_stl_files = self._generate_baffle_stl_files(force)
            stl_files.update(baffle_stl_files)

        for entity in self.entities:
            stl_path = self.stl_dir / entity.stl_filename

            # Check if this is a rack with leakage zones
            if isinstance(entity, RackEntity) and entity.air_leakage.enabled:
                leakage_zones = entity.get_leakage_zones()

                if leakage_zones:
                    # Generate hollow rack STL
                    hollow_stl_path = self.stl_dir / f"{entity.uid}_hollow.stl"

                    if hollow_stl_path.exists() and not force:
                        logger.info(f"Hollow rack STL already exists: {hollow_stl_path}")
                        stl_files[entity.uid] = hollow_stl_path
                        # Check for existing leakage STLs
                        existing_leakage = []
                        for zone in leakage_zones:
                            zone_path = self.stl_dir / f"{zone.uid}.stl"
                            if zone_path.exists():
                                existing_leakage.append(zone_path)
                        self._leakage_stl_files[entity.uid] = existing_leakage
                        continue

                    # Generate hollow rack and leakage zone STLs
                    logger.info(f"Generating hollow rack STL for '{entity.uid}' with {len(leakage_zones)} leakage zones")
                    hollow_path, leakage_paths = entity.generate_hollow_rack_stl(self.stl_dir)

                    if hollow_path:
                        stl_files[entity.uid] = hollow_path
                        self._leakage_stl_files[entity.uid] = leakage_paths
                        logger.info(f"Generated hollow rack STL: {hollow_path}")
                        for lp in leakage_paths:
                            logger.info(f"Generated leakage zone STL: {lp}")
                    else:
                        # Fallback to regular STL generation
                        logger.warning(f"Hollow rack generation failed for '{entity.uid}', using regular STL")
                        regions = entity.get_solid_regions()
                        STLWriter.write_from_regions(stl_path, regions)
                        stl_files[entity.uid] = stl_path
                    continue

            # Regular STL generation for non-rack entities or racks without leakage
            if stl_path.exists() and not force:
                logger.info(f"STL file already exists: {stl_path}")
                stl_files[entity.uid] = stl_path
                continue

            # Generate solid regions
            regions = entity.get_solid_regions()

            # Write STL file
            STLWriter.write_from_regions(stl_path, regions)
            logger.info(f"Generated STL file: {stl_path} ({len(regions)} regions)")

            stl_files[entity.uid] = stl_path

        # Merge all STL files into consolidated files
        self.merge_stl_files()

        return stl_files

    def _generate_room_wall_stl(self, force: bool = False) -> Optional[Path]:
        """
        Generate STL for room outer walls (polygon room enclosure).
        
        This STL is used by snappyHexMesh to delete cells outside the room.
        """
        if not self.room_geometry:
            return None
        
        room_wall_entity = self.room_geometry.get_room_wall_entity()
        stl_path = self.stl_dir / room_wall_entity.stl_filename
        
        if stl_path.exists() and not force:
            logger.info(f"Room wall STL already exists: {stl_path}")
            return stl_path
        
        regions = room_wall_entity.get_solid_regions()
        STLWriter.write_from_regions(stl_path, regions)
        logger.info(f"Generated room wall STL: {stl_path} ({len(regions)} regions)")
        
        return stl_path

    def _get_coplanar_footprints_horizontal(
        self, baffle_z: float, exclude_uid: str = "", tolerance: float = 0.01
    ) -> List[tuple]:
        """Get footprints (2D XY bounding boxes) of objects at horizontal baffle_z level.
        
        For horizontal baffle surfaces like raised_floor (z=0.6), we need to find:
        1. All equipment (ACU, RACK, etc.) that have their bottom/top face at this z level
        2. All perforated baffles (openings) at this z level
        
        Args:
            baffle_z: The z-coordinate of the baffle surface
            exclude_uid: UID to exclude (the baffle itself)
            tolerance: Tolerance for z-coordinate matching (default 1cm)
            
        Returns:
            List of (x_min, x_max, y_min, y_max) tuples representing footprints
        """
        footprints = []
        
        # 1. Get footprints from equipment entities
        for entity in self.entities:
            bbox = entity.bounding_box
            
            # Check if entity's bottom face is at baffle_z level
            if abs(bbox.v_min.z - baffle_z) < tolerance:
                footprints.append((
                    bbox.v_min.x,
                    bbox.v_max.x,
                    bbox.v_min.y,
                    bbox.v_max.y,
                ))
                logger.debug(f"Entity '{entity.uid}' bottom at z={bbox.v_min.z:.3f} overlaps baffle at z={baffle_z:.3f}")
            
            # Check if entity's top face is at baffle_z level (for false ceiling)
            elif abs(bbox.v_max.z - baffle_z) < tolerance:
                footprints.append((
                    bbox.v_min.x,
                    bbox.v_max.x,
                    bbox.v_min.y,
                    bbox.v_max.y,
                ))
                logger.debug(f"Entity '{entity.uid}' top at z={bbox.v_max.z:.3f} overlaps baffle at z={baffle_z:.3f}")
        
        # 2. Get footprints from perforated baffles (openings) at same z level
        if self.room_geometry:
            for baffle in self.room_geometry.get_perforated_baffle_entities():
                if baffle.uid == exclude_uid:
                    continue
                regions = baffle.get_solid_regions()
                if not regions or not regions[0].triangles:
                    continue
                # Check if this baffle is at the same z level
                first_tri = regions[0].triangles[0]
                opening_z = (first_tri[0].z + first_tri[1].z + first_tri[2].z) / 3
                if abs(opening_z - baffle_z) < tolerance:
                    # Get XY bounds
                    all_x = [v.x for t in regions[0].triangles for v in [t[0], t[1], t[2]]]
                    all_y = [v.y for t in regions[0].triangles for v in [t[0], t[1], t[2]]]
                    footprints.append((
                        min(all_x),
                        max(all_x),
                        min(all_y),
                        max(all_y),
                    ))
                    logger.debug(f"Perforated baffle '{baffle.uid}' at z={opening_z:.3f} overlaps baffle at z={baffle_z:.3f}")
        
        return footprints

    def _get_coplanar_footprints_vertical_x(
        self, baffle_x: float, tolerance: float = 0.01
    ) -> List[tuple]:
        """Get footprints (2D YZ bounding boxes) of objects at vertical plane x=baffle_x.
        
        For vertical baffle surfaces like partition walls (x=const), we need to find
        all equipment that have side faces at this x level.
        
        Args:
            baffle_x: The x-coordinate of the baffle surface
            tolerance: Tolerance for x-coordinate matching (default 1cm)
            
        Returns:
            List of (y_min, y_max, z_min, z_max) tuples representing footprints
        """
        footprints = []
        
        for entity in self.entities:
            bbox = entity.bounding_box
            
            # Check if entity's min_x face is at baffle_x level
            if abs(bbox.v_min.x - baffle_x) < tolerance:
                footprints.append((
                    bbox.v_min.y,
                    bbox.v_max.y,
                    bbox.v_min.z,
                    bbox.v_max.z,
                ))
                logger.debug(f"Entity '{entity.uid}' x_min={bbox.v_min.x:.3f} overlaps vertical baffle at x={baffle_x:.3f}")
            
            # Check if entity's max_x face is at baffle_x level
            elif abs(bbox.v_max.x - baffle_x) < tolerance:
                footprints.append((
                    bbox.v_min.y,
                    bbox.v_max.y,
                    bbox.v_min.z,
                    bbox.v_max.z,
                ))
                logger.debug(f"Entity '{entity.uid}' x_max={bbox.v_max.x:.3f} overlaps vertical baffle at x={baffle_x:.3f}")
        
        return footprints

    def _get_coplanar_footprints_vertical_y(
        self, baffle_y: float, tolerance: float = 0.01
    ) -> List[tuple]:
        """Get footprints (2D XZ bounding boxes) of objects at vertical plane y=baffle_y.
        
        For vertical baffle surfaces like partition walls (y=const), we need to find
        all equipment that have side faces at this y level.
        
        Args:
            baffle_y: The y-coordinate of the baffle surface
            tolerance: Tolerance for y-coordinate matching (default 1cm)
            
        Returns:
            List of (x_min, x_max, z_min, z_max) tuples representing footprints
        """
        footprints = []
        
        for entity in self.entities:
            bbox = entity.bounding_box
            
            # Check if entity's min_y face is at baffle_y level
            if abs(bbox.v_min.y - baffle_y) < tolerance:
                footprints.append((
                    bbox.v_min.x,
                    bbox.v_max.x,
                    bbox.v_min.z,
                    bbox.v_max.z,
                ))
                logger.debug(f"Entity '{entity.uid}' y_min={bbox.v_min.y:.3f} overlaps vertical baffle at y={baffle_y:.3f}")
            
            # Check if entity's max_y face is at baffle_y level
            elif abs(bbox.v_max.y - baffle_y) < tolerance:
                footprints.append((
                    bbox.v_min.x,
                    bbox.v_max.x,
                    bbox.v_min.z,
                    bbox.v_max.z,
                ))
                logger.debug(f"Entity '{entity.uid}' y_max={bbox.v_max.y:.3f} overlaps vertical baffle at y={baffle_y:.3f}")
        
        return footprints

    def _subtract_footprints_from_horizontal_regions(
        self, regions: List[SolidRegion], footprints: List[tuple], baffle_z: float
    ) -> List[SolidRegion]:
        """Subtract footprints from horizontal baffle regions (XY plane at z=baffle_z).
        
        Args:
            regions: List of SolidRegion from the baffle
            footprints: List of (x_min, x_max, y_min, y_max) tuples to subtract
            baffle_z: The z-coordinate of the baffle
            
        Returns:
            Modified list of SolidRegion with footprints subtracted
        """
        if not footprints:
            return regions
        
        from .base import Triangle, rect_to_triangles, orient_triangles_outward, compute_triangle_normal
        
        modified_regions = []
        for region in regions:
            region_triangles = region.triangles
            if not region_triangles:
                modified_regions.append(region)
                continue
            
            # Check if region is horizontal at baffle_z
            first_tri = region_triangles[0]
            avg_z = (first_tri[0].z + first_tri[1].z + first_tri[2].z) / 3
            if abs(avg_z - baffle_z) > 0.01:
                # Not at baffle z level, keep as is
                modified_regions.append(region)
                continue
            
            # Get region bounds
            all_x = [v.x for t in region_triangles for v in [t[0], t[1], t[2]]]
            all_y = [v.y for t in region_triangles for v in [t[0], t[1], t[2]]]
            region_x_min, region_x_max = min(all_x), max(all_x)
            region_y_min, region_y_max = min(all_y), max(all_y)
            
            # Clip footprints to region bounds
            clipped_holes = []
            for fx_min, fx_max, fy_min, fy_max in footprints:
                cx_min = max(region_x_min, fx_min)
                cx_max = min(region_x_max, fx_max)
                cy_min = max(region_y_min, fy_min)
                cy_max = min(region_y_max, fy_max)
                if cx_max > cx_min and cy_max > cy_min:
                    clipped_holes.append((cx_min, cx_max, cy_min, cy_max))
            
            if not clipped_holes:
                modified_regions.append(region)
                continue
            
            # Use grid-based subtraction
            # Snap all coordinates to 1mm precision to avoid degenerate cells
            # from floating-point differences between baffle and rack coordinates
            SNAP = 1e-3
            def snap(v): return round(v / SNAP) * SNAP
            xs = {snap(region_x_min), snap(region_x_max)}
            ys = {snap(region_y_min), snap(region_y_max)}
            snapped_holes = []
            for hx_min, hx_max, hy_min, hy_max in clipped_holes:
                sh = (snap(hx_min), snap(hx_max), snap(hy_min), snap(hy_max))
                xs.update([sh[0], sh[1]])
                ys.update([sh[2], sh[3]])
                snapped_holes.append(sh)

            xs_sorted = sorted(xs)
            ys_sorted = sorted(ys)

            # Determine normal direction
            normal = compute_triangle_normal(region_triangles[0])
            normal_up = normal.z > 0
            expected_normal = Vec3(0, 0, 1 if normal_up else -1)

            # Generate remaining rectangles
            new_triangles = []
            for i in range(len(xs_sorted) - 1):
                for j in range(len(ys_sorted) - 1):
                    rx_min, rx_max = xs_sorted[i], xs_sorted[i + 1]
                    ry_min, ry_max = ys_sorted[j], ys_sorted[j + 1]
                    # Skip degenerate cells
                    if rx_max - rx_min < SNAP * 0.5 or ry_max - ry_min < SNAP * 0.5:
                        continue
                    cx, cy = (rx_min + rx_max) / 2, (ry_min + ry_max) / 2

                    is_hole = any(
                        hx_min <= cx <= hx_max and hy_min <= cy <= hy_max
                        for hx_min, hx_max, hy_min, hy_max in snapped_holes
                    )

                    if not is_hole:
                        v00 = Vec3(rx_min, ry_min, baffle_z)
                        v10 = Vec3(rx_max, ry_min, baffle_z)
                        v11 = Vec3(rx_max, ry_max, baffle_z)
                        v01 = Vec3(rx_min, ry_max, baffle_z)
                        tris = rect_to_triangles(v00, v10, v11, v01)
                        tris = orient_triangles_outward(tris, expected_normal)
                        new_triangles.extend(tris)
            
            if new_triangles:
                new_region = SolidRegion(
                    name=region.name,
                    patch_type=region.patch_type,
                    refinement_level=region.refinement_level,
                )
                new_region.triangles = new_triangles
                modified_regions.append(new_region)
                logger.info(f"Subtracted {len(clipped_holes)} footprints from horizontal region '{region.name}'")
            else:
                logger.warning(f"Region '{region.name}' entirely covered by footprints - skipping")
        
        return modified_regions

    def _subtract_footprints_from_vertical_x_regions(
        self, regions: List[SolidRegion], footprints: List[tuple], baffle_x: float
    ) -> List[SolidRegion]:
        """Subtract footprints from vertical baffle regions (YZ plane at x=baffle_x).
        
        Args:
            regions: List of SolidRegion from the baffle
            footprints: List of (y_min, y_max, z_min, z_max) tuples to subtract
            baffle_x: The x-coordinate of the baffle
            
        Returns:
            Modified list of SolidRegion with footprints subtracted
        """
        if not footprints:
            return regions
        
        from .base import Triangle, rect_to_triangles, orient_triangles_outward, compute_triangle_normal
        
        modified_regions = []
        for region in regions:
            region_triangles = region.triangles
            if not region_triangles:
                modified_regions.append(region)
                continue
            
            # Check if region is vertical at baffle_x
            all_x = [v.x for t in region_triangles for v in [t[0], t[1], t[2]]]
            avg_x = sum(all_x) / len(all_x)
            x_range = max(all_x) - min(all_x)
            if x_range > 0.01 or abs(avg_x - baffle_x) > 0.01:
                # Not at baffle x level or not a YZ plane, keep as is
                modified_regions.append(region)
                continue
            
            # Get region bounds in YZ
            all_y = [v.y for t in region_triangles for v in [t[0], t[1], t[2]]]
            all_z = [v.z for t in region_triangles for v in [t[0], t[1], t[2]]]
            region_y_min, region_y_max = min(all_y), max(all_y)
            region_z_min, region_z_max = min(all_z), max(all_z)
            
            # Clip footprints to region bounds
            clipped_holes = []
            for fy_min, fy_max, fz_min, fz_max in footprints:
                cy_min = max(region_y_min, fy_min)
                cy_max = min(region_y_max, fy_max)
                cz_min = max(region_z_min, fz_min)
                cz_max = min(region_z_max, fz_max)
                if cy_max > cy_min and cz_max > cz_min:
                    clipped_holes.append((cy_min, cy_max, cz_min, cz_max))
            
            if not clipped_holes:
                modified_regions.append(region)
                continue
            
            # Use grid-based subtraction
            # Snap to 1mm precision to avoid degenerate cells from floating-point drift
            SNAP = 1e-3
            def snap(v): return round(v / SNAP) * SNAP
            ys = {snap(region_y_min), snap(region_y_max)}
            zs = {snap(region_z_min), snap(region_z_max)}
            snapped_holes = []
            for hy_min, hy_max, hz_min, hz_max in clipped_holes:
                sh = (snap(hy_min), snap(hy_max), snap(hz_min), snap(hz_max))
                ys.update([sh[0], sh[1]])
                zs.update([sh[2], sh[3]])
                snapped_holes.append(sh)

            ys_sorted = sorted(ys)
            zs_sorted = sorted(zs)

            # Determine normal direction (positive or negative x)
            normal = compute_triangle_normal(region_triangles[0])
            normal_positive = normal.x > 0
            expected_normal = Vec3(1 if normal_positive else -1, 0, 0)

            # Generate remaining rectangles
            new_triangles = []
            for i in range(len(ys_sorted) - 1):
                for j in range(len(zs_sorted) - 1):
                    ry_min, ry_max = ys_sorted[i], ys_sorted[i + 1]
                    rz_min, rz_max = zs_sorted[j], zs_sorted[j + 1]
                    if ry_max - ry_min < SNAP * 0.5 or rz_max - rz_min < SNAP * 0.5:
                        continue
                    cy, cz = (ry_min + ry_max) / 2, (rz_min + rz_max) / 2

                    is_hole = any(
                        hy_min <= cy <= hy_max and hz_min <= cz <= hz_max
                        for hy_min, hy_max, hz_min, hz_max in snapped_holes
                    )

                    if not is_hole:
                        v00 = Vec3(baffle_x, ry_min, rz_min)
                        v10 = Vec3(baffle_x, ry_max, rz_min)
                        v11 = Vec3(baffle_x, ry_max, rz_max)
                        v01 = Vec3(baffle_x, ry_min, rz_max)
                        tris = rect_to_triangles(v00, v10, v11, v01)
                        tris = orient_triangles_outward(tris, expected_normal)
                        new_triangles.extend(tris)
            
            if new_triangles:
                new_region = SolidRegion(
                    name=region.name,
                    patch_type=region.patch_type,
                    refinement_level=region.refinement_level,
                )
                new_region.triangles = new_triangles
                modified_regions.append(new_region)
                logger.info(f"Subtracted {len(clipped_holes)} footprints from vertical-X region '{region.name}'")
            else:
                logger.warning(f"Region '{region.name}' entirely covered by footprints - skipping")
        
        return modified_regions

    def _subtract_footprints_from_vertical_y_regions(
        self, regions: List[SolidRegion], footprints: List[tuple], baffle_y: float
    ) -> List[SolidRegion]:
        """Subtract footprints from vertical baffle regions (XZ plane at y=baffle_y).
        
        Args:
            regions: List of SolidRegion from the baffle
            footprints: List of (x_min, x_max, z_min, z_max) tuples to subtract
            baffle_y: The y-coordinate of the baffle
            
        Returns:
            Modified list of SolidRegion with footprints subtracted
        """
        if not footprints:
            return regions
        
        from .base import Triangle, rect_to_triangles, orient_triangles_outward, compute_triangle_normal
        
        modified_regions = []
        for region in regions:
            region_triangles = region.triangles
            if not region_triangles:
                modified_regions.append(region)
                continue
            
            # Check if region is vertical at baffle_y
            all_y = [v.y for t in region_triangles for v in [t[0], t[1], t[2]]]
            avg_y = sum(all_y) / len(all_y)
            y_range = max(all_y) - min(all_y)
            if y_range > 0.01 or abs(avg_y - baffle_y) > 0.01:
                # Not at baffle y level or not an XZ plane, keep as is
                modified_regions.append(region)
                continue
            
            # Get region bounds in XZ
            all_x = [v.x for t in region_triangles for v in [t[0], t[1], t[2]]]
            all_z = [v.z for t in region_triangles for v in [t[0], t[1], t[2]]]
            region_x_min, region_x_max = min(all_x), max(all_x)
            region_z_min, region_z_max = min(all_z), max(all_z)
            
            # Clip footprints to region bounds
            clipped_holes = []
            for fx_min, fx_max, fz_min, fz_max in footprints:
                cx_min = max(region_x_min, fx_min)
                cx_max = min(region_x_max, fx_max)
                cz_min = max(region_z_min, fz_min)
                cz_max = min(region_z_max, fz_max)
                if cx_max > cx_min and cz_max > cz_min:
                    clipped_holes.append((cx_min, cx_max, cz_min, cz_max))
            
            if not clipped_holes:
                modified_regions.append(region)
                continue
            
            # Use grid-based subtraction
            # Snap to 1mm precision to avoid degenerate cells from floating-point drift
            SNAP = 1e-3
            def snap(v): return round(v / SNAP) * SNAP
            xs = {snap(region_x_min), snap(region_x_max)}
            zs = {snap(region_z_min), snap(region_z_max)}
            snapped_holes = []
            for hx_min, hx_max, hz_min, hz_max in clipped_holes:
                sh = (snap(hx_min), snap(hx_max), snap(hz_min), snap(hz_max))
                xs.update([sh[0], sh[1]])
                zs.update([sh[2], sh[3]])
                snapped_holes.append(sh)

            xs_sorted = sorted(xs)
            zs_sorted = sorted(zs)

            # Determine normal direction (positive or negative y)
            normal = compute_triangle_normal(region_triangles[0])
            normal_positive = normal.y > 0
            expected_normal = Vec3(0, 1 if normal_positive else -1, 0)

            # Generate remaining rectangles
            new_triangles = []
            for i in range(len(xs_sorted) - 1):
                for j in range(len(zs_sorted) - 1):
                    rx_min, rx_max = xs_sorted[i], xs_sorted[i + 1]
                    rz_min, rz_max = zs_sorted[j], zs_sorted[j + 1]
                    if rx_max - rx_min < SNAP * 0.5 or rz_max - rz_min < SNAP * 0.5:
                        continue
                    cx, cz = (rx_min + rx_max) / 2, (rz_min + rz_max) / 2

                    is_hole = any(
                        hx_min <= cx <= hx_max and hz_min <= cz <= hz_max
                        for hx_min, hx_max, hz_min, hz_max in snapped_holes
                    )

                    if not is_hole:
                        v00 = Vec3(rx_min, baffle_y, rz_min)
                        v10 = Vec3(rx_max, baffle_y, rz_min)
                        v11 = Vec3(rx_max, baffle_y, rz_max)
                        v01 = Vec3(rx_min, baffle_y, rz_max)
                        tris = rect_to_triangles(v00, v10, v11, v01)
                        tris = orient_triangles_outward(tris, expected_normal)
                        new_triangles.extend(tris)
            
            if new_triangles:
                new_region = SolidRegion(
                    name=region.name,
                    patch_type=region.patch_type,
                    refinement_level=region.refinement_level,
                )
                new_region.triangles = new_triangles
                modified_regions.append(new_region)
                logger.info(f"Subtracted {len(clipped_holes)} footprints from vertical-Y region '{region.name}'")
            else:
                logger.warning(f"Region '{region.name}' entirely covered by footprints - skipping")
        
        return modified_regions

    def _detect_baffle_plane(self, regions: List[SolidRegion]) -> tuple:
        """Detect the plane type and coordinate of a baffle from its solid regions.
        
        Returns:
            Tuple of (plane_type, coordinate) where:
            - plane_type: 'horizontal_z', 'vertical_x', 'vertical_y', or 'unknown'
            - coordinate: The z, x, or y value of the plane
        """
        if not regions or not regions[0].triangles:
            return ('unknown', 0.0)
        
        # Get all vertex coordinates from first region
        tris = regions[0].triangles
        all_x = [v.x for t in tris for v in [t[0], t[1], t[2]]]
        all_y = [v.y for t in tris for v in [t[0], t[1], t[2]]]
        all_z = [v.z for t in tris for v in [t[0], t[1], t[2]]]
        
        x_range = max(all_x) - min(all_x)
        y_range = max(all_y) - min(all_y)
        z_range = max(all_z) - min(all_z)
        
        # Determine which dimension is constant (near zero range)
        tolerance = 0.01
        if z_range < tolerance:
            # Horizontal plane (constant z)
            return ('horizontal_z', sum(all_z) / len(all_z))
        elif x_range < tolerance:
            # Vertical plane perpendicular to X (constant x)
            return ('vertical_x', sum(all_x) / len(all_x))
        elif y_range < tolerance:
            # Vertical plane perpendicular to Y (constant y)
            return ('vertical_y', sum(all_y) / len(all_y))
        else:
            return ('unknown', 0.0)

    def _get_baffle_own_openings_footprints(
        self, baffle: BaffleEntity, plane_type: str
    ) -> List[tuple]:
        """Get footprints from baffle's own openings.
        
        Args:
            baffle: The baffle entity
            plane_type: 'horizontal_z', 'vertical_x', or 'vertical_y'
            
        Returns:
            List of footprint tuples (depends on plane type)
        """
        footprints = []
        
        # Check if this is a ContainmentBaffleEntity with openings
        if isinstance(baffle, ContainmentBaffleEntity) and baffle.openings:
            for opening in baffle.openings:
                if plane_type == 'horizontal_z':
                    # XY footprint
                    footprints.append((
                        opening.location.x,
                        opening.location.x + opening.size.x,
                        opening.location.y,
                        opening.location.y + opening.size.y,
                    ))
                elif plane_type == 'vertical_x':
                    # YZ footprint
                    footprints.append((
                        opening.location.y,
                        opening.location.y + opening.size.y,
                        opening.location.z,
                        opening.location.z + opening.size.z,
                    ))
                elif plane_type == 'vertical_y':
                    # XZ footprint
                    footprints.append((
                        opening.location.x,
                        opening.location.x + opening.size.x,
                        opening.location.z,
                        opening.location.z + opening.size.z,
                    ))
                logger.debug(f"Baffle '{baffle.uid}' has opening '{opening.uid}' to preserve")
        
        return footprints

    def _subtract_coplanar_objects_from_baffle(
        self, baffle: BaffleEntity, regions: List[SolidRegion]
    ) -> List[SolidRegion]:
        """Subtract coplanar objects from baffle regions.
        
        Automatically detects the baffle plane type and applies the appropriate
        footprint subtraction. Also preserves the baffle's own openings.
        """
        plane_type, coord = self._detect_baffle_plane(regions)
        
        if plane_type == 'horizontal_z':
            footprints = self._get_coplanar_footprints_horizontal(coord, exclude_uid=baffle.uid)
            # Add baffle's own openings
            own_openings = self._get_baffle_own_openings_footprints(baffle, plane_type)
            footprints.extend(own_openings)
            if footprints:
                logger.info(f"Found {len(footprints)} coplanar objects for horizontal baffle '{baffle.uid}' at z={coord:.3f}")
                regions = self._subtract_footprints_from_horizontal_regions(regions, footprints, coord)
        elif plane_type == 'vertical_x':
            footprints = self._get_coplanar_footprints_vertical_x(coord)
            # Add baffle's own openings
            own_openings = self._get_baffle_own_openings_footprints(baffle, plane_type)
            footprints.extend(own_openings)
            if footprints:
                logger.info(f"Found {len(footprints)} coplanar objects for vertical-X baffle '{baffle.uid}' at x={coord:.3f}")
                regions = self._subtract_footprints_from_vertical_x_regions(regions, footprints, coord)
        elif plane_type == 'vertical_y':
            footprints = self._get_coplanar_footprints_vertical_y(coord)
            # Add baffle's own openings
            own_openings = self._get_baffle_own_openings_footprints(baffle, plane_type)
            footprints.extend(own_openings)
            if footprints:
                logger.info(f"Found {len(footprints)} coplanar objects for vertical-Y baffle '{baffle.uid}' at y={coord:.3f}")
                regions = self._subtract_footprints_from_vertical_y_regions(regions, footprints, coord)
        
        return regions

    def _generate_baffle_stl_files(self, force: bool = False) -> Dict[str, Path]:
        """
        Generate STL files for ALL baffle entities (wall and perforated types).
        
        Wall-type baffles (containment, raised_floor_panel, etc.):
        - STL merged into merged_wall_baffles.stl
        - snappyHexMesh creates wall boundary patches directly
        - No createBaffles needed
        
        Perforated baffles (opening_raised_floor, etc.):
        - STL merged into merged_perforated_baffles.stl
        - NOT processed by snappyHexMesh (to keep internal faces)
        - createBaffles uses searchableSurface (triSurfaceMesh) to reference STL
        - Creates master/slave cyclic pairs at STL location
        """
        if not self.room_geometry:
            return {}
        
        stl_files = {}
        self._baffle_stl_files: Dict[str, Path] = {}
        self._wall_baffle_stl_files: Dict[str, Path] = {}
        self._perforated_baffle_stl_files: Dict[str, Path] = {}
        
        # Process wall-type baffles
        wall_baffle_entities = self.room_geometry.get_wall_baffle_entities()
        for baffle in wall_baffle_entities:
            stl_path = self.stl_dir / baffle.stl_filename
            
            if stl_path.exists() and not force:
                logger.info(f"Wall baffle STL already exists: {stl_path}")
            else:
                regions = baffle.get_solid_regions()
                if regions:
                    # Subtract footprints of coplanar objects (ACU, RACK, openings, etc.)
                    # Automatically detects horizontal/vertical plane and applies appropriate subtraction.
                    # For containment baffles, subtract rack footprints so the baffle only covers
                    # the gap between rack columns (not the rack surfaces themselves, which are
                    # separate STL regions). Overlapping surfaces cause snappyHexMesh to absorb
                    # rack patches into the baffle faceZone.
                    regions = self._subtract_coplanar_objects_from_baffle(baffle, regions)
                    
                    if regions:
                        STLWriter.write_from_regions(stl_path, regions)
                        logger.info(f"Generated wall baffle STL: {stl_path} ({len(regions)} regions)")
            
            stl_files[baffle.uid] = stl_path
            self._baffle_stl_files[baffle.uid] = stl_path
            self._wall_baffle_stl_files[baffle.uid] = stl_path
        
        # Process perforated baffles (STL for createBaffles searchableSurface)
        perforated_baffle_entities = self.room_geometry.get_perforated_baffle_entities()
        for baffle in perforated_baffle_entities:
            stl_path = self.stl_dir / baffle.stl_filename
            
            if stl_path.exists() and not force:
                logger.info(f"Perforated baffle STL already exists: {stl_path}")
            else:
                regions = baffle.get_solid_regions()
                if regions:
                    STLWriter.write_from_regions(stl_path, regions)
                    logger.info(f"Generated perforated baffle STL: {stl_path} ({len(regions)} regions)")
            
            stl_files[baffle.uid] = stl_path
            self._baffle_stl_files[baffle.uid] = stl_path
            self._perforated_baffle_stl_files[baffle.uid] = stl_path
        
        return stl_files

    def get_baffle_entities(self) -> List[BaffleEntity]:
        """Get all baffle entities."""
        if not self.room_geometry:
            return []
        return self.room_geometry.get_all_baffle_entities()

    def get_perforated_baffle_entities(self) -> List[BaffleEntity]:
        """Get perforated baffle entities (need master/slave patches)."""
        if not self.room_geometry:
            return []
        return self.room_geometry.get_perforated_baffle_entities()

    def get_wall_baffle_entities(self) -> List[BaffleEntity]:
        """Get wall-type baffle entities."""
        if not self.room_geometry:
            return []
        return self.room_geometry.get_wall_baffle_entities()

    def merge_stl_files(self) -> None:
        """
        Merge all STL files into consolidated files:
        1. merged_rack.stl - all RACK hollow STL files (for server inlet/outlet features)
        2. merged_geometry.stl - all non-rack equipment STL files (ACUs, Pillars, etc.)
        3. merged_leakage.stl - all leakage zone STL files
        4. merged_wall_baffles.stl - wall-type baffles (containment, raised_floor_panel)
        5. merged_perforated_baffles.stl - perforated baffles (opening_raised_floor)
        
        Note: room_wall.stl is kept separate as it's used for cell removal.
        
        Each solid region is preserved with its original name.
        """
        rack_solids = []      # (solid_name, content) for RACK hollow STL
        geometry_solids = []  # (solid_name, content) for non-rack equipment
        leakage_solids = []   # (solid_name, content) for leakage
        wall_baffle_solids = []  # (solid_name, content) for wall-type baffles
        perforated_baffle_solids = []  # (solid_name, content) for perforated baffles
        
        # Get baffle STL file names by type
        wall_baffle_stl_names = set()
        perforated_baffle_stl_names = set()
        if hasattr(self, '_wall_baffle_stl_files'):
            wall_baffle_stl_names = {p.name for p in self._wall_baffle_stl_files.values()}
        if hasattr(self, '_perforated_baffle_stl_files'):
            perforated_baffle_stl_names = {p.name for p in self._perforated_baffle_stl_files.values()}

        # Rack STL filenames for entities without leakage (no _hollow.stl generated)
        rack_entity_stl_names = set()
        for entity in self.entities:
            if isinstance(entity, RackEntity):
                rack_entity_stl_names.add(entity.stl_filename)  # e.g. "Default_Rack_1_copy.stl"

        # Files to skip during merge
        skip_files = [
            "merged_rack.stl", "merged_geometry.stl", "merged_leakage.stl", 
            "merged_baffles.stl",  # Legacy file name
            "merged_wall_baffles.stl", "merged_perforated_baffles.stl",
            "room_wall.stl"
        ]
        
        # Process all STL files
        for stl_file in sorted(self.stl_dir.glob("*.stl")):
            if stl_file.name in skip_files:
                continue
                
            try:
                content = stl_file.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning(f"Failed to read {stl_file}: {e}")
                continue
            
            # Classify STL file type
            is_rack_hollow = "_hollow.stl" in stl_file.name or stl_file.name in rack_entity_stl_names
            is_leakage = "_leakage_" in stl_file.name
            is_wall_baffle = stl_file.name in wall_baffle_stl_names
            is_perforated_baffle = stl_file.name in perforated_baffle_stl_names
            
            # Parse solid blocks
            lines = content.split('\n')
            current_solid_name = None
            current_solid_lines = []
            solid_idx = 0
            
            for line in lines:
                stripped = line.strip().lower()
                
                # Check for 'solid' keyword at line start (but not 'endsolid')
                if stripped.startswith('solid') and not stripped.startswith('endsolid'):
                    # Start of a new solid
                    name_part = line.strip()[5:].strip()  # Remove 'solid' prefix
                    if name_part and not name_part.lower().startswith('facet'):
                        current_solid_name = name_part
                    else:
                        # Empty name, use filename
                        current_solid_name = stl_file.stem
                        if solid_idx > 0:
                            current_solid_name = f"{current_solid_name}_{solid_idx}"
                    current_solid_lines = []
                    
                elif stripped.startswith('endsolid'):
                    # End of current solid
                    if current_solid_name and current_solid_lines:
                        solid_content = '\n'.join(current_solid_lines)
                        solid_block = f"solid {current_solid_name}\n{solid_content}\nendsolid {current_solid_name}\n"
                        if is_leakage:
                            leakage_solids.append((current_solid_name, solid_block))
                        elif is_wall_baffle:
                            wall_baffle_solids.append((current_solid_name, solid_block))
                        elif is_perforated_baffle:
                            perforated_baffle_solids.append((current_solid_name, solid_block))
                        elif is_rack_hollow:
                            rack_solids.append((current_solid_name, solid_block))
                        else:
                            geometry_solids.append((current_solid_name, solid_block))
                        solid_idx += 1
                    current_solid_name = None
                    current_solid_lines = []
                elif current_solid_name is not None:
                    # Inside a solid block
                    current_solid_lines.append(line)
        
        # Write merged rack file (only RACK hollow STL with server inlet/outlet)
        if rack_solids:
            merged_rack_path = self.stl_dir / "merged_rack.stl"
            with open(merged_rack_path, "w", encoding="utf-8") as f:
                for solid_name, solid_block in rack_solids:
                    f.write(solid_block)
                    f.write("\n")
            logger.info(f"Merged {len(rack_solids)} rack solid regions into: {merged_rack_path}")
        
        # NOTE: Non-rack equipment (ACU, Pillar, Box, etc.) are kept as individual STL files
        # They are NOT merged into merged_geometry.stl per requirement 10
        # Each equipment can have different boundary conditions on different faces
        if geometry_solids:
            logger.info(f"Non-rack equipment ({len(geometry_solids)} solids) kept as individual STL files (not merged)")
        
        # Write merged leakage file with unified solid name
        if leakage_solids:
            merged_leakage_path = self.stl_dir / "merged_leakage.stl"
            with open(merged_leakage_path, "w", encoding="utf-8") as f:
                f.write("solid leakage\n")
                for solid_name, solid_block in leakage_solids:
                    lines = solid_block.split('\n')
                    for line in lines:
                        stripped = line.strip().lower()
                        if stripped.startswith('solid') or stripped.startswith('endsolid'):
                            continue
                        if line.strip():
                            f.write(line + "\n")
                f.write("endsolid leakage\n")
            logger.info(f"Merged {len(leakage_solids)} leakage zones into unified solid: {merged_leakage_path}")
        
        # Write merged wall baffles file (single-face wall patches)
        if wall_baffle_solids:
            merged_wall_baffles_path = self.stl_dir / "merged_wall_baffles.stl"
            with open(merged_wall_baffles_path, "w", encoding="utf-8") as f:
                for solid_name, solid_block in wall_baffle_solids:
                    f.write(solid_block)
                    f.write("\n")
            logger.info(f"Merged {len(wall_baffle_solids)} wall baffle regions into: {merged_wall_baffles_path}")
        
        # Perforated baffles: DO NOT merge, keep individual STL files
        # Each perforated baffle needs its own STL for createBaffles to reference
        if perforated_baffle_solids:
            logger.info(f"Perforated baffles ({len(perforated_baffle_solids)}) kept as individual STL files (not merged)")

    def get_leakage_stl_files(self) -> Dict[str, List[Path]]:
        """Get leakage zone STL files for all racks.

        Returns:
            Dictionary mapping rack UIDs to lists of leakage zone STL paths.
        """
        return getattr(self, '_leakage_stl_files', {})

    def get_all_leakage_zones(self) -> List[LeakageZone]:
        """Get all leakage zones from all racks.

        Returns:
            List of LeakageZone objects from all racks.
        """
        zones = []
        for entity in self.entities:
            if isinstance(entity, RackEntity) and entity.air_leakage.enabled:
                zones.extend(entity.get_leakage_zones())
        return zones

    def get_leakage_merge_config(self) -> List[Dict[str, str]]:
        """Get configuration for merging leakage patches into rack wall patches.
        
        Returns:
            List of dicts with 'leakage_patch' and 'target_patch' keys.
            Used by createPatchDict to merge leakage patches into walls.
        """
        merge_configs = []
        
        for entity in self.entities:
            if isinstance(entity, RackEntity) and entity.air_leakage.enabled:
                safe_uid = entity.safe_uid
                
                # The leakage patch is named "merged_leakage" (single unified solid)
                # We want to merge it into each rack's wall
                # Since all leakage is unified, we only need one merge operation
                # targeting a generic wall patch
                pass  # Leakage is now unified, no per-rack patches to merge
        
        # If there's a unified "merged_leakage" patch, we could merge it
        # But since snappyHexMesh creates it from triSurfaceMesh without regions,
        # it becomes a single patch that can be renamed or left as wall
        
        return merge_configs

    def get_topoSet_leakage_zones_cmd(self) -> str:
        """Generate topoSetDict commands for selecting leakage zone cellZones.

        Returns:
            topoSetDict actions string for leakage zones.
        """
        cmd = ""
        for entity in self.entities:
            if isinstance(entity, RackEntity) and entity.air_leakage.enabled:
                leakage_zones = entity.get_leakage_zones()
                for zone in leakage_zones:
                    v_min, v_max = entity._get_leakage_zone_bbox(zone)
                    cmd += f"""
    {{
        name    {zone.cellzone_name}Set;
        type    cellSet;
        action  new;
        source  boxToCell;
        box     ({v_min.x} {v_min.y} {v_min.z}) ({v_max.x} {v_max.y} {v_max.z});
    }}
    {{
        name    {zone.cellzone_name};
        type    cellZoneSet;
        action  new;
        source  setToCellZone;
        set     {zone.cellzone_name}Set;
    }}
"""
        return cmd

    def get_leakage_zones_bbox_config(self) -> Dict[str, Dict[str, Any]]:
        """Get bounding box configuration for leakage zones.
        
        Returns a dictionary mapping zone names to their bbox coordinates,
        for use in topoSetDict template rendering.
        """
        from dataclasses import dataclass
        
        config = {}
        for entity in self.entities:
            if isinstance(entity, RackEntity) and entity.air_leakage.enabled:
                leakage_zones = entity.get_leakage_zones()
                for zone in leakage_zones:
                    v_min, v_max = entity._get_leakage_zone_bbox(zone)
                    config[zone.cellzone_name] = {
                        "bbox_min": {"x": v_min.x, "y": v_min.y, "z": v_min.z},
                        "bbox_max": {"x": v_max.x, "y": v_max.y, "z": v_max.z},
                    }
        return config

    def get_snappyHexMesh_geometry_cmd(self, use_merged: bool = True) -> str:
        """Generate the geometry section of snappyHexMeshDict.
        
        Per requirement 10:
        - merged_rack.stl contains all RACK hollow STL files
        - Other equipment (ACU, Pillar, Box) kept as individual STL files
        
        Args:
            use_merged: If True, use merged_rack.stl + individual equipment STL files.
        """
        cmd = ""

        if use_merged:
            # Step 1: Add merged_rack.stl for all RACK hollow geometries
            # Read actual region names from the merged STL file
            rack_regions = self._collect_merged_rack_regions()
            
            if rack_regions:
                region_cmds = [f"            {name} {{ name {name}; }}" for name in rack_regions]
                cmd += f"""
    merged_rack.stl
    {{
        type triSurfaceMesh;
        name merged_rack;
        regions
        {{
{chr(10).join(region_cmds)}
        }}
    }}
"""
            
            # Step 2: Add individual STL files for non-RACK equipment (ACU, Pillar, Box, etc.)
            for entity in self.entities:
                if isinstance(entity, RackEntity):
                    continue  # Skip - already in merged_rack.stl
                    
                regions = entity.get_solid_regions()
                stl_filename = entity.stl_filename
                safe_uid = entity.safe_uid
                
                region_cmds = []
                for region in regions:
                    region_cmds.append(f"            {region.name} {{ name {region.name}; }}")
                
                cmd += f"""
    {stl_filename}
    {{
        type triSurfaceMesh;
        name {safe_uid};
        regions
        {{
{chr(10).join(region_cmds)}
        }}
    }}
"""
            # Add searchableBox for each entity to help with cell deletion
            for entity in self.entities:
                bbox = entity.bounding_box
                safe_uid = entity.safe_uid
                cmd += f"""
    {safe_uid}_box
    {{
        type searchableBox;
        min ({bbox.v_min.x} {bbox.v_min.y} {bbox.v_min.z});
        max ({bbox.v_max.x} {bbox.v_max.y} {bbox.v_max.z});
    }}
"""
            # Add searchableBox geometry for leakage zones (for volume refinement)
            # cellZones are still created via topoSet after snappyHexMesh
            cmd += self.get_snappyHexMesh_geometry_leakage_boxes_cmd()
            
            # Note: room_wall.stl and merged_baffles.stl are added separately
            # via template variables (room_wall_geometry_cmd, baffles_geometry_cmd)
            # to keep the geometry section cleaner and more modular
        else:
            # Original per-entity STL logic
            for entity in self.entities:
                regions = entity.get_solid_regions()
                stl_filename = entity.stl_filename
                safe_uid = entity.safe_uid

                region_cmds = []
                for region in regions:
                    region_cmds.append(f"            {region.name} {{ name {region.name}; }}")

                cmd += f"""
    {stl_filename}
    {{
        type triSurfaceMesh;
        name {safe_uid};
        regions
        {{
{chr(10).join(region_cmds)}
        }}
    }}
"""
                bbox = entity.bounding_box
                cmd += f"""
    {safe_uid}_box
    {{
        type searchableBox;
        min ({bbox.v_min.x} {bbox.v_min.y} {bbox.v_min.z});
        max ({bbox.v_max.x} {bbox.v_max.y} {bbox.v_max.z});
    }}
"""

        return cmd

    def _get_room_wall_geometry_cmd(self) -> str:
        """Generate geometry command for room_wall.stl (polygon room outer walls)."""
        if not self.room_geometry:
            return ""
        
        room_wall_entity = self.room_geometry.get_room_wall_entity()
        regions = room_wall_entity.get_solid_regions()
        region_cmds = []
        for region in regions:
            region_cmds.append(f"            {region.name} {{ name {region.name}; }}")
        
        cmd = f"""
    room_wall.stl
    {{
        type triSurfaceMesh;
        name room_wall;
        regions
        {{
{chr(10).join(region_cmds)}
        }}
    }}
"""
        return cmd

    def _get_baffles_geometry_cmd(self) -> str:
        """Generate geometry commands for all baffles.
        
        Returns geometry command for:
        - merged_wall_baffles.stl (wall-type, single-face wall patches)
        
        Perforated baffles don't have STL - they use searchablePlate in createBaffles.
        """
        wall_cmd = self._get_wall_baffles_geometry_cmd()
        perforated_cmd = self._get_perforated_baffles_geometry_cmd()
        return wall_cmd + perforated_cmd

    def _get_wall_baffles_geometry_cmd(self) -> str:
        """Generate geometry command for merged_wall_baffles.stl.
        
        Wall-type baffles become direct wall boundary patches in snappyHexMesh.
        No createBaffles needed.
        """
        if not self.room_geometry:
            return ""
        
        baffle_entities = self.room_geometry.get_wall_baffle_entities()
        if not baffle_entities:
            return ""
        
        region_cmds = []
        for baffle in baffle_entities:
            for region in baffle.get_solid_regions():
                if not region.triangles:  # Skip empty regions (e.g. raised_floor_panel fully covered by openings)
                    continue
                region_cmds.append(f"            {region.name} {{ name {region.name}; }}")

        if not region_cmds:
            return ""
        
        cmd = f"""
    merged_wall_baffles.stl
    {{
        type triSurfaceMesh;
        name merged_wall_baffles;
        regions
        {{
{chr(10).join(region_cmds)}
        }}
    }}
"""
        return cmd

    def _get_perforated_baffles_geometry_cmd(self) -> str:
        """Generate geometry commands for individual perforated baffle STL files.
        
        Each perforated baffle has its own STL file (not merged).
        
        Perforated baffles use faceZone + faceType baffle in snappyHexMesh,
        then createBaffles converts them to master/slave cyclic pairs.
        """
        if not self.room_geometry:
            return ""
        
        baffle_entities = self.room_geometry.get_perforated_baffle_entities()
        if not baffle_entities:
            return ""
        
        cmd = ""
        for baffle in baffle_entities:
            regions = baffle.get_solid_regions()
            if not regions:
                continue
            
            # Use the individual STL filename
            stl_filename = baffle.stl_filename
            stl_name = stl_filename.replace('.stl', '')
            zone_name = regions[0].name
            
            cmd += f"""
    {stl_filename}
    {{
        type triSurfaceMesh;
        name {stl_name};
    }}
"""
        return cmd

    def get_snappyHexMesh_baffle_refinementSurfaces_cmd(
        self,
        wall_level: tuple = (0, 0),
        perforated_level: tuple = (1, 1),
    ) -> str:
        """Generate refinementSurfaces commands for all baffles.
        
        Wall baffles (merged_wall_baffles.stl):
        - snappyHexMesh creates wall boundary patches directly
        - No createBaffles needed, single-face wall boundaries
        
        Perforated baffles:
        - Uses faceZone + faceType baffle for snappyHexMesh
        - createBaffles converts to master/slave cyclic pairs
        
        Args:
            wall_level: (min, max) refinement level for wall baffles
            perforated_level: (min, max) refinement level for perforated baffles
        """
        wall_cmd = self._get_wall_baffles_refinementSurfaces_cmd(wall_level)
        perforated_cmd = self._get_perforated_baffles_refinementSurfaces_cmd(perforated_level)
        return wall_cmd + perforated_cmd

    def _get_wall_baffles_refinementSurfaces_cmd(self, refinement_level: tuple = (0, 0)) -> str:
        """Generate refinementSurfaces commands for wall-type baffles.

        Wall baffles use faceType internal + faceZone, same as perforated baffles.
        createBaffles then converts them to master/slave wall patches (no-slip).

        Args:
            refinement_level: (min, max) refinement level tuple
        """
        if not self.room_geometry:
            return ""

        baffle_entities = self.room_geometry.get_wall_baffle_entities()
        if not baffle_entities:
            return ""

        # Parse level tuple
        if isinstance(refinement_level, (list, tuple)) and len(refinement_level) == 2:
            level_min, level_max = int(refinement_level[0]), int(refinement_level[1])
        elif isinstance(refinement_level, int):
            level_min = level_max = refinement_level
        else:
            level_min = level_max = 0

        region_cmds = []
        for baffle in baffle_entities:
            for region in baffle.get_solid_regions():
                if not region.triangles:  # Skip empty regions
                    continue
                region_cmds.append(f"""
            {region.name}
            {{
                level ({level_min} {level_max});
                faceZone {region.name};
                faceType internal;
            }}""")

        cmd = f"""
        merged_wall_baffles
        {{
            level ({level_min} {level_max});
            faceType internal;
            regions
            {{{chr(10).join(region_cmds)}
            }}
        }}
"""
        return cmd

    def _get_perforated_baffles_refinementSurfaces_cmd(self, refinement_level: tuple = (1, 1)) -> str:
        """Generate refinementSurfaces commands for perforated baffles.
        
        Perforated baffles use faceZone + faceType internal.
        This allows snappyHexMesh to snap mesh to STL surface and create internal faces
        in a faceZone. createBaffles then converts these to master/slave cyclic pairs.
        
        Key: faceType internal (not baffle) creates internal faces that can be
        properly converted to cyclic patches by createBaffles with correct transform.
        
        Note: snappyHexMesh uses the faceZone parameter as the faceZone name.
        
        Args:
            refinement_level: (min, max) refinement level tuple
        """
        if not self.room_geometry:
            return ""
        
        baffle_entities = self.room_geometry.get_perforated_baffle_entities()
        if not baffle_entities:
            return ""
        
        # Parse level tuple - use at least level 1 for perforated baffles
        if isinstance(refinement_level, (list, tuple)) and len(refinement_level) == 2:
            level_min = max(1, int(refinement_level[0]))
            level_max = max(1, int(refinement_level[1]))
        elif isinstance(refinement_level, int):
            level_min = level_max = max(1, refinement_level)
        else:
            level_min = level_max = 1
        
        # Perforated baffles are kept as individual STL files (not merged)
        # Each baffle gets its own refinementSurfaces entry
        cmd = ""
        for baffle in baffle_entities:
            regions = baffle.get_solid_regions()
            if not regions:
                continue
            
            # Get the STL filename for this baffle (without .stl extension)
            stl_name = baffle.stl_filename.replace('.stl', '')
            zone_name = regions[0].name
            
            # Use faceZone with faceType internal for perforated baffles
            # faceType internal: snappyHexMesh creates internal faces in faceZone
            # createBaffles then converts these to cyclic master/slave patches
            cmd += f"""
        {stl_name}
        {{
            level ({level_min} {level_max});
            faceZone {zone_name};
            faceType internal;
        }}
"""
        return cmd

    def get_snappyHexMesh_room_wall_refinementSurfaces_cmd(self, refinement_level: tuple = (0, 0)) -> str:
        """Generate refinementSurfaces commands for room wall (cell removal mode).
        
        Room wall surfaces are in room_wall.stl, so they need to be
        configured as regions under the room_wall parent entry.
        
        Args:
            refinement_level: (min, max) refinement level tuple
        """
        if not self.room_geometry:
            return ""
        
        room_wall_entity = self.room_geometry.get_room_wall_entity()
        regions = room_wall_entity.get_solid_regions()
        
        # Parse level tuple
        if isinstance(refinement_level, (list, tuple)) and len(refinement_level) == 2:
            level_min, level_max = int(refinement_level[0]), int(refinement_level[1])
        elif isinstance(refinement_level, int):
            level_min = level_max = refinement_level
        else:
            level_min = level_max = 0
        
        # Collect all region configurations
        region_cmds = []
        for region in regions:
            region_cmds.append(f"""
            {region.name}
            {{
                level ({level_min} {level_max});
                patchInfo
                {{
                    type wall;
                }}
            }}""")
        
        # Wrap in room_wall parent entry
        cmd = f"""
        room_wall
        {{
            level ({level_min} {level_max});
            regions
            {{{chr(10).join(region_cmds)}
            }}
        }}
"""
        
        return cmd

    def get_createBaffles_faceZone_cmd(self) -> str:
        """Generate createBafflesDict commands for converting faceZones to boundaries.
        
        ONLY for perforated baffles - creates cyclic master/slave pairs.
        
        Wall baffles do NOT need createBaffles because:
        - They are already wall boundary patches from snappyHexMesh
        - Single-face wall boundaries, no need to split
        
        Perforated baffles need createBaffles because:
        - snappyHexMesh creates them as faceZone with faceType baffle
        - createBaffles splits faceZone into master/slave cyclic pairs
        - Used with porousBafflePressure boundary condition
        
        Note: snappyHexMesh uses the STL region name as the faceZone name,
        NOT the faceZone name specified in snappyHexMeshDict.
        """
        if not self.room_geometry:
            return ""
        
        cmd = ""
        # Only process perforated baffles
        perforated_baffle_entities = self.room_geometry.get_perforated_baffle_entities()
        
        for baffle in perforated_baffle_entities:
            for region in baffle.get_solid_regions():
                # IMPORTANT: snappyHexMesh uses region name as faceZone name
                # (not the faceZone name specified in snappyHexMeshDict)
                face_zone_name = region.name
                master_name = f"{region.name}_master"
                slave_name = f"{region.name}_slave"
                
                cmd += f"""
    {region.name}
    {{
        type        faceZone;
        zoneName    {face_zone_name};
        
        patches
        {{
            master
            {{
                name            {master_name};
                type            cyclic;
                neighbourPatch  {slave_name};
            }}
            slave
            {{
                name            {slave_name};
                type            cyclic;
                neighbourPatch  {master_name};
            }}
        }}
    }}
"""
        
        return cmd

    def get_perforated_baffle_bc_config(self) -> Dict[str, Dict[str, Any]]:
        """Get boundary condition configuration for perforated baffles.
        
        Returns config for porousBafflePressure boundary condition.
        """
        if not self.room_geometry:
            return {}
        
        config = {}
        perforated_baffles = self.room_geometry.get_perforated_baffle_entities()
        
        for baffle in perforated_baffles:
            for region in baffle.get_solid_regions():
                master_name = f"{region.name}_master"
                slave_name = f"{region.name}_slave"
                
                # Calculate pressure drop coefficient based on porosity
                # Using orifice plate equation: K = (1/C_d^2 - 1) * (1 - beta^4) / beta^4
                # Simplified: K ≈ (1 - porosity)^2 / porosity^2 for thin plate
                porosity = baffle.porosity
                if porosity > 0 and porosity < 1:
                    # Discharge coefficient
                    D = 1.0 - (1.0 - porosity) ** 2 / porosity ** 2
                    I = 0.5 * (1.0 - porosity)
                else:
                    D = 1.0
                    I = 0.0
                
                config[master_name] = {
                    "patch_name": master_name,
                    "neighbour_patch": slave_name,
                    "porosity": porosity,
                    "D": D,
                    "I": I,
                }
                config[slave_name] = {
                    "patch_name": slave_name,
                    "neighbour_patch": master_name,
                    "porosity": porosity,
                    "D": D,
                    "I": I,
                }
        
        return config

    def get_perforated_baffle_toposet_config(self) -> List[Dict[str, str]]:
        """Get configuration for topoSetDict to create faceZones from boundary patches.
        
        Returns list of dicts with patch_name for perforated baffles.
        These are used by topoSetDict_perforated_baffle.j2 template.
        """
        if not self.room_geometry:
            return []
        
        config = []
        perforated_baffles = self.room_geometry.get_perforated_baffle_entities()
        
        for baffle in perforated_baffles:
            for region in baffle.get_solid_regions():
                config.append({
                    "patch_name": region.name,
                    "zone_name": f"{region.name}_zone",
                })
        
        return config

    def get_perforated_baffle_createbaffles_cmd(self) -> str:
        """Generate createBafflesDict commands for perforated baffles.
        
        These commands convert boundary patches into wall baffle pairs.
        Note: For boundary-derived faceZones, we use wall type with patchPairs
        because cyclic requires internal faces that can be split.
        
        The perforated behavior is achieved through porousBafflePressure BC
        applied to these wall patches (configured separately in the 0/ files).
        """
        if not self.room_geometry:
            return ""
        
        perforated_baffles = self.room_geometry.get_perforated_baffle_entities()
        if not perforated_baffles:
            return ""
        
        cmd = ""
        for baffle in perforated_baffles:
            for region in baffle.get_solid_regions():
                zone_name = f"{region.name}_zone"
                master_name = f"{region.name}_master"
                slave_name = f"{region.name}_slave"
                
                # Use wall type with patchPairs - this duplicates the boundary faces
                # into master and slave wall patches at the same location
                cmd += f"""
    {region.name}
    {{
        type        faceZone;
        zoneName    {zone_name};
        
        patchPairs
        {{
            type        wall;
            patchFields
            {{
                // Master patch
                {master_name}
                {{
                    type wall;
                }}
                // Slave patch  
                {slave_name}
                {{
                    type wall;
                }}
            }}
        }}
    }}
"""
        
        return cmd

    def get_perforated_baffle_searchable_surface_cmd(self) -> str:
        """Generate searchableSurface (triSurfaceMesh) commands for perforated baffles.
        
        Each perforated baffle references its own individual STL file (not merged).
        This ensures each baffle correctly finds its internal faces at the STL location.
        
        Returns createBafflesDict commands using triSurfaceMesh for perforated baffles.
        """
        if not self.room_geometry:
            return ""
        
        perforated_baffles = self.room_geometry.get_perforated_baffle_entities()
        if not perforated_baffles:
            return ""
        
        cmd = ""
        for baffle in perforated_baffles:
            # Use safe_uid as the name (matches STL solid name)
            name = baffle.safe_uid
            master_name = f"{name}_master"
            slave_name = f"{name}_slave"
            
            # Reference individual STL file for this baffle (not merged)
            stl_filename = baffle.stl_filename
            cmd += f"""
    {name}
    {{
        type        searchableSurface;
        surface     triSurfaceMesh;
        name        {stl_filename};
        
        patches
        {{
            master
            {{
                name            {master_name};
                type            cyclic;
                neighbourPatch  {slave_name};
            }}
            slave
            {{
                name            {slave_name};
                type            cyclic;
                neighbourPatch  {master_name};
            }}
        }}
    }}
"""
        
        return cmd
    
    def get_perforated_baffle_geometry_cmd(self) -> str:
        """Generate geometry definitions for perforated baffle STLs in createBafflesDict.
        
        Each perforated baffle needs its own geometry entry referencing its individual STL.
        """
        if not self.room_geometry:
            return ""
        
        perforated_baffles = self.room_geometry.get_perforated_baffle_entities()
        if not perforated_baffles:
            return ""
        
        cmd = ""
        for baffle in perforated_baffles:
            stl_filename = baffle.stl_filename
            cmd += f"""
    {stl_filename}
    {{
        type triSurfaceMesh;
        name {baffle.safe_uid};
    }}
"""
        
        return cmd

    def get_perforated_baffle_searchable_plate_cmd(self) -> str:
        """Generate searchablePlate commands for perforated baffles (fallback method).
        
        Uses searchablePlate with origin + span to define the baffle region.
        This is a fallback method when STL-based triSurfaceMesh is not preferred.
        
        Returns createBafflesDict commands using searchablePlate for each perforated baffle.
        """
        if not self.room_geometry:
            return ""
        
        perforated_baffles = self.room_geometry.get_perforated_baffle_entities()
        if not perforated_baffles:
            return ""
        
        cmd = ""
        for baffle in perforated_baffles:
            # Get baffle geometry from the entity's location and size
            origin = baffle.location
            span = baffle.size
            
            # Use safe_uid as the name
            name = baffle.safe_uid
            master_name = f"{name}_master"
            slave_name = f"{name}_slave"
            
            cmd += f"""
    {name}
    {{
        type        searchableSurface;
        surface     searchablePlate;
        origin      ({origin.x} {origin.y} {origin.z});
        span        ({span.x} {span.y} {span.z});
        
        patches
        {{
            master
            {{
                name            {master_name};
                type            cyclic;
                neighbourPatch  {slave_name};
            }}
            slave
            {{
                name            {slave_name};
                type            cyclic;
                neighbourPatch  {master_name};
            }}
        }}
    }}
"""
        
        return cmd

    def _read_stl_solid_names(self, stl_path: Path) -> List[str]:
        """Read actual solid region names from an STL file.
        
        This ensures consistency between the STL file and snappyHexMeshDict.
        """
        regions = []
        if stl_path.exists():
            with open(stl_path, "r", encoding="utf-8") as f:
                for line in f:
                    stripped = line.strip()
                    if stripped.startswith("solid ") and not stripped.startswith("solid facet"):
                        name = stripped[6:].strip()
                        if name and name not in regions:
                            regions.append(name)
        return regions
    
    def _collect_merged_rack_regions(self) -> List[str]:
        """Collect all solid region names from merged_rack.stl.
        
        This reads the actual solid names from the generated merged STL file,
        ensuring consistency between the STL and snappyHexMeshDict.
        """
        merged_rack_stl = self.stl_dir / "merged_rack.stl"
        regions = self._read_stl_solid_names(merged_rack_stl)
        if not regions:
            logger.warning("merged_rack.stl not found or empty, using entity regions")
            for entity in self.entities:
                if isinstance(entity, RackEntity):
                    for region in entity.get_solid_regions():
                        regions.append(region.name)
        return regions

    def _collect_all_solid_regions(self) -> List[str]:
        """Collect all solid region names from merged_geometry.stl.
        
        DEPRECATED: merged_geometry.stl is no longer used per requirement 10.
        This method is kept for backward compatibility.
        """
        regions = []
        merged_stl = self.stl_dir / "merged_geometry.stl"
        
        if merged_stl.exists():
            regions = self._read_stl_solid_names(merged_stl)
        else:
            # Fallback: use entity solid regions (may not match merged STL)
            logger.warning("merged_geometry.stl not found, using entity regions")
            for entity in self.entities:
                for region in entity.get_solid_regions():
                    regions.append(region.name)
        
        return regions

    def _collect_all_leakage_zones(self) -> List[LeakageZone]:
        """Collect all leakage zones from rack entities."""
        zones = []
        for entity in self.entities:
            if isinstance(entity, RackEntity):
                zones.extend(entity.get_leakage_zones())
        return zones

    def _build_server_height_map(self) -> dict:
        """Build a map of server inlet/outlet region names to server heights.
        
        Returns:
            Dict mapping region name (e.g., 'Rack_1_server_Rack_1_Server_1_inlet') 
            to server height in meters.
        """
        height_map = {}
        for entity in self.entities:
            if isinstance(entity, RackEntity):
                rack_uid = entity.uid
                slot_height = entity.slot_height
                for server in entity.servers:
                    server_uid = server.uid
                    server_height = server.slot_occupation * slot_height
                    
                    # Map both inlet and outlet region names to server height
                    inlet_name = f"{rack_uid}_server_{server_uid}_inlet"
                    outlet_name = f"{rack_uid}_server_{server_uid}_outlet"
                    height_map[inlet_name] = server_height
                    height_map[outlet_name] = server_height
        return height_map

    def _calculate_adaptive_level(
        self, feature_height: float, base_size: float, default_level: int,
        margin_factor: float = 0.5
    ) -> int:
        """Calculate the minimum refinement level to properly mesh a geometric feature.
        
        Ensures the mesh cell size is significantly smaller than the feature height,
        with a safety margin to avoid boundary cases.
        
        Args:
            feature_height: Height of the geometric feature (e.g., server or leakage zone height)
            base_size: Base mesh cell size
            default_level: Default refinement level to start from
            margin_factor: Safety margin factor (0.5 means mesh size should be < 50% of feature height)
        
        Returns:
            Adaptive refinement level that ensures cell_size < feature_height * margin_factor
        """
        level = default_level
        # Keep increasing level until cell size is significantly smaller than feature height
        max_level = 10  # Prevent infinite loop
        while level < max_level:
            cell_size = base_size / (2 ** level)
            if cell_size < feature_height * margin_factor:
                break
            level += 1
        return level

    def _infer_patch_type_from_name(self, region_name: str) -> str:
        """Infer patch type from region name.
        
        Returns 'wall' for most surfaces, 'patch' for inlet/outlet/supply/return.
        """
        name_lower = region_name.lower()
        if any(kw in name_lower for kw in ["inlet", "outlet", "supply", "return"]):
            return "patch"
        return "wall"

    def get_snappyHexMesh_refinementSurfaces_cmd(
        self, 
        use_merged: bool = True, 
        refinement_level: tuple = (1, 1),
        rack_refinement_level: tuple = None,
        server_face_refinement_level: tuple = None,
        acu_refinement_level: tuple = None,
        base_size: float = None,
    ) -> str:
        """Generate the refinementSurfaces section of snappyHexMeshDict.
        
        Per requirement 10:
        - merged_rack contains all RACK hollow STL files
        - Other equipment (ACU, Pillar, Box) kept as individual STL files
        
        Args:
            use_merged: If True, use merged_rack + individual equipment STL files.
            refinement_level: (min, max) refinement level for surfaces (default: (1, 1)).
            rack_refinement_level: (min, max) refinement level for rack walls.
            server_face_refinement_level: (min, max) refinement level for server inlet/outlet.
            acu_refinement_level: (min, max) refinement level for ACU surfaces.
            base_size: Base mesh cell size for adaptive refinement.
        """
        # Helper to ensure tuple format
        def ensure_tuple(val, default):
            if val is None:
                return default
            if isinstance(val, (list, tuple)) and len(val) == 2:
                return (int(val[0]), int(val[1]))
            if isinstance(val, int):
                return (val, val)
            return default
        
        refinement_level = ensure_tuple(refinement_level, (1, 1))
        
        # Set default values for type-specific refinement levels
        if rack_refinement_level is None:
            rack_refinement_level = refinement_level
        else:
            rack_refinement_level = ensure_tuple(rack_refinement_level, refinement_level)
        if server_face_refinement_level is None:
            server_face_refinement_level = refinement_level
        else:
            server_face_refinement_level = ensure_tuple(server_face_refinement_level, refinement_level)
        if acu_refinement_level is None:
            acu_refinement_level = refinement_level
        else:
            acu_refinement_level = ensure_tuple(acu_refinement_level, refinement_level)
        
        # Build server height map for adaptive refinement
        server_height_map = self._build_server_height_map() if base_size else {}
        
        cmd = ""

        if use_merged:
            # Step 1: Add merged_rack refinementSurface for all RACK entities
            # Read actual region names from the merged STL file
            rack_regions = self._collect_merged_rack_regions()
            rack_region_cmds = []
            
            for region_name in rack_regions:
                patch_type = self._infer_patch_type_from_name(region_name)
                name_lower = region_name.lower()
                
                if "inlet" in name_lower or "outlet" in name_lower:
                    level_min, level_max = server_face_refinement_level
                    # Adaptive refinement for narrow server faces
                    margin_factor = 0.5
                    if base_size and region_name in server_height_map:
                        server_height = server_height_map[region_name]
                        mesh_cell_size = base_size / (2 ** level_max)
                        if mesh_cell_size >= server_height * margin_factor:
                            adaptive_level = self._calculate_adaptive_level(
                                server_height, base_size, level_max, margin_factor
                            )
                            logger.info(
                                f"[Adaptive Mesh] Server '{region_name}' height={server_height:.4f}m, "
                                f"level: ({level_min}, {level_max}) -> ({level_min}, {adaptive_level})"
                            )
                            level_max = adaptive_level
                elif "rack" in name_lower and "server" not in name_lower:
                    level_min, level_max = rack_refinement_level
                else:
                    level_min, level_max = refinement_level
                
                rack_region_cmds.append(f"""
            {region_name}
            {{
                level ({level_min} {level_max});
                patchInfo
                {{
                    type {patch_type};
                }}
            }}""")
            
            if rack_region_cmds:
                cmd += f"""
        merged_rack
        {{
            level ({rack_refinement_level[0]} {rack_refinement_level[1]});
            regions
            {{{chr(10).join(rack_region_cmds)}
            }}
        }}
"""
            
            # Step 2: Add individual refinementSurfaces for non-RACK equipment
            for entity in self.entities:
                if isinstance(entity, RackEntity):
                    continue  # Skip - already in merged_rack
                
                safe_uid = entity.safe_uid
                regions = entity.get_solid_regions()
                region_cmds = []
                
                for region in regions:
                    region_name = region.name
                    patch_type = self._infer_patch_type_from_name(region_name)
                    name_lower = region_name.lower()
                    
                    if "acu" in name_lower or "supply" in name_lower or "return" in name_lower:
                        level_min, level_max = acu_refinement_level
                    else:
                        level_min, level_max = refinement_level
                    
                    region_cmds.append(f"""
            {region_name}
            {{
                level ({level_min} {level_max});
                patchInfo
                {{
                    type {patch_type};
                }}
            }}""")
                
                cmd += f"""
        {safe_uid}
        {{
            level ({refinement_level[0]} {refinement_level[1]});
            regions
            {{{chr(10).join(region_cmds)}
            }}
        }}
"""
            # Leakage cellZones are created via topoSet (boxToCell) after mesh generation
            # NOT using merged_leakage in refinementSurfaces to avoid creating patches
        else:
            # Original per-entity logic
            for entity in self.entities:
                regions = entity.get_solid_regions()
                safe_uid = entity.safe_uid

                region_cmds = []
                for region in regions:
                    patch_type = region.patch_type.value
                    level = region.refinement_level

                    if region.patch_type == PatchType.CYCLIC:
                        neighbour = region.neighbour_patch or ""
                        region_cmds.append(f"""
            {region.name}
            {{
                level ({level} {level});
                patchInfo
                {{
                    type cyclic;
                    neighbourPatch {neighbour};
                }}
            }}""")
                    else:
                        region_cmds.append(f"""
            {region.name}
            {{
                level ({level} {level});
                patchInfo
                {{
                    type {patch_type};
                }}
            }}""")

                cmd += f"""
        {safe_uid}
        {{
            level ({self.refinement_level} {self.refinement_level});
            regions
            {{{chr(10).join(region_cmds)}
            }}
        }}
"""

        return cmd

    def get_snappyHexMesh_refinementRegions_cmd(
        self, 
        refinement_level: tuple = (1, 1),
        base_size: float = None,
    ) -> str:
        """Generate the refinementRegions section of snappyHexMeshDict.
        
        This includes volume refinement for leakage zones to ensure
        uniform mesh density inside RACK hollow regions.
        
        Uses adaptive refinement (fallback algorithm):
        - Default level is refinement_level[0] (min)
        - For narrow gaps (e.g., 1U height), automatically increases level
          until mesh cell size < 50% of zone height
        
        Args:
            refinement_level: (min, max) refinement level tuple for leakage regions
                             min is used as default, adaptive algorithm may increase up to max or beyond
            base_size: Base mesh cell size for adaptive refinement calculation
        """
        # Parse refinement level
        if isinstance(refinement_level, (list, tuple)) and len(refinement_level) == 2:
            default_level = int(refinement_level[0])  # Use min as default
        elif isinstance(refinement_level, int):
            default_level = refinement_level
        else:
            default_level = 1
        
        cmd = ""
        
        # Get all leakage zone bounding boxes
        leakage_zones_config = self.get_leakage_zones_bbox_config()
        
        if not leakage_zones_config:
            return ""
        
        # Build leakage zone height map for adaptive refinement
        leakage_height_map = {}
        if base_size:
            for entity in self.entities:
                if isinstance(entity, RackEntity):
                    slot_height = entity.slot_height
                    for zone in entity.get_leakage_zones():
                        zone_height = zone.get_slot_height(slot_height)
                        leakage_height_map[zone.cellzone_name] = zone_height
        
        # Use margin_factor=0.5 to ensure mesh cell size < 50% of zone height
        margin_factor = 0.5
        
        for zone_name, zone_info in leakage_zones_config.items():
            level = default_level
            
            # Adaptive refinement (fallback algorithm):
            # If zone height is small relative to mesh cell size, increase level
            if base_size and zone_name in leakage_height_map:
                zone_height = leakage_height_map[zone_name]
                mesh_cell_size = base_size / (2 ** level)
                
                # Trigger adaptive if mesh_size >= 50% of zone_height
                if mesh_cell_size >= zone_height * margin_factor:
                    adaptive_level = self._calculate_adaptive_level(
                        zone_height, base_size, level, margin_factor
                    )
                    logger.info(
                        f"[Adaptive Mesh] Leakage '{zone_name}' height={zone_height:.4f}m, "
                        f"mesh_size={mesh_cell_size:.4f}m (>= {margin_factor*100:.0f}% of height), "
                        f"level: {level} -> {adaptive_level}"
                    )
                    level = adaptive_level

            # Quote zone_name if it starts with a digit (OpenFOAM requirement)
            safe_zone_name = f'"{zone_name}"' if zone_name[0].isdigit() else zone_name

            # Use searchableBox for volume refinement
            cmd += f"""
        {safe_zone_name}
        {{
            mode inside;
            levels ((1e15 {level}));
        }}"""

        return cmd
    
    def get_snappyHexMesh_geometry_leakage_boxes_cmd(self) -> str:
        """Generate searchableBox geometry entries for leakage zones.

        These are used for volume refinement in refinementRegions.
        """
        cmd = ""

        leakage_zones_config = self.get_leakage_zones_bbox_config()

        for zone_name, zone_info in leakage_zones_config.items():
            bbox_min = zone_info["bbox_min"]
            bbox_max = zone_info["bbox_max"]

            # Quote zone_name if it starts with a digit (OpenFOAM requirement)
            safe_zone_name = f'"{zone_name}"' if zone_name[0].isdigit() else zone_name

            cmd += f"""
    {safe_zone_name}
    {{
        type searchableBox;
        min ({bbox_min['x']} {bbox_min['y']} {bbox_min['z']});
        max ({bbox_max['x']} {bbox_max['y']} {bbox_max['z']});
    }}
"""

        return cmd

    def get_snappyHexMesh_features_cmd(self, use_merged: bool = True, feature_level: int = 2) -> str:
        """Generate the features section of snappyHexMeshDict.
        
        References eMesh files generated by surfaceFeatureExtract for
        preserving sharp edges (right angles) during mesh snapping.
        
        Per requirement 10:
        - merged_rack.eMesh for RACK hollow STL
        - Individual eMesh files for ACU, Pillar, Box, etc. (not merged)
        
        Args:
            use_merged: If True, reference merged rack eMesh + individual equipment eMesh.
            feature_level: Refinement level for feature edges (default: 2).
        """
        cmd = ""
        
        if use_merged:
            # Reference merged rack eMesh (RACK hollow with server inlet/outlet features)
            cmd += f"""
        {{
            file "merged_rack.eMesh";
            level {feature_level};
        }}"""
            # Individual equipment eMesh files (ACU, Pillar, Box, etc. - per requirement 10)
            for entity in self.entities:
                # Skip RackEntity - they are in merged_rack.stl
                if isinstance(entity, RackEntity):
                    continue
                eMesh_name = entity.stl_filename.replace('.stl', '.eMesh')
                cmd += f"""
        {{
            file "{eMesh_name}";
            level {feature_level};
        }}"""
        else:
            # Individual entity eMesh files for all entities
            for entity in self.entities:
                eMesh_name = entity.stl_filename.replace('.stl', '.eMesh')
                cmd += f"""
        {{
            file "{eMesh_name}";
            level {feature_level};
        }}"""
        
        return cmd

    def get_snappyHexMesh_locationsInside_cmd(self) -> str:
        """Generate the locationsInMesh entries for leakage cellZones.

        For each leakage zone, we specify a point inside the zone and
        associate it with a cellZone. This allows snappyHexMesh to
        automatically create cellZones for porous media regions.
        """
        lines = []

        leakage_zones = self._collect_all_leakage_zones()
        for zone in leakage_zones:
            # Get the bounding box for this zone
            for entity in self.entities:
                if isinstance(entity, RackEntity) and entity.uid == zone.rack_uid:
                    v_min, v_max = entity._get_leakage_zone_bbox(zone)
                    # Point at center of the zone
                    cx = (v_min.x + v_max.x) / 2
                    cy = (v_min.y + v_max.y) / 2
                    cz = (v_min.z + v_max.z) / 2
                    # Quote cellzone_name if it starts with a digit (OpenFOAM requirement)
                    zone_name = zone.cellzone_name
                    if zone_name and zone_name[0].isdigit():
                        safe_name = f'"{zone_name}"'
                    else:
                        safe_name = zone_name
                    lines.append(f"(({cx} {cy} {cz}) {safe_name})")
                    break

        # Join with newline and proper indentation (8 spaces to match template)
        return "\n        ".join(lines)

    def get_createBaffles_cmd(self) -> str:
        """Generate CreateBaffles commands for internal surfaces.

        This includes:
        - Room geometry baffles (raised floor, false ceiling, containments)
        
        Note: Rack air leakage is now handled via volumetric porous zones,
        not surface baffles.
        """
        cmd = ""

        # Room geometry baffles
        if self.room_geometry:
            cmd += self.room_geometry.get_all_createBaffle_cmd()

        # NOTE: Removed rack air leakage baffles - now using volumetric approach
        # with Darcy-Forchheimer source terms in fvOptions

        return cmd

    def get_leakage_fvoptions_config(self) -> Dict[str, Dict[str, Any]]:
        """Get fvOptions configuration for all leakage zones.
        
        Returns a dictionary mapping zone names to their Darcy-Forchheimer
        coefficients for use in fvOptions template.
        
        The coefficients are calculated using:
        - Darcy coefficient: d = 150 * (1-ε)² / (ε³ * dp²)
        - Forchheimer coefficient: f = 1.75 * (1-ε) / (ε³ * dp)
        
        where ε is the porosity (sigma) and dp is characteristic pore size.
        For perforated plates, we use a simplified model based on sigma.
        """
        import math
        
        config = {}
        
        for entity in self.entities:
            if isinstance(entity, RackEntity):
                zones = entity.get_leakage_zones()
                sigma = entity.air_leakage.sigma
                rack_depth = entity.size.y  # Flow length
                
                if sigma <= 0 or sigma >= 1:
                    sigma = 0.1  # Default 10%
                
                # For perforated plates, characteristic pore size dp ≈ sqrt(sigma) * typical_opening
                # Using simplified approach: assume dp ≈ 0.01m (1cm equivalent opening)
                dp = 0.01 * math.sqrt(sigma / 0.1)  # Scale with sigma
                
                # Darcy coefficient: d = 150 * (1-σ)² / (σ³ * dp²)
                d_coeff = 150.0 * ((1 - sigma) ** 2) / ((sigma ** 3) * (dp ** 2))
                
                # Forchheimer coefficient: f = 1.75 * (1-σ) / (σ³ * dp)
                f_coeff = 1.75 * (1 - sigma) / ((sigma ** 3) * dp)
                
                # Cap coefficients to reasonable values
                d_coeff = min(d_coeff, 1e8)  # Max Darcy coefficient
                f_coeff = min(f_coeff, 1e6)  # Max Forchheimer coefficient
                
                for zone in zones:
                    v_min, v_max = entity._get_leakage_zone_bbox(zone)
                    
                    # Determine flow direction based on rack orientation
                    # Default: flow in Y direction (front to back)
                    if entity.orientation in [0, 180]:
                        # Flow in Y direction
                        d_vec = f"({d_coeff} 0 {d_coeff})"  # High resistance in X,Z, low in Y
                        f_vec = f"({f_coeff} 0 {f_coeff})"
                        e1 = "(0 1 0)"  # Flow direction
                        e2 = "(1 0 0)"
                    else:
                        # Flow in X direction (rotated 90 degrees)
                        d_vec = f"(0 {d_coeff} {d_coeff})"
                        f_vec = f"(0 {f_coeff} {f_coeff})"
                        e1 = "(1 0 0)"
                        e2 = "(0 1 0)"
                    
                    config[zone.cellzone_name] = {
                        "cellzone_name": zone.cellzone_name,
                        "d": d_vec,
                        "f": f_vec,
                        "origin": f"({v_min.x} {v_min.y} {v_min.z})",
                        "e1": e1,
                        "e2": e2,
                        "sigma": sigma,
                    }
        
        return config

    def get_minimum_object_dimension(self) -> float:
        """
        Get the minimum dimension of all objects in the scene.
        
        This includes:
        - Entity bounding box dimensions (x, y, z)
        - Leakage zone dimensions
        - Baffle thicknesses (conceptually thin, but for safety margin checking)
        
        Returns:
            The smallest dimension found across all objects.
        """
        min_dim = float('inf')
        
        # Check entity dimensions
        for entity in self.entities:
            bbox = entity.bounding_box
            dims = [
                bbox.v_max.x - bbox.v_min.x,
                bbox.v_max.y - bbox.v_min.y,
                bbox.v_max.z - bbox.v_min.z,
            ]
            for dim in dims:
                if dim > 0 and dim < min_dim:
                    min_dim = dim
            
            # For RackEntity, also check leakage zones
            if isinstance(entity, RackEntity):
                for zone in entity.get_leakage_zones():
                    zone_height = zone.get_slot_height(entity.slot_height)
                    if zone_height > 0 and zone_height < min_dim:
                        min_dim = zone_height
                
                # Check server dimensions
                for server in entity.servers:
                    server_height = server.slot_occupation * entity.slot_height
                    if server_height > 0 and server_height < min_dim:
                        min_dim = server_height
        
        # Check room geometry if available
        if self.room_geometry:
            # Check raised floor height
            if hasattr(self.room_geometry, 'raised_floor') and self.room_geometry.raised_floor:
                rf_height = self.room_geometry.raised_floor.height
                if rf_height > 0 and rf_height < min_dim:
                    min_dim = rf_height
        
        return min_dim if min_dim != float('inf') else 0.1  # Default to 0.1m if nothing found

    def validate_and_adjust_base_size(self) -> None:
        """
        Validate that blockMesh cell size is appropriate for all objects.
        
        Rule: blockMesh cell size should be <= 80% of the minimum object dimension.
        If not, log a warning but DO NOT automatically adjust (user may want to use
        higher refinement levels for small objects like leakage zones instead).
        """
        min_dim = self.get_minimum_object_dimension()
        max_allowed_base_size = min_dim * 0.8
        
        if self.base_size > max_allowed_base_size:
            logger.warning(
                f"BlockMesh base_size ({self.base_size}m) is larger than 80% of minimum object dimension ({min_dim}m). "
                f"Consider using higher refinement levels (e.g., level 2) for small objects like leakage zones. "
                f"Minimum object dimension: {min_dim}m, recommended max base_size: {max_allowed_base_size:.4f}m"
            )
        else:
            logger.info(
                f"BlockMesh base_size ({self.base_size}m) is valid. "
                f"Minimum object dimension: {min_dim}m, max allowed base_size: {max_allowed_base_size:.4f}m"
            )

    def get_blockMesh_config(self) -> Dict[str, Any]:
        """Get blockMesh configuration.
        
        This method also validates and adjusts base_size if necessary to ensure
        the mesh can properly resolve all objects in the scene.
        """
        if not self.room_geometry:
            raise ValueError("Room geometry not configured")

        # Validate and adjust base_size before calculating cell counts
        self.validate_and_adjust_base_size()

        # Expand blockMesh domain by a fixed absolute margin (0.5 m) on all sides
        # so snappyHexMesh can properly snap to and cut room_wall geometry.
        # Using a fixed margin avoids the old 10% relative logic being constrained
        # by the project bounding box setting in the UI.
        MARGIN = 0.5
        v_min = self.room_geometry.v_min
        v_max = self.room_geometry.v_max
        v_min_exp = Vec3(v_min.x - MARGIN, v_min.y - MARGIN, v_min.z - MARGIN)
        v_max_exp = Vec3(v_max.x + MARGIN, v_max.y + MARGIN, v_max.z + MARGIN)

        return {
            "v_min": v_min_exp,
            "v_max": v_max_exp,
            "x_cells": int((v_max_exp.x - v_min_exp.x) / self.base_size),
            "y_cells": int((v_max_exp.y - v_min_exp.y) / self.base_size),
            "z_cells": int((v_max_exp.z - v_min_exp.z) / self.base_size),
        }

    def get_config_hash(self) -> str:
        """
        Compute a hash of the current configuration.

        This can be used to detect changes and avoid unnecessary regeneration.
        """
        if self._config is None:
            config_str = json.dumps(
                {
                    "hashVersion": GEOMETRY_HASH_VERSION,
                    "entities": [e.uid for e in self.entities],
                    "room": self.room_geometry.v_max.to_tuple() if self.room_geometry else None,
                },
                sort_keys=True,
            )
        else:
            config_str = json.dumps(self._geometry_hash_payload(), sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()

    def _geometry_hash_payload(self) -> Dict[str, Any]:
        """Build a geometry-only payload for caching."""
        config = self._config or {}
        models = config.get("models", {})
        payload: Dict[str, Any] = {
            "hashVersion": GEOMETRY_HASH_VERSION,
            "room": config.get("room", {}),
            "models": {"geometryModels": models.get("geometryModels", {})},
        }

        entities = config.get("entities", {})
        racks_out = {}
        for uid, rack in (entities.get("racks", {}) or {}).items():
            rack_copy = dict(rack)
            servers_out = []
            for server in rack_copy.get("servers", []) or []:
                server_copy = dict(server)
                server_copy.pop("power", None)
                server_copy.pop("volumeFlowRate", None)
                servers_out.append(server_copy)
            rack_copy["servers"] = servers_out
            racks_out[uid] = rack_copy

        acus_out = {}
        for uid, acu in (entities.get("acus", {}) or {}).items():
            acu_copy = dict(acu)
            acu_copy.pop("cooling", None)
            acus_out[uid] = acu_copy

        payload["entities"] = {
            "racks": racks_out,
            "acus": acus_out,
            "pillars": entities.get("pillars", {}) or {},
            "containments": entities.get("containments", {}) or {},
            "boxes": entities.get("boxes", {}) or {},
        }
        return payload

    def validate_all(self) -> List[str]:
        """
        Validate all entities.

        Returns:
            List of validation error messages (empty if all valid)
        """
        errors = []

        for entity in self.entities:
            try:
                entity.validate()
            except ValueError as e:
                errors.append(str(e))

        patches = self.get_all_patch_names()
        dupes = [name for name, count in Counter(patches).items() if count > 1]
        if dupes:
            dupes_sorted = ", ".join(sorted(dupes))
            errors.append(f"Duplicate patch names after sanitization: {dupes_sorted}")

        return errors

    def get_all_patch_names(self) -> List[str]:
        """Get a list of all patch names that will be created."""
        patches = []

        # Room wall patches
        if self.room_geometry:
            patches.extend(self.room_geometry.boundary.all_patch_names)

        # Entity patches
        for entity in self.entities:
            for region in entity.get_solid_regions():
                patches.append(region.name)

        return patches

    def get_cyclic_patch_pairs(self) -> List[tuple]:
        """Get cyclic patch pairs that should be converted to cyclicAMI.

        This includes:
        - STL-based cyclic patches from entity solid regions
        - Rack air leakage cyclic patches (from createBaffles)
        
        NOTE: Perforated baffle cyclic patches are NOT included here.
        They must remain as 'cyclic' type (not cyclicAMI) for porousBafflePressure BC.
        """
        pairs = []

        # STL-based cyclic patches
        for entity in self.entities:
            for region in entity.get_solid_regions():
                if region.patch_type == PatchType.CYCLIC and region.neighbour_patch:
                    pair = tuple(sorted([region.name, region.neighbour_patch]))
                    if pair not in pairs:
                        pairs.append(pair)

        # Rack air leakage cyclic patches (created by createBaffles)
        from .rack import RackEntity
        for entity in self.entities:
            if isinstance(entity, RackEntity):
                for pair in entity.get_cyclic_patch_pairs():
                    if pair not in pairs:
                        pairs.append(pair)

        # NOTE: Perforated baffles stay as cyclic, not cyclicAMI
        # They are handled separately by porousBafflePressure BC

        return pairs

    def get_perforated_baffle_cyclic_pairs(self) -> List[tuple]:
        """Get perforated baffle cyclic patch pairs (for BC generation).
        
        These patches remain as 'cyclic' type (not converted to cyclicAMI).
        """
        pairs = []
        if self.room_geometry:
            perforated_baffles = self.room_geometry.get_perforated_baffle_entities()
            for baffle in perforated_baffles:
                master_name = f"{baffle.safe_uid}_master"
                slave_name = f"{baffle.safe_uid}_slave"
                pairs.append((master_name, slave_name))
        return pairs
