import math
import shutil
from typing import List
from venv import logger

from dclib.room import Room
from dctwin.utils import (
    template_env,
    template_dir,
    config,
)
from pathlib import Path
from typing import Optional, Union
import numpy as np
from dclib.construction.entities import Box
from dclib.models.geometry import Vertex


def generate_control_dict(
    probes: Optional[List[Vertex]] = None,
    steady=True,
    delta_t: Union[int, float] = 1,
    write_interval: int = 100,
    end_time: int = 500,
    process_num: int = 1,
    is_gpu: bool = False,
) -> None:
    if steady is False:
        delta_t = float("1e-5")
    if probes is None:
        probes = list()

    # Ensure write_interval doesn't exceed end_time to guarantee final results are written
    # For steady simulations (writeControl=timeStep), if write_interval > end_time,
    # no time directories will be created, causing post-processing to fail
    if steady and write_interval > end_time:
        logger.warning(
            f"write_interval ({write_interval}) > end_time ({end_time}), "
            f"adjusting write_interval to {end_time} to ensure results are written"
        )
        write_interval = end_time

    system_folder = "steady"
    if steady is False:
        system_folder = "transient"
    shutil.copy(
        Path(template_dir, f"foam/template/system/{system_folder}/fvSchemes"),
        Path(config.cfd.case_dir, "system/fvSchemes"),
    )
    if is_gpu:
        shutil.copy(
            Path(template_dir, f"foam/template/system/{system_folder}/fvSolution_gpu"),
            Path(config.cfd.case_dir, "system/fvSolution"),
        )
    else:
        shutil.copy(
            Path(template_dir, f"foam/template/system/{system_folder}/fvSolution_cpu"),
            Path(config.cfd.case_dir, "system/fvSolution"),
        )
    with open(Path(config.cfd.case_dir, "system/controlDict"), "w") as f:
        f.write(
            template_env.get_template(
                f"foam/template/system/{system_folder}/controlDict.j2"
            ).render(
                delta_t=delta_t,
                write_interval=write_interval,
                end_time=end_time,
                probes=probes,
            )
        )
    if process_num > 1:
        process_num = int(process_num)
        if process_num > 64:
            logger.error("The number of processes should be less than 64."
                         "But the number of processes is %d" % process_num)
            exit(1)
        with open(Path(config.cfd.case_dir, "system/decomposeParDict"), "w") as f:
            f.write(
                template_env.get_template("foam/template/system/decomposeParDict.j2").render(
                    process_num=process_num
                )
            )


def init_foam(is_gpu: bool = False, process_num: int = 1) -> None:
    Path(config.cfd.case_dir, "0").mkdir(parents=True, exist_ok=True)
    Path(config.cfd.case_dir, "constant").mkdir(parents=True, exist_ok=True)
    Path(config.cfd.case_dir, "system").mkdir(parents=True, exist_ok=True)
    # Create .foam file with timestamp_datasetname format for ParaView
    foam_filename = f"{config.LOG_DIR.name}.foam"
    Path(config.cfd.case_dir, foam_filename).touch(exist_ok=True)

    shutil.copy(
        Path(template_dir, "foam/template/constant/g"), Path(config.cfd.case_dir, "constant/g")
    )
    shutil.copy(
        Path(template_dir, "foam/template/constant/thermophysicalProperties"),
        Path(config.cfd.case_dir, "constant/thermophysicalProperties"),
    )
    shutil.copy(
        Path(template_dir, "foam/template/constant/transportProperties"),
        Path(config.cfd.case_dir, "constant/transportProperties"),
    )

    with open(Path(config.cfd.case_dir, "constant/turbulenceProperties"), "w") as f:
        f.write(
            template_env.get_template("foam/template/constant/turbulenceProperties.j2").render(
                turbulence=("on" if config.cfd.SOLVER_TURBULENCE else "off")
            )
        )

    shutil.copy(
        Path(template_dir, "foam/template/system/steady/fvSchemes"),
        Path(config.cfd.case_dir, "system/fvSchemes"),
    )

    if is_gpu:
        shutil.copy(
            Path(template_dir, f"foam/template/system/steady/fvSolution_gpu"),
            Path(config.cfd.case_dir, "system/fvSolution"),
        )
    else:
        shutil.copy(
            Path(template_dir, f"foam/template/system/steady/fvSolution_cpu"),
            Path(config.cfd.case_dir, "system/fvSolution"),
        )

    generate_control_dict()


