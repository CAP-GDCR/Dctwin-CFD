# Quick Start

This guide walks you through running your first DCTwin CFD simulation using the `triangle_room` example case.

## Run the Full Pipeline

```bash
cd Dctwin-CFD
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun
```

This single command executes the entire pipeline:

1. **Reads** the `.cfdrun` manifest and discovers STL geometry in `Mesh/constant/triSurface/`
2. **Generates** `blockMeshDict`, `snappyHexMeshDict`, and other OpenFOAM dictionaries
3. **Runs** `blockMesh` → `snappyHexMesh` (parallel mesh generation)
4. **Sets up** boundary conditions (velocity, temperature, pressure, turbulence)
5. **Decomposes** the domain for parallel solving
6. **Runs** `buoyantBoussinesqSimpleFoam` (steady-state RANS solver)
7. **Reconstructs** the parallel results

## Run Stages Independently

### Mesh Only

```bash
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun --mesh-only
```

Generates the mesh and stops before solving. Useful for inspecting mesh quality in ParaView before committing to a long solver run.

### Solve Only

```bash
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun --solve-only
```

Requires an existing mesh (from a previous `--mesh-only` run). Sets up boundary conditions and runs the solver.

## View Results

After a successful run, results are stored under `case/triangle_room/log/<timestamp>/`. Open the case in ParaView:

```bash
paraview case/triangle_room/log/<latest_run>/case.foam
```

Key fields to visualize:

- **T** — Temperature distribution (K)
- **U** — Velocity vectors (m/s)
- **p_rgh** — Pressure field (Pa)

## What's in a Case Directory?

```
case/triangle_room/
├── model/
│   └── triangle_room.cfdrun    # Simulation manifest (JSON)
├── Mesh/
│   └── constant/
│       └── triSurface/         # Pre-exported STL geometry files
│           ├── merged_acu.stl
│           ├── room_wall.stl
│           └── ...
└── log/                        # Solver output (created after running)
    └── <timestamp>/
        ├── log.mpi_solver      # Solver log
        ├── case.foam           # ParaView loader
        └── processor*/         # Parallel decomposition
```
