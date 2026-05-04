"""
Integration tests: run mesh + solve for every OSS tutorial case.

Each case must complete parallel mesh + parallel solve without errors.
All cases use parallelMesh=True, parallelSolve=True, 8 cores.

Usage:
    pytest test_case_pipeline.py -v --timeout=1800
    pytest test_case_pipeline.py -v -k "standard_dc"  # single case
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
CASE_DIR = REPO_ROOT / "case"

ALL_CASES = [
    "triangle_room",
    "slab_floor",
    "rack_leakage",
    "standard_dc",
    "conference_demo",
]


def _clean_case(case_path: Path) -> None:
    """Remove solver output so mesh+solve runs fresh."""
    import shutil
    mesh_dir = case_path / "Mesh"
    # Keep triSurface (STL input), remove everything else under Mesh
    for item in mesh_dir.iterdir():
        if item.name == "constant":
            # Keep constant/triSurface, remove constant/polyMesh
            poly = item / "polyMesh"
            if poly.exists():
                shutil.rmtree(poly)
            # Remove processor dirs inside constant
            for p in item.glob("processor*"):
                shutil.rmtree(p)
        elif item.name in ("system", "0"):
            shutil.rmtree(item)
        elif item.name.startswith("processor"):
            shutil.rmtree(item)
        elif item.is_dir() and item.name not in ("constant",):
            shutil.rmtree(item)

    # Remove processor dirs at Mesh level
    for p in mesh_dir.glob("processor*"):
        if p.is_dir():
            shutil.rmtree(p)

    # Remove log dir (solver output)
    log_dir = case_path / "log"
    if log_dir.exists():
        shutil.rmtree(log_dir)


def _find_cfdrun(case_name: str) -> Path:
    """Find the .cfdrun (or .json fallback) manifest for a case."""
    model_dir = CASE_DIR / case_name / "model"
    cfdrun_files = list(model_dir.glob("*.cfdrun"))
    if cfdrun_files:
        return cfdrun_files[0]
    json_files = list(model_dir.glob("*.json"))
    if json_files:
        return json_files[0]
    raise FileNotFoundError(f"No .cfdrun or .json manifest in {model_dir}")


def _run_case(case_name: str, extra_args: list[str] | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    """Run a case via the CLI entry point."""
    cfd_export = _find_cfdrun(case_name)
    cmd = [sys.executable, "-m", "dctwin.runner.run_cfd", "--from-cfdrun", str(cfd_export)]
    if extra_args:
        cmd.extend(extra_args)
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(REPO_ROOT),
    )


def _assert_no_fatal(result: subprocess.CompletedProcess, case_name: str) -> None:
    """Check output for FOAM FATAL errors and NaN."""
    combined = result.stdout + result.stderr
    assert "FOAM FATAL" not in combined, (
        f"{case_name}: FOAM FATAL ERROR found in output:\n"
        + _extract_context(combined, "FOAM FATAL")
    )
    assert "NaN" not in combined.split("Time = ")[-1] if "Time = " in combined else True, (
        f"{case_name}: NaN detected in solver output"
    )


def _extract_context(text: str, keyword: str, lines: int = 10) -> str:
    """Extract lines around a keyword match for error reporting."""
    all_lines = text.splitlines()
    for i, line in enumerate(all_lines):
        if keyword in line:
            start = max(0, i - 2)
            end = min(len(all_lines), i + lines)
            return "\n".join(all_lines[start:end])
    return ""


class TestCaseParallelSettings:
    """Verify all cases are configured for parallel execution."""

    @pytest.mark.parametrize("case_name", ALL_CASES)
    def test_parallel_mesh_enabled(self, case_name):
        cfd_export = _find_cfdrun(case_name)
        with open(cfd_export) as f:
            data = json.load(f)
        mc = data.get("meshConfig", {})
        assert mc.get("parallelMesh") is True, f"{case_name}: parallelMesh must be True"
        assert mc.get("meshProcess", 0) >= 4, f"{case_name}: meshProcess must be >= 4"

    @pytest.mark.parametrize("case_name", ALL_CASES)
    def test_parallel_solve_enabled(self, case_name):
        cfd_export = _find_cfdrun(case_name)
        with open(cfd_export) as f:
            data = json.load(f)
        sc = data.get("simulationConfig", {})
        assert sc.get("parallelSolve") is True, f"{case_name}: parallelSolve must be True"
        assert sc.get("solveProcess", 0) >= 4, f"{case_name}: solveProcess must be >= 4"


@pytest.mark.slow
class TestCaseMeshSolve:
    """Run mesh + solve for each tutorial case. Requires OpenFOAM on PATH."""

    @pytest.mark.parametrize("case_name", ALL_CASES)
    def test_mesh_solve(self, case_name, tmp_path):
        """Full pipeline: mesh + solve. Must exit 0 with no FOAM FATAL."""
        case_path = CASE_DIR / case_name

        # Clean previous results
        _clean_case(case_path)

        # Run full pipeline (mesh + solve)
        timeout = 600
        result = _run_case(case_name, timeout=timeout)

        # Must succeed
        assert result.returncode == 0, (
            f"{case_name}: pipeline failed (exit {result.returncode}).\n"
            f"STDERR (last 50 lines):\n"
            + "\n".join(result.stderr.splitlines()[-50:])
        )

        # No FOAM FATAL errors
        _assert_no_fatal(result, case_name)

        # Verify mesh was created
        poly_mesh = case_path / "Mesh" / "constant" / "polyMesh" / "points"
        assert poly_mesh.exists(), f"{case_name}: polyMesh/points not found after mesh"
