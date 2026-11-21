"""frankenweather flight reports to PSCC Flight Centre (on by default, --no-flight-centre-report).

frankenweather sends what it decided and why, so a flight's weather can be
analysed afterwards without the sim's own log files (user 2026-11-21):

- "session": the version, which sim, and every setting (allow-listed: no API
  keys, logon codes, key files, webhooks, passwords, proxies or local paths),
  at the start and whenever a setting changes;
- "log": frankenweather's INFO log lines (each zone move, CB placed, removed
  or kept, mode change, icing and fuel event, with its reason) every
  LOG_EVERY_S;
- "snapshot": what the /devel status page shows, every SNAPSHOT_EVERY_S.

Each batch is gzip JSON, POSTed with the sim's Flight Centre logon code (the
same one frankenpush uses) as a Bearer token. Batches wait on disk (QUEUE_DIR)
until Flight Centre takes them: a Flight Centre that doesn't answer delays the
report, never the weather. Format: docs/developers/flight-centre-reports.md.

frankenweather.py runs the coroutine; this module has the pieces.
"""
import collections
import gzip
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

REPORT_PATH = "/api/fw/report"   # Flight Centre's endpoint (psccfc 2026-11-21)
BATCHES_PER_POST = 100           # queued batches sent together as a JSON array (FC: up to 500) ...
POST_MAX_BYTES = 1_500_000       # ... at most this much gzip on the wire (FC: 2 MB)
LOG_EVERY_S = 30.0
SNAPSHOT_EVERY_S = 60.0
SESSION_CHECK_S = 60.0           # settings compared this often; changed: a new "session"
RETRY_S = 10.0                   # Flight Centre not answering: try again this often ...
GIVE_UP_S = 60.0                 # ... for this long (the agreed FC retry rule), then ...
BACKOFF_S = 300.0                # ... wait this long before the next round
REFUSED_BACKOFF_S = 1800.0       # 401/403 (the logon code refused): try again this much later
QUEUE_MAX_BYTES = 50 * 1024 * 1024   # oldest batches dropped beyond this
LOG_BUFFER_MAX = 20000           # log lines kept between batches at most
QUEUE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "frankenweather", "fc_reports")
CODE_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "frankenweather", "flight_centre.json")

# Settings never sent: anything named like a secret, a credential, a file or a folder
_SECRET_RE = re.compile(r"(key|code|token|passw|secret|webhook|proxy|auth|cookie|file|dir|path|"
                        r"cache|url|host)", re.IGNORECASE)


def utc_iso(epoch: float) -> str:
    """Return an epoch time as ISO UTC with seconds ("2026-11-21T08:41:07Z")."""
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class LogBuffer(logging.Handler):
    """Keep frankenweather's INFO (and worse) log lines until the next "log" batch."""

    def __init__(self):
        """Start empty; at most LOG_BUFFER_MAX lines (the oldest go)."""
        super().__init__(logging.INFO)
        self.lines: collections.deque = collections.deque(maxlen=LOG_BUFFER_MAX)

    def emit(self, record: logging.LogRecord) -> None:
        """Keep [ISO time, level, message]."""
        try:
            msg = record.getMessage()
        except (TypeError, ValueError):
            return
        self.lines.append([utc_iso(record.created), record.levelname, msg])

    def take(self) -> list:
        """Return the lines kept so far and start afresh."""
        out = list(self.lines)
        self.lines.clear()
        return out


def safe_settings(settings: dict) -> dict:
    """Return the settings without anything secret or local (allow-list by name and type).

    Kept: names not matching _SECRET_RE, with plain values (bool, number,
    short text, lists of those). Everything else is left out, so a new
    secret option can't leak by accident.
    """
    out = {}
    for name, value in sorted(settings.items()):
        if _SECRET_RE.search(str(name)):
            continue
        if isinstance(value, (bool, int, float)) or value is None:
            out[name] = value
        elif isinstance(value, str) and len(value) <= 200 and "/" not in value and \
                "\\" not in value:
            out[name] = value
        elif isinstance(value, (list, tuple)) and all(
                isinstance(v, (bool, int, float, str)) and len(str(v)) <= 100 for v in value):
            out[name] = list(value)
        elif isinstance(value, dict):
            inner = safe_settings(value)
            if inner:
                out[name] = inner
    return out


