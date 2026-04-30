"""
STLGenRunner — Direct STL pipeline for STLGeneratorForOpenFoam.

This runner uses browser-exported STL files directly instead of re-generating
geometry from JSON parametric descriptions.  dctwin's internal STL generator is
completely bypassed.

Workflow
--------
1.  Read the .cfdrun manifest (model/<basename>.cfdrun — JSON).
2.  Find STL files in     Mesh/constant/triSurface/
3.  Parse solid names from ASCII STL headers
4.  Generate OpenFOAM mesh dictionaries:
      blockMeshDict, surfaceFeatureExtractDict, snappyHexMeshDict
    Leakage solids → faceType internal + cellZone (not walls)
5.  Run serial mesh pipeline:
      blockMesh → surfaceFeatureExtract → snappyHexMesh -overwrite
6.  Build in-memory BridgeBCRegistry from .cfdrun physics data
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
from typing import TYPE_CHECKING, Any, Dict, Iterable, List, NamedTuple, Optional, Set, Tuple

if TYPE_CHECKING:
    from dctwin.runner.bridge_bc import BridgeBCRegistry

from loguru import logger

from dctwin.runner.cfdproj_schema import CFDExport
from dctwin.runner.fields import FieldGenerator, STANDARD_FIELD_NAMES
from dctwin.utils.const import rho_air, air_specific_heat
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


def _compute_rack_min_u_span(rack_data: Dict[str, Any]) -> Optional[int]:
    """Read the frontend-computed `minUSpanForDoorBucket` field.

    The frontend's `_computeRackMinUSpan(rackModel)` in STL_generate.html
    is the single source of truth for the span rule (it walks
    equipments + merged-empty-slot ranges + through-slot groups using
    the front-end state shape). At export time the computed integer
    is written to each rack as `rack.minUSpanForDoorBucket`; this
    helper just reads it.

    Returns None when the field is absent or null — legacy cfdrun
    files and racks with no categorizable entries both land here,
    and `_door_level_for_rack` falls back to the unified door level.

    See docs/superpowers/specs/2026-04-17-rack-door-refinement-design.md
    §Algorithm §Resolve and §Risks (mirror-drift mitigation).
    """
    value = rack_data.get("minUSpanForDoorBucket")
    if value is None:
        return None
    return int(value)


def _door_level_for_rack(rack_data: Dict[str, Any], mesh_config: Dict[str, Any]) -> List[int]:
    """Unified door refinement level — no per-bucket variation.
    All doors share one level since they merge into a single STL file."""
    return list(mesh_config.get("rackDoorRefinementLevel") or [1, 1])


def _door_feature_level_for_rack(rack_data: Dict[str, Any],
                                 mesh_config: Dict[str, Any]) -> int:
    """Unified door feature-line level. Falls back to surface max."""
    explicit = mesh_config.get("rackDoorFeatureLevel")
    if explicit is not None:
        return int(explicit)
    rl = mesh_config.get("rackDoorRefinementLevel") or [1, 1]
    return int(rl[-1]) if rl else 1


_BLANKING_PANEL_SIGMA = 0.6401

# ---- 2026-04-17 refinement architecture redesign ----

_BUCKET_FILE_PATTERNS = {
    "server":    ("merged_server_",    "serverFaceRefinementLevel",    "serverFacePerUnitLevel",    "serverFaceFeatureLevel",    "serverFaceFeaturePerUnitLevel"),
    "blanking":  ("merged_blanking_",  "blankingPlateRefinementLevel", "blankingPlatePerUnitLevel", "blankingPlateFeatureLevel", "blankingPlateFeaturePerUnitLevel"),
    "partition": ("merged_partition_", "rackPartitionRefinementLevel", "rackPartitionPerUnitLevel", "rackPartitionFeatureLevel", "rackPartitionFeaturePerUnitLevel"),
    "through_zone": ("merged_through_zone_", "rackThroughZoneRefinementLevel", "rackThroughZonePerUnitLevel", "rackThroughZoneFeatureLevel", "rackThroughZoneFeaturePerUnitLevel"),
    # wallBaffle shares blanking level/feature keys — it is a subcategory of blanking
    # (half-dissolved U-slot panels that need surface-level faceType internal).
    "wallBaffle":  ("merged_wallBaffle_",  "blankingPlateRefinementLevel", "blankingPlatePerUnitLevel", "blankingPlateFeatureLevel", "blankingPlateFeaturePerUnitLevel"),
    # typicalSmallGap blanking panels (faceType internal + createBaffles).
    "perforatedBlankingPanel": ("merged_perforatedBlankingPanel_", "blankingPlateRefinementLevel", "blankingPlatePerUnitLevel", "blankingPlateFeatureLevel", "blankingPlateFeaturePerUnitLevel"),
    "wallBlankingPanel": ("merged_wallBlankingPanel_", "blankingPlateRefinementLevel", "blankingPlatePerUnitLevel", "blankingPlateFeatureLevel", "blankingPlateFeaturePerUnitLevel"),
}


def _parse_bucket_from_filename(filename: str) -> Optional[Tuple[str, str]]:
    """For per-bucket STL files, return (category, bucket_key). None for scene-merged.

    Recognises two formats:
      - ``{prefix}<N>U.stl``              — original multi-solid bucket file
      - ``{prefix}<N>U__<zone>.stl``      — per-zone file (through-zone only,
        2026-04-18+). One closed-surface per file so snappy's
        ``refinementRegions mode=inside`` closure test accepts the shell.

    Both forms share the same ``<N>U`` bucket key, so downstream level
    lookups are unchanged.
    """
    for cat, (prefix, *_rest) in _BUCKET_FILE_PATTERNS.items():
        if not filename.startswith(prefix):
            continue
        m = re.match(rf"{re.escape(prefix)}(\d+)U__[A-Za-z0-9_]+\.stl$", filename)
        if m:
            return (cat, m.group(1))
        m = re.match(rf"{re.escape(prefix)}(\d+)U\.stl$", filename)
        if m:
            return (cat, m.group(1))
        m = re.match(rf"{re.escape(prefix)}[A-Za-z]+_(\d+)U\.stl$", filename)
        if m:
            return (cat, m.group(1))
    return None


def _bucket_level_lookup(category: str, bucket_key: str,
                        mesh_config: Dict[str, Any]) -> List[int]:
    """Resolve surface refinement level for (category, bucket). Priority:
    per-unit dict > unified pair > [1, 1]."""
    spec = _BUCKET_FILE_PATTERNS.get(category)
    if spec is None:
        return [1, 1]
    _prefix, unified_key, per_unit_key, *_ = spec
    per_unit = mesh_config.get(per_unit_key) or {}
    if bucket_key in per_unit:
        return list(per_unit[bucket_key])
    unified = mesh_config.get(unified_key)
    if isinstance(unified, (list, tuple)) and len(unified) == 2:
        return list(unified)
    return [1, 1]


def _bucket_feature_lookup(category: str, bucket_key: str,
                          mesh_config: Dict[str, Any]) -> int:
    """Resolve feature-line level for (category, bucket). Priority:
    per-unit dict > unified scalar > 2. For 'door' category, feature
    level = surface max (follows _door_level_for_rack convention)."""
    spec = _BUCKET_FILE_PATTERNS.get(category)
    if spec is None:
        return 2
    _prefix, unified_key, per_unit_key, feat_unified, feat_per_unit = spec
    if feat_unified is None and feat_per_unit is None:
        # door: feature = surface max
        return _bucket_level_lookup(category, bucket_key, mesh_config)[1]
    per_unit = mesh_config.get(feat_per_unit) or {}
    if bucket_key in per_unit:
        return int(per_unit[bucket_key])
    unified = mesh_config.get(feat_unified)
    return int(unified) if unified is not None else 2


def _darcy_forchheimer(sigma: float, depth: float, min_depth: float = 0.1) -> Tuple[float, float]:
    """Compute Darcy (d) and Forchheimer (f) coefficients for rack leakage.

    Uses orifice/perforated-plate model (see AGENTS.md §7.3):
        f = (1/L) × ((1/(Cd×σ)) - 1)²
    where L = rack depth, Cd = 0.6 (discharge coefficient), σ = open area ratio.
    Darcy (viscous) term is set to zero — leakage flow is turbulent.
    """
    sigma = max(0.01, min(0.99, sigma))
    if depth < min_depth:
        logger.warning(
            f"Leakage depth={depth:.4f} is suspiciously small (<{min_depth}m), "
            f"clamping to {min_depth} — check rack geometry 'D' dimension"
        )
        depth = min_depth
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
SurfaceRef = Tuple[str, str]
SceneTriangle = Tuple[str, str, Triangle]

_GEOM_EPS = 1e-9


class PatchGeometry(NamedTuple):
    """Geometric summary of a flat STL patch, used by FieldGenerator
    for physically-grounded turbulence inlet BC computation.

    Fields:
        area:       total surface area in m²
        perimeter:  total boundary-polygon length in m (0.0 if extraction
                    yields no boundary edges — caller should fall back to
                    circular hydraulic-diameter approximation)
        bbox_dims:  axis-aligned bounding box (dx, dy, dz) in m
    """
    area: float
    perimeter: float
    bbox_dims: Tuple[float, float, float]


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


def _quat_normalize(q: Dict[str, float]) -> Tuple[float, float, float, float]:
    qx = float(q.get("x", 0.0))
    qy = float(q.get("y", 0.0))
    qz = float(q.get("z", 0.0))
    qw = float(q.get("w", 1.0))
    norm = math.sqrt(qx * qx + qy * qy + qz * qz + qw * qw)
    if norm <= _GEOM_EPS:
        return (0.0, 0.0, 0.0, 1.0)
    return (qx / norm, qy / norm, qz / norm, qw / norm)


def _quat_rotate(vec: Vec3, quat: Dict[str, float]) -> Vec3:
    qx, qy, qz, qw = _quat_normalize(quat)
    vx, vy, vz = vec
    tx = 2.0 * (qy * vz - qz * vy)
    ty = 2.0 * (qz * vx - qx * vz)
    tz = 2.0 * (qx * vy - qy * vx)
    return (
        vx + qw * tx + (qy * tz - qz * ty),
        vy + qw * ty + (qz * tx - qx * tz),
        vz + qw * tz + (qx * ty - qy * tx),
    )


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


def _compute_boundary_perimeter(triangles: Iterable[Triangle]) -> float:
    """Length of the boundary polygon of a triangle mesh patch.

    An edge (v_i, v_j) belongs to the patch boundary iff it appears in
    exactly one triangle. Shared edges (interior) appear in two; degenerate
    edges are ignored. Vertices are quantised to 1e-6 m so floating-point
    jitter from STL generation does not break edge identity.

    Returns 0.0 for an empty input or a closed-shell topology where every
    edge is shared (e.g. sphere, tetrahedron surface) — caller interprets
    this as 'fall back to circular D_h'.
    """
    edge_counts: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], int] = {}
    edge_lengths: Dict[Tuple[Tuple[int, int, int], Tuple[int, int, int]], float] = {}

    def _quantise(v: Vec3) -> Tuple[int, int, int]:
        return (
            int(round(v[0] * 1_000_000)),
            int(round(v[1] * 1_000_000)),
            int(round(v[2] * 1_000_000)),
        )

    for triangle in triangles:
        qa, qb, qc = _quantise(triangle[0]), _quantise(triangle[1]), _quantise(triangle[2])
        for (va, vb), (qa_k, qb_k) in (
            ((triangle[0], triangle[1]), (qa, qb)),
            ((triangle[1], triangle[2]), (qb, qc)),
            ((triangle[2], triangle[0]), (qc, qa)),
        ):
            if qa_k == qb_k:  # degenerate edge
                continue
            key = (qa_k, qb_k) if qa_k < qb_k else (qb_k, qa_k)
            edge_counts[key] = edge_counts.get(key, 0) + 1
            if key not in edge_lengths:
                edge_lengths[key] = _vec_norm(_vec_sub(vb, va))

    total = 0.0
    for key, count in edge_counts.items():
        if count == 1:
            total += edge_lengths[key]
    return total


def _format_vec(vec: Vec3) -> str:
    return f"({vec[0]:.6f}, {vec[1]:.6f}, {vec[2]:.6f})"


def _partition_hole_expected_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Return expected partition-hole helper STLs for the current export."""
    expected: Dict[str, Dict[str, Any]] = {}
    for part_name, part in (data.get("partitionWalls", {}) or {}).items():
        for hole in part.get("holes", []):
            phys = hole.get("physics", {}) or {}
            # 2026-04-16 simplification: only leakageRate gates the source
            # term now. The legacy `resistanceEnabled` field is no longer
            # checked (still accepted for backward compatibility on load).
            try:
                leakage_rate = float(phys.get("leakageRate", 100.0))
            except (TypeError, ValueError):
                continue
            if leakage_rate >= 100.0:
                continue

            zone_name = hole.get("zoneName", "")
            zone_file = hole.get("zoneStlFile")
            if not zone_name or not zone_file:
                continue

            expected[zone_file] = {
                "part_name": part_name,
                "zone_name": zone_name,
                "zone_file": zone_file,
                "surface_name": _sanitize(Path(zone_file).stem),
                "zone_solid": _sanitize(zone_name),
            }
    return expected


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
    scene_triangles: Iterable[SceneTriangle],
    ignored_surfaces: Iterable[SurfaceRef],
) -> Optional[float]:
    origin = _vec_add(center, _vec_scale(direction, _RAY_START_EPS))
    nearest: Optional[float] = None
    ignored_surface_set = set(ignored_surfaces)
    for filename, solid_name, triangle in scene_triangles:
        if (filename, solid_name) in ignored_surface_set:
            continue
        hit = _ray_triangle_intersection(origin, direction, triangle)
        if hit is None:
            continue
        clearance = hit + _RAY_START_EPS
        if nearest is None or clearance < nearest:
            nearest = clearance
    return nearest


def _flatten_scene_triangles(
    triangle_cache: Dict[str, Dict[str, List[Triangle]]]
) -> List[SceneTriangle]:
    return [
        (filename, solid_name, triangle)
        for filename, solids in triangle_cache.items()
        for solid_name, triangles in solids.items()
        for triangle in triangles
    ]


def _normalized_match_key(name: str) -> str:
    return _sanitize(name).strip("_").lower()


def _resolve_partition_wall_body_surfaces(
    triangle_cache: Dict[str, Dict[str, List[Triangle]]],
    part_name: str,
    wall_solid_names: Iterable[str],
) -> Tuple[Set[SurfaceRef], List[str]]:
    resolved_surfaces: Set[SurfaceRef] = set()
    resolved_names: Set[str] = set()
    missing_names: List[str] = []

    for solid_name in wall_solid_names:
        if not solid_name:
            continue
        matched = False
        for filename, solids in triangle_cache.items():
            if solid_name in solids:
                resolved_surfaces.add((filename, solid_name))
                resolved_names.add(solid_name)
                matched = True
        if not matched:
            missing_names.append(solid_name)

    if missing_names:
        quoted = ", ".join(repr(name) for name in missing_names)
        raise RuntimeError(
            f"Cannot validate partition wall seed placement for '{part_name}': "
            f"wall body solid(s) {quoted} were not parsed from any grouped STL file"
        )

    if resolved_surfaces:
        return resolved_surfaces, sorted(resolved_names)

    part_key = _normalized_match_key(part_name)
    expected_body_key = f"{part_key}_body"
    expected_body_prefix = f"{expected_body_key}_"
    preferred_filenames = [
        filename
        for filename in triangle_cache
        if "partition_wall" in _normalized_match_key(Path(filename).stem)
    ]
    def _collect_matches(filenames: Iterable[str]) -> None:
        for filename in filenames:
            for solid_name in triangle_cache.get(filename, {}):
                solid_key = _normalized_match_key(solid_name)
                if solid_key == expected_body_key or solid_key.startswith(expected_body_prefix):
                    resolved_surfaces.add((filename, solid_name))
                    resolved_names.add(solid_name)

    _collect_matches(preferred_filenames or list(triangle_cache.keys()))
    if not resolved_surfaces and preferred_filenames:
        _collect_matches(triangle_cache.keys())

    if resolved_surfaces:
        return resolved_surfaces, sorted(resolved_names)

    raise RuntimeError(
        f"Cannot validate partition wall seed placement for '{part_name}': "
        "wallSolidNames is missing/empty and the wall body solid cannot be inferred "
        f"from the partition wall name (expected naming like '{part_name}_body')"
    )


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
# BlockMesh grid-alignment optimisation
# ---------------------------------------------------------------------------

def optimize_blockmesh_cells(
    room_bounds: Dict[str, Tuple[float, float]],
    base_size: float,
    rack_configs: List[Dict[str, Any]],
    search_range: int = 12,
) -> Dict[str, Tuple[float, float, int]]:
    """Backward-compat wrapper — returns single-block result from multi-block.

    Returns ``{axis: (bm_min, bm_max, n_cells)}`` for each of x, y, z.
    For the actual multi-block generation, use ``compute_multiblock_grid``.
    """
    mb = compute_multiblock_grid(room_bounds, base_size, rack_configs)
    result: Dict[str, Tuple[float, float, int]] = {}
    for axis in ("x", "y", "z"):
        breaks = mb[axis]
        bm_min = breaks[0][0]
        bm_max = breaks[-1][1]
        n_total = sum(seg[2] for seg in breaks)
        result[axis] = (bm_min, bm_max, n_total)
    return result


def compute_multiblock_grid(
    room_bounds: Dict[str, Tuple[float, float]],
    base_size: float,
    rack_configs: List[Dict[str, Any]],
) -> Dict[str, List[Tuple[float, float, int]]]:
    """Compute non-uniform multi-block grid with exact rack-edge alignment.

    Returns ``{axis: [(seg_min, seg_max, n_cells), ...]}`` per axis.
    Breakpoints are room boundaries + rack bounding-box edges. Each segment
    gets ``max(1, round(length / base_size))`` cells so all critical edges
    sit exactly on grid lines while cell sizes stay close to base_size.
    """
    _AXIS_ALIGNED = {0, 90, 180, 270}

    critical: Dict[str, set] = {"x": set(), "y": set(), "z": set()}

    for rc in rack_configs:
        ori = round(rc.get("orientation", 0)) % 360
        if ori not in _AXIS_ALIGNED:
            continue

        a = rc["anchor"]
        s = rc["size"]
        W, D, H = s["W"], s["D"], s["H"]
        inlet_off = rc.get("inletOffset", 0)
        outlet_off = rc.get("outletOffset", 0)

        critical["y"].update([a["y"], a["y"] + H])

        if ori in (0, 180):
            depth_axis, width_axis = "z", "x"
            depth_start, width_start = a["z"], a["x"]
        else:
            depth_axis, width_axis = "x", "z"
            depth_start, width_start = a["x"], a["z"]

        critical[depth_axis].update([
            depth_start,
            depth_start + D,
        ])
        if inlet_off > 1e-9:
            critical[depth_axis].add(depth_start + inlet_off)
        if outlet_off > 1e-9:
            critical[depth_axis].add(depth_start + D - outlet_off)
        critical[width_axis].update([width_start, width_start + W])

    _MERGE_TOL = base_size * 0.1

    result: Dict[str, List[Tuple[float, float, int]]] = {}

    for axis in ("x", "y", "z"):
        r_min, r_max = room_bounds[axis]

        raw = sorted(critical[axis] | {r_min, r_max})
        raw = [b for b in raw if r_min - _MERGE_TOL <= b <= r_max + _MERGE_TOL]
        breaks = [raw[0]] if raw else [r_min]
        for b in raw[1:]:
            if b - breaks[-1] > _MERGE_TOL:
                breaks.append(b)
            else:
                breaks[-1] = (breaks[-1] + b) / 2.0
        if abs(breaks[0] - r_min) < _MERGE_TOL:
            breaks[0] = r_min
        if abs(breaks[-1] - r_max) < _MERGE_TOL:
            breaks[-1] = r_max
        if not breaks or breaks[0] != r_min:
            breaks.insert(0, r_min)
        if breaks[-1] != r_max:
            breaks.append(r_max)
        breaks = sorted(set(breaks))

        segments: List[Tuple[float, float, int]] = []

        pad_cs = base_size
        if len(breaks) >= 2:
            first_seg = breaks[1] - breaks[0]
            if first_seg > 1e-9:
                pad_cs = first_seg / max(1, round(first_seg / base_size))

        pad_lo_size = pad_cs
        segments.append((r_min - pad_lo_size, r_min, 1))

        for i in range(len(breaks) - 1):
            seg_min = breaks[i]
            seg_max = breaks[i + 1]
            length = seg_max - seg_min
            if length < 1e-9:
                continue
            n = max(1, round(length / base_size))
            segments.append((seg_min, seg_max, n))

        if len(breaks) >= 2:
            last_seg = breaks[-1] - breaks[-2]
            if last_seg > 1e-9:
                pad_cs = last_seg / max(1, round(last_seg / base_size))
        pad_hi_size = pad_cs
        segments.append((r_max, r_max + pad_hi_size, 1))

        result[axis] = segments

    return result


