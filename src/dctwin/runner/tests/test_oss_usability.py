"""
Tests that verify the OSS repo is usable by a fresh clone following README instructions.

Every assertion maps to a specific README or CONTRIBUTING.md claim.
If a test fails, either the code or the docs are wrong — fix the one that's lying.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
CASE_DIR = REPO_ROOT / "case"

EXPECTED_CASES = [
    "triangle_room",
    "slab_floor",
    "rack_leakage",
    "standard_dc",
    "conference_demo",
]


def _find_cfdrun(case_name: str) -> Path:
    model_dir = CASE_DIR / case_name / "model"
    cfdrun_files = list(model_dir.glob("*.cfdrun"))
    if cfdrun_files:
        return cfdrun_files[0]
    json_files = list(model_dir.glob("*.json"))
    if json_files:
        return json_files[0]
    raise FileNotFoundError(f"No .cfdrun manifest in {model_dir}")


# ── README: Package Installation ──────────────────────────────────────────

class TestPackageInstallation:
    """README §Installation: after setup, dctwin must be importable."""

    def test_dctwin_importable(self):
        import dctwin
        assert hasattr(dctwin, "__init__")

    def test_runner_modules_importable(self):
        """README §Project Structure lists these modules — they must all import."""
        from dctwin.runner import stlgen_runner, run_cfd, cfdproj_schema
        from dctwin.runner import bridge_bc, fields, postprocess
        assert hasattr(stlgen_runner, "STLGenRunner")
        assert hasattr(run_cfd, "main")
        assert hasattr(cfdproj_schema, "CFDExport")


# ── README: CLI Entry Point ───────────────────────────────────────────────

class TestCLIEntryPoint:
    """README §Quick Start: CLI flags must match documented usage."""

    def test_help_shows_from_cfdrun(self):
        result = subprocess.run(
            [sys.executable, "-m", "dctwin.runner.run_cfd", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0
        assert "--from-cfdrun" in result.stdout

    def test_help_shows_mesh_only(self):
        result = subprocess.run(
            [sys.executable, "-m", "dctwin.runner.run_cfd", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert "--mesh-only" in result.stdout

    def test_help_shows_solve_only(self):
        result = subprocess.run(
            [sys.executable, "-m", "dctwin.runner.run_cfd", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert "--solve-only" in result.stdout

    def test_quick_start_path_exists(self):
        """The exact path shown in README Quick Start must exist."""
        qstart = REPO_ROOT / "case" / "triangle_room" / "model" / "triangle_room.cfdrun"
        assert qstart.is_file(), f"Quick Start path missing: {qstart}"


# ── README: Example Cases ─────────────────────────────────────────────────

class TestExampleCases:
    """README §Example Cases: every listed case must exist with valid data."""

    @pytest.mark.parametrize("case_name", EXPECTED_CASES)
    def test_case_dir_exists(self, case_name):
        assert (CASE_DIR / case_name).is_dir()

    @pytest.mark.parametrize("case_name", EXPECTED_CASES)
    def test_case_has_cfdrun(self, case_name):
        cfdrun = _find_cfdrun(case_name)
        assert cfdrun.is_file()
        data = json.loads(cfdrun.read_text())
        assert "room" in data, f"{case_name}: cfdrun missing 'room' key"

    @pytest.mark.parametrize("case_name", EXPECTED_CASES)
    def test_case_has_stl_files(self, case_name):
        tri_surface = CASE_DIR / case_name / "Mesh" / "constant" / "triSurface"
        assert tri_surface.is_dir()
        stl_files = list(tri_surface.glob("*.stl"))
        assert len(stl_files) >= 1

    def test_no_unlisted_cases(self):
        """case/ must not contain dirs that README doesn't mention."""
        actual = {d.name for d in CASE_DIR.iterdir() if d.is_dir()}
        expected = set(EXPECTED_CASES)
        extra = actual - expected
        assert not extra, f"case/ has dirs not in README: {extra}"

    def test_no_stale_cfd_export_json(self):
        """Old cfd_export.json files should not remain."""
        stale = list(CASE_DIR.rglob("cfd_export.json"))
        assert len(stale) == 0, f"Stale cfd_export.json: {stale}"

    def test_no_stale_run_scripts(self):
        """Per-case run_cfd.py scripts are obsolete."""
        stale = list(CASE_DIR.rglob("run_cfd.py"))
        assert len(stale) == 0, f"Stale run_cfd.py: {stale}"


