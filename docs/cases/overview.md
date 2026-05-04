# Tutorial Cases

DCTwin ships with 5 example cases covering a range of data center configurations. Each case includes a `.cfdrun` manifest and pre-exported STL geometry files, ready to run.

## Case Summary

| Case | Racks | ACUs | Key Feature | Approx. Runtime |
|------|-------|------|-------------|-----------------|
| `triangle_room` | 2 | 1 | Triangular room geometry | ~2 min |
| `slab_floor` | 8 | 2 | Raised floor with perforated tiles | ~5 min |
| `rack_leakage` | 4 | 2 | Air leakage through unoccupied U-slots | ~5 min |
| `standard_dc` | 24 | 3 | Standard data center topology | ~3 min |
| `conference_demo` | 50 | 6 | Large case with perforated blanking panels | ~15 min |

## Running a Case

```bash
# Replace <case_name> with any case from the table above
python -m dctwin.runner.run_cfd --from-cfdrun case/<case_name>/model/<case_name>.cfdrun
```

## Case Details

### triangle_room

A small data center room with a triangular floor plan, 2 server racks, and 1 ACU (Air Cooling Unit). This is the simplest case and runs the fastest — ideal for verifying your installation.

### slab_floor

A single-slab data center with raised floor and perforated floor tiles. 8 server racks with mixed equipment (fan-servers and non-fan equipment) and 2 ACUs. Demonstrates the raised floor plenum airflow modeling.

### rack_leakage

Models air leakage through unoccupied U-slots in server racks using the Darcy-Forchheimer porous media approach. 4 racks with 2 ACUs and partially filled equipment — empty slots create leakage paths that bypass the intended front-to-back airflow.

### standard_dc

A standard data center topology with 24 server racks arranged in hot-aisle/cold-aisle configuration, served by 3 ACUs. This is the most representative real-world configuration.

### conference_demo

A large-scale demonstration case with 50 server racks and 6 ACUs, featuring perforated blanking panels and multiple rack configurations. Uses the `upwind` discretization scheme for enhanced numerical stability on complex geometries.
