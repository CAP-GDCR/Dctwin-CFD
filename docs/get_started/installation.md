# Installation

## Prerequisites

- **Python 3.10+**
- **[OpenFOAM v2312+](https://www.openfoam.com/)** — Required for mesh generation and CFD solving. Follow the [official installation guide](https://www.openfoam.com/download/).
- **[Poetry](https://python-poetry.org/)** — For dependency management (or use pip directly).

## Option 1: Conda + Poetry (recommended)

```bash
git clone https://github.com/CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
bash setup_env.sh
conda activate dctwin
```

## Option 2: Poetry only

```bash
git clone https://github.com/CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
pip install poetry
poetry install
```

## Option 3: pip

```bash
git clone https://github.com/CAP-GDCR/Dctwin-CFD.git
cd Dctwin-CFD
pip install -e .
```

## Verify Installation

```bash
python -c "from dctwin.runner.case import CaseRunner; print('OK')"
```

## Verify OpenFOAM

```bash
blockMesh -help
```

If this command is not found, ensure OpenFOAM is sourced in your shell (e.g., `source /opt/openfoam/etc/bashrc`).
