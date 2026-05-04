"""Parse OpenFOAM checkMesh output into structured, severity-leveled warnings.

Warning levels:
  1 (CRITICAL) — solver will definitely fail (e.g. isolated cells, zero determinant)
  2 (SEVERE)   — solver may diverge (e.g. high skewness, bad tet decomposition)
  3 (WARNING)  — accuracy may be affected (e.g. high non-orthogonality)
"""
from __future__ import annotations

import re
from typing import Any, Dict, List

_RE_ISOLATED = re.compile(
    r"Writing\s+(\d+)\s+cells?\s+with\s+zero\s+or\s+one\s+non-boundary\s+face"
)
_RE_REGION = re.compile(
    r"Writing\s+region\s+\d+\s+\((point connected|fully disconnected)\)\s+with\s+(\d+)\s+cells?\s+to\s+cellSet\s+(\S+)"
)
_RE_SMALL_DET = re.compile(
    r"\*\*\*Cells\s+with\s+small\s+determinant.*?number\s+of\s+cells:\s+(\d+)"
)
_RE_BAD_TETS = re.compile(
    r"\*\*\*Error\s+in\s+face\s+tets:\s+(\d+)\s+faces"
)
_RE_SKEWNESS_FAIL = re.compile(
    r"Max\s+skewness\s*=\s*([\d.]+)\s*--\s*\*\*\*FAILED"
)
_RE_SKEWNESS_INTERNAL = re.compile(
    r"Max\s+internal\s+skewness\s+exceeded:\s*([\d.]+)"
)
_RE_NON_ORTHO = re.compile(
    r"non-orthogonality\s+Max:\s*([\d.]+)"
)
_RE_NEGATIVE_VOL = re.compile(
    r"\*\*\*Error\s+in\s+cell\s+volumes.*?(\d+)\s+negative"
)
_RE_NUM_REGIONS = re.compile(
    r"Number\s+of\s+regions:\s+(\d+)"
)


def parse_checkmesh_warnings(output: str) -> List[Dict[str, Any]]:
    """Parse checkMesh stdout text into a list of warning dicts.

    Each dict contains:
      level (int): 1=critical, 2=severe, 3=warning
      code (str): machine-readable identifier
      message (str): human-readable description
      count (int): number of affected cells/faces (where applicable)
      max_value (float): max measured value (where applicable)
    """
    warnings: List[Dict[str, Any]] = []

    m = _RE_ISOLATED.search(output)
    if m:
        n = int(m.group(1))
        warnings.append({
            "level": 1,
            "code": "isolated_cells",
            "message": (
                f"检测到 {n} 个孤立单元（无内部连接面），"
                f"求解器将因矩阵奇异而失败。"
                f"通常由 rack 框架尖角处网格碎片引起。"
            ),
            "count": n,
        })

    m = _RE_SMALL_DET.search(output)
    if m:
        n = int(m.group(1))
        warnings.append({
            "level": 1,
            "code": "small_determinant",
            "message": (
                f"检测到 {n} 个退化单元（行列式 < 0.001），"
                f"这些单元几何严重变形，求解器可能失败。"
            ),
            "count": n,
        })

    m = _RE_BAD_TETS.search(output)
    if m:
        n = int(m.group(1))
        warnings.append({
            "level": 2,
            "code": "bad_face_tets",
            "message": f"检测到 {n} 个面的四面体分解质量差或体积为负。",
            "count": n,
        })

    m = _RE_SKEWNESS_FAIL.search(output)
    if m:
        val = float(m.group(1))
        warnings.append({
            "level": 2,
            "code": "high_skewness",
            "message": f"最大网格歪斜度 {val:.1f} 超过阈值，可能导致求解发散。",
            "max_value": val,
        })
    elif not m:
        m2 = _RE_SKEWNESS_INTERNAL.search(output)
        if m2:
            val = float(m2.group(1))
            warnings.append({
                "level": 2,
                "code": "high_skewness",
                "message": f"最大内部网格歪斜度 {val:.1f} 超过阈值，可能导致求解发散。",
                "max_value": val,
            })

    m = _RE_NEGATIVE_VOL.search(output)
    if m:
        n = int(m.group(1))
        warnings.append({
            "level": 1,
            "code": "negative_volume",
            "message": f"检测到 {n} 个负体积单元，网格存在严重拓扑错误。",
            "count": n,
        })

    m = _RE_NON_ORTHO.search(output)
    if m:
        val = float(m.group(1))
        if val > 70:
            warnings.append({
                "level": 3,
                "code": "high_non_orthogonality",
                "message": f"最大非正交角 {val:.1f}° 较高，可能影响计算精度。",
                "max_value": val,
            })

    # --- Multi-region detection ---
    # Detailed per-region info from -allTopology output (runner path).
    regions = parse_checkmesh_regions(output)
    # Fallback: basic "Number of regions: N" (server checkMesh path).
    n_regions_basic = 0
    for m_nr in _RE_NUM_REGIONS.finditer(output):
        n_regions_basic = max(n_regions_basic, int(m_nr.group(1)))

    if len(regions) > 1:
        total = sum(r["cells"] for r in regions)
        largest = max(regions, key=lambda r: r["cells"])
        others = [r for r in regions if r["name"] != largest["name"]]
        others_cells = sum(r["cells"] for r in others)
        warnings.append({
            "level": 1,
            "code": "disconnected_regions",
            "message": (
                f"检测到 {len(regions)} 个独立网格区域"
                f"（最大区域 {largest['cells']} 单元，"
                f"另有 {len(others)} 个区域共 {others_cells} 单元）。"
                f"多区域通常由 STL 几何缝隙或重叠面引起，"
                f"求解器无法在断开的区域间传递场量。"
                f"如果这是冷/热通道隔离场景，多区域属于正常行为。"
            ),
            "count": len(regions),
            "regions": [
                {"name": r["name"], "type": r["type"], "cells": r["cells"]}
                for r in regions
            ],
        })
    elif n_regions_basic > 1:
        warnings.append({
            "level": 1,
            "code": "disconnected_regions",
            "message": (
                f"检测到 {n_regions_basic} 个独立网格区域。"
                f"多区域通常由 STL 几何缝隙或重叠面引起，"
                f"求解器无法在断开的区域间传递场量。"
                f"如果这是冷/热通道隔离场景，多区域属于正常行为。"
            ),
            "count": n_regions_basic,
            "regions": [],
        })

    warnings.sort(key=lambda w: w["level"])
    return warnings


def parse_checkmesh_regions(output: str) -> List[Dict[str, Any]]:
    """Extract region connectivity info from checkMesh -allTopology output.

    Returns a list of dicts, each with:
      name (str): cellSet name written by checkMesh (e.g. "region2")
      type (str): "point connected" or "fully disconnected"
      cells (int): number of cells in this region

    Only populated when checkMesh reports multiple regions.
    Single-region meshes produce no region lines → empty list.
    """
    return [
        {"name": m.group(3), "type": m.group(1), "cells": int(m.group(2))}
        for m in _RE_REGION.finditer(output)
    ]
