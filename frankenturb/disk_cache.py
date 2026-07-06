"""Small on-disk cache for expensive fetches (Open-Meteo, etc.).

Persists a fetcher's cache across process restarts, so a dev inner loop
that restarts frankenweather/frankenturb many times a day doesn't have to
re-fetch identical data every time it comes back up. Entries expire the
same way whether served from memory or reloaded from disk, so callers get
this for free without changing their own freshness guarantees: pass a
max_age_s that matches whatever staleness the caller already tolerates
(e.g. its existing cache-bucket granularity, or its refresh cadence), and
disk persistence never makes data any staler than the caller already
allows on its own.
"""
import json
import logging
import os
import tempfile
import time
from typing import Any, Optional

log = logging.getLogger(__name__)


class DiskCache:
    """A flat JSON-backed string-key cache with a single max age.

    Values must already be JSON-serializable (plain dicts/lists/numbers/
    strings) — callers with richer types (dataclasses, numpy arrays, …)
    are expected to encode/decode at their own call sites.
    """

    def __init__(self, path: str, max_age_s: float) -> None:
        """Load path if present, dropping entries already older than max_age_s."""
        self._path = path
        self._max_age_s = max_age_s
        self._entries: dict[str, tuple[float, Any]] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        now = time.time()
        for key, (stored_at, value) in raw.items():
            if now - stored_at <= self._max_age_s:
                self._entries[key] = (stored_at, value)
        log.info("Disk cache %s: loaded %d/%d still-fresh entries",
                 self._path, len(self._entries), len(raw))

    def get(self, key: str) -> Optional[Any]:
        """Return the cached value for key, or None if absent or expired."""
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if time.time() - stored_at > self._max_age_s:
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        """Store value under key and flush the whole cache to disk."""
        self._entries[key] = (time.time(), value)
        self._flush()

    def _flush(self) -> None:
        try:
            directory = os.path.dirname(self._path) or "."
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=directory, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._entries, fh)
            os.replace(tmp_path, self._path)
        except OSError as exc:
            log.warning("Failed to save disk cache %s: %s", self._path, exc)
