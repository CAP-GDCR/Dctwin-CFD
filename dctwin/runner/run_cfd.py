#!/usr/bin/env python3
"""
Run OpenFOAM CFD simulation for data center thermal analysis.

This is a simplified entry script that uses the dctwin.runner module
to handle the complete CFD workflow.

Usage:
    python run_cfd.py                    # Run full simulation
    python run_cfd.py --stl-only         # Generate STL files only
    python run_cfd.py --mesh-only        # Generate mesh only (skip solver)
    python run_cfd.py --config my.toml   # Use custom config file
"""
import argparse
import os
import sys
from pathlib import Path

from loguru import logger

# Setup logging
logger.remove()
logger.add(sys.stderr, level="INFO", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Run OpenFOAM CFD simulation for data center",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    
    parser.add_argument(
        "--config",
        type=str,
        default="config.toml",
        help="Path to configuration file (default: config.toml)",
    )
    
    parser.add_argument(
        "--stl-only",
        action="store_true",
        help="Generate STL geometry files only",
    )
    
    parser.add_argument(
        "--mesh-only",
        action="store_true",
        help="Generate mesh only (skip solver)",
    )

    parser.add_argument(
        "--solve-only",
        action="store_true",
        help="Run solver only (requires existing mesh)",
    )

    parser.add_argument(
        "--from-cfdproj",
        type=str,
        default=None,
        help="Path to cfd_export.json from STLGeneratorForOpenFoam. "
             "Converts to room.json and sets up case directory before running.",
    )

    parser.add_argument(
        "--from-stlgen",
        type=str,
        default=None,
        metavar="CFD_EXPORT_JSON",
        help="Path to cfd_export.json from STLGeneratorForOpenFoam (direct STL pipeline). "
             "Uses browser-exported STL files in Mesh/constant/triSurface/ directly — "
             "dctwin does NOT regenerate geometry from JSON parametric descriptions.",
    )

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    # Handle --from-stlgen: direct STL pipeline (browser-exported STLs bypass dctwin geometry)
    if args.from_stlgen:
        from dctwin.runner.stlgen_runner import STLGenRunner

        cfdproj_path = Path(args.from_stlgen).resolve()
        if not cfdproj_path.exists():
            logger.error(f"CFD export file not found: {cfdproj_path}")
            sys.exit(1)

        # cfd_export.json lives in <case>/model/ — case_dir is the parent
        case_dir = cfdproj_path.parent
        if case_dir.name == "model":
            case_dir = case_dir.parent

        logger.info(f"STLGen pipeline: {cfdproj_path}")
        logger.info(f"Case directory: {case_dir}")

        try:
            runner = STLGenRunner(case_dir, cfdproj_path)
            if args.mesh_only:
                runner.run_mesh_only()
            elif args.solve_only:
                runner.run_solve_only()
            else:
                runner.run_simulation()
        except Exception as e:
            logger.error(f"STLGen runner failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        return

    # Handle --from-cfdproj: convert bridge JSON to room.json first
    if args.from_cfdproj:
        from dctwin.runner.cfdproj_importer import CFDProjectImporter

        cfdproj_path = Path(args.from_cfdproj).resolve()
        if not cfdproj_path.exists():
            logger.error(f"CFD project file not found: {cfdproj_path}")
            sys.exit(1)

        # Determine case directory from the cfdproj location
        case_dir = cfdproj_path.parent
        if case_dir.name == "model":
            # canonical: cfd_export.json lives in <case>/model/
            case_dir = case_dir.parent
        elif case_dir.name.endswith("_cfd"):
            case_dir = case_dir.parent / case_dir.name.replace("_cfd", "")

        logger.info(f"Importing CFD project: {cfdproj_path}")
        logger.info(f"Case directory: {case_dir}")

        try:
            importer = CFDProjectImporter(cfdproj_path, case_dir)
            room_json_path = importer.import_and_convert()
            logger.info(f"Import complete. room.json at: {room_json_path}")
            logger.info(f"Case setup at: {case_dir}")
        except Exception as e:
            logger.error(f"Import failed: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

        # If no pipeline flag given, just import and exit (legacy behaviour)
        if not (args.stl_only or args.mesh_only or args.solve_only):
            logger.info(f"To run simulation: cd {case_dir} && python -m dctwin.runner.run_cfd")
            return

        # Otherwise fall through and run the requested pipeline stage from case_dir
        os.chdir(case_dir)

    # Get case directory (current working directory)
    case_dir = Path.cwd()

    logger.info(f"Case directory: {case_dir}")
    logger.info(f"Configuration: {args.config}")

    # Create case runner
    try:
        from dctwin.runner.case import CaseRunner

        runner = CaseRunner(case_dir, config_file=args.config)
    except FileNotFoundError as e:
        logger.error(f"Configuration error: {e}")
        sys.exit(1)

    # Execute appropriate workflow
    try:
        if args.stl_only:
            stl_dir = runner.run_stl_only()
            logger.info(f"STL generation complete (--stl-only mode)")
            logger.info(f"STL files in: {stl_dir}")

        elif args.mesh_only:
            poly_mesh_dir = runner.run_mesh_only()
            logger.info(f"Mesh generation complete (--mesh-only mode)")
            logger.info(f"polyMesh in: {poly_mesh_dir}")

        elif args.solve_only:
            run_dir = runner.run_solve_only()
            logger.info(f"Solver complete (--solve-only mode)")
            logger.info(f"Results in: {run_dir}")

        else:
            run_dir = runner.run_simulation()
            logger.info(f"Simulation complete")
            logger.info(f"Results in: {run_dir}")

    except Exception as e:
        logger.error(f"Execution failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
