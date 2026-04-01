"""
Basic CFD pipeline test using the case runner.

Usage:
    cd case/Base_coldAisleIso/
    python -m dctwin.runner.run_cfd --config config.toml --stl-only
"""
import sys
from pathlib import Path


def test_import():
    """Test that core modules can be imported."""
    from dctwin.runner.case import CaseRunner
    from dctwin.runner.config import CaseConfig
    from dctwin.runner.mesh import MeshGenerator
    from dctwin.runner.fields import FieldGenerator
    from dctwin.third_parties.foam.geometry.manager import GeometryManager
    from dclib.room import Room
    print("All core imports successful.")


if __name__ == "__main__":
    test_import()
    print("Basic tests passed.")
