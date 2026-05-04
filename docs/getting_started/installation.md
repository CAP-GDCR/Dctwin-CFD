# Installation

## Prerequisites

- **Python 3.10+**
- **Conda** ([Miniconda](https://docs.conda.io/en/latest/miniconda.html) or Anaconda)
- **OpenFOAM v2406** installed and available on `PATH` ([download](https://www.openfoam.com/download/))

Verify OpenFOAM is available:

```bash
buoyantBoussinesqSimpleFoam -help 2>&1 | head -1
# Should show: Usage: buoyantBoussinesqSimpleFoam [OPTIONS]
```

## Install DCTwin

```bash
git clone https://github.com/CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
bash setup_env.sh
conda activate dctwin
```

`setup_env.sh` creates a Conda environment named `dctwin` with Python 3.10, installs [Poetry](https://python-poetry.org/) for dependency management, and installs all required packages.

## Verify Installation

```bash
python -m dctwin.runner.run_cfd --help
```

Expected output:

```
usage: run_cfd.py [-h] --from-cfdrun CFDRUN_PATH [--mesh-only] [--solve-only]

Run OpenFOAM CFD simulation for data center

options:
  -h, --help            show this help message and exit
  --from-cfdrun CFDRUN_PATH
                        Path to .cfdrun manifest file
  --mesh-only           Generate mesh only (skip solver)
  --solve-only          Run solver only (requires existing mesh)
```
