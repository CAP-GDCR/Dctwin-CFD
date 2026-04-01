"""
STL file writer utilities.

This module provides functions for writing STL files in ASCII format,
supporting multiple solid regions within a single file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Sequence, Union

from .base import Vec3, Triangle, SolidRegion, compute_triangle_normal


class STLWriter:
    """
    A class for writing STL files with multiple solid regions.

    STL files can contain multiple named "solid" sections, each of which
    maps to a separate patch in OpenFOAM.
    """

    @staticmethod
    def write_ascii_stl(
        filepath: Union[str, Path],
        solids: Dict[str, Sequence[Triangle]],
    ) -> None:
        """
        Write an ASCII STL file with multiple solid regions.

        Args:
            filepath: Path to the output STL file
            solids: Dictionary mapping solid names to lists of triangles

        The output file will contain multiple solid sections:
            solid <name1>
              facet normal ...
              ...
            endsolid <name1>
            solid <name2>
              ...
            endsolid <name2>
        """
        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with filepath.open("w", encoding="utf-8", newline="\n") as f:
            for solid_name, triangles in solids.items():
                f.write(f"solid {solid_name}\n")
                for tri in triangles:
                    normal = compute_triangle_normal(tri)
                    f.write(f"  facet normal {normal.x:.8g} {normal.y:.8g} {normal.z:.8g}\n")
                    f.write("    outer loop\n")
                    for vertex in tri:
                        f.write(f"      vertex {vertex.x:.8g} {vertex.y:.8g} {vertex.z:.8g}\n")
                    f.write("    endloop\n")
                    f.write("  endfacet\n")
                f.write(f"endsolid {solid_name}\n")

    @staticmethod
    def read_ascii_stl(
        filepath: Union[str, Path],
    ) -> Dict[str, List[Triangle]]:
        """
        Read an ASCII STL file and return a dictionary of solid names to triangle lists.

        Parses the STL format:
            solid <name>
              facet normal nx ny nz
                outer loop
                  vertex x1 y1 z1
                  vertex x2 y2 z2
                  vertex x3 y3 z3
                endloop
              endfacet
            endsolid <name>

        Args:
            filepath: Path to the ASCII STL file

        Returns:
            Dictionary mapping solid names to lists of Triangle (Tuple[Vec3, Vec3, Vec3])

        Raises:
            FileNotFoundError: If the file does not exist
            ValueError: If the file cannot be parsed
        """
        filepath = Path(filepath)
        if not filepath.exists():
            raise FileNotFoundError(f"STL file not found: {filepath}")

        solids: Dict[str, List[Triangle]] = {}
        current_solid: str = ""
        current_vertices: List[Vec3] = []
        unnamed_count = 0

        with filepath.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()

                if stripped.startswith("solid ") or stripped == "solid":
                    name = stripped[6:].strip() if len(stripped) > 6 else ""
                    if not name:
                        name = f"unnamed_{unnamed_count}"
                        unnamed_count += 1
                    current_solid = name
                    if current_solid not in solids:
                        solids[current_solid] = []

                elif stripped.startswith("vertex "):
                    parts = stripped.split()
                    if len(parts) >= 4:
                        try:
                            vx = float(parts[1])
                            vy = float(parts[2])
                            vz = float(parts[3])
                            current_vertices.append(Vec3(vx, vy, vz))
                        except (ValueError, IndexError):
                            pass

                elif stripped == "endloop":
                    if len(current_vertices) == 3 and current_solid:
                        tri: Triangle = (
                            current_vertices[0],
                            current_vertices[1],
                            current_vertices[2],
                        )
                        solids[current_solid].append(tri)
                    current_vertices = []

                elif stripped.startswith("endsolid"):
                    current_vertices = []

        if not solids:
            raise ValueError(f"No solid regions found in STL file: {filepath}")

        return solids

    @staticmethod
    def write_from_regions(
        filepath: Union[str, Path],
        regions: List[SolidRegion],
    ) -> None:
        """
        Write an ASCII STL file from a list of SolidRegion objects.

        Args:
            filepath: Path to the output STL file
            regions: List of SolidRegion objects
        """
        solids = {region.name: region.triangles for region in regions}
        STLWriter.write_ascii_stl(filepath, solids)

    @staticmethod
    def write_binary_stl(
        filepath: Union[str, Path],
        triangles: Sequence[Triangle],
    ) -> None:
        """
        Write a binary STL file.

        Note: Binary STL does not support multiple named solids.
        For multi-region STL files, use write_ascii_stl instead.

        Args:
            filepath: Path to the output STL file
            triangles: List of triangles to write
        """
        import struct

        filepath = Path(filepath)
        filepath.parent.mkdir(parents=True, exist_ok=True)

        with filepath.open("wb") as f:
            # Write 80-byte header
            header = b"Binary STL generated by dctwin geometry module"
            header = header.ljust(80, b"\0")
            f.write(header)

            # Write triangle count
            f.write(struct.pack("<I", len(triangles)))

            # Write triangles
            for tri in triangles:
                normal = compute_triangle_normal(tri)
                # Normal vector
                f.write(struct.pack("<fff", normal.x, normal.y, normal.z))
                # Vertices
                for vertex in tri:
                    f.write(struct.pack("<fff", vertex.x, vertex.y, vertex.z))
                # Attribute byte count (unused)
                f.write(struct.pack("<H", 0))

    @staticmethod
    def merge_stl_files(
        output_path: Union[str, Path],
        input_paths: List[Union[str, Path]],
    ) -> None:
        """
        Merge multiple ASCII STL files into a single file.

        This preserves all solid regions from each input file.

        Args:
            output_path: Path for the merged STL file
            input_paths: List of input STL file paths
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with output_path.open("w", encoding="utf-8", newline="\n") as out_file:
            for input_path in input_paths:
                input_path = Path(input_path)
                if input_path.exists():
                    with input_path.open("r", encoding="utf-8") as in_file:
                        out_file.write(in_file.read())

    @staticmethod
    def validate_stl(filepath: Union[str, Path]) -> bool:
        """
        Validate an ASCII STL file for correct syntax.

        Args:
            filepath: Path to the STL file to validate

        Returns:
            True if the file is valid, False otherwise
        """
        filepath = Path(filepath)
        if not filepath.exists():
            return False

        try:
            with filepath.open("r", encoding="utf-8") as f:
                content = f.read()

            # Count solid/endsolid pairs
            solid_count = content.count("solid ")
            endsolid_count = content.count("endsolid ")

            if solid_count != endsolid_count:
                return False

            # Count facet/endfacet pairs
            facet_count = content.count("facet normal")
            endfacet_count = content.count("endfacet")

            if facet_count != endfacet_count:
                return False

            # Count outer loop/endloop pairs
            loop_count = content.count("outer loop")
            endloop_count = content.count("endloop")

            if loop_count != endloop_count:
                return False

            return True
        except Exception:
            return False

    @staticmethod
    def get_solid_names(filepath: Union[str, Path]) -> List[str]:
        """
        Extract the names of all solid regions in an STL file.

        Args:
            filepath: Path to the STL file

        Returns:
            List of solid region names
        """
        filepath = Path(filepath)
        names = []

        with filepath.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith("solid ") and not line.startswith("solid\n"):
                    name = line[6:].strip()
                    if name:
                        names.append(name)

        return names

    @staticmethod
    def count_triangles(filepath: Union[str, Path]) -> int:
        """
        Count the number of triangles in an STL file.

        Args:
            filepath: Path to the STL file

        Returns:
            Number of triangles
        """
        filepath = Path(filepath)

        with filepath.open("r", encoding="utf-8") as f:
            content = f.read()

        return content.count("facet normal")


