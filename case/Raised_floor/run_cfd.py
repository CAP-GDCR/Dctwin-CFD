#!/usr/bin/env python3
"""
Run OpenFOAM CFD simulation for data center thermal analysis.

Simplified entry script using dctwin.runner module.

Usage:
    python run_cfd.py                    # Run full simulation
    python run_cfd.py --stl-only         # Generate STL files only
    python run_cfd.py --mesh-only        # Generate mesh only
    python run_cfd.py --config my.toml   # Use custom config file
"""
import argparse
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
    )
    
    parser.add_argument(
        "--config", type=str, default="config.toml",
        help="Configuration file (default: config.toml)",
    )
    parser.add_argument(
        "--stl-only", action="store_true",
        help="Generate STL files only",
    )
    parser.add_argument(
        "--mesh-only", action="store_true",
        help="Generate mesh only (skip solver)",
    )
    parser.add_argument(
        "--solve-only", action="store_true",
        help="Run solver only (requires existing mesh in Mesh/constant/polyMesh)",
    )
    
    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()
    
    from dctwin.runner import CaseRunner
    
    case_dir = Path(__file__).parent
    
    logger.info(f"Case directory: {case_dir}")
    logger.info(f"Configuration: {args.config}")
    
    try:
        runner = CaseRunner(case_dir, config_file=args.config)
        
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
            logger.info(f"Solve complete (--solve-only mode)")
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
