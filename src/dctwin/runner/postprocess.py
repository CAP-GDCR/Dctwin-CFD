"""
Post-processing module for OpenFOAM CFD results using PyVista.

Renders temperature, velocity, and streamline visualizations from foamToVTK
output (VTM files) or directly from OpenFOAM case directories. All rendering
is off-screen (headless) -- images are returned as base64-encoded PNGs suitable
for sending to a browser frontend via REST API.

Environment requirements:
    - pyvista >= 0.44  (tested with 0.47.1)
    - vtk >= 9.0       (tested with 9.5.1)
    - xvfb or osmesa for headless rendering (pyvista.start_xvfb())

Typical usage from a REST API handler:

    from dctwin.runner.postprocess import CFDPostProcessor, PostProcessor

    # --- Low-level API (VTM-centric) ---
    pp = CFDPostProcessor.from_vtm("/path/to/case_500.vtm")
    # -- or --
    pp = CFDPostProcessor.from_openfoam_case("/path/to/case", time_value=500)

    b64_png = pp.render_temperature(clim=(288, 310))
    b64_png = pp.render_velocity_magnitude()
    b64_png = pp.render_streamlines(seed_center=(4, 4, 1.2), seed_radius=0.5)
    b64_png = pp.render_slice_temperature(normal='z', origin=(4, 4, 0.6))

    # --- High-level API (server.py integration) ---
    post = PostProcessor(run_dir=Path("/path/to/log/2026-03-22_case"))
    vtk_dir = post.convert_to_vtk()
    info = post.load_vtk()
    b64 = post.render_surface_field("T")
    b64 = post.render_streamlines()

    # Return b64_png in JSON response to browser
"""

from __future__ import annotations

import base64
import json
import logging
import os
import struct
import subprocess
import tempfile
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Sequence, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy pyvista import with headless setup
# ---------------------------------------------------------------------------

_pv = None  # module-level cache


def _get_pyvista():
    """Import pyvista with off-screen rendering configured.

    Uses VTK's OSMesa backend for clean headless rendering without
    requiring Xvfb, EGL, or a display server.  Must set environment
    variables BEFORE the first VTK/PyVista import.
    """
    global _pv
    if _pv is not None:
        return _pv

    # Force OSMesa backend — avoids GLX/EGL failures on headless servers
    os.environ.setdefault("VTK_DEFAULT_OPENGL_WINDOW", "vtkOSOpenGLRenderWindow")
    os.environ["PYVISTA_OFF_SCREEN"] = "true"

    import pyvista as pv

    pv.OFF_SCREEN = True

    # Suppress VTK warnings in logs
    pv.set_error_output_file(os.devnull)

    _pv = pv
    return pv


# ---------------------------------------------------------------------------
# Data classes for configuration
# ---------------------------------------------------------------------------


@dataclass
class RenderConfig:
    """Common rendering parameters."""
    window_size: Tuple[int, int] = (1920, 1080)
    background_color: str = "white"
    camera_position: Optional[str | Sequence] = "iso"  # 'xy','xz','yz','iso' or [(pos),(focal),(viewup)]
    camera_zoom: float = 1.0
    show_edges: bool = False
    opacity: float = 1.0
    lighting: bool = True
    smooth_shading: bool = True


@dataclass
class ScalarBarConfig:
    """Color bar / scalar bar appearance."""
    title: str = ""
    n_labels: int = 5
    fmt: str = "%.1f"
    vertical: bool = True
    width: float = 0.08
    height: float = 0.5
    position_x: float = 0.87
    position_y: float = 0.25
    title_font_size: int = 16
    label_font_size: int = 14
    color: str = "black"
    bold: bool = True


@dataclass
class StreamlineConfig:
    """Streamline computation parameters."""
    seed_center: Optional[Tuple[float, float, float]] = None
    seed_radius: float = 0.5
    n_seed_points: int = 50
    seed_points: Optional[np.ndarray] = None  # explicit seed points (N,3)
    integration_direction: Literal["both", "forward", "backward"] = "both"
    max_steps: int = 2000
    initial_step_length: float = 0.5
    max_step_length: float = 1.0
    terminal_speed: float = 1e-12
    tube_radius: float = 0.005
    color_by: Literal["T", "speed", "solid"] = "speed"  # color streamlines by field


# ---------------------------------------------------------------------------
# Main post-processor class
# ---------------------------------------------------------------------------


