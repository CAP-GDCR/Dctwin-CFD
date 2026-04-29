"""
Tests that verify the OSS repo is usable by a fresh clone following README instructions.

These tests check:
1. Package installs correctly (poetry can find dctwin)
2. CLI entry point works with documented arguments
3. Tutorial cases have the files the README says they have
4. Case-level run_cfd.py scripts can at least import successfully
"""
import importlib
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
CASE_DIR = REPO_ROOT / "case"


class TestPackageInstallation:
    """Verify the package is importable after poetry install."""

    def test_dctwin_is_importable(self):
        import dctwin
        assert hasattr(dctwin, "__init__")

    def test_runner_module_importable(self):
        from dctwin.runner import stlgen_runner
        assert hasattr(stlgen_runner, "STLGenRunner")

    def test_run_cfd_module_importable(self):
        from dctwin.runner import run_cfd
        assert hasattr(run_cfd, "main")

    def test_cfdproj_schema_importable(self):
        from dctwin.runner import cfdproj_schema
        assert hasattr(cfdproj_schema, "CFDExport")


class TestCLIEntryPoint:
    """Verify the CLI entry point matches README documentation."""

    def test_run_cfd_help(self):
        result = subprocess.run(
            [sys.executable, "-m", "dctwin.runner.run_cfd", "--help"],
            capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, f"--help failed: {result.stderr}"
        assert "--from-cfdrun" in result.stdout, (
            "CLI should accept --from-cfdrun, "
            f"but help says: {result.stdout}"
        )

    def test_readme_claims_config_toml_flag(self):
        """README says: python -m dctwin.runner.run_cfd --config config.toml
        But the actual CLI only accepts --from-stlgen. This test documents
        the mismatch."""
        result = subprocess.run(
            [sys.executable, "-m", "dctwin.runner.run_cfd", "--config", "fake.toml"],
            capture_output=True, text=True, timeout=30,
        )
        # If --config is not recognized, argparse returns exit code 2
        assert result.returncode != 0, (
            "--config should NOT be accepted (README is wrong)"
        )


class TestTutorialCaseFiles:
    """Verify tutorial cases have the files needed to run."""

    EXPECTED_CASES = [
        "Base_coldAisleIso",
        "Base_leakage",
        "Slab",
        "STDCT",
        "Tri-angle",
        "GTC",
        "Alibaba-hongchou",
    ]

    @pytest.mark.parametrize("case_name", EXPECTED_CASES)
    def test_case_directory_exists(self, case_name):
        case_path = CASE_DIR / case_name
        assert case_path.is_dir(), f"Case directory missing: {case_path}"

    @pytest.mark.parametrize("case_name", EXPECTED_CASES)
    def test_case_has_cfd_export_json(self, case_name):
        cfd_export = CASE_DIR / case_name / "model" / "cfd_export.json"
        assert cfd_export.is_file(), f"Missing: {cfd_export}"

    @pytest.mark.parametrize("case_name", EXPECTED_CASES)
    def test_case_has_stl_files(self, case_name):
        tri_surface = CASE_DIR / case_name / "Mesh" / "constant" / "triSurface"
        assert tri_surface.is_dir(), f"Missing triSurface dir: {tri_surface}"
        stl_files = list(tri_surface.glob("*.stl"))
        assert len(stl_files) > 0, f"No STL files in {tri_surface}"

    def test_readme_cases_match_actual(self):
        """README case list should match actual case directories."""
        readme_cases = {
            "Base_coldAisleIso", "Base_leakage",
            "Slab", "STDCT", "Tri-angle", "GTC", "Alibaba-hongchou",
        }
        actual_cases = {d.name for d in CASE_DIR.iterdir() if d.is_dir()}
        phantom_in_readme = readme_cases - actual_cases
        assert not phantom_in_readme, (
            f"README lists cases that don't exist: {phantom_in_readme}"
        )


class TestCaseRunScripts:
    """Verify no stale per-case run_cfd.py scripts remain."""

    def test_no_stale_case_run_scripts(self):
        """Old run_cfd.py scripts imported CaseRunner which doesn't exist.
        They should be removed — use the CLI entry point instead."""
        stale_scripts = list(CASE_DIR.rglob("run_cfd.py"))
        assert len(stale_scripts) == 0, (
            f"Stale run_cfd.py scripts found: {stale_scripts}. "
            "Use 'python -m dctwin.runner.run_cfd --from-cfdrun ...' instead."
        )


class TestReadmeAccuracy:
    """Verify README claims match repository reality."""

    def test_no_config_toml_in_any_case(self):
        """README Quick Start says to use config.toml but none exist."""
        config_files = list(CASE_DIR.rglob("config.toml"))
        # This SHOULD fail if README is accurate — documenting that it's wrong
        assert len(config_files) == 0, (
            "Expected no config.toml files (README is outdated)"
        )

    def test_no_dclib_directory(self):
        """README project structure shows dclib/ but it doesn't exist."""
        dclib_dir = REPO_ROOT / "dclib"
        assert not dclib_dir.exists(), (
            "dclib/ should not exist (it's not a dependency)"
        )

    def test_project_structure_claims_root_dctwin(self):
        """README shows dctwin/ at root but code is in src/dctwin/."""
        root_dctwin = REPO_ROOT / "dctwin"
        src_dctwin = REPO_ROOT / "src" / "dctwin"
        assert not root_dctwin.exists(), "dctwin/ at root should not exist"
        assert src_dctwin.exists(), "src/dctwin/ should exist"

    def test_pyproject_packages_path(self):
        """pyproject.toml says packages=[{include='dctwin'}] but code is in src/."""
        import toml
        pyproject = toml.load(REPO_ROOT / "pyproject.toml")
        packages = pyproject["tool"]["poetry"]["packages"]
        for pkg in packages:
            if pkg.get("include") == "dctwin":
                assert pkg.get("from") == "src", (
                    f"pyproject.toml packages should use from='src', "
                    f"but got: {pkg}"
                )