# ---------------------------------------------------------------------------
# Main runner class
# ---------------------------------------------------------------------------

class STLGenRunner:
    """Run the stlgen (direct STL) pipeline for a CFD case."""

    def __init__(self, case_dir: Path, cfdrun_path: Optional[Path] = None) -> None:
        """Construct a runner for `case_dir`.

        `cfdrun_path` — explicit path to the `.cfdrun` manifest (JSON).
        If omitted, the runner globs `<case_dir>/model/*.cfdrun`:

        - exactly one `.cfdrun` found → used;
        - zero `.cfdrun` found → `FileNotFoundError` (no silent fallback —
          deliberate per the 2026-04-18 no-silent-fallback policy);
        - two or more `.cfdrun` files → `RuntimeError` asking the user to
          pass `cfdrun_path=` explicitly.
        """
        self.case_dir = Path(case_dir).resolve()
        self.mesh_dir = self.case_dir / "Mesh"
        self.model_dir = self.case_dir / "model"
        self.tri_surface_dir = self.mesh_dir / "constant" / "triSurface"

        if cfdrun_path is None:
            candidates = sorted(self.model_dir.glob("*.cfdrun"))
            if len(candidates) == 0:
                raise FileNotFoundError(
                    f"no *.cfdrun file found in {self.model_dir}. Re-export "
                    f"from the STL UI (or POST /api/browser/export-cfdrun "
                    f"for the manifest — STL files are a separate re-export "
                    f"via /api/browser/export-stl), or pass cfdrun_path= "
                    f"explicitly."
                )
            if len(candidates) > 1:
                raise RuntimeError(
                    f"multiple *.cfdrun files in {self.model_dir}: "
                    f"{[c.name for c in candidates]}. Pass cfdrun_path= "
                    f"explicitly to disambiguate."
                )
            cfdrun_path = candidates[0]
        self.cfdrun_path = Path(cfdrun_path).resolve()

        self._data: Optional[Dict[str, Any]] = None   # parsed .cfdrun manifest
        self._stl_solids: Dict[str, List[str]] = {}   # {filename: [solidName, …]}
        self._baffle_physics: Dict[str, Dict] = {}   # {sanitized_solid: {baffleType, porosity}}
        self._wall_baffle_zone_names: set = set()     # populated by _generate_create_baffles_dict()
        # In-memory bridge BC registry; populated by _build_bridge_bc() during
        # _prepare_runtime_metadata(). Replaces the old model/bridge_bc.json file.
        self._bridge_registry: Optional["BridgeBCRegistry"] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate_geometry(self) -> None:
        """Pre-flight check: ensure room.height is consistent with floor y-coordinates.

        Raises RuntimeError if any floor's anchor y exceeds room.height, which would
        cause the room enclosure walls to be shorter than the geometry, leading to
        unclosed mesh domains and baffle seed placement failures.
        """
        if self._data is None:
            self._load_cfd_json_only()
        room_height = self._data.get("room", {}).get("height", 0)
        _TOLERANCE = 1e-3  # allow sub-mm floating-point rounding from JS export
        for name, floor in self._data.get("floors", {}).items():
            y = floor.get("geometry", {}).get("anchor", {}).get("y", 0)
            if y > room_height + _TOLERANCE:
                raise RuntimeError(
                    f"floor '{name}' y={y} exceeds room.height={room_height}. "
                    f"The room enclosure (walls) only extends to y={room_height}, "
                    f"but this floor is placed above it. "
                    f"Update the room height in the 2D floor plan (房间 2D 平面设计 → 房间高度) "
                    f"to at least {y}."
                )

    def _load_cfd_json_only(self) -> None:
        """Load and validate the .cfdrun manifest without requiring STL files."""
        if not self.cfdrun_path.exists():
            raise FileNotFoundError(f"cfdrun input file not found: {self.cfdrun_path}")
        with open(self.cfdrun_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        from dctwin.runner.cfdproj_schema import CFDExport
        validated = CFDExport.model_validate(raw)
        self._data = validated.model_dump(mode="json")

    def run_mesh_only(self) -> Path:
        """Generate mesh + complete OpenFOAM case in Mesh/ directory."""
        logger.info("=== STLGen: Mesh-Only Mode ===")
        self._load()
        self.validate_geometry()
        self._preflight_partition_wall_thickness()
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
        self._remove_disconnected_cells(self.mesh_dir)
        # Generate complete case files in Mesh/. Mesh-only mode is tolerant
        # of fan_outlet BC issues (missing inlet patches → placeholder BC +
        # warning); the solve-path call sites keep strict=True so the real
        # error surfaces when the user tries to solve.
        self._setup_complete_case_in_mesh(strict=False)
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
        self._preflight_partition_wall_thickness()
        self._prepare_runtime_metadata()
        self._generate_mesh_dicts()
        mc = self._data.get("meshConfig", {})
        parallel_mesh = mc.get("parallelMesh", False)
        n_procs = mc.get("meshProcess", 1)
        if parallel_mesh and n_procs > 1:
            logger.info(f"Parallel mesh enabled: {n_procs} processes")
            self._run_parallel_mesh_pipeline(n_procs)
        else:
            logger.info("Serial mesh mode")
            self._run_serial_mesh_pipeline()
        self._remove_disconnected_cells(self.mesh_dir)
        self._setup_complete_case_in_mesh()
        return self._run_solve_from_mesh()

    def _prepare_runtime_metadata(self) -> None:
        """Refresh STLGen runtime metadata without generating config.toml/room.json."""
        self._cleanup_stale_compat_files()
        self._build_bridge_bc()

    def _cleanup_stale_compat_files(self) -> None:
        """Remove old STLGen-generated compatibility files from previous runs."""
        config_path = self.case_dir / "config.toml"
        if config_path.exists():
            try:
                config_text = config_path.read_text(encoding="utf-8")
            except Exception:
                config_text = ""
            if config_text.startswith("# Auto-generated by STLGenRunner from"):
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

        # Delete legacy bridge_bc.json (now in-memory, never written to disk).
        # Safe to remove: if the user rolls back to pre-refactor main, the next
        # run regenerates it from scratch — it is a derived artifact, not an
        # input file.
        stale_bridge = self.model_dir / "bridge_bc.json"
        if stale_bridge.exists():
            stale_bridge.unlink()
            logger.info(f"Removed stale bridge_bc.json (now in-memory): {stale_bridge}")

    def _compute_patch_geometry(self) -> Dict[str, PatchGeometry]:
        """Compute area, boundary perimeter, and bbox for each STL solid.

        Returns dict mapping sanitized solid name → PatchGeometry.
        Used by FieldGenerator to derive physically-grounded k/epsilon
        inlet BCs (rectangular hydraulic diameter D_h = 4A/P).
        """
        geometries: Dict[str, PatchGeometry] = {}
        for stl_file in sorted(self.tri_surface_dir.glob("*.stl")):
            try:
                solids = _read_ascii_stl_triangles(stl_file)
            except Exception as exc:
                logger.warning(
                    f"Could not read {stl_file.name} for patch geometry: {exc}"
                )
                continue
            for solid_name, triangles in solids.items():
                if not triangles:
                    continue
                total_area = 0.0
                xs: List[float] = []
                ys: List[float] = []
                zs: List[float] = []
                for tri in triangles:
                    a, _ = _triangle_area_and_normal(tri)
                    if a > _GEOM_EPS:
                        total_area += a
                    for v in tri:
                        xs.append(v[0])
                        ys.append(v[1])
                        zs.append(v[2])
                if total_area <= _GEOM_EPS:
                    continue
                perimeter = _compute_boundary_perimeter(triangles)
                bbox = (
                    max(xs) - min(xs),
                    max(ys) - min(ys),
                    max(zs) - min(zs),
                )
                geometries[_sanitize(solid_name)] = PatchGeometry(
                    area=total_area,
                    perimeter=perimeter,
                    bbox_dims=bbox,
                )
        return geometries

    def _build_field_generator(self) -> FieldGenerator:
        """Build a field generator for STLGen that does not depend on CaseConfig.

        Injects the in-memory bridge registry (built earlier in
        _prepare_runtime_metadata()) so FieldGenerator can render 0/ fields
        without touching disk for BC lookups.
        """
        return FieldGenerator(
            case_dir=self.case_dir,
            simulation_config=self._data.get("simulationConfig", {}),
            room_config={},
            patch_geometry=self._compute_patch_geometry(),
            bridge_registry=self._bridge_registry,
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

    def _setup_complete_case_in_mesh(self, *, strict: bool = True) -> None:
        """Generate case files in Mesh/ EXCEPT controlDict and fvSolution.

        Args:
            strict: When True (default, solve-path), raise on BC conditions
                that cannot be built (e.g. fan_outlet's paired inlet dropped
                by snappy). When False (mesh-path), emit placeholder BCs so
                the mesh pipeline completes; the solve gate will re-raise.

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
        # converted back to wall type. Use the in-memory bridge registry as the source of truth.
        self._ensure_wall_baffles_are_wall_type(boundary_path)

        patches = read_boundary_patches(boundary_path)
        logger.info(f"Setting up complete case: {len(patches)} patches")

        # 2. Generate 0/ field files
        field_generator = self._build_field_generator()
        field_generator.generate_fields(mesh_dir, patches, strict=strict)
        logger.info(f"Generated field files in {mesh_dir / '0'}")

        # 2b. Reconcile boundary types: ensure any patch that received wall-function
        # BCs in the field files also has 'type wall' in the boundary file.
        self._reconcile_boundary_wall_types(boundary_path, mesh_dir)

        # 3. Generate system/ files (fvOptions, fvSchemes, constant/ files)
        #    NOTE: controlDict and fvSolution are NOT generated here — they are
        #    generated at solve-time via _generate_solver_dicts()
        # 2026-04-17 architecture change: rack through-zones are no longer
        # modeled as cellZones + DarcyForchheimer porous source. They are pure
        # fluid regions; pressure drop is provided by the perforated rack doors
        # (createBaffles). Only partition-hole porous sources remain in fvOptions.
        fvoptions_zones = self._build_partition_hole_fvoptions_config()
        generate_fv_options(mesh_dir, fvoptions_zones)
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

        # (Historical Step 6 — pre-solve decomposePar — removed 2026-04-17:
        # the solve pipeline in _run_solve_from_mesh() already runs decomposePar
        # unconditionally when simulationConfig.parallelSolve is true, so doing
        # it here was always redundant. Removing it saves one full decompose
        # pass per mesh build on parallelSolve=true cases.)

    def _generate_solver_dicts(self, run_dir: Path) -> None:
        """Generate controlDict and fvSolution in the given run directory.

        Called at solve-time (after copying Mesh/ to log/<timestamp>/) so the
        user's latest solver settings are always used.
        """
        sc = self._data.get("simulationConfig", {})
        solver_type = sc.get("solverType", "steady") or "steady"

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
            solver_type=solver_type,
            n_outer_correctors=int(sc.get("nOuterCorrectors", 1)),
            n_correctors=int(sc.get("nCorrectors", 2)),
        )

        # Regenerate fvSchemes at solve-time (user may have changed preset)
        schemes_config = dict(sc.get("schemes", {}))
        schemes_config.setdefault("ddtScheme", sc.get("ddtScheme", "Euler"))
        schemes_config.setdefault("crankNicolsonCoeff", sc.get("crankNicolsonCoeff", 0.9))
        generate_fv_schemes(system_dir, schemes_config, solver_type=solver_type)

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
            r'type\s+(?:(?:compressible::)?)?'
            r'(?:nutkWallFunction|nutk?Wall[Ff]unction|'
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
        """Copy Mesh/ to log/date/ and run solver.

        The ACU mass-balance preflight runs here (not in run_mesh_only), so
        a BC mismatch gates the SOLVE button only — mesh generation itself
        doesn't depend on supply/return totals and stays unblocked.
        """
        self._preflight_mass_balance()
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

        # Parallel-vs-serial decision is driven by simulationConfig.parallelSolve,
        # not by pre-existing processor* directories in the copied Mesh/. The mesh
        # pipeline now ends with a serial reconstructed polyMesh (no processor dirs);
        # if parallelSolve=true the solve pipeline decomposes the run copy here.
        # This keeps mesh outputs clean and means mesh.procs vs solve.procs are
        # independent — the user can re-solve with a different core count without
        # rebuilding the mesh.
        sc = self._data.get("simulationConfig", {})
        parallel_solve = bool(sc.get("parallelSolve", False))
        n_procs = int(sc.get("solveProcess", 1) or 1)
        solver_type = sc.get("solverType", "steady") or "steady"
        solver = (
            "buoyantBoussinesqPimpleFoam"
            if solver_type == "transient"
            else "buoyantBoussinesqSimpleFoam"
        )

        try:
            if parallel_solve and n_procs > 1:
                self._decompose_for_solve(run_dir, n_procs)
                logger.info(f"Running {solver} in parallel with {n_procs} processes")
                self._run_cmd(
                    f"mpirun -np {n_procs} {solver} -parallel",
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

        # 2026-04-19: auto-harvest sensor results for CLI parity with
        # the browser UI's /api/harvest-sensor-results flow. Failures
        # must not fail the run — the raw probes/ are already on disk.
        try:
            self._auto_harvest_sensors()
        except Exception as exc:
            logger.warning(
                f"Sensor harvest failed (non-fatal; raw probes remain on disk): {exc}"
            )

        return run_dir

    def _auto_harvest_sensors(self) -> None:
        """Write ``{run_dir}/sensorResults/{stem}_sensor_HH-MM-SS.json``.

        Pulls sensor names from ``self._data['sensorPoints']`` in the
        same order ``_extract_sensor_points`` emitted to ``probeLocations``
        so column indices align. Silently skips when probes weren't
        written (see sensor_harvest.harvest_sensor_results).
        """
        from dctwin.runner.sensor_harvest import harvest_sensor_results

        sensor_names: List[str] = []
        for i, sensor in enumerate(self._data.get("sensorPoints", []) or []):
            if not isinstance(sensor, dict):
                continue
            if "x" not in sensor or "y" not in sensor or "z" not in sensor:
                continue
            sensor_names.append(str(sensor.get("name") or f"sensor_{i + 1}"))

        if not sensor_names:
            logger.info("Sensor harvest skipped: no sensorPoints in cfdrun")
            return

        unit_t = (self._data.get("simulationConfig", {}) or {}).get(
            "unitTemperature", "C"
        )
        case_name = self.cfdrun_path.stem
        harvest_sensor_results(
            self.case_dir,
            case_name=case_name,
            sensor_names=sensor_names,
            unit_temperature=unit_t,
        )

    # _build_leakage_fvoptions_config — REMOVED 2026-04-17.
    # Through-zones are no longer modeled as cellZones + DarcyForchheimer porous source.
    # They are pure fluid; pressure drop is provided by the perforated rack doors
    # (createBaffles porousBafflePressure). Only partition-hole porous sources remain
    # in fvOptions (see _build_partition_hole_fvoptions_config).
    # (formerly called _build_leakage_fvoptions_config)

    def _build_partition_hole_fvoptions_config(self) -> Dict[str, Any]:
        """Build fvOptions entries for partition-wall helper holes."""
        config: Dict[str, Any] = {}
        for part in self._data.get("partitionWalls", {}).values():
            geom = part.get("geometry", {}) or {}
            size = geom.get("size", {}) or {}
            quat = geom.get(
                "transformQuaternion",
                {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            )
            try:
                depth_value = size.get("D")
                depth = max(0.001, float(depth_value))
            except (TypeError, ValueError):
                depth = 0.001

            # Y-up: D (thickness) is along local-Z; rotate local-Z to world.
            thickness_axis = _quat_rotate((0.0, 0.0, 1.0), quat)
            try:
                e1 = _vec_normalize(thickness_axis)
            except RuntimeError:
                e1 = (0.0, 0.0, 1.0)

            # Pick a fallback axis not parallel to e1 (e1 is horizontal under
            # Y-up, so world-Y is always a safe tangent).
            tmp_axis = (0.0, 1.0, 0.0) if abs(e1[1]) < 0.9 else (1.0, 0.0, 0.0)
            try:
                e2 = _vec_normalize(_vec_cross(tmp_axis, e1))
            except RuntimeError:
                fallback_axis = (1.0, 0.0, 0.0) if tmp_axis == (0.0, 0.0, 1.0) else (0.0, 0.0, 1.0)
                e2 = _vec_normalize(_vec_cross(fallback_axis, e1))

            for hole in part.get("holes", []):
                phys = hole.get("physics", {}) or {}
                # 2026-04-16: only leakageRate gates the source term.
                try:
                    leakage_rate = float(phys.get("leakageRate", 100.0))
                except (TypeError, ValueError):
                    continue
                if leakage_rate >= 100.0:
                    continue

                zone_name = hole.get("zoneName", "")
                zone_file = hole.get("zoneStlFile")
                if not zone_name or not zone_file:
                    continue

                zone_path = self.tri_surface_dir / zone_file
                if not zone_path.exists():
                    continue

                surface_name = _sanitize(Path(zone_file).stem)
                centroid = _compute_stl_solid_centroid(zone_path, surface_name)
                if centroid is None:
                    continue

                sigma = max(0.0, min(1.0, leakage_rate / 100.0))
                d_coeff, f_coeff = _darcy_forchheimer(sigma, depth, min_depth=0.001)
                cellzone_name = surface_name
                origin = f"({centroid[0]:.6f} {centroid[1]:.6f} {centroid[2]:.6f})"
                config[cellzone_name] = type("ZoneCfg", (), {
                    "cellzone_name": cellzone_name,
                    "sigma": sigma,
                    "d": f"({d_coeff:.6g} 0 0)",
                    "f": f"({f_coeff:.6g} 0 0)",
                    "origin": origin,
                    "e1": f"({e1[0]:.6g} {e1[1]:.6g} {e1[2]:.6g})",
                    "e2": f"({e2[0]:.6g} {e2[1]:.6g} {e2[2]:.6g})",
                })()
                logger.info(
                    f"Partition hole fvOptions: {cellzone_name}, sigma={sigma}, "
                    f"depth={depth}, f={f_coeff:.4g}"
                )
        return config

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _ensure_watertight_room_enclosure(self) -> None:
        """Regenerate room_wall.stl and merged_floor_ceiling.stl from room data
        to ensure they form a watertight enclosure with shared vertices.

        Supports arbitrary polygon rooms (triangles, L-shapes, etc.) by
        extruding the polygon along Y (Y-up convention) to form a prismatic
        enclosure. Floor and ceiling are triangulated using ear-clipping.

        Y-up notes: room.plane[i] is a face-local floor-plan point; the
        frontend serializes it as {x, y, z=0} where `y` is actually the
        Z-axis (depth) under Y-up. We rename it to `pz` internally and
        extrude along Y.
        """
        import math

        data = self._data
        room = data.get("room", {})
        plane = room.get("plane", [])
        height = room.get("height", 0)
        if len(plane) < 3 or height <= 0:
            return

        # Extract polygon vertices in order. pts[i] = (world_x, world_z),
        # since plane[i].y holds the face-local 2nd coord (world Z under Y-up).
        pts = [(p["x"], p["y"]) for p in plane]
        n = len(pts)
        y0, y1 = 0.0, height  # vertical extrusion along Y

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
            ax, az = pts[i]
            bx, bz = pts[j]
            # Wall quad vertices in world (x, y_vertical, z_depth).
            bl = (ax, y0, az)
            br = (bx, y0, bz)
            tr = (bx, y1, bz)
            tl = (ax, y1, az)
            # Outward normal (pointing away from polygon interior) in XZ plane.
            dx, dz = bx - ax, bz - az
            length = math.sqrt(dx * dx + dz * dz)
            if length < 1e-10:
                continue
            # Normal = perpendicular to edge in the horizontal XZ plane.
            # For a CCW polygon (when viewed from above, +Y looking down),
            # the outward normal is (dz, 0, -dx) / length.
            nx, ny, nz = dz / length, 0.0, -dx / length

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
            # Floor at Y=0 under Y-up; normal pointing down (-Y).
            fc_stl += _tri(
                (pts[i][0], y0, pts[i][1]),
                (pts[k][0], y0, pts[k][1]),  # reversed winding for -Y normal
                (pts[j][0], y0, pts[j][1]),
                0, -1, 0)
        fc_stl += f"endsolid {floor_name}\n"

        ceil_name = fc_solids[1] if len(fc_solids) > 1 else "Room_top"
        fc_stl += f"solid {ceil_name}\n"
        for i, j, k in tris:
            # Ceiling at Y=height under Y-up; normal pointing up (+Y).
            fc_stl += _tri(
                (pts[i][0], y1, pts[i][1]),
                (pts[j][0], y1, pts[j][1]),
                (pts[k][0], y1, pts[k][1]),
                0, 1, 0)
        fc_stl += f"endsolid {ceil_name}\n"

        fc_path = self.tri_surface_dir / "merged_floor_ceiling.stl"
        fc_path.write_text(fc_stl, encoding="utf-8")

        self._stl_solids["room_wall.stl"] = _read_stl_solid_names(wall_path)
        self._stl_solids["merged_floor_ceiling.stl"] = _read_stl_solid_names(fc_path)

        xs = [p[0] for p in pts]
        zs = [p[1] for p in pts]
        logger.info(
            f"Regenerated watertight room enclosure (Y-up): "
            f"room_wall.stl ({len(self._stl_solids['room_wall.stl'])} walls), "
            f"merged_floor_ceiling.stl ({len(self._stl_solids['merged_floor_ceiling.stl'])} faces), "
            f"polygon={n} verts, y={y0:.2f}-{y1:.2f} (vertical), "
            f"floor-plan x=[{min(xs):.2f},{max(xs):.2f}] z=[{min(zs):.2f},{max(zs):.2f}]"
        )

    def _load(self) -> None:
        """Load the cfdrun/cfd_export JSON, discover STL solid names, and prepare
        STL files for snappyHexMesh (merge perforated into floor_ceiling,
        split wall vs internal solids into separate files)."""
        if not self.cfdrun_path.exists():
            raise FileNotFoundError(f"cfdrun input file not found: {self.cfdrun_path}")
        with open(self.cfdrun_path, encoding="utf-8") as fh:
            raw = json.load(fh)
        validated = CFDExport.model_validate(raw)
        self._data = validated.model_dump(mode="json")
        logger.info(f"Loaded cfdrun input: {self.cfdrun_path}")

        # Discover STL files and parse solid names
        # Only consider browser-exported grouped STL files (merged_*.stl + room_wall.stl).
        # Individual per-entity STL files from old pipeline runs are intentionally ignored.
        # 2026-04-17 redesign: also accept per-bucket files (merged_door_NU.stl, etc.),
        # merged_rack_frame.stl, and legacy leakage_*.stl files (pre-2026-04-17 naming).
        GROUPED_STL_NAMES = {
            "merged_rack.stl",
            "merged_rack_frame.stl",        # 2026-04-17 redesign: rack frame
            "merged_acu.stl",
            "merged_baffle.stl",
            "merged_all_baffles.stl",       # new browser export: all baffle/door/wallBaffle solids

            "merged_floor_ceiling.stl",
            "merged_perforated.stl",
            "merged_perforated_baffles.stl",  # new browser export: perforated subset
            "merged_wall_baffles.stl",      # new browser export: wall baffle subset
            "merged_box.stl",
            "merged_partition_wall.stl",
            "merged_leakage.stl",   # legacy pre-2026-04-17 scene-merged through-zone file
            "room_wall.stl",
            "merged_rack_door.stl",         # baffle merge: all rack doors
            "merged_wall_baffle.stl",       # baffle merge: all wall baffles
        }
        self._stl_solids = {}
        if self.tri_surface_dir.exists():
            for stl_path in sorted(self.tri_surface_dir.glob("*.stl")):
                name = stl_path.name
                # Accept: static grouped names, partition-hole helpers,
                # per-bucket files (merged_server_NU / merged_blanking_NU /
                # merged_partition_NU / merged_through_zone_NU / merged_door_NU),
                # and legacy individual leakage_*.stl files (pre-2026-04-17 naming).
                if (name not in GROUPED_STL_NAMES
                        and not name.startswith("partition_hole_")
                        and not name.startswith("leakage_")
                        and not name.startswith("merged_door_")
                        and not name.startswith("perforated_baffle_")
                        and not name.startswith("perforated_floor_")
                        and _parse_bucket_from_filename(name) is None):
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

        expected_partition_helpers = _partition_hole_expected_map(self._data)
        for helper_name, expected in expected_partition_helpers.items():
            helper_path = self.tri_surface_dir / helper_name
            if not helper_path.exists():
                raise RuntimeError(
                    f"Referenced partition helper STL '{helper_name}' for zoneName "
                    f"'{expected['zone_name']}' is missing from {self.tri_surface_dir}.\n"
                    f"This usually means the cfdrun was edited (partition-wall hole "
                    f"toggled on) without a fresh mesh upload. Re-export STL files "
                    f"from the browser UI and re-mesh — the helper STL is generated "
                    f"only during the browser export step and cannot be reconstructed "
                    f"from the existing mesh."
                )

            helper_names = _read_stl_solid_names(helper_path)
            if not helper_names:
                raise RuntimeError(
                    f"Referenced partition helper STL '{helper_name}' for zoneName "
                    f"'{expected['zone_name']}' contains no solids"
                )
            if len(helper_names) != 1:
                raise RuntimeError(
                    f"Partition helper STL '{helper_name}' must contain exactly one solid "
                    f"for zoneName '{expected['zone_name']}', got {len(helper_names)}"
                )

            solid_name = helper_names[0]
            surface_name = expected["surface_name"]
            # Accept either the raw zone name (fresh from frontend export) or
            # the file-stem-normalized form written by a previous run-mesh
            # pass below. Without this dual check, solve-only after mesh
            # would always trip the equality test, since the first mesh run
            # rewrites the solid to surface_name.
            sanitized_solid = _sanitize(solid_name)
            if sanitized_solid not in (_sanitize(expected["zone_name"]),
                                       _sanitize(surface_name)):
                raise RuntimeError(
                    f"Partition helper STL '{helper_name}' solid '{solid_name}' does not match "
                    f"expected zoneName '{expected['zone_name']}' (or normalized surface name "
                    f"'{surface_name}') for partition wall '{expected['part_name']}'"
                )

            if solid_name != surface_name:
                text = helper_path.read_text(encoding="utf-8")
                text = re.sub(r"(?m)^solid\s+.*$", f"solid {surface_name}", text, count=1)
                text = re.sub(r"(?m)^endsolid\s+.*$", f"endsolid {surface_name}", text, count=1)
                helper_path.write_text(text, encoding="utf-8")
                logger.info(
                    f"Normalized partition helper surface name: {helper_name} "
                    f"{solid_name!r} -> {surface_name!r}"
                )
                solid_name = surface_name
            self._stl_solids[helper_name] = [solid_name]

        primary_geometry_found = any(name in GROUPED_STL_NAMES for name in self._stl_solids)
        if not primary_geometry_found:
            raise FileNotFoundError(
                f"No grouped STL files (merged_*.stl, room_wall.stl) found in {self.tri_surface_dir}. "
                "Start mesh generation from the browser (which uploads STL files first)."
            )

        helper_stl_names = [name for name in list(self._stl_solids) if name.startswith("partition_hole_")]
        for helper_name in helper_stl_names:
            expected = expected_partition_helpers.get(helper_name)
            if expected is None:
                # Drop from the in-memory map only — never unlink from disk.
                # _load runs in BOTH mesh-only and solve-only modes; deleting
                # a user-uploaded STL here makes a subsequent solve-only fail
                # with "Referenced partition helper STL ... is missing"
                # (the file is irrecoverable without re-meshing from the UI).
                # Mesh-phase re-upload from the browser overwrites triSurface
                # wholesale, which is the right place for cleanup.
                self._stl_solids.pop(helper_name, None)
                logger.info(
                    f"Ignored stale partition helper STL "
                    f"(left on disk for safety): {helper_name}"
                )
                continue

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

        # Restore merge-output files from _split_internal_face_solids() on
        # previous runs back to merged_baffle.stl so the split can re-categorize.
        # merged_wall_baffle.stl, merged_rack_door.stl → merged_baffle.stl
        # perforated_baffle_*.stl, perforated_floor_*.stl → merged_baffle.stl
        # (The actual source file doesn't matter; _split will re-extract.)
        _baffle_restore_target = self.tri_surface_dir / "merged_baffle.stl"
        for merge_leftover in ("merged_wall_baffle.stl", "merged_rack_door.stl"):
            lp = self.tri_surface_dir / merge_leftover
            if lp.exists():
                with open(lp, "r", encoding="utf-8") as f:
                    block_text = f.read().strip()
                if block_text:
                    with open(_baffle_restore_target, "a", encoding="utf-8") as f:
                        f.write("\n" + block_text + "\n")
                    logger.info(f"Restored {merge_leftover} → merged_baffle.stl")
                lp.unlink()
                self._stl_solids.pop(merge_leftover, None)
        if self.tri_surface_dir.exists():
            for pattern in ("perforated_baffle_*.stl", "perforated_floor_*.stl",
                            "merged_door_*.stl", "merged_wallBaffle_*.stl"):
                for pf in sorted(self.tri_surface_dir.glob(pattern)):
                    with open(pf, "r", encoding="utf-8") as f:
                        block_text = f.read().strip()
                    if block_text:
                        with open(_baffle_restore_target, "a", encoding="utf-8") as f:
                            f.write("\n" + block_text + "\n")
                        logger.info(f"Restored {pf.name} → merged_baffle.stl")
                    pf.unlink()
                    self._stl_solids.pop(pf.name, None)
        # Re-read merged_baffle.stl solid names after restoration
        if _baffle_restore_target.exists():
            names = _read_stl_solid_names(_baffle_restore_target)
            if names:
                self._stl_solids["merged_baffle.stl"] = names

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
                # 2026-04-17: legacy is_rack_wall_baffle branch (restored to
                # merged_rack.stl) removed — wallBaffle solids are now handled
                # by the is_rack_thin_wallBaffle check below, which preserves
                # the individual baffle_*.stl file instead of merging back
                # into a legacy container.
                # Check if this solid is a rack thin-baffle (door / wallBaffle /
                # internal partition). These are NOT part of any merged_*.stl
                # at mesh-entry time — they arrive as single-solid baffle_*.stl
                # files, either freshly extracted by _split_internal_face_solids
                # this run OR left over from the previous run. Either way,
                # preserving them (rather than restoring into floor_ceiling)
                # is correct: _split will rewrite them from the corresponding
                # merged_<bucket>.stl file if present, and snappy picks them
                # up via the baffle_* refinementSurfaces branch.
                is_rack_door = any(
                    (rack.get("inletDoor", {}) or {}).get("solidName") == bf_file.stem.replace("baffle_", "", 1)
                    or (rack.get("outletDoor", {}) or {}).get("solidName") == bf_file.stem.replace("baffle_", "", 1)
                    or san == f"{_sanitize(rname)}_inlet_door"
                    or san == f"{_sanitize(rname)}_outlet_door"
                    for rname, rack in self._data.get("racks", {}).items()
                )
                is_rack_thin_wallBaffle = any(
                    san == _sanitize(wb)
                    for rack in self._data.get("racks", {}).values()
                    for wb in rack.get("wallBaffleSolids", [])
                )
                # Rack internal partition (name pattern `{rack}_u{idx+1}_partition`)
                # is a single-face wall that stays in merged_partition_<N>U.stl —
                # never extracted to an individual baffle_*.stl. Any stale
                # baffle_*_partition.stl from a previous (buggy) run is discarded:
                # the fresh export already contains the partition in merged_partition.
                # Asymmetry with the partition-wall helper STL branch above
                # (which preserves stale files on disk): the rack partition's
                # geometry is in merged_partition_<N>U.stl, not in the stale
                # baffle_*.stl, so unlink here is safe for solve-only too —
                # triSurface/*.stl is a meshing input, not a solver input, and
                # solve-only reads the already-built constant/polyMesh/.
                m_part = re.search(r"^(.*)_u\d+_partition$", san)
                is_rack_partition = bool(m_part) and any(
                    m_part.group(1) == _sanitize(rn)
                    for rn in self._data.get("racks", {}).keys()
                )
                if is_rack_partition:
                    if not getattr(self, "_stale_partition_discarded_hint_emitted", False):
                        logger.info("Discarding stale rack partition baffle files "
                                    "(partitions now live in merged_partition_<N>U.stl). "
                                    "Re-export STL from the UI / /api/browser/export-stl "
                                    "to regenerate merged_partition_<N>U.stl.")
                        self._stale_partition_discarded_hint_emitted = True
                    logger.info(f"  discarded stale {bf_file.name}")
                    bf_file.unlink()
                    self._stl_solids.pop(bf_file.name, None)
                    continue
                if is_rack_door or is_rack_thin_wallBaffle:
                    # Restore door/wallBaffle solids to merged_baffle.stl so
                    # _split_internal_face_solids can categorise and re-merge
                    # them into merged_wall_baffle.stl / merged_rack_door.stl.
                    target = self.tri_surface_dir / "merged_baffle.stl"
                    with open(target, "a", encoding="utf-8") as f:
                        f.write("\n" + block_text + "\n")
                    names = _read_stl_solid_names(target)
                    if names:
                        self._stl_solids[target.name] = names
                    bf_file.unlink()
                    self._stl_solids.pop(bf_file.name, None)
                    kind = "door" if is_rack_door else "wallBaffle"
                    logger.info(f"Restored rack {kind} {bf_file.name} → {target.name}")
                    continue
                if is_perf:
                    target = self.tri_surface_dir / "merged_perforated.stl"
                elif is_baffle_model:
                    target = self.tri_surface_dir / "merged_baffle.stl"
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

        # Build baffle physics lookup from .cfdrun data
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
                    entry = {
                        "baffleType": model_phys.get("baffleType", "wall"),
                        "porosity": model_phys.get("porosity", 0),
                    }
                    if model_phys.get("perforated"):
                        entry["perforated"] = dict(model_phys["perforated"])
                    baffle_physics[san_solid] = entry
                    break

        # Third pass: rack inlet/outlet perforated doors.
        # Without this, _generate_createBaffles_dict step 3 would default to
        # {baffleType:"wall", porosity:0}, misclassifying every door as a wall
        # baffle regardless of the user's chosen perforated open-area pct.
        # 2026-04-16 unification: rack doors now carry the same `perforated`
        # block as baffles/floors (instead of a fraction-valued `porosity`).
        # We carry the block through here so downstream classifiers and
        # bridge_bc can derive sigma from openAreaPct identically.
        DEFAULT_DOOR_PERF = {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0}
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                perf = door.get("perforated") or DEFAULT_DOOR_PERF
                baffle_physics[solid] = {
                    "baffleType": "perforated",
                    "perforated": dict(perf),
                }

        self._baffle_physics = baffle_physics

    def _split_internal_face_solids(self) -> None:
        """Categorize and merge baffle/door/wallBaffle solids into grouped STL files.

        Instead of splitting every baffle/door solid into individual baffle_<name>.stl
        files (which creates hundreds of files for large cases, killing snappyHexMesh
        performance), solids are merged into category-grouped files:

        - merged_wall_baffle.stl: all wall baffles (baffle models with baffleType=wall,
          rack wallBaffle solids, floor wallBaffle remaining-area)
        - merged_rack_door.stl: all rack perforated doors
        - perforated_baffle_<name>.stl: perforated baffle model solids (individual)
        - perforated_floor_<name>.stl: perforated floor opening patches (individual)

        Each merged/individual file uses surface-level faceType internal (no named
        regions in the geometry section) and per-solid faceZones.
        """
        data = self._data
        baffle_physics = self._baffle_physics

        # ── Collect all candidate solids with their source file + original name ──
        # {sanitized_name: (source_stl_filename, original_solid_name)}
        candidates: Dict[str, tuple] = {}

        def _register(san: str, src_file: str) -> None:
            """Register a solid for extraction, resolving original name."""
            if san in candidates:
                return
            orig = san
            for s in self._stl_solids.get(src_file, []):
                if _sanitize(s) == san:
                    orig = s
                    break
            candidates[san] = (src_file, orig)

        # 1. Floor solids: wallBaffle remaining-area + perforated/wallBaffle openings
        #    Search both merged_floor_ceiling.stl and merged_baffle.stl (on re-runs,
        #    floor solids may have been restored to merged_baffle.stl).
        # Source files that may contain baffle/door/wallBaffle/floor-patch solids.
        # Includes both old naming (merged_baffle.stl, merged_perforated.stl) and
        # new browser export naming (merged_all_baffles.stl, merged_wall_baffles.stl,
        # merged_perforated_baffles.stl).
        _baffle_source_keys = [
            "merged_baffle.stl", "merged_perforated.stl",
            "merged_all_baffles.stl", "merged_wall_baffles.stl",
            "merged_perforated_baffles.stl",
        ]
        fc_key = "merged_floor_ceiling.stl"
        _floor_search_keys = [fc_key] + _baffle_source_keys
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            san_fn = _sanitize(floor_name)
            op_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            if default_bc == "wallBaffle":
                for search_key in _floor_search_keys:
                    for s in self._stl_solids.get(search_key, []):
                        san = _sanitize(s)
                        if san.startswith(san_fn) and san not in op_names:
                            _register(san, search_key)
            for op in floor.get("openings", []):
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type in ("perforated", "wallBaffle"):
                    san = _sanitize(op["solidName"])
                    # Find which source file has this solid
                    for search_key in _floor_search_keys:
                        if san in {_sanitize(s) for s in self._stl_solids.get(search_key, [])}:
                            _register(san, search_key)
                            break
                    else:
                        # Fallback: register against fc_key even if not found yet
                        _register(san, fc_key)

        # 2. Rack wallBaffle solids — search multiple source files.
        #    Skip solids already in blankingpanel bucket files — the frontend
        #    has routed them correctly and they must not be extracted.
        _blankingpanel_sans: set = set()
        for fn, sols in self._stl_solids.items():
            bkt = _parse_bucket_from_filename(fn)
            if bkt and bkt[0] in ("perforatedBlankingPanel", "wallBlankingPanel"):
                for s in sols:
                    _blankingpanel_sans.add(_sanitize(s))

        rack_wall_baffle_sans: set = set()
        _rack_search_keys = ["merged_rack.stl"] + _baffle_source_keys
        for rack_name, rack in data.get("racks", {}).items():
            for wb_solid in rack.get("wallBaffleSolids", []):
                san = _sanitize(wb_solid)
                if san in _blankingpanel_sans:
                    logger.info(f"Rack wallBaffle {san} already in blankingpanel bucket — skipping extraction")
                    continue
                rack_wall_baffle_sans.add(san)
                found = False
                for candidate_key in _rack_search_keys:
                    if san in {_sanitize(s) for s in self._stl_solids.get(candidate_key, [])}:
                        _register(san, candidate_key)
                        logger.info(f"Rack wallBaffle {san} found in {candidate_key}")
                        found = True
                        break
                if not found:
                    logger.warning(f"Rack wallBaffle {san} (from {rack_name}) NOT found in any source STL")

        # 3. ALL baffle model solids (from merged_baffle.stl and browser exports)
        for bf_key in _baffle_source_keys:
            for solid in self._stl_solids.get(bf_key, []):
                san = _sanitize(solid)
                _register(san, bf_key)

        # 4. Perforated solids (from merged_perforated.stl and browser exports)
        # Already covered by step 3 via _baffle_source_keys

        # 5. Rack doors from bucket files (merged_door_<N>U.stl) — since "door"
        #    was removed from _BUCKET_FILE_PATTERNS, detect by prefix
        for filename, solids in list(self._stl_solids.items()):
            if filename.startswith("merged_door_"):
                for solid in solids:
                    _register(_sanitize(solid), filename)

        # 6. wallBaffle bucket files (blankingpanel buckets are NOT registered —
        #    they're already in the correct file and must not be extracted)
        for filename, solids in list(self._stl_solids.items()):
            bucket = _parse_bucket_from_filename(filename)
            if bucket and bucket[0] == "wallBaffle":
                for solid in solids:
                    _register(_sanitize(solid), filename)

        if not candidates:
            self._discard_or_keep_legacy_through_zone_stl()
            return

        # ── Categorize each solid into one of 4 groups ──
        # Build sets for classification
        door_solid_sans: set = set()
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if door and door.get("enabled"):
                    solid_name = door.get("solidName", f"{rack_san}_{side}_door")
                    door_solid_sans.add(_sanitize(solid_name))

        floor_opening_sans: set = set()
        # Map floor opening sanitized names to their physics type from cfdrun
        floor_opening_type: Dict[str, str] = {}
        floor_wallbaffle_remaining_sans: set = set()
        for floor_name, floor in data.get("floors", {}).items():
            default_bc = floor.get("defaultBC", "wall")
            san_fn = _sanitize(floor_name)
            op_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            for op in floor.get("openings", []):
                op_type = op.get("physics", {}).get("type", "wall")
                if op_type in ("perforated", "wallBaffle"):
                    san_op = _sanitize(op["solidName"])
                    floor_opening_sans.add(san_op)
                    floor_opening_type[san_op] = op_type
            if default_bc == "wallBaffle":
                # Search all possible source files for floor solids
                for search_key in _floor_search_keys:
                    for s in self._stl_solids.get(search_key, []):
                        san = _sanitize(s)
                        if san.startswith(san_fn) and san not in op_names:
                            floor_wallbaffle_remaining_sans.add(san)

        # Classify each candidate
        wall_baffle_solids: Dict[str, tuple] = {}   # san → (src_file, orig_name)
        door_solids: Dict[str, tuple] = {}           # san → (src_file, orig_name)
        perforated_baffle_solids: Dict[str, tuple] = {}  # san → (src_file, orig_name)
        perforated_floor_solids: Dict[str, tuple] = {}   # san → (src_file, orig_name)

        for san, (src_file, orig_name) in candidates.items():
            if san in door_solid_sans:
                door_solids[san] = (src_file, orig_name)
            elif san in floor_opening_sans:
                # Floor openings: use physics type from cfdrun data, not _baffle_physics
                op_type = floor_opening_type.get(san, "wall")
                if op_type == "perforated":
                    perforated_floor_solids[san] = (src_file, orig_name)
                else:
                    # wallBaffle floor opening → goes to wall baffle
                    wall_baffle_solids[san] = (src_file, orig_name)
            elif san in floor_wallbaffle_remaining_sans:
                wall_baffle_solids[san] = (src_file, orig_name)
            elif san in rack_wall_baffle_sans:
                wall_baffle_solids[san] = (src_file, orig_name)
            else:
                # Baffle model solid — classify by physics
                phys = baffle_physics.get(san, {})
                if phys.get("baffleType") == "perforated" or phys.get("porosity", 0) > 0:
                    perforated_baffle_solids[san] = (src_file, orig_name)
                else:
                    wall_baffle_solids[san] = (src_file, orig_name)

        logger.info(f"Baffle merge categorization: "
                    f"{len(wall_baffle_solids)} wall_baffle, "
                    f"{len(door_solids)} door, "
                    f"{len(perforated_baffle_solids)} perforated_baffle, "
                    f"{len(perforated_floor_solids)} perforated_floor")

        # ── Read STL blocks from all source files ──
        all_src_files = set()
        for san, (src, _) in {**wall_baffle_solids, **door_solids,
                               **perforated_baffle_solids, **perforated_floor_solids}.items():
            all_src_files.add(src)

        stl_blocks: Dict[str, Dict[str, str]] = {}  # {filename: {sanitized: block_text}}
        for filename in all_src_files:
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

        def _has_facets(block: str) -> bool:
            return "facet normal" in block

        # Helper to extract block text for a solid
        def _get_block(san: str, src_file: str) -> Optional[str]:
            return stl_blocks.get(src_file, {}).get(san)

        # ── Write merged_wall_baffle.stl ──
        # All wallBaffle solids (floor, baffle models, rack fullBlock) go here.
        # typicalSmallGap rack blanking is now routed by the frontend to
        # merged_blankingpanel_<N>U.stl and never appears in wallBaffle buckets.
        extracted_from: Dict[str, set] = {}  # track which solids extracted from which file

        if wall_baffle_solids:
            wb_path = self.tri_surface_dir / "merged_wall_baffle.stl"
            wb_parts = []
            wb_names = []
            for san, (src_file, orig_name) in wall_baffle_solids.items():
                block = _get_block(san, src_file)
                if block is None:
                    continue
                if not _has_facets(block):
                    logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                    extracted_from.setdefault(src_file, set()).add(san)
                    continue
                wb_parts.append(block)
                wb_names.append(orig_name)
                extracted_from.setdefault(src_file, set()).add(san)
            if wb_parts:
                with open(wb_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(wb_parts) + "\n")
                self._stl_solids["merged_wall_baffle.stl"] = wb_names
                logger.info(f"Wrote merged_wall_baffle.stl: {len(wb_names)} solids")

        # ── Write merged_rack_door.stl ──
        if door_solids:
            dr_path = self.tri_surface_dir / "merged_rack_door.stl"
            dr_text_parts = []
            dr_orig_names = []
            for san, (src_file, orig_name) in door_solids.items():
                block = _get_block(san, src_file)
                if block is None:
                    continue
                if not _has_facets(block):
                    logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                    extracted_from.setdefault(src_file, set()).add(san)
                    continue
                dr_text_parts.append(block)
                dr_orig_names.append(orig_name)
                extracted_from.setdefault(src_file, set()).add(san)
            if dr_text_parts:
                with open(dr_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(dr_text_parts) + "\n")
                self._stl_solids["merged_rack_door.stl"] = dr_orig_names
                logger.info(f"Wrote merged_rack_door.stl: {len(dr_orig_names)} solids")

        # ── Write individual perforated_baffle_<name>.stl ──
        for san, (src_file, orig_name) in perforated_baffle_solids.items():
            block = _get_block(san, src_file)
            if block is None:
                continue
            if not _has_facets(block):
                logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                extracted_from.setdefault(src_file, set()).add(san)
                continue
            ind_file = f"perforated_baffle_{san}.stl"
            ind_path = self.tri_surface_dir / ind_file
            with open(ind_path, "w", encoding="utf-8") as f:
                f.write(block + "\n")
            self._stl_solids[ind_file] = [orig_name]
            extracted_from.setdefault(src_file, set()).add(san)
            logger.info(f"Wrote {ind_file}")

        # ── Write individual perforated_floor_<name>.stl ──
        for san, (src_file, orig_name) in perforated_floor_solids.items():
            block = _get_block(san, src_file)
            if block is None:
                continue
            if not _has_facets(block):
                logger.info(f"Skipping empty solid '{orig_name}' from {src_file} (no facets)")
                extracted_from.setdefault(src_file, set()).add(san)
                continue
            ind_file = f"perforated_floor_{san}.stl"
            ind_path = self.tri_surface_dir / ind_file
            with open(ind_path, "w", encoding="utf-8") as f:
                f.write(block + "\n")
            self._stl_solids[ind_file] = [orig_name]
            extracted_from.setdefault(src_file, set()).add(san)
            logger.info(f"Wrote {ind_file}")

        # ── Remove extracted solids from source files ──
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
                self._stl_solids.pop(filename, None)
                fpath.unlink()
            logger.info(f"Updated {filename}: {len(keep_names)} solids remaining")

        # Legacy through-zone processing — discards or keeps merged_leakage.stl.
        # Under the new architecture (2026-04-17), through-zones go to
        # refinementRegions rather than cellZones; no STL splitting needed.
        self._discard_or_keep_legacy_through_zone_stl()

    def _discard_or_keep_legacy_through_zone_stl(self) -> None:
        """Handle legacy merged_leakage.stl from pre-2026-04-17 frontend exports.

        The new frontend emits merged_through_zone_<N>U.stl (per-bucket).
        Old exports still produce merged_leakage.stl (scene-merged). Under
        the new architecture (through-zone = refinementRegions, not cellZone),
        we no longer split merged_leakage.stl into individual leakage_<san>.stl
        files. Instead we:
        - Discard it if no racks have through-zones enabled.
        - Keep it in _stl_solids as-is if through-zones are enabled; the geometry
          block emits it as type triSurfaceMesh and the refinementRegions block
          emits it with mode inside (see _generate_snappy_hex_mesh_dict, the
          leakage_ prefix branch).

        Also cleans up stale individual leakage_<san>.stl files that may still
        exist from a previous run under the old splitting architecture.
        (formerly called _split_leakage_solids)
        """
        data = self._data
        any_through_zone_enabled = any(
            rack.get("leakage", {}).get("enabled", False)
            or rack.get("leakage", {}).get("groups", [])
            for rack in data.get("racks", {}).values()
        )
        lk_key = "merged_leakage.stl"
        if lk_key in self._stl_solids:
            if not any_through_zone_enabled:
                lk_path = self.tri_surface_dir / lk_key
                if lk_path.exists():
                    lk_path.unlink()
                del self._stl_solids[lk_key]
                logger.info(f"Discarded {lk_key} — no racks have through-zones enabled")
            else:
                logger.info(
                    f"Kept {lk_key} (legacy through-zone STL) — "
                    "will be emitted as refinementRegions entry (mode inside)"
                )
        # Clean up stale per-solid leakage files from old splitting runs.
        # Under the new architecture these are no longer needed.
        if self.tri_surface_dir.exists():
            for orphan in sorted(self.tri_surface_dir.glob("leakage_*.stl")):
                orphan.unlink(missing_ok=True)
                self._stl_solids.pop(orphan.name, None)
                logger.info(f"Removed stale individual through-zone file (old cellZone split): {orphan.name}")

    def _resolve_single_solid_baffle_file(
        self,
        zone_name: str,
        patch_kind: str,
        seed_config: Optional[Dict[str, Any]] = None,
    ) -> _PatchSeedSpec:
        # Try multiple naming conventions (post-merge filenames first):
        candidates = [
            f"perforated_floor_{zone_name}.stl",
            f"perforated_baffle_{zone_name}.stl",
            f"baffle_{zone_name}.stl",            # legacy
        ]
        for stl_name in candidates:
            stl_path = self.tri_surface_dir / stl_name
            if stl_path.exists():
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
        # Check multi-solid merged files (merged_wall_baffle.stl, merged_rack_door.stl)
        for merge_file in ("merged_wall_baffle.stl", "merged_rack_door.stl"):
            merge_solids = self._stl_solids.get(merge_file, [])
            san_zone = _sanitize(zone_name)
            for solid in merge_solids:
                if _sanitize(solid) == san_zone:
                    stl_path = self.tri_surface_dir / merge_file
                    if stl_path.exists():
                        return _PatchSeedSpec(
                            zone_name=zone_name,
                            solid_name=solid,
                            stl_path=stl_path,
                            patch_kind=patch_kind,
                            seed_config=seed_config,
                        )
        raise RuntimeError(
            f"Expected {patch_kind} patch '{zone_name}' to exist as one of "
            f"{[c for c in candidates]}, but none found. Re-export the case geometry before meshing."
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
                if not (filename.startswith("baffle_")
                        or filename.startswith("perforated_floor_")
                        or filename == "merged_wall_baffle.stl"):
                    continue
                for solid in solids:
                    san = _sanitize(solid)
                    if san.startswith(san_floor_name) and san not in opening_names:
                        add_zone(
                            solid,
                            f"floor_surface_{default_bc}",
                            default_seed_map.get(solid) or default_seed_map.get(san) or default_seed_cfg,
                        )

        # 2026-04-17 redesign (Task 10): rack thin-baffle solids — perforated
        # doors AND single-face blanking panels (wallBaffle — the remaining
        # side of a half-deleted U-slot) — moved from per-solid
        # `baffle_<san>.stl` to bucket-merged `merged_door_<N>U.stl` /
        # `merged_blanking_<N>U.stl`. The legacy baffle_ filename resolver
        # above no longer matches them, so they stopped producing any
        # createBaffles seed spec. That in turn stopped
        # _build_patch_driven_none_seed_entries from placing the rack-cavity
        # seed pair that snappyHexMesh needs to recognise each rack interior
        # as fluid — cavity gets merged into the surrounding solid body,
        # every inner/cavity/fan-server/wallBaffle-neighbour face drops.
        #
        # Fix: scan the new merged_* bucket files directly and bind each
        # thin-baffle solid to its own spec.
        wall_baffle_solids_per_rack: Dict[str, str] = {}
        for rack_name, rack in data.get("racks", {}).items():
            for wb in rack.get("wallBaffleSolids", []):
                wall_baffle_solids_per_rack[_sanitize(wb)] = rack_name

        # Post-merge: merged_wall_baffle.stl / merged_rack_door.stl contain
        # multiple solids. Each solid needs its own seed spec.
        for merge_file, patch_kind in [
            ("merged_wall_baffle.stl", "rack_wallBaffle"),
            ("merged_rack_door.stl", "rack_door"),
        ]:
            if merge_file not in self._stl_solids:
                continue
            stl_path = self.tri_surface_dir / merge_file
            if not stl_path.exists():
                continue
            for solid in self._stl_solids[merge_file]:
                san = _sanitize(solid)
                if san in added:
                    continue
                specs.append(_PatchSeedSpec(
                    zone_name=san,
                    solid_name=solid,
                    stl_path=stl_path,
                    patch_kind=patch_kind,
                    seed_config=None,
                ))
                added.add(san)

        # Legacy bucket files (merged_door_<N>U.stl, merged_wallBaffle_<N>U.stl, etc.)
        for filename, file_solids in self._stl_solids.items():
            bucket = _parse_bucket_from_filename(filename)
            if not bucket:
                continue
            cat = bucket[0]
            if cat == "door":
                patch_kind = "rack_door"
                eligible_solids = {_sanitize(s): s for s in file_solids}
            elif cat == "wallBaffle":
                patch_kind = "rack_wallBaffle"
                eligible_solids = {_sanitize(s): s for s in file_solids}
            elif cat == "blanking":
                patch_kind = "rack_wallBaffle"
                eligible_solids = {
                    _sanitize(s): s for s in file_solids
                    if _sanitize(s) in wall_baffle_solids_per_rack
                }
            else:
                continue
            if not eligible_solids:
                continue
            stl_path = self.tri_surface_dir / filename
            if not stl_path.exists():
                continue
            for san, solid in eligible_solids.items():
                if san in added:
                    continue
                specs.append(_PatchSeedSpec(
                    zone_name=san,
                    solid_name=solid,
                    stl_path=stl_path,
                    patch_kind=patch_kind,
                    seed_config=None,
                ))
                added.add(san)

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
            if not (filename.startswith("baffle_")
                    or filename.startswith("perforated_baffle_")):
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

        # Defensive regression guard: a rack u-slot partition must never end up
        # in the createBaffles seed-spec list. Partitions are single-face walls
        # (see design doc + _split_internal_face_solids comment). A future edit
        # that accidentally routes them here would hit RuntimeError placement
        # failures downstream (partition pitch < seed offset); surface the
        # architectural violation here instead.
        for spec in specs:
            assert not re.search(r"_u\d+_partition$", spec.zone_name), (
                f"Rack u-slot partition '{spec.zone_name}' must not enter "
                f"createBaffles seed specs — partitions are single-face walls "
                f"without dual-side seeds. Check _split_internal_face_solids "
                f"and the bucket-category dispatch above."
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
        base_size = float(mc.get("baseSize", 0.34) or 0.34)
        if base_size <= 0:
            raise RuntimeError(f"meshConfig.baseSize must be positive, got {base_size}")
        rl_baffle = mc.get("baffleRefinementLevel", [1, 1])
        if not isinstance(rl_baffle, list):
            rl_baffle = [rl_baffle, rl_baffle]
        max_level = max(int(level) for level in rl_baffle) if rl_baffle else 0
        h_local = base_size / float(2 ** max_level)
        default_offset = 0.5 * h_local

        triangle_cache = self._build_stl_triangle_cache()
        scene_triangles = _flatten_scene_triangles(triangle_cache)

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

            ignored_surfaces = {(spec.stl_path.name, spec.solid_name)}
            if spec.stl_path.name in ("merged_wall_baffle.stl", "merged_rack_door.stl"):
                for sibling in self._stl_solids.get(spec.stl_path.name, []):
                    ignored_surfaces.add((spec.stl_path.name, sibling))

            clearance_pos = _nearest_ray_clearance(
                anchor,
                direction,
                scene_triangles,
                ignored_surfaces,
            )
            clearance_neg = _nearest_ray_clearance(
                anchor,
                _vec_scale(direction, -1.0),
                scene_triangles,
                ignored_surfaces,
            )

            logger.info(
                "Dual-side seed patch="
                f"{spec.zone_name} kind={spec.patch_kind} center={_format_vec(center)} "
                f"anchor={_format_vec(anchor)} "
                f"direction={_format_vec(direction)} "
                f"normal={_format_vec(normal)} refinementLevel={max_level} "
                f"h_local={h_local:.6f} offset={offset:.6f}"
            )

            if clearance_pos is None or clearance_neg is None:
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
            min_clearance = min(clearance_pos, clearance_neg)
            if min_clearance < offset:
                clamped = min_clearance * 0.8
                logger.warning(
                    f"Seed offset clamped for '{spec.zone_name}' ({spec.patch_kind}): "
                    f"{offset:.6f} → {clamped:.6f} (clearance {min_clearance:.6f} < offset)"
                )
                offset = clamped

            seed_entries.append((_vec_add(anchor, _vec_scale(direction, offset)), "none"))
            seed_entries.append((_vec_add(anchor, _vec_scale(direction, -offset)), "none"))

        return seed_entries

    # _build_leakage_cellzone_seed_entries — REMOVED 2026-04-17.
    # Through-zones (formerly called "leakage zones") are now modeled as
    # refinementRegions (mode inside), NOT as cellZones. No locationsInMesh
    # seed is needed — snappy's refinementRegions mode inside does not
    # require a seed point. See _build_locations_in_mesh_entries (above).
    # (formerly called _build_leakage_cellzone_seed_entries)

    def _build_partition_hole_cellzone_seed_entries(self) -> List[Tuple[Vec3, str]]:
        """Seed enabled partition-hole cellZones from helper STL centroids."""
        entries: List[Tuple[Vec3, str]] = []
        for part in self._data.get("partitionWalls", {}).values():
            for hole in part.get("holes", []):
                phys = hole.get("physics", {}) or {}
                # 2026-04-16: only leakageRate gates the source term.
                try:
                    rate = float(phys.get("leakageRate", 100.0))
                except (TypeError, ValueError):
                    continue
                if rate >= 100.0:
                    continue

                zone_name = hole.get("zoneName", "")
                zone_file = hole.get("zoneStlFile")
                if not zone_name or not zone_file:
                    continue

                zone_path = self.tri_surface_dir / zone_file
                if not zone_path.exists():
                    continue

                surface_name = _sanitize(Path(zone_file).stem)
                centroid = _compute_stl_solid_centroid(zone_path, surface_name)
                if centroid is None:
                    continue

                entries.append((centroid, surface_name))
                logger.info(
                    f"Partition hole seed: {surface_name} at {_format_vec(centroid)} "
                    "(cellZone seed, not a main fluid-domain seed)"
                )
        return entries

    def _preflight_mass_balance(self) -> None:
        """Reject cfdproj configurations whose ACU supply/return totals differ.

        Incompressible SIMPLE solvers need ∮ U·dA = 0 over the fluid domain.
        Each fan-server contributes zero (inlet and outlet carry identical
        volumeFlowRate), so the global balance reduces to:
            Σ(acu.supply.volumeFlowRate) == Σ(acu.return.volumeFlowRate)
        When these differ, the pressure Poisson equation has no solution and
        the solver NaNs at iteration 1 — cheap to catch here, expensive to
        diagnose from solver logs.
        """
        acus = self._data.get("acus") or {}
        if not acus:
            return

        def _sum_face_flow(face_list) -> float:
            total = 0.0
            for f in face_list or []:
                try:
                    total += float(
                        (f.get("physics") or {}).get("volumeFlowRate", 0) or 0
                    )
                except (TypeError, ValueError):
                    pass
            return total

        total_supply = 0.0
        total_return = 0.0
        for acu in acus.values():
            supply_faces = acu.get("supplyFaces") or []
            return_faces = acu.get("returnFaces") or []
            total_supply += _sum_face_flow(supply_faces)
            total_return += _sum_face_flow(return_faces)
            if not supply_faces and acu.get("supplyFace"):
                total_supply += _sum_face_flow([acu["supplyFace"]])
            if not return_faces and acu.get("returnFace"):
                total_return += _sum_face_flow([acu["returnFace"]])

        mean = 0.5 * (total_supply + total_return)
        if mean <= 1e-9:
            return

        drift = abs(total_supply - total_return) / mean
        if drift > 0.01:
            raise RuntimeError(
                f"ACU mass imbalance: Σsupply={total_supply:.4f} m³/s, "
                f"Σreturn={total_return:.4f} m³/s, drift={drift*100:.2f}% "
                f"(tolerance 1%). An incompressible solver with mismatched "
                f"∮U·dA has no pressure solution and diverges at iteration 1. "
                f"Fix the cfdproj: either equalise total ACU supply and "
                f"return flow rates, or add a makeup/exhaust face to close "
                f"the mass balance."
            )

    def _preflight_partition_wall_thickness(self) -> None:
        """Warn (but do NOT auto-bump) when partitionWallRefinementLevel is
        too low for thin partition walls.

        The frontend auto-detect button (``_autoDetectPartitionWall``) now
        owns the safe-level calculation and sets the meshConfig value.  The
        runner respects whatever the user chose — control is fully on the
        frontend side.
        """
        walls = self._data.get("partitionWalls") or {}
        if not walls:
            return
        mc = self._data.get("meshConfig") or self._data.setdefault("meshConfig", {})
        base_size = float(mc.get("baseSize", 0.34) or 0.34)
        if base_size <= 0:
            raise RuntimeError(
                f"meshConfig.baseSize must be positive, got {base_size}"
            )
        rl = mc.get("partitionWallRefinementLevel",
                    mc.get("refinementLevel", [1, 1]))
        if not isinstance(rl, (list, tuple)):
            rl = [rl, rl]
        current_max = max(int(level) for level in rl) if rl else 0

        for part_name, part in walls.items():
            size = (part.get("geometry", {}) or {}).get("size", {}) or {}
            try:
                d = float(size.get("D", 0.1))
            except (TypeError, ValueError):
                continue
            if d <= 0:
                continue
            h_local = base_size / float(2 ** current_max)
            if d < 2.0 * h_local - 1e-9:
                min_level = current_max
                while base_size / float(2 ** min_level) > d / 2.0:
                    min_level += 1
                logger.warning(
                    f"Partition wall '{part_name}' D={d:.4f} m is thinner "
                    f"than 2·h_local={2*h_local:.4f} m at level {current_max}. "
                    f"Recommended level ≥ {min_level}. Use the frontend "
                    f"auto-detect button to set a safe level."
                )

    def _build_partition_wall_seed_entries(self) -> List[Tuple[Vec3, str]]:
        entries: List[Tuple[Vec3, str]] = []
        if not self._data.get("partitionWalls"):
            return entries

        triangle_cache = self._build_stl_triangle_cache()
        scene_triangles = _flatten_scene_triangles(triangle_cache)

        for part_name, part in self._data.get("partitionWalls", {}).items():
            geom = part.get("geometry", {})
            anchor = _dict_to_vec3(geom.get("anchor"), (0.0, 0.0, 0.0))
            size = geom.get("size", {})
            quat = geom.get(
                "transformQuaternion",
                {"x": 0.0, "y": 0.0, "z": 0.0, "w": 1.0},
            )

            w = float(size.get("W", 1.0))
            d = float(size.get("D", 0.1))
            h = float(size.get("H", 3.0))
            if d < 0.0:
                raise RuntimeError(
                    f"Cannot place partition wall seeds for '{part_name}': "
                    f"geometry.size.D must be non-negative, got {d}"
                )

            wall_solid_names = part.get("wallSolidNames", [])
            ignored_surfaces, ignored_solid_names = _resolve_partition_wall_body_surfaces(
                triangle_cache,
                part_name,
                wall_solid_names,
            )
            ignored_solid_label = ",".join(ignored_solid_names)

            # Y-up convention (matches frontend BoxGeometry(W, H, D) +
            # translate(W/2, H/2, D/2) at STL_generate.html:6958-6960):
            #   W → local-X, H → local-Y (vertical), D → local-Z (thin axis).
            local_center = (w * 0.5, h * 0.5, d * 0.5)
            direction = _vec_normalize(_quat_rotate((0.0, 0.0, 1.0), quat))
            center = _vec_add(anchor, _quat_rotate(local_center, quat))
            offset = d * 0.5 + 0.002

            clearance_pos = _nearest_ray_clearance(
                center,
                direction,
                scene_triangles,
                ignored_surfaces,
            )
            clearance_neg = _nearest_ray_clearance(
                center,
                _vec_scale(direction, -1.0),
                scene_triangles,
                ignored_surfaces,
            )
            failed = (
                clearance_pos is None
                or clearance_neg is None
                or clearance_pos < offset
                or clearance_neg < offset
            )
            if failed:
                raise RuntimeError(
                    "Cannot place dual-side seed for partition wall "
                    f"'{part_name}'. "
                    f"wall_solid={ignored_solid_label or 'unknown'} "
                    f"anchor={_format_vec(anchor)} "
                    f"center={_format_vec(center)} "
                    f"direction={_format_vec(direction)} "
                    f"quat=({float(quat.get('x', 0.0)):.6f}, {float(quat.get('y', 0.0)):.6f}, "
                    f"{float(quat.get('z', 0.0)):.6f}, {float(quat.get('w', 1.0)):.6f}) "
                    f"size=({w:.6f}, {d:.6f}, {h:.6f}) "
                    f"offset={offset:.6f} "
                    f"clearance_pos={'none' if clearance_pos is None else f'{clearance_pos:.6f}'} "
                    f"clearance_neg={'none' if clearance_neg is None else f'{clearance_neg:.6f}'}. "
                    "Adjust the partition wall placement/orientation or nearby geometry so both "
                    "seed points have at least thickness/2 + 0.002 m of clear space."
                )

            entries.append((_vec_add(center, _vec_scale(direction, offset)), "none"))
            entries.append((_vec_add(center, _vec_scale(direction, -offset)), "none"))
        return entries

    def _build_locations_in_mesh_entries(self) -> List[Tuple[Vec3, str]]:
        room = self._data.get("room", {})
        room_height = float(room.get("height", 3.0) or 3.0)
        # Y-up: y is vertical (1.5 m mid-height), z is floor-plan depth.
        loc = room.get("locationInMesh", {"x": 5.0, "y": 1.5, "z": 5.0})
        loc_y = float(loc["y"])
        if loc_y >= room_height:
            clamped_y = room_height * 0.5
            logger.warning(
                f"Room locationInMesh Y={loc_y} ≥ room height {room_height}; "
                f"clamped to {clamped_y}"
            )
            loc_y = clamped_y
        entries: List[Tuple[Vec3, str]] = [
            ((float(loc["x"]), loc_y, float(loc["z"])), "none")
        ]

        # User-specified extra seed points (all none mode)
        for eloc in room.get("extraLocationsInMesh", []):
            entries.append(((float(eloc["x"]), float(eloc["y"]), float(eloc["z"])), "none"))

        entries.extend(self._build_patch_driven_none_seed_entries())
        # 2026-04-17: through-zone seed entries removed — through-zones are now
        # refinementRegions (mode inside), not cellZones. No locationsInMesh seed needed.
        entries.extend(self._build_partition_hole_cellzone_seed_entries())
        entries.extend(self._build_partition_wall_seed_entries())
        return _dedupe_seed_entries(entries)

    def _ensure_config(self) -> None:
        """Backward-compatible no-op wrapper for the old compatibility layer."""
        self._prepare_runtime_metadata()

    # ------------------------------------------------------------------
    # Bridge BC registry
    # ------------------------------------------------------------------

    def _get_rack_width(self, rack_data: dict) -> float:
        """Get rack width in meters from rack data."""
        # Try geometry.size.W (.cfdrun from STLGenerator)
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

    def _collect_liquid_cooled_server_ids(self) -> set:
        """Return the set of server IDs that have liquid cooling enabled.

        A server is liquid-cooled if its `liquidCooling.enabled` flag is true
        in the .cfdrun manifest. Used by the liquid-results completeness check.
        """
        liquid_ids: set = set()
        data = self._data
        for rack in data.get("racks", {}).values():
            for srv in rack.get("servers", []):
                lc = srv.get("liquidCooling")
                if lc and lc.get("enabled", False):
                    sid = srv.get("id")
                    if sid:
                        liquid_ids.add(sid)
        return liquid_ids

    def _build_bridge_bc(self) -> None:
        """Build the in-memory BridgeBCRegistry from .cfdrun physics data.

        Populates self._bridge_registry with a {sanitized_solid_name: BCSpec}
        mapping used by FieldGenerator (for 0/ field generation) and by
        _ensure_wall_baffles_are_wall_type()
        (for mesh-side fixes). Does NOT write any file — the registry is
        consumed within the same Python process.
        """
        from dctwin.runner.bridge_bc import BridgeBCRegistry, BCSpec, PerforatedSpec

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
            return_patch = None
            rf_list = acu.get("returnFaces", [])
            if rf_list:
                return_patch = _sanitize(rf_list[0]["solidName"])

            for sf in acu.get("supplyFaces", []):
                solid = _sanitize(sf["solidName"])
                phys = sf.get("physics", {})
                cooling_kw = phys.get("coolingCapacity")
                coolant_c = phys.get("coolantTemperature")
                min_sup = phys.get("minSupplyTemperature")
                if min_sup is None and coolant_c is not None:
                    min_sup = coolant_c + 6.3
                cooling_model = phys.get("coolingModel", {})
                cm_mode = cooling_model.get("mode")
                rated_return = cooling_model.get("ratedReturnTemp")
                perf_table = cooling_model.get("performanceTable")
                registry.register(solid, BCSpec(
                    bc_type="acu_supply",
                    temperature=phys.get("temperature", 15.0),
                    flow_rate=phys.get("volumeFlowRate", 4.0),
                    cooling_capacity=cooling_kw * 1000 if cooling_kw else None,
                    set_point_temp=phys.get("setPointTemperature"),
                    coolant_temp=coolant_c,
                    min_supply_temp=min_sup,
                    return_patch_name=return_patch,
                    cooling_model_mode=cm_mode,
                    rated_return_temp=rated_return,
                    cooling_performance_table=perf_table,
                    timeVarying=phys.get("timeVarying", {}),
                ))
            for rf in rf_list:
                solid = _sanitize(rf["solidName"])
                phys = rf.get("physics", {})
                registry.register(solid, BCSpec(
                    bc_type="acu_return",
                    flow_rate=phys.get("volumeFlowRate", 4.0),
                    timeVarying=phys.get("timeVarying", {}),
                ))

        # --- Load liquid cooling results (hard-fail gate) ---
        liquid_results_path = self.model_dir / "liquid_results.json"
        inherit_liquid = self._data.get("inheritLiquidResults", False)
        liquid_server_results: Dict[str, Any] = {}
        if inherit_liquid:
            if not liquid_results_path.exists():
                raise RuntimeError(
                    f"inheritLiquidResults=true but {liquid_results_path} does not exist. "
                    "Run POST /api/liquid/solve before running the CFD pipeline, "
                    "or uncheck 'Inherit Liquid Cooling Results' in the solver panel."
                )

            from dctwin.liquid.results import LiquidResult
            try:
                lr = LiquidResult.load(liquid_results_path)
            except (json.JSONDecodeError, OSError, ValueError, KeyError, TypeError) as e:
                raise RuntimeError(
                    f"Failed to read {liquid_results_path}: {e}. "
                    "The liquid results file may be corrupted — re-run POST /api/liquid/solve."
                ) from e

            liquid_server_results = {sid: srv for sid, srv in lr.servers.items()}

            expected_server_ids = self._collect_liquid_cooled_server_ids()
            got_server_ids = set(liquid_server_results.keys())
            missing = expected_server_ids - got_server_ids
            if missing:
                raise RuntimeError(
                    f"liquid_results.json is incomplete — missing servers: {sorted(missing)}. "
                    "Re-run POST /api/liquid/solve to regenerate."
                )

            logger.info(
                f"Inheriting liquid cooling results for {len(liquid_server_results)} servers"
            )

        # Rack servers (fanServer and nonFan)
        for rack_name, rack in data.get("racks", {}).items():
            for srv in rack.get("servers", []):
                phys = srv.get("physics", {})
                Q = phys.get("volumeFlowRate", 0.0)
                P = phys.get("power", 0.0)
                bc_mode = phys.get("serverBCMode", "powerAndFlow")
                if bc_mode == "tempRiseAndFlow":
                    dt_user = phys.get("temperatureRise") or 0.0
                    P = rho_air * air_specific_heat * Q * dt_user
                elif bc_mode == "powerAndTempRise":
                    dt_user = phys.get("temperatureRise") or 0.0
                    Q = P / (rho_air * air_specific_heat * dt_user) if dt_user > 0 else 0.0
                srv_type = srv.get("type", "fanServer")

                # If liquid cooling results exist, use residual heat instead of full power
                server_id = srv.get("id", "")
                if server_id in liquid_server_results:
                    lc_srv = liquid_server_results[server_id]
                    P = lc_srv.residual_heat_to_air_W
                    logger.debug(f"  {server_id}: using liquid-cooled residual power {P:.1f}W "
                                 f"(eta={lc_srv.liquid_heat_capture_ratio:.2f})")

                faces = srv.get("faces")
                if faces:
                    # ---------------------------------------------------
                    # New format: iterate all faces from the faces dict
                    # ---------------------------------------------------
                    for face_id in ("inlet", "outlet", "top", "bottom", "left", "right"):
                        face_info = faces.get(face_id)
                        if not face_info:
                            continue

                        # Determine primary BC for this face
                        if srv_type == "fanServer":
                            _rack_geom = rack.get("geometry", {})
                            _rack_W = _rack_geom.get("size", {}).get("W", 0.6)
                            _slot_ratio = _rack_geom.get("slotWidthRatio", 0.8)
                            _unit_h = _rack_geom.get("unitHeight", 0.04445)
                            _span = srv.get("spanSlots", 1)
                            _phys_area = _rack_W * _slot_ratio * _span * _unit_h
                            srv_tv = phys.get("timeVarying")
                            if face_id == "inlet":
                                bc = BCSpec(bc_type="fan_inlet", flow_rate=Q, power=P,
                                            physical_flow_area=_phys_area,
                                            timeVarying=srv_tv)
                            elif face_id == "outlet":
                                T_outlet = 25.0 + P / (rho_air * air_specific_heat * Q) if Q > 0 else 35.0
                                bc = BCSpec(bc_type="fan_outlet", flow_rate=Q, power=P,
                                            temperature=T_outlet, physical_flow_area=_phys_area,
                                            timeVarying=srv_tv)
                            else:
                                bc = BCSpec(bc_type="wall")
                        else:
                            # nonFan per-face BC
                            if face_id in ("inlet", "outlet"):
                                face_phys = phys.get(face_id, {})
                                bc_type_val = face_phys.get("bcType", "wall")
                                if bc_type_val == "nonfan_fixed_temp":
                                    T_val = face_phys.get("temperature", 35.0) or 35.0
                                    bc = BCSpec(bc_type="nonfan_fixed_temp", temperature=T_val)
                                elif bc_type_val == "nonfan_heat_flux":
                                    power_val = face_phys.get("power", 500) or 500
                                    span = srv.get("spanSlots", 1)
                                    eq_height = span * 0.04445
                                    rack_width = self._get_rack_width(rack)
                                    area = eq_height * rack_width
                                    hf = power_val / max(area, 1e-6)
                                    bc = BCSpec(bc_type="nonfan_heat_flux", heat_flux=hf)
                                else:
                                    bc = BCSpec(bc_type="wall")
                            else:
                                bc = BCSpec(bc_type="wall")

                        # Register based on face subdivision.
                        # For fan_inlet/fan_outlet with N>1 patches, split the
                        # server's total volumetric flow + power evenly per-patch
                        # so each patch's flowRateInletVelocity = Q_total / N.
                        # Power per-patch = P_total / N — preserves ΔT since
                        # ΔT = (P/N) / (Q/N · ρ · Cp) = P / (Q · ρ · Cp).
                        patches = face_info.get("patches", [])
                        if patches:
                            n_patches = len(patches)
                            is_flow_bc = bc.bc_type in ("fan_inlet", "fan_outlet")
                            for p in patches:
                                solid = p.get("solid")
                                if not solid:
                                    continue
                                if is_flow_bc and n_patches > 1:
                                    per_q = (bc.flow_rate or 0.0) / n_patches
                                    per_p = (bc.power or 0.0) / n_patches
                                    patch_bc = BCSpec(
                                        bc_type=bc.bc_type,
                                        flow_rate=per_q,
                                        power=per_p,
                                        temperature=bc.temperature,
                                        physical_flow_area=bc.physical_flow_area,
                                    )
                                    registry.register(_sanitize(solid), patch_bc)
                                else:
                                    registry.register(_sanitize(solid), bc)
                            if is_flow_bc and n_patches > 1:
                                logger.info(
                                    f"  {server_id}.{face_id}: split flow across {n_patches} patches "
                                    f"(Q/patch={(bc.flow_rate or 0)/n_patches:.4f} m³/s)"
                                )
                            wall_solid = face_info.get("wallSolid")
                            if wall_solid:
                                registry.register(_sanitize(wall_solid), BCSpec(bc_type="wall"))
                        else:
                            solid = face_info.get("solid")
                            if solid:
                                registry.register(_sanitize(solid), bc)

                    logger.info(f"  {server_id}: registered via faces dict")
                else:
                    # ---------------------------------------------------
                    # Legacy fallback — inletSolidName / outletSolidName
                    # ---------------------------------------------------
                    if srv_type == "fanServer":
                        inlet = _sanitize(srv["inletSolidName"]) if srv.get("inletSolidName") else None
                        outlet = _sanitize(srv["outletSolidName"]) if srv.get("outletSolidName") else None
                        if not inlet or not outlet:
                            continue
                        T_outlet = 25.0 + P / (rho_air * air_specific_heat * Q) if Q > 0 else 35.0
                        _rack_geom = rack.get("geometry", {})
                        _rack_W = _rack_geom.get("size", {}).get("W", 0.6)
                        _slot_ratio = _rack_geom.get("slotWidthRatio", 0.8)
                        _unit_h = _rack_geom.get("unitHeight", 0.04445)
                        _span = srv.get("spanSlots", 1)
                        _phys_area = _rack_W * _slot_ratio * _span * _unit_h
                        registry.register(inlet, BCSpec(bc_type="fan_inlet", flow_rate=Q, power=P,
                                                        physical_flow_area=_phys_area))
                        registry.register(outlet, BCSpec(bc_type="fan_outlet", flow_rate=Q, power=P,
                                                         temperature=T_outlet,
                                                         physical_flow_area=_phys_area))
                        # If server has outlet patch, register wall for remaining area
                        outlet_wall_name = srv.get("outletWallSolidName")
                        if outlet_wall_name and srv.get("outletPatch"):
                            registry.register(_sanitize(outlet_wall_name), BCSpec(bc_type="wall"))
                            logger.info(f"  {server_id}: outlet patch → wall remainder {outlet_wall_name}")
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

            # RDHx — adjust fan_outlet temperature if rack has rear door heat exchanger
            rdhx_cfg = rack.get("rdhx", {})
            if rdhx_cfg.get("enabled", False):
                try:
                    from dctwin.liquid.devices.rdhx import compute_rdhx_heat_rejection
                    # Compute total rack air flow and power for RDHx
                    total_rack_Q_air = sum(
                        s.get("physics", {}).get("volumeFlowRate", 0.0)
                        for s in rack.get("servers", [])
                        if s.get("type") == "fanServer"
                    )
                    total_rack_power = sum(
                        s.get("physics", {}).get("power", 0.0)
                        for s in rack.get("servers", [])
                    )
                    if total_rack_Q_air > 0 and total_rack_power > 0:
                        T_exhaust = ambient_temp + total_rack_power / (rho_air * air_specific_heat * total_rack_Q_air)
                        rdhx_result = compute_rdhx_heat_rejection(
                            air_flow_rate_m3s=total_rack_Q_air,
                            air_inlet_temp_C=T_exhaust,
                            water_flow_rate_LPM=rdhx_cfg.get("waterFlowRate", 20.0),
                            water_inlet_temp_C=rdhx_cfg.get("waterInletTemp", 25.0),
                            effectiveness=rdhx_cfg.get("effectiveness", 0.5),
                        )
                        # Update all fan_outlet temperatures for this rack's servers
                        for srv in rack.get("servers", []):
                            if srv.get("type") == "fanServer":
                                # Collect outlet solid names from faces dict or legacy field
                                outlet_solids_to_adjust: list = []
                                srv_faces = srv.get("faces")
                                if srv_faces:
                                    outlet_face = srv_faces.get("outlet", {})
                                    patches = outlet_face.get("patches", [])
                                    if patches:
                                        for p in patches:
                                            s = p.get("solid")
                                            if s:
                                                outlet_solids_to_adjust.append(_sanitize(s))
                                    else:
                                        s = outlet_face.get("solid")
                                        if s:
                                            outlet_solids_to_adjust.append(_sanitize(s))
                                else:
                                    s = srv.get("outletSolidName", "")
                                    if s:
                                        outlet_solids_to_adjust.append(_sanitize(s))
                                for outlet_solid in outlet_solids_to_adjust:
                                    bc = registry.get(outlet_solid)
                                    if bc is not None:
                                        bc.temperature = rdhx_result["air_outlet_temp_C"]
                                        logger.debug(f"  RDHx adjusted {outlet_solid} T to {bc.temperature:.1f}°C")
                except Exception as e:
                    logger.warning(f"RDHx processing failed for {rack_name}: {e}")

            # Through-zone solids → registered as through_zone (formerly leakage_zone).
            # 2026-04-17 architecture change: through-zones are pure fluid regions;
            # they no longer get a DarcyForchheimer porous source. The registration
            # is kept so FieldGenerator can identify these solids as ordinary walls
            # (no fan/ACU BC needed). The bc_type="through_zone" key is informational.
            for grp in rack.get("leakage", {}).get("groups", []):
                solid = _sanitize(grp["stlSolidName"])
                registry.register(solid, BCSpec(bc_type="through_zone", sigma=grp.get("sigma", 0.05)))

            # Rack frame: multiple solids (outer shell + margin strips + inner walls
            # + inner ceiling/floor + cavity walls), all wall BC.
            frame_solids = rack.get("frameSolidNames") or []
            # Backward compat for older exports that used singular `frameSolidName`.
            if not frame_solids and rack.get("frameSolidName"):
                frame_solids = [rack["frameSolidName"]]
            for fs in frame_solids:
                if fs:
                    registry.register(_sanitize(fs), BCSpec(bc_type="wall"))
            if frame_solids:
                logger.info(f"  Rack {rack_name}: registered {len(frame_solids)} frame wall solids")

            # Partitions: all wall
            for part in rack.get("partitions", []):
                solid = part.get("solidName")
                if solid:
                    registry.register(_sanitize(solid), BCSpec(bc_type="wall"))

        # Perforated floor openings → porousBafflePressure or cyclic or wall
        for floor_name, floor in data.get("floors", {}).items():
            for op in floor.get("openings", []):
                if op.get("physics", {}).get("type") == "perforated":
                    solid = _sanitize(op["solidName"])
                    perf = op["physics"].get("perforated")
                    # New format wins: pydantic always emits porosity=0.0 even
                    # when the user-authored cfdrun omitted it, so a
                    # `if "porosity" in physics` check would always pick the
                    # legacy zero and silently downgrade to wall_baffle. Prefer
                    # `perforated.openAreaPct` whenever present.
                    if perf and perf.get("openAreaPct") is not None:
                        porosity_pct = float(perf["openAreaPct"])
                    elif "porosity" in op["physics"]:
                        porosity_pct = op["physics"]["porosity"]
                    else:
                        porosity_pct = 50.0  # defensive default
                    porosity = porosity_pct / 100.0
                    if porosity_pct <= 0:
                        # porosity=0 → fully blocked → wall baffle (no-slip both sides)
                        registry.register(f"{solid}_master", BCSpec(bc_type="wall_baffle"))
                        registry.register(f"{solid}_slave", BCSpec(bc_type="wall_baffle"))
                    else:
                        # porosity > 0 → cyclic + porousBafflePressure (D=0 for 100%)
                        perf_spec = PerforatedSpec(**perf) if perf else None
                        registry.register(f"{solid}_master", BCSpec(
                            bc_type="perforated_baffle", sigma=porosity, perforated=perf_spec))
                        registry.register(f"{solid}_slave", BCSpec(
                            bc_type="perforated_baffle", sigma=porosity, perforated=perf_spec))

        # Floor perforated remaining-area solids (2026-04-16 unification).
        # Must run BEFORE the baffle_solids loop below: floor remaining-area
        # solids are extracted into baffle_<name>.stl files by
        # _split_internal_face_solids and would otherwise be picked up by
        # the baffle loop and registered as wall_baffle (since they have
        # no entry in _baffle_physics). Registering perforated_baffle here
        # first lets the baffle loop's "already registered" guard skip them.
        for floor_name, floor in data.get("floors", {}).items():
            if floor.get("defaultBC", "wall") != "perforated":
                continue
            perf_dict = floor.get("defaultBCPerforated")
            legacy_porosity_pct = float(floor.get("defaultBCPorosity") or 0.0) * 100.0
            if not perf_dict and legacy_porosity_pct > 0:
                perf_dict = {"shape": "circle", "holeDiameterMm": 3.0,
                             "openAreaPct": legacy_porosity_pct}
            if not perf_dict:
                perf_dict = {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0}
            perf_spec = PerforatedSpec(**perf_dict)
            sigma = max(0.0, min(1.0, float(perf_spec.openAreaPct) / 100.0))
            san_floor_name = _sanitize(floor_name)
            opening_solid_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
            for bf_name, bf_solids in self._stl_solids.items():
                if not (bf_name.startswith("baffle_")
                        or bf_name.startswith("perforated_floor_")
                        or bf_name == "merged_wall_baffle.stl"):
                    continue
                for solid in bf_solids:
                    s = _sanitize(solid)
                    if not s.startswith(san_floor_name) or s in opening_solid_names:
                        continue
                    if f"{s}_master" in registry:
                        continue
                    if sigma <= 0:
                        registry.register(f"{s}_master", BCSpec(bc_type="wall_baffle"))
                        registry.register(f"{s}_slave", BCSpec(bc_type="wall_baffle"))
                    else:
                        registry.register(f"{s}_master", BCSpec(
                            bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
                        registry.register(f"{s}_slave", BCSpec(
                            bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))

        # Baffles from baffle models → perforated or wall baffle
        # Check merged_baffle.stl, individual baffle_<name>.stl files, and
        # post-merge perforated_baffle_*/perforated_floor_* files.
        baffle_solids = list(self._stl_solids.get("merged_baffle.stl", []))
        for fn, solids in self._stl_solids.items():
            if (fn.startswith("baffle_")
                    or fn.startswith("perforated_baffle_")
                    or fn.startswith("perforated_floor_")) and fn != "merged_baffle.stl":
                baffle_solids.extend(solids)
        for solid in baffle_solids:
            san = _sanitize(solid)
            phys = self._baffle_physics.get(san, {"baffleType": "wall", "porosity": 0})
            porosity_pct = phys.get("porosity", 0)
            perf = phys.get("perforated")
            if f"{san}_master" in registry:
                continue  # Already registered by floor opening loop
            # New exporter (Task 8 of perforated-plate plan) emits `perforated`
            # without an accompanying `porosity` scalar. Treat baffleType ==
            # 'perforated' as authoritative; derive sigma from openAreaPct as
            # a fallback so the wall/cyclic/porous classifier in fields.py
            # still chooses the porous branch.
            is_perforated = phys.get("baffleType") == "perforated" or porosity_pct > 0
            if is_perforated:
                if porosity_pct > 0:
                    sigma = porosity_pct / 100.0
                elif perf and perf.get("openAreaPct") is not None:
                    sigma = float(perf["openAreaPct"]) / 100.0
                else:
                    sigma = 0.5  # defensive default — perforated but no params
                perf_spec = PerforatedSpec(**perf) if perf else None
                registry.register(f"{san}_master", BCSpec(
                    bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
                registry.register(f"{san}_slave", BCSpec(
                    bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
            else:
                # Wall baffle (no perforated config, porosity=0) → no-slip both sides
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
                if (bf_name.startswith("baffle_")
                        or bf_name == "merged_wall_baffle.stl"):
                    for solid in bf_solids:
                        s = _sanitize(solid)
                        if s.startswith(san_floor_name) and s not in opening_solid_names:
                            if f"{s}_master" not in registry:
                                registry.register(f"{s}_master", BCSpec(bc_type="wall_baffle"))
                                registry.register(f"{s}_slave", BCSpec(bc_type="wall_baffle"))

        # Perforated doors on rack inlet/outlet faces.
        # 2026-04-16 unification: door now carries a `perforated` block
        # (shape, holeDiameterMm, openAreaPct) identical to baffles/floors,
        # not a fraction-valued `porosity`. We derive sigma from openAreaPct
        # and pass the full PerforatedSpec through. Semantics:
        #   sigma == 0   → wall_baffle (no-slip both sides)
        #   0 < σ < 1    → cyclic U/T + porousBafflePressure on p_rgh
        #   sigma == 1   → pure cyclic for ALL fields
        DEFAULT_DOOR_PERF = {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0}
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door_key = f"{side}Door"
                door = rack.get(door_key)
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                perf_dict = door.get("perforated") or DEFAULT_DOOR_PERF
                perf_spec = PerforatedSpec(**perf_dict)
                sigma = max(0.0, min(1.0, float(perf_spec.openAreaPct) / 100.0))
                if sigma <= 0:
                    registry.register(f"{solid}_master", BCSpec(bc_type="wall_baffle"))
                    registry.register(f"{solid}_slave", BCSpec(bc_type="wall_baffle"))
                    logger.info(f"Registered rack door as wall_baffle (no-slip): {solid}")
                else:
                    registry.register(f"{solid}_master", BCSpec(
                        bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
                    registry.register(f"{solid}_slave", BCSpec(
                        bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
                    if sigma >= 1.0:
                        logger.info(f"Registered rack door as pure cyclic (100% open): {solid}")
                    else:
                        logger.info(f"Registered rack door as perforated (σ={sigma:.2f}): {solid}")

        # Rack doors from merged_door_<N>U.stl bucket files.
        # Register _master/_slave for any door solid not already covered by the
        # enabled-door loop above (e.g. doors where `enabled` is absent/false
        # but the solid was still exported by the frontend).
        DEFAULT_DOOR_PERF_BUCKET = {"shape": "circle", "holeDiameterMm": 3.0, "openAreaPct": 64.0}
        rack_san_lookup = {_sanitize(rn): rd for rn, rd in data.get("racks", {}).items()}
        for filename, file_solids in self._stl_solids.items():
            bkt = _parse_bucket_from_filename(filename)
            if bkt is None or bkt[0] != "door":
                continue
            for solid in file_solids:
                san = _sanitize(solid)
                if f"{san}_master" in registry:
                    continue  # already registered by enabled-door loop above
                # Match solid → rack by name pattern: <rack_san>_<side>_door
                perf_dict = DEFAULT_DOOR_PERF_BUCKET
                for r_san, rack in rack_san_lookup.items():
                    for side in ("inlet", "outlet"):
                        if san == f"{r_san}_{side}_door":
                            door = rack.get(f"{side}Door") or {}
                            perf_dict = door.get("perforated") or DEFAULT_DOOR_PERF_BUCKET
                            break
                try:
                    perf_spec = PerforatedSpec(**perf_dict)
                except Exception:
                    perf_spec = PerforatedSpec(**DEFAULT_DOOR_PERF_BUCKET)
                sigma = max(0.0, min(1.0, float(perf_spec.openAreaPct) / 100.0))
                if sigma <= 0:
                    registry.register(f"{san}_master", BCSpec(bc_type="wall_baffle"))
                    registry.register(f"{san}_slave", BCSpec(bc_type="wall_baffle"))
                else:
                    registry.register(f"{san}_master", BCSpec(
                        bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
                    registry.register(f"{san}_slave", BCSpec(
                        bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
                logger.info(f"Registered merged_door bucket solid {san}_master/_slave "
                            f"(σ={sigma:.2f})")

        # merged_rack_door.stl: register unified merged_rack_door_master/_slave.
        # After baffle merge, all door solids share a single faceZone
        # "merged_rack_door" → createBaffles produces merged_rack_door_master/slave.
        if "merged_rack_door.stl" in self._stl_solids:
            if "merged_rack_door_master" not in registry:
                try:
                    sigma = self._get_uniform_door_sigma()
                except (ValueError, KeyError):
                    sigma = 0.64
                DEFAULT_DOOR_PERF_UNIFIED = {"shape": "circle", "holeDiameterMm": 3.0,
                                             "openAreaPct": sigma * 100.0}
                perf_spec = PerforatedSpec(**DEFAULT_DOOR_PERF_UNIFIED)
                registry.register("merged_rack_door_master", BCSpec(
                    bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
                registry.register("merged_rack_door_slave", BCSpec(
                    bc_type="perforated_baffle", sigma=sigma, perforated=perf_spec))
                logger.info(f"Registered unified merged_rack_door_master/_slave (σ={sigma:.2f})")

        _SMALL_GAP_PERF = PerforatedSpec(shape="circle", holeDiameterMm=1.0,
                                         openAreaPct=_BLANKING_PANEL_SIGMA * 100)

        # merged_wall_baffle.stl: fullBlock wallBaffles only → wall BC
        if "merged_wall_baffle.stl" in self._stl_solids:
            if "merged_wall_baffle_master" not in registry:
                registry.register("merged_wall_baffle_master", BCSpec(bc_type="wall_baffle"))
                registry.register("merged_wall_baffle_slave", BCSpec(bc_type="wall_baffle"))
                logger.info("Registered unified merged_wall_baffle_master/_slave (wall)")

        # wallBaffle and blankingpanel solids from bucket files.

        for filename, file_solids in self._stl_solids.items():
            bkt = _parse_bucket_from_filename(filename)
            if bkt is None:
                continue
            if bkt[0] == "wallBaffle":
                for solid in file_solids:
                    san = _sanitize(solid)
                    if f"{san}_master" not in registry:
                        registry.register(f"{san}_master", BCSpec(bc_type="wall_baffle"))
                        registry.register(f"{san}_slave", BCSpec(bc_type="wall_baffle"))
                        logger.info(f"Registered wallBaffle bucket solid {san}_master/_slave (wall)")
            elif bkt[0] == "perforatedBlankingPanel":
                bp_stem = Path(filename).stem
                if f"{bp_stem}_master" not in registry:
                    registry.register(f"{bp_stem}_master", BCSpec(
                        bc_type="perforated_baffle", sigma=_BLANKING_PANEL_SIGMA,
                        perforated=_SMALL_GAP_PERF))
                    registry.register(f"{bp_stem}_slave", BCSpec(
                        bc_type="perforated_baffle", sigma=_BLANKING_PANEL_SIGMA,
                        perforated=_SMALL_GAP_PERF))
                    logger.info(f"Registered {bp_stem}_master/_slave (σ={_BLANKING_PANEL_SIGMA})")
            elif bkt[0] == "wallBlankingPanel":
                pass

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

        for part_name, part in data.get("partitionWalls", {}).items():
            for solid in part.get("wallSolidNames", []):
                if solid:
                    registry.register(_sanitize(solid), BCSpec(bc_type="wall"))

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
                spec = BCSpec(
                    bc_type="heat_source_flux",
                    heat_flux=q,
                    timeVarying=hs.get("timeVarying", {}),
                    surface_area=area if area > 1e-8 else None,
                )
            else:
                continue
            for solid_name in hs.get("wallSolidNames", []):
                registry.register(_sanitize(solid_name), spec)

        # Store the in-memory registry on self — consumed by:
        #   - _build_field_generator()  → FieldGenerator.bridge_registry
        #   - _ensure_wall_baffles_are_wall_type()
        self._bridge_registry = registry

        # Debug-friendly summary (replaces the old `cat bridge_bc.json` workflow)
        from collections import Counter
        counts = Counter(spec.bc_type for _, spec in registry.items())
        summary = " | ".join(f"{n} {bc}" for bc, n in sorted(counts.items()))
        logger.info(
            f"Built BridgeBCRegistry: {len(registry)} patches | {summary}"
        )

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
        """Generate blockMeshDict with grid-aligned room boundaries and
        optimised cell counts for rack face alignment.
        """
        data = self._data
        mc = data.get("meshConfig", {})
        base_size = mc.get("baseSize", 0.34)

        plane = data.get("room", {}).get("plane", [])
        h = data.get("room", {}).get("height", 3.0)
        if plane:
            xs = [p["x"] for p in plane]
            zs = [p["y"] for p in plane]
            rx_min, rx_max = min(xs), max(xs)
            rz_min, rz_max = min(zs), max(zs)
        else:
            rx_min, rx_max, rz_min, rz_max = 0.0, 10.0, 0.0, 10.0
        ry_min, ry_max = 0.0, h

        rack_configs = []
        for rk in data.get("racks", {}).values():
            geo = rk.get("geometry", {})
            if not geo:
                continue
            rack_configs.append({
                "anchor": geo.get("anchor", {"x": 0, "y": 0, "z": 0}),
                "size": geo.get("size", {"W": 0.6, "D": 1.0, "H": 2.0}),
                "orientation": geo.get("orientation", 0),
                "inletOffset": geo.get("inletOffset", 0),
                "outletOffset": geo.get("outletOffset", 0),
            })

        room_bounds = {"x": (rx_min, rx_max), "y": (ry_min, ry_max), "z": (rz_min, rz_max)}
        mb = compute_multiblock_grid(room_bounds, base_size, rack_configs)

        x_segs = mb["x"]
        y_segs = mb["y"]
        z_segs = mb["z"]

        x_breaks = sorted({s[0] for s in x_segs} | {s[1] for s in x_segs})
        y_breaks = sorted({s[0] for s in y_segs} | {s[1] for s in y_segs})
        z_breaks = sorted({s[0] for s in z_segs} | {s[1] for s in z_segs})

        nx = len(x_breaks) - 1
        ny = len(y_breaks) - 1
        nz = len(z_breaks) - 1

        seg_nx = [seg[2] for seg in x_segs]
        seg_ny = [seg[2] for seg in y_segs]
        seg_nz = [seg[2] for seg in z_segs]

        def vidx(ix, iy, iz):
            return ix + iy * (nx + 1) + iz * (nx + 1) * (ny + 1)

        vertices = []
        for iz in range(nz + 1):
            for iy in range(ny + 1):
                for ix in range(nx + 1):
                    vertices.append(f"    ( {x_breaks[ix]} {y_breaks[iy]} {z_breaks[iz]} )")
        vert_str = "\n".join(vertices)

        blocks = []
        for iz in range(nz):
            for iy in range(ny):
                for ix in range(nx):
                    v = [vidx(ix, iy, iz), vidx(ix+1, iy, iz),
                         vidx(ix+1, iy+1, iz), vidx(ix, iy+1, iz),
                         vidx(ix, iy, iz+1), vidx(ix+1, iy, iz+1),
                         vidx(ix+1, iy+1, iz+1), vidx(ix, iy+1, iz+1)]
                    vstr = " ".join(str(i) for i in v)
                    blocks.append(
                        f"    hex ( {vstr} ) ( {seg_nx[ix]} {seg_ny[iy]} {seg_nz[iz]} ) "
                        f"simpleGrading ( 1 1 1 )")
        block_str = "\n".join(blocks)

        total_cells = sum(seg_nx) * sum(seg_ny) * sum(seg_nz)
        bm_min_x, bm_max_x = x_breaks[0], x_breaks[-1]
        bm_min_y, bm_max_y = y_breaks[0], y_breaks[-1]
        bm_min_z, bm_max_z = z_breaks[0], z_breaks[-1]

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
{vert_str}
);

blocks
(
{block_str}
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
        logger.info(f"Generated multi-block blockMeshDict: "
                    f"({bm_min_x:.2f}…{bm_max_x:.2f}, {bm_min_y:.2f}…{bm_max_y:.2f}, "
                    f"{bm_min_z:.2f}…{bm_max_z:.2f}), "
                    f"blocks={nx}×{ny}×{nz}={nx*ny*nz}, cells≈{total_cells}")

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
        """Generate snappyHexMeshDict with through-zones as refinementRegions."""
        data = self._data
        mc = data.get("meshConfig", {})

        # 2026-04-17 redesign: wallBaffleSolids is a deprecated schema slot.
        # Emit a warning if any rack populates it so the developer catches
        # schema drift early. Rack-internal geometry must go through the
        # 4 enumerated categories (server/blanking/partition/through_zone) —
        # not a bucket-less wallBaffleSolids dump.
        for rack_name, rack in (data.get("racks") or {}).items():
            wbs = rack.get("wallBaffleSolids") or []
            if wbs:
                logger.warning(
                    f"rack '{rack_name}' has non-empty wallBaffleSolids "
                    f"({len(wbs)} entries); dropping — these are not routed "
                    f"to any STL file. See "
                    f"docs/superpowers/specs/2026-04-17-refinement-"
                    f"architecture-redesign-design.md §Risks."
                )

        rl_default = mc.get("refinementLevel", [1, 1])
        rl_rack = mc.get("rackRefinementLevel", [1, 1])
        rl_server = mc.get("serverFaceRefinementLevel", [1, 1])
        rl_acu = mc.get("acuRefinementLevel", [1, 1])
        rl_box = mc.get("boxRefinementLevel", [1, 1])
        rl_partition = mc.get("partitionWallRefinementLevel", rl_box)
        rl_baffle = mc.get("baffleRefinementLevel", [1, 1])
        rl_floor = mc.get("floorRefinementLevel", [1, 1])
        rl_through_zone = mc.get("throughZoneRefinementLevel", rl_rack)  # default same as rack (formerly leakageRefinementLevel)

        # Per-unit server face refinement levels (keyed by spanSlots string)
        per_unit_levels = mc.get("serverFacePerUnitLevel", {})

        # Per-unit blanking plate refinement levels
        per_unit_blanking = mc.get("blankingPlatePerUnitLevel", {})
        rl_blanking_unified = mc.get("blankingPlateRefinementLevel", rl_rack)

        # Per-unit rack through-zone refinement levels (formerly rackLeakagePerUnitLevel)
        per_unit_through_zone = mc.get("rackThroughZonePerUnitLevel", {})
        rl_through_zone_unified = mc.get("rackThroughZoneRefinementLevel", rl_through_zone)

        # Build solid-name lookup: which solids are server faces vs through-zone vs etc.
        server_inlet_solids: set = set()
        server_outlet_solids: set = set()
        nonfan_solids: set = set()
        through_zone_solids: Dict[str, float] = {}  # {solidName: sigma}  (formerly leakage_solids)
        acu_supply_solids: set = set()
        acu_return_solids: set = set()
        perforated_solids: Dict[str, float] = {}  # {solidName: porosity}

        # Build mapping: sanitized solid name → spanSlots for per-unit refinement lookup.
        # Per-unit level applies to ALL faces of a fanServer (inlet/outlet AND
        # top/bottom/left/right + patch wall remainders), not just flow faces.
        # Different U-height servers thus still get distinct refinement, but the
        # setting now governs the full server enclosure, not only the fan faces.
        solid_to_span: Dict[str, int] = {}
        for rack in data.get("racks", {}).values():
            for srv in rack.get("servers", []):
                span = srv.get("spanSlots", 1)
                is_fan = srv.get("type") == "fanServer"
                faces = srv.get("faces")
                if faces:
                    # New format: iterate faces dict
                    for face_id, face_info in faces.items():
                        patches = face_info.get("patches", [])
                        if patches:
                            for p in patches:
                                s = p.get("solid")
                                if s:
                                    san = _sanitize(s)
                                    if face_id in ("inlet", "outlet") and is_fan:
                                        (server_inlet_solids if face_id == "inlet"
                                         else server_outlet_solids).add(san)
                                    else:
                                        nonfan_solids.add(san)
                                    # All fanServer solids inherit per-U refinement.
                                    if is_fan:
                                        solid_to_span[san] = span
                            wall_s = face_info.get("wallSolid")
                            if wall_s:
                                san_w = _sanitize(wall_s)
                                nonfan_solids.add(san_w)
                                if is_fan:
                                    solid_to_span[san_w] = span
                        else:
                            s = face_info.get("solid")
                            if s:
                                san = _sanitize(s)
                                if is_fan:
                                    if face_id in ("inlet", "outlet"):
                                        (server_inlet_solids if face_id == "inlet"
                                         else server_outlet_solids).add(san)
                                    else:
                                        nonfan_solids.add(san)
                                    solid_to_span[san] = span
                                else:
                                    nonfan_solids.add(san)
                else:
                    # Legacy format — inletSolidName / outletSolidName
                    if is_fan:
                        _in = srv.get("inletSolidName")
                        _out = srv.get("outletSolidName")
                        if _in:
                            server_inlet_solids.add(_sanitize(_in))
                            solid_to_span[_sanitize(_in)] = span
                        if _out:
                            server_outlet_solids.add(_sanitize(_out))
                            solid_to_span[_sanitize(_out)] = span
                    else:
                        for k in ("inletSolidName", "outletSolidName", "topSolidName", "bottomSolidName"):
                            if srv.get(k):
                                nonfan_solids.add(_sanitize(srv[k]))
            for grp in rack.get("leakage", {}).get("groups", []):
                through_zone_solids[_sanitize(grp["stlSolidName"])] = grp.get("sigma", 0.05)

        # Rack frame and partition solids: wall BC with rack-level refinement.
        rack_frame_solids: set = set()
        partition_solids: set = set()
        for rack_name, rack in data.get("racks", {}).items():
            # Frame: new multi-solid format (frameSolidNames) + legacy singular fallback.
            frame_list = rack.get("frameSolidNames") or []
            if not frame_list and rack.get("frameSolidName"):
                frame_list = [rack["frameSolidName"]]
            for fs in frame_list:
                if fs:
                    rack_frame_solids.add(_sanitize(fs))
            for part in rack.get("partitions", []):
                solid = part.get("solidName")
                if solid:
                    partition_solids.add(_sanitize(solid))
        partition_wall_solids: set = set()
        for part in data.get("partitionWalls", {}).values():
            for solid in part.get("wallSolidNames", []):
                if solid:
                    partition_wall_solids.add(_sanitize(solid))

        # Rack wall-baffle solids: half-dissolved U-slots (this side wall, opposite void).
        # These become faceType internal → createBaffles cyclic → post-process to wall.
        rack_wall_baffle_solids: set = set()
        for rack in data.get("racks", {}).values():
            for wb_solid in rack.get("wallBaffleSolids", []):
                rack_wall_baffle_solids.add(_sanitize(wb_solid))

        # Rack perforated door solids are exported as standalone
        # baffle_*.stl files. Register them in dedicated containers so
        # the refinementSurfaces / feature-edge branches below can
        # detect each door's filename, reverse-map it to its rack, and
        # emit the bucket-resolved door refinement level via
        # _door_level_for_rack (spec 2026-04-17 §4). The rack_door_solids
        # SET is still read by the secondary per-solid branch near the
        # end of this method (Task 7 handles that branch).
        rack_door_solids: set = set()
        rack_door_filenames: set = set()
        # filename → rack_name so the refinement / feature-edge branches can
        # look up which rack a door belongs to and resolve its bucketed
        # refinement level via _door_level_for_rack. See spec §4.2.
        rack_door_filename_to_rack: Dict[str, str] = {}
        # solid → rack_name sibling map for the secondary per-solid branch
        # (doors appearing inside multi-solid STL files). Spec §4 Task 7.
        rack_door_solid_to_rack: Dict[str, str] = {}
        for rack_name, rack in data.get("racks", {}).items():
            rack_san = _sanitize(rack_name)
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                solid = _sanitize(door.get("solidName", f"{rack_san}_{side}_door"))
                rack_door_solids.add(solid)
                fn = f"baffle_{solid}.stl"
                rack_door_filenames.add(fn)
                rack_door_filename_to_rack[fn] = rack_name
                rack_door_solid_to_rack[solid] = rack_name

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

        # Build mapping: through-zone solid name → spanUnits (formerly solid_to_leakage_span)
        solid_to_through_zone_span: Dict[str, int] = {}
        for rack in data.get("racks", {}).values():
            for grp in rack.get("leakage", {}).get("groups", []):
                san = _sanitize(grp["stlSolidName"])
                solid_to_through_zone_span[san] = grp.get("endUnit", 0) - grp.get("startUnit", 0) + 1

        for acu in data.get("acus", {}).values():
            for sf in acu.get("supplyFaces", []):
                acu_supply_solids.add(_sanitize(sf["solidName"]))
            for rf in acu.get("returnFaces", []):
                acu_return_solids.add(_sanitize(rf["solidName"]))

        # Perforated baffles from baffle models. New format wins: prefer
        # `perforated.openAreaPct` over the legacy `porosity` scalar (pydantic
        # always emits `porosity: 0.0` even for perforated baffles, so a
        # porosity-only check would silently drop them).
        def _phys_sigma(p: dict) -> float:
            perf = p.get("perforated") if isinstance(p, dict) else None
            if perf and perf.get("openAreaPct") is not None:
                return float(perf["openAreaPct"]) / 100.0
            return float(p.get("porosity", 0) or 0) / 100.0

        for baffle_name, baffle in data.get("baffles", {}).items():
            phys = baffle.get("physics", {})
            porosity_pct = phys.get("porosity", 0)
            if phys.get("baffleType") == "perforated" or porosity_pct > 0:
                san_bn = _sanitize(baffle_name)
                for solid in self._stl_solids.get("merged_baffle.stl", []):
                    san = _sanitize(solid)
                    if san.startswith(san_bn + "_") or san == san_bn:
                        sol_phys = self._baffle_physics.get(san, phys)
                        sol_porosity = _phys_sigma(sol_phys)
                        if sol_porosity > 0 and san not in perforated_solids:
                            perforated_solids[san] = sol_porosity
            # Per-patch physics
            for solid_name, pp in baffle.get("patchPhysics", {}).items():
                san = _sanitize(solid_name)
                pp_porosity = _phys_sigma(pp)
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
                    porosity = _phys_sigma(op["physics"])
                    if porosity <= 0:
                        # No usable physical params and porosity scalar is
                        # zero — treat as wall_baffle so the BC stack stays
                        # consistent (skip the porous classification).
                        wall_baffle_solids.add(op_solid)
                    else:
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
            # Also check individual baffle/perforated/merged_wall files (floor solids are extracted there)
            for bf_name, bf_solids in self._stl_solids.items():
                if (bf_name.startswith("baffle_")
                        or bf_name.startswith("perforated_floor_")
                        or bf_name == "merged_wall_baffle.stl"):
                    floor_solids_in_stl.extend(bf_solids)
            if default_bc == "wallBaffle":
                for solid in floor_solids_in_stl:
                    s = _sanitize(solid)
                    if s.startswith(san_floor_name) and s not in opening_solid_names and s not in perforated_solids:
                        wall_baffle_solids.add(s)
            elif default_bc == "perforated":
                # 2026-04-16 unification: prefer the perforated block's
                # openAreaPct over the legacy defaultBCPorosity scalar.
                perf_block = floor.get("defaultBCPerforated")
                if perf_block and perf_block.get("openAreaPct") is not None:
                    default_porosity = float(perf_block["openAreaPct"]) / 100.0
                else:
                    default_porosity = floor.get(
                        "defaultBCPorosity", floor.get("defaultBC_porosity", 0.5))
                for solid in floor_solids_in_stl:
                    s = _sanitize(solid)
                    if s.startswith(san_floor_name) and s not in opening_solid_names:
                        perforated_solids[s] = default_porosity

        # --- Geometry section ---
        geom_lines = []
        for filename, solids in sorted(self._stl_solids.items()):
            stem = Path(filename).stem   # e.g. merged_rack
            if (filename in ("merged_perforated.stl",
                             "merged_baffle.stl",
                             "merged_wall_baffle.stl",
                             "merged_rack_door.stl")
                    or filename.startswith("baffle_")
                    or filename.startswith("perforated_baffle_")
                    or filename.startswith("perforated_floor_")):
                # These surfaces use faceType internal or baffle → no named regions in geometry.
                # Named regions force snappyHexMesh to create wall boundary patches
                # instead of internal faces, defeating faceType internal/baffle.
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
    }}""")
                continue
            elif filename.startswith("partition_hole_"):
                # Helper partition-hole surfaces behave like through-zone files: keep
                # them as standalone surfaces so snappyHexMesh can assign a cellZone.
                geom_lines.append(f"""    {filename}
    {{
        type triSurfaceMesh;
        name {stem};
    }}""")
                continue
            elif (filename == "merged_leakage.stl"
                  or filename.startswith("leakage_")
                  or re.match(r"merged_leakage_\d+U\.stl$", filename)):
                # Legacy through-zone files (pre-2026-04-17 naming):
                # "merged_leakage.stl" = old scene-merged export.
                # "leakage_<san>.stl" = old per-solid split files.
                # "merged_leakage_<N>U.stl" = old per-bucket name (renamed to
                #   merged_through_zone_<N>U.stl in 2026-04-17 refactor).
                # No named regions needed — declared as standalone closed surface
                # so snappyHexMesh can use it for refinementRegions mode inside.
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
        feat_partition = _feat_int(mc.get("partitionWallFeatureLevel"), feat_box)
        feat_baffle = _feat_int(mc.get("baffleFeatureLevel"), 2)
        feat_through_zone = _feat_int(mc.get("throughZoneFeatureLevel"), feat_rack)  # formerly feat_leakage
        feature_lines = []
        for filename in sorted(self._stl_solids.keys()):
            stem = Path(filename).stem
            emesh_name = filename.replace(".stl", ".eMesh")

            # 2026-04-17 redesign: per-bucket files route through the
            # bucket resolver (features per STL file per snappyHexMesh).
            bucket = _parse_bucket_from_filename(filename)
            if bucket is not None:
                cat, key = bucket
                feat_level = _bucket_feature_lookup(cat, key, mc)
            elif filename == "merged_rack_frame.stl":
                feat_level = feat_rack
            elif filename in rack_door_filenames:
                # Legacy individual door files (baffle_<door>.stl).
                rack_name = rack_door_filename_to_rack[filename]
                feat_level = _door_feature_level_for_rack(
                    data["racks"][rack_name], mc)
            elif filename == "merged_wall_baffle.stl":
                feat_level = feat_baffle
            elif filename == "merged_rack_door.stl":
                feat_level = _door_feature_level_for_rack({}, mc)
            elif filename.startswith("perforated_baffle_"):
                feat_level = feat_baffle
            elif filename.startswith("perforated_floor_"):
                feat_level = _feat_int(mc.get("floorFeatureLevel"), feat_default)
            elif filename == "merged_acu.stl":
                feat_level = feat_acu
            elif filename == "merged_box.stl":
                feat_level = feat_box
            elif filename == "merged_partition_wall.stl":
                feat_level = feat_partition
            elif filename == "merged_baffle.stl":
                feat_level = feat_baffle
            elif filename == "merged_floor_ceiling.stl":
                feat_level = _feat_int(mc.get("floorFeatureLevel"), feat_default)
            elif filename == "room_wall.stl":
                feat_level = feat_default
            # Legacy fallbacks (transitional; old-style individual files
            # may still exist in partial rollouts until the frontend ships
            # Task 12's buildGroupedStlMap refactor):
            elif filename.startswith("partition_hole_"):
                feat_level = feat_partition
            elif (filename == "merged_leakage.stl"
                  or filename.startswith("leakage_")
                  or re.match(r"merged_leakage_\d+U\.stl$", filename)):
                feat_level = feat_through_zone  # legacy through-zone files (formerly feat_leakage)
            elif filename == "merged_perforated.stl":
                feat_level = feat_baffle
            elif filename.startswith("baffle_"):
                feat_level = feat_baffle
            else:
                feat_level = feat_default

            feature_lines.append(f"""    {{
        file "{emesh_name}";
        level {feat_level};
    }}""")

        # --- Refinement surfaces section ---
        # through-zone files (merged_through_zone_<N>U.stl and legacy leakage_*.stl)
        # are NOT emitted in refinementSurfaces — they go to refinementRegions below.
        # Collect through-zone entries for refinementRegions as we scan the files.
        through_zone_region_lines = []

        refine_lines = []
        for filename, solids in sorted(self._stl_solids.items()):
            stem = Path(filename).stem
            if filename.startswith("partition_hole_"):
                san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_partition[0]} {rl_partition[1]});
        faceType internal;
        cellZone {san};
        cellZoneInside inside;
    }}""")
                continue
            if (filename == "merged_leakage.stl"
                    or filename.startswith("leakage_")
                    or re.match(r"merged_leakage_\d+U\.stl$", filename)):
                # Legacy through-zone file (pre-2026-04-17 naming).
                # "merged_leakage.stl" = old scene-merged through-zone export.
                # "leakage_<san>.stl" = old per-solid split files.
                # "merged_leakage_<N>U.stl" = old per-bucket name (renamed to
                #   merged_through_zone_<N>U.stl in 2026-04-17 refactor).
                # Architecture change (2026-04-17): through-zones are no longer
                # modeled as cellZones + DarcyForchheimer porous source. They are
                # pure fluid — the pressure drop comes from the perforated rack
                # doors (createBaffles). Emit as refinementRegions entry only.
                # For per-bucket legacy files, try to resolve the unit span.
                _legacy_bucket = re.match(r"merged_leakage_(\d+)U\.stl$", filename)
                if _legacy_bucket:
                    _lk_key = _legacy_bucket.group(1)
                    _lvl = _bucket_level_lookup("through_zone", _lk_key, mc)
                    _lvl_val = _lvl[1]
                else:
                    _lvl_val = rl_through_zone_unified[1]
                through_zone_region_lines.append(f"""        {stem}
        {{
            mode inside;
            levels ((1e15 {_lvl_val}));
        }}""")
                continue

            # 2026-04-17 redesign: per-bucket through-zone files go to
            # refinementRegions (mode inside) — NOT to refinementSurfaces.
            # Through-zone is pure fluid; no cellZone + porous source needed.
            # Server/blanking/partition bucket files fall through to the
            # per-region emission below — each solid is classified by the
            # big switch (server_inlet_solids, nonfan_solids, rack_io_solids,
            # …) so fan-server inlets become type=patch, channel walls
            # become type=wall, blanking openings become type=patch, etc.
            bucket = _parse_bucket_from_filename(filename)
            if bucket is not None and bucket[0] == "through_zone":
                cat, key = bucket
                lvl = _bucket_level_lookup(cat, key, mc)
                through_zone_region_lines.append(f"""        {stem}
        {{
            mode inside;
            levels ((1e15 {lvl[1]}));
        }}""")
                continue

            # 2026-04-17 fix: merged_door_<N>U.stl needs surface-level
            # faceType internal so snappy keeps the faces as internal mesh
            # faces (not boundary wall patches). Per-region faceType internal
            # is silently ignored by snappy — it creates wall patches anyway.
            # With surface-level faceType internal, snappy keeps ALL faces of
            # this surface internal, and createBaffles can then move them to
            # a cyclic master/slave pair. Each solid gets its own faceZone
            # (needed by createBaffles for zone-name matching).
            if bucket is not None and bucket[0] == "door":
                cat, key = bucket
                door_region_entries = []
                for solid in solids:
                    san = _sanitize(solid)
                    # Resolve per-rack door level via _door_level_for_rack.
                    # Attempt to match solid → rack (name pattern:
                    # <rack_san>_inlet_door / <rack_san>_outlet_door).
                    rack_lvl = None
                    for rack_name, rack in data.get("racks", {}).items():
                        rack_san = _sanitize(rack_name)
                        if san.startswith(rack_san + "_") and san.endswith("_door"):
                            rack_lvl = _door_level_for_rack(rack, mc)
                            break
                    if rack_lvl is None:
                        rack_lvl = _bucket_level_lookup(cat, key, mc)
                    door_region_entries.append(f"""            {solid}
            {{
                level ({rack_lvl[0]} {rack_lvl[1]});
                faceZone {san};
            }}""")
                door_regions_str = "\n".join(door_region_entries)
                rl_door_outer = _bucket_level_lookup(cat, key, mc)
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_door_outer[0]} {rl_door_outer[1]});
        faceType internal;
        regions
        {{
{door_regions_str}
        }}
    }}""")
                continue

            # 2026-04-17 fix: merged_wallBaffle_<N>U.stl — same surface-level
            # faceType internal treatment as merged_door_<N>U.stl. wallBaffle
            # panels are half-dissolved U-slot blanking faces (double-sided);
            # createBaffles converts them to cyclic master/slave, then
            # _convert_wall_baffle_patches_to_wall() reclassifies to wall type.
            if bucket is not None and bucket[0] in ("wallBaffle", "perforatedBlankingPanel"):
                cat, key = bucket
                _rl_wb = _bucket_level_lookup(cat, key, mc)
                refine_lines.append(f"""    {stem}
    {{
        level ({_rl_wb[0]} {_rl_wb[1]});
        faceType internal;
        faceZone {stem};
    }}""")
                continue

            if bucket is not None and bucket[0] == "wallBlankingPanel":
                cat, key = bucket
                _rl_wb = _bucket_level_lookup(cat, key, mc)
                refine_lines.append(f"""    {stem}
    {{
        level ({_rl_wb[0]} {_rl_wb[1]});
    }}""")
                continue

            # ── merged_wall_baffle.stl: surface-level faceZone + faceType internal ──
            # Per-region faceZone does NOT work in snappyHexMesh — only
            # surface-level faceZone keeps faces internal. Uses ONE merged
            # faceZone; createBaffles creates merged_wall_baffle_master/_slave.
            if filename == "merged_wall_baffle.stl":
                floor_wb_sans = set()
                for floor_name, floor in data.get("floors", {}).items():
                    if floor.get("defaultBC") == "wallBaffle":
                        san_fn = _sanitize(floor_name)
                        op_names = {_sanitize(op["solidName"]) for op in floor.get("openings", [])}
                        for s in solids:
                            san_s = _sanitize(s)
                            if san_s.startswith(san_fn) and san_s not in op_names:
                                floor_wb_sans.add(san_s)
                wb_region_entries = []
                for solid in solids:
                    san = _sanitize(solid)
                    _rl_wb = rl_floor if san in floor_wb_sans else rl_baffle
                    wb_region_entries.append(f"""            {solid}
            {{
                level ({_rl_wb[0]} {_rl_wb[1]});
            }}""")
                wb_regions_str = "\n".join(wb_region_entries)
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone merged_wall_baffle;
        regions
        {{
{wb_regions_str}
        }}
    }}""")
                continue

            # ── merged_rack_door.stl: faceType internal (createBaffles cyclic) ──
            if filename == "merged_rack_door.stl":
                rl_door = _door_level_for_rack({}, mc)
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_door[0]} {rl_door[1]});
        faceType internal;
        faceZone merged_rack_door;
    }}""")
                continue

            # ── perforated_baffle_*.stl / perforated_floor_*.stl ──
            if filename.startswith("perforated_baffle_") or filename.startswith("perforated_floor_"):
                solid_san = _sanitize(solids[0])
                _rl_perf = rl_floor if filename.startswith("perforated_floor_") else rl_baffle
                refine_lines.append(f"""    {stem}
    {{
        level ({_rl_perf[0]} {_rl_perf[1]});
        faceType internal;
        faceZone {solid_san};
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

            if filename in rack_door_filenames:
                # Legacy: individual rack door files (baffle_<door>.stl).
                rack_name = rack_door_filename_to_rack[filename]
                door_lvl = _door_level_for_rack(data["racks"][rack_name], mc)
                solid_san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({door_lvl[0]} {door_lvl[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue

            if filename.startswith("baffle_"):
                solid_san = _sanitize(solids[0])
                refine_lines.append(f"""    {stem}
    {{
        level ({rl_baffle[0]} {rl_baffle[1]});
        faceType internal;
        faceZone {solid_san};
    }}""")
                continue

            # 2026-04-17: Patches on faces (solids named `<parent>_p<N>`)
            # live in the same STL as their parent solid and implicitly
            # inherit the parent's surface level via the file-level `level`
            # fallback. No explicit per-patch regions block is emitted —
            # patches only get their own `regions {}` entry when they need
            # a DIFFERENT level from the parent, which never happens in the
            # current UI (which doesn't expose per-patch refinement controls).
            # See docs/superpowers/specs/2026-04-17-refinement-architecture-
            # redesign-design.md §Patch-on-face refinement inheritance.
            solid_entries = []
            for solid in solids:
                sanitized = _sanitize(solid)
                if sanitized in through_zone_solids:
                    # Through-zone solid in a non-through-zone STL — treat as regular wall
                    # (formerly: leakage solid in non-leakage STL)
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
                    # Top/bottom/left/right of fanServer (and nonFan faces,
                    # and patch-remainder walls) also honor per-U refinement
                    # when the solid belongs to a fanServer. Falls back to
                    # rl_server for nonFan or solids without span info.
                    _span_nf = solid_to_span.get(sanitized)
                    _rl_nf = per_unit_levels.get(str(_span_nf), rl_server) if _span_nf else rl_server
                    if not isinstance(_rl_nf, (list, tuple)) or len(_rl_nf) < 2:
                        _rl_nf = rl_server
                    solid_entries.append(f"""            {solid}
            {{
                level ({_rl_nf[0]} {_rl_nf[1]});
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
                elif sanitized in rack_frame_solids or sanitized in partition_solids:
                    # Rack frame / partition: wall with rack-level refinement
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_rack[0]} {rl_rack[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
                elif sanitized in partition_wall_solids:
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_partition[0]} {rl_partition[1]});
                patchInfo {{ type wall; inGroups (wall); }}
            }}""")
                elif sanitized in wall_baffle_solids:
                    # Standalone wall baffle (floor/baffle model): uses baffleRefinementLevel
                    solid_entries.append(f"""            {solid}
            {{
                level ({rl_baffle[0]} {rl_baffle[1]});
                faceType internal;
                faceZone {sanitized};
            }}""")
                elif sanitized in rack_door_solids:
                    # Rack perforated door embedded inside a multi-solid STL.
                    # Use the same bucketed door level as the standalone
                    # baffle_<door>.stl path so both routes stay consistent.
                    # Reverse-lookup rack via the door-solids→rack map built
                    # alongside rack_door_filename_to_rack.
                    rack_name = rack_door_solid_to_rack.get(sanitized)
                    if rack_name:
                        _door_lvl = _door_level_for_rack(
                            data["racks"][rack_name], mc)
                    else:
                        # Solid listed in rack_door_solids but not bound to a
                        # rack (can't happen via normal flow; guard anyway).
                        _door_lvl = mc.get("rackDoorRefinementLevel", [1, 1])
                    solid_entries.append(f"""            {solid}
            {{
                level ({_door_lvl[0]} {_door_lvl[1]});
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
                    if filename in ("merged_rack.stl", "merged_rack_frame.stl"): rl = rl_rack
                    elif filename == "merged_acu.stl": rl = rl_acu
                    elif filename == "merged_box.stl": rl = rl_box
                    elif filename == "merged_partition_wall.stl": rl = rl_partition
                    elif filename == "merged_floor_ceiling.stl": rl = rl_floor
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
        # Combines user-defined searchableBox zones + through-zone STL entries.
        # Through-zones (merged_through_zone_<N>U.stl and legacy leakage_*.stl)
        # use mode=inside so snappy locally refines cells inside the closed STL
        # volume. No cellZone is created (through-zone is pure fluid).
        rz_region_lines = []
        for rz in rz_list:
            rz_name = _sanitize(rz["name"])
            lvl = rz.get("level", 2)
            rz_region_lines.append(f"""        {rz_name}
        {{
            mode inside;
            levels ((1e15 {lvl}));
        }}""")
        # Add through-zone STL entries collected during the refinementSurfaces loop.
        rz_region_lines.extend(through_zone_region_lines)
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
    nCellsBetweenLevels 2;
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
    nSmoothPatch 1;
    tolerance 1.5;
    nSolveIter 20;
    nRelaxIter 5;
    nFeatureSnapIter 10;
    implicitFeatureSnap true;
    explicitFeatureSnap true;
    multiRegionFeatureSnap true;
    nFaceSplitInterval 5;
    releasePoints true;
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
    maxNonOrtho 60;
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
        maxNonOrtho 70;
    }}
}}