class CFDPostProcessor:
    """
    Headless renderer for OpenFOAM CFD results.

    Loads a VTM multi-block dataset (from foamToVTK) or reads an OpenFOAM
    case directory directly via pyvista.OpenFOAMReader. Provides methods
    to render various field visualizations and return them as base64 PNG
    strings.

    Attributes:
        multiblock: The loaded pyvista.MultiBlock dataset.
        internal:   The internal (volume) mesh (UnstructuredGrid).
        boundary:   The boundary MultiBlock containing all patch surfaces.
        patch_names: List of boundary patch names.
    """

    def __init__(self, multiblock):
        """
        Initialize from an already-loaded pyvista.MultiBlock.

        Use the class methods `from_vtm()` or `from_openfoam_case()` instead
        of calling this directly.
        """
        pv = _get_pyvista()

        self.multiblock = multiblock

        # ---- Locate internal mesh and boundary block ----
        # foamToVTK layout:    [0] = "internal" (UnstructuredGrid)
        #                      [1] = "boundary" (MultiBlock of PolyData)
        # OpenFOAMReader layout: keys "internalMesh" and "boundary"
        self.internal = None
        self.boundary = None

        for i in range(multiblock.n_blocks):
            key = multiblock.keys()[i]
            block = multiblock[i]
            if block is None:
                continue
            if key in ("internal", "internalMesh"):
                self.internal = block
            elif key == "boundary" and isinstance(block, pv.MultiBlock):
                self.boundary = block

        if self.internal is None:
            raise ValueError(
                "Could not find internal mesh in MultiBlock. "
                f"Available keys: {list(multiblock.keys())}"
            )

        self.patch_names: List[str] = []
        if self.boundary is not None:
            self.patch_names = list(self.boundary.keys())

        # Pre-compute velocity magnitude on internal mesh if U is present
        if "U" in self.internal.point_data:
            U = self.internal.point_data["U"]
            self.internal.point_data["Umag"] = np.linalg.norm(U, axis=1)
            # Also store individual components
            self.internal.point_data["Ux"] = U[:, 0]
            self.internal.point_data["Uy"] = U[:, 1]
            self.internal.point_data["Uz"] = U[:, 2]
            self.internal.set_active_vectors("U")

        # Pre-compute on boundary patches too
        if self.boundary is not None:
            for i in range(self.boundary.n_blocks):
                patch = self.boundary[i]
                if patch is not None and "U" in patch.point_data:
                    U = patch.point_data["U"]
                    patch.point_data["Umag"] = np.linalg.norm(U, axis=1)
                    patch.point_data["Ux"] = U[:, 0]
                    patch.point_data["Uy"] = U[:, 1]
                    patch.point_data["Uz"] = U[:, 2]

        logger.info(
            "CFDPostProcessor initialized: %d internal cells, %d boundary patches",
            self.internal.n_cells,
            len(self.patch_names),
        )

    # ------------------------------------------------------------------
    # Factory methods
    # ------------------------------------------------------------------

    @classmethod
    def from_vtm(cls, vtm_path: str | Path) -> "CFDPostProcessor":
        """
        Load from a VTM (VTK Multi-block) file produced by foamToVTK.

        Parameters
        ----------
        vtm_path : str or Path
            Path to the .vtm file, e.g.
            ``case/VTK/casename_500.vtm``

        Returns
        -------
        CFDPostProcessor
        """
        pv = _get_pyvista()
        vtm_path = str(vtm_path)
        if not os.path.isfile(vtm_path):
            raise FileNotFoundError(f"VTM file not found: {vtm_path}")
        mb = pv.read(vtm_path)
        return cls(mb)

    @classmethod
    def from_openfoam_case(
        cls,
        case_dir: str | Path,
        time_value: Optional[float] = None,
    ) -> "CFDPostProcessor":
        """
        Load directly from an OpenFOAM case directory using pyvista.OpenFOAMReader.

        This avoids the need to run ``foamToVTK`` first. PyVista reads the
        ``constant/polyMesh`` and time directories natively.

        Parameters
        ----------
        case_dir : str or Path
            Path to the OpenFOAM case directory (containing ``system/``,
            ``constant/``, and time step directories).
        time_value : float, optional
            Which time step to load. If None, loads the latest available.

        Returns
        -------
        CFDPostProcessor
        """
        pv = _get_pyvista()
        case_dir = Path(case_dir)
        if not case_dir.is_dir():
            raise NotADirectoryError(f"Case directory not found: {case_dir}")

        # OpenFOAMReader needs a .foam file (can be empty) in the case dir
        foam_file = case_dir / "pyvista_reader.foam"
        created_foam = False
        if not foam_file.exists():
            foam_file.touch()
            created_foam = True

        try:
            reader = pv.OpenFOAMReader(str(foam_file))
            available_times = reader.time_values

            if time_value is not None:
                reader.set_active_time_value(time_value)
            elif len(available_times) > 0:
                reader.set_active_time_value(available_times[-1])

            logger.info(
                "Reading OpenFOAM case: %s at t=%s (available: %s)",
                case_dir,
                reader.active_time_value,
                available_times,
            )
            mb = reader.read()
        finally:
            if created_foam:
                foam_file.unlink(missing_ok=True)

        return cls(mb)

    # ------------------------------------------------------------------
    # Informational methods
    # ------------------------------------------------------------------

    def list_patches(self) -> List[Dict[str, Any]]:
        """
        Return a list of all boundary patches with metadata.

        Returns
        -------
        list of dict
            Each dict has keys: ``name``, ``n_cells``, ``n_points``,
            ``fields`` (list of field names).
        """
        results = []
        if self.boundary is None:
            return results
        for i in range(self.boundary.n_blocks):
            name = self.boundary.keys()[i]
            patch = self.boundary[i]
            if patch is None:
                results.append({"name": name, "n_cells": 0, "n_points": 0, "fields": []})
            else:
                results.append({
                    "name": name,
                    "n_cells": patch.n_cells,
                    "n_points": patch.n_points,
                    "fields": list(patch.point_data.keys()),
                })
        return results

    def get_field_range(
        self,
        field_name: str,
        source: Literal["internal", "boundary", "all"] = "all",
    ) -> Tuple[float, float]:
        """
        Get the min/max range of a scalar field.

        Parameters
        ----------
        field_name : str
            Name of the field (e.g. 'T', 'Umag', 'p').
        source : str
            'internal', 'boundary', or 'all'.

        Returns
        -------
        (min_val, max_val)
        """
        vals = []
        if source in ("internal", "all") and field_name in self.internal.point_data:
            vals.append(self.internal.point_data[field_name])
        if source in ("boundary", "all") and self.boundary is not None:
            for i in range(self.boundary.n_blocks):
                patch = self.boundary[i]
                if patch is not None and field_name in patch.point_data:
                    vals.append(patch.point_data[field_name])
        if not vals:
            raise KeyError(f"Field '{field_name}' not found in {source} mesh(es)")
        combined = np.concatenate(vals)
        return float(combined.min()), float(combined.max())

    # ------------------------------------------------------------------
    # Internal rendering helpers
    # ------------------------------------------------------------------

    def _make_plotter(self, config: RenderConfig):
        """Create a configured off-screen Plotter."""
        pv = _get_pyvista()
        p = pv.Plotter(
            off_screen=True,
            window_size=list(config.window_size),
        )
        p.set_background(config.background_color)
        return p

    def _set_camera(self, plotter, config: RenderConfig):
        """Apply camera settings to the plotter."""
        if isinstance(config.camera_position, str):
            plotter.camera_position = config.camera_position
        elif config.camera_position is not None:
            plotter.camera_position = config.camera_position
        if config.camera_zoom != 1.0:
            plotter.camera.zoom(config.camera_zoom)

    def _render_to_base64(
        self,
        plotter,
        config: RenderConfig,
    ) -> str:
        """
        Finalize the plotter and return the rendered image as a base64 PNG string.

        Parameters
        ----------
        plotter : pv.Plotter
            The configured plotter with meshes already added.
        config : RenderConfig
            Rendering configuration (camera, etc).

        Returns
        -------
        str
            Base64-encoded PNG image data (no ``data:image/png;base64,`` prefix).
        """
        self._set_camera(plotter, config)
        plotter.show(auto_close=False)

        # Write screenshot to a temporary file, then read back as bytes
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            plotter.screenshot(tmp_path)
            with open(tmp_path, "rb") as f:
                png_bytes = f.read()
        finally:
            os.unlink(tmp_path)
            plotter.close()

        return base64.b64encode(png_bytes).decode("ascii")

    # ------------------------------------------------------------------
    # Rendering methods
    # ------------------------------------------------------------------

    def render_temperature(
        self,
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "coolwarm",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        patches: Optional[List[str]] = None,
        show_internal: bool = False,
    ) -> str:
        """
        Render the temperature field (T) on boundary surfaces.

        Parameters
        ----------
        clim : tuple of (min, max), optional
            Color range. If None, uses the data range.
        cmap : str
            Matplotlib colormap name. Default "coolwarm".
        render_config : RenderConfig, optional
            Rendering parameters. If None, uses defaults.
        scalar_bar_config : ScalarBarConfig, optional
            Color bar parameters. If None, uses defaults with title "Temperature [K]".
        patches : list of str, optional
            Specific patch names to render. If None, renders all boundary patches.
        show_internal : bool
            If True, also render the internal mesh as a translucent volume.

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        return self._render_scalar_field(
            field_name="T",
            clim=clim,
            cmap=cmap,
            default_title="Temperature [K]",
            render_config=render_config,
            scalar_bar_config=scalar_bar_config,
            patches=patches,
            show_internal=show_internal,
        )

    def render_velocity_magnitude(
        self,
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "jet",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        patches: Optional[List[str]] = None,
        show_internal: bool = False,
    ) -> str:
        """
        Render the velocity magnitude field (|U|) on boundary surfaces.

        Parameters
        ----------
        clim : tuple of (min, max), optional
            Color range for velocity magnitude [m/s].
        cmap : str
            Matplotlib colormap name. Default "jet".
        render_config : RenderConfig, optional
        scalar_bar_config : ScalarBarConfig, optional
        patches : list of str, optional
        show_internal : bool

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        return self._render_scalar_field(
            field_name="Umag",
            clim=clim,
            cmap=cmap,
            default_title="Velocity Magnitude [m/s]",
            render_config=render_config,
            scalar_bar_config=scalar_bar_config,
            patches=patches,
            show_internal=show_internal,
        )

    def render_velocity_component(
        self,
        component: Literal["x", "y", "z"] = "x",
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "RdBu_r",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        patches: Optional[List[str]] = None,
    ) -> str:
        """
        Render a single velocity component (Ux, Uy, or Uz) on boundary surfaces.

        Parameters
        ----------
        component : 'x', 'y', or 'z'
        clim, cmap, render_config, scalar_bar_config, patches : see render_temperature

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        field_name = f"U{component}"
        label_map = {"x": "Ux", "y": "Uy", "z": "Uz"}
        return self._render_scalar_field(
            field_name=field_name,
            clim=clim,
            cmap=cmap,
            default_title=f"Velocity {label_map[component]} [m/s]",
            render_config=render_config,
            scalar_bar_config=scalar_bar_config,
            patches=patches,
            show_internal=False,
        )

    def render_pressure(
        self,
        field: Literal["p", "p_rgh"] = "p_rgh",
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "viridis",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        patches: Optional[List[str]] = None,
    ) -> str:
        """
        Render pressure field on boundary surfaces.

        Parameters
        ----------
        field : 'p' or 'p_rgh'
        clim, cmap, render_config, scalar_bar_config, patches : see render_temperature

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        title = "Pressure p [Pa]" if field == "p" else "Pressure p_rgh [Pa]"
        return self._render_scalar_field(
            field_name=field,
            clim=clim,
            cmap=cmap,
            default_title=title,
            render_config=render_config,
            scalar_bar_config=scalar_bar_config,
            patches=patches,
            show_internal=False,
        )

    def _render_scalar_field(
        self,
        field_name: str,
        clim: Optional[Tuple[float, float]],
        cmap: str,
        default_title: str,
        render_config: Optional[RenderConfig],
        scalar_bar_config: Optional[ScalarBarConfig],
        patches: Optional[List[str]],
        show_internal: bool = False,
    ) -> str:
        """Internal: render any scalar field on boundary patches."""
        rc = render_config or RenderConfig()
        sbc = scalar_bar_config or ScalarBarConfig(title=default_title)
        if not sbc.title:
            sbc.title = default_title

        plotter = self._make_plotter(rc)

        scalar_bar_args = {
            "title": sbc.title,
            "n_labels": sbc.n_labels,
            "fmt": sbc.fmt,
            "vertical": sbc.vertical,
            "width": sbc.width,
            "height": sbc.height,
            "position_x": sbc.position_x,
            "position_y": sbc.position_y,
            "title_font_size": sbc.title_font_size,
            "label_font_size": sbc.label_font_size,
            "color": sbc.color,
            "bold": sbc.bold,
        }

        # Determine actual clim
        if clim is None:
            try:
                clim = self.get_field_range(field_name, source="boundary")
            except KeyError:
                clim = self.get_field_range(field_name, source="all")

        # Add boundary patches
        if self.boundary is not None:
            added_first = False
            for i in range(self.boundary.n_blocks):
                name = self.boundary.keys()[i]
                patch = self.boundary[i]
                if patch is None or patch.n_points == 0:
                    continue
                if patches is not None and name not in patches:
                    continue
                if field_name not in patch.point_data:
                    continue

                plotter.add_mesh(
                    patch,
                    scalars=field_name,
                    cmap=cmap,
                    clim=clim,
                    show_edges=rc.show_edges,
                    opacity=rc.opacity,
                    lighting=rc.lighting,
                    smooth_shading=rc.smooth_shading,
                    scalar_bar_args=scalar_bar_args if not added_first else None,
                    show_scalar_bar=not added_first,
                    name=f"patch_{name}",
                )
                added_first = True

        # Optionally show internal mesh as translucent
        if show_internal and field_name in self.internal.point_data:
            # Extract surface of internal mesh for visualization
            surface = self.internal.extract_surface()
            plotter.add_mesh(
                surface,
                scalars=field_name,
                cmap=cmap,
                clim=clim,
                opacity=0.15,
                show_scalar_bar=False,
                name="internal_surface",
            )

        return self._render_to_base64(plotter, rc)

    def render_slice(
        self,
        field_name: str = "T",
        normal: Union[str, Tuple[float, float, float]] = "z",
        origin: Optional[Tuple[float, float, float]] = None,
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "coolwarm",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        show_boundary_wireframe: bool = True,
    ) -> str:
        """
        Render a planar slice through the internal volume mesh.

        This is one of the most useful views for data center CFD: a horizontal
        slice at a given height showing the temperature distribution.

        Parameters
        ----------
        field_name : str
            Field to color the slice by (e.g. 'T', 'Umag', 'Ux').
        normal : str or tuple
            Slice plane normal. Use 'x', 'y', 'z' for axis-aligned,
            or provide a (nx, ny, nz) tuple.
        origin : tuple, optional
            Point on the slice plane. If None, uses mesh center.
        clim : tuple, optional
        cmap : str
        render_config : RenderConfig, optional
        scalar_bar_config : ScalarBarConfig, optional
        show_boundary_wireframe : bool
            If True, overlay boundary patch wireframes for context.

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        rc = render_config or RenderConfig()
        title = f"{field_name} (slice)"
        sbc = scalar_bar_config or ScalarBarConfig(title=title)
        if not sbc.title:
            sbc.title = title

        plotter = self._make_plotter(rc)

        # Map string normals to vectors. Y-up convention: fallback default
        # for an unknown string is (0, 1, 0) (horizontal slice at fixed Y).
        normal_map = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}
        if isinstance(normal, str):
            normal = normal_map.get(normal.lower(), (0, 1, 0))

        if origin is None:
            origin = self.internal.center

        # Create the slice
        sliced = self.internal.slice(normal=normal, origin=origin)

        if clim is None:
            if field_name in sliced.point_data:
                data = sliced.point_data[field_name]
                clim = (float(data.min()), float(data.max()))
            else:
                clim = self.get_field_range(field_name, source="internal")

        scalar_bar_args = {
            "title": sbc.title,
            "n_labels": sbc.n_labels,
            "fmt": sbc.fmt,
            "vertical": sbc.vertical,
            "width": sbc.width,
            "height": sbc.height,
            "position_x": sbc.position_x,
            "position_y": sbc.position_y,
            "title_font_size": sbc.title_font_size,
            "label_font_size": sbc.label_font_size,
            "color": sbc.color,
            "bold": sbc.bold,
        }

        plotter.add_mesh(
            sliced,
            scalars=field_name,
            cmap=cmap,
            clim=clim,
            show_edges=False,
            lighting=rc.lighting,
            scalar_bar_args=scalar_bar_args,
            name="slice",
        )

        # Overlay boundary wireframe for spatial context
        if show_boundary_wireframe and self.boundary is not None:
            combined = self.boundary.combine()
            plotter.add_mesh(
                combined,
                style="wireframe",
                color="gray",
                opacity=0.15,
                show_scalar_bar=False,
                name="boundary_wireframe",
            )

        return self._render_to_base64(plotter, rc)

    def render_streamlines(
        self,
        streamline_config: Optional[StreamlineConfig] = None,
        color_field: str = "speed",
        clim: Optional[Tuple[float, float]] = None,
        cmap: str = "plasma",
        render_config: Optional[RenderConfig] = None,
        scalar_bar_config: Optional[ScalarBarConfig] = None,
        show_boundary_wireframe: bool = True,
    ) -> str:
        """
        Render velocity streamlines through the internal mesh.

        Parameters
        ----------
        streamline_config : StreamlineConfig, optional
            Streamline computation parameters. If None, seeds from mesh center.
        color_field : str
            Field to color streamlines by. Special values:
            - 'speed': color by velocity magnitude along streamline
            - 'T': color by temperature
            - any other field name present in the data
        clim : tuple, optional
        cmap : str
        render_config : RenderConfig, optional
        scalar_bar_config : ScalarBarConfig, optional
        show_boundary_wireframe : bool

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        pv = _get_pyvista()
        rc = render_config or RenderConfig()
        sc = streamline_config or StreamlineConfig()
        sbc = scalar_bar_config or ScalarBarConfig(title="Streamlines")
        if not sbc.title:
            sbc.title = "Streamlines"

        plotter = self._make_plotter(rc)

        # Ensure active vectors are set
        self.internal.set_active_vectors("U")

        # Compute streamlines
        if sc.seed_points is not None:
            # Use explicit seed points
            seed = pv.PolyData(np.asarray(sc.seed_points))
            streams = self.internal.streamlines_from_source(
                source=seed,
                vectors="U",
                integration_direction=sc.integration_direction,
                max_steps=sc.max_steps,
                initial_step_length=sc.initial_step_length,
                max_step_length=sc.max_step_length,
                terminal_speed=sc.terminal_speed,
            )
        else:
            # Use spherical seed region
            center = sc.seed_center or tuple(self.internal.center)
            streams = self.internal.streamlines(
                vectors="U",
                source_center=center,
                source_radius=sc.seed_radius,
                n_points=sc.n_seed_points,
                max_steps=sc.max_steps,
                initial_step_length=sc.initial_step_length,
            )

        if streams.n_points == 0:
            logger.warning("No streamlines were generated. Check seed point locations.")
            plotter.add_text(
                "No streamlines generated",
                position="upper_left",
                font_size=18,
                color="red",
            )
            return self._render_to_base64(plotter, rc)

        # Determine color scalar
        if color_field == "speed":
            if "U" in streams.point_data:
                U = streams.point_data["U"]
                streams.point_data["speed"] = np.linalg.norm(U, axis=1)
                scalars = "speed"
                sbc.title = "Speed [m/s]"
            else:
                scalars = None
        elif color_field in streams.point_data:
            scalars = color_field
        else:
            scalars = None

        # Convert streamlines to tubes for better visualization
        if sc.tube_radius > 0 and streams.n_points > 1:
            try:
                tubes = streams.tube(radius=sc.tube_radius)
                render_mesh = tubes
            except Exception:
                render_mesh = streams
        else:
            render_mesh = streams

        scalar_bar_args = {
            "title": sbc.title,
            "n_labels": sbc.n_labels,
            "fmt": sbc.fmt,
            "vertical": sbc.vertical,
            "width": sbc.width,
            "height": sbc.height,
            "position_x": sbc.position_x,
            "position_y": sbc.position_y,
            "title_font_size": sbc.title_font_size,
            "label_font_size": sbc.label_font_size,
            "color": sbc.color,
            "bold": sbc.bold,
        }

        plotter.add_mesh(
            render_mesh,
            scalars=scalars,
            cmap=cmap,
            clim=clim,
            show_edges=False,
            lighting=True,
            scalar_bar_args=scalar_bar_args if scalars else None,
            show_scalar_bar=scalars is not None,
            name="streamlines",
        )

        # Overlay boundary wireframe
        if show_boundary_wireframe and self.boundary is not None:
            combined = self.boundary.combine()
            plotter.add_mesh(
                combined,
                style="wireframe",
                color="gray",
                opacity=0.1,
                show_scalar_bar=False,
                name="boundary_wireframe",
            )

        return self._render_to_base64(plotter, rc)

    def render_combined(
        self,
        show_temperature: bool = True,
        show_streamlines: bool = True,
        t_clim: Optional[Tuple[float, float]] = None,
        t_cmap: str = "coolwarm",
        streamline_config: Optional[StreamlineConfig] = None,
        stream_cmap: str = "plasma",
        render_config: Optional[RenderConfig] = None,
    ) -> str:
        """
        Render a combined view with temperature on surfaces and velocity streamlines.

        Parameters
        ----------
        show_temperature : bool
        show_streamlines : bool
        t_clim : tuple, optional
        t_cmap : str
        streamline_config : StreamlineConfig, optional
        stream_cmap : str
        render_config : RenderConfig, optional

        Returns
        -------
        str
            Base64-encoded PNG image.
        """
        pv = _get_pyvista()
        rc = render_config or RenderConfig()
        plotter = self._make_plotter(rc)

        # Temperature on boundary surfaces
        if show_temperature and self.boundary is not None:
            if t_clim is None:
                try:
                    t_clim = self.get_field_range("T", source="boundary")
                except KeyError:
                    t_clim = None

            added_bar = False
            for i in range(self.boundary.n_blocks):
                patch = self.boundary[i]
                if patch is None or patch.n_points == 0:
                    continue
                if "T" not in patch.point_data:
                    continue

                sbar = None
                if not added_bar:
                    sbar = {
                        "title": "Temperature [K]",
                        "n_labels": 5,
                        "fmt": "%.1f",
                        "vertical": True,
                        "position_x": 0.05,
                        "position_y": 0.25,
                        "color": "black",
                    }
                    added_bar = True

                plotter.add_mesh(
                    patch,
                    scalars="T",
                    cmap=t_cmap,
                    clim=t_clim,
                    opacity=0.6,
                    show_scalar_bar=sbar is not None,
                    scalar_bar_args=sbar,
                    smooth_shading=True,
                )

        # Streamlines
        if show_streamlines:
            sc = streamline_config or StreamlineConfig()
            self.internal.set_active_vectors("U")

            center = sc.seed_center or tuple(self.internal.center)
            streams = self.internal.streamlines(
                vectors="U",
                source_center=center,
                source_radius=sc.seed_radius,
                n_points=sc.n_seed_points,
                max_steps=sc.max_steps,
            )

            if streams.n_points > 0:
                if "U" in streams.point_data:
                    U = streams.point_data["U"]
                    streams.point_data["speed"] = np.linalg.norm(U, axis=1)

                try:
                    tubes = streams.tube(radius=sc.tube_radius)
                except Exception:
                    tubes = streams

                plotter.add_mesh(
                    tubes,
                    scalars="speed" if "speed" in tubes.point_data else None,
                    cmap=stream_cmap,
                    show_scalar_bar=True,
                    scalar_bar_args={
                        "title": "Speed [m/s]",
                        "n_labels": 5,
                        "fmt": "%.1f",
                        "vertical": True,
                        "position_x": 0.87,
                        "position_y": 0.25,
                        "color": "black",
                    },
                    name="streamlines",
                )

        return self._render_to_base64(plotter, rc)


