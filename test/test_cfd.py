"""
Comprehensive CFD pipeline tests.

Tests cover imports, config/JSON parsing, data model construction, and
case directory completeness. No OpenFOAM installation is required.

Usage:
    pytest test/test_cfd.py -v
"""
import json
import sys
import tomllib
from pathlib import Path

import pytest

# Resolve the repository root and the shared case directory
REPO_ROOT = Path(__file__).parent.parent
CASE_ROOT = REPO_ROOT / "case"

# ---------------------------------------------------------------------------
# 1. Import tests
# ---------------------------------------------------------------------------

def test_import_case_runner():
    from dctwin.runner.case import CaseRunner


def test_import_case_config():
    from dctwin.runner.config import CaseConfig


def test_import_mesh_generator():
    from dctwin.runner.mesh import MeshGenerator


def test_import_field_generator():
    from dctwin.runner.fields import FieldGenerator


def test_import_geometry_manager():
    from dctwin.third_parties.foam.geometry.manager import GeometryManager


def test_import_room():
    from dclib.room import Room


# ---------------------------------------------------------------------------
# 2. Config parsing test
# ---------------------------------------------------------------------------

def test_config_toml_sections():
    """Load config.toml with tomllib and verify expected top-level sections."""
    config_path = CASE_ROOT / "Base_coldAisleIso" / "config.toml"
    assert config_path.exists(), f"config.toml not found: {config_path}"

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    assert "paths" in config, "Expected [paths] section in config.toml"
    assert "mesh" in config, "Expected [mesh] section in config.toml"
    assert "simulation" in config, "Expected [simulation] section in config.toml"
    assert "process" in config, "Expected [process] section in config.toml"


def test_config_toml_key_values():
    """Verify specific key values within config.toml."""
    config_path = CASE_ROOT / "Base_coldAisleIso" / "config.toml"

    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    assert config["paths"]["room_json"] == "model/room.json"
    assert isinstance(config["mesh"]["base_size"], float)
    assert config["mesh"]["base_size"] > 0


# ---------------------------------------------------------------------------
# 3. Room JSON parsing test
# ---------------------------------------------------------------------------

def test_room_json_loads():
    """Load room.json with json.load and verify it is a valid dict."""
    room_path = CASE_ROOT / "Base_coldAisleIso" / "model" / "room.json"
    assert room_path.exists(), f"room.json not found: {room_path}"

    with open(room_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert isinstance(data, dict), "room.json should parse to a dict"


def test_room_json_top_level_keys():
    """Verify required top-level keys are present in room.json."""
    room_path = CASE_ROOT / "Base_coldAisleIso" / "model" / "room.json"

    with open(room_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for key in ("geometry", "constructions", "models"):
        assert key in data, f"Expected key '{key}' in room.json"


# ---------------------------------------------------------------------------
# 4. Room model test (dclib.room.Room)
# ---------------------------------------------------------------------------

def test_room_model_construction():
    """Parse room.json with dclib Room.load() and verify basic attributes."""
    from dclib.room import Room

    room_path = CASE_ROOT / "Base_coldAisleIso" / "model" / "room.json"
    room = Room.load(room_path)

    assert room is not None
    assert room.geometry is not None
    assert room.geometry.height > 0
    assert room.constructions is not None


def test_room_model_geometry_plane():
    """The Base_coldAisleIso room geometry should have a non-trivial floor plane."""
    from dclib.room import Room

    room_path = CASE_ROOT / "Base_coldAisleIso" / "model" / "room.json"
    room = Room.load(room_path)

    # The floor plane must have at least 3 vertices to form a valid polygon.
    assert len(room.geometry.plane) >= 3, "Room floor plane must have at least 3 vertices"
    # Area must be positive.
    assert room.geometry.area > 0, "Room floor area must be positive"


# ---------------------------------------------------------------------------
# 5. CaseConfig test
# ---------------------------------------------------------------------------

def test_case_config_construction():
    """Construct CaseConfig from Base_coldAisleIso and verify key properties."""
    from dctwin.runner.config import CaseConfig

    case_dir = CASE_ROOT / "Base_coldAisleIso"
    cfg = CaseConfig(case_dir)

    assert cfg.case_dir == case_dir.resolve()
    assert cfg.config_path.exists()


def test_case_config_mesh_settings():
    """Verify mesh settings are accessible through CaseConfig."""
    from dctwin.runner.config import CaseConfig

    cfg = CaseConfig(CASE_ROOT / "Base_coldAisleIso")

    assert cfg.base_size > 0
    assert isinstance(cfg.generate_mesh, bool)
    assert isinstance(cfg.mesh_cache_dir, Path)


def test_case_config_get_dotted_key():
    """Verify CaseConfig.get() resolves dot-separated keys correctly."""
    from dctwin.runner.config import CaseConfig

    cfg = CaseConfig(CASE_ROOT / "Base_coldAisleIso")

    room_json_val = cfg.get("paths.room_json")
    assert room_json_val == "model/room.json"

    missing = cfg.get("nonexistent.key", default="fallback")
    assert missing == "fallback"


def test_case_config_room_json_path():
    """CaseConfig.room_json_path should resolve to an existing file."""
    from dctwin.runner.config import CaseConfig

    cfg = CaseConfig(CASE_ROOT / "Base_coldAisleIso")
    assert cfg.room_json_path.exists(), f"room_json_path not found: {cfg.room_json_path}"


def test_case_config_load_room_config():
    """CaseConfig.load_room_config() should return a non-empty dict."""
    from dctwin.runner.config import CaseConfig

    cfg = CaseConfig(CASE_ROOT / "Base_coldAisleIso")
    room_cfg = cfg.load_room_config()

    assert isinstance(room_cfg, dict)
    assert len(room_cfg) > 0


# ---------------------------------------------------------------------------
# 6. Parametrized case completeness tests
# ---------------------------------------------------------------------------

def _get_case_dirs():
    """Return all immediate subdirectories under case/."""
    return sorted([d for d in CASE_ROOT.iterdir() if d.is_dir()])


@pytest.mark.parametrize("case_dir", _get_case_dirs(), ids=lambda d: d.name)
def test_case_has_config_toml(case_dir):
    """Every case directory must contain config.toml."""
    assert (case_dir / "config.toml").exists(), \
        f"Missing config.toml in {case_dir}"


@pytest.mark.parametrize("case_dir", _get_case_dirs(), ids=lambda d: d.name)
def test_case_has_room_json(case_dir):
    """Every case directory must contain model/room.json."""
    assert (case_dir / "model" / "room.json").exists(), \
        f"Missing model/room.json in {case_dir}"


@pytest.mark.parametrize("case_dir", _get_case_dirs(), ids=lambda d: d.name)
def test_case_has_run_cfd_py(case_dir):
    """Every case directory must contain run_cfd.py."""
    assert (case_dir / "run_cfd.py").exists(), \
        f"Missing run_cfd.py in {case_dir}"
