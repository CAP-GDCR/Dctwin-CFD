"""
STLGenRunner — Direct STL pipeline for STLGeneratorForOpenFoam.

This runner uses browser-exported STL files directly instead of re-generating
geometry from JSON parametric descriptions.  dctwin's internal STL generator is
completely bypassed.

Workflow
--------
1.  Read cfd_export.json  (model/cfd_export.json)
2.  Find STL files in     Mesh/constant/triSurface/
3.  Parse solid names from ASCII STL headers
4.  Generate OpenFOAM mesh dictionaries:
      blockMeshDict, surfaceFeatureExtractDict, snappyHexMeshDict
    Leakage solids → faceType internal + cellZone (not walls)
5.  Run serial mesh pipeline:
      blockMesh → surfaceFeatureExtract → snappyHexMesh -overwrite
6.  Build bridge_bc.json from cfd_export.json physics
7.  Prepare a runnable OpenFOAM case directly from STLGen data
8.  Run solver directly from the STLGen-derived case
"""

import datetime
import glob as _glob
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from loguru import logger

from dctwin.runner.cfdproj_schema import CFDExport
from dctwin.runner.fields import FieldGenerator, STANDARD_FIELD_NAMES
from dctwin.runner.openfoam_case import (
    copy_system_files_for_solver,
    fix_cyclic_match_tolerance,
    generate_control_dict,
    generate_decompose_par_dict,
    generate_fv_options,
    generate_fv_schemes,
    generate_fv_solution,
    read_boundary_patches,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize(name: str) -> str:
    """Same sanitization as HTML tool and bridge_bc.py."""
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _darcy_forchheimer(sigma: float, depth: float) -> Tuple[float, float]:
    """Compute Darcy (d) and Forchheimer (f) coefficients for rack leakage.

    Uses orifice/perforated-plate model (see AGENTS.md §7.3):
        f = (1/L) × ((1/(Cd×σ)) - 1)²
    where L = rack depth, Cd = 0.6 (discharge coefficient), σ = open area ratio.
    Darcy (viscous) term is set to zero — leakage flow is turbulent.
    """
    sigma = max(0.01, min(0.99, sigma))
    if depth < 0.1:
        logger.warning(f"Leakage depth={depth:.4f} is suspiciously small (<0.1m), "
                       f"clamping to 0.1 — check rack geometry 'D' dimension")
        depth = 0.1
    cd = 0.6
    f_coeff = (1.0 / depth) * ((1.0 / (cd * sigma)) - 1.0) ** 2
    d_coeff = 0.0
    return d_coeff, f_coeff


def _read_stl_solid_names(stl_path: Path) -> List[str]:
    """Parse `solid <name>` lines from an ASCII STL file and return solid names."""
    names: List[str] = []
    try:
        with open(stl_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped.startswith("solid "):
                    name = stripped[6:].strip()
                    if name and name not in names:
                        names.append(name)
    except Exception as exc:
        logger.warning(f"Could not read STL solid names from {stl_path}: {exc}")
    return names


Vec3 = Tuple[float, float, float]
Triangle = Tuple[Vec3, Vec3, Vec3]

_GEOM_EPS = 1e-9
_PLANAR_TOL = 1e-5
_RAY_START_EPS = 1e-6
_SEED_DEDUPE_TOL = 1e-6


@dataclass(frozen=True)
class _PatchSeedSpec:
    zone_name: str
    solid_name: str
    stl_path: Path
    patch_kind: str
    seed_config: Optional[Dict[str, Any]] = None


def _vec_add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _vec_sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _vec_scale(v: Vec3, scale: float) -> Vec3:
    return (v[0] * scale, v[1] * scale, v[2] * scale)


def _vec_dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _vec_cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _vec_norm(v: Vec3) -> float:
    return math.sqrt(_vec_dot(v, v))


def _vec_normalize(v: Vec3) -> Vec3:
    norm = _vec_norm(v)
    if norm <= _GEOM_EPS:
        raise RuntimeError("Cannot normalize zero-length vector")
    return (v[0] / norm, v[1] / norm, v[2] / norm)


def _triangle_centroid(triangle: Triangle) -> Vec3:
    a, b, c = triangle
    return (
        (a[0] + b[0] + c[0]) / 3.0,
        (a[1] + b[1] + c[1]) / 3.0,
        (a[2] + b[2] + c[2]) / 3.0,
    )


def _triangle_area_and_normal(triangle: Triangle) -> Tuple[float, Vec3]:
    a, b, c = triangle
    cross = _vec_cross(_vec_sub(b, a), _vec_sub(c, a))
    area = 0.5 * _vec_norm(cross)
    return area, cross


def _format_vec(vec: Vec3) -> str:
    return f"({vec[0]:.6f}, {vec[1]:.6f}, {vec[2]:.6f})"


def _read_ascii_stl_triangles(stl_path: Path) -> Dict[str, List[Triangle]]:
    """Parse triangles grouped by solid name from an ASCII STL file."""
    solids: Dict[str, List[Triangle]] = {}
    current_name: Optional[str] = None
    vertices: List[Vec3] = []

    with open(stl_path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            stripped = line.strip()
            if stripped.startswith("solid "):
                current_name = stripped[6:].strip()
                if current_name:
                    solids.setdefault(current_name, [])
                vertices = []
                continue
            if stripped.startswith("endsolid"):
                current_name = None
                vertices = []
                continue
            if current_name and stripped.startswith("vertex "):
                parts = stripped.split()
                if len(parts) >= 4:
                    vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
                    if len(vertices) == 3:
                        solids[current_name].append((vertices[0], vertices[1], vertices[2]))
                        vertices = []
    return solids


def _compute_triangle_surface_centroid(triangles: Iterable[Triangle]) -> Optional[Vec3]:
    total_area = 0.0
    weighted = (0.0, 0.0, 0.0)
    for triangle in triangles:
        area, _ = _triangle_area_and_normal(triangle)
        if area <= _GEOM_EPS:
            continue
        centroid = _triangle_centroid(triangle)
        weighted = _vec_add(weighted, _vec_scale(centroid, area))
        total_area += area
    if total_area <= _GEOM_EPS:
        return None
    return _vec_scale(weighted, 1.0 / total_area)


def _compute_planar_patch_geometry(
    triangles: Iterable[Triangle],
    planar_tol: float = _PLANAR_TOL,
) -> Tuple[Vec3, Vec3]:
    """Return an area-weighted center and unit normal for a near-planar patch."""
    tri_list = list(triangles)
    if not tri_list:
        raise RuntimeError("Patch STL contains no triangles")

    center = _compute_triangle_surface_centroid(tri_list)
    if center is None:
        raise RuntimeError("Patch STL contains only degenerate triangles")

    total_normal = (0.0, 0.0, 0.0)
    vertices: List[Vec3] = []
    for triangle in tri_list:
        area, cross = _triangle_area_and_normal(triangle)
        if area <= _GEOM_EPS:
            continue
        total_normal = _vec_add(total_normal, cross)
        vertices.extend(triangle)

    if _vec_norm(total_normal) <= _GEOM_EPS:
        raise RuntimeError("Patch STL has zero net normal and cannot define a seed plane")

    normal = _vec_normalize(total_normal)
    max_deviation = max(abs(_vec_dot(_vec_sub(vertex, center), normal)) for vertex in vertices)
    if max_deviation > planar_tol:
        raise RuntimeError(
            f"Patch STL is not planar enough for dual-side seed placement "
            f"(max deviation {max_deviation:.6g} > tolerance {planar_tol:.6g})"
        )
    return center, normal


def _ray_triangle_intersection(origin: Vec3, direction: Vec3, triangle: Triangle) -> Optional[float]:
    """Return ray distance to the triangle using Moller-Trumbore, or None if no hit."""
    v0, v1, v2 = triangle
    edge1 = _vec_sub(v1, v0)
    edge2 = _vec_sub(v2, v0)
    pvec = _vec_cross(direction, edge2)
    det = _vec_dot(edge1, pvec)
    if abs(det) <= _GEOM_EPS:
        return None
    inv_det = 1.0 / det
    tvec = _vec_sub(origin, v0)
    u = _vec_dot(tvec, pvec) * inv_det
    if u < 0.0 or u > 1.0:
        return None
    qvec = _vec_cross(tvec, edge1)
    v = _vec_dot(direction, qvec) * inv_det
    if v < 0.0 or u + v > 1.0:
        return None
    distance = _vec_dot(edge2, qvec) * inv_det
    if distance <= _GEOM_EPS:
        return None
    return distance


def _nearest_ray_clearance(
    center: Vec3,
    direction: Vec3,
    scene_triangles: Iterable[Tuple[str, str, Triangle]],
    ignored_surface: Tuple[str, str],
) -> Optional[float]:
    origin = _vec_add(center, _vec_scale(direction, _RAY_START_EPS))
    nearest: Optional[float] = None
    for filename, solid_name, triangle in scene_triangles:
        if (filename, solid_name) == ignored_surface:
            continue
        hit = _ray_triangle_intersection(origin, direction, triangle)
        if hit is None:
            continue
        clearance = hit + _RAY_START_EPS
        if nearest is None or clearance < nearest:
            nearest = clearance
    return nearest


def _dedupe_seed_entries(entries: Iterable[Tuple[Vec3, str]], tol: float = _SEED_DEDUPE_TOL) -> List[Tuple[Vec3, str]]:
    deduped: List[Tuple[Vec3, str]] = []
    for point, zone_name in entries:
        if any(
            existing_zone == zone_name
            and math.isclose(point[0], existing_point[0], abs_tol=tol)
            and math.isclose(point[1], existing_point[1], abs_tol=tol)
            and math.isclose(point[2], existing_point[2], abs_tol=tol)
            for existing_point, existing_zone in deduped
        ):
            continue
        deduped.append((point, zone_name))
    return deduped


def _closest_point_on_triangle(point: Vec3, triangle: Triangle) -> Vec3:
    """Return the closest point on a triangle to an arbitrary point."""
    a, b, c = triangle
    ab = _vec_sub(b, a)
    ac = _vec_sub(c, a)
    ap = _vec_sub(point, a)

    d1 = _vec_dot(ab, ap)
    d2 = _vec_dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return a

    bp = _vec_sub(point, b)
    d3 = _vec_dot(ab, bp)
    d4 = _vec_dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return b

    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        return _vec_add(a, _vec_scale(ab, v))

    cp = _vec_sub(point, c)
    d5 = _vec_dot(ab, cp)
    d6 = _vec_dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return c

    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        return _vec_add(a, _vec_scale(ac, w))

    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        bc = _vec_sub(c, b)
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return _vec_add(b, _vec_scale(bc, w))

    denom = 1.0 / (va + vb + vc)
    v = vb * denom
    w = vc * denom
    return _vec_add(a, _vec_add(_vec_scale(ab, v), _vec_scale(ac, w)))


def _project_point_onto_plane(point: Vec3, plane_point: Vec3, plane_normal: Vec3) -> Vec3:
    distance = _vec_dot(_vec_sub(point, plane_point), plane_normal)
    return _vec_sub(point, _vec_scale(plane_normal, distance))


def _project_point_onto_patch(point: Vec3, triangles: Iterable[Triangle]) -> Vec3:
    """Project a point onto the nearest point on the patch triangles."""
    nearest_point: Optional[Vec3] = None
    nearest_dist2: Optional[float] = None
    for triangle in triangles:
        candidate = _closest_point_on_triangle(point, triangle)
        delta = _vec_sub(candidate, point)
        dist2 = _vec_dot(delta, delta)
        if nearest_dist2 is None or dist2 < nearest_dist2:
            nearest_dist2 = dist2
            nearest_point = candidate
    if nearest_point is None:
        raise RuntimeError("Patch STL contains no valid triangles for seed anchor projection")
    return nearest_point


def _compute_stl_solid_centroid(stl_path: Path, solid_name: str) -> Optional[Tuple[float, float, float]]:
    """Compute an approximate geometric center from a named STL solid surface."""
    try:
        solids = _read_ascii_stl_triangles(stl_path)
    except Exception:
        return None
    triangles = solids.get(solid_name, [])
    return _compute_triangle_surface_centroid(triangles)


def _dict_to_vec3(value: Optional[Dict[str, Any]], fallback: Vec3) -> Vec3:
    if not isinstance(value, dict):
        return fallback
    try:
        return (
            float(value.get("x", fallback[0])),
            float(value.get("y", fallback[1])),
            float(value.get("z", fallback[2])),
        )
    except (TypeError, ValueError):
        return fallback


def _resolve_patch_seed_config(
    seed_config: Optional[Dict[str, Any]],
    center: Vec3,
    normal: Vec3,
    default_offset: float,
) -> Tuple[Vec3, Vec3, float]:
    anchor = center
    direction = normal
    offset = default_offset
    if isinstance(seed_config, dict) and seed_config.get("enabled", True) is not False:
        anchor = _dict_to_vec3(seed_config.get("anchor"), center)
        direction = _dict_to_vec3(seed_config.get("direction"), normal)
        try:
            offset = float(seed_config.get("offset", default_offset))
        except (TypeError, ValueError):
            offset = default_offset
    try:
        direction = _vec_normalize(direction)
    except RuntimeError:
        direction = normal
    if offset <= _GEOM_EPS:
        offset = default_offset
    return anchor, direction, offset



# ---------------------------------------------------------------------------
# Main runner class
# ---------------------------------------------------------------------------

class STLGenRunner:
    """Run the stlgen (direct STL) pipeline for a CFD case."""

    def __init__(self, case_dir: Path, cfd_export_path: Optional[Path] = None) -> None:
        self.case_dir = Path(case_dir).resolve()
        self.mesh_dir = self.case_dir / "Mesh"
        self.model_dir = self.case_dir / "model"
        self.tri_surface_dir = self.mesh_dir / "constant" / "triSurface"

        if cfd_export_path is None:
            cfd_export_path = self.model_dir / "cfd_export.json"
        self.cfd_export_path = Path(cfd_export_path).resolve()

        self._data: Optional[Dict[str, Any]] = None   # parsed cfd_export.json
        self._stl_solids: Dict[str, List[str]] = {}   # {filename: [solidName, …]}
        self._baffle_physics: Dict[str, Dict] = {}   # {sanitized_solid: {baffleType, porosity}}
        self._wall_baffle_zone_names: set = set()     # populated by _generate_create_baffles_dict()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_geometry(self) -> None:
        """Pre-flight check: ensure room.height is consistent with floor z-coordinates.

        Raises RuntimeError if any floor's anchor z exceeds room.height, which would
        cause the room enclosure walls to be shorter than the geometry, leading to
        unclosed mesh domains and baffle seed placement failures.
        """
        if self._data is None:
            self._load_cfd_json_only()
        room_height = self._data.get("room", {}).get("height", 0)
        _TOLERANCE = 1e-3  # allow sub-mm floating-point rounding from JS export
        for name, floor in self._data.get("floors", {}).items():
            z = floor.get("geometry", {}).get("anchor", {}).get("z", 0)
            if z > room_height + _TOLERANCE:
                raise RuntimeError(
                    f"floor '{name}' z={z} exceeds room.height={room_height}. "
                    f"The room enclosure (walls) only extends to z={room_height}, "
                    f"but this floor is placed above it. "
                    f"Update the room height in the 2D floor plan (房间 2D 平面设计 → 房间高度) "
                    f"to at least {z}."
                )

    def _load_cfd_json_only(self) -> None:
        """Load and validate cfd_export.json without requiring STL files."""
        if not self.cfd_export_path.exists():
            raise FileNotFoundError(f"cfd_export.json not found: {self.cfd_export_path}")
        with open(self.cfd_export_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        from dctwin.runner.cfdproj_schema import CFDExport
        validated = CFDExport.model_validate(raw)
        self._data = validated.model_dump(mode="json")

    def run_mesh_only(self) -> Path:
        """Generate mesh + complete OpenFOAM case in Mesh/ directory."""
        logger.info("=== STLGen: Mesh-Only Mode ===")
        self._load()
        self.validate_geometry()
        self._ensure_watertight_room_enclosure()
        self._prepare_runtime_metadata()
        self._generate_mesh_dicts()
        # Dispatch to serial or parallel mesh pipeline based on meshConfig
        mc = self._data.get("meshConfig", {})
        parallel_mesh = mc.get("parallelMesh", False)
        n_procs = mc.get("meshProcess", 1)
        if parallel_mesh and n_procs > 1:
            logger.info(f"Parallel mesh enabled: {n_procs} processes")
            self._run_parallel_mesh_pipeline(n_procs)
        else:
            logger.info("Serial mesh mode")
            self._run_serial_mesh_pipeline()
        # Generate complete case files in Mesh/ so it's a runnable OpenFOAM case
        self._setup_complete_case_in_mesh()
        foam_file = self.mesh_dir / "case.foam"
        if not foam_file.exists():
            foam_file.touch()
        logger.info(f"Mesh complete. Complete case in {self.mesh_dir}")
        return self.mesh_dir / "constant" / "polyMesh"

    def run_solve_only(self) -> Path:
        """Run solver using complete case in Mesh/ directory."""
        logger.info("=== STLGen: Solve-Only Mode ===")
        self._load()
        self._prepare_runtime_metadata()
        # Always regenerate case files (user may have changed settings)
        self._setup_complete_case_in_mesh()
        return self._run_solve_from_mesh()

    def run_simulation(self) -> Path:
        """Run mesh + solve."""
        logger.info("=== STLGen: Full Simulation Mode ===")
        self._load()
        self.validate_geometry()
        self._prepare_runtime_metadata()
        self._generate_mesh_dicts()
        self._run_serial_mesh_pipeline()
        self._setup_complete_case_in_mesh()
        return self._run_solve_from_mesh()

    def _prepare_runtime_metadata(self) -> None:
        """Refresh STLGen runtime metadata without generating config.toml/room.json."""
        self._cleanup_stale_compat_files()
        self._build_and_save_bridge_bc()

    def _cleanup_stale_compat_files(self) -> None:
        """Remove old STLGen-generated compatibility files from previous runs."""
        config_path = self.case_dir / "config.toml"
        if config_path.exists():
            try:
                config_text = config_path.read_text(encoding="utf-8")
            except Exception:
                config_text = ""
            if config_text.startswith("# Auto-generated by STLGenRunner from cfd_export.json"):
                config_path.unlink()
                logger.info(f"Removed stale STLGen compatibility file: {config_path}")

        room_json_path = self.model_dir / "room.json"
        if room_json_path.exists():
            try:
                room_data = json.loads(room_json_path.read_text(encoding="utf-8"))
            except Exception:
                room_data = {}
            meta = room_data.get("meta", {}) if isinstance(room_data, dict) else {}
            if meta.get("name") == "stlgen_stub":
                room_json_path.unlink()
                logger.info(f"Removed stale STLGen compatibility file: {room_json_path}")

    def _compute_patch_areas(self) -> Dict[str, float]:
        """Compute total surface area for each STL solid from triSurface files.

        Returns dict mapping sanitized solid name → area in m².
        Used by FieldGenerator to compute physically-based k/epsilon inlet BCs.
        """
        areas: Dict[str, float] = {}
        for stl_file in sorted(self.tri_surface_dir.glob("*.stl")):
            try:
                solids = _read_ascii_stl_triangles(stl_file)
            except Exception as exc:
                logger.warning(f"Could not read {stl_file.name} for patch areas: {exc}")
                continue
            for solid_name, triangles in solids.items():
                total_area = 0.0
                for tri in triangles:
                    a, _ = _triangle_area_and_normal(tri)
                    if a > _GEOM_EPS:
                        total_area += a
                if total_area > _GEOM_EPS:
                    areas[_sanitize(solid_name)] = total_area
        return areas

    def _build_field_generator(self) -> FieldGenerator:
        """Build a field generator for STLGen that does not depend on CaseConfig."""
        return FieldGenerator(
            case_dir=self.case_dir,
            simulation_config=self._data.get("simulationConfig", {}),
            room_config={},
            patch_areas=self._compute_patch_areas(),
        )

    def _extract_sensor_points(self) -> List[Dict[str, float]]:
        """Return STLGen sensor points in controlDict probe format."""
        probes = []
        for sensor in self._data.get("sensorPoints", []):
            if not isinstance(sensor, dict):
                continue
            if "x" not in sensor or "y" not in sensor or "z" not in sensor:
                continue
            probes.append(
                {
                    "x": float(sensor["x"]),
                    "y": float(sensor["y"]),
                    "z": float(sensor["z"]),
                }
            )
        return probes

    def _setup_complete_case_in_mesh(self) -> None:
        """Generate case files in Mesh/ EXCEPT controlDict and fvSolution.

        controlDict and fvSolution are generated at solve-time in the run
        directory (log/<timestamp>/) so the user can adjust solver parameters
        between mesh generation and solve execution.

        After this, Mesh/ contains:
        - constant/polyMesh (from mesh generation)
        - constant/g, transportProperties, turbulenceProperties, thermophysicalProperties
        - system/fvSchemes, fvOptions
        - 0/U, 0/T, 0/p_rgh, p, nut, k, epsilon
        """
        mesh_dir = self.mesh_dir

        # 1. Read boundary patches from the generated mesh
        boundary_path = mesh_dir / "constant" / "polyMesh" / "boundary"
        fix_cyclic_match_tolerance(boundary_path)

        # 1b. Ensure wall baffle patches have type=wall (not cyclic) in boundary.
        # createBaffles creates all baffles as cyclic; wall baffles must be
        # converted back to wall type. Use bridge_bc.json as the source of truth.
        self._ensure_wall_baffles_are_wall_type(boundary_path)

        patches = read_boundary_patches(boundary_path)
        logger.info(f"Setting up complete case: {len(patches)} patches")

        # 2. Generate 0/ field files
        field_generator = self._build_field_generator()
        field_generator.generate_fields(mesh_dir, patches)
        logger.info(f"Generated field files in {mesh_dir / '0'}")

        # 2b. Reconcile boundary types: ensure any patch that received wall-function
        # BCs in the field files also has 'type wall' in the boundary file.
        self._reconcile_boundary_wall_types(boundary_path, mesh_dir)

        # 3. Generate system/ files (fvOptions, fvSchemes, constant/ files)
        #    NOTE: controlDict and fvSolution are NOT generated here — they are
        #    generated at solve-time via _generate_solver_dicts()
        leakage_zones = self._build_leakage_fvoptions_config()
        generate_fv_options(mesh_dir, leakage_zones)
        sc = self._data.get("simulationConfig", {})
        copy_system_files_for_solver(
            mesh_dir,
            physical_properties=sc.get("physical_properties", {}),
            gravity=sc.get("gravity", {}),
            room_temperature_c=float(sc.get("roomTemperature", None) or 22.0),
            schemes_config=sc.get("schemes", {}),
        )
        logger.info("Generated system files (fvSchemes, fvOptions, constant/)")

        # 5. Clean old processor dirs, snappyHexMesh artifacts, and stale time dirs
        import glob, shutil as _shutil
        for pdir in sorted(glob.glob(str(mesh_dir / "processor*"))):
            _shutil.rmtree(pdir, ignore_errors=True)
        # Remove snappyHexMesh-generated fields that confuse decomposePar
        for artifact in ["cellLevel", "pointLevel"]:
            p = mesh_dir / "0" / artifact
            if p.exists():
                p.unlink()
        # Remove stale time-step directories from previous solver runs (keep only 0/)
        for entry in mesh_dir.iterdir():
            if entry.is_dir() and entry.name.replace(".", "", 1).isdigit() and entry.name != "0":
                _shutil.rmtree(entry, ignore_errors=True)
                logger.info(f"Removed stale time directory: {entry.name}")

        # 6. If parallel solve requested, run decomposePar and keep processor dirs
        sc = self._data.get("simulationConfig", {})
        logger.info(f"parallelSolve={sc.get('parallelSolve')}, solveProcess={sc.get('solveProcess')}")
        if sc.get("parallelSolve", False):
            n_procs = sc.get("solveProcess", 1)
            if n_procs > 1:
                self._decompose_for_solve(mesh_dir, n_procs)

    def _generate_solver_dicts(self, run_dir: Path) -> None:
        """Generate controlDict and fvSolution in the given run directory.

        Called at solve-time (after copying Mesh/ to log/<timestamp>/) so the
        user's latest solver settings are always used.
        """
        sc = self._data.get("simulationConfig", {})

        # Generate controlDict
        monitoring = sc.get("monitoring", {})
        monitor_patches = [p["patch"] for p in monitoring.get("serverTemperature", [])]
        generate_control_dict(
            run_dir,
            sc,
            probes=self._extract_sensor_points(),
            monitor_patches=monitor_patches,
        )

        # Generate fvSolution with user-specified residual thresholds
        residual_control = sc.get("residualControl", {})
        system_dir = run_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)

        generate_fv_solution(
            system_dir,
            residual_control=residual_control,
            relaxation_factors=sc.get("relaxationFactors", {}),
            n_non_ortho_correctors=int(sc.get("nNonOrthogonalCorrectors", 0)),
        )

        # Regenerate fvSchemes at solve-time (user may have changed preset)
        generate_fv_schemes(system_dir, sc.get("schemes", {}))

        logger.info(f"Generated solver dicts (controlDict, fvSolution, fvSchemes) in {run_dir / 'system'}")

    @staticmethod
    def _reconcile_boundary_wall_types(boundary_path: Path, case_dir: Path) -> None:
        """Ensure boundary file patch types are consistent with field BCs.

        If a patch has wall-function BCs (nutkWallFunction, epsilonWallFunction,
        kqRWallFunction, omegaWallFunction) in ANY 0/ field file, the boundary
        file must declare it as ``type wall``.  Otherwise the solver crashes with
        "Invalid wall function specification — Current patch type is patch".

        This is a safety net: the primary fix is in FieldGenerator which now
        avoids assigning wall functions to flow patches.  But if for any reason
        a mismatch still occurs (e.g. custom patches, future code changes), this
        step corrects the boundary file.
        """
        import re as _re

        WALL_FN_RE = _re.compile(
            r'type\s+(?:nutkWallFunction|nutk?Wall[Ff]unction|'
            r'epsilonWallFunction|kqRWallFunction|omegaWallFunction|'
            r'alphatWallFunction|alphatJayatillekeWallFunction)',
        )

        # Collect all patches that receive wall-function BCs in any field file
        wall_fn_patches: set = set()
        field_dir = case_dir / "0"
        if not field_dir.is_dir():
            return

        for field_file in field_dir.iterdir():
            if not field_file.is_file():
                continue
            try:
                content = field_file.read_text(encoding="utf-8")
            except Exception:
                continue

            # Match:  <patch_name>\n    {\n        type  <wallFunction>;
            for m in _re.finditer(
                r'^\s+(\S+)\s*\n\s*\{\s*\n\s*' + WALL_FN_RE.pattern,
                content, _re.MULTILINE,
            ):
                wall_fn_patches.add(m.group(1))

        if not wall_fn_patches:
            return

        # Read the boundary file and fix patch types
        if not boundary_path.exists():
            return
        boundary_text = boundary_path.read_text(encoding="utf-8")

        fixed_count = 0
        for patch_name in wall_fn_patches:
            # Match:  <patch_name>\n    {\n        type  patch;
            pattern = _re.compile(
                r'(^\s+' + _re.escape(patch_name) + r'\s*\n'
                r'\s*\{\s*\n'
                r'\s*type\s+)patch(\s*;)',
                _re.MULTILINE,
            )
            boundary_text, n = pattern.subn(r'\1wall\2', boundary_text)
            fixed_count += n

        if fixed_count > 0:
            boundary_path.write_text(boundary_text, encoding="utf-8")
            logger.info(
                f"Reconciled boundary types: changed {fixed_count} patches "
                f"from 'type patch' to 'type wall' to match wall-function BCs"
            )

    def _decompose_for_solve(self, case_dir: Path, n_procs: int) -> None:
        """Run decomposePar in the case directory for parallel solving."""
        generate_decompose_par_dict(case_dir, n_procs)

        # Clean old processor dirs
        self._clean_processor_dirs(case_dir)

        logger.info(f"Running decomposePar with {n_procs} processes")
        self._run_cmd("decomposePar", case_dir)

        # Copy constant files to each processor dir
        const_files = ["g", "transportProperties", "turbulenceProperties", "thermophysicalProperties"]
        for pdir in sorted(case_dir.glob("processor*")):
            pconst = pdir / "constant"
            pconst.mkdir(parents=True, exist_ok=True)
            for fname in const_files:
                src = case_dir / "constant" / fname
                if src.exists():
                    import shutil
                    shutil.copy(src, pconst / fname)

        logger.info(f"decomposePar complete: {n_procs} processor directories created")

    def _run_solve_from_mesh(self) -> Path:
        """Copy Mesh/ to log/date/ and run solver."""
        import shutil
        import datetime

        mesh_dir = self.mesh_dir
        log_dir = self.case_dir / "log"
        log_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        case_name = self.case_dir.name
        run_dir = log_dir / f"{timestamp}_{case_name}"

        # Copy Mesh/ to run_dir/, skipping stale time-step directories (keep only 0/)
        def _ignore_stale_time_dirs(directory, contents):
            """Only called for the top-level Mesh/ directory."""
            if Path(directory) == mesh_dir:
                ignored = []
                for name in contents:
                    p = Path(directory) / name
                    if p.is_dir() and name.replace(".", "", 1).isdigit() and name != "0":
                        ignored.append(name)
                        logger.info(f"Skipping stale time directory in copy: {name}")
                return ignored
            return []

        logger.info(f"Copying case from {mesh_dir} to {run_dir}")
        shutil.copytree(mesh_dir, run_dir, dirs_exist_ok=True, ignore=_ignore_stale_time_dirs)

        # Create .foam file
        (run_dir / "case.foam").touch()

        # Generate controlDict and fvSolution at solve-time with latest settings
        self._generate_solver_dicts(run_dir)

        # Setup log file
        log_file = run_dir / f"{timestamp}_{case_name}.log"
        log_handler_id = logger.add(log_file, format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
                                     level="DEBUG", mode="w")

        logger.info(f"Created run directory: {run_dir}")
        logger.info(f"Log file: {log_file}")

        # Detect parallel: check for processor directories
        proc_dirs = sorted(run_dir.glob("processor*"))
        solver = "buoyantBoussinesqSimpleFoam"

        try:
            if proc_dirs:
                n_procs = len(proc_dirs)
                logger.info(f"Running {solver} in parallel with {n_procs} processes")
                self._run_cmd(
                    f"mpirun --oversubscribe -np {n_procs} {solver} -parallel",
                    run_dir
                )
                logger.info("Reconstructing parallel results...")
                self._run_cmd("reconstructPar -latestTime", run_dir)
            else:
                logger.info(f"Running {solver} in serial mode")
                self._run_cmd(solver, run_dir)
        finally:
            try:
                logger.remove(log_handler_id)
            except Exception:
                pass

        logger.info(f"Solve complete")
        logger.info(f"Results in: {run_dir}")
        return run_dir

    def _build_leakage_fvoptions_config(self) -> Dict[str, Any]:
        """Build fvOptions leakage zone config from bridge_bc.json.

        Each leakage zone in bridge_bc.json has key = stlSolidName (e.g.
        Default_Rack_1_u30_u32_leakage_zone).  The actual mesh cellZone
        has the ``leakage_`` prefix (leakage_Default_Rack_1_u30_u32_leakage_zone)
        because snappyHexMesh requires individual STL files with matching
        surface names (see AGENTS.md §6.4).
        """
        bridge_bc_path = self.model_dir / "bridge_bc.json"
        if not bridge_bc_path.exists():
            return {}
        with open(bridge_bc_path, encoding="utf-8") as fh:
            bc_data = json.load(fh)

        # Build lookup: stlSolidName → (rack_orientation, rack_depth)
        data = self._data
        leakage_rack_info: Dict[str, Tuple[float, float]] = {}
        for rack in data.get("racks", {}).values():
            geom = rack.get("geometry", {})
            ori = geom.get("orientation", 0)
            depth = geom.get("size", {}).get("D", 1.0)
            for grp in rack.get("leakage", {}).get("groups", []):
                leakage_rack_info[_sanitize(grp.get("stlSolidName", ""))] = (ori, depth)

        config: Dict[str, Any] = {}

        for solid_name, bc_spec in bc_data.items():
            if bc_spec.get("bc_type") != "leakage_zone":
                continue
            sigma = bc_spec.get("sigma", 0.1)

            # Get rack orientation and depth for this leakage zone
            ori, depth = leakage_rack_info.get(solid_name, (0, 1.0))
            d_coeff, f_coeff = _darcy_forchheimer(sigma, depth)

            # cellZone name has leakage_ prefix (added during STL splitting)
            san = _sanitize(solid_name)
            cellzone_name = f"leakage_{san}"

            # Skip if leakage STL doesn't exist (no cellZone was created)
            lk_stl = self.tri_surface_dir / f"{cellzone_name}.stl"
            if not lk_stl.exists():
                logger.warning(f"Leakage STL {cellzone_name}.stl not found, skipping fvOptions entry")
                continue

            centroid = _compute_stl_solid_centroid(lk_stl, cellzone_name)
            if centroid:
                origin = f"({centroid[0]:.6f} {centroid[1]:.6f} {centroid[2]:.6f})"
            else:
                origin = "(0 0 0)"

            # Resistance along rack front-to-back axis via coordinateSystem.
            # e1 = front normal = (sin(θ), -cos(θ), 0), supports arbitrary angles.
            # Coefficients in LOCAL coords: f along e1 only.
            theta_rad = math.radians(ori)
            e1x = round(math.sin(theta_rad), 10)
            e1y = round(-math.cos(theta_rad), 10)
            e2x = round(math.cos(theta_rad), 10)
            e2y = round(math.sin(theta_rad), 10)
            d_vec = f"({d_coeff:.6g} 0 0)"
            f_vec = f"({f_coeff:.6g} 0 0)"

            config[cellzone_name] = type("ZoneCfg", (), {
                "cellzone_name": cellzone_name,
                "sigma": sigma,
                "d": d_vec,
                "f": f_vec,
                "origin": origin,
                "e1": f"({e1x:.6g} {e1y:.6g} 0)",
                "e2": f"({e2x:.6g} {e2y:.6g} 0)",
            })()
            logger.info(f"Leakage fvOptions: {cellzone_name}, sigma={sigma}, "
                        f"orientation={ori}, depth={depth}, f={f_coeff:.4g}")

        return config

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _ensure_watertight_room_enclosure(self) -> None:
        """Regenerate room_wall.stl and merged_floor_ceiling.stl from room data
        to ensure they form a watertight enclosure with shared vertices.

        Supports arbitrary polygon rooms (triangles, L-shapes, etc.) by
        extruding the polygon along Z to form a prismatic enclosure.
        Floor and ceiling are triangulated using ear-clipping.
        """
        import math

        data = self._data
        room = data.get("room", {})
        plane = room.get("plane", [])
        height = room.get("height", 0)
        if len(plane) < 3 or height <= 0:
            return

        # Extract polygon vertices in order
        pts = [(p["x"], p["y"]) for p in plane]
        n = len(pts)
        z0, z1 = 0.0, height

        def _tri(v0, v1, v2, nx, ny, nz):
            return (f"  facet normal {nx} {ny} {nz}\n"
                    f"    outer loop\n"
                    f"      vertex {v0[0]} {v0[1]} {v0[2]}\n"
                    f"      vertex {v1[0]} {v1[1]} {v1[2]}\n"
                    f"      vertex {v2[0]} {v2[1]} {v2[2]}\n"
                    f"    endloop\n"
                    f"  endfacet\n")

        wall_solids = self._stl_solids.get("room_wall.stl", [])
        fc_solids = self._stl_solids.get("merged_floor_ceiling.stl", [])

        # --- Walls: one quad (2 triangles) per polygon edge ---
        wall_stl = ""
        for i in range(n):
            j = (i + 1) % n
            ax, ay = pts[i]
            bx, by = pts[j]
            # Wall quad vertices (bottom-left, bottom-right, top-right, top-left)
            bl = (ax, ay, z0)
            br = (bx, by, z0)
            tr = (bx, by, z1)
            tl = (ax, ay, z1)
            # Outward normal (pointing away from polygon interior)
            dx, dy = bx - ax, by - ay
            length = math.sqrt(dx * dx + dy * dy)
            if length < 1e-10:
                continue
            # Normal = perpendicular to edge, pointing outward (right-hand rule for CCW polygon)
            nx, ny, nz = -dy / length, dx / length, 0.0

            name = wall_solids[i] if i < len(wall_solids) else f"Wall_{i+1}"
            wall_stl += f"solid {name}\n"
            wall_stl += _tri(bl, br, tr, nx, ny, nz)
            wall_stl += _tri(bl, tr, tl, nx, ny, nz)
            wall_stl += f"endsolid {name}\n"

        wall_path = self.tri_surface_dir / "room_wall.stl"
        wall_path.write_text(wall_stl, encoding="utf-8")

        # --- Floor and Ceiling: triangulate polygon ---
        # Simple ear-clipping triangulation for arbitrary (convex/concave) polygons
        def _ear_clip(polygon):
            """Ear-clipping triangulation. Returns list of (i, j, k) index tuples."""
            indices = list(range(len(polygon)))
            triangles = []

            def _cross2d(o, a, b):
                return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

            def _point_in_tri(p, a, b, c):
                d1 = _cross2d(p, a, b)
                d2 = _cross2d(p, b, c)
                d3 = _cross2d(p, c, a)
                has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
                has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
                return not (has_neg and has_pos)

            # Ensure CCW winding
            area = sum(_cross2d(polygon[0], polygon[i], polygon[i + 1])
                       for i in range(1, len(polygon) - 1))
            if area < 0:
                indices.reverse()

            max_iter = len(indices) * len(indices)
            iter_count = 0
            while len(indices) > 2 and iter_count < max_iter:
                iter_count += 1
                ear_found = False
                for idx in range(len(indices)):
                    prev_i = indices[(idx - 1) % len(indices)]
                    curr_i = indices[idx]
                    next_i = indices[(idx + 1) % len(indices)]
                    prev_p = polygon[prev_i]
                    curr_p = polygon[curr_i]
                    next_p = polygon[next_i]
                    # Check convex
                    if _cross2d(prev_p, curr_p, next_p) <= 0:
                        continue
                    # Check no other point inside triangle
                    is_ear = True
                    for check_idx in indices:
                        if check_idx in (prev_i, curr_i, next_i):
                            continue
                        if _point_in_tri(polygon[check_idx], prev_p, curr_p, next_p):
                            is_ear = False
                            break
                    if is_ear:
                        triangles.append((prev_i, curr_i, next_i))
                        indices.remove(curr_i)
                        ear_found = True
                        break
                if not ear_found:
                    # Fallback: if stuck, add remaining as triangle (may be degenerate)
                    if len(indices) == 3:
                        triangles.append(tuple(indices))
                        indices.clear()
                    break
            return triangles

        tris = _ear_clip(pts)

        fc_stl = ""
        floor_name = fc_solids[0] if len(fc_solids) > 0 else "Ground_floor"
        fc_stl += f"solid {floor_name}\n"
        for i, j, k in tris:
            # Floor: normal pointing down (-Z)
            fc_stl += _tri(
                (pts[i][0], pts[i][1], z0),
                (pts[k][0], pts[k][1], z0),  # reversed winding for -Z normal
                (pts[j][0], pts[j][1], z0),
                0, 0, -1)
        fc_stl += f"endsolid {floor_name}\n"

        ceil_name = fc_solids[1] if len(fc_solids) > 1 else "Room_top"
        fc_stl += f"solid {ceil_name}\n"
        for i, j, k in tris:
            # Ceiling: normal pointing up (+Z)
            fc_stl += _tri(
                (pts[i][0], pts[i][1], z1),
                (pts[j][0], pts[j][1], z1),
                (pts[k][0], pts[k][1], z1),
                0, 0, 1)
        fc_stl += f"endsolid {ceil_name}\n"

        fc_path = self.tri_surface_dir / "merged_floor_ceiling.stl"
        fc_path.write_text(fc_stl, encoding="utf-8")

        self._stl_solids["room_wall.stl"] = _read_stl_solid_names(wall_path)
        self._stl_solids["merged_floor_ceiling.stl"] = _read_stl_solid_names(fc_path)

        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        logger.info(
            f"Regenerated watertight room enclosure: "
            f"room_wall.stl ({len(self._stl_solids['room_wall.stl'])} walls), "
            f"merged_floor_ceiling.stl ({len(self._stl_solids['merged_floor_ceiling.stl'])} faces), "
            f"polygon={n} verts, z={z0:.2f}-{z1:.2f}, "
            f"bbox=({min(xs):.2f},{min(ys):.2f})-({max(xs):.2f},{max(ys):.2f})"
        )

    def _load(self) -> None:
        """Load cfd_export.json, discover STL solid names, and prepare
        STL files for snappyHexMesh (merge perforated into floor_ceiling,
        split wall vs internal solids into separate files)."""
        if not self.cfd_export_path.exists():
            raise FileNotFoundError(f"cfd_export.json not found: {self.cfd_export_path}")
        with open(self.cfd_export_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        validated = CFDExport.model_validate(raw)
        self._data = validated.model_dump(mode="json")
        logger.info(f"Loaded cfd_export.json: {self.cfd_export_path}")

        # Discover STL files and parse solid names
        # Only consider browser-exported grouped STL files (merged_*.stl + room_wall.stl).
        # Individual per-entity STL files from old pipeline runs are intentionally ignored.
        GROUPED_STL_NAMES = {
            "merged_rack.stl",
            "merged_acu.stl",
            "merged_baffle.stl",

            "merged_floor_ceiling.stl",
            "merged_perforated.stl",
            "merged_box.stl",
            "merged_leakage.stl",
            "room_wall.stl",
        }
        self._stl_solids = {}
        if self.tri_surface_dir.exists():
            for stl_path in sorted(self.tri_surface_dir.glob("*.stl")):
                if stl_path.name not in GROUPED_STL_NAMES:
                    continue
                names = _read_stl_solid_names(stl_path)
                if names:
                    self._stl_solids[stl_path.name] = names
                    logger.info(f"  {stl_path.name}: {len(names)} solids")
        if not self._stl_solids:
            raise FileNotFoundError(
                f"No grouped STL files (merged_*.stl, room_wall.stl) found in {self.tri_surface_dir}. "
                "Start mesh generation from the browser (which uploads STL files first)."
            )

        # Clean up leftover split files from previous attempts
        for leftover in ("merged_floor_ceiling_internal.stl",
                         "merged_floor_ceiling_perforated.stl",
                         "merged_baffle_wall.stl",
                         "room_enclosure.stl",
                         "room_enclosure.eMesh"):
            lp = self.tri_surface_dir / leftover
            if lp.exists():
                lp.unlink()
                self._stl_solids.pop(leftover, None)
        # Restore individual baffle_*.stl files from previous runs back into
        # their source STL files, then delete the individual files.
        if self.tri_surface_dir.exists():
            for bf_file in sorted(self.tri_surface_dir.glob("baffle_*.stl")):
                with open(bf_file, "r", encoding="utf-8") as f:
                    block_text = f.read().strip()
                if not block_text:
                    bf_file.unlink()
                    self._stl_solids.pop(bf_file.name, None)
                    continue
                # Determine source file from solid name prefix
                san = bf_file.stem.replace("baffle_", "", 1)
                # Check if this solid belongs to a floor (merge back to floor_ceiling)
                # or a perforated opening (merge back to merged_perforated.stl)
                # or a rack wallBaffle (merge back to merged_rack.stl)
                # Default: merge back to merged_floor_ceiling.stl
                is_perf = any(san == _sanitize(op["solidName"])
                              for fl in self._data.get("floors", {}).values()
                              for op in fl.get("openings", [])
                              if op.get("physics", {}).get("type") == "perforated")
                # Check if this solid belongs to a baffle model (not a floor)
                is_baffle_model = any(
                    san.startswith(_sanitize(bn) + "_") or san == _sanitize(bn)
                    for bn in self._data.get("baffles", {}).keys()
                )
                # Check if this solid is a rack wallBaffle (merge back to merged_rack.stl)
                is_rack_wall_baffle = any(
                    san == _sanitize(wb)
                    for rack in self._data.get("racks", {}).values()
                    for wb in rack.get("wallBaffleSolids", [])
                )
                if is_perf:
                    target = self.tri_surface_dir / "merged_perforated.stl"
                elif is_baffle_model:
                    target = self.tri_surface_dir / "merged_baffle.stl"
                elif is_rack_wall_baffle:
                    target = self.tri_surface_dir / "merged_rack.stl"
                else:
                    target = self.tri_surface_dir / "merged_floor_ceiling.stl"
                with open(target, "a", encoding="utf-8") as f:
                    f.write("\n" + block_text + "\n")
                # Update stl_solids for target
                names = _read_stl_solid_names(target)
                if names:
                    self._stl_solids[target.name] = names
                bf_file.unlink()
                self._stl_solids.pop(bf_file.name, None)
                logger.info(f"Restored {bf_file.name} → {target.name}")

        # ── Keep room-wall and floor/ceiling STL files as room boundary surfaces ──
        # These STL files define the user-authored room enclosure. snappyHexMesh
        # uses the closed enclosure to remove cells outside the room. Any logic
        # that deletes these files would turn the background blockMesh container
        # into the effective room boundary, which is incorrect.
        if "room_wall.stl" in self._stl_solids:
            logger.info(
                f"room_wall.stl has {len(self._stl_solids['room_wall.stl'])} wall solids: "
                f"{', '.join(self._stl_solids['room_wall.stl'])}"
            )
        if "merged_floor_ceiling.stl" in self._stl_solids:
            logger.info(
                "Keeping merged_floor_ceiling.stl as part of the room enclosure: "
                f"{len(self._stl_solids['merged_floor_ceiling.stl'])} solids"
            )

        # Build baffle physics lookup from cfd_export.json
        self._build_baffle_physics_lookup()

        # ── Split solids that need faceType internal + faceZone into individual STL files ──
        # snappyHexMesh ignores faceType internal for surfaces with named regions
        # (it creates wall patches instead). The only reliable way to get internal faces
        # with per-solid faceZones is to put each solid in its own STL file (no named regions).
        #
        # Groups:
        # 1. merged_baffle.stl: porosity=0 baffle model solids → faceType internal, no faceZone
        # 2. Individual baffle_<solid>.stl: porosity>0 baffle solids → faceType internal + faceZone
        # 3. Individual baffle_<solid>.stl: wallBaffle floor solids → faceType internal + faceZone
        # 4. Individual baffle_<solid>.stl: perforated floor/baffle solids → faceType internal + faceZone
        # 5. Individual baffle_<solid>.stl: rack wallBaffle solids → faceType internal + faceZone
        self._split_internal_face_solids()

    def _build_baffle_physics_lookup(self) -> None:
        """Build lookup mapping sanitized STL solid names to their baffle physics."""
        data = self._data
        baffle_physics: Dict[str, Dict] = {}
        # Sort by name length descending to match longest prefix first
        # (avoids "Baffle_1" matching "Baffle_1_copy")
        sorted_baffles = sorted(
            data.get("baffles", {}).items(),
            key=lambda x: len(x[0]), reverse=True
        )

        # First pass: map exact solid names from patchPhysics
        for baffle_name, baffle in sorted_baffles:
            for solid_name, phys in baffle.get("patchPhysics", {}).items():
                baffle_physics[_sanitize(solid_name)] = phys

        # Second pass: remaining-area solids get model-level physics
        for solid in self._stl_solids.get("merged_baffle.stl", []):
            san_solid = _sanitize(solid)
            if san_solid in baffle_physics:
                continue
            for baffle_name, baffle in sorted_baffles:
                san_bn = _sanitize(baffle_name)
                if san_solid.startswith(san_bn + "_") or san_solid == san_bn:
                    model_phys = baffle.get("physics", {})
                    baffle_physics[san_solid] = {
                        "baffleType": model_phys.get("baffleType", "wall"),
                        "porosity": model_phys.get("porosity", 0)
                    }
                    break

        self._baffle_physics = baffle_physics

    def _split_internal_face_solids(self) -> None:
        """Extract solids that need faceType internal + faceZone into individual STL files.

        snappyHexMesh ignores per-region faceType internal when named regions exist
        in the geometry section. Each solid that needs its own faceZone must be in
        a separate STL file (no named regions → surface-level faceType internal works).

        Solids that need faceZone:
        - wallBaffle floor solids (createBaffles cyclic → post-process to wall)
        - perforated floor openings (createBaffles cyclic)
        - porosity>0 baffle model solids (createBaffles cyclic)

        Solids that stay in merged_baffle.stl (no faceZone):
        - porosity=0 baffle model solids (just internal face, no createBaffles)
        """
        data = self._data

        # Identify which solids need individual faceZones
        needs_facezone: Dict[str, str] = {}  # {sanitized_name: source_stl}

        # wallBaffle floor solids AND perforated openings need extraction to
        # individual files (per-region faceType is ignored in multi-region STLs).
        fc_key = "merged_floor_ceiling.stl"
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            san_fn = _sanitize(floor_name)
            op_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            # wallBaffle remaining area solids → extract for faceType baffle
            if default_bc == "wallBaffle":
                for s in self._stl_solids.get(fc_key, []):
                    san = _sanitize(s)
                    if san.startswith(san_fn) and san not in op_names:
                        needs_facezone[san] = fc_key
            # perforated floor openings → extract for faceType internal
            for op in floor.get("openings", []):
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type in ("perforated", "wallBaffle"):
                    san = _sanitize(op["solidName"])
                    needs_facezone[san] = fc_key

        # Rack wall-baffle solids (half-dissolved U-slots, from merged_rack.stl)
        rack_key = "merged_rack.stl"
        rack_solid_names = {_sanitize(s) for s in self._stl_solids.get(rack_key, [])}
        for rack_name, rack in data.get("racks", {}).items():
            for wb_solid in rack.get("wallBaffleSolids", []):
                san = _sanitize(wb_solid)
                if san in rack_solid_names:
                    needs_facezone[san] = rack_key
                    logger.info(f"Rack wallBaffle {san} found in {rack_key}, will extract")
                else:
                    logger.warning(f"Rack wallBaffle {san} (from {rack_name}) NOT found in {rack_key} solids")

        # ALL baffle model solids need individual files for faceZone + dual-side seeds.
        # Both porosity>0 (perforated/cyclic) and porosity=0 (wall baffles) need
        # createBaffles processing and therefore need dual-side locationsInMesh seeds
        # to ensure cells on both sides of the baffle survive castellatedMesh.
        bf_key = "merged_baffle.stl"
        for solid in self._stl_solids.get(bf_key, []):
            san = _sanitize(solid)
            needs_facezone[san] = bf_key

        # perforated solids (from merged_perforated.stl) — each needs its own faceZone
        perf_key = "merged_perforated.stl"
        for solid in self._stl_solids.get(perf_key, []):
            needs_facezone[_sanitize(solid)] = perf_key

        if not needs_facezone:
            return

        # Read STL files and extract blocks
        stl_blocks: Dict[str, Dict[str, str]] = {}  # {filename: {sanitized_name: block_text}}
        for filename in set(needs_facezone.values()):
            fpath = self.tri_surface_dir / filename
            if not fpath.exists():
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                stl_text = f.read()
            blocks = re.findall(r'(^solid\s+(\S+).*?endsolid\s+\S+)',
                                stl_text, re.MULTILINE | re.DOTALL)
            stl_blocks[filename] = {}
            for block, name in blocks:
                stl_blocks[filename][_sanitize(name)] = block

        # Extract each solid that needs a faceZone to its own file
        extracted_from: Dict[str, set] = {}  # {filename: set of extracted sanitized names}
        for san, src_file in needs_facezone.items():
            if src_file not in stl_blocks or san not in stl_blocks[src_file]:
                continue
            block_text = stl_blocks[src_file][san]
            individual_file = f"baffle_{san}.stl"
            ind_path = self.tri_surface_dir / individual_file
            with open(ind_path, "w", encoding="utf-8") as f:
                f.write(block_text + "\n")
            # Find original solid name (not sanitized)
            orig_name = san
            for s in self._stl_solids.get(src_file, []):
                if _sanitize(s) == san:
                    orig_name = s
                    break
            self._stl_solids[individual_file] = [orig_name]
            extracted_from.setdefault(src_file, set()).add(san)
            logger.info(f"Extracted {san} → {individual_file}")

        # Remove extracted solids from their source STL files
        for filename, extracted_names in extracted_from.items():
            fpath = self.tri_surface_dir / filename
            if not fpath.exists():
                continue
            with open(fpath, "r", encoding="utf-8") as f:
                stl_text = f.read()
            blocks = re.findall(r'(^solid\s+(\S+).*?endsolid\s+\S+)',
                                stl_text, re.MULTILINE | re.DOTALL)
            keep_text = ""
            keep_names = []
            for block, name in blocks:
                if _sanitize(name) not in extracted_names:
                    keep_text += block + "\n"
                    keep_names.append(name)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(keep_text)
            if keep_names:
                self._stl_solids[filename] = keep_names
            else:
                del self._stl_solids[filename]
                fpath.unlink()
            logger.info(f"Updated {filename}: {len(keep_names)} solids remaining")

        # Split ALL remaining baffle solids into individual baffle_<name>.stl files.
        # ALL baffles (wall and perforated) use faceType internal + createBaffles.
        # Final patch names come from createBafflesDict, not filenames.
        bf_key = "merged_baffle.stl"
        bf_remaining = list(self._stl_solids.get(bf_key, []))
        if bf_remaining:
            bf_path = self.tri_surface_dir / bf_key
            if bf_path.exists():
                with open(bf_path, "r", encoding="utf-8") as f:
                    bf_text = f.read()
                bf_blocks = re.findall(r'(^solid\s+(\S+).*?endsolid\s+\S+)',
                                       bf_text, re.MULTILINE | re.DOTALL)
                for block_text, solid_name in bf_blocks:
                    san = _sanitize(solid_name)
                    ind_file = f"baffle_{san}.stl"
                    ind_path = self.tri_surface_dir / ind_file
                    with open(ind_path, "w", encoding="utf-8") as f:
                        f.write(block_text + "\n")
                    self._stl_solids[ind_file] = [solid_name]
                    logger.info(f"Split baffle: {solid_name} → {ind_file}")
                del self._stl_solids[bf_key]
                bf_path.unlink()
                logger.info(f"Removed empty {bf_key}")

        # Split leakage solids into individual leakage_<name>.stl files,
        # but ONLY if at least one rack actually has leakage enabled.
        # The frontend may upload merged_leakage.stl even when leakage is
        # disabled for all racks — processing it would create internal faces
        # that cut through rack geometry and destroy server patches.
        any_leakage_enabled = any(
            rack.get("leakage", {}).get("enabled", False)
            or rack.get("leakage", {}).get("groups", [])
            for rack in data.get("racks", {}).values()
        )
        lk_key = "merged_leakage.stl"
        if lk_key in self._stl_solids:
            if not any_leakage_enabled:
                # No leakage enabled — discard the leakage STL entirely
                lk_path = self.tri_surface_dir / lk_key
                if lk_path.exists():
                    lk_path.unlink()
                del self._stl_solids[lk_key]
                logger.info(f"Discarded {lk_key} — no racks have leakage enabled")
            else:
                # Leakage IS enabled — split into individual files for cellZone
                lk_path = self.tri_surface_dir / lk_key
                if lk_path.exists():
                    with open(lk_path, "r", encoding="utf-8") as f:
                        lk_text = f.read()
                    lk_blocks = re.findall(r'(^solid\s+(\S+).*?endsolid\s+\S+)',
                                           lk_text, re.MULTILINE | re.DOTALL)
                    for block_text, solid_name in lk_blocks:
                        san = _sanitize(solid_name)
                        surface_name = f"leakage_{san}"
                        ind_file = f"{surface_name}.stl"
                        ind_path = self.tri_surface_dir / ind_file
                        renamed_block = re.sub(
                            r'^(solid\s+)\S+', rf'\g<1>{surface_name}',
                            block_text, count=1, flags=re.MULTILINE)
                        renamed_block = re.sub(
                            r'(endsolid\s+)\S+\s*$', rf'\g<1>{surface_name}',
                            renamed_block, count=1, flags=re.MULTILINE)
                        with open(ind_path, "w", encoding="utf-8") as f:
                            f.write(renamed_block + "\n")
                        self._stl_solids[ind_file] = [surface_name]
                        logger.info(f"Split leakage: {solid_name} → {ind_file}")
                    del self._stl_solids[lk_key]
                    lk_path.unlink()
                    logger.info(f"Removed {lk_key} after splitting into individual files")
                else:
                    logger.warning(f"{lk_key} in _stl_solids but file not found on disk")
                    del self._stl_solids[lk_key]
        # Also clean up orphan per-rack leakage files from the frontend
        if not any_leakage_enabled and self.tri_surface_dir.exists():
            for orphan in sorted(self.tri_surface_dir.glob("*_leakage_*.stl")):
                orphan.unlink(missing_ok=True)
                self._stl_solids.pop(orphan.name, None)
                logger.info(f"Removed orphan leakage file: {orphan.name}")
            for orphan in sorted(self.tri_surface_dir.glob("leakage_*.stl")):
                orphan.unlink(missing_ok=True)
                self._stl_solids.pop(orphan.name, None)
                logger.info(f"Removed orphan leakage file: {orphan.name}")

    def _resolve_single_solid_baffle_file(
        self,
        zone_name: str,
        patch_kind: str,
        seed_config: Optional[Dict[str, Any]] = None,
    ) -> _PatchSeedSpec:
        stl_name = f"baffle_{zone_name}.stl"
        stl_path = self.tri_surface_dir / stl_name
        if not stl_path.exists():
            raise RuntimeError(
                f"Expected {patch_kind} patch '{zone_name}' to exist as {stl_name}, "
                "but the STL file is missing. Re-export the case geometry before meshing."
            )
        solid_names = self._stl_solids.get(stl_name, [])
        if len(solid_names) != 1:
            raise RuntimeError(
                f"Expected {stl_name} to contain exactly one solid for patch-driven seed placement, "
                f"found {len(solid_names)}"
            )
        return _PatchSeedSpec(
            zone_name=zone_name,
            solid_name=solid_names[0],
            stl_path=stl_path,
            patch_kind=patch_kind,
            seed_config=seed_config,
        )

    def _collect_create_baffle_seed_specs(self) -> List[_PatchSeedSpec]:
        """Collect every patch that should keep cells on both sides via createBaffles."""
        data = self._data
        specs: List[_PatchSeedSpec] = []
        added: set = set()

        def add_zone(zone_name: str, patch_kind: str, seed_config: Optional[Dict[str, Any]] = None) -> None:
            san = _sanitize(zone_name)
            if san in added:
                return
            specs.append(self._resolve_single_solid_baffle_file(san, patch_kind, seed_config=seed_config))
            added.add(san)

        for floor in data.get("floors", {}).values():
            for op in floor.get("openings", []):
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type in ("perforated", "wallBaffle"):
                    add_zone(op["solidName"], f"floor_opening_{op_type}", op.get("seedConfig"))

        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            if default_bc not in ("wallBaffle", "perforated"):
                continue
            san_floor_name = _sanitize(floor_name)
            opening_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            default_seed_map = floor.get("defaultBCSeedConfigs", {}) or {}
            default_seed_cfg = floor.get("defaultBCSeedConfig")
            for filename, solids in self._stl_solids.items():
                if not filename.startswith("baffle_"):
                    continue
                for solid in solids:
                    san = _sanitize(solid)
                    if san.startswith(san_floor_name) and san not in opening_names:
                        add_zone(
                            solid,
                            f"floor_surface_{default_bc}",
                            default_seed_map.get(solid) or default_seed_map.get(san) or default_seed_cfg,
                        )

        for rack in data.get("racks", {}).values():
            for wall_baffle_solid in rack.get("wallBaffleSolids", []):
                add_zone(wall_baffle_solid, "rack_wallBaffle")

        baffle_seed_cfg_by_solid: Dict[str, Optional[Dict[str, Any]]] = {}
        sorted_baffles = sorted(
            data.get("baffles", {}).items(),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        for baffle_name, baffle in sorted_baffles:
            for solid_name, phys in (baffle.get("patchPhysics", {}) or {}).items():
                baffle_seed_cfg_by_solid[_sanitize(solid_name)] = phys.get("seedConfig")
            for solid_name, seed_cfg in (baffle.get("wallRegionSeedConfigs", {}) or {}).items():
                baffle_seed_cfg_by_solid[_sanitize(solid_name)] = seed_cfg

        for filename, solids in self._stl_solids.items():
            if not filename.startswith("baffle_"):
                continue
            for solid in solids:
                san = _sanitize(solid)
                if san in added:
                    continue
                phys = self._baffle_physics.get(san, {})
                if phys.get("baffleType") == "perforated" or phys.get("porosity", 0) > 0:
                    patch_kind = "baffle_model_perforated"
                else:
                    patch_kind = "baffle_model_wall"
                seed_cfg = baffle_seed_cfg_by_solid.get(san)
                if seed_cfg is None:
                    for baffle_name, baffle in sorted_baffles:
                        san_bn = _sanitize(baffle_name)
                        if san == san_bn or san.startswith(san_bn + "_"):
                            seed_cfg = baffle.get("seedConfig")
                            break
                add_zone(
                    solid,
                    patch_kind,
                    seed_cfg,
                )

        return specs

    def _build_stl_triangle_cache(self) -> Dict[str, Dict[str, List[Triangle]]]:
        cache: Dict[str, Dict[str, List[Triangle]]] = {}
        for filename in sorted(self._stl_solids):
            stl_path = self.tri_surface_dir / filename
            if not stl_path.exists():
                continue
            cache[filename] = _read_ascii_stl_triangles(stl_path)
        return cache

    def _build_patch_driven_none_seed_entries(self) -> List[Tuple[Vec3, str]]:
        """Generate dual-side none seeds only for createBaffles patches."""
        specs = self._collect_create_baffle_seed_specs()
        if not specs:
            return []

        mc = self._data.get("meshConfig", {})
        base_size = float(mc.get("baseSize", 0.2) or 0.2)
        if base_size <= 0:
            raise RuntimeError(f"meshConfig.baseSize must be positive, got {base_size}")
        rl_baffle = mc.get("baffleRefinementLevel", [1, 1])
        if not isinstance(rl_baffle, list):
            rl_baffle = [rl_baffle, rl_baffle]
        max_level = max(int(level) for level in rl_baffle) if rl_baffle else 0
        h_local = base_size / float(2 ** max_level)
        default_offset = 0.5 * h_local

        triangle_cache = self._build_stl_triangle_cache()
        scene_triangles = [
            (filename, solid_name, triangle)
            for filename, solids in triangle_cache.items()
            for solid_name, triangles in solids.items()
            for triangle in triangles
        ]

        seed_entries: List[Tuple[Vec3, str]] = []
        for spec in specs:
            triangles = triangle_cache.get(spec.stl_path.name, {}).get(spec.solid_name)
            if not triangles:
                raise RuntimeError(
                    f"Cannot generate seeds for {spec.patch_kind} patch '{spec.zone_name}': "
                    f"no triangles were parsed from {spec.stl_path.name}"
                )
            center, normal = _compute_planar_patch_geometry(triangles)
            anchor, direction, offset = _resolve_patch_seed_config(
                spec.seed_config,
                center=center,
                normal=normal,
                default_offset=default_offset,
            )

            clearance_pos = _nearest_ray_clearance(
                anchor,
                direction,
                scene_triangles,
                (spec.stl_path.name, spec.solid_name),
            )
            clearance_neg = _nearest_ray_clearance(
                anchor,
                _vec_scale(direction, -1.0),
                scene_triangles,
                (spec.stl_path.name, spec.solid_name),
            )

            logger.info(
                "Dual-side seed patch="
                f"{spec.zone_name} kind={spec.patch_kind} center={_format_vec(center)} "
                f"anchor={_format_vec(anchor)} "
                f"direction={_format_vec(direction)} "
                f"normal={_format_vec(normal)} refinementLevel={max_level} "
                f"h_local={h_local:.6f} offset={offset:.6f}"
            )

            failed = (
                clearance_pos is None
                or clearance_neg is None
                or clearance_pos < offset
                or clearance_neg < offset
            )
            if failed:
                raise RuntimeError(
                    "Cannot place dual-side seed for createBaffles patch "
                    f"'{spec.zone_name}' ({spec.patch_kind}). "
                    f"center={_format_vec(center)} anchor={_format_vec(anchor)} "
                    f"direction={_format_vec(direction)} "
                    f"normal={_format_vec(normal)} "
                    f"offset={offset:.6f} "
                    f"h_local={h_local:.6f} "
                    f"clearance_pos={'none' if clearance_pos is None else f'{clearance_pos:.6f}'} "
                    f"clearance_neg={'none' if clearance_neg is None else f'{clearance_neg:.6f}'}. "
                    "Reduce meshConfig.baseSize, increase baffleRefinementLevel, or adjust the geometry."
                )

            seed_entries.append((_vec_add(anchor, _vec_scale(direction, offset)), "none"))
            seed_entries.append((_vec_add(anchor, _vec_scale(direction, -offset)), "none"))

        return seed_entries

    def _build_leakage_cellzone_seed_entries(self) -> List[Tuple[Vec3, str]]:
        """Keep leakage zone seeds tied to the leakage geometry center and cellZone name."""
        entries: List[Tuple[Vec3, str]] = []
        for rack in self._data.get("racks", {}).values():
            for grp in rack.get("leakage", {}).get("groups", []):
                san = _sanitize(grp["stlSolidName"])
                surface_name = f"leakage_{san}"
                lk_stl_path = self.tri_surface_dir / f"{surface_name}.stl"
                if not lk_stl_path.exists():
                    continue
                centroid = _compute_stl_solid_centroid(lk_stl_path, surface_name)
                if centroid is None:
                    continue
                entries.append((centroid, surface_name))
                logger.info(
                    f"Leakage zone seed: {surface_name} at {_format_vec(centroid)} "
                    "(cellZone seed, not a main fluid-domain seed)"
                )
        return entries

    def _build_locations_in_mesh_entries(self) -> List[Tuple[Vec3, str]]:
        room = self._data.get("room", {})
        loc = room.get("locationInMesh", {"x": 5.0, "y": 5.0, "z": 1.5})
        entries: List[Tuple[Vec3, str]] = [
            ((float(loc["x"]), float(loc["y"]), float(loc["z"])), "none")
        ]

        # User-specified extra seed points (all none mode)
        for eloc in room.get("extraLocationsInMesh", []):
            entries.append(((float(eloc["x"]), float(eloc["y"]), float(eloc["z"])), "none"))

        entries.extend(self._build_patch_driven_none_seed_entries())
        entries.extend(self._build_leakage_cellzone_seed_entries())
        return _dedupe_seed_entries(entries)

    def _ensure_config(self) -> None:
        """Backward-compatible no-op wrapper for the old compatibility layer."""
        self._prepare_runtime_metadata()

    # ------------------------------------------------------------------
    # Bridge BC registry
    # ------------------------------------------------------------------

    def _get_rack_width(self, rack_data: dict) -> float:
        """Get rack width in meters from rack data."""
        # Try geometry.size.W (cfd_export.json from STLGenerator)
        geom = rack_data.get("geometry", {})
        size = geom.get("size", {})
        width = size.get("W") or size.get("w") or size.get("width")
        if width and float(width) > 0:
            return float(width)
        # Fallback: base.width (legacy format)
        base = rack_data.get("base", {})
        width = base.get("width") or base.get("w") or base.get("W")
        if width and float(width) > 0:
            return float(width)
        return 0.6  # default rack width 600mm

    def _build_and_save_bridge_bc(self) -> None:
        """Build bridge_bc.json from cfd_export.json physics data."""
        from dctwin.runner.bridge_bc import BridgeBCRegistry, BCSpec

        registry = BridgeBCRegistry()
        data = self._data
        ambient_temp = data.get("simulationConfig", {}).get("roomTemperature", 22.0)

        def resolve_wall_object_temp(obj_cfg: Dict[str, Any], heat_patch: Dict[str, Any]) -> Optional[float]:
            physics = heat_patch.get("physics", {}) or {}
            fixed_temp = physics.get("fixedTemperature")
            if fixed_temp is not None:
                return fixed_temp

            power = physics.get("heatPower", 0.0) or 0.0
            if power <= 0:
                return None

            size = obj_cfg.get("geometry", {}).get("size", {}) or {}
            w = float(size.get("W", 0.0) or 0.0)
            d = float(size.get("D", 0.0) or 0.0)
            h = float(size.get("H", 0.0) or 0.0)
            area = 2.0 * (w * d + w * h + d * h)
            if area <= 1e-8:
                return None
            return ambient_temp + power / (10.0 * area)

        # ACU supply / return
        for acu_name, acu in data.get("acus", {}).items():
            for sf in acu.get("supplyFaces", []):
                solid = _sanitize(sf["solidName"])
                phys = sf.get("physics", {})
                registry.register(solid, BCSpec(
                    bc_type="acu_supply",
                    temperature=phys.get("temperature", 15.0),
                    flow_rate=phys.get("volumeFlowRate", 4.0),
                ))
            for rf in acu.get("returnFaces", []):
                solid = _sanitize(rf["solidName"])
                phys = rf.get("physics", {})
                registry.register(solid, BCSpec(
                    bc_type="acu_return",
                    flow_rate=phys.get("volumeFlowRate", 4.0),
                ))

        # Rack servers (fanServer and nonFan)
        for rack_name, rack in data.get("racks", {}).items():
            for srv in rack.get("servers", []):
                phys = srv.get("physics", {})
                Q = phys.get("volumeFlowRate", 0.0)
                P = phys.get("power", 0.0)
                srv_type = srv.get("type", "fanServer")

                if srv_type == "fanServer":
                    inlet = _sanitize(srv["inletSolidName"])
                    outlet = _sanitize(srv["outletSolidName"])
                    T_outlet = 25.0 + P / (1.2 * 1006 * Q) if Q > 0 else 35.0
                    registry.register(inlet, BCSpec(bc_type="fan_inlet", flow_rate=Q, power=P))
                    registry.register(outlet, BCSpec(bc_type="fan_outlet", flow_rate=Q, power=P,
                                                     temperature=T_outlet))
                else:
                    # nonFan — per-face independent BC
                    phys_inlet = phys.get("inlet", {})
                    phys_outlet = phys.get("outlet", {})

                    # Backward compat: old format has flat fixedTemperature
                    if not phys_inlet and not phys_outlet:
                        T = phys.get("fixedTemperature", 35.0) or 35.0
                        for face_key in ("inletSolidName", "outletSolidName"):
                            solid = srv.get(face_key)
                            if solid:
                                registry.register(_sanitize(solid),
                                                  BCSpec(bc_type="nonfan_fixed_temp", temperature=T))
                        for face_key in ("topSolidName", "bottomSolidName"):
                            solid = srv.get(face_key)
                            if solid:
                                registry.register(_sanitize(solid), BCSpec(bc_type="wall"))
                    else:
                        # New per-face format
                        def _register_nonfan_face(face_key, face_phys):
                            solid_name = srv.get(face_key)
                            if not solid_name:
                                return
                            bc_type = face_phys.get("bcType", "wall")
                            if bc_type == "nonfan_fixed_temp":
                                T_val = face_phys.get("temperature", 35.0) or 35.0
                                registry.register(_sanitize(solid_name),
                                                  BCSpec(bc_type="nonfan_fixed_temp", temperature=T_val))
                            elif bc_type == "nonfan_heat_flux":
                                power = face_phys.get("power", 500) or 500
                                span = srv.get("spanSlots", 1)
                                eq_height = span * 0.04445  # 1U = 44.45mm
                                rack_width = self._get_rack_width(rack)
                                area = eq_height * rack_width
                                hf = power / max(area, 1e-6)
                                registry.register(_sanitize(solid_name),
                                                  BCSpec(bc_type="nonfan_heat_flux", heat_flux=hf))
                            else:
                                registry.register(_sanitize(solid_name), BCSpec(bc_type="wall"))

                        _register_nonfan_face("inletSolidName", phys_inlet)
                        _register_nonfan_face("outletSolidName", phys_outlet)
                        # Top and bottom are always wall
                        for face_key in ("topSolidName", "bottomSolidName"):
                            solid = srv.get(face_key)
                            if solid:
                                registry.register(_sanitize(solid), BCSpec(bc_type="wall"))

            # Leakage zones → porous cellZone
            for grp in rack.get("leakage", {}).get("groups", []):
                solid = _sanitize(grp["stlSolidName"])
                registry.register(solid, BCSpec(bc_type="leakage_zone", sigma=grp.get("sigma", 0.05)))

        # Perforated floor openings → porousBafflePressure or cyclic or wall
        for floor_name, floor in data.get("floors", {}).items():
            for op in floor.get("openings", []):
                if op.get("physics", {}).get("type") == "perforated":
                    solid = _sanitize(op["solidName"])
                    porosity_pct = op["physics"].get("porosity", 50)
                    porosity = porosity_pct / 100.0
                    if porosity_pct <= 0:
                        # porosity=0 → fully blocked → wall baffle (no-slip both sides)
                        registry.register(f"{solid}_master", BCSpec(bc_type="wall_baffle"))
                        registry.register(f"{solid}_slave", BCSpec(bc_type="wall_baffle"))
                    else:
                        # porosity > 0 → cyclic + porousBafflePressure (D=0 for 100%)
                        registry.register(f"{solid}_master", BCSpec(
                            bc_type="perforated_baffle", sigma=porosity))
                        registry.register(f"{solid}_slave", BCSpec(
                            bc_type="perforated_baffle", sigma=porosity))

        # Baffles from baffle models → perforated or wall baffle
        # Check both merged_baffle.stl and individual baffle_<name>.stl files
        # (after _split_internal_face_solids, solids may have been extracted to individual files)
        baffle_solids = list(self._stl_solids.get("merged_baffle.stl", []))
        for fn, solids in self._stl_solids.items():
            if fn.startswith("baffle_") and fn != "merged_baffle.stl":
                baffle_solids.extend(solids)
        for solid in baffle_solids:
            san = _sanitize(solid)
            phys = self._baffle_physics.get(san, {"baffleType": "wall", "porosity": 0})
            porosity_pct = phys.get("porosity", 0)
            if f"{san}_master" in registry:
                continue  # Already registered by floor opening loop
            if porosity_pct > 0:
                porosity = porosity_pct / 100.0
                registry.register(f"{san}_master", BCSpec(
                    bc_type="perforated_baffle", sigma=porosity))
                registry.register(f"{san}_slave", BCSpec(
                    bc_type="perforated_baffle", sigma=porosity))
            else:
                # Wall baffle (porosity=0) → no-slip both sides
                registry.register(f"{san}_master", BCSpec(bc_type="wall_baffle"))
                registry.register(f"{san}_slave", BCSpec(bc_type="wall_baffle"))

        # Floor wallBaffle remaining-area solids (the main floor area minus openings)
        # These become wall baffle patches after createBaffles.
        for floor_name, floor in data.get("floors", {}).items():
            if floor.get("defaultBC", "wall") != "wallBaffle":
                continue
            san_floor_name = _sanitize(floor_name)
            opening_solid_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            for bf_name, bf_solids in self._stl_solids.items():
                if bf_name.startswith("baffle_"):
                    for solid in bf_solids:
                        s = _sanitize(solid)
                        if s.startswith(san_floor_name) and s not in opening_solid_names:
                            if f"{s}_master" not in registry:
                                registry.register(f"{s}_master", BCSpec(bc_type="wall_baffle"))
                                registry.register(f"{s}_slave", BCSpec(bc_type="wall_baffle"))

        # Room walls → simple wall BC (zeroGradient T, noSlip U)
        for wall_name in data.get("walls", {}):
            # Wall solids are named {wall_name}_Front (the single visible face)
            # Check actual STL solids to get exact names
            for solid in self._stl_solids.get("room_wall.stl", []):
                san = _sanitize(solid)
                san_wall = _sanitize(wall_name)
                if san.startswith(san_wall):
                    if san not in registry:
                        registry.register(san, BCSpec(bc_type="wall"))

        # Wall objects → fixedTemperature
        for obj_name, obj in data.get("wallObjects", {}).items():
            for hp in obj.get("heatPatches", []):
                solid = _sanitize(f"{obj_name}_{hp['solidName']}")
                T = resolve_wall_object_temp(obj, hp)
                if T is not None:
                    registry.register(solid, BCSpec(bc_type="fixedTemperature", temperature=T))

        # Heat sources → heat_source_flux or fixedTemperature
        for hs_name, hs in data.get("heatSources", {}).items():
            heat_mode = hs.get("heatMode", "power")
            if heat_mode == "temperature" and hs.get("fixedTemperature") is not None:
                spec = BCSpec(bc_type="fixedTemperature", temperature=hs["fixedTemperature"])
            elif heat_mode == "power" and (hs.get("totalPower", 0) or 0) > 0:
                size = hs.get("geometry", {}).get("size", {}) or {}
                w = float(size.get("W", 0) or 0)
                d = float(size.get("D", 0) or 0)
                h = float(size.get("H", 0) or 0)
                area = 2.0 * (w * d + w * h + d * h)
                q = hs["totalPower"] / area if area > 1e-8 else 0.0
                spec = BCSpec(bc_type="heat_source_flux", heat_flux=q)
            else:
                continue
            for solid_name in hs.get("wallSolidNames", []):
                registry.register(_sanitize(solid_name), spec)

        bridge_bc_path = self.model_dir / "bridge_bc.json"
        registry.save(bridge_bc_path)
        logger.info(f"Written bridge_bc.json: {bridge_bc_path} ({len(registry)} entries)")

    # ------------------------------------------------------------------
    # Mesh dict generation
    # ------------------------------------------------------------------

    def _generate_mesh_dicts(self) -> None:
        """Generate all OpenFOAM mesh dictionaries."""
        system_dir = self.mesh_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)
        (self.mesh_dir / "constant" / "triSurface").mkdir(parents=True, exist_ok=True)

        self._generate_block_mesh_dict(system_dir)
        self._generate_surface_feature_extract_dict(system_dir)
        self._generate_snappy_hex_mesh_dict(system_dir)
        self._generate_create_baffles_dict()

    def _generate_block_mesh_dict(self, system_dir: Path) -> None:
        """Generate blockMeshDict as a disposable container (AABB + 10% margin).

        The blockMesh extends beyond the user-authored room enclosure so that
        snappyHexMesh removes background boundary faces when it cuts cells
        outside the room boundary STL surfaces.
        """
        data = self._data
        mc = data.get("meshConfig", {})
        base_size = mc.get("baseSize", 0.2)

        # ── Bounds from room polygon (raw AABB from frontend) ──
        plane = data.get("room", {}).get("plane", [])
        h = data.get("room", {}).get("height", 3.0)
        if plane:
            xs = [p["x"] for p in plane]
            ys = [p["y"] for p in plane]
            x_min, x_max = min(xs), max(xs)
            y_min, y_max = min(ys), max(ys)
        else:
            x_min, x_max, y_min, y_max = 0.0, 10.0, 0.0, 10.0
        z_min, z_max = 0.0, h

        # ── Apply 10% margin per axis (min 0.1m) ──
        # blockMesh must extend beyond the room enclosure so snappyHexMesh
        # removes all blockMesh faces when cutting cells outside the room.
        MARGIN_FRAC = 0.10
        MIN_MARGIN = 0.1
        mx = max(MIN_MARGIN, (x_max - x_min) * MARGIN_FRAC)
        my = max(MIN_MARGIN, (y_max - y_min) * MARGIN_FRAC)
        mz = max(MIN_MARGIN, (z_max - z_min) * MARGIN_FRAC)
        x_min -= mx; x_max += mx
        y_min -= my; y_max += my
        z_min -= mz; z_max += mz

        x_cells = max(1, int(round((x_max - x_min) / base_size)))
        y_cells = max(1, int(round((y_max - y_min) / base_size)))
        z_cells = max(1, int(round((z_max - z_min) / base_size)))

        content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
|   \\\\  /    A nd           |                                                 |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    location system;
    object blockMeshDict;
}}

scale 1;
vertices
(
    ( {x_min} {y_min} {z_min} )
    ( {x_max} {y_min} {z_min} )
    ( {x_max} {y_max} {z_min} )
    ( {x_min} {y_max} {z_min} )
    ( {x_min} {y_min} {z_max} )
    ( {x_max} {y_min} {z_max} )
    ( {x_max} {y_max} {z_max} )
    ( {x_min} {y_max} {z_max} )
);

blocks
(
    hex
    ( 0 1 2 3 4 5 6 7 )
    ( {x_cells} {y_cells} {z_cells} )
    simpleGrading ( 1 1 1 )
);

edges
();

defaultPatch
{{
    name backgroundBoundary;
    type patch;
}}

patches
(
);

mergePatchPairs
();
"""
        (system_dir / "blockMeshDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated blockMeshDict: ({x_min:.2f}…{x_max:.2f}, {y_min:.2f}…{y_max:.2f}, "
                    f"{z_min:.2f}…{z_max:.2f}), cells={x_cells}×{y_cells}×{z_cells}")

    def _generate_surface_feature_extract_dict(self, system_dir: Path) -> None:
        """Generate surfaceFeatureExtractDict for all STL files."""
        entries = []
        for filename in sorted(self._stl_solids.keys()):
            entries.append(f"""    {filename}
    {{
        extractionMethod    extractFromSurface;
        extractFromSurfaceCoeffs {{ includedAngle 150; }}
        writeObj yes;
    }}""")

        content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    location system;
    object surfaceFeatureExtractDict;
}}

{chr(10).join(entries)}
"""
        (system_dir / "surfaceFeatureExtractDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated surfaceFeatureExtractDict for {len(self._stl_solids)} STL files")

    def _generate_snappy_hex_mesh_dict(self, system_dir: Path) -> None:
        """Generate snappyHexMeshDict with leakage zones as cellZones."""
        data = self._data
        mc = data.get("meshConfig", {})

        rl_default = mc.get("refinementLevel", [1, 1])
        rl_rack = mc.get("rackRefinementLevel", [1, 1])
        rl_server = mc.get("serverFaceRefinementLevel", [1, 1])
        rl_acu = mc.get("acuRefinementLevel", [1, 1])
        rl_box = mc.get("boxRefinementLevel", [1, 1])
        rl_baffle = mc.get("baffleRefinementLevel", [1, 1])
        rl_leakage = mc.get("leakageRefinementLevel", rl_rack)  # default same as rack

        # Per-unit server face refinement levels (keyed by spanSlots string)
        per_unit_levels = mc.get("serverFacePerUnitLevel", {})

        # Per-unit blanking plate refinement levels
        per_unit_blanking = mc.get("blankingPlatePerUnitLevel", {})
        rl_blanking_unified = mc.get("blankingPlateRefinementLevel", rl_rack)

        # Per-unit rack leakage zone refinement levels
        per_unit_leakage = mc.get("rackLeakagePerUnitLevel", {})
        rl_leakage_unified = mc.get("rackLeakageRefinementLevel", rl_leakage)

        # Build solid-name lookup: which solids are server faces vs leakage vs etc.
        server_inlet_solids: set = set()
        server_outlet_solids: set = set()
        nonfan_solids: set = set()
        leakage_solids: Dict[str, float] = {}  # {solidName: sigma}
        acu_supply_solids: set = set()
        acu_return_solids: set = set()
        perforated_solids: Dict[str, float] = {}  # {solidName: porosity}

        # Build mapping: sanitized solid name → spanSlots for per-unit refinement lookup
        solid_to_span: Dict[str, int] = {}
        for rack in data.get("racks", {}).values():
            for srv in rack.get("servers", []):
                if srv.get("type") == "fanServer":
                    server_inlet_solids.add(_sanitize(srv["inletSolidName"]))
                    server_outlet_solids.add(_sanitize(srv["outletSolidName"]))
                    span = srv.get("spanSlots", 1)
                    solid_to_span[_sanitize(srv["inletSolidName"])] = span
                    solid_to_span[_sanitize(srv["outletSolidName"])] = span
                else:
                    for k in ("inletSolidName", "outletSolidName", "topSolidName", "bottomSolidName"):
                        if srv.get(k):
                            nonfan_solids.add(_sanitize(srv[k]))
            for grp in rack.get("leakage", {}).get("groups", []):
                leakage_solids[_sanitize(grp["stlSolidName"])] = grp.get("sigma", 0.05)

        # Rack wall-baffle solids: half-dissolved U-slots (this side wall, opposite void).
        # These become faceType internal → createBaffles cyclic → post-process to wall.
        rack_wall_baffle_solids: set = set()
        for rack in data.get("racks", {}).values():
            for wb_solid in rack.get("wallBaffleSolids", []):
                rack_wall_baffle_solids.add(_sanitize(wb_solid))

        # Structural inlet/outlet solids from rack geometry (safe margins, frames, U-ranges).
        # These are NOT in server_inlet/outlet_solids (which only contain server face names).
        # They must be type patch so snappyHexMesh creates boundary patches for them.
        _rack_io_pat = re.compile(r'_(inlet|outlet)_(safe_|frame_|u\d)')
        rack_io_solids: set = set()
        for solid in self._stl_solids.get("merged_rack.stl", []):
            san = _sanitize(solid)
            if (_rack_io_pat.search(san)
                    and san not in server_inlet_solids
                    and san not in server_outlet_solids
                    and san not in rack_wall_baffle_solids):
                rack_io_solids.add(san)

        # Build mapping: blanking plate solid name → spanUnits (for per-unit refinement)
        solid_to_blanking_span: Dict[str, int] = {}
        _u_range_pat = re.compile(r'_u(\d+)_u(\d+)')
        for solid_name in (rack_io_solids | rack_wall_baffle_solids):
            m = _u_range_pat.search(solid_name)
            if m:
                solid_to_blanking_span[solid_name] = int(m.group(2)) - int(m.group(1)) + 1

        # Build mapping: leakage zone solid name → spanUnits
        solid_to_leakage_span: Dict[str, int] = {}
        for rack in data.get("racks", {}).values():
            for grp in rack.get("leakage", {}).get("groups", []):
                san = _sanitize(grp["stlSolidName"])
                solid_to_leakage_span[san] = grp.get("endUnit", 0) - grp.get("startUnit", 0) + 1

        for acu in data.get("acus", {}).values():
            for sf in acu.get("supplyFaces", []):
                acu_supply_solids.add(_sanitize(sf["solidName"]))
            for rf in acu.get("returnFaces", []):
                acu_return_solids.add(_sanitize(rf["solidName"]))

        # Perforated baffles from baffle models
        for baffle_name, baffle in data.get("baffles", {}).items():
            phys = baffle.get("physics", {})
            porosity_pct = phys.get("porosity", 0)
            if phys.get("baffleType") == "perforated" or porosity_pct > 0:
                san_bn = _sanitize(baffle_name)
                for solid in self._stl_solids.get("merged_baffle.stl", []):
                    san = _sanitize(solid)
                    if san.startswith(san_bn + "_") or san == san_bn:
                        sol_phys = self._baffle_physics.get(san, phys)
                        sol_porosity = sol_phys.get("porosity", 0) / 100.0
                        if sol_porosity > 0 and san not in perforated_solids:
                            perforated_solids[san] = sol_porosity
            # Per-patch physics
            for solid_name, pp in baffle.get("patchPhysics", {}).items():
                san = _sanitize(solid_name)
                pp_porosity = pp.get("porosity", 0) / 100.0
                if pp_porosity > 0 and san not in perforated_solids:
                    perforated_solids[san] = pp_porosity

        # Collect floor solid types: wallBaffle (internal face, no-slip) and perforated (cyclic baffle)
        # Also includes rack wall-baffle solids (half-dissolved U-slots).
        wall_baffle_solids: set = set(rack_wall_baffle_solids)
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            san_floor_name = _sanitize(floor_name)
            # Identify which solids are patches (openings) vs remaining area
            opening_solid_names = set()
            for op in floor.get("openings", []):
                op_solid = _sanitize(op["solidName"])
                opening_solid_names.add(op_solid)
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type == "perforated":
                    porosity = op["physics"].get("porosity", 50) / 100.0
                    perforated_solids[op_solid] = porosity
                elif op_type == "wallBaffle":
                    wall_baffle_solids.add(op_solid)
            # Remaining area (non-opening) solids that belong to THIS floor get the defaultBC type
            # Filter by floor name prefix to avoid affecting solids from other floors in same STL
            # Check both original and internal-split STL files
            floor_stl = floor.get("stlFile", "merged_floor_ceiling.stl")
            floor_solids_in_stl = list(self._stl_solids.get(floor_stl, []))
            # Also check internal split file (created by _load split)
            fc_int_stl = "merged_floor_ceiling_internal.stl"
            if fc_int_stl in self._stl_solids:
                floor_solids_in_stl.extend(self._stl_solids[fc_int_stl])
            # Also check individual baffle_*.stl files (floor solids are extracted there)
            for bf_name, bf_solids in self._stl_solids.items():
                if bf_name.startswith("baffle_"):
                    floor_solids_in_stl.extend(bf_solids)
            if default_bc == "wallBaffle":
                for solid in floor_solids_in_stl:
                    s = _sanitize(solid)
                    if s.startswith(san_floor_name) and s not in opening_solid_names and s not in perforated_solids:
                        wall_baffle_solids.add(s)
            elif default_bc == "perforated":
                for solid in floor_solids_in_stl:
                    s = _sanitize(solid)
                    if s.startswith(san_floor_name) and s not in opening_solid_names:
                        perforated_solids[s] = floor.get("defaultBCPorosity", floor.get("defaultBC_porosity", 0.5))

        # --- Geometry section ---
        geom_lines = []
        for filename, solids in sorted(self._stl_solids.items()):
            stem = Path(filename).stem   # e.g. merged_rack
            if filename in ("merged_perforated.stl",
                            "merged_baffle.stl") or filename.startswith("baffle_"):
                # These surfaces use faceType internal → no named regions in geometry.
                # Named regions force snappyHexMesh to create wall boundary patches
                # instead of internal faces, defeating faceType internal.
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
    }}""")
                continue
            elif filename.startswith("leakage_"):
                # Individual leakage zone file: no named regions needed.
                # Each file is a standalone closed surface for cellZone detection.
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
    }}""")
                continue
            else:
                region_lines = "\n".join(f"            {s} {{ name {s}; }}" for s in solids)
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
        regions
        {{
{region_lines}
        }}
    }}""")

        # --- Features section (per-STL feature edge levels) ---
        def _feat_int(val, default=2):
            """Ensure feature level is a scalar integer (not a list)."""
            if isinstance(val, list): return int(val[0]) if val else default
            return int(val) if val is not None else default
        feat_default = _feat_int(mc.get("defaultFeatureLevel"), 2)
        feat_rack = _feat_int(mc.get("rackFeatureLevel"), 2)
        feat_acu = _feat_int(mc.get("acuFeatureLevel"), 2)
        feat_box = _feat_int(mc.get("boxFeatureLevel"), 2)
        feat_baffle = _feat_int(mc.get("baffleFeatureLevel"), 2)
        feat_leakage = _feat_int(mc.get("leakageFeatureLevel"), feat_rack)
        feature_lines = []
        for filename in sorted(self._stl_solids.keys()):
            stem = Path(filename).stem
            emesh_name = filename.replace(".stl", ".eMesh")
            # Per-group feature edge level
            if filename == "merged_rack.stl":
                feat_level = feat_rack
            elif filename == "merged_acu.stl":
                feat_level = feat_acu
            elif filename == "merged_box.stl":
                feat_level = feat_box
            elif filename.startswith("leakage_"):
                feat_level = feat_leakage
            elif filename in ("merged_baffle.stl",
                              "merged_floor_ceiling.stl", "merged_perforated.stl") \
                    or filename.startswith("baffle_"):
                feat_level = feat_baffle
            elif filename == "room_wall.stl":
                feat_level = feat_default
            else:
                feat_level = feat_default
            feature_lines.append(f"""    {{
        file "{emesh_name}";
        level {feat_level};
    }}""")

        # --- Refinement surfaces section ---
        refine_lines = []
        for filename, solids in sorted(self._stl_solids.items()):
            stem = Path(filename).stem
            if filename.startswith("leakage_"):
                # Individual leakage zone: surface-level faceType internal + cellZone.
                # snappyHexMesh ignores per-region faceType, so each zone is its own file.
                san = _sanitize(solids[0])
                _span_lk = solid_to_leakage_span.get(san)
                _rl_lk = per_unit_leakage.get(str(_span_lk), rl_leakage_unified) if _span_lk else rl_leakage
                if not isinstance(_rl_lk, (list, tuple)) or len(_rl_lk) < 2:
                    _rl_lk = rl_leakage
                refine_lines.append(f"""    {stem}
    {{
        level ({_rl_lk[0]} {_rl_lk[1]});
        faceType internal;
        cellZone {san};
        cellZoneInside inside;
    }}""")
                continue

            if filename == "merged_perforated.stl":
                # Perforated solids should have been extracted to individual files.
                # If any remain (shouldn't happen), treat as surface-level faceZone.
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone {_sanitize(solids[0])};
    }}""")
                continue

            if filename.startswith("baffle_"):
                # ALL baffle files (wall + perforated) use faceType internal + faceZone.
                # createBaffles then converts to wall or cyclic patches.
                solid_san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue

            solid_entries = []
            for solid in solids:
                sanitized = _sanitize(solid)
                if sanitized in leakage_solids:
                    # Leakage solid in a non-leakage STL — treat as regular wall
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_default[0]} {rl_default[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
                elif sanitized in server_inlet_solids or sanitized in server_outlet_solids:
                    # Per-unit refinement: look up this solid's spanSlots → bucket level
                    _span = solid_to_span.get(sanitized)
                    _rl = per_unit_levels.get(str(_span), rl_server) if _span else rl_server
                    if not isinstance(_rl, (list, tuple)) or len(_rl) < 2:
                        _rl = rl_server
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl[0]} {_rl[1]});
                patchInfo {{ type patch; }}
            }}""")
                elif sanitized in nonfan_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_server[0]} {rl_server[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
                elif sanitized in rack_io_solids:
                    # Blanking plate (wall patch): per-unit refinement
                    _span_bp = solid_to_blanking_span.get(sanitized)
                    _rl_bp = per_unit_blanking.get(str(_span_bp), rl_blanking_unified) if _span_bp else rl_rack
                    if not isinstance(_rl_bp, (list, tuple)) or len(_rl_bp) < 2:
                        _rl_bp = rl_rack
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl_bp[0]} {_rl_bp[1]});
                patchInfo {{ type patch; }}
            }}""")
                elif sanitized in acu_supply_solids or sanitized in acu_return_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_acu[0]} {rl_acu[1]});
                patchInfo {{ type patch; }}
            }}""")
                elif sanitized in rack_wall_baffle_solids:
                    # Rack wall baffle (blanking plate): per-unit refinement, decoupled from standalone baffles
                    _span_wb = solid_to_blanking_span.get(sanitized)
                    _rl_wb = per_unit_blanking.get(str(_span_wb), rl_blanking_unified) if _span_wb else rl_baffle
                    if not isinstance(_rl_wb, (list, tuple)) or len(_rl_wb) < 2:
                        _rl_wb = rl_baffle
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl_wb[0]} {_rl_wb[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                elif sanitized in wall_baffle_solids:
                    # Standalone wall baffle (floor/baffle model): uses baffleRefinementLevel
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_baffle[0]} {rl_baffle[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                elif sanitized in perforated_solids:
                    # Perforated floor opening: faceType internal + faceZone for createBaffles
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_baffle[0]} {rl_baffle[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                elif filename == "merged_baffle.stl":
                    # All baffles use faceType internal + faceZone (both sides kept).
                    # snappyHexMesh requires faceZone for faceType internal to work.
                    # Porosity=0: faceZone exists but NO createBaffles entry →
                    #   stays as internal face (transparent) with noSlip semantics
                    # Porosity>0: faceZone + createBaffles cyclic master/slave
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_baffle[0]} {rl_baffle[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                else:
                    # Per-STL-file refinement level
                    if filename == "merged_rack.stl": rl = rl_rack
                    elif filename == "merged_acu.stl": rl = rl_acu
                    elif filename == "merged_box.stl": rl = rl_box
                    elif filename == "merged_floor_ceiling.stl": rl = rl_baffle
                    else: rl = rl_default
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl[0]} {rl[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")

            solid_block = "\n".join(solid_entries)
            # Surfaces with ONLY internal faces need faceType internal at
            # surface level (snappyHexMesh ignores per-region faceType).
            # Mixed surfaces (like merged_floor_ceiling with both wall and
            # internal regions) cannot use surface-level faceType internal
            # because it would override patchInfo type wall for solid walls.
            surface_facetype = ""  # per-region faceType handled by individual entries
            refine_lines.append(f"""    {stem}
    {{
        level ({rl_default[0]} {rl_default[1]});{surface_facetype}
        regions
        {{
{solid_block}
        }}
    }}""")

        # --- Locations in mesh ---
        location_entries = self._build_locations_in_mesh_entries()
        locations_str = "\n".join(
            f"        (( {point[0]:.6f} {point[1]:.6f} {point[2]:.6f} ) {zone_name})"
            for point, zone_name in location_entries
        )

        # ── Refinement zone searchableBox entries ──
        rz_list = mc.get("refinementZones", [])
        for rz in rz_list:
            rz_name = _sanitize(rz["name"])
            mn, mx = rz["min"], rz["max"]
            geom_lines.append(f"""    {rz_name}
    {{
        type searchableBox;
        min ({mn['x']} {mn['y']} {mn['z']});
        max ({mx['x']} {mx['y']} {mx['z']});
    }}""")

        geom_str = "\n\n".join(geom_lines)
        features_str = "\n".join(feature_lines)
        refine_str = "\n\n".join(refine_lines)

        # ── Refinement regions ──
        rz_region_lines = []
        for rz in rz_list:
            rz_name = _sanitize(rz["name"])
            lvl = rz.get("level", 2)
            rz_region_lines.append(f"""        {rz_name}
        {{
            mode inside;
            levels ((1e15 {lvl}));
        }}""")
        rz_regions_str = "\n\n".join(rz_region_lines) if rz_region_lines else ""

        content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    location system;
    object snappyHexMeshDict;
}}

castellatedMesh on;
snap on;
addLayers off;

geometry
{{
{geom_str}
}}

castellatedMeshControls
{{
    maxLocalCells   {mc.get('maxGlobalCells', 20000000)};
    maxGlobalCells  {mc.get('maxGlobalCells', 20000000)};
    minRefinementCells 5;
    nCellsBetweenLevels 3;
    maxLoadUnbalance 0.10;
    allowFreeStandingZoneFaces true;
    resolveFeatureAngle 30;

    features
    (
{features_str}
    );

    refinementSurfaces
    {{
{refine_str}
    }}

    refinementRegions
    {{
{rz_regions_str}
    }}

    // Use locationsInMesh with explicit cell zone assignment
    locationsInMesh
    (
{locations_str}
    );
}}

snapControls
{{
    nSmoothPatch 3;
    tolerance 2.0;
    nSolveIter 30;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    implicitFeatureSnap false;
    explicitFeatureSnap true;
    multiRegionFeatureSnap false;
}}

addLayersControls
{{
    relativeSizes true;
    expansionRatio 1.0;
    finalLayerThickness 0.3;
    minThickness 0.1;
    nGrow 0;
    featureAngle 60;
    nRelaxIter 3;
    nSmoothSurfaceNormals 1;
    nSmoothNormals 3;
    nSmoothThickness 10;
    maxFaceThicknessRatio 0.5;
    maxThicknessToMedialRatio 0.3;
    minMedianAxisAngle 90;
    nBufferCellsNoExtrude 0;
    nLayerIter 50;
    layers
    {{
    }}
}}

meshQualityControls
{{
    maxNonOrtho 65;
    maxBoundarySkewness 20;
    maxInternalSkewness 4;
    maxConcave 80;
    minFlatness 0.5;
    minVol 1e-13;
    minTetQuality 1e-15;
    minArea -1;
    minTwist 0.02;
    minDeterminant 0.001;
    minFaceWeight 0.05;
    minVolRatio 0.01;
    minTriangleTwist -1;
    nSmoothScale 4;
    errorReduction 0.75;
    relaxed
    {{
        maxNonOrtho 75;
    }}
}}

writeFlags
(
    scalarLevels
);

mergeTolerance 1e-6;
"""
        (system_dir / "snappyHexMeshDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated snappyHexMeshDict with {len(leakage_solids)} leakage cellZones")

    # ------------------------------------------------------------------
    # Mesh pipeline
    # ------------------------------------------------------------------

    def _generate_create_baffles_dict(self) -> None:
        """Generate createBafflesDict for perforated floor openings and
        zero-thickness baffles (containment panels, partition walls).

        ALL baffles use type cyclic in createBafflesDict.
        Wall baffles are converted from cyclic to wall type in the boundary
        file AFTER createBaffles runs, via _convert_wall_baffle_patches_to_wall().
        """
        data = self._data
        baffle_configs = []
        wall_baffle_configs = []
        for floor in data.get("floors", {}).values():
            for op in floor.get("openings", []):
                if op.get("physics", {}).get("type") == "perforated":
                    zone_name = _sanitize(op["solidName"])
                    porosity_pct = op["physics"].get("porosity", 50)
                    porosity = porosity_pct / 100.0

                    if porosity_pct <= 0:
                        # porosity=0 → fully blocked → degrade to no-slip wall
                        wall_baffle_configs.append({"zone_name": zone_name})
                    elif porosity_pct >= 100:
                        # porosity=100 → fully open → pure cyclic (no pressure drop)
                        baffle_configs.append({
                            "zone_name": zone_name,
                            "porosity": 1.0,
                            "d_coeff": 0.0,
                            "baffleType": "cyclic_only",  # no porousBafflePressure
                        })
                    else:
                        # 0 < porosity < 100 → cyclic + porousBafflePressure
                        d_coeff = (1.0 - porosity**2) / (2.0 * porosity**2)
                        baffle_configs.append({
                            "zone_name": zone_name,
                            "porosity": porosity,
                            "d_coeff": d_coeff,
                            "baffleType": "perforated",
                        })

        # ALL baffle model solids get createBaffles entries (type cyclic).
        # Wall baffles → cyclic (converted to wall in boundary post-processing).
        # Perforated → cyclic + porousBafflePressure BCs.
        # Skip solids already handled by the floor perforated loop above.
        _already_added = set(bc["zone_name"] for bc in baffle_configs)
        for filename, file_solids in self._stl_solids.items():
            if not filename.startswith("baffle_"):
                continue
            for solid in file_solids:
                zone_name = _sanitize(solid)
                if zone_name in _already_added:
                    continue
                phys = self._baffle_physics.get(zone_name, {"baffleType": "wall", "porosity": 0})
                porosity_pct = phys.get("porosity", 0)
                baffle_type = phys.get("baffleType", "wall")

                if porosity_pct > 0 or baffle_type == "perforated":
                    porosity = porosity_pct / 100.0
                    d_coeff = 0.0 if porosity >= 1.0 else (
                        (1.0 - porosity**2) / (2.0 * porosity**2) if porosity > 0 else 1e6
                    )
                    baffle_configs.append({
                        "zone_name": zone_name,
                        "porosity": min(porosity, 1.0),
                        "d_coeff": d_coeff,
                        "baffleType": "perforated",
                    })
                else:
                    # Wall baffle → cyclic master/slave (converted to wall in post-processing)
                    wall_baffle_configs.append({"zone_name": zone_name})
            # Floor wallBaffle solids in merged_baffle.stl (not in _baffle_physics)
            # are handled by the floor wallBaffle code below

        # Deduplicate: track zone names already added
        _added_zones = set(bc["zone_name"] for bc in baffle_configs)
        _added_zones.update(wbc["zone_name"] for wbc in wall_baffle_configs)

        # Wall-type baffles on floors (faceType internal + createBaffles cyclic → post-process to wall)
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            if default_bc != "wallBaffle":
                continue
            san_floor_name = _sanitize(floor_name)
            opening_solid_names = set()
            for op in floor.get("openings", []):
                opening_solid_names.add(_sanitize(op["solidName"]))
            for bf_name, bf_solids in self._stl_solids.items():
                if bf_name.startswith("baffle_"):
                    for solid in bf_solids:
                        s = _sanitize(solid)
                        if s.startswith(san_floor_name) and s not in opening_solid_names and s not in _added_zones:
                            wall_baffle_configs.append({"zone_name": s})
                            _added_zones.add(s)

        # Store wall baffle zone names for post-createBaffles boundary conversion
        self._wall_baffle_zone_names = {wbc["zone_name"] for wbc in wall_baffle_configs}

        if not baffle_configs and not wall_baffle_configs:
            return

        system_dir = self.mesh_dir / "system"
        entries = []
        # Perforated opening baffles → cyclic pairs
        for bc in baffle_configs:
            entries.append(f"""    {bc['zone_name']}
    {{
        type        faceZone;
        zoneName    {bc['zone_name']};

        patches
        {{
            master
            {{
                name            {bc['zone_name']}_master;
                type            cyclic;
                neighbourPatch  {bc['zone_name']}_slave;
            }}
            slave
            {{
                name            {bc['zone_name']}_slave;
                type            cyclic;
                neighbourPatch  {bc['zone_name']}_master;
            }}
        }}
    }}""")

        # Wall-type baffles → also cyclic in createBafflesDict (same as perforated).
        # After createBaffles, _convert_wall_baffle_patches_to_wall() converts
        # these cyclic patches to wall type in the boundary file.
        for wbc in wall_baffle_configs:
            zn = wbc['zone_name']
            entries.append(f"""    {zn}
    {{
        type        faceZone;
        zoneName    {zn};

        patches
        {{
            master
            {{
                name            {zn}_master;
                type            cyclic;
                neighbourPatch  {zn}_slave;
            }}
            slave
            {{
                name            {zn}_slave;
                type            cyclic;
                neighbourPatch  {zn}_master;
            }}
        }}
    }}""")

        content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version 2.0;
    format ascii;
    class dictionary;
    location system;
    object createBafflesDict;
}}

