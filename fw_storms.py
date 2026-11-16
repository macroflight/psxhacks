"""Storms outside weather radar coverage, the same in every frankenweather (--radar-cbs, testing).

Two or more PSX sims flying in the same area must see the same CB in the
same place (user, 2026-11-16). Inside radar coverage the radar gives every
frankenweather the same storms (fw_radar). Outside it, this module makes
them up, but the same way everywhere: nothing depends on where an aircraft
is, when its frankenweather started or what it has seen.

- Storms can only be born on a fixed global grid (GRID_DEG apart in
  latitude; in longitude as near GRID_DEG of distance as whole steps round
  the Earth allow), one chance per grid point per UTC hour.
- Whether a storm is born there, when in that hour, how far from the grid
  point and how long it lives come from a hash of the grid point and the
  hour (_unit()), not from random(): the same numbers in every sim.
- The chance comes from shared data at the grid point: a TS/TC SIGMET area
  around it (its qualifier: ISOL to SQL) or lightning near it. Without
  either, nothing here: the forecast's CBs stay as they were.
- A storm moves with a motion given for its grid point (the 850-300 hPa
  mean wind there, from the same forecast hour), and is a fw_radar.Cell, so
  frankenweather places, drifts and decays it like a radar storm.

Pure helpers, no PSX connection or downloads: frankenweather.py gathers the
SIGMETs, lightning and winds.
"""
import hashlib
import math
from typing import Callable, Optional

import fw_radar as _fw_radar

GRID_DEG = 0.5               # about 30 nm: storms can't be closer than PSX's zones allow anyway
HOUR_S = 3600
LIFE_MIN_S = 30 * 60.0       # like a drifting CB's (fw_drift)
LIFE_MAX_S = 60 * 60.0
TRUSTED_AGE_S = 10 ** 6      # a made-up storm's motion always counts (fw_radar.cell_at())
# The chance of a storm being born at a grid point in an hour, by SIGMET qualifier
# (fw_sigmet.TS_QUALIFIER_OKTAS): about chance x 3/4 storms there at any time
CHANCE_BY_OKTAS = {2: 0.2, 4: 0.45, 5: 0.55, 6: 0.7, 7: 0.85, 8: 0.9}
LIGHTNING_CHANCE = 0.9
KEY_PREFIX = "S"             # a made-up storm's key (fw_radar.Cell.key) starts with this


def _unit(*parts) -> float:
    """Return a number in [0, 1) from parts, the same in every Python process."""
    digest = hashlib.blake2b(repr(parts).encode(), digest_size=8).digest()
    return int.from_bytes(digest, 'big') / 2.0 ** 64


def _band_steps(band: int) -> int:
    """Return how many grid points latitude band band has round the Earth."""
    lat = (band + 0.5) * GRID_DEG - 90.0
    return max(1, round(360.0 * math.cos(math.radians(lat)) / GRID_DEG))


def grid_points(lat: float, lon: float, radius_nm: float) -> list:  # pylint: disable=too-many-locals
    """Return [(band, step, lat, lon)] of the grid points within radius_nm of (lat, lon)."""
    out = []
    dlat = radius_nm / 60.0
    b0 = max(0, int((lat - dlat + 90.0) / GRID_DEG))
    b1 = min(int(180.0 / GRID_DEG) - 1, int((lat + dlat + 90.0) / GRID_DEG))
    for band in range(b0, b1 + 1):
        steps = _band_steps(band)
        plat = (band + 0.5) * GRID_DEG - 90.0
        width = 360.0 / steps
        dlon = dlat / max(0.05, math.cos(math.radians(plat)))
        if dlon >= 180.0:
            idx = range(steps)
        else:
            first = math.floor((lon - dlon + 180.0) / width)
            idx = {i % steps for i in range(first, math.floor((lon + dlon + 180.0) / width) + 1)}
        for step in sorted(idx):
            plon = ((step + 0.5) * width) - 180.0
            if _fw_radar._dist_nm(lat, lon, plat, plon) <= radius_nm:  # pylint: disable=protected-access
                out.append((band, step, plat, plon))
    return out


def storms_at(now_s: float, points: list, chance: Callable, motion: Callable) -> list:  # pylint: disable=too-many-locals
    """Return the storms alive at now_s (unix s) born on grid points (grid_points()).

    chance(lat, lon) -> (probability per hour, peak dBZ) or None; motion(lat,
    lon) -> (towards deg, kt) or None. Each storm is a fw_radar.Cell whose
    frame is its birth: fw_radar.cell_at() gives where it is.
    """
    out = []
    hour = int(now_s // HOUR_S)
    for band, step, plat, plon in points:
        got = chance(plat, plon)
        if not got:
            continue
        prob, dbz = got
        for h in (hour - 1, hour):
            if _unit(band, step, h, "born") >= prob:
                continue
            born = h * HOUR_S + _unit(band, step, h, "when") * HOUR_S
            life = LIFE_MIN_S + _unit(band, step, h, "life") * (LIFE_MAX_S - LIFE_MIN_S)
            if not born <= now_s < born + life:
                continue
            width = 360.0 / _band_steps(band)
            lat = plat + (_unit(band, step, h, "dlat") - 0.5) * GRID_DEG
            lon = plon + (_unit(band, step, h, "dlon") - 0.5) * width
            area = 40.0 + _unit(band, step, h, "area") * 300.0
            out.append(_fw_radar.Cell(
                lat, ((lon + 180.0) % 360.0) - 180.0, int(dbz), area, int(born),
                motion=motion(plat, plon), age_s=TRUSTED_AGE_S,
                key=f"{KEY_PREFIX}{band}:{step}:{h}"))
    out.sort(key=lambda c: (-c.max_dbz, -c.area_km2, c.key))
    return out


def is_storm(cell) -> bool:
    """Return True if a cell is one of this module's storms (not a radar cell)."""
    return bool(getattr(cell, 'key', '').startswith(KEY_PREFIX))


def sigmet_chance(oktas: Optional[int]) -> Optional[tuple]:
    """Return (probability, peak dBZ) for a SIGMET area giving oktas, or None."""
    if not oktas:
        return None
    prob = CHANCE_BY_OKTAS.get(oktas) or CHANCE_BY_OKTAS[min(CHANCE_BY_OKTAS,
                                                             key=lambda k: abs(k - oktas))]
    return prob, 48 + min(12, 2 * oktas)
