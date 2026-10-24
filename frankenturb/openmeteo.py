"""Shared Open-Meteo access: one client for every request, and an hourly-forecast cache.

OpenMeteoClient is the single way frankenweather/frankenturb talk to
Open-Meteo's free API, which allows 10,000 calls a day (and 5,000 an hour,
600 a minute) per IP address. It keeps
- one backoff for everybody: after a refusal (HTTP 429) or another failure,
  no fetcher asks again until it expires, instead of each one finding out
  for itself;
- a usage count, weighted the way Open-Meteo counts calls (one per location
  per 10 variables), saved to disk so restarts don't reset it.

HourlyForecastCache keeps a whole hourly download (forecast_days=2) per
CACHE_DEG_GRID square on disk and serves any hour from it, refetching only
after REFRESH_S (newer forecast runs) or when the data no longer covers the
hour. If a fetch fails, older data that still covers the hour is used.
"""

import collections
import json
import logging
import math
import os
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Optional

import requests  # pylint: disable=import-error

from .disk_cache import DiskCache

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
REQUEST_TIMEOUT_S = 15
RATE_LIMIT_BACKOFF_S = 300.0      # nobody asks for this long after an HTTP 429
# ...or this long after any other failure (503, timeout, bad JSON). Without
# it, a failure at a position that never got cached leaves e.g. the
# turbulence loop (every 200 ms) free to retry immediately -- observed live
# during a transient Open-Meteo 503.
GENERIC_FAILURE_BACKOFF_S = 30.0
_USAGE_KEEP_S = 24 * 3600.0
_USAGE_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "frankenweather", "om_usage.json")


def call_weight(params: dict) -> float:
    """Return how many API calls Open-Meteo counts a request as.

    One per location, times the number of variables / 10 (at least 1),
    times the number of days / 14 (at least 1).
    """
    locations = len(str(params.get("latitude", "")).split(","))
    variables = sum(len([v for v in str(params[k]).split(",") if v])
                    for k in ("hourly", "current", "daily", "minutely_15") if params.get(k))
    days = float(params.get("forecast_days", 7)) + float(params.get("past_days", 0))
    return locations * max(1.0, variables / 10.0) * max(1.0, days / 14.0)


