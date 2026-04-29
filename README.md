<div align="center">

<img src="docs/_static/images/cap_banner.png" alt="Cloud Application and Platform Group — NTU Singapore" width="500"/>

# DCTwin — Data Center Digital Twin CFD Toolbox

**Automated CFD Simulation Framework for Data Center Thermal Analysis**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![OpenFOAM v2404](https://img.shields.io/badge/OpenFOAM-v2404-red.svg)](https://www.openfoam.com/)

</div>

---

**DCTwin** is an open-source Python framework that automates [OpenFOAM](https://openfoam.org/) thermal simulation for data center environments. Given a JSON simulation manifest (`cfd_export.json`) and pre-exported STL geometry files, DCTwin handles the full CFD pipeline automatically.

## Key Features

- **JSON-driven workflow** -- Data center layout (racks, servers, ACUs, raised floor, containment) defined in a `cfd_export.json` manifest with pre-exported multi-solid STL files
- **Automated CFD pipeline** -- blockMesh -> snappyHexMesh -> boundary conditions -> buoyantBoussinesqSimpleFoam -> post-processing
- **Domain-specific automation**:
  - Automatic U-slot server positioning and inlet/outlet face generation
  - Air leakage modeling with auto-computed Darcy-Forchheimer porous media
  - Raised floor with perforated tile openings
  - Cold/hot aisle containment support
- **Modular pipeline** -- Run STL-only, mesh-only, or solve-only stages independently

## Prerequisites

- Python 3.10+
- [OpenFOAM v2404](https://www.openfoam.com/) installed and available on PATH
- [Poetry](https://python-poetry.org/) for dependency management

## Installation

### Using Conda + Poetry (recommended)

```bash
git clone https://github.com/CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
bash setup_env.sh
conda activate dctwin
```

### Using Poetry only

```bash
git clone https://github.com/CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
poetry install
```

## Quick Start

```bash
# Full pipeline (mesh -> solve)
python -m dctwin.runner.run_cfd --from-cfdrun case/Base_coldAisleIso/model/cfd_export.json

# Or run stages independently:
python -m dctwin.runner.run_cfd --from-cfdrun case/Base_coldAisleIso/model/cfd_export.json --mesh-only   # Mesh only
python -m dctwin.runner.run_cfd --from-cfdrun case/Base_coldAisleIso/model/cfd_export.json --solve-only  # Solve only
```

## Example Cases

| Case                        | Description                                          |
| --------------------------- | ---------------------------------------------------- |
| `case/Base_coldAisleIso/` | 2 racks, 1 ACU, cold aisle containment               |
| `case/Base_leakage/`      | Air leakage modeling through unoccupied rack U-slots |
| `case/Slab/`              | Single-slab data center room                         |
| `case/STDCT/`             | Standard data center topology                        |
| `case/Tri-angle/`         | Triangular room layout                               |
| `case/GTC/`               | GTC conference demo case                             |
| `case/Alibaba-hongchou/`  | Large-scale Alibaba data center                      |

## Architecture

```
cfd_export.json + STL files
    |
    v
+------------------------------+
|  STLGenRunner                |
|  Validates JSON manifest,    |
|  discovers STL geometry      |
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
Dctwin-CFD/
├── src/dctwin/                # Core Python package
│   ├── runner/                # Pipeline orchestration
│   │   ├── stlgen_runner.py   # STLGenRunner (main orchestrator)
│   │   ├── run_cfd.py         # CLI entry point
│   │   ├── cfdproj_schema.py  # Pydantic v2 schema validation
│   │   ├── bridge_bc.py       # Boundary condition registry
│   │   ├── fields.py          # FieldGenerator (boundary conditions)
│   │   └── postprocess.py     # Result extraction
│   ├── third_parties/foam/    # OpenFOAM integration
│   │   ├── geometry/          # STL generation (Entity/SubEntity pattern)
│   │   └── template/          # Jinja2 OpenFOAM dict templates
│   └── utils/                 # Shared utilities
├── case/                      # Example simulation cases
└── docs/                      # Documentation (MkDocs)
```

## Documentation

```bash
mkdocs serve
```

## Contributing

Contributions are welcome! Please read the [Contributing Guide](CONTRIBUTING.md) before submitting a pull request.

## License

This project is licensed under the Apache License 2.0. See the [LICENSE](LICENSE) file for details.

## Acknowledgments

Developed by the [CAP-GDCR](https://github.com/CAP-GDCR) research group at [Nanyang Technological University](https://www.ntu.edu.sg/), Singapore.