# ---------------------------------------------------------------------------
# Convenience functions (for quick use in API handlers)
# ---------------------------------------------------------------------------


def render_vtm_temperature(
    vtm_path: str,
    clim: Optional[Tuple[float, float]] = None,
    cmap: str = "coolwarm",
    window_size: Tuple[int, int] = (1920, 1080),
    camera_position: str = "iso",
) -> str:
    """
    One-shot: load VTM, render temperature, return base64 PNG.

    Parameters
    ----------
    vtm_path : str
    clim : tuple, optional
    cmap : str
    window_size : tuple
    camera_position : str

    Returns
    -------
    str
        Base64-encoded PNG.
    """
    pp = CFDPostProcessor.from_vtm(vtm_path)
    return pp.render_temperature(
        clim=clim,
        cmap=cmap,
        render_config=RenderConfig(
            window_size=window_size,
            camera_position=camera_position,
        ),
    )


def render_vtm_velocity(
    vtm_path: str,
    clim: Optional[Tuple[float, float]] = None,
    cmap: str = "jet",
    window_size: Tuple[int, int] = (1920, 1080),
    camera_position: str = "iso",
) -> str:
    """One-shot: load VTM, render velocity magnitude, return base64 PNG."""
    pp = CFDPostProcessor.from_vtm(vtm_path)
    return pp.render_velocity_magnitude(
        clim=clim,
        cmap=cmap,
        render_config=RenderConfig(
            window_size=window_size,
            camera_position=camera_position,
        ),
    )


def render_vtm_streamlines(
    vtm_path: str,
    seed_center: Optional[Tuple[float, float, float]] = None,
    seed_radius: float = 0.5,
    n_seed_points: int = 50,
    window_size: Tuple[int, int] = (1920, 1080),
    camera_position: str = "iso",
) -> str:
    """One-shot: load VTM, render streamlines, return base64 PNG."""
    pp = CFDPostProcessor.from_vtm(vtm_path)
    return pp.render_streamlines(
        streamline_config=StreamlineConfig(
            seed_center=seed_center,
            seed_radius=seed_radius,
            n_seed_points=n_seed_points,
        ),
        render_config=RenderConfig(
            window_size=window_size,
            camera_position=camera_position,
        ),
    )


# ---------------------------------------------------------------------------
# PostProcessor — High-level wrapper for server.py integration
# ---------------------------------------------------------------------------

_FOAM_BASHRC = "/usr/lib/openfoam/openfoam2406/etc/bashrc"


