# DCTwin — Data Center Digital Twin CFD Toolbox

DCTwin is an open-source Python framework that automates [OpenFOAM](https://www.openfoam.com/) thermal simulation for data center environments. Given a JSON simulation manifest (`.cfdrun`) and pre-exported STL geometry files, DCTwin handles the full CFD pipeline automatically:

**blockMesh → snappyHexMesh → boundary conditions → buoyantBoussinesqSimpleFoam → results**

## Key Features

- **JSON-driven workflow** — Data center layout (racks, servers, ACUs, raised floor, containment) defined in a `.cfdrun` manifest
- **Automated CFD pipeline** — From mesh generation to solver execution, fully automated
- **Domain-specific automation** — U-slot server positioning, air leakage modeling, raised floor with perforated tiles, cold/hot aisle containment
- **Modular pipeline** — Run mesh-only or solve-only stages independently
- **Parallel execution** — Parallel mesh generation and parallel solver via MPI

## Next Steps

- [Installation](getting_started/installation.md) — Set up your environment
- [Quick Start](getting_started/quick_start.md) — Run your first simulation
- [Tutorial Cases](cases/overview.md) — Explore the included example cases
- [Architecture](architecture/pipeline.md) — Understand the pipeline internals
