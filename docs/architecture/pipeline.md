# Pipeline Architecture

## Overview

DCTwin automates the OpenFOAM CFD pipeline through a series of stages, orchestrated by the `STLGenRunner` class.

```
.cfdrun manifest + STL files
        │
        ▼
┌──────────────────────┐
│   STLGenRunner       │  Validates manifest, discovers STL geometry
│   (stlgen_runner.py) │
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   Mesh Generation    │  blockMesh → surfaceFeatureExtract →
│                      │  snappyHexMesh → topoSet → createBaffles
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   Field Generation   │  Auto boundary conditions for U, T, p_rgh,
│   (fields.py)        │  k, epsilon, nut, alphat
└──────────┬───────────┘
           │
           ▼
┌──────────────────────┐
│   OpenFOAM Solver    │  buoyantBoussinesqSimpleFoam (steady-state)
│                      │  or buoyantBoussinesqPimpleFoam (transient)
└──────────┬───────────┘
           │
           ▼
     CFD Results (T, U, p fields)
```

## Key Components

### STLGenRunner (`stlgen_runner.py`)

The main orchestrator. Reads the `.cfdrun` JSON manifest, sets up the OpenFOAM case directory, and coordinates all pipeline stages.

- Validates geometry (room height vs floor positions)
- Generates `blockMeshDict` with adaptive block sizing
- Generates `snappyHexMeshDict` with per-entity refinement levels
- Handles rack U-slot decomposition (fan-servers, blanking panels, leakage zones)
- Manages parallel mesh decomposition and reconstruction

### FieldGenerator (`fields.py`)

Generates OpenFOAM boundary condition files (`0/U`, `0/T`, `0/p_rgh`, `0/k`, `0/epsilon`, `0/nut`, `0/alphat`) for every patch in the mesh.

Boundary condition mapping:

| Patch Type | U | T | p_rgh |
|-----------|---|---|-------|
| Room wall | `noSlip` | `zeroGradient` | `fixedFluxPressure` |
| ACU supply | `flowRateInletVelocity` | `fixedValue` | `fixedFluxPressure` |
| ACU return | `flowRateOutletVelocity` | `zeroGradient` | `fixedValue 0` |
| Server inlet | `inletOutlet` | `inletOutlet` | `fixedFluxPressure` |
| Server outlet | `flowRateInletVelocity` | `fixedValue` (heat source) | `fixedFluxPressure` |

### OpenFOAM Templates (`third_parties/foam/template/`)

Jinja2 templates for all OpenFOAM dictionary files:

- `system/steady/controlDict.j2` — Solver control (endTime, writeInterval)
- `system/steady/fvSolution_cpu.j2` — Linear solver settings (PCG+GAMG for p_rgh, PBiCGStab+DILU for others)
- `system/share/fvOptions.j2` — Source terms (perforated openings, leakage zones, turbulence limiter)
- `constant/turbulenceProperties.j2` — Turbulence model selection (realizableKE)

### Schema Validation (`cfdproj_schema.py`)

Pydantic v2 models that validate the `.cfdrun` manifest structure before any simulation runs. Catches configuration errors early with clear error messages.

## Turbulence Modeling

DCTwin uses the **realizableKE** turbulence model (realizable k-epsilon) with:

- **Table-ramped relaxation** for k and epsilon: starts at 0.05, ramps to 0.3 over 50 iterations
- **Turbulent viscosity limiter** (`limitTurbulenceViscosity` in fvOptions): caps nut to prevent positive feedback loops in large data center cases
- **PCG + GAMG preconditioner** for the pressure equation with maxIter=200

## Parallel Execution

Both mesh generation and solving support MPI parallelism:

- Mesh: `mpirun -np N snappyHexMesh -parallel`
- Solve: `mpirun -np N buoyantBoussinesqSimpleFoam -parallel`

The number of processes is configured in the `.cfdrun` manifest (`meshConfig.meshProcess` and `simulationConfig.solveProcess`).
