"""
Shared OpenFOAM case setup helpers used by both legacy and STLGen runners.
"""
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from dctwin.utils import template_env


def _cfg_value(config: Dict[str, Any], snake_key: str, camel_key: str, default: Any) -> Any:
    """Read either snake_case or camelCase config keys."""
    if snake_key in config:
        return config.get(snake_key)
    if camel_key in config:
        return config.get(camel_key)
    return default


def read_boundary_patches(boundary_path: Path) -> List[Tuple[str, str]]:
    """Read boundary patches from an OpenFOAM boundary file."""
    if not boundary_path.exists():
        return []

    content = boundary_path.read_text(encoding="utf-8")
    patches = []
    pattern = r"([A-Za-z0-9_]+)\s*\{\s*type\s+([A-Za-z0-9_]+)\s*;"
    for name, patch_type in re.findall(pattern, content):
        patches.append((name, patch_type))
    return patches


def fix_cyclic_match_tolerance(boundary_path: Path) -> None:
    """Normalize cyclic patch metadata created by createBaffles."""
    if not boundary_path.exists():
        return

    content = boundary_path.read_text(encoding="utf-8")
    if "type            cyclic;" not in content:
        return

    lines = content.split("\n")
    new_lines = []
    modified = False
    in_cyclic_patch = False
    added_tolerance = False

    for i, line in enumerate(lines):
        if "type            cyclic;" in line:
            in_cyclic_patch = True
            added_tolerance = False
        if in_cyclic_patch and line.strip() == "}":
            in_cyclic_patch = False

        if in_cyclic_patch and "inGroups" in line and "wall" in line:
            modified = True
            continue

        if in_cyclic_patch and "transform       unknown;" in line:
            new_lines.append(
                line.replace("transform       unknown;", "transform       coincidentFullMatch;")
            )
            modified = True
            continue

        if in_cyclic_patch and "neighbourPatch" in line and not added_tolerance:
            new_lines.append(line)
            next_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if not next_line.startswith("matchTolerance"):
                new_lines.append("        matchTolerance  0.01;")
                modified = True
            added_tolerance = True
            continue

        if in_cyclic_patch and "matchTolerance" in line:
            if added_tolerance:
                modified = True
                continue

        new_lines.append(line)

    if modified:
        boundary_path.write_text("\n".join(new_lines), encoding="utf-8")
        logger.info("Fixed cyclic patches: transform→coincidentFullMatch, matchTolerance=0.01")


def generate_control_dict(
    run_dir: Path,
    simulation_config: Dict[str, Any],
    probes: Optional[List[Dict[str, float]]] = None,
    monitor_patches: Optional[List[str]] = None,
) -> None:
    """Generate controlDict with probes and optional surface monitoring."""
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    end_time = int(_cfg_value(simulation_config, "end_time", "endTime", 500) or 500)
    write_interval = int(_cfg_value(simulation_config, "write_interval", "writeInterval", 100) or 100)
    delta_t = 1

    if write_interval > end_time:
        logger.warning(
            f"write_interval ({write_interval}) > end_time ({end_time}), "
            f"adjusting write_interval to {end_time}"
        )
        write_interval = end_time

    probes = probes or []
    monitor_patches = monitor_patches or []

    surface_field_values = []
    for patch_name in monitor_patches:
        fo_name = "avgT_" + patch_name.replace(" ", "_")
        surface_field_values.append({"name": fo_name, "patch": patch_name})

    template = template_env.get_template("foam/template/system/steady/controlDict.j2")
    content = template.render(
        delta_t=delta_t,
        write_interval=write_interval,
        end_time=end_time,
        probes=probes,
        surface_field_values=surface_field_values,
    )
    (system_dir / "controlDict").write_text(content, encoding="utf-8")
    logger.info(
        f"Generated controlDict: endTime={end_time}, writeInterval={write_interval}, probes={len(probes)}"
    )


