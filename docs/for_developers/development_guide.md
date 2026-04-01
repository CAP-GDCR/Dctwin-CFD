# Development Guide

## Setting Up Your Development Environment

```bash
# Clone the repository
git clone git@github.com:CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD

# Create a conda environment
conda create -n dctwin python=3.10 -y
conda activate dctwin

# Install dependencies
pip install poetry
poetry config virtualenvs.create false
poetry install
```

## Prerequisites

- Python 3.10+
- [OpenFOAM v2312+](https://www.openfoam.com/) for running mesh/solver commands
- [Poetry](https://python-poetry.org/) for dependency management

## Running Tests

```bash
cd test
python test_cfd.py
```

## Code Style

This project uses:
- [Black](https://github.com/psf/black) for code formatting
- [isort](https://pycqa.github.io/isort/) for import sorting

```bash
black dctwin/ dclib/
isort dctwin/ dclib/
```

## Project Layout

- `dctwin/runner/` — CFD pipeline orchestration (case, mesh, fields, solver)
- `dctwin/third_parties/foam/` — OpenFOAM integration (geometry, templates)
- `dctwin/utils/` — Shared utilities (config, constants, Jinja2 template loader)
- `dclib/` — Data center domain models (Pydantic v2)
- `case/` — Example simulation cases
- `docs/` — MkDocs documentation

## Building Documentation

```bash
mkdocs serve
# Visit http://localhost:8000
```
