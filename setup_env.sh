#!/bin/bash
set -e

ENV_NAME="dctwin"
ENV_PREFIX="$(conda info --base)/envs/$ENV_NAME"

echo ">>> Creating Conda environment: $ENV_NAME..."
conda create -n $ENV_NAME python=3.10 -y

echo ">>> Installing Poetry into Conda environment..."
conda run -n $ENV_NAME pip install --force-reinstall poetry

echo ">>> Configuring Poetry to use the Conda environment..."
conda run -n $ENV_NAME poetry config virtualenvs.create false

echo ">>> Installing project dependencies..."
VIRTUAL_ENV="$ENV_PREFIX" conda run -n $ENV_NAME poetry lock
VIRTUAL_ENV="$ENV_PREFIX" conda run -n $ENV_NAME poetry install

echo ">>> Setup complete!"
echo "To start working, run:"
echo "    conda activate $ENV_NAME"
