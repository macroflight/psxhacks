"""Copernicus GLO-30 tile management: naming, download, and local disk cache.

Tiles are 1°×1° GeoTIFFs, 3600×3600 pixels at ~30 m resolution.
Available from AWS S3 (no credentials required):
  https://copernicus-dem-30m.s3.amazonaws.com/{name}/{name}.tif
"""

import logging
import math
from pathlib import Path
from typing import Optional

import requests  # pylint: disable=import-error

log = logging.getLogger(__name__)

COPERNICUS_BASE = "https://copernicus-dem-30m.s3.amazonaws.com"
TILE_LIST_URL = f"{COPERNICUS_BASE}/tileList.txt"
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "frankenturb" / "terrain"

# Download chunk size (64 KiB)
_CHUNK = 1 << 16


def tile_name(lat: float, lon: float) -> str:
    """Return the Copernicus GLO-30 tile name covering the point (lat, lon).

    Each tile covers a 1°×1° cell whose south-west corner is at
    (floor(lat), floor(lon)).

    >>> tile_name(47.26, 11.39)   # Innsbruck
    'Copernicus_DSM_COG_10_N47_00_E011_00_DEM'
    >>> tile_name(-33.87, 151.21) # Sydney
    'Copernicus_DSM_COG_10_S34_00_E151_00_DEM'
    >>> tile_name(27.70, -85.31)  # mid-Atlantic
    'Copernicus_DSM_COG_10_N27_00_W086_00_DEM'
    """
    ilat = math.floor(lat)
    ilon = math.floor(lon)
    ns = "N" if ilat >= 0 else "S"
    ew = "E" if ilon >= 0 else "W"
    return f"Copernicus_DSM_COG_10_{ns}{abs(ilat):02d}_00_{ew}{abs(ilon):03d}_00_DEM"


def tile_url(name: str) -> str:
    """Return the HTTPS URL for a tile by name."""
    return f"{COPERNICUS_BASE}/{name}/{name}.tif"


def tiles_for_bbox(
    lat_min: float, lat_max: float, lon_min: float, lon_max: float
) -> list[str]:
    """Return all tile names required to cover a bounding box."""
    names = []
    for ilat in range(math.floor(lat_min), math.ceil(lat_max)):
        for ilon in range(math.floor(lon_min), math.ceil(lon_max)):
            names.append(tile_name(ilat, ilon))
    return names


class TileCache:
    """Local disk cache for Copernicus GLO-30 terrain tiles.

    Downloads tiles from AWS on demand and stores them as local GeoTIFFs.
    Uses tileList.txt to avoid attempting downloads for ocean/no-data cells.

    Parameters
    ----------
    cache_dir:
        Directory for cached tiles. Created automatically if absent.
        Defaults to ~/.cache/frankenturb/terrain.

    """

    def __init__(self, cache_dir: Path = DEFAULT_CACHE_DIR):
        """Initialize the tile cache, creating the cache directory if needed."""
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._available: Optional[set[str]] = None

    # ------------------------------------------------------------------
    # Tile index
    # ------------------------------------------------------------------

    def _fetch_tile_index(self) -> set[str]:
        """Download tileList.txt and return as a set of tile names."""
        index_path = self.cache_dir / "tileList.txt"
        if not index_path.exists():
            log.info("Downloading Copernicus tile index (~200 kB)…")
            try:
                r = requests.get(TILE_LIST_URL, timeout=30)
                r.raise_for_status()
            except requests.RequestException as exc:
                log.error("Tile index download failed: %s — terrain unavailable", exc)
                return set()
            index_path.write_bytes(r.content)
            log.info("Tile index saved to %s", index_path)
        return set(index_path.read_text().splitlines())

    @property
    def available(self) -> set[str]:
        """Set of all tile names present in the Copernicus dataset."""
        if self._available is None:
            self._available = self._fetch_tile_index()
        return self._available

    def exists_in_dataset(self, name: str) -> bool:
        """Return True if this tile exists (i.e. not pure ocean / outside coverage)."""
        return name in self.available

    # ------------------------------------------------------------------
    # Local paths
    # ------------------------------------------------------------------

    def local_path(self, name: str) -> Path:
        """Return the local cache path for a tile (may not exist yet)."""
        return self.cache_dir / f"{name}.tif"

    def is_cached(self, name: str) -> bool:
        """Return True if the tile file is already present in the local cache."""
        return self.local_path(name).exists()

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def ensure(self, lat: float, lon: float) -> Optional[Path]:
        """Ensure the tile covering (lat, lon) is present in the local cache.

        Downloads from AWS if necessary. Returns the local path, or None if
        the tile does not exist in the dataset (ocean / no-data area).
        """
        name = tile_name(lat, lon)

        if not self.exists_in_dataset(name):
            return None  # ocean or outside coverage

        path = self.local_path(name)
        if path.exists():
            return path

        try:
            self._download(name, path)
        except requests.RequestException as exc:
            log.error("Tile download failed for %s: %s", name, exc)
            return None
        return path

    def ensure_by_name(self, name: str) -> Optional[Path]:
        """Like ensure() but takes a tile name directly."""
        if not self.exists_in_dataset(name):
            return None
        path = self.local_path(name)
        if not path.exists():
            try:
                self._download(name, path)
            except requests.RequestException as exc:
                log.error("Tile download failed for %s: %s", name, exc)
                return None
        return path

    def prefetch(
        self,
        lat_min: float,
        lat_max: float,
        lon_min: float,
        lon_max: float,
    ) -> list[Path]:
        """Download all tiles covering a bounding box.

        Useful for pre-loading tiles around a destination airport before
        the approach begins. Returns a list of local paths for tiles that
        exist in the dataset.
        """
        paths = []
        for name in tiles_for_bbox(lat_min, lat_max, lon_min, lon_max):
            p = self.ensure_by_name(name)
            if p is not None:
                paths.append(p)
        return paths

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _download(self, name: str, dest: Path) -> None:
        url = tile_url(name)
        log.info("Downloading %s …", name)
        r = requests.get(url, timeout=120, stream=True)
        r.raise_for_status()

        # Write to a temp file then rename so we never leave a partial tile.
        tmp = dest.with_suffix(".tmp")
        try:
            with tmp.open("wb") as f:
                for chunk in r.iter_content(chunk_size=_CHUNK):
                    f.write(chunk)
            tmp.rename(dest)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

        mb = dest.stat().st_size / 1e6
        log.info("Cached %s (%.1f MB)", name, mb)