writeFlags
(
    scalarLevels
);

mergeTolerance 1e-6;
"""
        (system_dir / "snappyHexMeshDict").write_text(content, encoding="utf-8")
        logger.info(f"Generated snappyHexMeshDict with {len(through_zone_solids)} through-zone solids (refinementRegions)")

    # ------------------------------------------------------------------
    # Mesh pipeline
    # ------------------------------------------------------------------

    def _get_uniform_door_sigma(self) -> float:
        """Return the shared sigma for all rack doors.

        All rack doors are merged into a single merged_rack_door.stl file
        and share one createBafflesDict entry, so they must have the same
        openAreaPct. Raises ValueError if multiple distinct values are found.
        """
        sigmas: set = set()
        for rack_name, rack in self._data.get("racks", {}).items():
            for side in ("inlet", "outlet"):
                door = rack.get(f"{side}Door")
                if not door or not door.get("enabled"):
                    continue
                perf = door.get("perforated") or {"openAreaPct": 64.0}
                sigmas.add(float(perf.get("openAreaPct", 64.0)) / 100.0)
        if len(sigmas) > 1:
            raise ValueError(f"All rack doors must have same openAreaPct, got: {sigmas}")
        return sigmas.pop() if sigmas else 0.64

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

        def _sigma_from_phys(p: dict, default_pct: float = 50.0) -> float:
            """Resolve porosity fraction from new perforated block or legacy
            porosity scalar (new format wins). Returns sigma in [0, 1]."""
            perf = p.get("perforated") if isinstance(p, dict) else None
            if perf and perf.get("openAreaPct") is not None:
                return max(0.0, min(1.0, float(perf["openAreaPct"]) / 100.0))
            porosity_pct = p.get("porosity", default_pct)
            try:
                return max(0.0, min(1.0, float(porosity_pct) / 100.0))
            except (TypeError, ValueError):
                return default_pct / 100.0

        for floor in data.get("floors", {}).values():
            for op in floor.get("openings", []):
                if op.get("physics", {}).get("type") == "perforated":
                    zone_name = _sanitize(op["solidName"])
                    porosity = _sigma_from_phys(op["physics"])
                    porosity_pct = porosity * 100.0

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
        #
        # Post-merge (Task 2): individual baffle files are:
        #   perforated_baffle_*.stl — perforated baffle models
        #   perforated_floor_*.stl  — perforated floor patches
        #   baffle_*.stl            — legacy (fallback)
        # merged_rack_door.stl   → ONE unified cyclic entry
        _already_added = set(bc["zone_name"] for bc in baffle_configs)

        # merged_wall_baffle.stl → ONE unified wall_baffle entry.
        # Surface-level faceZone "merged_wall_baffle" in snappyHexMeshDict
        # keeps all faces internal; createBaffles creates ONE master/slave
        # pair, then _convert_wall_baffle_patches_to_wall() makes them wall.
        if "merged_wall_baffle.stl" in self._stl_solids:
            if "merged_wall_baffle" not in _already_added:
                wall_baffle_configs.append({"zone_name": "merged_wall_baffle"})
                _already_added.add("merged_wall_baffle")

        # Scan individual perforated/baffle files AND legacy baffle_ files
        for filename, file_solids in self._stl_solids.items():
            is_perforated_individual = (
                filename.startswith("perforated_baffle_")
                or filename.startswith("perforated_floor_")
            )
            is_legacy_baffle = filename.startswith("baffle_")
            if not is_perforated_individual and not is_legacy_baffle:
                continue
            # 2026-04-17: through-zone STLs must NEVER produce a baffle pair.
            bucket = _parse_bucket_from_filename(filename)
            if bucket and bucket[0] == "through_zone":
                continue
            for solid in file_solids:
                zone_name = _sanitize(solid)
                if zone_name in _already_added:
                    continue
                phys = self._baffle_physics.get(zone_name, {"baffleType": "wall", "porosity": 0})
                baffle_type = phys.get("baffleType", "wall")
                porosity = _sigma_from_phys(phys, default_pct=0.0)
                porosity_pct = porosity * 100.0

                if porosity_pct > 0 or baffle_type == "perforated":
                    if porosity <= 0 and baffle_type == "perforated":
                        porosity = 0.5
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
                    wall_baffle_configs.append({"zone_name": zone_name})
                _already_added.add(zone_name)

        # merged_rack_door.stl → ONE unified cyclic entry for ALL door solids.
        # All doors share a single faceZone "merged_rack_door" so one
        # createBafflesDict entry suffices.
        if "merged_rack_door.stl" in self._stl_solids:
            if "merged_rack_door" not in _already_added:
                sigma = self._get_uniform_door_sigma()
                d_coeff = (1.0 - sigma**2) / (2.0 * sigma**2) if 0 < sigma < 1.0 else 0.0
                baffle_configs.append({
                    "zone_name": "merged_rack_door",
                    "porosity": sigma,
                    "d_coeff": d_coeff,
                    "baffleType": "perforated",
                })
                _already_added.add("merged_rack_door")
                logger.info(f"createBaffles: merged_rack_door → perforated σ={sigma:.2f}")

        # Legacy bucket files (merged_door_<N>U.stl, merged_wallBaffle_<N>U.stl)
        for filename, file_solids in self._stl_solids.items():
            bucket = _parse_bucket_from_filename(filename)
            if bucket is None:
                continue
            cat = bucket[0]

            if cat == "door":
                DEFAULT_DOOR_OPEN_AREA_PCT = 64.0
                rack_san_to_data: Dict[str, Any] = {
                    _sanitize(rn): rd for rn, rd in data.get("racks", {}).items()
                }
                for solid in file_solids:
                    zone_name = _sanitize(solid)
                    if zone_name in _already_added:
                        continue
                    sigma = DEFAULT_DOOR_OPEN_AREA_PCT / 100.0
                    for rack_san, rack in rack_san_to_data.items():
                        for side in ("inlet", "outlet"):
                            if zone_name == f"{rack_san}_{side}_door":
                                door = rack.get(f"{side}Door") or {}
                                perf = door.get("perforated")
                                if perf and perf.get("openAreaPct") is not None:
                                    sigma = float(perf["openAreaPct"]) / 100.0
                                elif door.get("porosity") is not None:
                                    sigma = float(door["porosity"]) / 100.0
                                break
                    sigma = max(0.0, min(1.0, sigma))
                    if sigma <= 0:
                        wall_baffle_configs.append({"zone_name": zone_name})
                    else:
                        d_coeff = (1.0 - sigma**2) / (2.0 * sigma**2) if sigma < 1.0 else 0.0
                        baffle_configs.append({
                            "zone_name": zone_name,
                            "porosity": sigma,
                            "d_coeff": d_coeff,
                            "baffleType": "perforated",
                        })
                    _already_added.add(zone_name)

            elif cat == "wallBaffle":
                for solid in file_solids:
                    zone_name = _sanitize(solid)
                    if zone_name in _already_added:
                        continue
                    wall_baffle_configs.append({"zone_name": zone_name})
                    _already_added.add(zone_name)

            elif cat == "perforatedBlankingPanel":
                bp_stem = Path(filename).stem
                if bp_stem in _already_added:
                    continue
                _d_bp = (1.0 - _BLANKING_PANEL_SIGMA**2) / (2.0 * _BLANKING_PANEL_SIGMA**2)
                baffle_configs.append({
                    "zone_name": bp_stem,
                    "porosity": _BLANKING_PANEL_SIGMA,
                    "d_coeff": _d_bp,
                    "baffleType": "perforated",
                })
                _already_added.add(bp_stem)
                logger.info(f"createBaffles: {bp_stem} → perforated σ={_BLANKING_PANEL_SIGMA}")

            elif cat == "wallBlankingPanel":
                pass

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
                if bf_name == "merged_wall_baffle.stl":
                    continue
                if (bf_name.startswith("baffle_")
                        or bf_name.startswith("perforated_floor_")):
                    for solid in bf_solids:
                        s = _sanitize(solid)
                        if s.startswith(san_floor_name) and s not in opening_solid_names and s not in _added_zones:
                            wall_baffle_configs.append({"zone_name": s})
                            _added_zones.add(s)

        # Store wall baffle zone names for post-createBaffles boundary conversion
        self._wall_baffle_zone_names = {wbc["zone_name"] for wbc in wall_baffle_configs}

        if not baffle_configs and not wall_baffle_configs:
            stale = self.mesh_dir / "system" / "createBafflesDict"
            if stale.exists():
                stale.unlink()
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

        sc = self._data.get("simulationConfig", {}) or {}
        wp_str = sc.get("writePrecision")
        if wp_str in ("single", "double"):
            double_prec = wp_str == "double"
        else:
            double_prec = bool(sc.get("doublePrecisionWrite", False))
        write_prec = 10 if double_prec else 6

        stubs = {
            "controlDict": f"""\