# ── README: Project Structure ─────────────────────────────────────────────

class TestProjectStructure:
    """README §Project Structure: every listed path must exist."""

    @pytest.mark.parametrize("path", [
        "src/dctwin/runner/stlgen_runner.py",
        "src/dctwin/runner/run_cfd.py",
        "src/dctwin/runner/cfdproj_schema.py",
        "src/dctwin/runner/bridge_bc.py",
        "src/dctwin/runner/fields.py",
        "src/dctwin/runner/postprocess.py",
        "src/dctwin/third_parties/foam/template",
        "src/dctwin/utils",
        "docs",
    ])
    def test_readme_path_exists(self, path):
        assert (REPO_ROOT / path).exists(), f"README lists {path} but it doesn't exist"

    def test_no_phantom_geometry_dir(self):
        """geometry/ was removed — must not appear in README or filesystem."""
        geo = REPO_ROOT / "src" / "dctwin" / "third_parties" / "foam" / "geometry"
        assert not geo.exists()

    def test_pyproject_src_layout(self):
        import toml
        pyproject = toml.load(REPO_ROOT / "pyproject.toml")
        packages = pyproject["tool"]["poetry"]["packages"]
        for pkg in packages:
            if pkg.get("include") == "dctwin":
                assert pkg.get("from") == "src"


# ── README: Metadata ──────────────────────────────────────────────────────

class TestMetadata:
    """README badges and top-level files must be consistent."""

    def test_license_file_exists(self):
        assert (REPO_ROOT / "LICENSE").is_file()

    def test_license_is_agpl3(self):
        text = (REPO_ROOT / "LICENSE").read_text()
        assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text
        assert "Version 3" in text

    def test_banner_image_exists(self):
        assert (REPO_ROOT / "docs" / "_static" / "images" / "cap_banner.png").is_file()

    def test_contributing_exists(self):
        assert (REPO_ROOT / "CONTRIBUTING.md").is_file()

    def test_setup_env_exists(self):
        assert (REPO_ROOT / "setup_env.sh").is_file()

    def test_lint_sh_exists(self):
        assert (REPO_ROOT / "lint.sh").is_file()


# ── README: No stale references ───────────────────────────────────────────

class TestNoStaleReferences:
    """Docs and code must not reference removed cases or old filenames."""

    def test_readme_no_cfd_export_json(self):
        text = (REPO_ROOT / "README.md").read_text()
        assert "cfd_export.json" not in text

    def test_contributing_no_old_test_path(self):
        text = (REPO_ROOT / "CONTRIBUTING.md").read_text()
        assert "pytest test/" not in text

    def test_contributing_no_config_toml(self):
        text = (REPO_ROOT / "CONTRIBUTING.md").read_text()
        assert "config.toml" not in text

    def test_no_internal_project_markers(self):
        this_file = Path(__file__).resolve()
        banned = [
            "non-" + "public",
            "internal" + "-only",
            "internal" + " only",
            "not for " + "public release",
        ]
        for path in list((REPO_ROOT / "src").rglob("*")) + list((REPO_ROOT / "docs").rglob("*")):
            if path.is_dir() or "__pycache__" in str(path) or path.resolve() == this_file:
                continue
            text = path.read_text(errors="replace").lower()
            for marker in banned:
                assert marker not in text, f"internal marker leak in {path}"

    def test_no_legacy_org_marker_in_python_files(self):
        legacy_org_marker = "dcd" + "tuni"
        this_file = Path(__file__).resolve()
        for py_file in (REPO_ROOT / "src").rglob("*.py"):
            if "__pycache__" in str(py_file) or py_file.resolve() == this_file:
                continue
            text = py_file.read_text(errors="replace")
            assert legacy_org_marker not in text, f"legacy organization marker leak in {py_file}"

    def test_tests_no_removed_case_refs(self):
        this_file = Path(__file__).resolve()
        test_dir = REPO_ROOT / "src" / "dctwin" / "runner" / "tests"
        for py_file in test_dir.glob("*.py"):
            if py_file.resolve() == this_file:
                continue
            text = py_file.read_text()
            assert "cfd_export.json" not in text, f"stale ref in {py_file.name}"
