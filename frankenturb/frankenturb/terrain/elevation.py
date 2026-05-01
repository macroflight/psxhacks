"""Terrain elevation queries backed by Copernicus GLO-30 tiles.

ElevationGrid maintains an in-memory LRU cache of loaded tiles and provides:
  - Single-point elevation with bilinear interpolation
  - Rectangular window extraction spanning tile boundaries
  - Upwind terrain profile
  - Terrain roughness (elevation std-dev in a local window)
"""

import logging
import math
from collections import OrderedDict
from pathlib import Path
from typing import Optional

import numpy as np  # pylint: disable=import-error

from .tiles import TileCache, tile_name

log = logging.getLogger(__name__)

# Copernicus GLO-30 tiles: 3600×3600 pixels per 1°×1° cell.
PIXELS_PER_DEGREE = 3600
NODATA = -32768  # int16 sentinel used in Copernicus tiles

# Rough metres per degree of latitude (constant); longitude varies with cos(lat).
M_PER_DEG_LAT = 111_320.0


def _deg_to_m_lon(lat: float) -> float:
    """Metres per degree of longitude at the given latitude."""
    return M_PER_DEG_LAT * math.cos(math.radians(lat))


class _Tile:  # pylint: disable=too-few-public-methods
    """One loaded 1°×1° elevation tile."""

    __slots__ = ("name", "data", "lat0", "lon0")

    def __init__(self, name: str, data: np.ndarray, lat0: int, lon0: int):
        # data shape: (3600, 3600), float32, NaN for no-data.
        # lat0/lon0: SW corner (integer degrees).
        self.name = name
        self.data = data
        self.lat0 = lat0
        self.lon0 = lon0

    def row_col(self, lat: float, lon: float) -> tuple[float, float]:
        """Convert (lat, lon) to fractional (row, col) within this tile.

        Row 0 is the northern edge (lat0+1), row 3600 is the southern edge (lat0).
        Col 0 is the western edge (lon0),  col 3600 is the eastern edge (lon0+1).
        """
        row = (self.lat0 + 1 - lat) * PIXELS_PER_DEGREE
        col = (lon - self.lon0) * PIXELS_PER_DEGREE
        return row, col


