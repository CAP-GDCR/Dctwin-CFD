# Case Usage Guide

This guide explains how to use the included demo cases and create your own.

## Demo Cases Overview

DCTwin ships with 6 ready-to-run demo cases under `case/`:

| Case | Description | Room Size | Racks | ACUs | Key Feature |
|------|-------------|-----------|-------|------|-------------|
| `Base_coldAisleIso` | Cold aisle containment | 9m x 8m x 3.8m | 14 | 4 | Baffle partitions for aisle isolation |
| `Base_leakage` | Air leakage modeling | 4m x 6m x 2.6m | 4 | 2 | Porous media leakage through empty U-slots |
| `Alibaba-hongchou` | Large-scale facility | 41m x 21m x 7m | Many | Multiple | Real facility layout at scale |
| `GTC` | Multi-zone data center | 31m x 19m x 24m | Many | Multiple | Large room with raised floor |
| `STDCT` | Standard data center | 18m x 8m x 3.8m | Multiple | Multiple | Typical medium facility |
| `Slab` | Raised floor with tiles | 10m x 8m x 3.4m | Multiple | 2 | Perforated raised floor openings |

## Running a Demo Case

### Full Pipeline (STL + Mesh + Solve)

```bash
cd case/Base_coldAisleIso/
python run_cfd.py
```

Or equivalently:

```bash
python -m dctwin.runner.run_cfd --config config.toml
```

### Stage by Stage

```bash
# Step 1: Generate STL geometry files
python run_cfd.py --stl-only

# Step 2: Generate mesh (blockMesh + snappyHexMesh)
python run_cfd.py --mesh-only

# Step 3: Run the solver
python run_cfd.py --solve-only
```

### Using a Custom Config

```bash
python run_cfd.py --config my_config.toml
```

## Case Directory Structure

Every case follows this structure:

```
case/MyCaseDir/
├── config.toml                  # Simulation parameters
├── run_cfd.py                   # Entry script
├── model/
│   ├── room.json                # Room geometry definition
│   ├── cfd_export.json          # Complete model exported from preprocessor
│   ├── bridge_bc.json           # Boundary condition mapping
│   └── geometry/                # (optional) Extra geometry files
└── Mesh/                        # Generated output (created by pipeline)
    ├── constant/
    │   ├── triSurface/          # Input STL files
    │   └── polyMesh/            # Generated mesh
    ├── system/                  # OpenFOAM solver dictionaries
    └── 0/                       # Initial conditions
```

## Configuration File (config.toml)

The `config.toml` file controls all pipeline parameters:

```toml
[paths]
room_json = "model/room.json"       # Path to room layout definition
dataset_name = "default"            # Dataset identifier
log_root = "./log"                  # Log output directory

[geometry]
generate_stl = false                # Generate STL from model (vs use pre-generated)
use_stl_geometry = true             # Use STL files in Mesh/constant/triSurface/
force_stl_generation = false        # Force STL regeneration even if files exist
mesh_root = "Mesh"                  # Output directory name

[mesh]
generate_mesh = true                # Run mesh generation
parallel_mesh = false               # Enable parallel meshing (requires MPI)
base_size = 0.2                     # Background mesh cell size (meters)
refinement_level = [1, 1]           # General refinement [min, max]
rack_refinement_level = [1, 1]      # Rack-specific refinement
server_face_refinement_level = [1, 1]  # Server inlet/outlet refinement
wall_baffles_refinement_level = [1, 1] # Baffle refinement
location_in_mesh = [4.62, 3.39, 1.78]  # Reference point inside fluid domain

[simulation]
simulation_type = "steady"          # "steady" or "transient"
end_time = 500                      # Number of iterations (steady) or seconds (transient)
write_interval = 100                # Write results every N iterations/seconds
room_temperature = 22.0             # Initial room temperature (Celsius)

[process]
mesh_process = 1                    # Number of MPI processes for meshing
solve_process = 1                   # Number of MPI processes for solving

[output]
write_fields = "all"                # Which fields to output ("all" or specific names)
convert_vtk = false                 # Convert results to VTK format
```

### Key Parameters to Tune

**Mesh quality vs speed:**

- `base_size`: Smaller = finer mesh, slower. Start with 0.3-0.5 for testing, use 0.1-0.2 for production.
- `refinement_level`: `[1, 1]` is coarse, `[2, 3]` is fine. Server faces often need higher values.
- `location_in_mesh`: Must be a point inside the fluid domain (not inside any solid geometry). If mesh generation fails with "location not found", adjust this point.

**Solver convergence:**

- `end_time`: For steady-state, 500 iterations is often enough for simple cases. Large/complex rooms may need 1000-2000.
- `room_temperature`: Initial condition. Set close to expected average temperature for faster convergence.

## Viewing Results

After solving, open the results in ParaView:

```bash
paraview Mesh/case.foam
```

Key fields to visualize:

- **T** — Temperature field (Kelvin)
- **U** — Velocity vector field (m/s)
- **p_rgh** — Pressure field (Pa)
- **k** — Turbulent kinetic energy

## Creating Your Own Case

### Method 1: Copy and Modify an Existing Case

1. Copy a demo case directory:
   ```bash
   cp -r case/Base_coldAisleIso case/MyDataCenter
   ```

2. Edit `model/cfd_export.json` to define your geometry (racks, ACUs, walls, floors, baffles).

3. Update `config.toml` with appropriate mesh/simulation parameters.

4. Regenerate `bridge_bc.json` by running the pipeline (it is auto-generated from cfd_export.json).

5. Run:
   ```bash
   cd case/MyDataCenter
   python run_cfd.py
   ```

### Method 2: Start from Scratch

1. Create the case directory structure:
   ```bash
   mkdir -p case/MyDataCenter/model
   cp case/Base_coldAisleIso/run_cfd.py case/MyDataCenter/
   ```

2. Create `config.toml` (see template above).

3. Create `model/cfd_export.json` following the [JSON Schema Reference](../concepts/json_schema.md).

4. Create `model/room.json` with room geometry metadata.

5. Run the pipeline to generate STL, mesh, and solve.

## Troubleshooting

**Mesh generation fails with "location not found":**
The `location_in_mesh` point in config.toml must be inside the fluid domain. Pick a point in the middle of the room, away from walls and equipment.

**Solver diverges (residuals blow up):**
- Try a coarser mesh first (`base_size = 0.5`)
- Reduce the initial time step or increase relaxation factors
- Check that all boundary conditions have reasonable values (flow rates, temperatures)

**STL generation fails:**
- Ensure `model/cfd_export.json` is valid JSON
- Check that rack/ACU dimensions and positions don't overlap
- Verify room plane coordinates define a valid polygon

**ParaView shows empty results:**
- Check that `Mesh/case.foam` exists (or create an empty file: `touch Mesh/case.foam`)
- Ensure the solver completed at least one write interval
