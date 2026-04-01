"""
CFD case runner - orchestrates the complete CFD workflow.
"""
import datetime
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from dctwin.third_parties.foam.geometry.manager import GeometryManager
from .config import CaseConfig
from .mesh import MeshGenerator
from .fields import FieldGenerator
from .openfoam_case import (
    copy_system_files_for_solver,
    fix_cyclic_match_tolerance,
    generate_control_dict,
    generate_decompose_par_dict,
    generate_fv_options,
    generate_fv_solution,
    generate_thermophysical_properties,
    read_boundary_patches,
)


class CaseRunner:
    """
    Orchestrates the complete CFD simulation workflow.
    
    Workflow stages:
    1. STL geometry generation
    2. Mesh generation (blockMesh + snappyHexMesh)
    3. Field file generation
    4. CFD solver execution
    """
    
    def __init__(self, case_dir: Path, config_file: str = "config.toml"):
        """
        Initialize the case runner.
        
        Args:
            case_dir: Path to the case directory
            config_file: Name of the TOML config file
        """
        self.case_dir = Path(case_dir).resolve()
        
        # Load configuration
        self.config = CaseConfig(self.case_dir, config_file)
        
        # Initialize components (lazy loading)
        self._geometry_manager: Optional[GeometryManager] = None
        self._mesh_generator: Optional[MeshGenerator] = None
        self._field_generator: Optional[FieldGenerator] = None
        
        # Log directory for simulation runs
        self.log_dir = self.case_dir / "log"
    
    @property
    def geometry_manager(self) -> GeometryManager:
        """Get or create the geometry manager."""
        if self._geometry_manager is None:
            self._geometry_manager = GeometryManager(
                output_dir=self.config.mesh_cache_dir,  # Use Mesh dir for STL output
                base_size=self.config.base_size,
                refinement_level=self.config.refinement_level,
            )
            self._setup_geometry()
        return self._geometry_manager
    
    @property
    def mesh_generator(self) -> MeshGenerator:
        """Get or create the mesh generator."""
        if self._mesh_generator is None:
            self._mesh_generator = MeshGenerator(
                config=self.config,
                geometry_manager=self.geometry_manager,
            )
        return self._mesh_generator
    
    @property
    def field_generator(self) -> FieldGenerator:
        """Get or create the field generator."""
        if self._field_generator is None:
            self._field_generator = FieldGenerator(
                self.config, 
                geometry_manager=self.geometry_manager
            )
        return self._field_generator
    
    def _setup_geometry(self) -> None:
        """Setup geometry from room configuration."""
        # Load geometry from room.json
        room_json_path = self.config.room_json_path
        logger.info(f"Loading geometry from: {room_json_path}")
        
        self._geometry_manager.load_from_json(room_json_path)
        
        # Validate configuration
        errors = self._geometry_manager.validate_all()
        if errors:
            for error in errors:
                logger.error(f"Validation error: {error}")
            raise ValueError("Geometry validation failed")
        
        logger.info(f"Geometry setup complete with {len(self._geometry_manager.entities)} entities")
    
    def run_stl_only(self) -> Path:
        """
        Generate STL geometry files only.
        
        Returns:
            Path to the triSurface directory
        """
        logger.info("=== STL Generation Mode ===")
        
        # Generate STL files
        stl_dir = self.mesh_generator.generate_stl()
        
        # Generate mesh dictionaries (needed for snappyHexMesh later)
        self.mesh_generator.generate_mesh_dicts()
        
        logger.info(f"STL generation complete")
        logger.info(f"STL files in: {stl_dir}")
        
        return stl_dir
    
    def run_mesh_only(self) -> Path:
        """
        Generate mesh only (STL + mesh generation).
        
        Returns:
            Path to the polyMesh directory
        """
        logger.info("=== Mesh Generation Mode ===")
        
        mesh_cfg = self.config.mesh_config
        mesh_dir = self.config.mesh_cache_dir
        
        # Check for cached mesh
        force_mesh = mesh_cfg.get("force_mesh_generation", False)
        
        if not force_mesh and self.mesh_generator.check_mesh_cache():
            logger.info("Using cached mesh")
            poly_mesh_dir = mesh_dir / "constant" / "polyMesh"
            logger.info(f"polyMesh in: {poly_mesh_dir}")
            return poly_mesh_dir
        
        # Generate STL files
        self.mesh_generator.generate_stl()
        
        # Generate mesh dictionaries
        self.mesh_generator.generate_mesh_dicts()
        
        # Run mesh generation pipeline
        run_block_mesh = mesh_cfg.get("run_block_mesh", True)
        run_snappy = mesh_cfg.get("run_snappy_hex_mesh", True)
        run_baffles = mesh_cfg.get("run_create_baffles", True)
        
        self.mesh_generator.run_mesh_generation(
            run_block_mesh=run_block_mesh,
            run_feature_extract=mesh_cfg.get("use_feature_edges", True),
            run_snappy=run_snappy,
            run_toposet_leakage=True,
            run_baffles=run_baffles,
        )
        
        # Save mesh cache hash
        self.mesh_generator.save_mesh_cache_hash()
        
        poly_mesh_dir = mesh_dir / "constant" / "polyMesh"
        
        # Report mesh statistics
        self._report_mesh_stats(poly_mesh_dir)
        
        logger.info(f"Mesh generation complete")
        logger.info(f"polyMesh in: {poly_mesh_dir}")
        
        return poly_mesh_dir
    
    def _report_mesh_stats(self, poly_mesh_dir: Path) -> None:
        """Report mesh statistics."""
        boundary_path = poly_mesh_dir / "boundary"
        if boundary_path.exists():
            patches = self._read_boundary_patches(boundary_path)
            logger.info(f"Total patches: {len(patches)}")
            
            cyclic_pairs = self.geometry_manager.get_cyclic_patch_pairs()
            logger.info(f"Cyclic patch pairs: {len(cyclic_pairs)}")
    
    def run_solve_only(self) -> Path:
        """
        Run solver only using existing mesh.
        
        Requires mesh already generated in Mesh/constant/polyMesh.
        
        Returns:
            Path to the simulation run directory
        """
        logger.info("=== Solve Only Mode ===")
        
        # Check if polyMesh exists
        mesh_dir = self.config.mesh_cache_dir
        poly_mesh_dir = mesh_dir / "constant" / "polyMesh"
        
        if not poly_mesh_dir.exists():
            raise FileNotFoundError(
                f"polyMesh not found at {poly_mesh_dir}. "
                "Please run --mesh-only first to generate mesh."
            )
        
        logger.info(f"Using existing mesh from: {poly_mesh_dir}")
        
        # Check simulation type - only steady is supported
        sim_cfg = self.config.simulation_config
        sim_type = sim_cfg.get("simulation_type", "steady")
        if sim_type != "steady":
            raise NotImplementedError(
                f"Simulation type '{sim_type}' is not yet implemented. "
                "Only 'steady' simulation is currently supported."
            )
        
        # Create run directory with timestamp
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        case_name = self.case_dir.name
        run_dir = self.log_dir / f"{timestamp}_{case_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        
        # Create log file for all outputs (dctwin + OpenFOAM)
        log_file = run_dir / f"{timestamp}_{case_name}.log"
        self._current_log_file = log_file
        
        # Add loguru file handler to capture all dctwin logs
        # Remove any previous file handler for this session
        if hasattr(self, '_log_handler_id') and self._log_handler_id is not None:
            try:
                logger.remove(self._log_handler_id)
            except ValueError:
                pass  # Handler already removed
        
        # Add new file handler - captures all loguru output to the log file
        self._log_handler_id = logger.add(
            log_file,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
            level="DEBUG",
            mode="w",  # Overwrite mode to start fresh
        )
        
        logger.info(f"{'='*60}")
        logger.info(f"DCTwin CFD Simulation Log")
        logger.info(f"Case: {case_name}")
        logger.info(f"Started: {timestamp}")
        logger.info(f"Run Directory: {run_dir}")
        logger.info(f"{'='*60}")
        logger.info(f"Created run directory: {run_dir}")
        logger.info(f"Log file: {log_file}")
        
        # Copy mesh to run directory
        self._copy_mesh_to_run_dir(run_dir)
        
        # Fix cyclic patch matchTolerance if needed
        boundary_path = run_dir / "constant" / "polyMesh" / "boundary"
        self._fix_cyclic_match_tolerance(boundary_path)
        
        # Read boundary patches
        patches = self._read_boundary_patches(boundary_path)
        logger.info(f"Found {len(patches)} patches in boundary file")
        
        # Generate field files (0 folder)
        self.field_generator.generate_fields(run_dir, patches)

        # Reconcile boundary types with wall-function BCs (safety net)
        from .stlgen_runner import STLGenRunner
        STLGenRunner._reconcile_boundary_wall_types(boundary_path, run_dir)

        # Generate fvOptions with leakage zones
        self._generate_fv_options(run_dir)

        # Generate controlDict with probes
        self._generate_control_dict(run_dir)

        # Copy system files for solver
        self._copy_system_files_for_solver(run_dir)

        # Generate fvSolution (with default residual thresholds for CLI path)
        self._generate_fv_solution(run_dir / "system")

        # Create .foam file for ParaView
        foam_file = run_dir / f"{timestamp}_{case_name}.foam"
        foam_file.touch()
        
        # Check dry_run mode
        if sim_cfg.get("dry_run", False):
            logger.info("Dry run mode: skipping solver execution")
            return run_dir
        
        # Run solver
        solver = "buoyantBoussinesqSimpleFoam"
        n_procs = self.config.process_config.get("solve_process", 1)
        
        logger.info(f"Running solver: {solver} with {n_procs} processes")
        
        if n_procs > 1:
            self._run_parallel_solver(run_dir, solver, n_procs, log_file)
        else:
            self._run_serial_solver(run_dir, solver, log_file)
        
        # Read and save sensor results
        self._save_sensor_results(run_dir)
        
        # Export results to VTK format
        self._export_vtk(run_dir, log_file)
        
        logger.info(f"Solve complete")
        logger.info(f"Results in: {run_dir}")
        
        # Remove the log file handler after completion
        if hasattr(self, '_log_handler_id') and self._log_handler_id is not None:
            try:
                logger.remove(self._log_handler_id)
                self._log_handler_id = None
            except ValueError:
                pass
        
        return run_dir
    
    def _export_vtk(self, run_dir: Path, log_file: Path = None) -> None:
        """Export the last saved time step to VTK format using foamToVTK.
        
        This creates VTM files that can be opened in ParaView, containing
        all patches and cell zones with field values.
        """
        output_cfg = self.config.output_config
        if not output_cfg.get("export_vtk", True):
            logger.info("VTK export disabled in config, skipping")
            return
        
        # Find the last time step directory (excluding 0)
        time_dirs = []
        for item in run_dir.iterdir():
            if item.is_dir():
                try:
                    time_val = float(item.name)
                    if time_val > 0:
                        time_dirs.append((time_val, item))
                except ValueError:
                    continue
        
        if not time_dirs:
            logger.warning("No time step directories found, skipping VTK export")
            return
        
        # Get the last time step
        time_dirs.sort(key=lambda x: x[0])
        last_time, last_time_dir = time_dirs[-1]
        
        logger.info(f"Exporting time step {int(last_time)} to VTK format...")
        
        try:
            # Run foamToVTK for the last time step
            cmd = f"foamToVTK -time {int(last_time)}"
            self._run_cmd(cmd, run_dir, log_file)
            
            vtk_dir = run_dir / "VTK"
            if vtk_dir.exists():
                logger.info(f"VTK export complete: {vtk_dir}")
            else:
                logger.warning("VTK directory not created, foamToVTK may have failed silently")
                
        except Exception as e:
            logger.error(f"Failed to export VTK: {e}")
    
    def _generate_control_dict(self, run_dir: Path, monitor_patches: list | None = None) -> None:
        """Generate controlDict with simulation settings, probes, and optional monitoring."""
        sensors = self.config.extract_sensors()
        probes = [{"x": s["x"], "y": s["y"], "z": s["z"]} for s in sensors]
        generate_control_dict(
            run_dir,
            self.config.simulation_config,
            probes=probes,
            monitor_patches=monitor_patches,
        )

    def _generate_fv_solution(self, system_dir: Path, residual_control: dict | None = None,
                              pref_point: tuple | None = None) -> None:
        """Generate fvSolution from Jinja2 template with user-specified residual thresholds."""
        if residual_control is None:
            sim_cfg = self.config.simulation_config
            residual_control = sim_cfg.get("residualControl", sim_cfg.get("residual_control", {}))
        generate_fv_solution(system_dir, residual_control=residual_control, pref_point=pref_point)

    def _is_perforated_baffle_patch(self, patch_name: str) -> bool:
        """Check if a patch is a perforated baffle (opening_*_master or opening_*_slave)."""
        patch_lower = patch_name.lower()
        return ("opening_" in patch_lower and 
                (patch_lower.endswith("_master") or patch_lower.endswith("_slave")))
    
    def _fix_cyclic_match_tolerance(self, boundary_path: Path) -> None:
        """Fix cyclic patch issues for baffles created by createBaffles.

        createBaffles generates cyclic master/slave pairs with
        ``transform unknown`` which prevents GAMG from correctly
        agglomerating across the coupled faces.  This causes the
        pressure solver to diverge when baffles split the mesh into
        disconnected regions (e.g. raised floor / suspended ceiling).

        This method (idempotent — safe to call multiple times):
        1. Replaces ``transform unknown`` with ``transform coincidentFullMatch``
        2. Removes inGroups(wall) from cyclic patches only
        3. Adds matchTolerance to cyclic patches (if not already present after neighbourPatch)
        """
        fix_cyclic_match_tolerance(boundary_path)

    def _save_sensor_results(self, run_dir: Path) -> None:
        """Read and save sensor results from probe output."""
        probes_dir = run_dir / "postProcessing" / "probes" / "0"
        
        if not probes_dir.exists():
            logger.warning("Probes output directory not found, skipping sensor results")
            return
        
        sensors = self.config.extract_sensors()
        if not sensors:
            logger.info("No sensors defined, skipping sensor results")
            return
        
        results = {"T": {}, "U": {}, "p": {}, "p_rgh": {}}
        
        # Read temperature probe data
        t_file = probes_dir / "T"
        if t_file.exists():
            with open(t_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) > len(sensors):
                        for idx, sensor in enumerate(sensors):
                            # Temperature in Kelvin, convert to Celsius
                            temp_k = float(parts[idx + 1])
                            results["T"][sensor["id"]] = round(temp_k - 273.15, 2)
        
        # Save results to JSON
        output_cfg = self.config.output_config
        if output_cfg.get("save_simulation_results", True):
            results_path = run_dir / "simulation_sensor_results.json"
            with open(results_path, "w", encoding="utf-8") as f:
                json.dump(results, f, indent=4)
            logger.info(f"Saved sensor results to: {results_path}")

    def run_simulation(self) -> Path:
        """
        Run the complete CFD simulation.
        
        Returns:
            Path to the simulation run directory
        """
        logger.info("=== Full CFD Simulation Mode ===")
        
        # Generate mesh
        poly_mesh_dir = self.run_mesh_only()
        
        # Create run directory with timestamp
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        case_name = self.case_dir.name
        run_dir = self.log_dir / f"{timestamp}_{case_name}"
        run_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy mesh to run directory
        self._copy_mesh_to_run_dir(run_dir)
        
        # Read boundary patches
        boundary_path = run_dir / "constant" / "polyMesh" / "boundary"
        patches = self._read_boundary_patches(boundary_path)
        
        # Generate field files
        self.field_generator.generate_fields(run_dir, patches)

        # Reconcile boundary types with wall-function BCs (safety net)
        from .stlgen_runner import STLGenRunner
        STLGenRunner._reconcile_boundary_wall_types(boundary_path, run_dir)

        # Generate fvOptions with leakage zones
        self._generate_fv_options(run_dir)

        # Generate controlDict
        self._generate_control_dict(run_dir)

        # Copy system files
        self._copy_system_files_for_solver(run_dir)

        # Generate fvSolution (with default residual thresholds for CLI path)
        self._generate_fv_solution(run_dir / "system")

        # Create .foam file for ParaView
        foam_file = run_dir / f"{timestamp}_{case_name}.foam"
        foam_file.touch()
        
        # Check dry_run mode
        sim_cfg = self.config.simulation_config
        if sim_cfg.get("dry_run", False):
            logger.info("Dry run mode: skipping solver execution")
            return run_dir
        
        # Run solver
        solver = "buoyantBoussinesqSimpleFoam"
        n_procs = self.config.process_config.get("solve_process", 1)
        
        if n_procs > 1:
            self._run_parallel_solver(run_dir, solver, n_procs)
        else:
            self._run_serial_solver(run_dir, solver)
        
        # Save sensor results
        self._save_sensor_results(run_dir)
        
        logger.info(f"Simulation complete")
        logger.info(f"Results in: {run_dir}")
        
        return run_dir
    
    def _copy_mesh_to_run_dir(self, run_dir: Path) -> None:
        """Copy mesh files to the run directory."""
        mesh_dir = self.config.mesh_cache_dir
        
        # Copy constant directory
        src_constant = mesh_dir / "constant"
        dst_constant = run_dir / "constant"
        if src_constant.exists():
            shutil.copytree(src_constant, dst_constant, dirs_exist_ok=True)
        
        # Copy system directory  
        src_system = mesh_dir / "system"
        dst_system = run_dir / "system"
        if src_system.exists():
            shutil.copytree(src_system, dst_system, dirs_exist_ok=True)
    
    def _generate_fv_options(self, run_dir: Path) -> None:
        """Generate fvOptions with leakage zone porous media settings."""
        leakage_zones = self.geometry_manager.get_leakage_fvoptions_config()
        generate_fv_options(run_dir, leakage_zones)
    
    def _copy_system_files_for_solver(self, run_dir: Path) -> None:
        """Copy additional system files needed for the solver."""
        physical_properties = self.config.simulation_config.get("physical_properties", {})
        copy_system_files_for_solver(run_dir, physical_properties=physical_properties)
    
    def _generate_thermophysical_properties(self, constant_dir: Path) -> None:
        """Generate thermophysicalProperties using Jinja2 template."""
        physical_properties = self.config.simulation_config.get("physical_properties", {})
        generate_thermophysical_properties(constant_dir, physical_properties)
    
    def _run_serial_solver(self, run_dir: Path, solver: str, log_file: Path = None) -> None:
        """Run solver in serial mode with streaming output."""
        logger.info(f"Running {solver} in serial mode...")
        self._run_cmd_streaming(solver, run_dir, log_file)
    
    def _run_parallel_solver(self, run_dir: Path, solver: str, n_procs: int, log_file: Path = None) -> None:
        """Run solver in parallel mode."""
        logger.info(f"Running {solver} with {n_procs} processes...")
        
        # Generate decomposeParDict
        self._generate_decompose_par_dict(run_dir, n_procs)
        
        # Decompose (short command, use normal run)
        self._run_cmd("decomposePar", run_dir, log_file)
        
        # Copy constant files to processor directories (OpenFOAM 2406 requires this)
        self._copy_constant_files_to_processors(run_dir, n_procs)
        
        # Run parallel solver with STREAMING output (allows real-time log viewing)
        self._run_cmd_streaming(f"mpirun -np {n_procs} {solver} -parallel", run_dir, log_file)
        
        # Reconstruct (short command, use normal run)
        self._run_cmd("reconstructPar", run_dir, log_file)
    
    def _copy_constant_files_to_processors(self, run_dir: Path, n_procs: int) -> None:
        """Copy constant configuration files to each processor directory.
        
        OpenFOAM 2406 requires configuration files in each processor's constant directory
        for parallel execution.
        """
        constant_dir = run_dir / "constant"
        constant_files = [
            "thermophysicalProperties",
            "turbulenceProperties", 
            "transportProperties",
            "g",
        ]
        
        for i in range(n_procs):
            proc_constant_dir = run_dir / f"processor{i}" / "constant"
            proc_constant_dir.mkdir(parents=True, exist_ok=True)
            
            for file_name in constant_files:
                src = constant_dir / file_name
                dst = proc_constant_dir / file_name
                if src.exists() and not dst.exists():
                    shutil.copy(src, dst)
    
    def _generate_decompose_par_dict(self, run_dir: Path, n_procs: int) -> None:
        """Generate decomposeParDict."""
        generate_decompose_par_dict(run_dir, n_procs)
    
    def _read_boundary_patches(self, boundary_path: Path) -> List[Tuple[str, str]]:
        """Read boundary patches from the boundary file."""
        return read_boundary_patches(boundary_path)
    
    def _run_cmd(self, command: str, workdir: Path, log_file: Path = None) -> None:
        """Run a command in the specified directory.
        
        Args:
            command: The command to run.
            workdir: Working directory for the command.
            log_file: Optional log file path to append stdout/stderr.
        """
        logger.info(f"Running: {command}")

        # Source OpenFOAM bashrc so shared libraries and PATH are available
        _FOAM_BASHRC = "/usr/lib/openfoam/openfoam2406/etc/bashrc"
        shell_cmd = f'source "{_FOAM_BASHRC}" && {command}'

        result = subprocess.run(
            shell_cmd,
            shell=True,
            executable="/bin/bash",
            cwd=workdir,
            capture_output=True,
            text=True,
        )
        
        # Log command output via logger (goes to both console and log file)
        logger.debug(f"{'='*60}")
        logger.debug(f"Command: {command}")
        logger.debug(f"Working Directory: {workdir}")
        logger.debug(f"Exit Code: {result.returncode}")
        
        # Write detailed output to log file if provided
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[OpenFOAM] Command: {command}\n")
                f.write(f"Working Directory: {workdir}\n")
                f.write(f"{'='*60}\n")
                if result.stdout:
                    f.write(f"STDOUT:\n{result.stdout}\n")
                if result.stderr:
                    f.write(f"STDERR:\n{result.stderr}\n")
                f.write(f"Exit Code: {result.returncode}\n")
        
        if result.returncode != 0:
            logger.error(f"Command failed: {command}")
            logger.error(f"STDERR: {result.stderr}")
            raise RuntimeError(f"Command failed: {command}")
    
    def _run_cmd_streaming(self, command: str, workdir: Path, log_file: Path = None) -> None:
        """Run a command with streaming output (for long-running processes like solvers).
        
        This method streams stdout/stderr in real-time to the log file,
        allowing users to monitor solver progress during execution.
        
        Args:
            command: The command to run.
            workdir: Working directory for the command.
            log_file: Optional log file path to stream stdout/stderr.
        """
        logger.info(f"Running (streaming): {command}")
        
        # Write header to log file
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{'='*60}\n")
                f.write(f"[OpenFOAM Solver] Command: {command}\n")
                f.write(f"Working Directory: {workdir}\n")
                f.write(f"{'='*60}\n")
                f.flush()
        
        # Source OpenFOAM bashrc so shared libraries are on LD_LIBRARY_PATH
        _FOAM_BASHRC = "/usr/lib/openfoam/openfoam2406/etc/bashrc"
        wrapped = f'source "{_FOAM_BASHRC}" && {command}'

        # Use Popen for streaming output
        process = subprocess.Popen(
            wrapped,
            shell=True,
            executable="/bin/bash",
            cwd=workdir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            text=True,
            bufsize=1,  # Line buffered
        )
        
        # Stream output line by line
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                for line in process.stdout:
                    f.write(line)
                    f.flush()  # Flush immediately for real-time viewing
        else:
            # If no log file, just consume the output
            for line in process.stdout:
                pass
        
        # Wait for process to complete
        return_code = process.wait()
        
        # Write exit code to log
        if log_file:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\nExit Code: {return_code}\n")
                f.flush()
        
        logger.debug(f"Command completed with exit code: {return_code}")
        
        if return_code != 0:
            logger.error(f"Command failed: {command}")
            raise RuntimeError(f"Command failed with exit code {return_code}: {command}")