FoamFile {{ version 2.0; format ascii; class dictionary; object controlDict; }}
application     snappyHexMesh;
startFrom       startTime;
startTime       0;
stopAt          endTime;
endTime         0;
deltaT          1;
writeControl    timeStep;
writeInterval   1;
writeFormat     ascii;
writePrecision  {write_prec};
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
        """Ensure wall_baffle patches have type=wall in boundary.

        Uses the in-memory bridge registry as the authoritative source for
        which patches are wall baffles. This works in both mesh and solve-only
        modes (unlike _convert_wall_baffle_patches_to_wall which depends on
        _wall_baffle_zone_names being populated during mesh dict generation).
        """
        if self._bridge_registry is None or not boundary_path.exists():
            return
        wall_patches = {
            name for name, spec in self._bridge_registry.items()
            if spec.bc_type == "wall_baffle"
        }
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
            logger.info(f"Ensured wall baffle patches have type=wall (from bridge registry)")

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
        # Also include perforated baffles with porosity=0. Use the new format
        # (`perforated.openAreaPct`) when present, falling back to the legacy
        # `porosity` scalar. Pydantic always emits `porosity: 0.0` even when
        # the user-authored cfdrun specified perforated; without this check
        # the patch would be degraded to `wall` in the boundary file while
        # fields.py still emits `porousBafflePressure` → solver crash.
        for floor in self._data.get("floors", {}).values():
            for op in floor.get("openings", []):
                phys = op.get("physics", {})
                if phys.get("type") == "perforated":
                    perf = phys.get("perforated") or {}
                    if perf.get("openAreaPct") is not None:
                        porosity_pct = float(perf["openAreaPct"])
                    else:
                        porosity_pct = float(phys.get("porosity", 50) or 0)
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

        self._clean_stale_polymesh_zones(case_dir)
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

    def _remove_disconnected_cells(self, case_dir: Path) -> int:
        """Run checkMesh and clean up mesh artifacts.

        Multi-region meshes are logged but NOT auto-deleted — they surface
        as ``disconnected_regions`` meshWarnings for the user to review
        (could be legitimate Cold/Hot Aisle Containment).

        Isolated cells (zero/one non-boundary face) ARE auto-deleted since
        they are always tiny snappy artifacts that crash the solver.
        """
        from dctwin.runner.mesh_quality import (
            parse_checkmesh_regions,
            parse_checkmesh_warnings,
        )

        try:
            output = self._run_cmd_capture("checkMesh -allTopology", case_dir)
        except Exception as exc:
            logger.warning(f"checkMesh failed, skipping isolated-cell cleanup: {exc}")
            return 0

        # --- Strategy 1: multiple regions (warn only, no auto-delete) ---
        regions = parse_checkmesh_regions(output)

        if len(regions) > 1:
            total = sum(r["cells"] for r in regions)
            largest = max(regions, key=lambda r: r["cells"])
            logger.warning(
                f"checkMesh detected {len(regions)} disconnected regions "
                f"(total {total} cells, largest: {largest['name']} "
                f"with {largest['cells']} cells). NOT auto-removing — "
                f"this will be reported as a meshWarning for user review."
            )

        # --- Strategy 2: isolated cells (zero/one non-boundary face) ---
        warnings = parse_checkmesh_warnings(output)
        isolated = [w for w in warnings if w["code"] == "isolated_cells"]
        if not isolated:
            return 0

        n_isolated = isolated[0]["count"]
        logger.warning(
            f"Found {n_isolated} isolated cell(s) with zero/one non-boundary face "
            f"(point-connected but no flow coupling) — removing via oneInternalFaceCells"
        )

        # checkMesh writes the bad cells to constant/polyMesh/sets/oneInternalFaceCells.
        # We load that set then invert it: keepCells = all cells EXCEPT isolated ones.
        sets_dir = case_dir / "constant" / "polyMesh" / "sets"
        if not (sets_dir / "oneInternalFaceCells").exists():
            logger.warning(
                "oneInternalFaceCells set not found on disk — "
                "checkMesh may not have written it. Skipping removal."
            )
            return 0

        actions = [
            # Load the isolated cells
            '    {\n        name    keepCells;\n        type    cellSet;\n'
            '        action  new;\n        source  cellToCell;\n'
            '        sourceInfo { set oneInternalFaceCells; }\n    }',
            # Invert: now keepCells = all cells EXCEPT the isolated ones
            '    {\n        name    keepCells;\n        type    cellSet;\n'
            '        action  invert;\n    }',
        ]
        return self._do_subset_mesh(case_dir, actions, n_isolated)

    def _do_subset_mesh(self, case_dir: Path, topo_actions: list, n_removed: int) -> int:
        """Run topoSet + subsetMesh to keep only the cells selected by *topo_actions*."""
        import shutil as _shutil

        topo_dict = (
            "FoamFile\n{\n    version 2.0;\n    format ascii;\n"
            "    class dictionary;\n    object topoSetDict;\n}\n\n"
            "actions\n(\n" + "\n".join(topo_actions) + "\n);\n"
        )
        topo_path = case_dir / "system" / "topoSetDict"
        topo_path.write_text(topo_dict, encoding="utf-8")

        zero_dir = case_dir / "0"
        zero_backup = case_dir / "0_pre_subset"
        try:
            self._run_cmd("topoSet", case_dir)
            if zero_dir.exists():
                if zero_backup.exists():
                    _shutil.rmtree(zero_backup)
                zero_dir.rename(zero_backup)
                zero_dir.mkdir()
            self._run_cmd("subsetMesh -overwrite keepCells", case_dir)
        except RuntimeError as exc:
            logger.error(f"Failed to remove disconnected cells: {exc}")
            if zero_backup.exists() and not any(zero_dir.iterdir()):
                _shutil.rmtree(zero_dir)
                zero_backup.rename(zero_dir)
            return 0
        finally:
            topo_path.unlink(missing_ok=True)
            if zero_backup.exists():
                _shutil.rmtree(zero_backup, ignore_errors=True)

        logger.info(f"Removed {n_removed} disconnected/isolated cell(s) from mesh")
        return n_removed

    @staticmethod
    def _clean_processor_dirs(case_dir: Path) -> None:
        """Remove processor* directories from a case directory."""
        for pdir in sorted(_glob.glob(str(case_dir / "processor*"))):
            shutil.rmtree(pdir, ignore_errors=True)
        logger.info(f"Cleaned processor directories in {case_dir}")

    @staticmethod
    def _clean_stale_polymesh_zones(case_dir: Path) -> None:
        """Delete stale face/cell/point zone files in constant/polyMesh/ before blockMesh.

        blockMesh overwrites points/faces/cells/owner/neighbour/boundary but
        does NOT touch faceZones/cellZones/pointZones. Leftover zone files
        from a prior run's createBaffles output survive blockMesh and are
        picked up by decomposePar, which then distributes them to whichever
        processor scotch assigns — usually concentrated on one proc while
        other procs have 0 zones. snappyHexMesh parallel then fails with
        "faceZones are not synchronised on processors" in
        meshRefinement::checkCoupledFaceZones.
        """
        polymesh_dir = case_dir / "constant" / "polyMesh"
        for name in ("faceZones", "cellZones", "pointZones"):
            stale = polymesh_dir / name
            if stale.exists():
                stale.unlink()
                logger.info(f"Removed stale {name} from {polymesh_dir}")

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
        self._clean_stale_polymesh_zones(case_dir)
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
            f"mpirun -np {n_procs} snappyHexMesh -overwrite -parallel",
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

        These fields are INTERMEDIATE — createBaffles only needs a file to
        exist + patch-structure to match. `_setup_complete_case_in_mesh`
        overwrites them afterward with the final (possibly strict) output.
        So we always pass strict=False here: a mesh run must not be blocked
        by BC issues at this stage; the solve gate in
        _setup_complete_case_in_mesh(strict=True) catches them if the user
        proceeds to solve.
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
        self._build_field_generator().generate_fields(case_dir, patches, strict=False)
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

    def _run_cmd_capture(self, cmd: str, workdir: Path) -> str:
        """Run an OpenFOAM command and return its stdout+stderr as a string."""
        logger.info(f"Running (capture): {cmd}  (in {workdir})")
        if Path(self._FOAM_BASHRC).exists():
            shell_cmd = f'source "{self._FOAM_BASHRC}" && {cmd}'
        else:
            shell_cmd = cmd
        result = subprocess.run(
            shell_cmd, shell=True, executable="/bin/bash",
            cwd=str(workdir), capture_output=True, text=True, timeout=300,
        )
        return result.stdout + result.stderr
