"""
CFD Runner Module - Workflow automation for OpenFOAM simulations.

This package exposes the legacy runner symbols via lazy imports so the
STLGen-only path can import ``dctwin.runner.stlgen_runner`` without
eagerly importing the legacy ``case.py`` stack.
"""

from typing import Any

__all__ = ["CaseConfig", "MeshGenerator", "FieldGenerator", "CaseRunner"]


def __getattr__(name: str) -> Any:
    if name == "CaseConfig":
        from .config import CaseConfig

        return CaseConfig
    if name == "MeshGenerator":
        from .mesh import MeshGenerator

        return MeshGenerator
    if name == "FieldGenerator":
        from .fields import FieldGenerator

        return FieldGenerator
    if name == "CaseRunner":
        from .case import CaseRunner

        return CaseRunner
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
