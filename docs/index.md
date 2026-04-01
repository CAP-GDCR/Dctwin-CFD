# Welcome to DCTwin

DCTwin is a Python framework for automated CFD (Computational Fluid Dynamics) simulation of data center thermal environments. It uses [OpenFOAM](https://openfoam.org/) as the solver backend.

## Key Features

- **JSON-driven workflow**: Define your data center layout in a `room.json` file
- **Automated CFD pipeline**: STL generation → blockMesh → snappyHexMesh → boundary conditions → solver → post-processing
- **Domain-specific automation**: U-slot server positioning, air leakage modeling, raised floor with perforated tiles, cold/hot aisle containment

## Get Started

See the [Quick Start Guide](get_started/index.md) for installation and your first simulation.
