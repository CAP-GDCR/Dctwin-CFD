# Architecture

![DCTwin Architecture](../\_static/images/architecture.png)

## Overview

DCTwin automates the OpenFOAM CFD simulation pipeline for data center thermal analysis.
The system reads a JSON room layout definition and drives the following stages:

1. **Geometry Generation** — Converts room.json into multi-solid ASCII STL files
2. **Mesh Generation** — blockMesh → snappyHexMesh → topoSet → createBaffles
3. **Field Setup** — Generates boundary conditions (temperature, velocity, pressure, turbulence)
4. **Solver Execution** — Runs buoyantBoussinesqSimpleFoam (steady-state RANS k-ε)
5. **Post-processing** — PyVista-based visualization of temperature and velocity fields

## Core Modules

| Module | Path | Purpose |
|--------|------|---------|
| CaseRunner | `dctwin/runner/case.py` | Main orchestrator |
| MeshGenerator | `dctwin/runner/mesh.py` | OpenFOAM mesh pipeline |
| FieldGenerator | `dctwin/runner/fields.py` | Boundary condition setup |
| GeometryManager | `dctwin/third_parties/foam/geometry/manager.py` | STL generation |
| STLGenRunner | `dctwin/runner/stlgen_runner.py` | Direct STL pipeline |
| dclib | `dclib/` | Pydantic v2 data models for data center components |

## Data Flow

```
room.json + cfd_export.json
    │
    ▼
GeometryManager / STLGenRunner
    │ (multi-solid ASCII STL files)
    ▼
MeshGenerator
    │ (blockMesh → snappyHexMesh → topoSet → createBaffles)
    ▼
FieldGenerator
    │ (0/U, 0/T, 0/p, 0/k, 0/epsilon, fvOptions)
    ▼
buoyantBoussinesqSimpleFoam
    │
    ▼
CFD Results (temperature, velocity, pressure fields)
```