class ElevationGrid:
    """On-demand terrain elevation backed by a TileCache.

    Tiles are loaded into memory on first access and kept in an LRU cache.
    At ~26 MB each (3600×3600 float32), 9 tiles ≈ 235 MB — enough to cover
    a 3°×3° region centred on the aircraft.

    Parameters
    ----------
    cache:
        TileCache that handles download/local storage.
    max_tiles:
        Maximum number of tiles to hold in memory simultaneously.

    """

    def __init__(self, cache: TileCache, max_tiles: int = 9):
        """Initialize the elevation grid with the given tile cache."""
        self._cache = cache
        self._tiles: OrderedDict[str, Optional[_Tile]] = OrderedDict()
        self._max = max_tiles

    # ------------------------------------------------------------------
    # Tile loading
    # ------------------------------------------------------------------

    def _load(self, name: str) -> Optional[_Tile]:
        """Return a loaded tile, fetching/caching as needed. None = ocean."""
        if name in self._tiles:
            self._tiles.move_to_end(name)
            return self._tiles[name]

        # Evict LRU entry if at capacity.
        if len(self._tiles) >= self._max:
            self._tiles.popitem(last=False)

        # Determine SW corner from name, e.g. N47 E011 → lat0=47, lon0=11.
        try:
            parts = name.split("_")
            # parts: ['Copernicus','DSM','COG','10','N47','00','E011','00','DEM']
            lat_str, lon_str = parts[4], parts[6]
            lat_sign = 1 if lat_str[0] == "N" else -1
            lon_sign = 1 if lon_str[0] == "E" else -1
            lat0 = lat_sign * int(lat_str[1:])
            lon0 = lon_sign * int(lon_str[1:])
        except (IndexError, ValueError) as exc:
            log.error("Cannot parse tile name %r: %s", name, exc)
            self._tiles[name] = None
            return None

        path = self._cache.ensure_by_name(name)
        if path is None:
            self._tiles[name] = None
            return None

        tile = self._read_geotiff(name, path, lat0, lon0)
        self._tiles[name] = tile
        return tile

    @staticmethod
    def _read_geotiff(name: str, path: Path, lat0: int, lon0: int) -> "_Tile":
        try:
            import rasterio  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise ImportError("rasterio is required: pip install rasterio") from exc

        with rasterio.open(path) as ds:
            raw = ds.read(1)  # int16

        data = raw.astype(np.float32)
        data[raw == NODATA] = np.nan
        log.debug("Loaded tile %s (%.1f MB)", name, data.nbytes / 1e6)
        return _Tile(name, data, lat0, lon0)

    def _tile_at(self, lat: float, lon: float) -> Optional[_Tile]:
        return self._load(tile_name(lat, lon))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_loaded(self, lat: float, lon: float) -> None:
        """Pre-load the tile covering (lat, lon) — call before time-critical queries."""
        self._tile_at(lat, lon)

    def elevation_at(self, lat: float, lon: float) -> Optional[float]:
        """Return terrain elevation in metres at (lat, lon), bilinear-interpolated.

        Returns None over ocean or outside dataset coverage.
        """
        tile = self._tile_at(lat, lon)
        if tile is None:
            return None

        row, col = tile.row_col(lat, lon)
        # Clamp to the tile's actual pixel range (tiles are not always 3600×3600).
        row = max(0.0, min(row, tile.data.shape[0] - 1.0))
        col = max(0.0, min(col, tile.data.shape[1] - 1.0))

        return float(_bilinear(tile.data, row, col))

    def elevation_grid(  # pylint: disable=too-many-locals
        self,
        lat_center: float,
        lon_center: float,
        radius_km: float,
        n_points: int = 128,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Extract a square elevation grid centred on (lat_center, lon_center).

        Returns
        -------
        lats : 1-D array of latitudes  (length n_points)
        lons : 1-D array of longitudes (length n_points)
        elev : 2-D array of elevations (n_points × n_points), NaN over ocean.
               Row 0 is northernmost; col 0 is westernmost.

        """
        dlat = radius_km * 1000.0 / M_PER_DEG_LAT
        dlon = radius_km * 1000.0 / _deg_to_m_lon(lat_center)

        lats = np.linspace(lat_center + dlat, lat_center - dlat, n_points)
        lons = np.linspace(lon_center - dlon, lon_center + dlon, n_points)

        elev = np.full((n_points, n_points), np.nan, dtype=np.float32)

        # Group sample points by tile to avoid repeated tile lookups.
        tile_map: dict[str, list] = {}
        for r, lat in enumerate(lats):
            for c, lon in enumerate(lons):
                key = tile_name(lat, lon)
                tile_map.setdefault(key, []).append((r, c, lat, lon))

        for key, points in tile_map.items():
            tile = self._load(key)
            if tile is None:
                continue
            rs = np.array([p[0] for p in points], dtype=np.intp)
            cs = np.array([p[1] for p in points], dtype=np.intp)
            pt_lats = np.array([p[2] for p in points], dtype=np.float64)
            pt_lons = np.array([p[3] for p in points], dtype=np.float64)
            rows = np.clip(
                (tile.lat0 + 1 - pt_lats) * PIXELS_PER_DEGREE,
                0.0, tile.data.shape[0] - 1.0,
            )
            cols = np.clip(
                (pt_lons - tile.lon0) * PIXELS_PER_DEGREE,
                0.0, tile.data.shape[1] - 1.0,
            )
            vals = _bilinear_batch(tile.data, rows, cols)
            valid = np.isfinite(vals)
            elev[rs[valid], cs[valid]] = vals[valid]

        return lats, lons, elev

    def terrain_roughness(
        self,
        lat: float,
        lon: float,
        radius_km: float = 20.0,
        n_points: int = 64,
    ) -> float:
        """Return the standard deviation of terrain elevation in a square window.

        A proxy for mechanical turbulence potential (m).
        Returns 0.0 when no valid terrain data is available.
        """
        _, _, elev = self.elevation_grid(lat, lon, radius_km, n_points)
        valid = elev[np.isfinite(elev)]
        return float(np.std(valid)) if len(valid) > 1 else 0.0

    def upwind_profile(  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        self,
        lat: float,
        lon: float,
        wind_dir_deg: float,
        distance_km: float = 80.0,
        n_points: int = 200,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Sample terrain elevation along the upwind direction.

        Parameters
        ----------
        lat :
            Aircraft latitude (decimal degrees).
        lon :
            Aircraft longitude (decimal degrees).
        wind_dir_deg :
            Meteorological wind direction (degrees FROM which wind blows).
            0 = wind from north, 90 = wind from east.
        distance_km :
            How far upwind to look.
        n_points :
            Number of sample points along the profile.

        Returns
        -------
        distances_km : distances from aircraft position (0 = aircraft, positive = upwind)
        elevations_m : terrain elevation at each sample point (NaN = ocean/no-data)

        """
        upwind_rad = math.radians(wind_dir_deg)  # dir FROM which wind comes
        du_lat = math.cos(upwind_rad)             # northward component
        du_lon = math.sin(upwind_rad)             # eastward component

        distances = np.linspace(0, distance_km, n_points)
        elevations = np.full(n_points, np.nan, dtype=np.float32)

        m_per_deg_lon = _deg_to_m_lon(lat)
        d_m = distances * 1000.0
        s_lats = lat + du_lat * d_m / M_PER_DEG_LAT
        s_lons = lon + du_lon * d_m / m_per_deg_lon

        # Group sample indices by tile to minimise _load() calls and enable
        # vectorised bilinear interpolation per tile.
        tile_map: dict[str, list[int]] = {}
        for i in range(n_points):
            tile_map.setdefault(tile_name(float(s_lats[i]), float(s_lons[i])), []).append(i)

        for key, indices in tile_map.items():
            tile = self._load(key)
            if tile is None:
                continue
            idx = np.array(indices)
            rows = np.clip(
                (tile.lat0 + 1 - s_lats[idx]) * PIXELS_PER_DEGREE,
                0.0, tile.data.shape[0] - 1.0,
            )
            cols = np.clip(
                (s_lons[idx] - tile.lon0) * PIXELS_PER_DEGREE,
                0.0, tile.data.shape[1] - 1.0,
            )
            vals = _bilinear_batch(tile.data, rows, cols)
            valid = np.isfinite(vals)
            elevations[idx[valid]] = vals[valid]

        return distances, elevations

    def max_upwind_elevation(
        self,
        lat: float,
        lon: float,
        wind_dir_deg: float,
        distance_km: float = 80.0,
    ) -> float:
        """Return maximum terrain elevation along the upwind direction (metres).

        Returns 0.0 if no terrain data available.
        """
        _, elevations = self.upwind_profile(lat, lon, wind_dir_deg, distance_km)
        valid = elevations[np.isfinite(elevations)]
        return float(np.max(valid)) if len(valid) > 0 else 0.0


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _bilinear_batch(data: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    """Vectorised bilinear interpolation at multiple (row, col) positions.

    Returns NaN wherever any of the four corner samples is NaN.
    """
    r0 = np.clip(rows.astype(np.intp), 0, data.shape[0] - 1)
    c0 = np.clip(cols.astype(np.intp), 0, data.shape[1] - 1)
    r1 = np.minimum(r0 + 1, data.shape[0] - 1)
    c1 = np.minimum(c0 + 1, data.shape[1] - 1)
    dr = rows - r0
    dc = cols - c0
    v00 = data[r0, c0].astype(float)
    v01 = data[r0, c1].astype(float)
    v10 = data[r1, c0].astype(float)
    v11 = data[r1, c1].astype(float)
    result = (v00 * (1 - dr) * (1 - dc) +
              v01 * (1 - dr) * dc +
              v10 * dr * (1 - dc) +
              v11 * dr * dc)
    result[np.isnan(v00) | np.isnan(v01) | np.isnan(v10) | np.isnan(v11)] = np.nan
    return result.astype(np.float32)


def _bilinear(data: np.ndarray, row: float, col: float) -> float:
    """Perform bilinear interpolation on a 2-D array at fractional (row, col).

    Returns NaN if any of the four corner samples are NaN.
    """
    r0 = max(0, min(int(row), data.shape[0] - 1))
    c0 = max(0, min(int(col), data.shape[1] - 1))
    r1 = min(r0 + 1, data.shape[0] - 1)
    c1 = min(c0 + 1, data.shape[1] - 1)

    dr = row - r0
    dc = col - c0

    v00 = data[r0, c0]
    v01 = data[r0, c1]
    v10 = data[r1, c0]
    v11 = data[r1, c1]

    if np.isnan(v00) or np.isnan(v01) or np.isnan(v10) or np.isnan(v11):
        return np.nan

    return float(
        v00 * (1 - dr) * (1 - dc) +
        v01 * (1 - dr) * dc +
        v10 * dr * (1 - dc) +
        v11 * dr * dc
    )
