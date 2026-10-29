"""Navigation helpers (flat-earth, fine for a few tens of nm) and terrain along a track."""
import math
import os
import sys
from typing import Optional

_GRID = None


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the distance (nm) between two points."""
    dlat = (lat2 - lat1) * 60.0
    dlon = (lon2 - lon1) * 60.0 * math.cos(math.radians((lat1 + lat2) / 2.0))
    return math.hypot(dlat, dlon)


def track_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the true track (deg) from the first point to the second."""
    return math.degrees(math.atan2((lon2 - lon1) * math.cos(math.radians(lat1)),
                                   lat2 - lat1)) % 360.0


def dest_point(lat: float, lon: float, track: float, nm: float) -> tuple:
    """Return (lat, lon) nm from lat/lon on the true track (negative nm: behind)."""
    return (lat + nm / 60.0 * math.cos(math.radians(track)),
            lon + nm / 60.0 * math.sin(math.radians(track)) / math.cos(math.radians(lat)))


def along_cross_nm(ref_lat: float, ref_lon: float, track: float,
                   lat: float, lon: float) -> tuple:
    """Return (along, cross) nm of lat/lon from the line through ref on track.

    along is positive past ref, cross positive right of the line.
    """
    north = (lat - ref_lat) * 60.0
    east = (lon - ref_lon) * 60.0 * math.cos(math.radians(ref_lat))
    rad = math.radians(track)
    return (north * math.cos(rad) + east * math.sin(rad),
            east * math.cos(rad) - north * math.sin(rad))


def elevation_ft(lat: float, lon: float) -> Optional[float]:
    """Return the terrain elevation (ft) at lat/lon from frankenturb's tiles."""
    global _GRID  # pylint: disable=global-statement
    if _GRID is None:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        # pylint: disable-next=import-outside-toplevel
        from frankenturb.terrain import ElevationGrid, TileCache
        _GRID = ElevationGrid(TileCache())
    metres = _GRID.elevation_at(lat, lon)
    return None if metres is None else metres / 0.3048


def highest_terrain_ft(lat: float, lon: float, track: float, nm: float) -> float:
    """Return the highest terrain (ft) within nm from lat/lon along track (every 0.25 nm)."""
    top = 0.0
    for i in range(int(nm * 4) + 1):
        top = max(top, elevation_ft(*dest_point(lat, lon, track, i / 4.0)) or 0.0)
    return top