def generate_block_dict(room: Room) -> None:
    """Generate the blockMeshDict file"""
    min_z = 0
    v_min, v_max = Vertex(x=0, y=0, z=min_z), Vertex(x=0, y=0, z=room.geometry.height)
    for vertex in room.geometry.plane:
        if vertex.x < v_min.x:
            v_min.x = vertex.x
        if vertex.y < v_min.y:
            v_min.y = vertex.y
        if vertex.x > v_max.x:
            v_max.x = vertex.x
        if vertex.y > v_max.y:
            v_max.y = vertex.y

    base_size = config.cfd.base_size
    x_cells = math.ceil((v_max.x - v_min.x) / base_size)
    if base_size * x_cells != (v_max.x - v_min.x):
        v_max.x = x_cells * base_size - v_min.x
    y_cells = math.ceil((v_max.y - v_min.y) / base_size)
    if base_size * y_cells != (v_max.y - v_min.y):
        v_max.y = y_cells * base_size - v_min.y
    z_cells = math.ceil((v_max.z - v_min.z) / base_size)
    if base_size * z_cells != (v_max.z - v_min.z):
        v_max.z = z_cells * base_size - v_min.z

    v_min.x -= 0.1
    v_min.y -= 0.1
    v_min.z -= 0.1
    v_max.x += 0.1
    v_max.y += 0.1
    v_max.z += 0.1

    template = template_env.get_template("foam/template/mesh/blockMeshDict.j2")
    with open(Path(config.cfd.case_dir, "system/blockMeshDict"), "w") as f:
        f.write(
            template.render(
                v_max=v_max,
                v_min=v_min,
                x_cells=x_cells,
                y_cells=y_cells,
                z_cells=z_cells,
            )
        )


def read_internal_field(filename: Union[str, Path]):
    with open(filename) as f:
        started = False
        for line in f:
            if line.strip().startswith("internalField"):
                started = True
                yield line[len("internalField") :]
            elif started:
                if ";" not in line:
                    yield line
                else:
                    return
            else:
                continue


def is_closed(box: Box):
    if (
        box.geometry.faces.bottom and
        box.geometry.faces.top and
        box.geometry.faces.left and
        box.geometry.faces.right and
        box.geometry.faces.front and
        box.geometry.faces.rear
    ):
        return True
    return False


def round_to_base(value: float, base: float, mode: str = "round") -> float:
    """
    Adjust the value to the nearest base, with the option to use rounding,
    floor, or ceil rounding modes.

    Parameters:
    - value: The float value to adjust.
    - base: The base to which the value should be adjusted.
    - mode: The mode of adjustment, which can be 'round', 'floor', or 'ceil'.

    Returns:
    - The adjusted value rounded to three decimal places.
    """
    if mode == "round":
        adjusted_value = base * np.round(value / base)
    elif mode == "floor":
        adjusted_value = base * np.floor(value / base)
    elif mode == "ceil":
        adjusted_value = base * np.ceil(value / base)
    else:
        raise ValueError("Mode should be 'round', 'floor', or 'ceil'.")

    return round(adjusted_value, 3)


def rotate_vertex(
    origin: Vertex,
    vertex_to_be_rotated: Vertex,
    angle: float
):
    qx = origin.x + np.cos(angle) * (vertex_to_be_rotated.x - origin.x) - np.sin(angle) * (vertex_to_be_rotated.y - origin.y)
    qy = origin.y + np.sin(angle) * (vertex_to_be_rotated.x - origin.x) + np.cos(angle) * (vertex_to_be_rotated.y - origin.y)
    return Vertex(
        x=qx,
        y=qy,
        z=vertex_to_be_rotated.z
    )


def rotate_rectangular(
    abs_vertex_1: Vertex,
    abs_vertex_2: Vertex,
    abs_vertex_3: Vertex,
    abs_vertex_4: Vertex,
    angle: float,
):
    angle = np.deg2rad(angle)
    rotated_vertex_1 = rotate_vertex(abs_vertex_1, abs_vertex_1, angle)
    rotated_vertex_2 = rotate_vertex(abs_vertex_1, abs_vertex_2, angle)
    rotated_vertex_3 = rotate_vertex(abs_vertex_1, abs_vertex_3, angle)
    rotated_vertex_4 = rotate_vertex(abs_vertex_1, abs_vertex_4, angle)

    # find the minimum and maximum x, y, and z coordinates of the ACU
    rotated_vertices = [rotated_vertex_1, rotated_vertex_2, rotated_vertex_3, rotated_vertex_4]
    min_x = min([v.x for v in rotated_vertices])
    min_y = min([v.y for v in rotated_vertices])
    min_z = min([v.z for v in rotated_vertices])

    max_x = max([v.x for v in rotated_vertices])
    max_y = max([v.y for v in rotated_vertices])
    max_z = max([v.z for v in rotated_vertices])

    v_min = Vertex(x=min_x, y=min_y, z=min_z)
    v_max = Vertex(x=max_x, y=max_y, z=max_z)

    return v_min, v_max
