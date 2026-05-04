#!/usr/bin/env python3
"""
Run OpenFOAM CFD simulation for data center thermal analysis.

Usage:
    python -m dctwin.runner.run_cfd --from-cfdrun <path/to/<basename>.cfdrun>
    python -m dctwin.runner.run_cfd --from-cfdrun <path> --mesh-only
    python -m dctwin.runner.run_cfd --from-cfdrun <path> --solve-only

The .cfdrun manifest is a JSON file produced by the STL UI via
`/api/browser/export-cfdrun`; it sits at `<case>/model/<basename>.cfdrun`.
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
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--from-cfdrun",
        type=str,
        required=True,
        metavar="CFDRUN_PATH",
        help="Path to <basename>.cfdrun from STLGeneratorForOpenFoam. "
             "Uses browser-exported STL files in Mesh/constant/triSurface/ directly.",
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

    return parser.parse_args()


def main() -> None:
    """Main entry point."""
    args = parse_args()

    from dctwin.runner.stlgen_runner import STLGenRunner

    cfdrun_path = Path(args.from_cfdrun).resolve()
    if not cfdrun_path.exists():
        logger.error(f"cfdrun file not found: {cfdrun_path}")
        sys.exit(1)

    # <basename>.cfdrun lives in <case>/model/ — case_dir is the parent
    case_dir = cfdrun_path.parent
    if case_dir.name == "model":
        case_dir = case_dir.parent

    logger.info(f"STLGen pipeline: {cfdrun_path}")
    logger.info(f"Case directory: {case_dir}")

    try:
        runner = STLGenRunner(case_dir, cfdrun_path)
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


if __name__ == "__main__":
    main()