def create_box_stl(
    name: str,
    v_min: Vec3,
    v_max: Vec3,
    face_names: Dict[str, str] = None,
) -> Dict[str, List[Triangle]]:
    """
    Create STL solids for a box (hexahedron).

    Args:
        name: Base name for the solid regions
        v_min: Minimum corner of the box
        v_max: Maximum corner of the box
        face_names: Optional dictionary mapping face directions to custom names
                   If None, uses default names like "{name}_front", "{name}_rear", etc.

    Returns:
        Dictionary mapping solid names to lists of triangles
    """
    from .base import rect_to_triangles, orient_triangles_outward

    x0, y0, z0 = v_min.x, v_min.y, v_min.z
    x1, y1, z1 = v_max.x, v_max.y, v_max.z

    if face_names is None:
        face_names = {
            "front": f"{name}_front",
            "rear": f"{name}_rear",
            "left": f"{name}_left",
            "right": f"{name}_right",
            "top": f"{name}_top",
            "bottom": f"{name}_bottom",
        }

    # Define face vertices and expected normals
    faces_data = {
        "front": {
            "vertices": (
                Vec3(x0, y0, z0),  # v00
                Vec3(x1, y0, z0),  # v10
                Vec3(x1, y0, z1),  # v11
                Vec3(x0, y0, z1),  # v01
            ),
            "normal": Vec3(0, -1, 0),
        },
        "rear": {
            "vertices": (
                Vec3(x1, y1, z0),  # v00
                Vec3(x0, y1, z0),  # v10
                Vec3(x0, y1, z1),  # v11
                Vec3(x1, y1, z1),  # v01
            ),
            "normal": Vec3(0, 1, 0),
        },
        "left": {
            "vertices": (
                Vec3(x0, y1, z0),  # v00
                Vec3(x0, y0, z0),  # v10
                Vec3(x0, y0, z1),  # v11
                Vec3(x0, y1, z1),  # v01
            ),
            "normal": Vec3(-1, 0, 0),
        },
        "right": {
            "vertices": (
                Vec3(x1, y0, z0),  # v00
                Vec3(x1, y1, z0),  # v10
                Vec3(x1, y1, z1),  # v11
                Vec3(x1, y0, z1),  # v01
            ),
            "normal": Vec3(1, 0, 0),
        },
        "top": {
            "vertices": (
                Vec3(x0, y0, z1),  # v00
                Vec3(x1, y0, z1),  # v10
                Vec3(x1, y1, z1),  # v11
                Vec3(x0, y1, z1),  # v01
            ),
            "normal": Vec3(0, 0, 1),
        },
        "bottom": {
            "vertices": (
                Vec3(x0, y1, z0),  # v00
                Vec3(x1, y1, z0),  # v10
                Vec3(x1, y0, z0),  # v11
                Vec3(x0, y0, z0),  # v01
            ),
            "normal": Vec3(0, 0, -1),
        },
    }

    solids = {}
    for face_key, data in faces_data.items():
        solid_name = face_names.get(face_key, f"{name}_{face_key}")
        v00, v10, v11, v01 = data["vertices"]
        triangles = rect_to_triangles(v00, v10, v11, v01)
        triangles = orient_triangles_outward(triangles, data["normal"])
        solids[solid_name] = triangles

    return solids


def create_partial_face_stl(
    name: str,
    face_origin: Vec3,
    face_u: Vec3,
    face_v: Vec3,
    normal: Vec3,
) -> List[Triangle]:
    """
    Create triangles for a rectangular face defined by origin and two edge vectors.

    Args:
        name: Name for this face (for documentation purposes)
        face_origin: Origin point (one corner) of the face
        face_u: Vector along the first edge
        face_v: Vector along the second edge
        normal: Expected outward normal direction

    Returns:
        List of two triangles forming the rectangular face
    """
    from .base import rect_to_triangles, orient_triangles_outward

    v00 = face_origin
    v10 = face_origin + face_u
    v11 = face_origin + face_u + face_v
    v01 = face_origin + face_v

    triangles = rect_to_triangles(v00, v10, v11, v01)
    return orient_triangles_outward(triangles, normal)
