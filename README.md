# DCTwin

**DCTwin** is an open-source Python framework for automated CFD (Computational Fluid Dynamics) simulation of data center thermal environments. It uses [OpenFOAM](https://openfoam.org/) as the solver backend and turns a simple JSON room layout definition into a complete thermal simulation.

## Key Features

- **JSON-driven workflow**: Define your data center layout (racks, servers, ACUs, raised floor, containment) in a `room.json` file
- **Automated CFD pipeline**: STL geometry generation -> blockMesh -> snappyHexMesh -> boundary conditions -> buoyantBoussinesqSimpleFoam -> post-processing
- **Domain-specific automation**:
  - Automatic U-slot server positioning and inlet/outlet face generation
  - Air leakage modeling with auto-computed Darcy-Forchheimer porous media
  - Raised floor with perforated tile openings
  - Cold/hot aisle containment support
- **Modular pipeline**: Run STL-only, mesh-only, or solve-only stages independently

## Prerequisites

- Python 3.10+
- [OpenFOAM v2312](https://www.openfoam.com/) installed and available on PATH
- [Poetry](https://python-poetry.org/) for dependency management

## Installation

### Using Conda + Poetry (recommended)

```bash
git clone git@github.com:CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
bash setup_env.sh
conda activate dctwin
```

### Using Poetry only

```bash
git clone git@github.com:CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
poetry install
```

## Quick Start

```bash
cd case/Base_coldAisleIso/

# Full pipeline (STL -> mesh -> solve)
python -m dctwin.runner.run_cfd --config config.toml

# Or run stages independently:
python -m dctwin.runner.run_cfd --config config.toml --stl-only    # Geometry only
python -m dctwin.runner.run_cfd --config config.toml --mesh-only   # Mesh only
python -m dctwin.runner.run_cfd --config config.toml --solve-only  # Solve only
```

## Example Cases

| Case | Description |
|------|-------------|
| `case/Base_coldAisleIso/` | 2 racks, 1 ACU, cold aisle containment |
| `case/Base_leakage/` | Air leakage modeling through unoccupied rack U-slots |
| `case/Alibaba-hongchou/` | Real facility layout with multiple racks and ACUs |
| `case/GTC/` | GTC data center configuration |
| `case/STDCT/` | Standard data center topology |
| `case/Slab/` | Slab floor configuration |

## Architecture

```
room.json (layout definition)
    |
    v
+------------------------------+
|  GeometryManager             |
|  JSON -> multi-solid STL     |
|  (racks, ACUs, walls, floor) |
+------------------------------+
    |
    v
+------------------------------+
|  MeshGenerator               |
|  blockMesh -> snappyHexMesh  |
|  + topoSet + createBaffles   |
+------------------------------+
    |
    v
+------------------------------+
|  FieldGenerator              |
|  Auto boundary conditions    |
|  (velocity, temperature,     |
|   pressure, turbulence)      |
+------------------------------+
    |
    v
+------------------------------+
|  OpenFOAM Solver             |
|  buoyantBoussinesqSimpleFoam |
|  (steady-state RANS k-e)    |
+------------------------------+
    |
    v
    CFD Results (T, U, p fields)
```

## Project Structure

```
dctwin/
├── dctwin/                    # Core Python package
│   ├── runner/                # Pipeline orchestration
│   │   ├── case.py            # CaseRunner (main orchestrator)
│   │   ├── run_cfd.py         # CLI entry point
│   │   ├── mesh.py            # MeshGenerator
│   │   ├── fields.py          # FieldGenerator (boundary conditions)
│   │   └── config.py          # Configuration management
│   ├── third_parties/foam/    # OpenFOAM integration
│   │   ├── geometry/          # STL generation (Entity/SubEntity pattern)
│   │   └── template/          # Jinja2 OpenFOAM dict templates
│   └── utils/                 # Shared utilities
├── dclib/                     # Data center domain models (Pydantic v2)
├── case/                      # Example simulation cases
├── test/                      # Test suite
├── tutorials/                 # Usage tutorials
└── docs/                      # Documentation (MkDocs)
```

## Documentation

```bash
mkdocs serve
```

## Contributing

Contributions are welcome! Please feel free to submit issues and pull requests.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.