def generate_fv_solution(
    system_dir: Path,
    residual_control: Optional[Dict[str, Any]] = None,
    relaxation_factors: Optional[Dict[str, Any]] = None,
    n_non_ortho_correctors: int = 0,
    pref_point: Optional[Tuple[float, float, float]] = None,
) -> None:
    """Generate fvSolution with configurable residual thresholds and pRefPoint."""
    system_dir.mkdir(parents=True, exist_ok=True)

    residual_control = residual_control or {}
    res_p_rgh = residual_control.get("p_rgh", 1e-4)
    res_u = residual_control.get("U", 1e-5)
    res_t = residual_control.get("T", 1e-5)
    res_k_epsilon = residual_control.get("k_epsilon", 1e-4)

    relaxation_factors = relaxation_factors or {}
    relax_p = relaxation_factors.get("p_rgh", 0.3)
    relax_u = relaxation_factors.get("U", 0.7)
    relax_t = relaxation_factors.get("T", 0.7)
    relax_ke = relaxation_factors.get("k_epsilon", 0.5)

    use_pref_point = pref_point is not None
    if pref_point is None:
        pref_point = (0.0, 0.0, 0.0)
    px, py, pz = pref_point

    template = template_env.get_template("foam/template/system/steady/fvSolution_cpu.j2")
    content = template.render(
        res_p_rgh=res_p_rgh,
        res_U=res_u,
        res_T=res_t,
        res_k_epsilon=res_k_epsilon,
        relax_p_rgh=relax_p,
        relax_U=relax_u,
        relax_T=relax_t,
        relax_k_epsilon=relax_ke,
        n_non_ortho_correctors=n_non_ortho_correctors,
        use_pref_point=use_pref_point,
        pref_x=px,
        pref_y=py,
        pref_z=pz,
    )
    (system_dir / "fvSolution").write_text(content, encoding="utf-8")
    logger.info(
        f"Generated fvSolution: residualControl p_rgh={res_p_rgh}, "
        f"U={res_u}, T={res_t}, k/epsilon={res_k_epsilon}, "
        f"relaxation p_rgh={relax_p}, U={relax_u}, T={relax_t}, k/eps={relax_ke}, "
        f"nNonOrthCorr={n_non_ortho_correctors}, pRefPoint=({px}, {py}, {pz})"
    )