internalFacesOnly true;

baffles
{{
{"".join(entries)}
}}
"""
        (system_dir / "createBafflesDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated createBafflesDict with {len(baffle_configs)} perforated + {len(wall_baffle_configs)} wall baffles")

    def _ensure_mesh_system_files(self) -> None:
        """Write minimal controlDict + fvSchemes + fvSolution so blockMesh/snappyHexMesh can run."""
        system_dir = self.mesh_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)

        stubs = {
            "controlDict": """\
FoamFile { version 2.0; format ascii; class dictionary; object controlDict; }
application     snappyHexMesh;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         0;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
writeFormat     ascii;
writePrecision  6;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
""",
            "fvSchemes": """\
FoamFile { version 2.0; format ascii; class dictionary; object fvSchemes; }
ddtSchemes { default steadyState; }
gradSchemes { default Gauss linear; }
divSchemes { default none; }
laplacianSchemes { default Gauss linear corrected; }
interpolationSchemes { default linear; }
snGradSchemes { default corrected; }
""",
            "fvSolution": """\
FoamFile { version 2.0; format ascii; class dictionary; object fvSolution; }
solvers { }
""",
        }

        for name, content in stubs.items():
            path = system_dir / name
            if not path.exists():
                path.write_text(content, encoding="utf-8")
                logger.info(f"Written minimal {name} for mesh pipeline")

    def _ensure_wall_baffles_are_wall_type(self, boundary_path: Path) -> None:
        """Ensure wall_baffle patches in bridge_bc.json have type=wall in boundary.

        Uses bridge_bc.json as the authoritative source for which patches are
        wall baffles.  This works in both mesh and solve-only modes (unlike
        _convert_wall_baffle_patches_to_wall which depends on _wall_baffle_zone_names
        being populated during mesh dict generation).
        """
        bridge_path = self.case_dir / "model" / "bridge_bc.json"
        if not bridge_path.exists() or not boundary_path.exists():
            return
        bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
        wall_patches = {k for k, v in bridge.items() if v.get("bc_type") == "wall_baffle"}
        if not wall_patches:
            return
        content = boundary_path.read_text(encoding="utf-8")
        modified = False
        for patch_name in wall_patches:
            pattern = re.compile(
                rf'(\s+{re.escape(patch_name)}\s*\n\s*\{{\s*\n)(\s*type\s+)cyclic(\s*;)',
                re.MULTILINE,
            )
            if pattern.search(content):
                content = pattern.sub(r'\1\2wall\3', content)
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)neighbourPatch\s+\S+\s*;\n',
                    r'\1', content,
                )
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)inGroups\s+\d+\((?:[^)]*cyclic[^)]*)\)\s*;\n',
                    r'\1', content,
                )
                modified = True
        if modified:
            boundary_path.write_text(content, encoding="utf-8")
            logger.info(f"Ensured wall baffle patches have type=wall (from bridge_bc.json)")

    def _convert_wall_baffle_patches_to_wall(self, case_dir: Path) -> None:
        """Convert wall baffle patches from cyclic to wall type in boundary file.

        createBaffles creates ALL baffles as cyclic. Wall baffles need to be
        converted to wall type afterwards so they get no-slip BCs.
        """
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            return
        wall_patch_names = set()
        for zn in self._wall_baffle_zone_names:
            wall_patch_names.add(f"{zn}_master")
            wall_patch_names.add(f"{zn}_slave")
        # Also include perforated baffles with porosity=0
        for floor in self._data.get("floors", {}).values():
            for op in floor.get("openings", []):
                if op.get("physics", {}).get("type") == "perforated":
                    porosity_pct = op["physics"].get("porosity", 50)
                    if porosity_pct <= 0:
                        zn = _sanitize(op["solidName"])
                        wall_patch_names.add(f"{zn}_master")
                        wall_patch_names.add(f"{zn}_slave")
        if not wall_patch_names:
            return
        content = boundary_path.read_text(encoding="utf-8")
        modified = False
        for patch_name in wall_patch_names:
            pattern = re.compile(
                rf'(\s+{re.escape(patch_name)}\s*\n\s*\{{\s*\n)(\s*type\s+)cyclic(\s*;)',
                re.MULTILINE,
            )
            if pattern.search(content):
                content = pattern.sub(r'\1\2wall\3', content)
                # Remove neighbourPatch line
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)neighbourPatch\s+\S+\s*;\n',
                    r'\1', content,
                )
                # Remove inGroups with cyclic
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)inGroups\s+\d+\((?:[^)]*cyclic[^)]*)\)\s*;\n',
                    r'\1', content,
                )
                # Remove stale cyclic-specific fields (matchTolerance, transform)
                # These belong to cyclic patches, not wall patches
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)matchTolerance\s+[\d.eE+-]+\s*;\n',
                    r'\1', content,
                )
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)transform\s+\w+\s*;\n',
                    r'\1', content,
                )
                modified = True
        if modified:
            boundary_path.write_text(content, encoding="utf-8")
            logger.info(f"Converted {len(wall_patch_names)} wall baffle patches from cyclic to wall")

    def _run_serial_mesh_pipeline(self) -> None:
        """Run blockMesh → surfaceFeatureExtract → snappyHexMesh → createBaffles."""
        self._ensure_mesh_system_files()
        case_dir = self.mesh_dir
        total_steps = 3
        baffles_dict = case_dir / "system" / "createBafflesDict"
        if baffles_dict.exists(): total_steps += 1
        step = 0

        step += 1
        logger.info(f"Step {step}/{total_steps}: blockMesh")
        self._run_cmd("blockMesh", case_dir)

        feat_dict = case_dir / "system" / "surfaceFeatureExtractDict"
        if feat_dict.exists():
            step += 1
            logger.info(f"Step {step}/{total_steps}: surfaceFeatureExtract")
            self._run_cmd("surfaceFeatureExtract", case_dir)

        step += 1
        logger.info(f"Step {step}/{total_steps}: snappyHexMesh -overwrite")
        self._run_cmd("snappyHexMesh -overwrite", case_dir)
        self._diagnose_background_patch(case_dir)

        # createBaffles: convert faceZones to cyclic master/slave pairs
        # createBaffles reads 0/ field files — regenerate them so they match current patches
        if baffles_dict.exists():
            step += 1
            logger.info(f"Step {step}/{total_steps}: createBaffles -overwrite")
            self._regenerate_fields_for_baffles(case_dir)
            self._run_cmd("createBaffles -overwrite", case_dir)
            # Convert wall baffle patches from cyclic to wall type
            self._convert_wall_baffle_patches_to_wall(case_dir)

    @staticmethod
    def _clean_processor_dirs(case_dir: Path) -> None:
        """Remove processor* directories from a case directory."""
        for pdir in sorted(_glob.glob(str(case_dir / "processor*"))):
            shutil.rmtree(pdir, ignore_errors=True)
        logger.info(f"Cleaned processor directories in {case_dir}")

    def _run_parallel_mesh_pipeline(self, n_procs: int) -> None:
        """Run blockMesh → decomposePar → snappyHexMesh (parallel) → reconstructParMesh → createBaffles."""
        self._ensure_mesh_system_files()
        case_dir = self.mesh_dir

        baffles_dict = case_dir / "system" / "createBafflesDict"
        feat_dict = case_dir / "system" / "surfaceFeatureExtractDict"
        total_steps = 4  # blockMesh, decomposePar, snappyHexMesh, reconstructParMesh
        if feat_dict.exists():
            total_steps += 1
        if baffles_dict.exists():
            total_steps += 1
        step = 0

        # 1. blockMesh (serial)
        step += 1
        logger.info(f"Step {step}/{total_steps}: blockMesh")
        self._run_cmd("blockMesh", case_dir)

        # 2. surfaceFeatureExtract (serial, if dict exists)
        if feat_dict.exists():
            step += 1
            logger.info(f"Step {step}/{total_steps}: surfaceFeatureExtract")
            self._run_cmd("surfaceFeatureExtract", case_dir)

        # 3. decomposePar (mesh only, no fields — snappyHexMesh doesn't need 0/ fields,
        #    and stale field files from a previous run would cause FATAL IO errors)
        step += 1
        logger.info(f"Step {step}/{total_steps}: decomposePar -force -no-fields ({n_procs} processors)")
        generate_decompose_par_dict(case_dir, n_procs)
        self._clean_processor_dirs(case_dir)
        self._run_cmd("decomposePar -force -no-fields", case_dir)

        # 4. snappyHexMesh parallel
        step += 1
        logger.info(f"Step {step}/{total_steps}: snappyHexMesh -overwrite -parallel ({n_procs} procs)")
        self._run_cmd(
            f"mpirun --oversubscribe -np {n_procs} snappyHexMesh -overwrite -parallel",
            case_dir,
        )

        # 5. reconstructParMesh
        step += 1
        logger.info(f"Step {step}/{total_steps}: reconstructParMesh -constant")
        self._run_cmd("reconstructParMesh -constant -mergeTol 1E-08 -noZero", case_dir)
        self._diagnose_background_patch(case_dir)

        # 6. Clean up processor directories
        self._clean_processor_dirs(case_dir)

        # 7. createBaffles (serial, on reconstructed mesh)
        if baffles_dict.exists():
            step += 1
            logger.info(f"Step {step}/{total_steps}: createBaffles -overwrite")
            self._regenerate_fields_for_baffles(case_dir)
            self._run_cmd("createBaffles -overwrite", case_dir)
            self._convert_wall_baffle_patches_to_wall(case_dir)

    def _diagnose_background_patch(self, case_dir: Path) -> None:
        """Log the surviving face count of the generic background patch."""
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            return

        text = boundary_path.read_text(encoding="utf-8")
        match = re.search(
            r'backgroundBoundary\s*\n\s*\{[^}]*?nFaces\s+(\d+)',
            text,
            re.MULTILINE,
        )
        if match:
            n_faces = int(match.group(1))
            if n_faces > 0:
                logger.warning(
                    "Background blockMesh patch still has faces after snappyHexMesh: "
                    f"backgroundBoundary(nFaces={n_faces})"
                )
            else:
                logger.info("Background blockMesh patch is empty after snappyHexMesh")
        elif "backgroundBoundary" not in text:
            logger.warning(
                "backgroundBoundary patch not found in boundary file after snappyHexMesh"
            )

    def _regenerate_fields_for_baffles(self, case_dir: Path) -> None:
        """Regenerate 0/ field files so createBaffles can read them.

        After snappyHexMesh the boundary patches may have changed, so the old
        0/ files (if any) won't match. We regenerate them directly from the
        STLGen bridge metadata.
        """
        # Remove stale field files (e.g. Cx, Cy, Cz, C, V from previous
        # postProcess runs) that don't match the current mesh cell count.
        zero_dir = case_dir / "0"
        if zero_dir.is_dir():
            for f in zero_dir.iterdir():
                if f.is_file() and f.name not in STANDARD_FIELD_NAMES:
                    logger.info(f"Removing stale field file: 0/{f.name}")
                    f.unlink()

        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        fix_cyclic_match_tolerance(boundary_path)
        patches = read_boundary_patches(boundary_path)
        self._build_field_generator().generate_fields(case_dir, patches)
        logger.info(f"Regenerated 0/ fields ({len(patches)} patches) for createBaffles")

        # Reconcile boundary types with wall-function BCs (safety net)
        self._reconcile_boundary_wall_types(boundary_path, case_dir)

    # OpenFOAM environment script (same path as mesh.py uses)
    _FOAM_BASHRC = "/usr/lib/openfoam/openfoam2406/etc/bashrc"

    def _run_cmd(self, cmd: str, workdir: Path) -> None:
        """Run an OpenFOAM command, streaming output via loguru.

        Sources the OpenFOAM environment first so that shared libraries and PATH
        are available even when the parent process was started without sourcing bashrc.
        """
        logger.info(f"Running: {cmd}  (in {workdir})")
        if Path(self._FOAM_BASHRC).exists():
            shell_cmd = f'source "{self._FOAM_BASHRC}" && export FOAM_SIGFPE=false && {cmd}'
        else:
            shell_cmd = cmd

        is_mpi = "mpirun" in cmd
        # Workaround: hwloc's GL plugin scans X11 ports (6000-6006) on startup.
        # If a non-X11 service occupies one of these ports (e.g. TensorBoard on 6006),
        # mpirun hangs forever waiting for an X11 handshake response.
        # Fix: create a plugins dir without hwloc_gl.so and point HWLOC_PLUGINS_PATH to it.
        if is_mpi:
            _hwloc_nogl = Path("/tmp/hwloc_plugins_nogl")
            _hwloc_src = Path("/usr/lib/x86_64-linux-gnu/hwloc")
            if _hwloc_src.is_dir() and not _hwloc_nogl.is_dir():
                _hwloc_nogl.mkdir(parents=True, exist_ok=True)
                for p in _hwloc_src.glob("*.so"):
                    if p.name != "hwloc_gl.so":
                        (_hwloc_nogl / p.name).symlink_to(p)
                logger.info(f"Created hwloc plugins dir (no GL): {_hwloc_nogl}")
            if _hwloc_nogl.is_dir():
                shell_cmd = f'export HWLOC_PLUGINS_PATH="{_hwloc_nogl}" && {shell_cmd}'
            # Disable FPE trapping to allow solver to recover from initial transients
            shell_cmd = f'export FOAM_SIGFPE=false && {shell_cmd}'
        try:
            if is_mpi:
                # MPI: write output to log file to avoid pipe deadlock
                mpi_log = workdir / "log.mpi_solver"
                with open(mpi_log, "w") as log_fh:
                    proc = subprocess.Popen(
                        shell_cmd, shell=True, executable="/bin/bash",
                        cwd=str(workdir), stdout=log_fh, stderr=subprocess.STDOUT,
                    )
                    # Tail the log file while process runs
                    import time
                    last_pos = 0
                    while proc.poll() is None:
                        time.sleep(2)
                        with open(mpi_log, "r") as rf:
                            rf.seek(last_pos)
                            new_lines = rf.readlines()
                            last_pos = rf.tell()
                            for line in new_lines:
                                stripped = line.rstrip()
                                if stripped:
                                    logger.info(stripped)
                    # Read remaining output
                    with open(mpi_log, "r") as rf:
                        rf.seek(last_pos)
                        for line in rf:
                            stripped = line.rstrip()
                            if stripped:
                                logger.info(stripped)
            else:
                proc = subprocess.Popen(
                    shell_cmd, shell=True, executable="/bin/bash",
                    cwd=str(workdir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, bufsize=1,
                )
                for line in iter(proc.stdout.readline, ""):
                    stripped = line.rstrip()
                    if stripped:
                        logger.info(stripped)
                proc.wait()
        except Exception as exc:
            logger.error(f"Failed to start '{cmd}': {exc}")
            raise
        return_code = proc.returncode if proc else -1
        if return_code != 0:
            raise RuntimeError(f"'{cmd}' exited with code {return_code}")
