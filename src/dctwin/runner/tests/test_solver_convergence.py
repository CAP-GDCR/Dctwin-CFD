"""Integration tests: verify solver runs 100 steps without NaN/divergence.

These are end-to-end tests that exercise the full pipeline:
cfd_export.json + STL files → mesh (if needed) → solve 100 steps → check no NaN.
"""
import json
import subprocess
import sys
import pytest
from pathlib import Path

CASE_DIR = Path("/mnt/lv/litianyu/mycode/dctwin/case")
PYTHON = sys.executable


def _run_solve(case_name: str, steps: int = 100, timeout: int = 600) -> dict:
    """Run solve-only for a case and return result dict."""
    case = CASE_DIR / case_name
    cfd_json = case / "model" / "cfd_export.json"
    mesh_points = case / "Mesh" / "constant" / "polyMesh" / "points"

    if not cfd_json.exists():
        return {"ok": False, "reason": f"cfd_export.json not found: {cfd_json}"}
    if not mesh_points.exists():
        return {"ok": False, "reason": f"No mesh: {mesh_points}"}

    # Patch endTime to `steps` and force serial
    with open(cfd_json) as f:
        d = json.load(f)
    d.setdefault("simulationConfig", {})["endTime"] = steps
    d["simulationConfig"]["writeInterval"] = steps
    d["simulationConfig"]["solveProcess"] = 1
    with open(cfd_json, "w") as f:
        json.dump(d, f, indent=2)

    cmd = [PYTHON, "-m", "dctwin.runner.run_cfd",
           "--from-stlgen", str(cfd_json), "--solve-only"]
    proc = subprocess.run(cmd, cwd=str(case), capture_output=True,
                          text=True, timeout=timeout)

    stdout = proc.stdout + proc.stderr
    has_nan = "nan" in stdout.lower() and "Initial residual = nan" in stdout
    has_fatal = "FOAM FATAL" in stdout

    # Check for time-step output
    log_dir = case / "log"
    latest_run = sorted(log_dir.glob("*")) if log_dir.exists() else []
    solved_to = None
    if latest_run:
        for td in sorted(latest_run[-1].iterdir()):
            if td.is_dir() and td.name.isdigit() and int(td.name) > 0:
                solved_to = int(td.name)

    return {
        "ok": proc.returncode == 0 and not has_nan and not has_fatal,
        "returncode": proc.returncode,
        "has_nan": has_nan,
        "has_fatal": has_fatal,
        "solved_to": solved_to,
        "reason": "diverged (NaN)" if has_nan else
                  ("FOAM FATAL" if has_fatal else
                   (f"exit code {proc.returncode}" if proc.returncode != 0 else "OK")),
    }


class TestSolverConvergence:
    """Verify that STDCT and GTC cases solve 100 steps without divergence."""

    @pytest.mark.slow
    def test_stdct_solves_100_steps(self):
        """STDCT must solve 100 steps without NaN or FOAM FATAL errors."""
        result = _run_solve("STDCT", steps=100, timeout=600)
        assert result["ok"], (
            f"STDCT diverged: {result['reason']}. "
            f"solved_to={result.get('solved_to')}, "
            f"has_nan={result.get('has_nan')}"
        )
        assert result["solved_to"] == 100, (
            f"STDCT only solved to t={result['solved_to']}, expected 100"
        )

    @pytest.mark.slow
    def test_gtc_solves_100_steps(self):
        """GTC must solve 100 steps without NaN or FOAM FATAL errors."""
        result = _run_solve("GTC", steps=100, timeout=900)
        assert result["ok"], (
            f"GTC diverged: {result['reason']}. "
            f"solved_to={result.get('solved_to')}, "
            f"has_nan={result.get('has_nan')}"
        )
        assert result["solved_to"] == 100, (
            f"GTC only solved to t={result['solved_to']}, expected 100"
        )
