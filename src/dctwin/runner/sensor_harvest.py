"""Runner-side sensor harvest (2026-04-19).

Mirror of ``STLGeneratorForOpenFoam/server/server.py::handle_harvest_sensor_results``
so that the CLI flow (``python -m dctwin.runner.run_cfd --from-cfdrun …``)
auto-writes the same ``{run_dir}/sensorResults/{name}_sensor_HH-MM-SS.json``
the browser flow produces. Previously this JSON was only written when the
UI's "收集传感器结果" button fired ``/api/harvest-sensor-results``.

The module is intentionally kept pure-Python/stdlib so the CLI path picks it
up without touching STLGenerator server code — any future change must stay in
lock-step with the server endpoint (see server.py comments in that handler).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple, Union

from loguru import logger

Number = Union[int, float]
ScalarRow = List[float]
VectorRow = List[List[float]]

_OUT_OF_MESH_SENTINEL = -1e299


def parse_probes_file(
    path: Path,
    *,
    is_vector: bool,
    since: int = -1,
) -> Tuple[List[int], List[Union[ScalarRow, VectorRow]]]:
    """Parse an OpenFOAM probes FO output file.

    Returns ``(steps, values)`` where rows with ``step <= since`` are
    dropped. Missing / empty files return ``([], [])``. Scalar rows are
    ``list[float]`` (one entry per probe). Vector rows are
    ``list[list[float]]`` (each entry a 3-vector).
    """
    p = Path(path)
    if not p.exists():
        return [], []
    text = p.read_text(encoding="utf-8", errors="ignore")
    steps: List[int] = []
    values: List[Union[ScalarRow, VectorRow]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if is_vector:
            vecs = re.findall(r"\(([^)]+)\)", line)
            if not vecs:
                continue
            head = line.split("(", 1)[0].strip()
            try:
                t = float(head)
            except ValueError:
                continue
            step = int(t) if t == int(t) else t
            if step <= since:
                continue
            row = [[float(x) for x in v.split()] for v in vecs]
            steps.append(step)
            values.append(row)
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            try:
                t = float(parts[0])
            except ValueError:
                continue
            step = int(t) if t == int(t) else t
            if step <= since:
                continue
            try:
                row = [float(x) for x in parts[1:]]
            except ValueError:
                continue
            steps.append(step)
            values.append(row)
    return steps, values


def find_latest_run_dir(case_dir: Path) -> Optional[Path]:
    """Return newest ``<case>/log/<run>/`` containing ``constant/polyMesh``."""
    log_dir = Path(case_dir) / "log"
    if not log_dir.exists():
        return None
    for d in sorted(log_dir.iterdir(), reverse=True):
        if not d.is_dir() or d.name.startswith("."):
            continue
        if (d / "constant" / "polyMesh").exists():
            return d
    return None


def read_write_interval(run_dir: Path) -> int:
    """Parse ``writeInterval`` from ``run_dir/system/controlDict`` probes subdict."""
    cd = Path(run_dir) / "system" / "controlDict"
    if not cd.exists():
        return 10
    txt = cd.read_text(encoding="utf-8", errors="ignore")
    m = re.search(
        r"probes\s*\{[^}]*?writeInterval\s+(\d+)\s*;",
        txt,
        re.DOTALL,
    )
    return int(m.group(1)) if m else 10


def parse_probe_locations(run_dir: Path) -> List[List[float]]:
    """Extract ``probeLocations`` from ``run_dir/system/controlDict``."""
    cd = Path(run_dir) / "system" / "controlDict"
    if not cd.exists():
        return []
    txt = cd.read_text(encoding="utf-8", errors="ignore")
    m = re.search(r"probeLocations\s*\(\s*(.*?)\s*\)\s*;", txt, re.DOTALL)
    if not m:
        return []
    locs: List[List[float]] = []
    for tup in re.findall(
        r"\(\s*([\d.eE+-]+)\s+([\d.eE+-]+)\s+([\d.eE+-]+)\s*\)",
        m.group(1),
    ):
        locs.append([float(x) for x in tup])
    return locs


def convert_temp_from_k(value_k: float, unit: str) -> float:
    """Kelvin → requested unit. ``unit`` in {``K``, ``C``, ``F``}; default ``C``."""
    if unit == "K":
        return value_k
    if unit == "F":
        return (value_k - 273.15) * 9.0 / 5.0 + 32.0
    return value_k - 273.15


def _pick(row: Optional[list], i: int):
    """Return ``row[i]`` but map the OF out-of-mesh sentinel to None."""
    if row is None or i >= len(row):
        return None
    val = row[i]
    if isinstance(val, (int, float)) and val < _OUT_OF_MESH_SENTINEL:
        return None
    if isinstance(val, list) and val and all(
        isinstance(v, (int, float)) and v < _OUT_OF_MESH_SENTINEL for v in val
    ):
        return None
    return val


def harvest_sensor_results(
    case_dir: Path,
    *,
    case_name: str,
    sensor_names: List[str],
    unit_temperature: str = "C",
) -> Optional[Path]:
    """Write ``{run_dir}/sensorResults/{case_name}_sensor_HH-MM-SS.json``.

    Returns the output path, or ``None`` if no run_dir / probe data exists
    (solve never ran or probes FO not emitted). Raises ``ValueError`` when
    ``len(sensor_names)`` doesn't match the probe count in the T file
    (programmer error — runner must pass the same list it used to author
    ``controlDict``).
    """
    case_dir = Path(case_dir)
    run_dir = find_latest_run_dir(case_dir)
    if run_dir is None:
        logger.info(
            f"Sensor harvest skipped: no run_dir under {case_dir}/log"
        )
        return None

    probes_root = run_dir / "postProcessing" / "probes"
    if not probes_root.exists():
        logger.info(
            f"Sensor harvest skipped: no postProcessing/probes in {run_dir.name}"
        )
        return None
    time_dirs = sorted(d for d in probes_root.iterdir() if d.is_dir())
    if not time_dirs:
        logger.info(
            f"Sensor harvest skipped: empty postProcessing/probes in {run_dir.name}"
        )
        return None
    t0 = time_dirs[0]

    required = {"T", "U"}
    fields_last = {}
    last_step: Optional[int] = None
    for name in ("T", "U", "p", "p_rgh", "rho"):
        is_vec = name == "U"
        steps, values = parse_probes_file(t0 / name, is_vector=is_vec, since=-1)
        if not steps:
            if name in required:
                logger.warning(
                    f"Sensor harvest aborted: no data in probes/{name}"
                )
                return None
            fields_last[name] = None
            continue
        fields_last[name] = values[-1]
        if last_step is None:
            last_step = steps[-1]

    n_columns = len(fields_last["T"])
    if len(sensor_names) != n_columns:
        raise ValueError(
            f"sensor count mismatch: sensor_names={len(sensor_names)}, "
            f"probes={n_columns} (probes file column count)"
        )

    locations = parse_probe_locations(run_dir)

    sensors = []
    for i, name in enumerate(sensor_names):
        t_k = fields_last["T"][i]
        if isinstance(t_k, (int, float)) and t_k < _OUT_OF_MESH_SENTINEL:
            t_final = None
        else:
            t_final = convert_temp_from_k(t_k, unit_temperature)
        sensors.append(
            {
                "name": name,
                "location": locations[i] if i < len(locations) else None,
                "T_final": t_final,
                "U_final": _pick(fields_last.get("U"), i),
                "p_final": _pick(fields_last.get("p"), i),
                "p_rgh_final": _pick(fields_last.get("p_rgh"), i),
                "rho_final": _pick(fields_last.get("rho"), i),
            }
        )

    now = datetime.now()
    out_path = (
        run_dir
        / "sensorResults"
        / f"{case_name}_sensor_{now.strftime('%H-%M-%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "caseName": case_name,
        "solveRunDir": str(run_dir.relative_to(case_dir)),
        "finalStep": last_step,
        "writeInterval": read_write_interval(run_dir),
        "unitLength": "m",
        "unitTemperature": unit_temperature,
        "generatedAt": now.isoformat(timespec="seconds"),
        "sensors": sensors,
    }
    out_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info(
        f"Sensor harvest wrote {len(sensors)} sensors → "
        f"{out_path.relative_to(case_dir)}"
    )
    return out_path