def generate_fv_schemes(
    system_dir: Path,
    schemes_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Generate fvSchemes from Jinja2 template with configurable discretization schemes."""
    system_dir.mkdir(parents=True, exist_ok=True)
    schemes_config = schemes_config or {}

    template = template_env.get_template("foam/template/system/steady/fvSchemes.j2")
    content = template.render(
        grad_scheme_default=schemes_config.get("gradSchemeDefault", "Gauss linear"),
        grad_scheme_U=schemes_config.get("gradSchemeU", ""),
        grad_scheme_k=schemes_config.get("gradSchemeK", ""),
        grad_scheme_epsilon=schemes_config.get("gradSchemeEpsilon", ""),
        div_scheme_U=schemes_config.get("divSchemeU", "bounded Gauss upwind"),
        div_scheme_T=schemes_config.get("divSchemeT", "bounded Gauss upwind"),
        div_scheme_k=schemes_config.get("divSchemeK", "bounded Gauss upwind"),
        div_scheme_epsilon=schemes_config.get("divSchemeEpsilon", "bounded Gauss upwind"),
        laplacian_scheme=schemes_config.get("laplacianScheme", "Gauss linear limited 0.5"),
        sn_grad_scheme=schemes_config.get("snGradScheme", "limited 0.5"),
    )
    (system_dir / "fvSchemes").write_text(content, encoding="utf-8")
    logger.info(
        f"Generated fvSchemes: div(U)={schemes_config.get('divSchemeU', 'upwind')}, "
        f"laplacian={schemes_config.get('laplacianScheme', 'limited 0.5')}"
    )


def generate_fv_options(run_dir: Path, leakage_zones: Dict[str, Any]) -> None:
    """Generate fvOptions with porous leakage zones."""
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    template = template_env.get_template("foam/template/system/steady/fvOptions.j2")
    with open(system_dir / "fvOptions", "w", encoding="utf-8") as f:
        f.write(template.render(perforated_openings={}, leakage_zones=leakage_zones))

    if leakage_zones:
        logger.info(f"Generated fvOptions with {len(leakage_zones)} leakage zones")


def generate_thermophysical_properties(constant_dir: Path, physical_properties: Optional[Dict[str, Any]]) -> None:
    """Generate thermophysicalProperties using the legacy template."""
    physical_properties = physical_properties or {}
    template = template_env.get_template("foam/template/constant/thermophysicalProperties.j2")
    content = template.render(
        mol_weight=physical_properties.get("mol_weight", 28.9),
        cp=physical_properties.get("cp", 1006),
        hf=physical_properties.get("hf", 0),
        mu=physical_properties.get("mu", "1.837e-05"),
        pr=physical_properties.get("pr", 0.707),
    )
    (constant_dir / "thermophysicalProperties").write_text(content, encoding="utf-8")


def _write_gravity_file(constant_dir: Path, gravity: Dict[str, Any]) -> None:
    """Write custom gravity file for STLGen-driven runs."""
    gx = gravity.get("x", 0)
    gy = gravity.get("y", 0)
    gz = gravity.get("z", -9.81)
    g_content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       uniformDimensionedVectorField;
    location    "constant";
    object      g;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

dimensions      [0 1 -2 0 0 0 0];
value           ({gx} {gy} {gz});

// ************************************************************************* //
"""
    (constant_dir / "g").write_text(g_content, encoding="utf-8")
    logger.info(f"Gravity: ({gx}, {gy}, {gz})")


def _write_transport_properties(constant_dir: Path, room_temperature_c: float) -> None:
    """Write transportProperties with TRef derived from room temperature."""
    t_ref_k = room_temperature_c + 273.15
    beta = 1.0 / t_ref_k
    tp_content = f"""\
/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v1912                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      transportProperties;
}}

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

transportModel Newtonian;

// Laminar viscosity (air @ 20°C)
nu              1.516e-05;

// Thermal expansion coefficient (1/TRef)
beta            {beta:.6e};

// Reference temperature ({room_temperature_c}°C)
TRef            {t_ref_k};

// Laminar Prandtl number (air @ 20°C)
Pr              0.71;

// Turbulent Prandtl number
Prt             0.85;

// ************************************************************************* //
"""
    (constant_dir / "transportProperties").write_text(tp_content, encoding="utf-8")
    logger.info(f"transportProperties: TRef={t_ref_k} K ({room_temperature_c}°C), beta={beta:.6e}")


def copy_system_files_for_solver(
    run_dir: Path,
    *,
    physical_properties: Optional[Dict[str, Any]] = None,
    gravity: Optional[Dict[str, Any]] = None,
    room_temperature_c: Optional[float] = None,
    schemes_config: Optional[Dict[str, Any]] = None,
) -> None:
    """Copy/generate constant and system files needed by the solver."""
    template_root = Path(__file__).resolve().parents[1] / "third_parties"
    constant_dir = run_dir / "constant"
    constant_dir.mkdir(parents=True, exist_ok=True)
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    template = template_env.get_template("foam/template/constant/turbulenceProperties.j2")
    turb_content = template.render(turbulence="on")
    (constant_dir / "turbulenceProperties").write_text(turb_content, encoding="utf-8")

    if room_temperature_c is None:
        trans_props = template_root / "foam" / "template" / "constant" / "transportProperties"
        if trans_props.exists():
            shutil.copy(trans_props, constant_dir / "transportProperties")
    else:
        _write_transport_properties(constant_dir, room_temperature_c)

    if gravity is None:
        g_file = template_root / "foam" / "template" / "constant" / "g"
        if g_file.exists():
            shutil.copy(g_file, constant_dir / "g")
    else:
        _write_gravity_file(constant_dir, gravity)

    generate_fv_schemes(system_dir, schemes_config)

    generate_thermophysical_properties(constant_dir, physical_properties)


def generate_decompose_par_dict(run_dir: Path, n_procs: int) -> None:
    """Generate decomposeParDict for parallel execution."""
    system_dir = run_dir / "system"
    system_dir.mkdir(parents=True, exist_ok=True)

    content = f"""/*--------------------------------*- C++ -*----------------------------------*\\
| =========                 |                                                 |
| \\\\      /  F ield         | OpenFOAM: The Open Source CFD Toolbox           |
|  \\\\    /   O peration     | Version:  v2406                                 |
|   \\\\  /    A nd           | Website:  www.openfoam.com                      |
|    \\\\/     M anipulation  |                                                 |
\\*---------------------------------------------------------------------------*/
FoamFile
{{
    version     2.0;
    format      ascii;
    class       dictionary;
    object      decomposeParDict;
}}
// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

numberOfSubdomains {n_procs};

method          scotch;

// ************************************************************************* //
"""
    (system_dir / "decomposeParDict").write_text(content, encoding="utf-8")
