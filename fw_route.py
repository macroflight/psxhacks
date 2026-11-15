"""Route geometry for frankenweather's route-based zone planning (--route-zones).

The FMC route as a list of (lat, lon) points; where the aircraft is
relative to it (cross-track and along-track distance, the leg's course),
and the point a given distance further along it, through its turns.
Great-circle formulas on a sphere (Earth radius in nm), close enough for
placing weather zones. Pure functions only.
"""
import math
from dataclasses import dataclass
from typing import Optional

EARTH_NM = 3440.065

# On route: within ON_ROUTE_XTK_NM of a leg and tracking within ON_ROUTE_TRK_DEG
# of it for ON_ROUTE_ENTER_S; off route after OFF_ROUTE_EXIT_S beyond it
ON_ROUTE_XTK_NM = 10.0
ON_ROUTE_TRK_DEG = 30.0
ON_ROUTE_ENTER_S = 120.0
OFF_ROUTE_EXIT_S = 180.0
# Off the route but within this of it (a weather deviation): zones still count
# as behind by the distance along the route, so the deviation moves none
OFF_ROUTE_KEEP_NM = 60.0
# Near the destination: maneuvering once within DEST_MANEUVER_NM and more than
# DEST_XTK_NM or DEST_TRK_DEG off the route (vectors), until landing or a new route
DEST_MANEUVER_NM = 60.0
DEST_XTK_NM = 5.0
DEST_TRK_DEG = 45.0


def _bearing_dist(lat1: float, lon1: float, lat2: float, lon2: float) -> tuple:
    """Return (initial course deg, distance nm) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin((p2 - p1) / 2.0) ** 2 +
         math.cos(p1) * math.cos(p2) * math.sin(dlon / 2.0) ** 2)
    dist = 2.0 * EARTH_NM * math.asin(min(1.0, math.sqrt(a)))
    y = math.sin(dlon) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dlon)
    return math.degrees(math.atan2(y, x)) % 360.0, dist


def destination(lat: float, lon: float, course: float, dist_nm: float) -> tuple:
    """Return (lat, lon) dist_nm from a point along a great circle starting at course."""
    p1, l1 = math.radians(lat), math.radians(lon)
    d, c = dist_nm / EARTH_NM, math.radians(course)
    p2 = math.asin(math.sin(p1) * math.cos(d) + math.cos(p1) * math.sin(d) * math.cos(c))
    l2 = l1 + math.atan2(math.sin(c) * math.sin(d) * math.cos(p1),
                         math.cos(d) - math.sin(p1) * math.sin(p2))
    return math.degrees(p2), ((math.degrees(l2) + 180.0) % 360.0) - 180.0


def angle_diff(a: float, b: float) -> float:
    """Return the smallest difference between two directions (0-180 deg)."""
    return abs((a - b + 180.0) % 360.0 - 180.0)


@dataclass
class RoutePos:
    """Where a point is relative to the route."""

    leg: int            # the leg (points[leg] -> points[leg + 1]) it is nearest
    xtk_nm: float       # cross-track distance (positive right of the route)
    along_nm: float     # distance along the route from its first point, to abeam the point
    course: float       # the leg's course abeam the point (deg true)
    total_nm: float     # the whole route's length


def leg_lengths(points: list) -> list:
    """Return each leg's length (nm)."""
    return [_bearing_dist(*points[i], *points[i + 1])[1] for i in range(len(points) - 1)]


def locate(points: list, lat: float, lon: float) -> Optional[RoutePos]:  # pylint: disable=too-many-locals
    """Return where (lat, lon) is relative to the route (its nearest leg), or None.

    points: [(lat, lon), ...], at least two. A point before the first leg
    or past the last counts against the nearest end (along-track clamped).
    """
    if len(points) < 2:
        return None
    lengths = leg_lengths(points)
    total = sum(lengths)
    best_off, best = math.inf, None
    start = 0.0
    for i, length in enumerate(lengths):
        a = points[i]
        course_ab, _ = _bearing_dist(*a, *points[i + 1])
        course_ap, dist_ap = _bearing_dist(*a, lat, lon)
        dap = dist_ap / EARTH_NM
        xtk = math.asin(max(-1.0, min(1.0, math.sin(dap) * math.sin(
            math.radians(course_ap - course_ab))))) * EARTH_NM
        cos_x = math.cos(xtk / EARTH_NM)
        atk = math.acos(max(-1.0, min(1.0, math.cos(dap) / cos_x))) * EARTH_NM if cos_x else 0.0
        if math.cos(math.radians(course_ap - course_ab)) < 0:
            atk = -atk
        clamped = max(0.0, min(length, atk))
        # distance to the leg itself (beyond its ends: to the end point)
        off = abs(xtk) if clamped == atk else math.hypot(atk - clamped, xtk)
        if off < best_off:
            course = _bearing_dist(*destination(*a, course_ab, clamped), *points[i + 1])[0] \
                if clamped < length - 0.1 else course_ab
            best_off, best = off, RoutePos(i, xtk, start + clamped, course, total)
        start += length
    return best


def point_along(points: list, along_nm: float) -> Optional[tuple]:
    """Return (lat, lon, course) at along_nm from the route's first point, or None past its end."""
    if len(points) < 2 or along_nm < 0.0:
        return None
    start = 0.0
    for i, length in enumerate(leg_lengths(points)):
        if along_nm <= start + length:
            course, _ = _bearing_dist(*points[i], *points[i + 1])
            lat, lon = destination(*points[i], course, along_nm - start)
            # the course there (a great circle's course changes along it)
            here = (_bearing_dist(lat, lon, *points[i + 1])[0]
                    if start + length - along_nm > 0.1 else course)
            return lat, lon, here
        start += length
    return None


def place_ahead(points: list, ac_along_nm: float, fwd_nm: float,
                right_nm: float) -> Optional[tuple]:
    """Return (lat, lon) fwd_nm along the route past the aircraft and right_nm to its right.

    None if the route ends sooner (then place from the heading).
    """
    at = point_along(points, ac_along_nm + fwd_nm)
    if at is None:
        return None
    lat, lon, course = at
    if abs(right_nm) < 0.01:
        return lat, lon
    return destination(lat, lon, (course + (90.0 if right_nm > 0 else -90.0)) % 360.0,
                       abs(right_nm))
