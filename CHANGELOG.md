# Changelog

All notable changes to DCTwin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [3.1.0] - 2026-05-01

### Changed
- Desensitized case names for open-source release (triangle_room, slab_floor, rack_leakage, standard_dc, conference_demo)
- Switched license from Apache-2.0 to AGPL-3.0-or-later
- Replaced `cfd_export.json` manifests with `.cfdrun` format
- Simplified `setup_env.sh` for robust conda + Poetry installation

### Added
- MkDocs documentation (installation, quick start, case overview, architecture, CLI reference)
- Contributor License Agreement (CLA.md)
- Comprehensive test suite (63 tests covering imports, CLI, case data, docs accuracy)

### Removed
- Release-excluded case assets and examples

## [3.0.0] - 2026-04-01

### Added
- Open-source release of the DCTwin CFD simulation framework
- Automated pipeline: `.cfdrun` manifest + STL → mesh → solve → post-processing
- Support for steady-state RANS k-epsilon (buoyantBoussinesqSimpleFoam)
- U-slot server positioning with automatic inlet/outlet face generation
- Air leakage modeling with Darcy-Forchheimer porous media
- Raised floor with perforated tile openings
- Cold/hot aisle containment support
- 5 example cases (triangle_room, slab_floor, rack_leakage, standard_dc, conference_demo)
