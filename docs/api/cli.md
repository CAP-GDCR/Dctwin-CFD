# CLI Reference

## `python -m dctwin.runner.run_cfd`

The main entry point for running CFD simulations.

### Usage

```bash
python -m dctwin.runner.run_cfd --from-cfdrun <path_to_cfdrun> [--mesh-only] [--solve-only]
```

### Arguments

| Argument | Required | Description |
|----------|----------|-------------|
| `--from-cfdrun PATH` | Yes | Path to a `.cfdrun` manifest file. The runner reads this file and looks for STL geometry in `Mesh/constant/triSurface/` relative to the case directory. |
| `--mesh-only` | No | Generate mesh only (blockMesh + snappyHexMesh). Stops before setting up boundary conditions or running the solver. |
| `--solve-only` | No | Run the solver only. Requires an existing mesh from a previous `--mesh-only` run. |

### Examples

```bash
# Full pipeline
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun

# Mesh generation only
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun --mesh-only

# Solve only (requires existing mesh)
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun --solve-only
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Pipeline error (mesh failure, solver divergence, missing files) |

### Output

Results are written to `case/<name>/log/<timestamp>/`:

- `log.mpi_solver` — Full solver log (residuals, convergence, timing)
- `case.foam` — ParaView case file
- `processor*/` — Per-processor decomposed fields
- `<timestep>/` — Reconstructed field data at write intervals