def read_logon_code(code: Optional[str], code_file: Optional[str], portal_url: str) -> tuple:
    """Return (logon code or None, where it came from) and remember a given code.

    --logon-code, else --logon-code-file, else the code remembered from an
    earlier run with the same portal (CODE_CACHE, only readable by the
    user): a test site's code is never sent to the live Flight Centre.
    Never logged.
    """
    portal = portal_url.rstrip("/")
    source = None
    if code:
        source = "--logon-code"
    elif code_file:
        with open(os.path.expanduser(code_file), encoding="utf-8") as fh:
            code = fh.read().strip() or None
        source = "--logon-code-file"
    try:
        with open(CODE_CACHE, encoding="utf-8") as fh:
            cache = json.load(fh)
        if not isinstance(cache, dict) or "logon_code" in cache:   # the first format: no portal
            cache = {}
    except (OSError, ValueError):
        cache = {}
    if code:
        cache[portal] = code
        try:
            os.makedirs(os.path.dirname(CODE_CACHE), exist_ok=True)
            fd = os.open(CODE_CACHE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(cache, fh)
        except OSError:
            pass
        return code, source
    code = cache.get(portal) or None
    return code, ("remembered from an earlier run" if code else None)


class Report:
    """The batches of one frankenweather run: numbering and the on-disk queue."""

    def __init__(self, queue_dir: str = QUEUE_DIR):
        """Start a run: a new session id; the queue keeps earlier runs' unsent batches."""
        self.session_id = str(uuid.uuid4())
        self.seq = 0
        self.queue_dir = queue_dir
        os.makedirs(queue_dir, exist_ok=True)
        self.last_settings: Optional[str] = None

    def add(self, kind: str, body: dict) -> str:
        """Write one batch to the queue; return its file name."""
        self.seq += 1
        batch = {"type": kind, "session_id": self.session_id, "seq": self.seq,
                 "time": utc_iso(time.time()), **body}
        name = f"{int(time.time() * 1000):014d}-{self.session_id[:8]}-{self.seq:06d}-{kind}.json.gz"
        tmp = os.path.join(self.queue_dir, name + ".tmp")
        with gzip.open(tmp, "wt", encoding="utf-8") as fh:
            json.dump(batch, fh, separators=(",", ":"), default=str)
        os.replace(tmp, os.path.join(self.queue_dir, name))
        self.trim()
        return name

    def session_if_changed(self, body: dict) -> bool:
        """Queue a "session" batch if its settings differ from the last one; return True if so."""
        key = json.dumps(body.get("settings"), sort_keys=True, default=str)
        if key == self.last_settings:
            return False
        self.last_settings = key
        self.add("session", body)
        return True

    def pending(self) -> list:
        """Return the queued batch files, oldest first."""
        return sorted(f for f in os.listdir(self.queue_dir) if f.endswith(".json.gz"))

    def trim(self) -> None:
        """Drop the oldest batches beyond QUEUE_MAX_BYTES."""
        files = self.pending()
        sizes = {f: os.path.getsize(os.path.join(self.queue_dir, f)) for f in files}
        total = sum(sizes.values())
        for f in files:
            if total <= QUEUE_MAX_BYTES:
                break
            total -= sizes[f]
            os.remove(os.path.join(self.queue_dir, f))

    def read(self, name: str) -> bytes:
        """Return a queued batch's gzip bytes."""
        with open(os.path.join(self.queue_dir, name), "rb") as fh:
            return fh.read()

    def next_post(self) -> tuple:
        """Return (names, gzip body) of the next request: the oldest batches, or ([], b"").

        One batch as it is; more (when catching up) as one JSON array of up to
        BATCHES_PER_POST batches and POST_MAX_BYTES (Flight Centre counts
        requests: 60 a minute per sim).
        """
        pending = self.pending()
        if not pending:
            return [], b""
        if len(pending) == 1:
            return pending, self.read(pending[0])
        names, batches, size = [], [], 0
        for name in pending[:BATCHES_PER_POST]:
            raw = self.read(name)
            if names and size + len(raw) > POST_MAX_BYTES:
                break
            names.append(name)
            batches.append(json.loads(gzip.decompress(raw)))
            size += len(raw)
        return names, gzip.compress(json.dumps(batches, separators=(",", ":"),
                                               default=str).encode("utf-8"))

    def done(self, name: str) -> None:
        """Remove a batch Flight Centre took."""
        try:
            os.remove(os.path.join(self.queue_dir, name))
        except OSError:
            pass


def snapshot_of(devel: dict) -> dict:
    """Return the /devel status data trimmed for a "snapshot" batch.

    Left out: the event list (the "log" batches carry it) and the SIGMETs'
    raw text (long, the same in every snapshot).
    """
    out = {k: v for k, v in devel.items() if k != "events"}
    if isinstance(out.get("sigmets"), list):
        out["sigmets"] = [{k: v for k, v in s.items() if k != "raw"} for s in out["sigmets"]
                          if isinstance(s, dict)]
    return out
