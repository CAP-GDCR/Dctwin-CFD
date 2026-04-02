# JSON Schema Reference

DCTwin uses three JSON files to define a simulation case. This document describes the schema for each.

## File Overview

| File | Purpose | Size |
|------|---------|------|
| `cfd_export.json` | Complete model definition (geometry, physics, mesh config) | 30-100 KB |
| `room.json` | Room geometry metadata (room dimensions and structure) | 1-5 KB |
| `bridge_bc.json` | Boundary condition mapping (auto-generated from cfd_export.json) | 5-20 KB |

The primary file you edit is **`cfd_export.json`**. The other two are either metadata stubs (`room.json`) or auto-generated (`bridge_bc.json`).

---

## cfd_export.json

This is the main model definition file, exported from the geometry preprocessor. It defines all physical objects, their geometry, physics, and simulation parameters.

### Root Structure

```json
{
  "version": "1.0",
  "source": "STLGeneratorForOpenFoam",
  "projectName": "my_project",
  "room": { ... },
  "racks": { ... },
  "acus": { ... },
  "wallObjects": { ... },
  "heatSources": { ... },
  "baffles": { ... },
  "floors": { ... },
  "walls": { ... },
  "sensorPoints": [ ... ],
  "meshConfig": { ... },
  "simulationConfig": { ... }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `version` | string | Schema version (`"1.0"`) |
| `source` | string | Exporting tool identifier |
| `projectName` | string | Project name |
| `room` | object | Room dimensions and reference point |
| `racks` | dict | Server racks (keyed by rack ID) |
| `acus` | dict | Air conditioning units (keyed by ACU ID) |
| `wallObjects` | dict | Wall-mounted equipment with heat patches |
| `heatSources` | dict | Standalone heat-emitting objects |
| `baffles` | dict | Internal partitions/containment walls |
| `floors` | dict | Floor/ceiling surfaces (including raised floors) |
| `walls` | dict | Room boundary walls |
| `sensorPoints` | array | Virtual temperature sensor locations |
| `meshConfig` | object | Mesh generation parameters |
| `simulationConfig` | object | Solver parameters |

---

### Room

Defines the room boundary and mesh reference point.

```json
{
  "room": {
    "height": 3.8,
    "plane": [
      {"x": 0, "y": 0, "z": 0},
      {"x": 9.0, "y": 0, "z": 0},
      {"x": 9.0, "y": 8.0, "z": 0},
      {"x": 0, "y": 8.0, "z": 0}
    ],
    "locationInMesh": {"x": 4.5, "y": 4.0, "z": 1.8}
  }
}
```

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `height` | float | meters | Room height (floor to ceiling) |
| `plane` | Point3D[4] | meters | Four corner points of the room footprint at Z=0, counterclockwise |
| `locationInMesh` | Point3D | meters | A point inside the fluid domain (not inside any solid). Used by snappyHexMesh to identify the mesh region. |

!!! warning "locationInMesh"
    This point must be in open air, not inside any rack, ACU, wall, or other solid. If mesh generation fails, try moving this point.

---

### Racks

Server racks are the primary heat-generating equipment. Each rack contains server slots (U-slots) that can hold fan-servers or non-fan equipment.

```json
{
  "racks": {
    "Rack_A_01": {
      "stlFile": "merged_rack.stl",
      "leakageStlFile": null,
      "geometry": {
        "anchor": {"x": 2.0, "y": 3.0, "z": 0.0},
        "size": {"W": 0.6, "D": 1.0, "H": 2.0},
        "orientation": 0,
        "unitCount": 42,
        "unitHeight": 0.04445,
        "slotWidthRatio": 0.8
      },
      "servers": [ ... ],
      "leakage": { ... },
      "wallSolidNames": ["Rack_A_01_wall"]
    }
  }
}
```

#### Rack Geometry

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `anchor` | Point3D | (0,0,0) | Front-left-bottom corner position |
| `size` | {W, D, H} | 0.6 x 1.0 x 1.867 | Width, Depth, Height in meters |
| `orientation` | float | 0 | Rotation angle in degrees (0, 90, 180, 270) |
| `unitCount` | int | 42 | Total number of U-slots |
| `unitHeight` | float | 0.04445 | Height per U-slot (standard: 44.45mm = 1U) |
| `slotWidthRatio` | float | 0.8 | Fraction of rack width used for equipment |

#### Servers

Each server occupies one or more U-slots within a rack.

```json
{
  "servers": [
    {
      "id": "Rack_A_01_server_1",
      "type": "fanServer",
      "startSlot": 3,
      "spanSlots": 4,
      "inletSolidName": "Rack_A_01_server_1_inlet_patch",
      "outletSolidName": "Rack_A_01_server_1_outlet_patch",
      "physics": {
        "volumeFlowRate": 0.5,
        "power": 6000,
        "fixedTemperature": null,
        "fixedTempFace": null
      }
    }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Unique server identifier |
| `type` | `"fanServer"` or `"nonFan"` | Fan-servers draw air through; non-fan equipment is sealed |
| `startSlot` | int | Starting U-slot position (1-indexed from bottom) |
| `spanSlots` | int | Number of U-slots this server occupies |
| `inletSolidName` | string | STL solid name for the inlet face |
| `outletSolidName` | string | STL solid name for the outlet face |
| `topSolidName` | string (optional) | nonFan only: top plate solid name |
| `bottomSolidName` | string (optional) | nonFan only: bottom plate solid name |

**Server Physics (fanServer):**

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `volumeFlowRate` | float | m^3/s | Air volume flow rate through the server |
| `power` | float | W | Heat dissipation (electrical power) |
| `fixedTemperature` | float or null | K | Fixed outlet temperature (null = compute from power) |
| `fixedTempFace` | `"inlet"` or `"outlet"` or null | | Which face has fixed temperature |

#### Leakage

Models air leakage through unoccupied U-slots in a rack using Darcy-Forchheimer porous media.

```json
{
  "leakage": {
    "enabled": true,
    "sigma": 0.05,
    "throughSlots": [3, 4, 5, 6],
    "groups": [
      {
        "startUnit": 3,
        "endUnit": 6,
        "sigma": 0.05,
        "stlSolidName": "Rack_A_01_u3_u6_leakage_zone"
      }
    ]
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | false | Enable leakage for this rack |
| `sigma` | float | 0.05 | Global Darcy-Forchheimer resistance coefficient |
| `throughSlots` | int[] | [] | List of U-slot indices where leakage occurs |
| `groups` | LeakageGroup[] | [] | Per-group leakage definitions with custom sigma |

---

### ACUs (Air Conditioning Units)

ACUs supply cold air and return warm air. Each ACU has one or more supply and return faces.

```json
{
  "acus": {
    "ACU_01": {
      "stlFile": "merged_acu.stl",
      "geometry": {
        "anchor": {"x": 0, "y": 2.0, "z": 0},
        "size": {"W": 1.2, "D": 0.8, "H": 2.5},
        "orientation": 0
      },
      "supplyFaces": [
        {
          "solidName": "ACU_01_Front_patch",
          "patchId": "p_1",
          "face": "front",
          "physics": {
            "temperature": 15.0,
            "volumeFlowRate": 4.0,
            "velocity": 2.0
          }
        }
      ],
      "returnFaces": [
        {
          "solidName": "ACU_01_Back_patch",
          "patchId": "p_2",
          "face": "back",
          "physics": {
            "volumeFlowRate": 4.0,
            "velocity": 2.0
          }
        }
      ],
      "wallSolidNames": []
    }
  }
}
```

**ACU Face Physics:**

| Field | Type | Unit | Description |
|-------|------|------|-------------|
| `temperature` | float or null | Celsius | Supply air temperature (supply faces only) |
| `volumeFlowRate` | float | m^3/s | Air volume flow rate |
| `velocity` | float | m/s | Air velocity at the face |

---

### Baffles

Internal partitions that divide the room (e.g., cold aisle containment walls).

```json
{
  "baffles": {
    "Partition_1": {
      "stlFile": "merged_baffle.stl",
      "geometry": {
        "anchor": {"x": 1.5, "y": 0, "z": 0},
        "size": {"W": 6.0, "H": 3.8},
        "orientation": 0
      },
      "physics": {
        "baffleType": "wall",
        "porosity": 0.0
      }
    }
  }
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `baffleType` | string | `"wall"`, `"perforated"` | Solid wall or porous partition |
| `porosity` | float | 0.0 - 1.0 | 0 = solid, 1 = fully open |

---

### Floors

Floor and ceiling surfaces, including raised floors with perforated tile openings.

```json
{
  "floors": {
    "Raised_Floor": {
      "stlFile": "merged_floor.stl",
      "geometry": {
        "anchor": {"x": 0, "y": 0, "z": 0.6},
        "size": {"W": 10.0, "D": 8.0}
      },
      "defaultBC": "wall",
      "defaultBCPorosity": 0.0,
      "openings": [
        {
          "solidName": "Floor_tile_1_patch",
          "patchId": "p_10",
          "physics": {
            "type": "perforated",
            "porosity": 0.8
          }
        }
      ],
      "wallSolidNames": []
    }
  }
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `defaultBC` | string | `"wall"`, `"wallBaffle"`, `"perforated"` | Default boundary condition for the floor surface |
| `defaultBCPorosity` | float | 0.0 - 1.0 | Default porosity for porous floor regions |
| `openings` | array | | Specific tile regions with custom boundary conditions |

**Floor Opening Physics:**

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `type` | string | `"wall"`, `"wallBaffle"`, `"perforated"` | BC type for this opening |
| `porosity` | float | 0.0 - 1.0 | 0 = sealed, 0.8 = 80% open (typical perforated tile) |

---

### Wall Objects

Equipment mounted on walls with custom heat patches.

```json
{
  "wallObjects": {
    "Monitor_1": {
      "stlFile": "merged_wallobj.stl",
      "geometry": {
        "anchor": {"x": 0, "y": 5.0, "z": 1.5},
        "size": {"W": 0.5, "D": 0.1, "H": 0.4},
        "orientation": 0
      },
      "heatPatches": [
        {
          "solidName": "Monitor_1_heat_patch",
          "physics": {
            "heatPower": 200.0,
            "fixedTemperature": null
          }
        }
      ],
      "wallSolidNames": []
    }
  }
}
```

---

### Heat Sources

Standalone heat-emitting objects (not wall-mounted, not rack-servers).

```json
{
  "heatSources": {
    "UPS_1": {
      "stlFile": "merged_heat.stl",
      "geometry": {
        "anchor": {"x": 8.0, "y": 1.0, "z": 0},
        "size": {"W": 0.8, "D": 0.6, "H": 1.5},
        "orientation": 0
      },
      "heatMode": "power",
      "totalPower": 5000.0,
      "fixedTemperature": null,
      "wallSolidNames": []
    }
  }
}
```

| Field | Type | Values | Description |
|-------|------|--------|-------------|
| `heatMode` | string | `"power"`, `"temperature"` | How heat is specified |
| `totalPower` | float | Watts | Total heat output (when heatMode = "power") |
| `fixedTemperature` | float or null | Celsius | Fixed surface temperature (when heatMode = "temperature") |

---

### Sensor Points

Virtual temperature monitoring locations.

```json
{
  "sensorPoints": [
    {"id": "sensor_rack_a_inlet", "x": 2.0, "y": 3.0, "z": 2.0},
    {"id": "sensor_acu_return", "x": 0.5, "y": 5.0, "z": 2.5}
  ]
}
```

---

### Mesh Configuration

Controls OpenFOAM mesh generation (snappyHexMesh).

```json
{
  "meshConfig": {
    "baseSize": 0.2,
    "refinementLevel": [1, 1],
    "rackRefinementLevel": [2, 3],
    "serverFaceRefinementLevel": [2, 3],
    "baffleRefinementLevel": [1, 3],
    "parallelMesh": false,
    "meshProcess": 1
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `baseSize` | float | 0.2 | Background mesh cell size (meters). Smaller = finer mesh. |
| `refinementLevel` | [min, max] | [1, 1] | General geometry refinement |
| `rackRefinementLevel` | [min, max] | [2, 3] | Rack surface refinement |
| `serverFaceRefinementLevel` | [min, max] | [2, 3] | Server inlet/outlet face refinement (critical for accuracy) |
| `baffleRefinementLevel` | [min, max] | [1, 3] | Baffle partition refinement |
| `parallelMesh` | bool | true | Use MPI parallel mesh generation |
| `meshProcess` | int | 16 | Number of MPI processes for meshing |

!!! tip "Refinement levels"
    Refinement levels control how many times cells are subdivided near surfaces. Level 1 = halved once (8x cells), level 3 = halved 3 times (512x cells). Higher values on server faces improve thermal accuracy at inlet/outlet boundaries.

---

### Simulation Configuration

Controls the OpenFOAM solver (buoyantBoussinesqSimpleFoam).

```json
{
  "simulationConfig": {
    "endTime": 500,
    "writeInterval": 100,
    "solveProcess": 1,
    "solverType": "steady",
    "roomTemperature": 22.0,
    "residualControl": {
      "p_rgh": "1e-4",
      "U": "1e-5",
      "T": "1e-5",
      "k_epsilon": "1e-4"
    }
  }
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `endTime` | int | 500 | Max iterations (steady) or seconds (transient) |
| `writeInterval` | int | 100 | Write results every N iterations |
| `solveProcess` | int | 1 | MPI processes for solving |
| `solverType` | string | `"steady"` | `"steady"` or `"transient"` |
| `roomTemperature` | float | 22.0 | Initial room temperature (Celsius) |
| `residualControl` | object | see below | Convergence criteria per field |

**Residual Control:**

| Field | Default | Description |
|-------|---------|-------------|
| `p_rgh` | 1e-4 | Pressure residual threshold |
| `U` | 1e-5 | Velocity residual threshold |
| `T` | 1e-5 | Temperature residual threshold |
| `k_epsilon` | 1e-4 | Turbulence residual threshold |

---

## room.json

The room layout metadata file. In the current workflow, this serves as a geometry stub — the detailed model is in `cfd_export.json`.

```json
{
  "meta": {
    "name": "stlgen_stub",
    "version": "0.0.1"
  },
  "geometry": {
    "height": 3.8,
    "plane": [
      {"x": 0, "y": 0, "z": 0},
      {"x": 9.0, "y": 0, "z": 0},
      {"x": 9.0, "y": 8.0, "z": 0},
      {"x": 0, "y": 8.0, "z": 0}
    ]
  },
  "models": {
    "geometryModels": {"acus": {}, "racks": {}, "servers": {}, "boxes": {}},
    "coolingModels": {"acus": {}, "servers": {}},
    "powerModels": {"servers": {}}
  },
  "constructions": {
    "falseCeiling": {"geometry": {"height": 3.8, "openings": {}}},
    "boxes": {},
    "acus": {},
    "racks": {},
    "sensors": {}
  },
  "inputs": {
    "acus": {},
    "servers": {},
    "dehumidifiers": {},
    "cdus": {}
  }
}
```

---

## bridge_bc.json

Auto-generated boundary condition mapping. Each key is an STL solid/patch name, and the value specifies the thermal boundary condition.

```json
{
  "ACU_01_Front_patch": {
    "bc_type": "acu_supply",
    "temperature": 15.0,
    "flow_rate": 4.0,
    "power": null,
    "sigma": null,
    "heat_flux": null
  },
  "ACU_01_Back_patch": {
    "bc_type": "acu_return",
    "temperature": null,
    "flow_rate": 4.0,
    "power": null,
    "sigma": null,
    "heat_flux": null
  },
  "Rack_A_01_server_1_inlet_patch": {
    "bc_type": "fan_inlet",
    "temperature": null,
    "flow_rate": 0.5,
    "power": 6000,
    "sigma": null,
    "heat_flux": null
  }
}
```

**Boundary Condition Types:**

| bc_type | Description | Required Fields |
|---------|-------------|-----------------|
| `acu_supply` | ACU cold air supply face | temperature, flow_rate |
| `acu_return` | ACU warm air return face | flow_rate |
| `fan_inlet` | Server air inlet (fan-driven) | flow_rate, power |
| `fan_outlet` | Server air outlet (fan-driven) | flow_rate, power |
| `wall_baffle` | Solid internal partition | (none) |
| `perforated_baffle` | Porous partition or floor tile | sigma |

---

## Coordinate System

- **X, Y**: Horizontal plane (room floor)
- **Z**: Vertical axis (0 = floor, positive = up)
- **Units**: All dimensions in **meters**, temperatures in **Celsius** (except server fixedTemperature which uses Kelvin)
- **Orientation**: Rotation angle in degrees around Z-axis (0 = front-facing, 90 = right-facing, 180 = rear-facing, 270 = left-facing)
- **Room plane**: 4 corner points at Z=0, defining the room footprint counterclockwise

## Units Summary

| Quantity | Unit |
|----------|------|
| Length / Position | meters (m) |
| Temperature | Celsius (config, cfd_export) |
| Flow rate | m^3/s |
| Velocity | m/s |
| Power | Watts (W) |
| Heat flux | W/m^2 |
| Mesh size | meters |
| Porosity | 0.0 (solid) to 1.0 (open) |
| Darcy-Forchheimer sigma | dimensionless resistance coefficient |