class PostProcessor:
    """
    High-level post-processing wrapper for the STLGeneratorForOpenFoam server.

    Manages the full pipeline: foamToVTK conversion, VTM loading, and
    rendering of surface fields / streamlines.  Designed for lazy
    initialisation and caching in server.py (one instance per case).

    Parameters
    ----------
    run_dir : Path
        The solve run directory, e.g.
        ``<case>/log/2026-03-22-05-01-31_V5_CFD_Station``.
        Must contain OpenFOAM time-step directories and ``constant/polyMesh``.
    """

    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir).resolve()
        if not self.run_dir.is_dir():
            raise NotADirectoryError(f"Run directory does not exist: {self.run_dir}")
        self._pp: Optional[CFDPostProcessor] = None  # lazy
        self._vtk_dir: Optional[Path] = None
        self._vtm_path: Optional[Path] = None
        self.nan_warnings: List[str] = []  # fields that had NaN sanitized

    # ------------------------------------------------------------------
    # 1.  foamToVTK conversion
    # ------------------------------------------------------------------

    def convert_to_vtk(self) -> Path:
        """Run ``foamToVTK -latestTime`` on the run directory.

        If a VTK/ directory already exists and contains .vtm files, the
        conversion is skipped and the existing directory is reused.

        Returns
        -------
        Path
            Path to the ``VTK/`` directory created inside ``run_dir``.

        Raises
        ------
        RuntimeError
            If foamToVTK fails.
        """
        vtk_dir = self.run_dir / "VTK"

        # Skip if VTK/ already exists with valid .vtm files that are not stale
        if vtk_dir.is_dir():
            existing_vtm = sorted(vtk_dir.glob("*.vtm"))
            if existing_vtm:
                vtm_mtime = existing_vtm[-1].stat().st_mtime
                # Check if any time-step dir is newer than VTK output
                stale = False
                for item in self.run_dir.iterdir():
                    if item.is_dir():
                        try:
                            t = float(item.name)
                            if t > 0 and item.stat().st_mtime > vtm_mtime:
                                stale = True
                                break
                        except ValueError:
                            continue
                if not stale:
                    self._vtk_dir = vtk_dir
                    self._vtm_path = existing_vtm[-1]
                    logger.info("foamToVTK skipped: reusing existing %s", self._vtm_path)
                    return vtk_dir
                else:
                    logger.info("foamToVTK: VTK output is stale, re-converting")

        # Find the latest time-step directory (excluding 0)
        time_dirs: List[Tuple[float, Path]] = []
        for item in self.run_dir.iterdir():
            if item.is_dir():
                try:
                    t = float(item.name)
                    if t > 0:
                        time_dirs.append((t, item))
                except ValueError:
                    continue

        if not time_dirs:
            raise RuntimeError(
                f"No time-step directories found in {self.run_dir}. "
                "Has the solver completed?"
            )

        time_dirs.sort(key=lambda x: x[0])
        latest_time = time_dirs[-1][0]
        latest_str = str(int(latest_time)) if latest_time == int(latest_time) else str(latest_time)
        logger.info("foamToVTK: converting time %s in %s", latest_str, self.run_dir)

        # Defensive: sanitize NaN in field files before foamToVTK
        # (reconstructPar can produce NaN on cyclic/porousBaffle patches
        #  when the solver didn't converge)
        self._sanitize_nan_fields(time_dirs[-1][1])

        cmd = f'source "{_FOAM_BASHRC}" && foamToVTK -time {latest_str}'
        result = subprocess.run(
            cmd,
            shell=True,
            executable="/bin/bash",
            cwd=str(self.run_dir),
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            logger.error("foamToVTK failed:\n%s", result.stderr)
            raise RuntimeError(f"foamToVTK failed (exit {result.returncode}): {result.stderr[:500]}")

        if not vtk_dir.is_dir():
            raise RuntimeError(
                "foamToVTK completed but VTK/ directory was not created. "
                f"stdout: {result.stdout[:500]}"
            )

        self._vtk_dir = vtk_dir

        # Find the .vtm file
        vtm_files = sorted(vtk_dir.glob("*.vtm"))
        if not vtm_files:
            raise RuntimeError(f"No .vtm files found in {vtk_dir}")
        self._vtm_path = vtm_files[-1]  # take the latest

        logger.info("foamToVTK complete: %s", self._vtm_path)
        return vtk_dir

    def _sanitize_nan_fields(self, time_dir: Path) -> None:
        """Scan OpenFOAM field files for NaN and replace with 0.

        reconstructPar can produce NaN on cyclic/porousBaffle boundary
        patches when the solver did not converge.  This causes foamToVTK
        to fail.  We replace NaN tokens with 0 and record which fields
        were affected so the user can be warned.
        """
        import re
        nan_re = re.compile(r'\bnan\b', re.IGNORECASE)

        for fpath in sorted(time_dir.iterdir()):
            if not fpath.is_file():
                continue
            # Skip non-field files (e.g. uniform/)
            try:
                raw = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if nan_re.search(raw):
                field_name = fpath.name
                cleaned = nan_re.sub("0", raw)
                fpath.write_text(cleaned, encoding="utf-8")
                self.nan_warnings.append(field_name)
                logger.warning(
                    "NaN sanitized in field '%s' (%s) — "
                    "solver likely did not converge for this variable",
                    field_name, fpath,
                )

    # ------------------------------------------------------------------
    # 2.  Load VTM data
    # ------------------------------------------------------------------

    def load_vtk(self) -> dict:
        """Load the VTM file and return metadata about patches and fields.

        If ``convert_to_vtk()`` has not been called yet, it is called
        automatically.

        Returns
        -------
        dict
            ``{patches: [...], fields: [...], runDir: str}``
        """
        if self._vtm_path is None:
            # Check for existing VTK/ directory first
            vtk_dir = self.run_dir / "VTK"
            if vtk_dir.is_dir():
                vtm_files = sorted(vtk_dir.glob("*.vtm"))
                if vtm_files:
                    self._vtk_dir = vtk_dir
                    self._vtm_path = vtm_files[-1]

        if self._vtm_path is None:
            self.convert_to_vtk()

        self._pp = CFDPostProcessor.from_vtm(self._vtm_path)

        # Gather available scalar field names from the first non-empty patch
        fields: List[str] = []
        if self._pp.internal is not None:
            fields = list(self._pp.internal.point_data.keys())
        # Deduplicate while preserving order
        seen = set()
        unique_fields = []
        for f in fields:
            if f not in seen:
                seen.add(f)
                unique_fields.append(f)

        return {
            "patches": self._pp.patch_names,
            "fields": unique_fields,
            "runDir": str(self.run_dir),
            "vtmPath": str(self._vtm_path),
        }

    # ------------------------------------------------------------------
    # 3.  Surface field rendering
    # ------------------------------------------------------------------

    def render_surface_field(
        self,
        field_name: str,
        component: Optional[str] = None,
        clim: Optional[Tuple[float, float]] = None,
        visible_patches: Optional[List[str]] = None,
    ) -> str:
        """Render a surface field and return a base64-encoded PNG image.

        Parameters
        ----------
        field_name : str
            ``'T'`` for temperature, ``'U'`` for velocity.
        component : str or None
            For velocity: ``'x'``, ``'y'``, ``'z'`` for components,
            ``None`` or ``'mag'`` for magnitude.  Ignored for scalars.
        clim : tuple of (min, max) or None
            Colour-bar range.  If None, auto-range is used (data range
            contracted by 10% at each end).
        visible_patches : list of str or None
            Patch names to show.  None means all patches.

        Returns
        -------
        str
            Base64-encoded PNG (no ``data:image/png;base64,`` prefix).
        """
        self._ensure_loaded()
        pp = self._pp

        # Resolve field name for velocity components
        if field_name == "U":
            if component in ("x", "y", "z"):
                resolved = f"U{component}"
            else:
                resolved = "Umag"
        else:
            resolved = field_name

        # Auto-range: contract by 10% at each end
        actual_clim = clim
        if actual_clim is None:
            try:
                fmin, fmax = pp.get_field_range(resolved, source="boundary")
            except KeyError:
                fmin, fmax = pp.get_field_range(resolved, source="all")
            data_range = fmax - fmin
            if data_range > 0:
                actual_clim = (fmin + 0.1 * data_range, fmax - 0.1 * data_range)
            else:
                actual_clim = (fmin, fmax)

        # Choose colormap
        if field_name == "T":
            cmap = "coolwarm"
        elif field_name == "U":
            cmap = "jet" if component is None or component == "mag" else "RdBu_r"
        else:
            cmap = "viridis"

        # Choose title
        title_map = {
            "T": "Temperature [K]",
            "Umag": "Velocity Magnitude [m/s]",
            "Ux": "Velocity Ux [m/s]",
            "Uy": "Velocity Uy [m/s]",
            "Uz": "Velocity Uz [m/s]",
        }
        title = title_map.get(resolved, resolved)

        rc = RenderConfig(camera_position="iso")
        sbc = ScalarBarConfig(title=title)

        b64 = pp._render_scalar_field(
            field_name=resolved,
            clim=actual_clim,
            cmap=cmap,
            default_title=title,
            render_config=rc,
            scalar_bar_config=sbc,
            patches=visible_patches,
            show_internal=False,
        )
        return b64

    # ------------------------------------------------------------------
    # 4.  Streamline rendering
    # ------------------------------------------------------------------

    def render_streamlines(
        self,
        visible_patches: Optional[List[str]] = None,
        n_y_layers: int = 3,
        n_xz_points: int = 9,
    ) -> str:
        """Render velocity streamlines and return a base64-encoded PNG.

        Seed points are auto-generated within the mesh bounding box under
        Y-up convention (Y is vertical):

        * ``n_y_layers`` horizontal layers evenly spaced within the Y range
          (at y_min + h*k/(n_y_layers+1) for k=1..n_y_layers).
        * ``n_xz_points`` seed points per layer in a sqrt(n)xsqrt(n) XZ grid.
          Default 9 gives a 3x3 grid on the horizontal floor plan.

        Parameters
        ----------
        visible_patches : list of str or None
            Patches to show as wireframe context.  None means all.
        n_y_layers : int
            Number of Y (vertical) layers (default 3).
        n_xz_points : int
            Number of XZ seed points per layer (default 9, i.e. 3x3).

        Returns
        -------
        str
            Base64-encoded PNG.
        """
        self._ensure_loaded()
        pp = self._pp
        pv = _get_pyvista()

        # Compute mesh bounds for seed-point generation
        bounds = pp.internal.bounds  # (xmin, xmax, ymin, ymax, zmin, zmax)
        xmin, xmax, ymin, ymax, zmin, zmax = bounds

        # Generate seed points (Y-up: Y is vertical, XZ is floor-plan)
        h = ymax - ymin
        n_side = max(1, int(round(n_xz_points ** 0.5)))
        seed_points = []

        for k in range(1, n_y_layers + 1):
            y = ymin + h * k / (n_y_layers + 1)
            for ix in range(n_side):
                for iz in range(n_side):
                    x = xmin + (xmax - xmin) * (ix + 0.5) / n_side
                    z = zmin + (zmax - zmin) * (iz + 0.5) / n_side
                    seed_points.append([x, y, z])

        seed_arr = np.array(seed_points, dtype=np.float64)
        logger.info(
            "Streamline seeds: %d points (%d Y-layers x %d XZ-grid)",
            len(seed_arr), n_y_layers, n_side * n_side,
        )

        sc = StreamlineConfig(
            seed_points=seed_arr,
            max_steps=2000,
            initial_step_length=0.5,
            max_step_length=1.0,
            tube_radius=0.005,
        )
        rc = RenderConfig(camera_position="iso")

        # Filter boundary patches for wireframe overlay
        patches_for_wireframe = visible_patches

        b64 = pp.render_streamlines(
            streamline_config=sc,
            color_field="speed",
            cmap="plasma",
            render_config=rc,
            show_boundary_wireframe=True,
        )
        return b64

    # ------------------------------------------------------------------
    # 5.  Query helpers
    # ------------------------------------------------------------------

    def get_patch_names(self) -> List[str]:
        """Return list of all patch names from the loaded VTM data."""
        self._ensure_loaded()
        return list(self._pp.patch_names)

    def get_field_range(
        self,
        field_name: str,
        component: Optional[str] = None,
    ) -> Tuple[float, float]:
        """Return (min, max) of a field across all patches.

        Parameters
        ----------
        field_name : str
            ``'T'`` or ``'U'``.
        component : str or None
            For velocity: ``'x'``, ``'y'``, ``'z'``, ``'mag'``, or ``None``
            (defaults to magnitude).

        Returns
        -------
        tuple of (float, float)
        """
        self._ensure_loaded()
        if field_name == "U":
            if component in ("x", "y", "z"):
                resolved = f"U{component}"
            else:
                resolved = "Umag"
        else:
            resolved = field_name
        return self._pp.get_field_range(resolved, source="all")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        """Make sure VTM data has been loaded, auto-loading if necessary."""
        if self._pp is None:
            self.load_vtk()

    # ------------------------------------------------------------------
    # Class method: find latest run directory from case dir
    # ------------------------------------------------------------------

    @classmethod
    def find_latest_run_dir(cls, case_dir: Path) -> Optional[Path]:
        """Find the latest solve run directory under ``<case_dir>/log/``.

        The run directory is the one with the latest timestamp prefix
        (format: ``YYYY-MM-DD-HH-MM-SS_CaseName``).

        Returns None if no run directories are found.
        """
        log_dir = Path(case_dir) / "log"
        if not log_dir.is_dir():
            return None

        run_dirs = []
        for d in log_dir.iterdir():
            if d.is_dir():
                # Check it looks like a run directory (has constant/polyMesh)
                if (d / "constant" / "polyMesh").is_dir():
                    run_dirs.append(d)

        if not run_dirs:
            return None

        # Sort by name (timestamp prefix gives chronological order)
        run_dirs.sort(key=lambda p: p.name)
        return run_dirs[-1]


# ---------------------------------------------------------------------------
# CLI entry point (for testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(
        description="Render CFD post-processing images from foamToVTK output."
    )
    parser.add_argument("vtm_path", help="Path to .vtm file")
    parser.add_argument(
        "--field",
        choices=["T", "Umag", "Ux", "Uy", "Uz", "p", "p_rgh", "streamlines", "combined", "slice"],
        default="T",
        help="Field to render (default: T)",
    )
    parser.add_argument("--clim", type=float, nargs=2, metavar=("MIN", "MAX"), help="Color range")
    parser.add_argument("--cmap", default=None, help="Matplotlib colormap")
    parser.add_argument("--camera", default="iso", help="Camera position: iso, xy, xz, yz")
    parser.add_argument("--width", type=int, default=1920, help="Image width")
    parser.add_argument("--height", type=int, default=1080, help="Image height")
    parser.add_argument("--output", "-o", help="Output PNG file path (default: stdout as base64)")
    parser.add_argument(
        "--slice-normal", default="z", help="Slice normal direction (for --field slice)"
    )
    parser.add_argument(
        "--slice-origin", type=float, nargs=3, metavar=("X", "Y", "Z"),
        help="Slice origin point (for --field slice)",
    )
    parser.add_argument(
        "--seed-center", type=float, nargs=3, metavar=("X", "Y", "Z"),
        help="Streamline seed center (for --field streamlines)",
    )
    parser.add_argument(
        "--seed-radius", type=float, default=0.5,
        help="Streamline seed radius (for --field streamlines)",
    )
    parser.add_argument("--list-patches", action="store_true", help="List patches and exit")

    args = parser.parse_args()

    pp = CFDPostProcessor.from_vtm(args.vtm_path)

    if args.list_patches:
        patches = pp.list_patches()
        print(json.dumps(patches, indent=2))
        sys.exit(0)

    rc = RenderConfig(
        window_size=(args.width, args.height),
        camera_position=args.camera,
    )
    clim = tuple(args.clim) if args.clim else None

    if args.field == "T":
        b64 = pp.render_temperature(clim=clim, cmap=args.cmap or "coolwarm", render_config=rc)
    elif args.field == "Umag":
        b64 = pp.render_velocity_magnitude(clim=clim, cmap=args.cmap or "jet", render_config=rc)
    elif args.field in ("Ux", "Uy", "Uz"):
        comp = args.field[1].lower()
        b64 = pp.render_velocity_component(
            component=comp, clim=clim, cmap=args.cmap or "RdBu_r", render_config=rc
        )
    elif args.field in ("p", "p_rgh"):
        b64 = pp.render_pressure(
            field=args.field, clim=clim, cmap=args.cmap or "viridis", render_config=rc
        )
    elif args.field == "slice":
        b64 = pp.render_slice(
            field_name="T",
            normal=args.slice_normal,
            origin=tuple(args.slice_origin) if args.slice_origin else None,
            clim=clim,
            cmap=args.cmap or "coolwarm",
            render_config=rc,
        )
    elif args.field == "streamlines":
        sc = StreamlineConfig(
            seed_center=tuple(args.seed_center) if args.seed_center else None,
            seed_radius=args.seed_radius,
        )
        b64 = pp.render_streamlines(streamline_config=sc, render_config=rc)
    elif args.field == "combined":
        b64 = pp.render_combined(render_config=rc, t_clim=clim)
    else:
        print(f"Unknown field: {args.field}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        png_bytes = base64.b64decode(b64)
        with open(args.output, "wb") as f:
            f.write(png_bytes)
        logger.info("Saved %d bytes to %s", len(png_bytes), args.output)
    else:
        # Output base64 to stdout (for piping to an API)
        print(b64)


# ---------------------------------------------------------------------------
#  CachedPostProcessor — in-memory singleton for binary post-processing API
# ---------------------------------------------------------------------------


class CachedPostProcessor:
    """Singleton that holds VTM data in memory with mesh/field separation.

    Lifecycle:
      1.  init(run_dir) — runs foamToVTK if needed, loads VTM into PyVista
      2.  get_mesh()    — triangulates boundary patches, caches geometry
      3.  get_field(name) — extracts scalar array, caches per field
      4.  invalidate()  — clears caches (called when switching run dir)
    """

    _instance: Optional["CachedPostProcessor"] = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.run_dir: Optional[Path] = None
        self.multi_block: Optional[Any] = None
        self._internal: Optional[Any] = None
        self._boundary: Optional[Any] = None
        self._tri_cache: Dict[str, dict] = {}
        self._mesh_binary: Optional[bytes] = None
        self._mesh_meta: Optional[List[dict]] = None
        self._field_cache: Dict[str, bytes] = {}
        self._field_info: Dict[str, dict] = {}
        self._zones_cache: Optional[bytes] = None
        self._kdtree_cache: Optional[Any] = None  # cached cKDTree for volume resampling
        self._zones_meta: Optional[List[dict]] = None
        self._available_fields: List[dict] = []
        self._vtm_path: Optional[Path] = None

    @classmethod
    def get_instance(cls) -> "CachedPostProcessor":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _parallel_read_vtm(self, vtm_path: Path) -> Tuple[Optional[Any], Optional[Any]]:
        """Read VTM sub-files in parallel using ThreadPoolExecutor.

        Instead of one slow pv.read(vtm), read internal.vtu and individual
        boundary .vtp files concurrently (8 threads). VTK I/O releases the
        GIL so threads give real parallelism.

        Also pre-triangulates patches while they're hot in memory and stores
        results in ``self._tri_cache`` so ``get_mesh()`` can skip VTK access.

        Returns:
            (internal_mesh, boundary_multiblock) or falls back to serial read.
        """
        pv = _get_pyvista()
        vtm_dir = vtm_path.parent
        ts_dir_name = vtm_path.stem  # e.g. "2026-03-22-07-03-03_GTC_1000"
        ts_dir = vtm_dir / ts_dir_name
        internal_vtu = ts_dir / "internal.vtu"
        boundary_dir = ts_dir / "boundary"

        # Fallback: if sub-files don't exist, use serial read
        if not internal_vtu.exists() or not boundary_dir.exists():
            logger.info("[PP] Parallel read unavailable, falling back to serial pv.read")
            mb = pv.read(str(vtm_path))
            internal, boundary = None, None
            for i in range(mb.n_blocks):
                key = mb.keys()[i]
                block = mb[i]
                if block is None:
                    continue
                if key in ("internal", "internalMesh"):
                    internal = block
                elif key == "boundary":
                    boundary = block
            return internal, boundary

        patch_files = sorted(boundary_dir.glob("*.vtp"))

        # Read all sub-files in parallel
        with ThreadPoolExecutor(max_workers=8) as pool:
            fut_internal = pool.submit(pv.read, str(internal_vtu))
            fut_patches = [(pf.stem, pool.submit(pv.read, str(pf))) for pf in patch_files]

            internal = fut_internal.result()
            patch_results = [(name, fut.result()) for name, fut in fut_patches]

        # Assemble boundary MultiBlock (preserving patch names for downstream)
        boundary = pv.MultiBlock()
        for name, mesh in patch_results:
            boundary.append(mesh, name)

        # Pre-triangulate while patches are hot in memory (avoids VTK lazy-load
        # penalty when get_mesh() accesses them later via self._boundary)
        for name, mesh in patch_results:
            if mesh is None or mesh.n_points == 0:
                continue
            points = np.ascontiguousarray(mesh.points, dtype=np.float32)
            faces = mesh.faces
            tris: list = []
            idx = 0
            while idx < len(faces):
                n = faces[idx]
                verts = faces[idx + 1 : idx + 1 + n]
                if n == 3:
                    tris.append(verts)
                elif n == 4:
                    tris.append([verts[0], verts[1], verts[2]])
                    tris.append([verts[0], verts[2], verts[3]])
                else:
                    for j in range(1, n - 1):
                        tris.append([verts[0], verts[j], verts[j + 1]])
                idx += 1 + n
            indices = np.array(tris, dtype=np.uint32) if tris else np.empty((0, 3), dtype=np.uint32)
            self._tri_cache[name] = {"points": points, "indices": indices, "tri": mesh}

        return internal, boundary

    def init(self, run_dir: Path, force: bool = False, timeout: float = 3600.0) -> dict:
        """Load VTM for the given run directory. Skip if already loaded.

        Args:
            timeout: Maximum seconds for the entire init. Steps that complete
                     before the deadline are kept; the first step to exceed
                     it triggers an early return with partial timing data.
        """
        run_dir = Path(run_dir).resolve()
        if not force and self.run_dir == run_dir and self.multi_block is not None:
            return {
                "success": True,
                "message": f"Already loaded: {len(self._mesh_meta or [])} patches",
                "cached": True,
            }
        with self._lock:
            # Double-check after acquiring lock
            if not force and self.run_dir == run_dir and self.multi_block is not None:
                return {
                    "success": True,
                    "message": f"Already loaded: {len(self._mesh_meta or [])} patches",
                    "cached": True,
                }
            self.invalidate()
            self.run_dir = run_dir
            timing: Dict[str, float] = {}
            t0_total = time.perf_counter()
            deadline = t0_total + timeout

            def _tlog(msg: str) -> None:
                print(msg, flush=True)
                logger.info(msg)

            def _check_timeout(step_name: str) -> Optional[dict]:
                """Return an error dict if deadline exceeded, else None."""
                elapsed = time.perf_counter() - t0_total
                if elapsed > timeout:
                    timing["total"] = round(elapsed, 2)
                    timing["timed_out_at"] = step_name
                    _tlog(f"[PP-TIMING] TIMEOUT after {elapsed:.1f}s at step '{step_name}' (limit={timeout}s)")
                    self.invalidate()
                    return {
                        "success": False,
                        "error": f"PP init timed out after {elapsed:.0f}s at step '{step_name}' (limit={timeout:.0f}s). "
                                 f"This case may be too large for the current timeout. "
                                 f"Completed steps: {list(timing.keys())}",
                        "timing": timing,
                        "timed_out": True,
                    }
                return None

            # --- Step 1: foamToVTK conversion ---
            _tlog(f"[PP-TIMING] Starting init for {run_dir.name} (timeout={timeout}s)...")
            t0 = time.perf_counter()
            pp = PostProcessor(run_dir)
            pp.convert_to_vtk()
            nan_warnings = list(pp.nan_warnings)
            timing["foamToVTK"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] foamToVTK: {timing['foamToVTK']}s")
            err = _check_timeout("foamToVTK")
            if err:
                return err

            # --- Step 2: find VTM path (no read — just locate the file) ---
            t0 = time.perf_counter()
            vtk_dir = run_dir / "VTK"
            self._vtm_path = None
            if vtk_dir.is_dir():
                vtm_files = sorted(vtk_dir.glob("*.vtm"))
                if vtm_files:
                    self._vtm_path = vtm_files[-1]
            if self._vtm_path is None and pp._vtm_path is not None:
                self._vtm_path = pp._vtm_path
            timing["find_vtm"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] find_vtm: {timing['find_vtm']}s")

            if self._vtm_path is None:
                return {"success": False, "error": "No VTM file found after foamToVTK conversion"}

            # --- Step 3: parallel read (internal.vtu + boundary patches) ---
            t0 = time.perf_counter()
            self._internal, self._boundary = self._parallel_read_vtm(self._vtm_path)
            self.multi_block = True  # sentinel for "is initialized" checks
            timing["pv_read_parallel"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] pv_read_parallel: {timing['pv_read_parallel']}s")
            if self._internal is not None:
                _tlog(f"[PP-TIMING]   internal: {self._internal.n_cells} cells, {self._internal.n_points} points")
            if self._boundary is not None:
                _tlog(f"[PP-TIMING]   boundary: {self._boundary.n_blocks} patches")
            err = _check_timeout("pv_read_parallel")
            if err:
                return err

            # --- Step 5: velocity preprocessing ---
            t0 = time.perf_counter()
            if self._internal is not None and "U" in self._internal.point_data:
                U = self._internal.point_data["U"]
                self._internal.point_data["Umag"] = np.linalg.norm(U, axis=1)
                self._internal.point_data["Ux"] = U[:, 0]
                self._internal.point_data["Uy"] = U[:, 1]
                self._internal.point_data["Uz"] = U[:, 2]
            timing["velocity_preprocess"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] velocity_preprocess: {timing['velocity_preprocess']}s")
            err = _check_timeout("velocity_preprocess")
            if err:
                return err

            # --- Step 6: field discovery ---
            t0 = time.perf_counter()
            self._available_fields = self._discover_fields()
            timing["field_discovery"] = round(time.perf_counter() - t0, 2)
            _tlog(f"[PP-TIMING] field_discovery: {timing['field_discovery']}s")

            timing["total"] = round(time.perf_counter() - t0_total, 2)
            _tlog(f"[PP-TIMING] TOTAL: {timing['total']}s")

            n_patches = self._boundary.n_blocks if self._boundary else 0
            n_cells = self._internal.n_cells if self._internal else 0
            n_fields = len(self._available_fields)
            result: dict = {
                "success": True,
                "message": f"VTM loaded, {n_patches} patches, {n_fields} fields, {n_cells} cells",
                "cached": False,
                "timing": timing,
                "n_cells": n_cells,
            }
            if nan_warnings:
                result["nan_warnings"] = nan_warnings
            return result

    def _discover_fields(self) -> List[dict]:
        """Scan internal mesh and boundary patches for available fields."""
        fields: List[dict] = []
        seen: set = set()
        source = self._internal if self._internal is not None else None
        if source is None and self._boundary is not None and self._boundary.n_blocks > 0:
            source = self._boundary[0]
        if source is None:
            return fields

        data_sources = []
        if hasattr(source, "point_data"):
            data_sources.append(source.point_data)
        if hasattr(source, "cell_data"):
            data_sources.append(source.cell_data)

        for data_store in data_sources:
            for name in data_store.keys():
                if name in seen:
                    continue
                arr = data_store[name]
                if arr.ndim == 1:
                    fields.append({"name": name, "type": "scalar", "label": name})
                    seen.add(name)
                elif arr.ndim == 2 and arr.shape[1] == 3:
                    fields.append({
                        "name": name, "type": "vector", "label": name,
                        "components": [f"{name}x", f"{name}y", f"{name}z"],
                    })
                    seen.add(name)
                    for suffix in ("mag", "x", "y", "z"):
                        derived = f"{name}{suffix}"
                        if derived not in seen:
                            seen.add(derived)
        return fields

    def get_mesh(self) -> Tuple[bytes, List[dict]]:
        """Return binary geometry + patch metadata. Cached after first call."""
        if self._mesh_binary is not None and self._mesh_meta is not None:
            return self._mesh_binary, self._mesh_meta

        if self._boundary is None:
            return b"", []

        t0_mesh = time.perf_counter()

        # If _tri_cache was pre-populated by _parallel_read_vtm, use it directly
        # (avoids VTK lazy-load penalty from accessing self._boundary blocks)
        if self._tri_cache:
            patches = [{"name": n, "points": c["points"], "indices": c["indices"]}
                       for n, c in self._tri_cache.items()]
        else:
            # Fallback: triangulate from boundary MultiBlock
            patches = []
            for i in range(self._boundary.n_blocks):
                name = self._boundary.keys()[i]
                patch = self._boundary[i]
                if patch is None or patch.n_points == 0:
                    continue
                points = np.ascontiguousarray(patch.points, dtype=np.float32)
                faces = patch.faces
                tris: list = []
                idx = 0
                while idx < len(faces):
                    n = faces[idx]
                    verts = faces[idx + 1 : idx + 1 + n]
                    if n == 3:
                        tris.append(verts)
                    elif n == 4:
                        tris.append([verts[0], verts[1], verts[2]])
                        tris.append([verts[0], verts[2], verts[3]])
                    else:
                        for j in range(1, n - 1):
                            tris.append([verts[0], verts[j], verts[j + 1]])
                    idx += 1 + n
                indices = np.array(tris, dtype=np.uint32) if tris else np.empty((0, 3), dtype=np.uint32)
                self._tri_cache[name] = {"points": points, "indices": indices, "tri": patch}
                patches.append({"name": name, "points": points, "indices": indices})

        dt_tri = time.perf_counter() - t0_mesh

        t0_pack = time.perf_counter()
        self._mesh_binary, self._mesh_meta = self.pack_mesh_binary(patches)
        for m in self._mesh_meta:
            m["defaultVisible"] = (
                not m["name"].endswith("_slave")
                and m["name"] not in ("internalMesh", "internal")
            )
        dt_pack = time.perf_counter() - t0_pack
        dt_total = time.perf_counter() - t0_mesh
        print(f"[PP-TIMING] get_mesh: {dt_total:.2f}s (tri={dt_tri:.2f}s, pack={dt_pack:.2f}s, {len(patches)} patches)", flush=True)
        return self._mesh_binary, self._mesh_meta

    def _resolve_field_name(self, field_name: str) -> Tuple[str, bool, int]:
        """Resolve user-facing field name to internal name + component info.

        Returns (resolved_name, is_component, component_idx).
        """
        resolved = field_name
        is_component = False
        component_idx = -1

        for vec_field in self._available_fields:
            if vec_field["type"] != "vector":
                continue
            base = vec_field["name"]
            if field_name == f"{base}mag":
                # Always use _magnitude convention — boundary patches only
                # have the raw vector ("U"), not precomputed "Umag", so
                # _extract_scalars_from_mesh must compute magnitude on-the-fly.
                resolved = f"{base}_magnitude"
                break
            for ci, suffix in enumerate(("x", "y", "z")):
                if field_name == f"{base}{suffix}":
                    resolved = base
                    is_component = True
                    component_idx = ci
                    break

        return resolved, is_component, component_idx

    @staticmethod
    def _extract_scalars_from_mesh(mesh, resolved: str, is_component: bool, component_idx: int) -> np.ndarray:
        """Extract scalar values from a single mesh for a resolved field name."""
        if resolved in mesh.point_data:
            arr = mesh.point_data[resolved]
        elif resolved in mesh.cell_data:
            mesh_pd = mesh.cell_data_to_point_data()
            arr = mesh_pd.point_data[resolved]
        elif resolved.endswith("_magnitude"):
            base_name = resolved.replace("_magnitude", "")
            if base_name in mesh.point_data:
                arr = np.linalg.norm(mesh.point_data[base_name], axis=1)
            else:
                arr = np.zeros(mesh.n_points, dtype=np.float32)
        else:
            arr = np.zeros(mesh.n_points, dtype=np.float32)

        if is_component and arr.ndim == 2:
            arr = arr[:, component_idx]
        elif arr.ndim == 2:
            arr = np.linalg.norm(arr, axis=1)

        return np.asarray(arr, dtype=np.float32)

    def get_field(self, field_name: str) -> Tuple[bytes, dict]:
        """Return binary scalars + field info. Cached per field."""
        if field_name in self._field_cache:
            return self._field_cache[field_name], self._field_info[field_name]

        if self._boundary is None:
            return b"", {"min": 0, "max": 0, "nComponents": 1, "nPoints": 0}

        if not self._tri_cache:
            self.get_mesh()

        resolved, is_component, component_idx = self._resolve_field_name(field_name)

        all_scalars: List[np.ndarray] = []
        for name in [m["name"] for m in (self._mesh_meta or [])]:
            cached = self._tri_cache.get(name)
            if cached is None:
                continue
            tri = cached["tri"]
            all_scalars.append(self._extract_scalars_from_mesh(tri, resolved, is_component, component_idx))

        if all_scalars:
            combined = np.concatenate(all_scalars)
        else:
            combined = np.array([], dtype=np.float32)

        binary = self.pack_field_binary(combined, n_components=1)
        info = {
            "min": float(combined.min()) if len(combined) > 0 else 0.0,
            "max": float(combined.max()) if len(combined) > 0 else 0.0,
            "nComponents": 1,
            "nPoints": len(combined),
        }
        self._field_cache[field_name] = binary
        self._field_info[field_name] = info
        return binary, info

    def get_available_fields(self) -> List[dict]:
        """Return list of available field descriptors."""
        return self._available_fields

    def get_internal_mesh(self) -> Optional[Any]:
        """Return internal mesh for streamline computation."""
        return self._internal

    def get_slice(
        self,
        normal: Tuple[float, float, float],
        origin: Tuple[float, float, float],
        field_name: str,
    ) -> Tuple[bytes, dict]:
        """Slice the internal mesh with a plane and return binary geometry + field.

        Returns:
            (binary, info) where binary layout is:
              [uint32 headerLen][JSON header][float32 positions][uint32 indices][float32 scalars]
            and info is the parsed header dict.
        """
        internal = self._internal  # snapshot for thread safety
        if internal is None:
            return b"", {"error": "No internal mesh loaded"}

        pv = _get_pyvista()

        sliced = internal.slice(normal=normal, origin=origin)
        if sliced is None or sliced.n_points == 0:
            header = json.dumps({"nPoints": 0, "nTriangles": 0, "scalarRange": [0, 0], "field": field_name}).encode("utf-8")
            return struct.pack("<I", len(header)) + header, {"nPoints": 0, "nTriangles": 0}

        tri = sliced.triangulate()
        points = np.ascontiguousarray(tri.points, dtype=np.float32)

        # Extract triangle indices
        if hasattr(tri, "regular_faces") and tri.regular_faces is not None and len(tri.regular_faces) > 0:
            indices = np.ascontiguousarray(tri.regular_faces, dtype=np.uint32)
        else:
            faces_arr = tri.faces
            tris_list = []
            fidx = 0
            while fidx < len(faces_arr):
                n = int(faces_arr[fidx])
                for k in range(1, n - 1):
                    tris_list.append([int(faces_arr[fidx + 1]), int(faces_arr[fidx + 1 + k]), int(faces_arr[fidx + 2 + k])])
                fidx += 1 + n
            indices = np.array(tris_list, dtype=np.uint32) if tris_list else np.empty((0, 3), dtype=np.uint32)

        resolved, is_component, component_idx = self._resolve_field_name(field_name)
        scalars = self._extract_scalars_from_mesh(tri, resolved, is_component, component_idx).ravel()
        smin = float(scalars.min()) if len(scalars) > 0 else 0.0
        smax = float(scalars.max()) if len(scalars) > 0 else 0.0

        n_tris = len(indices)
        header_dict = {
            "nPoints": int(tri.n_points),
            "nTriangles": n_tris,
            "scalarRange": [smin, smax],
            "field": field_name,
        }
        header = json.dumps(header_dict).encode("utf-8")
        # Pad header to 4-byte alignment so typed arrays after it are aligned.
        # headerLen field stores the padded length; JSON parsing ignores trailing NULs.
        pad_len = (4 - (len(header) % 4)) % 4
        header_padded = header + b"\x00" * pad_len

        payload = (
            struct.pack("<I", len(header_padded))
            + header_padded
            + points.tobytes()
            + indices.tobytes()
            + scalars.tobytes()
        )
        return payload, header_dict

    def get_volume(self, field_name: str, resolution: int = 128) -> Tuple[bytes, dict]:
        """Resample internal mesh onto a regular 3D grid and return binary volume data.

        Uses PyVista's grid.sample() (wraps vtkProbeFilter) for both interpolation
        and inside/outside masking.  This guarantees correct memory layout (VTK
        X-fastest == Three.js DataTexture3D) and accurate cell-boundary-aware masking.

        Returns:
            (binary, info) where binary layout is:
              [uint32 headerLen][JSON header padded to 4-byte][float32 NxNxN scalars]
            and info is the parsed header dict.
        """
        t0_total = time.perf_counter()
        internal = self._internal
        if internal is None:
            return b"", {"error": "No internal mesh loaded"}

        pv = _get_pyvista()
        bounds = internal.bounds  # (xmin,xmax, ymin,ymax, zmin,zmax)
        N = max(16, min(resolution, 512))  # clamp to sane range

        origin = [bounds[0], bounds[2], bounds[4]]
        spacing = [
            (bounds[1] - bounds[0]) / max(N - 1, 1),
            (bounds[3] - bounds[2]) / max(N - 1, 1),
            (bounds[5] - bounds[4]) / max(N - 1, 1),
        ]

        # --- Step 1: resample internal mesh onto regular grid via vtkProbeFilter ---
        t0 = time.perf_counter()
        grid = pv.ImageData(dimensions=(N, N, N), spacing=spacing, origin=origin)
        resampled = grid.sample(internal)
        dt_resample = time.perf_counter() - t0

        # --- Step 2: extract scalars from the resampled grid ---
        t0 = time.perf_counter()
        SENTINEL = -9999.0
        resolved, is_component, component_idx = self._resolve_field_name(field_name)
        scalars = self._extract_scalars_from_mesh(resampled, resolved, is_component, component_idx)
        scalars = scalars.ravel().astype(np.float32)

        # Mark outside-mesh points with sentinel (shader skips them)
        mask = resampled["vtkValidPointMask"]
        if mask is not None:
            scalars[np.asarray(mask) == 0] = SENTINEL

        valid = scalars != SENTINEL
        smin = float(scalars[valid].min()) if valid.any() else 0.0
        smax = float(scalars[valid].max()) if valid.any() else 0.0
        dt_extract = time.perf_counter() - t0

        # --- Step 3: pack ---
        t0 = time.perf_counter()
        header_dict = {
            "dimensions": [N, N, N],
            "origin": origin,
            "spacing": spacing,
            "scalarRange": [smin, smax],
            "sentinel": SENTINEL,
            "field": field_name,
        }
        header_bytes = json.dumps(header_dict).encode("utf-8")
        # Pad to 4-byte alignment
        pad = (4 - len(header_bytes) % 4) % 4
        header_bytes += b"\x00" * pad

        payload = struct.pack("<I", len(header_bytes)) + header_bytes + scalars.tobytes()
        dt_pack = time.perf_counter() - t0

        dt_total = time.perf_counter() - t0_total
        print(f"[PP-TIMING] get_volume: {dt_total:.2f}s "
              f"(resample={dt_resample:.2f}s, extract={dt_extract:.2f}s, "
              f"pack={dt_pack:.2f}s, N={N}, cells={internal.n_cells}, "
              f"field={field_name})", flush=True)

        return payload, header_dict

    def inject_internal_fields(self, fields: Dict[str, np.ndarray]) -> List[dict]:
        """Attach AI or custom fields to the internal mesh and refresh discovery state."""
        if self._internal is None:
            raise RuntimeError("No internal mesh loaded")

        expected_cells = int(self._internal.n_cells)
        for field_name, values in fields.items():
            arr = np.asarray(values, dtype=np.float32)
            if arr.ndim == 1:
                if arr.shape[0] != expected_cells:
                    raise ValueError(
                        f"Field {field_name} expects {expected_cells} cells, got {arr.shape[0]}"
                    )
            elif arr.ndim == 2:
                if arr.shape[0] != expected_cells:
                    raise ValueError(
                        f"Field {field_name} expects {expected_cells} cells, got {arr.shape[0]}"
                    )
            else:
                raise ValueError(f"Unsupported field shape for {field_name}: {arr.shape}")
            self._internal.cell_data[field_name] = arr

        self._field_cache.clear()
        self._field_info.clear()
        self._available_fields = self._discover_fields()
        return self._available_fields

    def invalidate(self) -> None:
        """Clear all caches."""
        self.multi_block = None
        self._internal = None
        self._boundary = None
        self._tri_cache.clear()
        self._mesh_binary = None
        self._mesh_meta = None
        self._field_cache.clear()
        self._field_info.clear()
        self._zones_cache = None
        self._zones_meta = None
        self._kdtree_cache = None
        self._available_fields = []
        self._vtm_path = None

    # ── Static binary packing methods ──

    @staticmethod
    def pack_mesh_binary(patches: List[dict]) -> Tuple[bytes, List[dict]]:
        """Pack triangulated patches into a single binary buffer.

        Layout:
          [Float32 x totalPoints x 3]    -- positions (x,y,z interleaved)
          [Uint32 x totalTriangles x 3]  -- triangle indices (offset per patch)
          [Uint32 x totalEdges x 2]      -- original polygon edge pairs (offset per patch)

        Returns (binary_bytes, patch_metadata_list).
        """
        all_points: List[np.ndarray] = []
        all_indices: List[np.ndarray] = []
        all_edges: List[np.ndarray] = []
        meta: List[dict] = []
        point_offset = 0

        for p in patches:
            pts = np.asarray(p["points"], dtype=np.float32)
            idx = np.asarray(p["indices"], dtype=np.uint32)
            n_points = pts.shape[0]
            n_tris = idx.shape[0]

            all_points.append(pts)
            all_indices.append(idx + point_offset)

            # Original polygon edges (may be absent for legacy callers)
            edges = p.get("edges")
            if edges is not None and len(edges) > 0:
                edge_arr = np.asarray(edges, dtype=np.uint32) + point_offset
                n_edges = edge_arr.shape[0]
            else:
                edge_arr = np.empty((0, 2), dtype=np.uint32)
                n_edges = 0
            all_edges.append(edge_arr)

            meta.append({
                "name": p["name"],
                "nPoints": int(n_points),
                "nTriangles": int(n_tris),
                "nEdges": int(n_edges),
            })
            point_offset += n_points

        if all_points:
            positions = np.concatenate(all_points).ravel()
            indices = np.concatenate(all_indices).ravel()
        else:
            positions = np.array([], dtype=np.float32)
            indices = np.array([], dtype=np.uint32)

        if all_edges and any(len(e) > 0 for e in all_edges):
            edges_flat = np.concatenate(all_edges).ravel()
        else:
            edges_flat = np.array([], dtype=np.uint32)

        buf = positions.tobytes() + indices.tobytes() + edges_flat.tobytes()
        return buf, meta

    @staticmethod
    def pack_field_binary(values: np.ndarray, n_components: int = 1) -> bytes:
        """Pack scalar/vector field into binary buffer.

        Layout:
          Header (16 bytes): [Float32 min] [Float32 max] [Uint32 nComp] [Uint32 nPts]
          Data: [Float32 x nPoints x nComponents]
        """
        values = np.asarray(values, dtype=np.float32).ravel()
        n_points = len(values) // max(n_components, 1)
        fmin = float(values.min()) if len(values) > 0 else 0.0
        fmax = float(values.max()) if len(values) > 0 else 0.0
        header = struct.pack("<ffII", fmin, fmax, n_components, n_points)
        return header + values.tobytes()


class MeshPreviewLoader:
    """Load boundary surfaces from polyMesh for mesh preview.

    Reads OpenFOAM polyMesh files natively (no PyVista/VTK dependency).
    Only reads boundary faces + referenced points, skipping internal mesh
    entirely. For a 3.7M-cell mesh this reads ~9% of faces, reducing load
    time from ~660s (PyVista OpenFOAMReader) to a few seconds.
    """

    def __init__(self, mesh_dir: Path) -> None:
        self.mesh_dir = Path(mesh_dir).resolve()
        self._binary: Optional[bytes] = None
        self._meta: Optional[List[dict]] = None

    # ------------------------------------------------------------------
    # Volume mesh counts
    # ------------------------------------------------------------------

    def read_counts(self) -> dict:
        """Return volume cell + boundary face counts from polyMesh.

        OpenFOAM writes ``note "nPoints:... nCells:... nFaces:... nInternalFaces:..."``
        into the ``owner`` file header. When present we use it directly —
        it's authoritative and costs a few bytes to read. When absent
        (very old meshes, or custom writers that drop the note) we return
        ``None`` for counts that require it and still report the sum of
        ``nFaces`` parsed from the ``boundary`` file.

        Keys:
          - ``nCells`` — total volume cells (or ``None`` if note absent)
          - ``nFaces`` — total faces (internal + boundary) or ``None``
          - ``nInternalFaces`` — internal faces or ``None``
          - ``nBoundaryFaces`` — ``nFaces - nInternalFaces`` or ``None``
          - ``nBoundarySum`` — sum of per-patch ``nFaces`` from ``boundary``
            (always a concrete int; ``0`` when polyMesh missing)
        """
        import re

        result = {
            "nCells": None,
            "nFaces": None,
            "nInternalFaces": None,
            "nBoundaryFaces": None,
            "nBoundarySum": 0,
        }
        poly = self.mesh_dir / "constant" / "polyMesh"
        owner = poly / "owner"
        boundary = poly / "boundary"

        if owner.exists():
            # Header is ~20 lines; read a bounded chunk to avoid touching the
            # binary payload on large meshes.
            head = owner.read_text(encoding="utf-8", errors="replace")[:4096]
            m = re.search(
                r'note\s+"([^"]*)"',
                head,
            )
            if m:
                note = m.group(1)
                pairs = dict(re.findall(r"(\w+):(\d+)", note))
                if "nCells" in pairs:
                    result["nCells"] = int(pairs["nCells"])
                if "nFaces" in pairs:
                    result["nFaces"] = int(pairs["nFaces"])
                if "nInternalFaces" in pairs:
                    result["nInternalFaces"] = int(pairs["nInternalFaces"])
                if result["nFaces"] is not None and result["nInternalFaces"] is not None:
                    result["nBoundaryFaces"] = result["nFaces"] - result["nInternalFaces"]

        if boundary.exists():
            patches = self._parse_boundary(boundary)
            result["nBoundarySum"] = sum(int(p.get("nFaces", 0)) for p in patches)

        return result

    # ------------------------------------------------------------------
    # polyMesh ASCII parsers
    # ------------------------------------------------------------------

    @staticmethod
    def _skip_foam_header(lines: List[str]) -> int:
        """Return the index of the first data line after the FoamFile header."""
        i = 0
        n = len(lines)
        # Skip to the count line (first line that is just an integer)
        while i < n:
            stripped = lines[i].strip()
            if stripped.isdigit():
                return i
            i += 1
        return i

    @staticmethod
    def _parse_boundary(boundary_path: Path) -> List[dict]:
        """Parse boundary file → list of {name, type, nFaces, startFace}."""
        text = boundary_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        patches = []
        i = 0
        n = len(lines)
        # Find the opening '(' after the count
        while i < n and lines[i].strip() != "(":
            i += 1
        i += 1  # skip '('

        while i < n:
            line = lines[i].strip()
            if line == ")":
                break
            if line == "{" or line == "" or line.startswith("//"):
                i += 1
                continue
            # Patch name (a word not starting with special chars)
            if line and not line.startswith(("{", "}", "(", ")")):
                patch_name = line
                patch_info: dict = {"name": patch_name, "type": "patch", "nFaces": 0, "startFace": 0}
                # Read until closing '}'
                i += 1
                while i < n:
                    pline = lines[i].strip()
                    if pline == "}":
                        break
                    if pline.startswith("type"):
                        patch_info["type"] = pline.split()[-1].rstrip(";")
                    elif pline.startswith("nFaces"):
                        patch_info["nFaces"] = int(pline.split()[-1].rstrip(";"))
                    elif pline.startswith("startFace"):
                        patch_info["startFace"] = int(pline.split()[-1].rstrip(";"))
                    i += 1
                patches.append(patch_info)
            i += 1
        return patches

    @staticmethod
    def _parse_points(points_path: Path) -> np.ndarray:
        """Parse points file → (N, 3) float32 array."""
        text = points_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        idx = MeshPreviewLoader._skip_foam_header(lines)
        count = int(lines[idx].strip())
        idx += 1
        # Skip '('
        while idx < len(lines) and lines[idx].strip() != "(":
            idx += 1
        idx += 1

        points = np.empty((count, 3), dtype=np.float64)
        pi = 0
        while pi < count and idx < len(lines):
            line = lines[idx].strip()
            if line == ")" or line == "":
                idx += 1
                continue
            # Format: (x y z)
            inner = line.strip("()")
            parts = inner.split()
            if len(parts) >= 3:
                points[pi] = [float(parts[0]), float(parts[1]), float(parts[2])]
                pi += 1
            idx += 1
        return points[:pi].astype(np.float32)

    @staticmethod
    def _parse_faces_range(faces_path: Path, start: int, count: int) -> List[List[int]]:
        """Parse a range of faces from the faces file.

        Reads the entire faces data section but only extracts faces in
        [start, start+count). For large files this is I/O-bound, not
        CPU-bound, and avoids complex seeking through variable-length lines.
        """
        text = faces_path.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        idx = MeshPreviewLoader._skip_foam_header(lines)
        # total_faces = int(lines[idx].strip())
        idx += 1
        while idx < len(lines) and lines[idx].strip() != "(":
            idx += 1
        idx += 1  # skip '('

        faces: List[List[int]] = []
        face_idx = 0
        end = start + count
        while idx < len(lines):
            line = lines[idx].strip()
            idx += 1
            if line == ")" or line == "":
                if line == ")":
                    break
                continue
            if face_idx >= end:
                break
            if face_idx >= start:
                # Format: N(v0 v1 v2 ... vN-1)
                paren = line.index("(")
                n = int(line[:paren])
                inner = line[paren + 1 : line.rindex(")")]
                verts = [int(x) for x in inner.split()]
                faces.append(verts)
            face_idx += 1
        return faces

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> Tuple[bytes, List[dict]]:
        """Read polyMesh boundary surfaces and return (binary, metadata)."""
        if self._binary is not None:
            return self._binary, self._meta

        poly_dir = self.mesh_dir / "constant" / "polyMesh"
        boundary_path = poly_dir / "boundary"
        points_path = poly_dir / "points"
        faces_path = poly_dir / "faces"

        if not boundary_path.exists():
            return b"", []

        t0 = time.time()

        # 1. Parse boundary patches
        boundary_patches = self._parse_boundary(boundary_path)
        if not boundary_patches:
            return b"", []

        # 2. Determine the global face range we need (min startFace to max end)
        min_start = min(p["startFace"] for p in boundary_patches)
        max_end = max(p["startFace"] + p["nFaces"] for p in boundary_patches)
        total_boundary_faces = max_end - min_start

        logger.info(
            f"[MeshPreview] {len(boundary_patches)} patches, "
            f"boundary faces {min_start}..{max_end} ({total_boundary_faces} faces)"
        )

        # 3. Read ALL boundary faces in one pass (faces are contiguous at end of file)
        all_boundary_faces = self._parse_faces_range(faces_path, min_start, total_boundary_faces)
        t_faces = time.time()
        logger.info(f"[MeshPreview] Parsed {len(all_boundary_faces)} boundary faces in {t_faces - t0:.1f}s")

        # 4. Collect unique point indices referenced by boundary faces
        unique_pts: set = set()
        for face in all_boundary_faces:
            unique_pts.update(face)

        # 5. Read points file (we need all points since boundary can reference any)
        all_points = self._parse_points(points_path)
        t_points = time.time()
        logger.info(f"[MeshPreview] Parsed {all_points.shape[0]} points in {t_points - t_faces:.1f}s")

        # 6. Build per-patch geometry with local point indices
        result_patches = []
        for bp in boundary_patches:
            nf = bp["nFaces"]
            sf = bp["startFace"]
            if nf == 0:
                continue

            # Extract this patch's faces from the bulk read
            patch_faces = all_boundary_faces[sf - min_start : sf - min_start + nf]

            # Collect unique point indices for this patch
            patch_pt_set: set = set()
            for face in patch_faces:
                patch_pt_set.update(face)
            patch_pt_list = sorted(patch_pt_set)

            if not patch_pt_list:
                continue

            # Build global→local index map
            g2l = {g: l for l, g in enumerate(patch_pt_list)}

            # Extract points subset
            pts = all_points[patch_pt_list]

            # Remap faces to local indices, triangulate, extract edges
            edges_set: set = set()
            tris_list: list = []
            for face in patch_faces:
                local_verts = [g2l[v] for v in face]
                nv = len(local_verts)
                for k in range(nv):
                    v0, v1 = local_verts[k], local_verts[(k + 1) % nv]
                    edges_set.add((min(v0, v1), max(v0, v1)))
                for k in range(1, nv - 1):
                    tris_list.append([local_verts[0], local_verts[k], local_verts[k + 1]])

            edge_pairs = np.array(sorted(edges_set), dtype=np.uint32) if edges_set else np.empty((0, 2), dtype=np.uint32)
            indices = np.array(tris_list, dtype=np.uint32) if tris_list else np.empty((0, 3), dtype=np.uint32)

            result_patches.append({
                "name": bp["name"],
                "points": np.ascontiguousarray(pts, dtype=np.float32),
                "indices": indices,
                "edges": edge_pairs,
            })

        t_done = time.time()
        logger.info(
            f"[MeshPreview] Built {len(result_patches)} patches "
            f"({sum(p['points'].shape[0] for p in result_patches)} pts, "
            f"{sum(p['indices'].shape[0] for p in result_patches)} tris) "
            f"in {t_done - t0:.1f}s total"
        )

        self._binary, self._meta = CachedPostProcessor.pack_mesh_binary(result_patches)
        for m in self._meta:
            m["defaultVisible"] = not m["name"].endswith("_slave")
        return self._binary, self._meta
