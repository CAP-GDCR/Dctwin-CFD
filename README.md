<div align="center">

<img src="docs/_static/images/cap_banner.png" alt="Cloud Application and Platform Group — NTU Singapore" width="500"/>

# DCTwin — Data Center Digital Twin CFD Toolbox

**Automated CFD Simulation Framework for Data Center Thermal Analysis**

[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg?logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: AGPL-3.0](https://img.shields.io/badge/license-AGPL--3.0-green.svg)](LICENSE)
[![OpenFOAM v2406](https://img.shields.io/badge/OpenFOAM-v2406-red.svg)](https://www.openfoam.com/)

</div>

---

**DCTwin** is an open-source Python framework that automates [OpenFOAM](https://www.openfoam.com/) thermal simulation for data center environments. Given a JSON simulation manifest (`.cfdrun`) and pre-exported STL geometry files, DCTwin handles the full CFD pipeline automatically.

## Key Features

- **JSON-driven workflow** -- Data center layout (racks, servers, ACUs, raised floor, containment) defined in a `.cfdrun` manifest with pre-exported multi-solid STL files
- **Automated CFD pipeline** -- blockMesh -> snappyHexMesh -> boundary conditions -> buoyantBoussinesqSimpleFoam -> post-processing
- **Domain-specific automation**:
  - Automatic U-slot server positioning and inlet/outlet face generation
  - Air leakage modeling with auto-computed Darcy-Forchheimer porous media
  - Raised floor with perforated tile openings
  - Cold/hot aisle containment support
- **Modular pipeline** -- Run STL-only, mesh-only, or solve-only stages independently

## Prerequisites

- Python 3.10+
- [Conda](https://docs.conda.io/) (Miniconda or Anaconda)
- [OpenFOAM v2406](https://www.openfoam.com/) installed and available on PATH

## Installation

```bash
git clone https://github.com/CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
bash setup_env.sh
conda activate dctwin
```

## Quick Start

```bash
# Full pipeline (mesh -> solve) — runs the triangle_room example case
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun

# Or run stages independently:
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun --mesh-only   # Mesh only
python -m dctwin.runner.run_cfd --from-cfdrun case/triangle_room/model/triangle_room.cfdrun --solve-only  # Solve only
```

## Example Cases

| Case                      | Description                                          |
| ------------------------- | ---------------------------------------------------- |
| `case/triangle_room/`     | Triangular room layout (good first example)          |
| `case/slab_floor/`        | Single-slab data center with raised floor            |
| `case/rack_leakage/`      | Air leakage modeling through unoccupied rack U-slots |
| `case/standard_dc/`       | Standard data center topology (24 racks, 3 ACUs)     |
| `case/conference_demo/`   | Large conference demo case                           |

## Architecture

```
.cfdrun manifest + STL files
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

This project is licensed under the GNU Affero General Public License v3.0 (AGPL-3.0). See the [LICENSE](LICENSE) file for details.

## Acknowledgments

Developed by the [NTU-CAP](https://www.ntu-cap.org/team/) research group at [Nanyang Technological University](https://www.ntu.edu.sg/), Singapore.

This repository contains only the DCTwin backend currently. The DCTwin GUI for visual modeling and case setup is under active development and is planned for open-source release soon.
