# Changelog

All notable changes to DCTwin will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [3.0.0] - 2026-04-01

### Added
- Open-source release of the DCTwin CFD simulation framework
- Automated pipeline: JSON room layout -> STL -> mesh -> solve -> post-processing
- Support for steady-state RANS k-epsilon (buoyantBoussinesqSimpleFoam)
- U-slot server positioning with automatic inlet/outlet face generation
- Air leakage modeling with Darcy-Forchheimer porous media
- Raised floor with perforated tile openings
- Cold/hot aisle containment support
- 6 example cases (Base_coldAisleIso, Base_leakage, Alibaba-hongchou, GTC, STDCT, Slab)
- MkDocs documentation site
- Docker support for containerized deployment
