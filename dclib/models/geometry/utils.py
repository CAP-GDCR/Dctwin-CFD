import math
from typing import Tuple


def euclidean_distance(
    loc_1: Tuple[float, float, float],
    loc_2: Tuple[float, float, float]
) -> float:
    """
    Calculate the Euclidean distance between two points in 3D space.
    param: loc_1: Tuple[float, float, float] (x, y, z), the first point.
    param: loc_2: Tuple[float, float, float] (x, y, z), the second point.
    return: float, the Euclidean distance between the two points.
    """
    x_1, y_1, z_1 = loc_1
    x_2, y_2, z_2 = loc_2
    return math.sqrt((x_1 - x_2) ** 2 + (y_1 - y_2) ** 2 + (z_1 - z_2) ** 2)


def rotate(
    origin: Tuple[float, float],
    point: Tuple[float, float],
    angle: int
) -> Tuple[float, float]:
    """
    Rotate a point counterclockwise by a given angle around a given origin.
    param: origin: Tuple[float, float] (x, y), the origin of the rotation.
    param: point: Tuple[float, float] (x, y), the point to be rotated.
    param: angle: int, the angle of rotation.
    return: Tuple[float, float] (x, y), the rotated point.
    """
    ox, oy = origin
    px, py = point
    _angle = angle / 180 * math.pi
    qx = ox + math.cos(_angle) * (px - ox) - math.sin(_angle) * (py - oy)
    qy = oy + math.sin(_angle) * (px - ox) + math.cos(_angle) * (py - oy)
    return qx, qy
