"""
CFD Project Importer: converts cfd_export.json from STLGeneratorForOpenFoam
into the native room.json format for the existing dctwin pipeline.

Also generates model/bridge_bc.json which maps STL solid names to boundary
condition specs for patch types that cannot be expressed in room.json (e.g.
multi-face ACUs, nonFan servers, generic heat patches).

Usage:
    importer = CFDProjectImporter(cfdproj_path, case_dir)
    room_json_path = importer.import_and_convert()
"""
import json
import shutil
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

from loguru import logger

from dctwin.runner.cfdproj_schema import CFDExport, CFDExportACU, RackServer
from dctwin.runner.bridge_bc import BridgeBCRegistry, BCSpec


def _sanitize(name: str) -> str:
    return re.sub(r'[^A-Za-z0-9_]', '_', name)


class CFDProjectImporter:
    """Converts cfd_export.json → room.json + bridge_bc.json and sets up case directory."""

    def __init__(self, cfdproj_path: Path, case_dir: Path):
        self.cfdproj_path = Path(cfdproj_path).resolve()
        self.cfdproj_dir = self.cfdproj_path.parent
        self.case_dir = Path(case_dir).resolve()
        self._registry = BridgeBCRegistry()

    def import_and_convert(self) -> Path:
        """Main entry: parse bridge JSON → generate room.json + bridge_bc.json → copy STLs."""
        logger.info(f"Importing CFD project from: {self.cfdproj_path}")
        data = self._load_and_validate()
        room_json = self._convert_to_room_json(data)

        # Create case directory structure
        model_dir = self.case_dir / "model"
        model_dir.mkdir(parents=True, exist_ok=True)

        # Copy the original bridge JSON for reference (skip if already in place)
        dst_json = model_dir / "cfd_export.json"
        if self.cfdproj_path.resolve() != dst_json.resolve():
            shutil.copy2(self.cfdproj_path, dst_json)

        # Copy STL files for wall objects (external_objects)
        self._copy_stl_files(data)

        # Write room.json
        room_json_path = model_dir / "room.json"
        with open(room_json_path, "w") as f:
            json.dump(room_json, f, indent=4)
        logger.info(f"Generated room.json at: {room_json_path}")

        # Build and save bridge BC registry
        self._build_bridge_bc_registry(data)
        bridge_bc_path = model_dir / "bridge_bc.json"
        self._registry.save(bridge_bc_path)
        logger.info(f"Generated bridge_bc.json at: {bridge_bc_path} ({len(self._registry)} entries)")

        # Write config.toml
        self._write_config_toml(data)

        return room_json_path

    def _load_and_validate(self) -> CFDExport:
        """Load and validate cfd_export.json."""
        with open(self.cfdproj_path) as f:
            raw = json.load(f)
        data = CFDExport(**raw)
        logger.info(f"Validated CFD export: {data.projectName} "
                     f"({len(data.racks)} racks, {len(data.acus)} ACUs, "
                     f"{len(data.baffles)} baffles, {len(data.floors)} floors, "
                     f"{len(data.wallObjects)} wall objects)")
        return data

    def _convert_to_room_json(self, data: CFDExport) -> Dict[str, Any]:
        """Convert bridge JSON to room.json format."""
        room: Dict[str, Any] = {}

        # Meta
        room["meta"] = {
            "name": data.projectName,
            "description": f"Imported from STLGeneratorForOpenFoam ({data.source})",
            "load_distribution_scheme": "SequentialLoad",
        }

        # Geometry — derive room plane/height from walls (CFD domain boundary),
        # not from the scene-level room.plane which is the full datacenter bbox.
        plane, height = self._compute_room_from_walls(data)
        room["geometry"] = {
            "height": height,
            "plane": plane,
        }

        # Models
        room["models"] = {
            "geometry_models": {
                "racks": self._convert_rack_geometry_models(data),
                "servers": self._convert_server_geometry_models(data),
                "acus": self._convert_acu_geometry_models(data),
                "boxes": self._convert_box_geometry_models(data),
            },
            "cooling_models": {
                "servers": self._convert_server_cooling_models(data),
                "acus": self._convert_acu_cooling_models(data),
            },
        }

        # Constructions
        room["constructions"] = {
            "racks": self._convert_rack_constructions(data),
            "acus": self._convert_acu_constructions(data),
            "boxes": self._convert_baffle_constructions(data),
            "raised_floor": self._convert_floor_constructions(data),
            "external_objects": self._convert_wall_objects(data),
        }

        # Inputs
        room["inputs"] = self._convert_inputs(data)

        return room

    # ========== Room geometry helpers ==========

    def _compute_room_from_walls(self, data: CFDExport):
        """Derive room plane and height from wall elements with stlFile=None.

        Walls with stlFile=None are the virtual room boundary panels exported
        by STLGeneratorForOpenFoam. Their bounding box gives the actual CFD
        domain, which is typically a sub-region of the full datacenter floor
        represented by data.room.plane.

        Falls back to data.room.plane/height if no such walls are present.
        """
        xs, ys, zs = [], [], []
        for wall in data.walls.values():
            if wall.stlFile is not None:
                continue
            a = wall.geometry.anchor
            W = wall.geometry.size.get("W", 0.0)
            H = wall.geometry.size.get("H", 0.0)
            xs.append(a.x)
            ys.append(a.y)
            zs.extend([a.z, a.z + H])
            # orientation=0 → panel extends in X; orientation=90 → extends in Y
            if abs(wall.geometry.orientation % 180) < 1.0:
                xs.append(a.x + W)
            else:
                ys.append(a.y + W)

        if not xs or not ys:
            logger.warning("No boundary walls found; using full scene room.plane")
            return (
                [{"x": p.x, "y": p.y, "z": p.z} for p in data.room.plane],
                data.room.height,
            )

        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        # Height: use walls that START at the floor (anchor.z ≈ 0) so we
        # don't accidentally include ceiling-level walls (Wall_5 etc.)
        floor_heights = []
        for wall in data.walls.values():
            if wall.stlFile is not None:
                continue
            if wall.geometry.anchor.z < 0.01:
                H = wall.geometry.size.get("H", 0.0)
                if H > 0:
                    floor_heights.append(H)
        height = max(floor_heights) if floor_heights else (max(zs) if zs else data.room.height)
        plane = [
            {"x": x_min, "y": y_min, "z": 0.0},
            {"x": x_max, "y": y_min, "z": 0.0},
            {"x": x_max, "y": y_max, "z": 0.0},
            {"x": x_min, "y": y_max, "z": 0.0},
        ]
        logger.info(
            f"Room derived from walls: X[{x_min:.2f},{x_max:.2f}] "
            f"Y[{y_min:.2f},{y_max:.2f}] H={height:.2f}"
        )
        return plane, height

    # ========== Geometry Models ==========

    def _convert_rack_geometry_models(self, data: CFDExport) -> Dict[str, Any]:
        """Create rack geometry model entries."""
        models = {}
        for name, rack in data.racks.items():
            model_name = f"RACK_{name}"
            geom = rack.geometry
            models[model_name] = {
                "size": {
                    "x": geom.size.get("W", 0.6),
                    "y": geom.size.get("D", 1.0),
                    "z": geom.size.get("H", 2.0),
                },
                "slot": geom.unitCount,
                "first_slot_offset": 0.0,
            }
        return models

    def _convert_server_geometry_models(self, data: CFDExport) -> Dict[str, Any]:
        """Create server geometry model entries from rack server data (fanServer only)."""
        models = {}
        for rack_name, rack in data.racks.items():
            for srv in rack.servers:
                if srv.type != "fanServer":
                    continue  # nonFan servers don't get flow-based geometry models
                model_name = f"SRV_{srv.id}"
                models[model_name] = {
                    "slot_occupation": srv.spanSlots,
                    "depth": rack.geometry.size.get("D", 1.0),
                    "width": rack.geometry.size.get("W", 0.6),
                }
        return models

    def _convert_acu_geometry_models(self, data: CFDExport) -> Dict[str, Any]:
        """Create ACU geometry model entries (uses first supply/return face for room.json compat)."""
        models = {}
        for name, acu in data.acus.items():
            model_name = f"ACU_{name}"
            geom = acu.geometry
            models[model_name] = {
                "size": {
                    "x": geom.size.get("W", 1.0),
                    "y": geom.size.get("D", 0.5),
                    "z": geom.size.get("H", 2.0),
                },
                "supply_face": {
                    "side": "bottom",
                    "width": geom.size.get("W", 1.0),
                    "length": geom.size.get("D", 0.5),
                    "offset": {"x": 0, "y": 0, "z": 0},
                },
                "return_face": {
                    "side": "top",
                    "width": geom.size.get("W", 1.0),
                    "length": geom.size.get("D", 0.5),
                    "offset": {"x": 0, "y": 0, "z": 0},
                },
            }
        return models

    def _convert_box_geometry_models(self, data: CFDExport) -> Dict[str, Any]:
        """Create box geometry models for baffles (containment/partition)."""
        models = {}
        models["containment"] = {
            "faces": {
                "top": True, "bottom": True,
                "front": True, "rear": True,
                "left": True, "right": True,
            }
        }
        return models

    # ========== Cooling Models ==========

    def _convert_server_cooling_models(self, data: CFDExport) -> Dict[str, Any]:
        """Create server cooling model entries (fanServer only — nonFan has no flow)."""
        models = {}
        for rack_name, rack in data.racks.items():
            for srv in rack.servers:
                if srv.type != "fanServer":
                    continue  # nonFan servers have no fan-driven cooling model
                model_name = f"COOL_{srv.id}"
                flow = srv.physics.volumeFlowRate or 0.5
                models[model_name] = {
                    "fan_type": "Fixed",
                    "volume_flow_rate": flow,
                    "volume_flow_rate_ratio": 0.00005,
                    "liquid_cooling": False,
                    "liquid_cooling_percentage": 1.0,
                    "pipe_diameter": 0.01,
                    "operating": {
                        "air_volume_flow_rate": flow,
                        "inlet_air_temperature": 25.0,
                        "outlet_air_temperature": 35.0,
                        "inlet_liquid_temperature": 25.0,
                        "outlet_liquid_temperature": 40.0,
                        "liquid_mass_flow_rate": 0.005,
                    },
                }
        return models

    def _convert_acu_cooling_models(self, data: CFDExport) -> Dict[str, Any]:
        """Create ACU cooling model entries."""
        models = {}
        for name, acu in data.acus.items():
            model_name = f"ACUCOOL_{name}"
            models[model_name] = {
                "cooling_type": "CW",
                "cooling_capacity": 85.5,
                "pressure_rise": 500.0,
                "maximum_flow_rate": "autosize",
            }
        return models

    # ========== Constructions ==========

    def _convert_rack_constructions(self, data: CFDExport) -> Dict[str, Any]:
        """Convert racks → constructions.racks entries."""
        constructions = {}
        for name, rack in data.racks.items():
            geom = rack.geometry
            rack_model = f"RACK_{name}"

            servers = {}
            for srv in rack.servers:
                srv_uid = srv.id
                rack_w = geom.size.get("W", 0.6)
                rack_d = geom.size.get("D", 1.0)
                slot_height = geom.unitHeight

                if srv.type == "fanServer":
                    flow = srv.physics.volumeFlowRate or 0.5
                    srv_entry = {
                        "uid": srv_uid,
                        "valid_id": srv_uid,
                        "geometry": {
                            "slot_occupation": srv.spanSlots,
                            "depth": rack_d,
                            "width": rack_w,
                            "inlet": {
                                "heightRatio": 0.8,
                                "width": rack_w,
                                "offsetX": 0.02,
                                "offsetZ": 0.0,
                            },
                            "outlet": {
                                "heightRatio": 0.8,
                                "width": rack_w,
                                "offsetX": 0.02,
                                "offsetZ": 0.0,
                            },
                            "model": f"SRV_{srv_uid}",
                            "slot_position": srv.startSlot,
                            "orientation": geom.orientation,
                            "slot_height": slot_height,
                        },
                        "cooling": {
                            "fan_type": "Fixed",
                            "volume_flow_rate": flow,
                            "volume_flow_rate_ratio": 0.00005,
                            "liquid_cooling": False,
                            "liquid_cooling_percentage": 1.0,
                            "pipe_diameter": 0.01,
                            "operating": {
                                "air_volume_flow_rate": flow,
                                "inlet_air_temperature": 25.0,
                                "outlet_air_temperature": 35.0,
                                "inlet_liquid_temperature": 25.0,
                                "outlet_liquid_temperature": 40.0,
                                "liquid_mass_flow_rate": 0.005,
                            },
                            "model": f"COOL_{srv_uid}",
                        },
                        "power": {
                            "input_power": srv.physics.power or 1000.0,
                        },
                        "meta": {},
                    }
                else:
                    # nonFan: no cooling model, no flow — BC handled via bridge registry
                    srv_entry = {
                        "uid": srv_uid,
                        "valid_id": srv_uid,
                        "geometry": {
                            "slot_occupation": srv.spanSlots,
                            "depth": rack_d,
                            "width": rack_w,
                            "inlet": {
                                "heightRatio": 0.8,
                                "width": rack_w,
                                "offsetX": 0.02,
                                "offsetZ": 0.0,
                            },
                            "outlet": {
                                "heightRatio": 0.8,
                                "width": rack_w,
                                "offsetX": 0.02,
                                "offsetZ": 0.0,
                            },
                            "model": f"SRV_{srv_uid}",
                            "slot_position": srv.startSlot,
                            "orientation": geom.orientation,
                            "slot_height": slot_height,
                        },
                        # No cooling for nonFan
                        "power": {"input_power": 0.0},
                        "meta": {},
                    }

                servers[srv_uid] = srv_entry

            constructions[name] = {
                "uid": name,
                "valid_id": name,
                "geometry": {
                    "size": {
                        "x": geom.size.get("W", 0.6),
                        "y": geom.size.get("D", 1.0),
                        "z": geom.size.get("H", 2.0),
                    },
                    "slot": geom.unitCount,
                    "first_slot_offset": 0.0,
                    "faces": None,
                    "model": rack_model,
                    "location": {
                        "x": geom.anchor.x,
                        "y": geom.anchor.y,
                        "z": geom.anchor.z,
                    },
                    "orientation": geom.orientation,
                    "hasBlankingPanel": True,
                },
                "airLeakage": {
                    "enabled": rack.leakage.enabled,
                    # Use first group sigma if available (v1.1+), else global sigma (v1.0 compat)
                    "sigma": rack.leakage.groups[0].sigma if rack.leakage.groups else rack.leakage.sigma,
                    "groups": [
                        {"startUnit": g.startUnit, "endUnit": g.endUnit, "sigma": g.sigma, "stlSolidName": g.stlSolidName}
                        for g in rack.leakage.groups
                    ],
                },
                "constructions": {
                    "servers": servers,
                },
                "meta": {},
            }
        return constructions

    def _convert_acu_constructions(self, data: CFDExport) -> Dict[str, Any]:
        """Convert ACUs → constructions.acus entries.

        room.json only supports one supply/return face per ACU.
        Multi-face ACUs use the bridge registry for the full face list.
        """
        constructions = {}
        for name, acu in data.acus.items():
            geom = acu.geometry
            model_name = f"ACU_{name}"
            cool_model = f"ACUCOOL_{name}"

            # Use primary (first) supply face for room.json compat
            supply_temp = acu.get_primary_supply_temperature()
            supply_flow = acu.get_primary_supply_flow_rate()

            constructions[name] = {
                "uid": name,
                "valid_id": name,
                "geometry": {
                    "model": model_name,
                    "location": {
                        "x": geom.anchor.x,
                        "y": geom.anchor.y,
                        "z": geom.anchor.z,
                    },
                    "orientation": geom.orientation,
                },
                "cooling": {
                    "model": cool_model,
                    "operating": {
                        "supply_air_temperature": supply_temp,
                        "supply_air_volume_flow_rate": supply_flow,
                    },
                },
                "meta": {},
            }
        return constructions

    def _convert_baffle_constructions(self, data: CFDExport) -> Dict[str, Any]:
        """Convert baffles → constructions.boxes entries.

        Detects ceiling panels (anchor.z + H > room height) vs vertical walls.
        Ceiling panels are horizontal (top face only), vertical walls use the
        front face only (size.y = 0, so only the XZ plane face is non-degenerate).
        """
        _, room_height = self._compute_room_from_walls(data)

        constructions = {}
        for name, baffle in data.baffles.items():
            geom = baffle.geometry
            W = geom.size.get("W", 1.0)
            H = geom.size.get("H", 1.0)
            anchor_z = geom.anchor.z

            # A baffle is a ceiling panel when its top edge (anchor.z + H)
            # would exceed the room ceiling — meaning H is the Y-depth of the
            # cold aisle, not the Z-height of a vertical wall.
            is_ceiling = anchor_z > 0.5 and (anchor_z + H) > room_height

            if is_ceiling:
                # Horizontal ceiling panel at Z = anchor.z
                # Y spans from anchor.y - H to anchor.y
                constructions[name] = {
                    "uid": name,
                    "geometry": {
                        "faces": {
                            "top": True, "bottom": False,
                            "front": False, "rear": False,
                            "left": False, "right": False,
                        },
                        "model": "containment",
                        "baffle_type": baffle.physics.baffleType,
                        "porosity": baffle.physics.porosity,
                        "location": {
                            "x": geom.anchor.x,
                            "y": geom.anchor.y - H,
                            "z": anchor_z,
                        },
                        "size": {
                            "x": W,
                            "y": H,
                            "z": 0.0,
                        },
                        "orientation": geom.orientation,
                    },
                    "meta": {},
                }
            else:
                # Vertical wall panel — front face at Y = anchor.y, XZ plane
                constructions[name] = {
                    "uid": name,
                    "geometry": {
                        "faces": {
                            "top": False, "bottom": False,
                            "front": True, "rear": False,
                            "left": False, "right": False,
                        },
                        "model": "containment",
                        "baffle_type": baffle.physics.baffleType,
                        "porosity": baffle.physics.porosity,
                        "location": {
                            "x": geom.anchor.x,
                            "y": geom.anchor.y,
                            "z": geom.anchor.z,
                        },
                        "size": {
                            "x": W,
                            "y": 0.0,
                            "z": H,
                        },
                        "orientation": geom.orientation,
                    },
                    "meta": {},
                }
        return constructions

    @staticmethod
    def _map_floor_bc_type(ui_type: str) -> tuple:
        """Map UI floor BC type to geometry layer baffle_type and porosity.

        UI types:
          "wall"       → solid external wall   → geometry "wall", porosity 0
          "wallBaffle" → internal wall baffle   → geometry "wall", porosity 0
          "perforated" → porous baffle          → geometry "perforated"
        """
        if ui_type == "perforated":
            return ("perforated", None)  # porosity from opening/floor config
        # Both "wall" and "wallBaffle" → solid internal wall baffle
        return ("wall", 0.0)

    def _convert_floor_constructions(self, data: CFDExport) -> Optional[Dict[str, Any]]:
        """Convert floors → constructions.raised_floor."""
        if not data.floors:
            return None

        # Use the first floor as raised floor
        for name, floor in data.floors.items():
            geom = floor.geometry
            floor_height = geom.anchor.z if geom.anchor.z > 0 else 0.2

            # Map defaultBC from UI type to geometry baffle_type
            panel_bt, panel_por = self._map_floor_bc_type(floor.defaultBC)
            if panel_por is None:
                panel_por = floor.defaultBCPorosity

            openings = {}
            for i, opening in enumerate(floor.openings):
                # Create opening entries for all non-default types
                op_type = opening.physics.type
                if op_type == "wall":
                    # "wall" means same as panel default — no separate opening needed
                    continue
                opening_name = f"opening_{name}_{i+1}"
                op_bt, op_por = self._map_floor_bc_type(op_type)
                if op_por is None:
                    op_por = opening.physics.porosity
                openings[opening_name] = {
                    "location": {
                        "x": geom.anchor.x,
                        "y": geom.anchor.y,
                        "z": floor_height,
                    },
                    "size": {
                        "x": geom.size.get("W", 1.0),
                        "y": geom.size.get("D", 1.0),
                        "z": 0.0,
                    },
                    "baffle_type": op_bt,
                    "porosity": op_por,
                }

            return {
                "uid": "",
                "geometry": {
                    "height": floor_height,
                    "baffle_type": panel_bt,
                    "porosity": panel_por,
                    "openings": openings,
                },
                "meta": {},
            }

        return None

    def _convert_wall_objects(self, data: CFDExport) -> Dict[str, Any]:
        """Convert wallObjects + heatSources → constructions.external_objects."""
        external = {}
        for name, obj in data.wallObjects.items():
            stl_path = f"../Mesh/constant/triSurface/{name}.stl"
            bcs = {}
            for hp in obj.heatPatches:
                temp = self._resolve_wall_object_patch_temperature(data, obj, hp)
                if temp is not None:
                    bcs[hp.solidName] = {
                        "type": "fixedTemperature",
                        "temperature": temp,
                    }
            external[name] = {
                "stl_file": stl_path,
                "location": {
                    "x": obj.geometry.anchor.x,
                    "y": obj.geometry.anchor.y,
                    "z": obj.geometry.anchor.z,
                },
                "orientation": obj.geometry.orientation,
                "boundary_conditions": bcs if bcs else {
                    "default": {"type": "wall"},
                },
            }
        # Heat sources: register as external objects (BCs handled by bridge_bc.json)
        for name, hs in data.heatSources.items():
            stl_path = f"../Mesh/constant/triSurface/{name}.stl"
            external[name] = {
                "stl_file": stl_path,
                "location": {
                    "x": hs.geometry.anchor.x,
                    "y": hs.geometry.anchor.y,
                    "z": hs.geometry.anchor.z,
                },
                "orientation": hs.geometry.orientation,
                "boundary_conditions": {"default": {"type": "wall"}},
            }
        return external

    def _resolve_wall_object_patch_temperature(self, data: CFDExport, obj, hp) -> Optional[float]:
        """Resolve a wall-object patch temperature from explicit temp or heat power."""
        if hp.physics.fixedTemperature is not None:
            return hp.physics.fixedTemperature

        power = hp.physics.heatPower or 0.0
        if power <= 0:
            return None

        size = obj.geometry.size or {}
        w = float(size.get("W", 0.0) or 0.0)
        d = float(size.get("D", 0.0) or 0.0)
        h = float(size.get("H", 0.0) or 0.0)
        area = 2.0 * (w * d + w * h + d * h)
        if area <= 1e-8:
            return None

        ambient = (
            data.simulationConfig.roomTemperature
            if getattr(data, "simulationConfig", None) and data.simulationConfig.roomTemperature is not None
            else 22.0
        )
        return ambient + power / (10.0 * area)

    # ========== Inputs ==========

    def _convert_inputs(self, data: CFDExport) -> Dict[str, Any]:
        """Convert physics to inputs section."""
        inputs: Dict[str, Any] = {"acus": {}, "servers": {}}

        # ACU inputs — use primary supply face for room.json
        for name, acu in data.acus.items():
            inputs["acus"][name] = {
                "supply_air_temperature": acu.get_primary_supply_temperature(),
                "supply_air_volume_flow_rate": acu.get_primary_supply_flow_rate(),
                "acu_switch": True,
                "valid_id": name,
            }

        # Server inputs — fanServer only; nonFan has no flow-based input
        for rack_name, rack in data.racks.items():
            for srv in rack.servers:
                if srv.type != "fanServer":
                    continue
                inputs["servers"][srv.id] = {
                    "input_power": srv.physics.power or 1000.0,
                    "input_power_percentage": None,
                    "air_volume_flow_rate": None,
                    "server_switch": True,
                    "valid_id": srv.id,
                }

        return inputs

    # ========== Bridge BC Registry ==========

    def _build_bridge_bc_registry(self, data: CFDExport) -> None:
        """Populate the bridge BC registry for all patch types not expressible in room.json."""
        self._registry = BridgeBCRegistry()
        self._register_acu_patches(data)
        self._register_nonfan_patches(data)
        self._register_wall_object_patches(data)
        self._register_heat_source_patches(data)
        self._register_leakage_zones(data)

    def _register_acu_patches(self, data: CFDExport) -> None:
        """Register supply and return face BCs for all ACU faces."""
        for name, acu in data.acus.items():
            for sf in acu.get_supply_faces():
                solid = _sanitize(f"{name}_{sf.solidName}")
                self._registry.register(solid, BCSpec(
                    bc_type="acu_supply",
                    temperature=sf.physics.temperature if sf.physics.temperature is not None else 15.0,
                    flow_rate=sf.physics.volumeFlowRate,
                ))
            for rf in acu.get_return_faces():
                solid = _sanitize(f"{name}_{rf.solidName}")
                self._registry.register(solid, BCSpec(
                    bc_type="acu_return",
                    flow_rate=rf.physics.volumeFlowRate,
                ))

    def _register_nonfan_patches(self, data: CFDExport) -> None:
        """Register fixed-temperature BCs for all 4 exposed faces of nonFan servers.

        NonFan equipment is modeled as a watertight solid body inside the rack.
        All four exposed faces (front/back/top/bottom) receive the same fixedTemperature BC,
        simulating a heat-generating component that conductively heats adjacent air cavities.
        """
        for rack_name, rack in data.racks.items():
            for srv in rack.servers:
                if srv.type != "nonFan":
                    continue
                temp = srv.physics.fixedTemperature if srv.physics.fixedTemperature is not None else 35.0
                spec = BCSpec(bc_type="nonfan_fixed_temp", temperature=temp)
                for solid_name in [srv.inletSolidName, srv.outletSolidName,
                                   srv.topSolidName, srv.bottomSolidName]:
                    if solid_name:
                        self._registry.register(solid_name, spec)

    def _register_leakage_zones(self, data: CFDExport) -> None:
        """Register porousZone cellZone entry for each leakage group STL solid (v1.1+ groups)."""
        for rack_name, rack in data.racks.items():
            for group in rack.leakage.groups:
                if group.stlSolidName:
                    self._registry.register(group.stlSolidName, BCSpec(
                        bc_type="leakage_zone",
                        sigma=group.sigma,
                    ))

    def _register_wall_object_patches(self, data: CFDExport) -> None:
        """Register fixedTemperature BCs for wall object heat patches."""
        for name, obj in data.wallObjects.items():
            for hp in obj.heatPatches:
                temp = self._resolve_wall_object_patch_temperature(data, obj, hp)
                if temp is not None:
                    solid = _sanitize(f"{name}_{hp.solidName}")
                    self._registry.register(solid, BCSpec(
                        bc_type="fixedTemperature",
                        temperature=temp,
                    ))

    def _register_heat_source_patches(self, data: CFDExport) -> None:
        """Register BCs for heat source objects (whole-body heat).

        heatMode == "power":  compute q = P / A_body → heat_source_flux BC
        heatMode == "temperature":  direct fixedTemperature BC
        """
        for name, hs in data.heatSources.items():
            if hs.heatMode == "temperature" and hs.fixedTemperature is not None:
                spec = BCSpec(bc_type="fixedTemperature", temperature=hs.fixedTemperature)
            elif hs.heatMode == "power" and hs.totalPower > 0:
                size = hs.geometry.size
                w = float(size.get("W", 0) or 0)
                d = float(size.get("D", 0) or 0)
                h = float(size.get("H", 0) or 0)
                area = 2.0 * (w * d + w * h + d * h)
                if area > 1e-8:
                    q = hs.totalPower / area
                else:
                    q = 0.0
                spec = BCSpec(bc_type="heat_source_flux", heat_flux=q)
            else:
                continue
            for solid_name in hs.wallSolidNames:
                sanitized = _sanitize(solid_name)
                self._registry.register(sanitized, spec)

    # ========== File Operations ==========

    def _copy_stl_files(self, data: CFDExport) -> None:
        """Copy wall object and heat source STL files to Mesh/constant/triSurface/."""
        tri_surface = self.case_dir / "Mesh" / "constant" / "triSurface"
        tri_surface.mkdir(parents=True, exist_ok=True)

        for name, obj in data.wallObjects.items():
            src = self.cfdproj_dir / obj.stlFile
            if src.exists():
                dst = tri_surface / f"{name}.stl"
                shutil.copy2(src, dst)
                logger.info(f"Copied STL: {src.name} → {dst}")
            else:
                logger.warning(f"STL file not found: {src}")

        for name, hs in data.heatSources.items():
            src = self.cfdproj_dir / hs.stlFile
            if src.exists():
                dst = tri_surface / f"{name}.stl"
                shutil.copy2(src, dst)
                logger.info(f"Copied heat source STL: {src.name} → {dst}")
            else:
                logger.warning(f"Heat source STL file not found: {src}")

    def _write_config_toml(self, data: CFDExport) -> None:
        """Generate config.toml for the case."""
        mc = data.meshConfig
        sc = data.simulationConfig
        loc = data.room.locationInMesh

        config_content = f"""# Auto-generated config.toml from STLGeneratorForOpenFoam CFD export
[paths]
room_json = "model/room.json"
dataset_name = "{data.projectName}"
log_root = "./log"

[geometry]
generate_stl = true
use_stl_geometry = true
force_stl_generation = true
mesh_root = "Mesh"

[mesh]
generate_mesh = true
parallel_mesh = {str(mc.parallelMesh).lower()}
base_size = {mc.baseSize}
refinement_level = {mc.refinementLevel}
rack_refinement_level = {mc.rackRefinementLevel}
server_face_refinement_level = {mc.serverFaceRefinementLevel}
wall_baffles_refinement_level = {mc.baffleRefinementLevel}
location_in_mesh = [{loc.x}, {loc.y}, {loc.z}]

[simulation]
simulation_type = "{sc.solverType}"
end_time = {sc.endTime}
write_interval = {sc.writeInterval}
room_temperature = {sc.roomTemperature}

[process]
mesh_process = {mc.meshProcess}
solve_process = {sc.solveProcess}

[output]
write_fields = "all"
convert_vtk = true
"""
        config_path = self.case_dir / "config.toml"
        with open(config_path, "w") as f:
            f.write(config_content)
        logger.info(f"Generated config.toml at: {config_path}")
