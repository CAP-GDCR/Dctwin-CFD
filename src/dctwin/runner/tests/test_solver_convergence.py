"""Integration tests: verify solver runs without NaN/divergence.

These are end-to-end tests that exercise the full pipeline:
.cfdrun + STL files -> mesh -> solve -> check no NaN.

These tests are slow (minutes per case) and require OpenFOAM on PATH.
Mark with @pytest.mark.slow so they can be skipped in CI.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

CASE_DIR = Path(__file__).resolve().parents[4] / "case"
PYTHON = sys.executable


def _find_cfdrun(case_name: str) -> Path:
    model_dir = CASE_DIR / case_name / "model"
    cfdrun_files = list(model_dir.glob("*.cfdrun"))
    if cfdrun_files:
        return cfdrun_files[0]
    json_files = list(model_dir.glob("*.json"))
    if json_files:
        return json_files[0]
    raise FileNotFoundError(f"No .cfdrun manifest in {model_dir}")


def _check_solver_log(case_path: Path) -> dict:
    """Parse the latest solver log for NaN and final time step."""
    log_dir = case_path / "log"
    if not log_dir.exists():
        return {"last_time": 0, "nan_count": 0}
    runs = sorted(log_dir.iterdir(), key=lambda p: p.name)
    if not runs:
        return {"last_time": 0, "nan_count": 0}
    latest = runs[-1]
    solver_log = latest / "log.mpi_solver"
    if not solver_log.exists():
        return {"last_time": 0, "nan_count": 0}
    text = solver_log.read_text(errors="replace")
    times = re.findall(r"^Time = (\d+)", text, re.MULTILINE)
    last_time = int(times[-1]) if times else 0
    nan_count = text.count("Nan")
    return {"last_time": last_time, "nan_count": nan_count}


@pytest.mark.slow
class TestSolverConvergence:

    @pytest.mark.parametrize("case_name", ["triangle_room", "slab_floor"])
    def test_small_case_no_nan(self, case_name):
        """Small cases must complete without NaN."""
        cfdrun = _find_cfdrun(case_name)
        cmd = [PYTHON, "-m", "dctwin.runner.run_cfd", "--from-cfdrun", str(cfdrun)]
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=600, cwd=str(CASE_DIR.parent))
        result = _check_solver_log(CASE_DIR / case_name)
        assert result["nan_count"] == 0, f"{case_name}: {result['nan_count']} NaN detected"
        assert proc.returncode == 0, f"{case_name}: exit code {proc.returncode}"
