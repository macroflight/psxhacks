"""PSX CB storm cell location and properties.

Parses WxBasic/Wx1–7 (CB cloud top/base) and WxMode1–7 (zone position)
PSX variables to determine the position and vertical extent of each active
storm cell, and finds the one nearest to the aircraft.

PSX data model
--------------
Local zones 1–7: each zone has a center position (WxMode1–7) and CB data
(Wx1–7).  The CB is always 7 nm from the zone center on a bearing that
rotates slowly with simulation time, driven by TimeEarth.

Planet weather: WxBasic holds the global CB profile; WxClust holds up to
four cell lat/lon positions (in radians).

Lateral radius
--------------
For local zones the CB radius is half the distance to the nearest
neighbouring known zone center, guaranteeing no lateral overlap.
A fallback of 30 nm is used when fewer than two positions are known.
For planet-weather clusters the radius scales with cloud depth:
10 nm (0 ft) to 40 nm (28 000 ft).
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

# CB is always 7 nm from the weather zone center.
_CB_OFFSET_NM = 7.0
_EARTH_RADIUS_M = 6_371_000.0
_NM_TO_M = 1_852.0
_M_TO_NM = 1.0 / _NM_TO_M


@dataclass
class CbInfo:  # pylint: disable=too-many-instance-attributes
    """Nearest active CB storm cell relative to the aircraft.

    All ranges are in nautical miles; bearings in degrees true (0 = N,
    clockwise).  Cloud altitudes are feet MSL.  A negative range_edge_nm
    means the aircraft is inside the estimated CB lateral boundary.
    """

    source: str                  # e.g. "Wx3" or "WxClust2"
    center_lat: float
    center_lon: float
    bearing_deg: float           # aircraft → CB centre
    range_center_nm: float       # slant range to CB centre
    lateral_radius_nm: float     # estimated lateral radius
    range_edge_nm: float         # range to nearest edge; negative = inside
    cloud_base_ft_msl: float
    cloud_top_ft_msl: float
    coverage: int                # 0–8 oktas


def parse_wx_zone_basic(raw: str) -> Optional[tuple[int, int, int]]:
    """Parse CB cloud data from a WxBasic / Wx1–7 string.

    Returns (coverage_oktas, top_ft_agl, base_ft_agl) or None if the
    string is too short or unparseable.  CB fields are at indices 9, 10,
    and 11 of the semicolon-separated PSX weather string.
    """
    parts = raw.strip().split(';')
    if len(parts) < 12:
        return None
    try:
        return int(parts[9]), int(parts[10]), int(parts[11])
    except (ValueError, IndexError):
        return None


def parse_wx_zone_position(raw: str) -> Optional[tuple[float, float, float]]:
    """Parse zone center position from a WxMode1–7 string.

    Returns (lat_deg, lon_deg, elev_ft) or None.  PSX encodes coordinates
    in radians; the coordinate parser detects and converts as needed.
    Fields: [0]=lat, [1]=lon, [2]=ignored, [3]=elevation_ft.
    """
    parts = raw.strip().split(';')
    if len(parts) < 4:
        return None
    try:
        lat = _parse_psx_coord(parts[0], is_lon=False)
        lon = _parse_psx_coord(parts[1], is_lon=True)
        elev_ft = float(parts[3])
        return lat, lon, elev_ft
    except (ValueError, IndexError):
        return None


def parse_wx_clust(raw: str) -> list[tuple[float, float]]:
    """Parse planet-weather storm cell positions from a WxClust string.

    Returns a list of (lat_deg, lon_deg) for each cell.  Values in the
    PSX string are in radians; up to four lat/lon pairs are expected.
    """
    cells: list[tuple[float, float]] = []
    values: list[float] = []
    for part in raw.strip().split(';'):
        try:
            values.append(float(part))
        except ValueError:
            pass
    for i in range(0, len(values) - 1, 2):
        cells.append((math.degrees(values[i]), math.degrees(values[i + 1])))
    return cells


def find_nearest_cb(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    acft_lat: float,
    acft_lon: float,
    zone_positions: dict[int, tuple[float, float, float]],
    zone_cb_data: dict[int, tuple[int, int, int]],
    clust_positions: list[tuple[float, float]],
    time_earth_ms: int,
    lat_scale: float = 1.0,
) -> Optional[CbInfo]:
    """Return the nearest active CB storm cell to the aircraft.

    Parameters
    ----------
    acft_lat, acft_lon :
        Aircraft position (decimal degrees).
    zone_positions :
        Zone index (1–7) → (lat_deg, lon_deg, elev_ft).  All known zones
        should be included even if they have no active CB, because they are
        used for nearest-neighbour radius calculation.
    zone_cb_data :
        Zone index (0–7, where 0 = planet weather) → (coverage_oktas,
        top_ft_agl, base_ft_agl).
    clust_positions :
        Planet-weather cluster positions as (lat_deg, lon_deg) pairs.
    time_earth_ms :
        PSX simulation time in milliseconds since Unix epoch; used to
        compute the slowly-rotating CB bearing around each zone centre.
    lat_scale :
        Multiplier applied to the computed nearest-neighbour zone radius
        before it becomes the effective CB lateral radius (default 1.0 =
        unchanged).  Values below 1.0 shrink the CB; 0.5 gives half the
        nearest-neighbour radius.

    Returns
    -------
    CbInfo for the nearest active cell, or None when no CBs are active.

    """
    candidates: list[CbInfo] = []

    for zone_idx, (zone_lat, zone_lon, zone_elev) in zone_positions.items():
        cb_data = zone_cb_data.get(zone_idx)
        if cb_data is None or cb_data[0] == 0:
            continue

        coverage, top_agl, base_agl = cb_data
        bearing = _cb_bearing(time_earth_ms, zone_elev)
        cb_lat, cb_lon = _translate(zone_lat, zone_lon, bearing, _CB_OFFSET_NM * _NM_TO_M)

        # The nearest-neighbour radius is measured from zone centres, so the
        # edge distance must also be measured from the zone centre (not the CB
        # cloud centre, which is 7 nm away).
        radius = _nearest_neighbor_radius_nm(zone_idx, zone_positions) * lat_scale
        zone_dist = _distance_nm(acft_lat, acft_lon, zone_lat, zone_lon)
        dist = _distance_nm(acft_lat, acft_lon, cb_lat, cb_lon)
        brg = _bearing_deg(acft_lat, acft_lon, cb_lat, cb_lon)

        candidates.append(CbInfo(
            source=f"Wx{zone_idx}",
            center_lat=cb_lat,
            center_lon=cb_lon,
            bearing_deg=brg,
            range_center_nm=dist,
            lateral_radius_nm=radius,
            range_edge_nm=zone_dist - radius,
            cloud_base_ft_msl=float(base_agl) + zone_elev,
            cloud_top_ft_msl=float(top_agl) + zone_elev,
            coverage=coverage,
        ))

    planet_cb = zone_cb_data.get(0)
    if planet_cb is not None and planet_cb[0] > 0:
        p_coverage, p_top_agl, p_base_agl = planet_cb
        p_depth_ft = max(0, p_top_agl - p_base_agl)
        p_radius = _clust_radius_nm(p_depth_ft) * lat_scale

        for i, (cb_lat, cb_lon) in enumerate(clust_positions):
            dist = _distance_nm(acft_lat, acft_lon, cb_lat, cb_lon)
            brg = _bearing_deg(acft_lat, acft_lon, cb_lat, cb_lon)
            candidates.append(CbInfo(
                source=f"WxClust{i + 1}",
                center_lat=cb_lat,
                center_lon=cb_lon,
                bearing_deg=brg,
                range_center_nm=dist,
                lateral_radius_nm=p_radius,
                range_edge_nm=dist - p_radius,
                cloud_base_ft_msl=float(p_base_agl),
                cloud_top_ft_msl=float(p_top_agl),
                coverage=p_coverage,
            ))

    if not candidates:
        return None
    return min(candidates, key=lambda c: c.range_center_nm)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cb_bearing(time_earth_ms: int, elev_ft: float) -> float:
    """Return CB bearing (degrees) from zone centre at the given sim time.

    Replicates the PSX formula:
      bearing = (minutes × 6 + seconds × 0.1 + elevFt + 2000) % 360
    The CB completes one revolution around its zone centre per minute.
    """
    dt = datetime.fromtimestamp(time_earth_ms / 1000.0, tz=timezone.utc)
    sim_min = dt.minute
    sim_sec = dt.second + dt.microsecond / 1_000_000.0
    return (sim_min * 6.0 + sim_sec * 0.1 + elev_ft + 2000.0) % 360.0


def _parse_psx_coord(value: str, is_lon: bool) -> float:
    """Parse a PSX coordinate that may be in radians or degrees.

    Values whose magnitude exceeds PI/2 (lat) or PI (lon) but falls
    within the normal degree range are treated as already-degrees; all
    others are converted from radians.
    """
    raw = float(value)
    deg_limit = 180.0 if is_lon else 90.0
    rad_limit = math.pi if is_lon else math.pi / 2.0
    if abs(raw) <= deg_limit and abs(raw) > rad_limit:
        return raw
    return math.degrees(raw)


def _translate(
    lat_deg: float, lon_deg: float, bearing_deg: float, meters: float
) -> tuple[float, float]:
    """Great-circle destination given origin, bearing (°), and distance (m)."""
    ang = meters / _EARTH_RADIUS_M
    b_rad = math.radians(bearing_deg)
    lat_r = math.radians(lat_deg)
    lon_r = math.radians(lon_deg)
    dest_lat_r = math.asin(
        math.sin(lat_r) * math.cos(ang) +
        math.cos(lat_r) * math.sin(ang) * math.cos(b_rad)
    )
    dest_lon_r = lon_r + math.atan2(
        math.sin(b_rad) * math.sin(ang) * math.cos(lat_r),
        math.cos(ang) - math.sin(lat_r) * math.sin(dest_lat_r),
    )
    return math.degrees(dest_lat_r), math.degrees(dest_lon_r)


def _distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine great-circle distance in nautical miles."""
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlat = lat2r - lat1r
    dlon = math.radians(lon2 - lon1)
    hav = (math.sin(dlat / 2) ** 2 +
           math.cos(lat1r) * math.cos(lat2r) * math.sin(dlon / 2) ** 2)
    return 2.0 * _EARTH_RADIUS_M * math.asin(math.sqrt(hav)) * _M_TO_NM


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Compute initial great-circle bearing from point 1 to point 2 (degrees true)."""
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x_comp = math.sin(dlon) * math.cos(lat2r)
    y_comp = (math.cos(lat1r) * math.sin(lat2r) -
              math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon))
    return (math.degrees(math.atan2(x_comp, y_comp)) + 360.0) % 360.0


def _nearest_neighbor_radius_nm(
    zone_idx: int,
    zone_positions: dict[int, tuple[float, float, float]],
    fallback_nm: float = 30.0,
) -> float:
    """Return half the distance to the nearest other known zone centre (nm).

    Returns fallback_nm (default 30 nm) when this is the only known zone.
    Halving the nearest-neighbour distance guarantees that the lateral
    extents of neighbouring CBs never overlap.
    """
    lat, lon, _ = zone_positions[zone_idx]
    min_dist = None
    for other_idx, (other_lat, other_lon, _) in zone_positions.items():
        if other_idx == zone_idx:
            continue
        dist = _distance_nm(lat, lon, other_lat, other_lon)
        if min_dist is None or dist < min_dist:
            min_dist = dist
    return min_dist / 2.0 if min_dist is not None else fallback_nm


def _clust_radius_nm(cloud_depth_ft: float) -> float:
    """Estimate lateral radius (nm) for a planet-weather cluster cell.

    Scales from 10 nm (shallow/no depth) to 40 nm (28 000 ft deep),
    consistent with the PSX guideline that lateral and vertical sizes
    correspond for cluster cells.
    """
    return 10.0 + max(0.0, cloud_depth_ft) * 30.0 / 28_000.0
