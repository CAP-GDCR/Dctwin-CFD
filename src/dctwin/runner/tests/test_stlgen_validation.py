"""Tests for STLGenRunner geometry validation (room height vs floor z-coordinates)."""
import json
import pytest
from pathlib import Path

from dctwin.runner.stlgen_runner import STLGenRunner


def _make_cfd_export(room_height: float, floors: dict) -> dict:
    """Build a minimal cfd_export.json dict with room height and floor definitions."""
    return {
        "version": "1.0",
        "source": "STLGeneratorForOpenFoam",
        "projectName": "test",
        "room": {
            "height": room_height,
            "plane": [
                {"x": 0, "y": 0, "z": 0},
                {"x": 10, "y": 0, "z": 0},
                {"x": 10, "y": 10, "z": 0},
                {"x": 0, "y": 10, "z": 0},
            ],
            "locationInMesh": {"x": 5, "y": 5, "z": 1.5},
        },
        "floors": floors,
    }


def _setup_case(tmp_path: Path, cfd_data: dict) -> STLGenRunner:
    """Write cfd_export.json and return an STLGenRunner pointing at it."""
    case_dir = tmp_path / "case"
    model_dir = case_dir / "model"
    model_dir.mkdir(parents=True)
    cfd_path = model_dir / "cfd_export.json"
    cfd_path.write_text(json.dumps(cfd_data))
    return STLGenRunner(case_dir, cfd_export_path=cfd_path)


class TestRoomHeightFloorValidation:
    """room.height must be >= every floor's geometry.anchor.z."""

    def test_floor_z_exceeds_room_height_raises(self, tmp_path):
        """A floor at z=5.0 with room height=3.6 must raise RuntimeError."""
        data = _make_cfd_export(
            room_height=3.6,
            floors={
                "Ground_Floor": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
                "Real_Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 5.0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        with pytest.raises(RuntimeError, match=r"floor.*Real_Ceiling.*z=5\.0.*exceeds.*room\.height=3\.6"):
            runner.validate_geometry()

    def test_floor_z_equals_room_height_passes(self, tmp_path):
        """A floor at z=3.6 with room height=3.6 is valid (the ceiling itself)."""
        data = _make_cfd_export(
            room_height=3.6,
            floors={
                "Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 3.6}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        # Should not raise
        runner.validate_geometry()

    def test_all_floors_within_room_height_passes(self, tmp_path):
        """Multiple floors all within room height should pass."""
        data = _make_cfd_export(
            room_height=5.0,
            floors={
                "Ground": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
                "False_Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 3.6}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wallBaffle",
                },
                "Real_Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 5.0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        runner.validate_geometry()

    def test_sub_mm_floating_point_rounding_passes(self, tmp_path):
        """Sub-mm JS export rounding (e.g. z=3.4000000953) should be tolerated."""
        data = _make_cfd_export(
            room_height=3.4,
            floors={
                "Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 3.4000000953674316}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        runner.validate_geometry()  # should not raise

    def test_error_message_includes_fix_hint(self, tmp_path):
        """Error message should suggest updating room height in 2D floor plan."""
        data = _make_cfd_export(
            room_height=3.6,
            floors={
                "Top_Ceiling": {
                    "stlFile": "merged_floor_ceiling.stl",
                    "geometry": {"anchor": {"x": 0, "y": 0, "z": 5.0}, "size": {"W": 10, "D": 10}},
                    "defaultBC": "wall",
                },
            },
        )
        runner = _setup_case(tmp_path, data)
        with pytest.raises(RuntimeError, match=r"2D.*floor.*plan|房间.*高度"):
            runner.validate_geometry()