class OpenMeteoClient:
    """Every Open-Meteo request goes through here (thread-safe; see module docstring)."""

    def __init__(self, usage_path: Optional[str] = _USAGE_PATH) -> None:
        """Start with no backoff, and the usage of the last 24 h from disk."""
        self._lock = threading.Lock()
        self._backoff_until = 0.0     # wall clock, so it's comparable across threads
        self._usage_path = usage_path
        self._usage: collections.deque = collections.deque()  # (epoch, kind, weight)
        self._load_usage()

    def backoff_remaining(self) -> float:
        """Return how many more seconds nobody should ask Open-Meteo, 0 if none."""
        return max(0.0, self._backoff_until - time.time())

    def get(self, params: dict, kind: str, proxy: Optional[str] = None) -> Optional[Any]:
        """Return Open-Meteo's JSON reply for params, or None (failure or backing off).

        kind ("wind", "cape", "zones", "enroute", ...) is only for the usage
        count and log messages.
        """
        remaining = self.backoff_remaining()
        if remaining > 0:
            log.debug("Open-Meteo %s fetch skipped — backing off (%.0fs remaining)",
                      kind, remaining)
            return None
        proxies = {'http': proxy, 'https': proxy} if proxy else None
        try:
            r = requests.get(OPEN_METEO_URL, params=params, timeout=REQUEST_TIMEOUT_S,
                             proxies=proxies)
        except requests.RequestException as exc:
            self._fail(GENERIC_FAILURE_BACKOFF_S, f"{kind} fetch failed: {exc}")
            return None
        if r.status_code == 429:
            reason = ""
            try:
                reason = f" ({r.json().get('reason')})"
            except ValueError:
                pass
            self._fail(RATE_LIMIT_BACKOFF_S, f"{kind} fetch: rate limited{reason}")
            return None
        if r.status_code != 200:
            self._fail(GENERIC_FAILURE_BACKOFF_S, f"{kind} fetch: HTTP {r.status_code}")
            return None
        try:
            data = r.json()
        except ValueError as exc:
            self._fail(GENERIC_FAILURE_BACKOFF_S, f"{kind} fetch: bad JSON: {exc}")
            return None
        self._record(kind, call_weight(params))
        return data

    def _fail(self, backoff_s: float, message: str) -> None:
        with self._lock:
            self._backoff_until = max(self._backoff_until, time.time() + backoff_s)
        log.warning("Open-Meteo %s — nobody asks again for %.0fs", message, backoff_s)

    def usage(self) -> dict:
        """Return {"last_hour": calls, "last_24h": calls, "by_kind_24h": {kind: calls}}."""
        now = time.time()
        with self._lock:
            self._prune(now)
            entries = list(self._usage)
        by_kind: dict = collections.defaultdict(float)
        for _ts, kind, weight in entries:
            by_kind[kind] += weight
        return {
            "last_hour": round(sum(w for ts, _k, w in entries if now - ts <= 3600.0), 1),
            "last_24h": round(sum(w for _ts, _k, w in entries), 1),
            "by_kind_24h": {k: round(v, 1) for k, v in sorted(by_kind.items())},
        }

    def _record(self, kind: str, weight: float) -> None:
        now = time.time()
        with self._lock:
            self._usage.append((now, kind, weight))
            self._prune(now)
            entries = list(self._usage)
        self._save_usage(entries)

    def _prune(self, now: float) -> None:
        while self._usage and now - self._usage[0][0] > _USAGE_KEEP_S:
            self._usage.popleft()

    def _load_usage(self) -> None:
        if not self._usage_path:
            return
        try:
            with open(self._usage_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            now = time.time()
            self._usage.extend(tuple(e) for e in raw if now - e[0] <= _USAGE_KEEP_S)
        except (OSError, ValueError, TypeError, IndexError):
            pass

    def _save_usage(self, entries: list) -> None:
        if not self._usage_path:
            return
        try:
            directory = os.path.dirname(self._usage_path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entries, fh)
            os.replace(tmp_path, self._usage_path)
        except OSError as exc:
            log.debug("Failed to save Open-Meteo usage %s: %s", self._usage_path, exc)


# The one client everybody in this process uses.
CLIENT = OpenMeteoClient()


class HourlyForecastCache:  # pylint: disable=too-few-public-methods
    """Whole hourly Open-Meteo downloads per grid square, on disk (see module docstring)."""

    CACHE_DEG_GRID = 1.0
    REFRESH_S = 6 * 3600.0      # newer forecast runs are out by then
    KEEP_S = 48 * 3600.0        # forecast_days=2: no use keeping it longer

    def __init__(  # pylint: disable=too-many-arguments,too-many-positional-arguments
            self, kind: str, hourly: str, path: str,
            models: str = "best_match", extra_params: Optional[dict] = None,
            proxy: Optional[str] = None, client: Optional[OpenMeteoClient] = None) -> None:
        """kind: for the usage count; hourly: the comma-separated hourly variables."""
        self._kind = kind
        self._params = {"hourly": hourly, "timeformat": "unixtime", "forecast_days": 2,
                        "models": models, **(extra_params or {})}
        self._proxy = proxy
        self._client = client or CLIENT
        self._disk = DiskCache(path, max_age_s=self.KEEP_S)

    def get(self, lat: float, lon: float, target_time: datetime) -> Optional[dict]:
        """Return an Open-Meteo reply ({"hourly": {...}, ...}) covering target_time, or None."""
        key = (f"{math.floor(lat / self.CACHE_DEG_GRID)},"
               f"{math.floor(lon / self.CACHE_DEG_GRID)}")
        ts = target_time.timestamp()
        entry = self._disk.get_entry(key)
        if entry is not None and entry[1] <= self.REFRESH_S and _covers(entry[0], ts):
            return entry[0]
        data = self._client.get({"latitude": round(lat, 4), "longitude": round(lon, 4),
                                 **self._params}, self._kind, self._proxy)
        if isinstance(data, dict) and _covers(data, ts):
            self._disk.set(key, data)
            return data
        if entry is not None and _covers(entry[0], ts):
            log.info("Open-Meteo %s: using %.1f h old data at (%.2f, %.2f)",
                     self._kind, entry[1] / 3600.0, lat, lon)
            return entry[0]
        return None


def _covers(data: dict, ts: float) -> bool:
    """Return True if an Open-Meteo reply has an hourly time within 30 min of ts."""
    times = (data.get("hourly") or {}).get("time") or []
    return bool(times) and times[0] - 1800 <= ts <= times[-1] + 1800
