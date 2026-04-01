# CFD Simulation Tutorial

This tutorial walks through running a data center CFD thermal simulation using DCTwin.

## Prerequisites

- Python 3.10+
- [OpenFOAM v2312+](https://www.openfoam.com/) installed and available on `PATH`
- DCTwin installed (`poetry install` or `pip install -e .`)

## Running the Base_coldAisleIso Example

This example simulates a simple data center room with 2 racks, 1 ACU, and cold aisle containment.

### Step 1: Navigate to the case directory

```bash
cd case/Base_coldAisleIso/
```

### Step 2: Generate STL geometry and mesh dictionaries

```bash
python -m dctwin.runner.run_cfd --from-stlgen model/cfd_export.json --stl-only
```

This reads the `cfd_export.json` layout definition and generates:
- Multi-solid ASCII STL files in `Mesh/constant/triSurface/`
- OpenFOAM mesh dictionaries (`blockMeshDict`, `snappyHexMeshDict`, etc.) in `Mesh/system/`

### Step 3: Generate the mesh

```bash
python -m dctwin.runner.run_cfd --from-stlgen model/cfd_export.json --mesh-only
```

This runs the full meshing pipeline:
1. `blockMesh` — creates the background Cartesian mesh
2. `surfaceFeatureExtract` — extracts edge features from STL files
3. `snappyHexMesh` — body-fitted mesh refinement
4. `topoSet` — creates cell/face zones for baffles and leakage
5. `createBaffles` — converts face zones to internal boundary patches

### Step 4: Run the solver

```bash
python -m dctwin.runner.run_cfd --from-stlgen model/cfd_export.json --solve-only
```

This runs `buoyantBoussinesqSimpleFoam` (steady-state RANS k-epsilon with buoyancy).

### Step 5: View results

Open the case in ParaView:

```bash
paraview case/Base_coldAisleIso/Mesh/case.foam
```

## Configuration

Each case directory contains:

- `config.toml` — simulation parameters (mesh size, solver settings, etc.)
- `model/room.json` — room geometry and equipment layout
- `model/cfd_export.json` — CFD export from the geometry preprocessor
- `model/bridge_bc.json` — boundary condition mapping
- `Mesh/constant/triSurface/` — input STL geometry files

See the [README](../../README.md) for more details on the JSON schema.
