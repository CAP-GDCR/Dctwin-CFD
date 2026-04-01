"""
Mesh generation for OpenFOAM CFD cases.
"""
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from dctwin.utils import template_env
from dctwin.third_parties.foam.geometry.manager import GeometryManager


class MeshGenerator:
    """
    Generates mesh for OpenFOAM CFD simulations.
    
    Handles:
    - STL geometry generation
    - blockMesh background mesh
    - surfaceFeatureExtract for edge features
    - snappyHexMesh for body-fitted mesh
    - topoSet for cellZone creation
    - createBaffles for internal surfaces
    """
    
    def __init__(self, config: "CaseConfig", geometry_manager: Optional[GeometryManager] = None):
        """
        Initialize mesh generator.
        
        Args:
            config: CaseConfig instance
            geometry_manager: Optional pre-configured GeometryManager
        """
        from .config import CaseConfig
        self.config = config
        self.case_dir = config.case_dir
        self.mesh_dir = config.mesh_cache_dir
        
        # Create geometry manager if not provided
        if geometry_manager is None:
            self.geometry_manager = GeometryManager(
                output_dir=self.case_dir,
                base_size=config.base_size,
                refinement_level=config.refinement_level,
            )
        else:
            self.geometry_manager = geometry_manager
        
        self._setup_complete = False
    
    def setup_geometry(self) -> None:
        """Setup geometry from room configuration."""
        if self._setup_complete:
            return
        
        # Load geometry from room.json
        room_json_path = self.config.room_json_path
        logger.info(f"Loading geometry from: {room_json_path}")
        
        self.geometry_manager.load_from_json(room_json_path)
        
        # Validate configuration
        errors = self.geometry_manager.validate_all()
        if errors:
            for error in errors:
                logger.error(f"Validation error: {error}")
            raise ValueError("Geometry validation failed")
        
        self._setup_complete = True
        logger.info(f"Geometry setup complete with {len(self.geometry_manager.entities)} entities")
    
    def generate_stl(self) -> Path:
        """
        Generate STL geometry files.
        
        Returns:
            Path to the triSurface directory
        """
        self.setup_geometry()
        
        # Ensure directories exist
        tri_surface_dir = self.mesh_dir / "constant" / "triSurface"
        tri_surface_dir.mkdir(parents=True, exist_ok=True)
        
        # Set STL output directory
        self.geometry_manager.stl_dir = tri_surface_dir
        
        # Generate STL files
        self.geometry_manager.generate_stl_files(force=True)
        
        logger.info(f"STL files generated in: {tri_surface_dir}")
        return tri_surface_dir
    
    def generate_mesh_dicts(self) -> None:
        """Generate all mesh-related OpenFOAM dictionaries."""
        self.setup_geometry()
        
        system_dir = self.mesh_dir / "system"
        system_dir.mkdir(parents=True, exist_ok=True)
        
        mesh_cfg = self.config.mesh_config
        
        # blockMeshDict
        self._generate_block_mesh_dict(system_dir)
        
        # surfaceFeatureExtractDict
        if mesh_cfg.get("use_feature_edges", True):
            self._generate_surface_feature_extract_dict(system_dir)
        
        # snappyHexMeshDict
        self._generate_snappy_hex_mesh_dict(system_dir)
        
        # topoSetDict for leakage zones
        self._generate_toposet_leakage_dict(system_dir)
        
        # createBafflesDict (convert faceZone internal faces to cyclic baffles)
        # snappyHexMesh with faceType internal creates internal faces in faceZone
        # createBaffles converts these to proper cyclic master/slave patches
        self._generate_create_baffles_dict(system_dir)
        
        # Copy required system files
        self._copy_system_files(system_dir)
        
        # controlDict
        self._generate_control_dict(system_dir)
        
        logger.info(f"Mesh dictionaries generated in: {system_dir}")
    
    def _generate_block_mesh_dict(self, system_dir: Path) -> None:
        """Generate blockMeshDict."""
        block_mesh_config = self.geometry_manager.get_blockMesh_config()
        template = template_env.get_template("foam/template/mesh/blockMeshDict.j2")
        
        with open(system_dir / "blockMeshDict", "w", encoding="utf-8") as f:
            f.write(template.render(
                v_min=block_mesh_config["v_min"],
                v_max=block_mesh_config["v_max"],
                x_cells=block_mesh_config["x_cells"],
                y_cells=block_mesh_config["y_cells"],
                z_cells=block_mesh_config["z_cells"],
            ))
    
    def _generate_surface_feature_extract_dict(self, system_dir: Path) -> None:
        """Generate surfaceFeatureExtractDict."""
        from dctwin.third_parties.foam.geometry.rack import RackEntity
        
        mesh_cfg = self.config.mesh_config
        leakage_zones = self.geometry_manager._collect_all_leakage_zones()
        
        # Check if we have room wall and wall baffles
        has_room_wall = self.geometry_manager.room_geometry is not None
        has_wall_baffles = len(self.geometry_manager.get_wall_baffle_entities()) > 0
        
        # Get perforated baffle STL filenames for feature extraction
        # These use faceZone + faceType internal in snappyHexMesh
        perforated_baffle_stl_files = []
        for baffle in self.geometry_manager.get_perforated_baffle_entities():
            perforated_baffle_stl_files.append(baffle.stl_filename)
        
        # Get independent equipment STL filenames (non-RACK: ACU, Pillar, Box, etc.)
        # Per requirement 10: these are kept as individual STL files, not merged
        independent_equipment_stl_files = []
        for entity in self.geometry_manager.entities:
            if not isinstance(entity, RackEntity):
                independent_equipment_stl_files.append(entity.stl_filename)
        
        template = template_env.get_template("foam/template/mesh/surfaceFeatureExtractDict.j2")
        with open(system_dir / "surfaceFeatureExtractDict", "w", encoding="utf-8") as f:
            f.write(template.render(
                included_angle=mesh_cfg.get("feature_included_angle", 150),
                has_leakage_zones=len(leakage_zones) > 0,
                has_room_wall=has_room_wall,
                has_wall_baffles=has_wall_baffles,
                baffle_included_angle=mesh_cfg.get("baffle_included_angle", 150),
                perforated_baffle_stl_files=perforated_baffle_stl_files,
                independent_equipment_stl_files=independent_equipment_stl_files,
            ))
    
    def _generate_snappy_hex_mesh_dict(self, system_dir: Path) -> None:
        """Generate snappyHexMeshDict."""
        mesh_cfg = self.config.mesh_config
        
        # Location in mesh
        location_cfg = mesh_cfg.get("location_in_mesh", [0.0, 0.0, 0.0])
        location = type("Location", (), {"x": location_cfg[0], "y": location_cfg[1], "z": location_cfg[2]})()
        
        # Snap settings
        snap_enabled = mesh_cfg.get("snap_enabled", True)
        snap_n_solve_iter = mesh_cfg.get("snap_n_solve_iter", 30)
        snap_n_smooth_patch = mesh_cfg.get("snap_n_smooth_patch", 1)
        snap_n_relax_iter = mesh_cfg.get("snap_n_relax_iter", 3)
        snap_n_feature_snap_iter = mesh_cfg.get("snap_n_feature_snap_iter", 10)
        snap_tolerance = mesh_cfg.get("snap_tolerance", 2.0)
        resolve_feature_angle = mesh_cfg.get("resolve_feature_angle", 30)
        n_cells_between_levels = mesh_cfg.get("n_cells_between_levels", 1)
        
        # Feature snap settings
        use_feature_edges = mesh_cfg.get("use_feature_edges", True)
        explicit_feature_snap = mesh_cfg.get("explicit_feature_snap", True)
        
        # Mesh quality settings
        max_non_ortho = mesh_cfg.get("max_non_ortho", 45.0)
        max_boundary_skewness = mesh_cfg.get("max_boundary_skewness", 10)
        max_non_ortho_relaxed = mesh_cfg.get("max_non_ortho_relaxed", 50)
        
        use_merged_stl = mesh_cfg.get("use_merged_stl", True)
        
        # Refinement levels for STL surfaces (can be different per entity type)
        # Support both old format (int) and new format ([min, max])
        def parse_level(value, default=(1, 1)):
            """Parse refinement level to (min, max) tuple."""
            if value is None:
                return default
            if isinstance(value, (list, tuple)) and len(value) == 2:
                return (int(value[0]), int(value[1]))
            if isinstance(value, int):
                return (value, value)
            return default
        
        refinement_level = parse_level(mesh_cfg.get("refinement_level"), (1, 1))
        rack_refinement_level = parse_level(mesh_cfg.get("rack_refinement_level"), refinement_level)
        server_face_refinement_level = parse_level(mesh_cfg.get("server_face_refinement_level"), (refinement_level[0], refinement_level[1] + 1))
        acu_refinement_level = parse_level(mesh_cfg.get("acu_refinement_level"), refinement_level)
        # Leakage zones are often very thin (e.g., 1U = 0.05m), need higher refinement
        # Default to level 1 for normal zones, adaptive algorithm will boost for narrow gaps
        leakage_refinement_level = parse_level(mesh_cfg.get("leakage_refinement_level"), (1, 1))
        # New: wall baffles, perforated baffles, room wall refinement levels
        wall_baffles_refinement_level = parse_level(mesh_cfg.get("wall_baffles_refinement_level"), (1, 1))
        perforated_wall_baffles_refinement_level = parse_level(mesh_cfg.get("perforated_wall_baffles_refinement_level"), (1, 1))
        room_wall_refinement_level = parse_level(mesh_cfg.get("room_wall_refinement_level"), (0, 0))
        
        template = template_env.get_template("foam/template/mesh/snappyHexMeshDict_stl.j2")
        
        # Get room wall and baffle configurations
        room_wall_geometry_cmd = self.geometry_manager._get_room_wall_geometry_cmd()
        baffles_geometry_cmd = self.geometry_manager._get_baffles_geometry_cmd()
        room_wall_refinement_cmd = self.geometry_manager.get_snappyHexMesh_room_wall_refinementSurfaces_cmd(
            refinement_level=room_wall_refinement_level
        )
        # Use specific refinement levels for wall baffles and perforated baffles
        baffle_refinement_cmd = self.geometry_manager.get_snappyHexMesh_baffle_refinementSurfaces_cmd(
            wall_level=wall_baffles_refinement_level,
            perforated_level=perforated_wall_baffles_refinement_level,
        )
        
        # Feature edge commands for room wall, wall baffles, and perforated baffles
        # Use higher refinement level for feature edges to ensure sharp corners
        feature_level = max(refinement_level[1], server_face_refinement_level[1])
        room_wall_features_cmd = ""
        baffle_features_cmd = ""
        if use_feature_edges:
            if room_wall_geometry_cmd:
                room_wall_features_cmd = f"""
        {{
            file "room_wall.eMesh";
            level {feature_level};
        }}"""
            # Wall baffles have merged STL and eMesh
            wall_baffles = self.geometry_manager.get_wall_baffle_entities()
            if wall_baffles:
                baffle_features_cmd = f"""
        {{
            file "merged_wall_baffles.eMesh";
            level {feature_level};
        }}"""
            # Perforated baffles have individual STL and eMesh files
            # These help snappyHexMesh snap to STL edges for smooth baffle boundaries
            perforated_baffles = self.geometry_manager.get_perforated_baffle_entities()
            for baffle in perforated_baffles:
                emesh_filename = baffle.stl_filename.replace('.stl', '.eMesh')
                baffle_features_cmd += f"""
        {{
            file "{emesh_filename}";
            level {feature_level};
        }}"""
        
        with open(system_dir / "snappyHexMeshDict", "w", encoding="utf-8") as f:
            f.write(template.render(
                geometry_cmd=self.geometry_manager.get_snappyHexMesh_geometry_cmd(use_merged=use_merged_stl),
                features_cmd=self.geometry_manager.get_snappyHexMesh_features_cmd(use_merged=use_merged_stl, feature_level=feature_level) if use_feature_edges else "",
                refinement_surfaces_cmd=self.geometry_manager.get_snappyHexMesh_refinementSurfaces_cmd(
                    use_merged=use_merged_stl, 
                    refinement_level=refinement_level,
                    rack_refinement_level=rack_refinement_level,
                    server_face_refinement_level=server_face_refinement_level,
                    acu_refinement_level=acu_refinement_level,
                    base_size=self.config.base_size,
                ),
                refinement_regions_cmd=self.geometry_manager.get_snappyHexMesh_refinementRegions_cmd(
                    refinement_level=leakage_refinement_level,
                    base_size=self.config.base_size,
                ),
                locations_inside_cmd=self.geometry_manager.get_snappyHexMesh_locationsInside_cmd(),
                location=location,
                snap_enabled=snap_enabled,
                snap_n_solve_iter=snap_n_solve_iter,
                snap_n_smooth_patch=snap_n_smooth_patch,
                snap_n_relax_iter=snap_n_relax_iter,
                snap_n_feature_snap_iter=snap_n_feature_snap_iter,
                snap_tolerance=snap_tolerance,
                resolve_feature_angle=resolve_feature_angle,
                n_cells_between_levels=n_cells_between_levels,
                explicit_feature_snap='true' if explicit_feature_snap else 'false',
                implicit_feature_snap='false',
                max_non_ortho=max_non_ortho,
                max_boundary_skewness=max_boundary_skewness,
                max_non_ortho_relaxed=max_non_ortho_relaxed,
                # New baffle and room wall parameters
                room_wall_geometry_cmd=room_wall_geometry_cmd,
                baffles_geometry_cmd=baffles_geometry_cmd,
                room_wall_refinement_cmd=room_wall_refinement_cmd,
                baffle_refinement_cmd=baffle_refinement_cmd,
                room_wall_features_cmd=room_wall_features_cmd,
                baffle_features_cmd=baffle_features_cmd,
            ))
        
        # Rewrite cyclic as patch if needed
        if mesh_cfg.get("snappy_cyclic_as_patch", True):
            self._rewrite_snappy_cyclic_as_patch(system_dir / "snappyHexMeshDict")
    
    def _generate_toposet_leakage_dict(self, system_dir: Path) -> None:
        """Generate topoSetDict for leakage cellZones."""
        leakage_zones_config = self.geometry_manager.get_leakage_zones_bbox_config()
        
        if not leakage_zones_config:
            return
        
        template = template_env.get_template("foam/template/mesh/topoSetDict_leakage.j2")
        with open(system_dir / "topoSetDict_leakage", "w", encoding="utf-8") as f:
            f.write(template.render(leakage_zones=leakage_zones_config))
        
        logger.info(f"Generated topoSetDict_leakage for {len(leakage_zones_config)} leakage zones")
    
    def _generate_toposet_perforated_baffle_dict(self, system_dir: Path) -> None:
        """Generate topoSetDict for perforated baffles.
        
        This creates faceZones from boundary patches created by snappyHexMesh.
        These faceZones are then used by createBaffles to create cyclic master/slave pairs.
        """
        perforated_baffles = self.geometry_manager.get_perforated_baffle_toposet_config()
        
        if not perforated_baffles:
            return
        
        template = template_env.get_template("foam/template/mesh/topoSetDict_perforated_baffle.j2")
        with open(system_dir / "topoSetDict_perforated_baffle", "w", encoding="utf-8") as f:
            f.write(template.render(perforated_baffles=perforated_baffles))
        
        logger.info(f"Generated topoSetDict_perforated_baffle for {len(perforated_baffles)} perforated baffles")
    
    def _generate_create_baffles_dict(self, system_dir: Path) -> None:
        """Generate createBafflesDict to convert faceZone internal faces to baffles.

        ALL internal baffles use faceType internal + createBaffles with type cyclic.
        Only regular walls (patchInfo wall in snappyHexMesh) use type wall.
        The BC differentiation (no-slip vs porousBafflePressure vs pure cyclic)
        is handled in the 0/ field files, not here.

        After createBaffles, wall baffle patches are converted from cyclic to wall
        type in the boundary file via _convert_wall_baffles_to_wall_type().
        """
        perforated_baffles = self.geometry_manager.get_perforated_baffle_entities()
        wall_baffles = self.geometry_manager.get_wall_baffle_entities()

        if not perforated_baffles and not wall_baffles:
            return

        baffle_config = []

        # Perforated baffles → all cyclic in createBaffles
        for baffle in perforated_baffles:
            regions = baffle.get_solid_regions()
            if not regions:
                continue
            zone_name = regions[0].name
            baffle_config.append({
                "zone_name": zone_name,
                "master_name": f"{zone_name}_master",
                "slave_name": f"{zone_name}_slave",
            })

        # Wall baffles → also cyclic in createBaffles (converted to wall type later)
        for baffle in wall_baffles:
            for region in baffle.get_solid_regions():
                if not region.triangles:
                    continue
                baffle_config.append({
                    "zone_name": region.name,
                    "master_name": f"{region.name}_master",
                    "slave_name": f"{region.name}_slave",
                })

        if not baffle_config:
            return

        template = template_env.get_template("foam/template/mesh/createBafflesDict.j2")
        with open(system_dir / "createBafflesDict", "w", encoding="utf-8") as f:
            f.write(template.render(baffles=baffle_config))
        logger.info(f"Generated createBafflesDict for {len(baffle_config)} baffles (wall={len(wall_baffles)}, perforated={len(perforated_baffles)})")
    
    def _copy_system_files(self, system_dir: Path) -> None:
        """Copy required system files (fvSchemes, fvSolution)."""
        template_root = Path(__file__).resolve().parents[1] / "third_parties"
        
        shutil.copy(
            template_root / "foam" / "template" / "system" / "steady" / "fvSchemes",
            system_dir / "fvSchemes",
        )
        shutil.copy(
            template_root / "foam" / "template" / "system" / "steady" / "fvSolution_cpu",
            system_dir / "fvSolution",
        )
    
    def _generate_control_dict(self, system_dir: Path) -> None:
        """Generate controlDict."""
        sim_cfg = self.config.simulation_config
        probes = self.config.extract_sensors()
        
        template = template_env.get_template("foam/template/system/steady/controlDict.j2")
        with open(system_dir / "controlDict", "w", encoding="utf-8") as f:
            f.write(template.render(
                end_time=sim_cfg.get("end_time", 1000),
                delta_t=sim_cfg.get("delta_t", 1),
                write_interval=sim_cfg.get("write_interval", 100),
                probes=probes,
            ))
    
    def _generate_decompose_par_dict(self, system_dir: Path, n_procs: int) -> None:
        """Generate decomposeParDict for parallel mesh generation.
        
        Args:
            system_dir: Path to system directory
            n_procs: Number of processors for parallel decomposition
        """
        template = template_env.get_template("foam/template/system/decomposeParDict.j2")
        with open(system_dir / "decomposeParDict", "w", encoding="utf-8") as f:
            f.write(template.render(process_num=n_procs))
        logger.info(f"Generated decomposeParDict with {n_procs} processors")
    
    def _rewrite_snappy_cyclic_as_patch(self, snappy_path: Path) -> None:
        """Rewrite cyclic patch type as patch in snappyHexMeshDict."""
        content = snappy_path.read_text(encoding="utf-8")
        modified = re.sub(r'type\s+cyclic\s*;', 'type patch;', content)
        snappy_path.write_text(modified, encoding="utf-8")
    
    def run_mesh_generation(self, 
                           run_block_mesh: bool = True,
                           run_feature_extract: bool = True,
                           run_snappy: bool = True,
                           run_toposet_leakage: bool = True,
                           run_baffles: bool = True) -> None:
        """
        Run the complete mesh generation pipeline.
        
        Supports both serial and parallel modes:
        - Serial: blockMesh -> surfaceFeatureExtract -> snappyHexMesh -> topoSet -> createBaffles
        - Parallel: blockMesh -> surfaceFeatureExtract -> decomposePar -> 
                    mpirun snappyHexMesh -parallel -> mpirun topoSet -parallel ->
                    mpirun createBaffles -parallel -> reconstructParMesh
        
        In parallel mode, ALL mesh utilities run in parallel on decomposed mesh,
        and reconstruction happens only at the very end for ParaView visualization.
        
        Args:
            run_block_mesh: Run blockMesh
            run_feature_extract: Run surfaceFeatureExtract
            run_snappy: Run snappyHexMesh
            run_toposet_leakage: Run topoSet for leakage zones
            run_baffles: Run createBaffles
        """
        mesh_cfg = self.config.mesh_config
        process_cfg = self.config.process_config
        case_dir = self.mesh_dir
        
        # Check if parallel mesh generation is enabled.
        # Parallel requires passwordless SSH to localhost (OpenMPI rsh PLM).
        # If SSH to localhost fails, fall back to serial automatically.
        parallel_mesh = mesh_cfg.get("parallel_mesh", True)
        n_procs = process_cfg.get("mesh_process", 16)

        if parallel_mesh:
            parallel_mesh = self._check_mpi_available()
            if not parallel_mesh:
                logger.warning("SSH to localhost failed — falling back to SERIAL mesh generation")

        if parallel_mesh:
            logger.info(f"Starting PARALLEL mesh generation with {n_procs} processors...")
            self._run_parallel_mesh_pipeline(
                case_dir, n_procs,
                run_block_mesh=run_block_mesh,
                run_feature_extract=run_feature_extract,
                run_snappy=run_snappy,
                run_toposet_leakage=run_toposet_leakage,
                run_baffles=run_baffles,
            )
        else:
            logger.info("Starting SERIAL mesh generation...")
            self._run_serial_mesh_pipeline(
                case_dir,
                run_block_mesh=run_block_mesh,
                run_feature_extract=run_feature_extract,
                run_snappy=run_snappy,
                run_toposet_leakage=run_toposet_leakage,
                run_baffles=run_baffles,
            )
        
        # Convert cyclic to cyclicAMI (on reconstructed mesh)
        if mesh_cfg.get("convert_cyclic_to_ami", True):
            self._convert_cyclic_to_ami()
        
        # Create .foam file for ParaView
        self._create_foam_file()
        
        logger.info("Mesh generation complete")
    
    def _run_serial_mesh_pipeline(self, case_dir: Path,
                                   run_block_mesh: bool,
                                   run_feature_extract: bool,
                                   run_snappy: bool,
                                   run_toposet_leakage: bool,
                                   run_baffles: bool) -> None:
        """Run mesh generation in serial mode."""
        mesh_cfg = self.config.mesh_config
        
        # Step 1: blockMesh
        if run_block_mesh:
            self._run_cmd("blockMesh", case_dir)
        
        # Step 2: surfaceFeatureExtract
        if run_feature_extract and mesh_cfg.get("use_feature_edges", True):
            if (case_dir / "system" / "surfaceFeatureExtractDict").exists():
                logger.info("Extracting surface features (eMesh)...")
                self._run_cmd("surfaceFeatureExtract", case_dir)
        
        # Step 3: snappyHexMesh
        if run_snappy:
            self._run_cmd("snappyHexMesh -overwrite", case_dir)
        
        # Step 4: topoSet for leakage zones
        if run_toposet_leakage:
            if (case_dir / "system" / "topoSetDict_leakage").exists():
                logger.info("Creating leakage cellZones via topoSet...")
                self._run_cmd("topoSet -dict system/topoSetDict_leakage", case_dir)
        
        # Step 5: Create baffles from faceZones using createBaffles
        # All baffles use faceType internal + createBaffles (type cyclic)
        if run_baffles:
            if (case_dir / "system" / "createBafflesDict").exists():
                logger.info("Creating baffles from faceZones via createBaffles...")
                self._regenerate_fields_for_baffles(case_dir)
                self._run_cmd("createBaffles -overwrite", case_dir)
                # Convert wall baffle patches from cyclic to wall type
                self._convert_wall_baffle_patches_to_wall(case_dir)
    
    def _run_parallel_mesh_pipeline(self, case_dir: Path, n_procs: int,
                                     run_block_mesh: bool,
                                     run_feature_extract: bool,
                                     run_snappy: bool,
                                     run_toposet_leakage: bool,
                                     run_baffles: bool) -> None:
        """Run complete mesh generation pipeline in parallel mode.
        
        Most mesh utilities run in parallel, but createBaffles (which creates
        cyclic patches for perforated baffles) runs in serial AFTER reconstruction,
        because OpenFOAM cannot correctly create cyclic baffles in parallel.
        
        Workflow:
        1. blockMesh (serial)
        2. surfaceFeatureExtract (serial)
        3. decomposePar
        4. mpirun snappyHexMesh -parallel
        5. mpirun topoSet -parallel (for leakage cellZones)
        6. reconstructParMesh -constant
        7. createBaffles (serial, for cyclic perforated baffles - MUST be after reconstruct)
        8. Clean up processor directories
        
        Note: createBaffles is run serially after reconstruction because OpenFOAM
        bug tracker reports that cyclic baffles cannot be correctly created in parallel.
        
        Args:
            case_dir: Case directory path
            n_procs: Number of processors
        """
        mesh_cfg = self.config.mesh_config
        system_dir = case_dir / "system"
        
        # Step 1: blockMesh (always serial - creates background mesh)
        if run_block_mesh:
            self._run_cmd("blockMesh", case_dir)
        
        # Step 2: surfaceFeatureExtract (serial - extracts edge features)
        if run_feature_extract and mesh_cfg.get("use_feature_edges", True):
            if (system_dir / "surfaceFeatureExtractDict").exists():
                logger.info("Extracting surface features (eMesh)...")
                self._run_cmd("surfaceFeatureExtract", case_dir)
        
        # Step 3: Generate decomposeParDict and decompose
        self._generate_decompose_par_dict(system_dir, n_procs)
        logger.info(f"Decomposing mesh into {n_procs} domains...")
        self._run_cmd("decomposePar -force", case_dir)
        
        # Step 4: Run snappyHexMesh in parallel
        if run_snappy:
            logger.info(f"Running snappyHexMesh in parallel ({n_procs} processors)...")
            self._run_cmd(f"mpirun -np {n_procs} snappyHexMesh -parallel -overwrite", case_dir)
        
        # Step 5: Run topoSet in parallel for leakage cellZones
        if run_toposet_leakage:
            if (system_dir / "topoSetDict_leakage").exists():
                logger.info(f"Creating leakage cellZones via topoSet -parallel ({n_procs} processors)...")
                self._run_cmd(f"mpirun -np {n_procs} topoSet -parallel -dict system/topoSetDict_leakage", case_dir)
        
        # Step 6: Reconstruct the mesh BEFORE createBaffles
        # This is necessary because cyclic baffles cannot be created in parallel
        logger.info("Reconstructing parallel mesh...")
        self._run_cmd("reconstructParMesh -constant", case_dir)
        
        # Step 7: Clean up processor directories to save space
        logger.info("Cleaning up processor directories...")
        self._cleanup_processor_dirs(case_dir)
        
        # Step 8: Create baffles from faceZones using createBaffles
        # All baffles use faceType internal + createBaffles (type cyclic)
        if run_baffles:
            if (system_dir / "createBafflesDict").exists():
                logger.info("Creating baffles from faceZones via createBaffles...")
                self._regenerate_fields_for_baffles(case_dir)
                self._run_cmd("createBaffles -overwrite", case_dir)
                # Convert wall baffle patches from cyclic to wall type
                self._convert_wall_baffle_patches_to_wall(case_dir)
        
        logger.info("Parallel mesh pipeline complete")
    
    def _cleanup_processor_dirs(self, case_dir: Path) -> None:
        """Remove processor* directories after mesh reconstruction.
        
        Args:
            case_dir: Case directory path
        """
        import glob
        processor_dirs = list(case_dir.glob("processor*"))
        for proc_dir in processor_dirs:
            if proc_dir.is_dir():
                shutil.rmtree(proc_dir)
        if processor_dirs:
            logger.info(f"Removed {len(processor_dirs)} processor directories")
    
    def _create_foam_file(self) -> None:
        """Create .foam file for ParaView compatibility."""
        foam_file = self.mesh_dir / "case.foam"
        foam_file.touch()
        logger.info(f"Created ParaView file: {foam_file}")
    
    def _convert_cyclic_to_ami(self) -> None:
        """Convert cyclic patches to cyclicAMI."""
        boundary_path = self.mesh_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            return
        
        cyclic_pairs = self.geometry_manager.get_cyclic_patch_pairs()
        if not cyclic_pairs:
            return
        
        content = boundary_path.read_text(encoding="utf-8")
        
        for master, slave in cyclic_pairs:
            # Convert master patch
            pattern_master = rf'({master}\s*\{{\s*type\s+)cyclic(\s*;)'
            replacement_master = rf'\1cyclicAMI\2\n        neighbourPatch {slave};'
            content = re.sub(pattern_master, replacement_master, content)
            
            # Convert slave patch
            pattern_slave = rf'({slave}\s*\{{\s*type\s+)cyclic(\s*;)'
            replacement_slave = rf'\1cyclicAMI\2\n        neighbourPatch {master};'
            content = re.sub(pattern_slave, replacement_slave, content)
        
        boundary_path.write_text(content, encoding="utf-8")
        logger.info(f"Converted {len(cyclic_pairs)} cyclic pairs to cyclicAMI")

    def _get_wall_baffle_patch_names(self) -> set:
        """Get the set of master/slave patch names for wall baffles.

        These are patches that should be converted from cyclic to wall type
        after createBaffles, and should NOT be converted to cyclicAMI.
        Includes: wall baffles and perforated baffles with porosity <= 0.
        """
        names = set()
        for baffle in self.geometry_manager.get_wall_baffle_entities():
            for region in baffle.get_solid_regions():
                if not region.triangles:
                    continue
                names.add(f"{region.name}_master")
                names.add(f"{region.name}_slave")
        # Perforated baffles with porosity=0 also degenerate to wall
        for baffle in self.geometry_manager.get_perforated_baffle_entities():
            if baffle.porosity <= 0:
                regions = baffle.get_solid_regions()
                if regions:
                    names.add(f"{regions[0].name}_master")
                    names.add(f"{regions[0].name}_slave")
        return names

    def _convert_wall_baffle_patches_to_wall(self, case_dir: Path) -> None:
        """Convert wall baffle patches from cyclic to wall type in boundary file.

        After createBaffles creates all baffles as cyclic, this method converts
        wall baffle patches to type wall so they get proper no-slip treatment.
        """
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        if not boundary_path.exists():
            return

        wall_patches = self._get_wall_baffle_patch_names()
        if not wall_patches:
            return

        content = boundary_path.read_text(encoding="utf-8")
        modified = False

        for patch_name in wall_patches:
            # Match the patch block and change type cyclic → type wall
            # Also remove neighbourPatch and matchTolerance lines
            pattern = re.compile(
                rf'(\s+{re.escape(patch_name)}\s*\n'
                rf'\s*\{{\s*\n)'
                rf'(\s*type\s+)cyclic(\s*;)',
                re.MULTILINE,
            )
            if pattern.search(content):
                content = pattern.sub(r'\1\2wall\3', content)
                # Remove neighbourPatch line for this patch
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)neighbourPatch\s+\S+\s*;\n',
                    r'\1',
                    content,
                )
                # Remove inGroups containing cyclic
                content = re.sub(
                    rf'(\s+{re.escape(patch_name)}\s*\n(?:.*\n)*?\s*)inGroups\s+\d+\((?:[^)]*cyclic[^)]*)\)\s*;\n',
                    r'\1',
                    content,
                )
                modified = True

        if modified:
            boundary_path.write_text(content, encoding="utf-8")
            logger.info(f"Converted {len(wall_patches)} wall baffle patches from cyclic to wall type")

    def _convert_baffle_patches_to_cyclic(self, boundary_path: Path) -> None:
        """Convert baffle wall patches to cyclic patches.
        
        snappyHexMesh with faceType baffle creates two wall patches:
        - {name} (master, will be renamed to {name}_master)
        - {name}_slave (slave)
        
        This method:
        1. Renames master patch from {name} to {name}_master
        2. Converts both patches from wall to cyclic
        3. Sets up neighbourPatch references
        """
        if not boundary_path.exists():
            logger.warning(f"Boundary file not found: {boundary_path}")
            return
        
        content = boundary_path.read_text(encoding="utf-8")
        
        perforated_baffles = self.geometry_manager.get_perforated_baffle_entities()
        converted_count = 0
        
        for baffle in perforated_baffles:
            regions = baffle.get_solid_regions()
            if not regions:
                continue
            
            zone_name = regions[0].name
            old_master_name = zone_name  # snappyHexMesh creates this
            new_master_name = f"{zone_name}_master"  # we want this
            slave_name = f"{zone_name}_slave"
            
            # Step 1: Rename master patch from {name} to {name}_master
            # Match: "    opening_xxx\n    {"
            rename_pattern = rf'(\n    ){re.escape(old_master_name)}(\n    \{{)'
            rename_replacement = rf'\1{new_master_name}\2'
            new_content = re.sub(rename_pattern, rename_replacement, content)
            
            if new_content != content:
                content = new_content
                converted_count += 1
            
            # Step 2: Convert master patch from wall to cyclic
            master_pattern = rf'({re.escape(new_master_name)}\s*\{{\s*type\s+)wall(\s*;)'
            master_replacement = rf'\1cyclic\2\n        neighbourPatch  {slave_name};'
            new_content = re.sub(master_pattern, master_replacement, content)
            
            if new_content != content:
                content = new_content
            
            # Step 3: Convert slave patch from wall to cyclic
            slave_pattern = rf'({re.escape(slave_name)}\s*\{{\s*type\s+)wall(\s*;)'
            slave_replacement = rf'\1cyclic\2\n        neighbourPatch  {new_master_name};'
            new_content = re.sub(slave_pattern, slave_replacement, content)
            
            if new_content != content:
                content = new_content
                converted_count += 1
        
        if converted_count > 0:
            boundary_path.write_text(content, encoding="utf-8")
            logger.info(f"Converted {converted_count} baffle patches from wall to cyclic (with _master suffix)")
        else:
            logger.warning("No baffle patches converted - patterns may not match")
    
    # Path to the OpenFOAM environment script (sourced before every command)
    _FOAM_BASHRC = "/usr/lib/openfoam/openfoam2406/etc/bashrc"

    def _check_mpi_available(self) -> bool:
        """Return True if mpirun can actually spawn a worker process without hanging.

        Tests by running `mpirun -np 1 /bin/true` with a hard 8-second timeout.
        Simply checking SSH to localhost is insufficient: Open MPI 4.x can hang
        silently in pipe-mode even when SSH works, because MPI process launch or
        fabric initialisation blocks waiting for collective setup that never arrives.
        """
        try:
            result = subprocess.run(
                ["mpirun", "--oversubscribe", "-np", "1", "/bin/true"],
                capture_output=True,
                timeout=8,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _regenerate_fields_for_baffles(self, case_dir: Path) -> None:
        """Regenerate 0/ field files so createBaffles can read them.

        After snappyHexMesh the boundary patches may have changed, so the old
        0/ files (if any) won't match.  We use CaseRunner's field generator
        to create correct fields for all current patches.
        """
        # Remove stale field files (e.g. Cx, Cy, Cz from previous postProcess)
        from dctwin.runner.fields import STANDARD_FIELD_NAMES
        zero_dir = case_dir / "0"
        if zero_dir.is_dir():
            for f in zero_dir.iterdir():
                if f.is_file() and f.name not in STANDARD_FIELD_NAMES:
                    logger.info(f"Removing stale field file: 0/{f.name}")
                    f.unlink()

        from .case import CaseRunner
        runner = CaseRunner(self.case_dir)
        boundary_path = case_dir / "constant" / "polyMesh" / "boundary"
        runner._fix_cyclic_match_tolerance(boundary_path)
        patches = runner._read_boundary_patches(boundary_path)
        runner.field_generator.generate_fields(case_dir, patches)
        logger.info(f"Regenerated 0/ fields ({len(patches)} patches) for createBaffles")

        # Reconcile boundary types with wall-function BCs (safety net)
        from .stlgen_runner import STLGenRunner
        STLGenRunner._reconcile_boundary_wall_types(boundary_path, case_dir)

    def _run_cmd(self, command: str, workdir: Path) -> None:
        """Run an OpenFOAM command, streaming output via loguru.

        Sources the OpenFOAM environment first so that shared libraries and
        PATH are available even when the parent process (dctwin/server) was
        started without sourcing OpenFOAM's bashrc.
        """
        logger.info(f"Running: {command}")

        # Wrap in a bash subshell that sources OpenFOAM if the env script exists
        if Path(self._FOAM_BASHRC).exists():
            shell_cmd = f'source "{self._FOAM_BASHRC}" && {command}'
        else:
            shell_cmd = command

        proc = subprocess.Popen(
            shell_cmd,
            shell=True,
            executable="/bin/bash",
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )

        for line in iter(proc.stdout.readline, ""):
            stripped = line.rstrip()
            if stripped:
                logger.info(stripped)

        proc.wait()

        if proc.returncode != 0:
            logger.error(f"Command failed (exit {proc.returncode}): {command}")
            raise RuntimeError(f"OpenFOAM command failed: {command}")
    
    def get_config_hash(self) -> str:
        """Get a hash of the current configuration for caching."""
        return self.geometry_manager.get_config_hash()
    
    def check_mesh_cache(self) -> bool:
        """Check if a valid mesh cache exists."""
        cache_hash_file = self.mesh_dir / "constant" / "polyMesh" / ".config_hash"
        required_files = ["boundary", "faces", "neighbour", "owner", "points"]
        poly_mesh_dir = self.mesh_dir / "constant" / "polyMesh"
        
        if not cache_hash_file.exists():
            return False
        
        cached_hash = cache_hash_file.read_text(encoding="utf-8").strip()
        current_hash = self.get_config_hash()
        
        if cached_hash != current_hash:
            return False
        
        return all((poly_mesh_dir / f).exists() for f in required_files)
    
    def save_mesh_cache_hash(self) -> None:
        """Save the current configuration hash to the mesh cache."""
        cache_hash_file = self.mesh_dir / "constant" / "polyMesh" / ".config_hash"
        cache_hash_file.parent.mkdir(parents=True, exist_ok=True)
        cache_hash_file.write_text(self.get_config_hash(), encoding="utf-8")
