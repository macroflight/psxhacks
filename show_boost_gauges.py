"""show_boost_gauges.py - Analogue gauges for frankenboostproxy's acceleration telemetry.

Connects to frankenboostproxy.py's --accel-port (a plain TCP socket
streaming one JSON object per line at 5 Hz - see frankenboostproxy.py's
module docstring for the exact record format) and displays it as a panel
of speedometer-style analogue gauges (plus one compass-style gauge for
heading) in a small Tkinter window:

  - heave/surge/sway (g) - heave spans -2.0..+4.0g, labelled every 0.5g,
    with a green reference line at 1.0g (level flight) and red limit
    lines at -1.0/+2.5/+3.75g; surge/sway have a blue 0 reference line.
  - roll/pitch/yaw rate (deg/s), and pitch/bank attitude (deg) - all with
    a blue 0 reference line.
  - heading (deg) - a compass-style gauge (needle wraps through 360
    degrees, no speedometer end-stops), since a linear gauge would show
    a false discontinuity every time the aircraft flies through north.
  - IAS/TAS/ground speed (kt), vertical speed (fpm), approximate AoA
    (deg, see frankenboostproxy.py's module docstring for the
    "approximate" caveat - it's ground-track-derived, not airspeed-
    vector-derived), gross weight (kg), and kinetic energy (GJ, TAS-based).

"Instruments" at the top of the window are toggle buttons that show/hide
each gauge; the gauge area reflows to as many columns as fit the current
window width and gets scrollbars (vertical always, horizontal if even one
column doesn't fit) when the visible gauges don't fit otherwise. IAS,
TAS, ground speed, gross weight, and kinetic energy (the "plain
magnitude" gauges, as opposed to already-directional/signed ones like
heave_g or the rates) also show a trend arc: a short purple arc, inside
the tick circle, spanning from the current value to a linear projection
of where it will be in _TREND_PROJECTION_S seconds (estimated from the
last _TREND_WINDOW_S seconds of history) - not drawn until there's
enough history, and not drawn at all if that projection barely differs
from the current value.

Each gauge also shades an arc between the min/max values it has seen
since the window opened (or since the "Reset Min/Max" button was last
pressed), and shows those two numbers as text underneath - except the
heading compass, where "min/max heading" isn't a meaningful concept.
With --minmax-window=<seconds> the min/max cover only that many seconds
back instead (a moving window), so old extremes drop out on their own.

--single-instrument=<key> shows just one named gauge instead (see
--help for the valid keys), with none of the above buttons and no fixed
window size - e.g. for embedding or screenshotting a single instrument.
--geometry=<Tk geometry string> controls the window's size and/or
placement either way.

Pure standard library plus psx.py (vendored at the repo root, same as
every other addon here), so it runs unmodified on both Linux and Windows
wherever Python's Tk support is available. The network read happens in a
background thread that reconnects automatically if the data source isn't
up yet or drops the connection; the GUI itself just polls the latest
received record on a timer and never touches the socket/psx.Client
directly.

Two mutually exclusive data sources (--port vs --psx-port):

  - Default: connect directly to frankenboostproxy's --accel-port (a
    plain TCP socket streaming one JSON record per line at 5 Hz) - see
    AccelReader.
  - --psx-port (with --psx-host): connect to the PSX network itself
    instead (e.g. a frankenrouter or the PSX Main Server directly) and
    pick the same telemetry out of frankenboostproxy's
    addon=FRANKENBOOSTPROXY:TELEMETRY:<json> broadcasts - see AddonReader.
    Only receives anything if frankenboostproxy was started with
    --send-addon, and only as often as its --addon-interval allows
    (slower than the full 5 Hz of --port).
"""

import argparse
import asyncio
import json
import math
import socket
import threading
import time
import tkinter as tk
from collections import deque
from dataclasses import dataclass
from typing import Optional

from psx import Client
from psxhacks_version import get_version

# Shares frankenboostproxy's version number/changelog - the two are built
# and released together (see makepackages.ps1/build.yml's $GroupMembers),
# not independently versioned.
__version__ = get_version("frankenboostproxy", __file__)

_RECONNECT_DELAY_S = 3.0
_REFRESH_INTERVAL_MS = 100
_STALE_TIMEOUT_S = 2.0
_TREND_WINDOW_S = 2.0  # how much recent history to estimate the current rate of change from
_TREND_PROJECTION_S = 10.0  # how far ahead the trend arc projects that rate

_GAUGE_SIZE = 220
_GAUGE_PADDING = 10
_TOGGLE_COLUMNS = 5
_DEFAULT_VISIBLE = frozenset({"heave_g", "roll_rate_dps", "kinetic_energy_gj"})

# Speedometer-style sweep: _START_DEG is the needle angle (standard math
# convention, 0 = +x axis, counterclockwise positive) at the gauge's
# minimum value; the needle sweeps clockwise through _SWEEP_DEG degrees
# to reach _START_DEG - _SWEEP_DEG at the maximum value. Not used for
# compass-style gauges (GaugeSpec.is_compass) - see _draw_compass_face().
_START_DEG = 225.0
_SWEEP_DEG = 270.0

_COMPASS_LABELS = {0.0: "N", 90.0: "E", 180.0: "S", 270.0: "W"}


@dataclass
class GaugeSpec:  # pylint: disable=too-many-instance-attributes
    """Static appearance/range for one analogue gauge."""

    key: str  # telemetry record field name
    title: str
    unit: str
    min_val: float
    max_val: float
    decimals: int = 2
    # Coloured reference ticks, as (value, color) pairs; out-of-range ones are skipped.
    ref_lines: tuple[tuple[float, str], ...] = ((0.0, "#1565c0"),)
    tick_step: Optional[float] = None  # fixed spacing between labelled ticks; None = 5 even ticks
    is_compass: bool = False  # heading-style: needle wraps 0..360, no speedometer end-stops
    track_minmax: bool = True  # show the observed-range band/text (meaningless for a compass)
    show_trend: bool = False  # show a rising/falling/steady arrow next to the value


_GAUGES = (
    GaugeSpec("heave_g", "Heave", "g", -2.0, 4.0, 1, tick_step=0.5,
              ref_lines=((1.0, "green"), (-1.0, "red"), (2.5, "red"), (3.75, "red"))),
    GaugeSpec("surge_g", "Surge", "g", -0.5, 0.5, 2),
    GaugeSpec("sway_g", "Sway", "g", -0.5, 0.5, 2),
    GaugeSpec("roll_rate_dps", "Roll Rate", "deg/s", -30.0, 30.0, 1),
    GaugeSpec("pitch_rate_dps", "Pitch Rate", "deg/s", -30.0, 30.0, 1),
    GaugeSpec("yaw_rate_dps", "Yaw Rate", "deg/s", -30.0, 30.0, 1),
    GaugeSpec("pitch_deg", "Pitch", "deg", -15.0, 25.0, 1, tick_step=5.0),
    GaugeSpec("bank_deg", "Bank", "deg", -60.0, 60.0, 0, tick_step=15.0),
    GaugeSpec("heading_deg", "Heading", "deg", 0.0, 360.0, 0,
              ref_lines=(), is_compass=True, track_minmax=False),
    GaugeSpec("ias_kt", "IAS", "kt", 0.0, 400.0, 0, ref_lines=(), show_trend=True),
    GaugeSpec("tas_kt", "TAS", "kt", 0.0, 600.0, 0, ref_lines=(), show_trend=True),
    GaugeSpec("ground_speed_kt", "Ground Speed", "kt", 0.0, 700.0, 0,
              ref_lines=(), show_trend=True),
    GaugeSpec("vertical_speed_fpm", "Vertical Speed", "fpm", -4000.0, 4000.0, 0),
    GaugeSpec("aoa_deg", "AoA (approx)", "deg", -5.0, 20.0, 1),
    GaugeSpec("gross_weight_kg", "Gross Weight", "kg", 130_000.0, 410_000.0, 0,
              ref_lines=(), show_trend=True),
    GaugeSpec("kinetic_energy_gj", "Kinetic Energy", "GJ", 0.0, 15.0, 1,
              ref_lines=(), show_trend=True),
)


class AnalogGauge(tk.Frame):  # pylint: disable=too-many-instance-attributes
    """A speedometer- or compass-style analogue gauge (dial, ticks, needle) on a Canvas."""

    def __init__(self, parent: tk.Widget, spec: GaugeSpec, size: int = _GAUGE_SIZE,
                 minmax_window_s: Optional[float] = None):
        """Build the static gauge face for `spec` and leave the needle at rest.

        With `minmax_window_s` set, the observed min/max cover only the
        last that many seconds rather than everything since the start
        (or the last reset).
        """
        super().__init__(parent)
        self.spec = spec
        self._minmax_window_s = minmax_window_s
        self._cx = size / 2
        self._cy = size / 2 + 10
        self._radius = size / 2 - 30

        tk.Label(self, text=spec.title, font=("TkDefaultFont", 11, "bold")).pack()
        self.canvas = tk.Canvas(self, width=size, height=size, bg="white", highlightthickness=0)
        self.canvas.pack()
        self.value_label = tk.Label(self, text="--", font=("TkDefaultFont", 11))
        self.value_label.pack()
        self.minmax_label: Optional[tk.Label] = None
        if spec.track_minmax:
            self.minmax_label = tk.Label(self, text="min -- / max --",
                                         font=("TkDefaultFont", 8), fg="#8a5a00")
            self.minmax_label.pack()

        self._needle_id: Optional[int] = None
        self._band_id: Optional[int] = None
        self._trend_arc_id: Optional[int] = None
        self._hub_id: Optional[int] = None
        self._min_seen: Optional[float] = None
        self._max_seen: Optional[float] = None
        # (monotonic time, value) within the moving min/max window
        self._minmax_samples: "deque[tuple[float, float]]" = deque()
        self._last_value: Optional[float] = None
        self._history: "deque[tuple[float, float]]" = deque()
        self._draw_face()
        self.set_value(None)

    def status_text(self) -> str:
        """Return a short "value / min / max" summary, e.g. for a toggle button's label."""
        decimals = self.spec.decimals

        def fmt(value: Optional[float]) -> str:
            return f"{value:.{decimals}f}" if value is not None else "--"

        text = f"{fmt(self._last_value)} {self.spec.unit}"
        if self.spec.track_minmax:
            text += f"\nmin {fmt(self._min_seen)} / max {fmt(self._max_seen)}"
        return text

    # ── geometry ──────────────────────────────────────────────────────

    def _xy(self, angle_deg: float, radius: float) -> tuple[float, float]:
        """Return the canvas (x, y) at standard math angle `angle_deg`, `radius` from center."""
        angle = math.radians(angle_deg)
        return (
            self._cx + radius * math.cos(angle),
            self._cy - radius * math.sin(angle),
        )

    def _point(self, frac: float, radius: float) -> tuple[float, float]:
        """Return the (x, y) canvas point at sweep fraction `frac`, `radius` out from center."""
        return self._xy(_START_DEG - frac * _SWEEP_DEG, radius)

    def _value_angle_deg(self, value: float) -> float:
        """Return the needle's standard math angle (degrees) for `value`."""
        if self.spec.is_compass:
            return (90.0 - value) % 360.0
        clamped = max(self.spec.min_val, min(self.spec.max_val, value))
        frac = (clamped - self.spec.min_val) / (self.spec.max_val - self.spec.min_val)
        return _START_DEG - frac * _SWEEP_DEG

    # ── static face ───────────────────────────────────────────────────

    def _tick_values(self, span: float) -> list[float]:
        """Return the gauge values that should get a labelled tick mark."""
        if self.spec.tick_step is not None:
            steps = round(span / self.spec.tick_step)
            return [self.spec.min_val + i * self.spec.tick_step for i in range(steps + 1)]
        ticks = 5
        return [self.spec.min_val + i / ticks * span for i in range(ticks + 1)]

    def _draw_ticks(self, span: float) -> None:
        canvas = self.canvas
        for value in self._tick_values(span):
            frac = (value - self.spec.min_val) / span
            x1, y1 = self._point(frac, self._radius)
            x2, y2 = self._point(frac, self._radius * 0.85)
            canvas.create_line(x1, y1, x2, y2, width=2)
            label_x, label_y = self._point(frac, self._radius * 0.65)
            canvas.create_text(
                label_x, label_y, text=f"{value:.{self.spec.decimals}f}",
                font=("TkDefaultFont", 8),
            )

    def _draw_reference_lines(self, span: float) -> None:
        for ref, color in self.spec.ref_lines:
            if not self.spec.min_val < ref < self.spec.max_val:
                continue
            frac_ref = (ref - self.spec.min_val) / span
            x1, y1 = self._point(frac_ref, self._radius)
            x2, y2 = self._point(frac_ref, self._radius * 0.72)
            self.canvas.create_line(x1, y1, x2, y2, width=3, fill=color)

    def _draw_compass_face(self) -> None:
        canvas = self.canvas
        for deg in range(0, 360, 30):
            angle = 90.0 - deg
            major = deg in _COMPASS_LABELS
            x1, y1 = self._xy(angle, self._radius)
            x2, y2 = self._xy(angle, self._radius * (0.85 if major else 0.92))
            canvas.create_line(x1, y1, x2, y2, width=2 if major else 1)
            if major:
                label_x, label_y = self._xy(angle, self._radius * 0.65)
                canvas.create_text(
                    label_x, label_y, text=_COMPASS_LABELS[deg],
                    font=("TkDefaultFont", 10, "bold"),
                )

    def _draw_face(self) -> None:
        """Draw the dial outline, tick marks, value labels, and reference lines."""
        canvas = self.canvas
        canvas.create_oval(
            self._cx - self._radius, self._cy - self._radius,
            self._cx + self._radius, self._cy + self._radius,
            outline="black", width=2,
        )
        if self.spec.is_compass:
            self._draw_compass_face()
        else:
            span = self.spec.max_val - self.spec.min_val
            self._draw_ticks(span)
            self._draw_reference_lines(span)
        self._hub_id = canvas.create_oval(
            self._cx - 6, self._cy - 6, self._cx + 6, self._cy + 6,
            fill="#333333", outline="",
        )

    # ── observed min/max band ────────────────────────────────────────

    def _draw_band(self) -> None:
        """(Re)draw the shaded arc spanning the min/max values observed so far."""
        canvas = self.canvas
        if self._band_id is not None:
            canvas.delete(self._band_id)
            self._band_id = None
        if not self.spec.track_minmax or self._min_seen is None or self._max_seen is None:
            return
        span = self.spec.max_val - self.spec.min_val
        lo = max(self.spec.min_val, min(self.spec.max_val, self._min_seen))
        hi = max(self.spec.min_val, min(self.spec.max_val, self._max_seen))
        frac_lo = (lo - self.spec.min_val) / span
        frac_hi = (hi - self.spec.min_val) / span
        band_radius = self._radius * 0.55
        thickness = self._radius * 0.14
        if frac_hi - frac_lo < 0.004:  # only one distinct value seen so far
            x1, y1 = self._point(frac_lo, band_radius - thickness / 2)
            x2, y2 = self._point(frac_lo, band_radius + thickness / 2)
            self._band_id = canvas.create_line(x1, y1, x2, y2, width=3, fill="#ffa726")
            return
        bbox = (
            self._cx - band_radius, self._cy - band_radius,
            self._cx + band_radius, self._cy + band_radius,
        )
        self._band_id = canvas.create_arc(
            bbox,
            start=_START_DEG - frac_hi * _SWEEP_DEG,
            extent=(frac_hi - frac_lo) * _SWEEP_DEG,
            style=tk.ARC, outline="#ffa726", width=thickness,
        )

    def _update_minmax_label(self) -> None:
        if self.minmax_label is None:
            return
        if self._min_seen is None or self._max_seen is None:
            self.minmax_label.config(text="min -- / max --")
            return
        decimals = self.spec.decimals
        self.minmax_label.config(
            text=f"min {self._min_seen:.{decimals}f} / max {self._max_seen:.{decimals}f}")

    def _track_minmax(self, value: Optional[float]) -> None:
        """Fold `value` (None: no data) into the observed min/max."""
        if self._minmax_window_s is None:
            if value is not None:
                self._min_seen = value if self._min_seen is None else min(self._min_seen, value)
                self._max_seen = value if self._max_seen is None else max(self._max_seen, value)
            return
        # Moving window: samples older than it drop out, also while there's no data
        now = time.monotonic()
        samples = self._minmax_samples
        if value is not None:
            samples.append((now, value))
        while samples and now - samples[0][0] > self._minmax_window_s:
            samples.popleft()
        self._min_seen = min(v for _, v in samples) if samples else None
        self._max_seen = max(v for _, v in samples) if samples else None

    def _refresh_minmax(self) -> None:
        self._draw_band()
        self._update_minmax_label()

    def reset(self) -> None:
        """Clear this gauge's observed min/max band."""
        self._min_seen = None
        self._max_seen = None
        self._minmax_samples.clear()
        self._refresh_minmax()

    # ── trend arc ─────────────────────────────────────────────────────

    def _trend_projection(self, value: float) -> Optional[float]:
        """Track recent history and linearly project the value _TREND_PROJECTION_S ahead.

        Returns None when there isn't enough recent history yet (just
        started, or too little time spread in the window) to estimate a
        rate of change.
        """
        now = time.monotonic()
        self._history.append((now, value))
        while self._history and now - self._history[0][0] > _TREND_WINDOW_S:
            self._history.popleft()
        if len(self._history) < 2:
            return None
        oldest_t, oldest_v = self._history[0]
        newest_t, newest_v = self._history[-1]
        elapsed = newest_t - oldest_t
        if elapsed < 0.5:
            return None
        rate = (newest_v - oldest_v) / elapsed
        return value + rate * _TREND_PROJECTION_S

    def _draw_trend_arc(self, value: float) -> None:
        """(Re)draw the arc from the current value to where it's headed in _TREND_PROJECTION_S."""
        canvas = self.canvas
        if self._trend_arc_id is not None:
            canvas.delete(self._trend_arc_id)
            self._trend_arc_id = None
        if not self.spec.show_trend or self.spec.is_compass:
            return
        projected = self._trend_projection(value)
        if projected is None:
            return
        span = self.spec.max_val - self.spec.min_val
        now_clamped = max(self.spec.min_val, min(self.spec.max_val, value))
        proj_clamped = max(self.spec.min_val, min(self.spec.max_val, projected))
        frac_now = (now_clamped - self.spec.min_val) / span
        frac_proj = (proj_clamped - self.spec.min_val) / span
        if abs(frac_proj - frac_now) < 0.004:
            return
        frac_lo, frac_hi = sorted((frac_now, frac_proj))
        band_radius = self._radius * 0.35
        thickness = self._radius * 0.10
        bbox = (
            self._cx - band_radius, self._cy - band_radius,
            self._cx + band_radius, self._cy + band_radius,
        )
        self._trend_arc_id = canvas.create_arc(
            bbox,
            start=_START_DEG - frac_hi * _SWEEP_DEG,
            extent=(frac_hi - frac_lo) * _SWEEP_DEG,
            style=tk.ARC, outline="#7b1fa2", width=thickness,
        )

    # ── live value ────────────────────────────────────────────────────

    def set_value(self, value: Optional[float]) -> None:
        """Move the needle to `value`, or rest it at "--" if None."""
        canvas = self.canvas
        self._last_value = value
        if self._needle_id is not None:
            canvas.delete(self._needle_id)
            self._needle_id = None
        if self.spec.track_minmax:
            self._track_minmax(value)
        self._refresh_minmax()
        if value is None:
            if self._trend_arc_id is not None:
                canvas.delete(self._trend_arc_id)
                self._trend_arc_id = None
            self.value_label.config(text="--")
            return
        self._draw_trend_arc(value)
        x, y = self._xy(self._value_angle_deg(value), self._radius * 0.8)
        self._needle_id = canvas.create_line(
            self._cx, self._cy, x, y, width=3, fill="red",
        )
        canvas.tag_raise(self._hub_id)
        self.value_label.config(text=f"{value:.{self.spec.decimals}f} {self.spec.unit}")


class AccelReader(threading.Thread):
    """Background thread maintaining a reconnecting connection to --accel-port.

    Reads newline-delimited JSON records (frankenboostproxy.py's telemetry
    format) and exposes only the latest one via `snapshot`, guarded by a
    lock; the GUI thread never touches the socket.
    """

    def __init__(self, host: str, port: int):
        """Store the target address; call start() to begin connecting."""
        super().__init__(daemon=True)
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._latest: Optional[dict] = None
        self._latest_at: float = 0.0
        self._connected = False
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Signal the background thread to stop on its next opportunity."""
        self._stop_event.set()

    @property
    def snapshot(self) -> tuple[bool, Optional[dict], float]:
        """Return (connected, latest record or None, seconds since that record arrived)."""
        with self._lock:
            age = time.monotonic() - self._latest_at if self._latest is not None else math.inf
            return self._connected, self._latest, age

    def run(self) -> None:
        """Connect, read records until disconnected, then retry - until stop() is called."""
        while not self._stop_event.is_set():
            try:
                sock = socket.create_connection((self._host, self._port), timeout=5)
            except OSError:
                with self._lock:
                    self._connected = False
                self._stop_event.wait(_RECONNECT_DELAY_S)
                continue
            with self._lock:
                self._connected = True
            try:
                self._read_lines(sock)
            except OSError:
                pass
            finally:
                sock.close()
                with self._lock:
                    self._connected = False
            self._stop_event.wait(_RECONNECT_DELAY_S)

    def _read_lines(self, sock: socket.socket) -> None:
        """Read and dispatch complete newline-delimited records until the socket closes."""
        sock.settimeout(1.0)
        buf = b""
        while not self._stop_event.is_set():
            try:
                chunk = sock.recv(4096)
            except socket.timeout:
                continue
            if not chunk:
                return
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle_line(line)

    def _handle_line(self, line: bytes) -> None:
        try:
            record = json.loads(line.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return
        with self._lock:
            self._latest = record
            self._latest_at = time.monotonic()


_ADDON_PREFIX = "FRANKENBOOSTPROXY:TELEMETRY:"


class AddonReader(threading.Thread):  # pylint: disable=too-many-instance-attributes
    """Background thread maintaining a PSX connection, the --psx-port alternative to AccelReader.

    Instead of connecting directly to frankenboostproxy's --accel-port,
    this connects to the PSX network itself (e.g. a frankenrouter or the
    PSX Main Server) and picks the telemetry out of frankenboostproxy's
    addon=FRANKENBOOSTPROXY:TELEMETRY:<json> broadcasts - only present if it
    was started with --send-addon. Exposes the same `snapshot` property
    as AccelReader, so GaugeApp doesn't need to know which is in use.
    """

    def __init__(self, host: str, port: int):
        """Store the target address; call start() to begin connecting."""
        super().__init__(daemon=True)
        self._host = host
        self._port = port
        self._lock = threading.Lock()
        self._latest: Optional[dict] = None
        self._latest_at: float = 0.0
        self._connected = False
        self._psx = Client()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._task: Optional[asyncio.Task] = None

    def stop(self) -> None:
        """Cancel the PSX connection from any thread; psx.Client sends "exit" and closes."""
        if self._loop is not None and self._task is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)

    @property
    def snapshot(self) -> tuple[bool, Optional[dict], float]:
        """Return (connected, latest record or None, seconds since that record arrived)."""
        with self._lock:
            age = time.monotonic() - self._latest_at if self._latest is not None else math.inf
            return self._connected, self._latest, age

    def run(self) -> None:
        """Run this thread's own asyncio event loop for the PSX connection."""
        asyncio.run(self._main())

    async def _main(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._task = asyncio.current_task()

        def connected(*_args) -> None:
            with self._lock:
                self._connected = True

        def disconnected() -> None:
            with self._lock:
                self._connected = False

        self._psx.onConnect = lambda: None
        self._psx.onDisconnect = disconnected
        self._psx.onResume = lambda: None
        self._psx.onPause = lambda: None
        self._psx.subscribe("id")
        self._psx.subscribe("version", connected)
        self._psx.subscribe("addon", self._on_addon)
        try:
            await self._psx.connect(self._host, self._port)
        except asyncio.CancelledError:
            pass

    def _on_addon(self, _key: str, value: str) -> None:
        if not value.startswith(_ADDON_PREFIX):
            return
        try:
            record = json.loads(value[len(_ADDON_PREFIX):])
        except ValueError:
            return
        with self._lock:
            self._latest = record
            self._latest_at = time.monotonic()


class GaugeApp:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """Top-level window: instrument toggles, a status line, and a scrollable gauge grid."""

    def __init__(self, root: tk.Tk, reader: AccelReader, single_instrument: Optional[str] = None,
                 minmax_window_s: Optional[float] = None):
        """Build the window's widgets and start the periodic refresh loop.

        With `single_instrument` set to one of _GAUGES' keys, the window
        shows only that one gauge - no toggle bar, no Select/Deselect
        All, no Reset Min/Max button, and no fixed window size (it
        shrinks to fit the one gauge rather than the normal multi-gauge
        default size). `minmax_window_s`: see AnalogGauge.
        """
        self.root = root
        self.reader = reader
        self._minmax_window_s = minmax_window_s
        self._gauges: dict[str, AnalogGauge] = {}
        self._toggle_buttons: dict[str, tk.Checkbutton] = {}
        root.title("FrankenBoostProxy Gauges")

        if single_instrument is not None:
            self._build_single_instrument(root, single_instrument)
        else:
            root.geometry("1100x750")
            self._visible = {
                spec.key: tk.BooleanVar(value=spec.key in _DEFAULT_VISIBLE) for spec in _GAUGES
            }
            self._build_gauges(root)
            self._build_toggle_bar(root)

            status_row = tk.Frame(root)
            status_row.pack(fill="x", padx=8, pady=(4, 0))
            self.status_label = tk.Label(status_row, text="Connecting...", anchor="w")
            self.status_label.pack(side="left", fill="x", expand=True)
            tk.Button(status_row, text="Reset Min/Max", command=self._reset_all).pack(side="right")

            self._relayout()

        self._refresh()

    def _build_single_instrument(self, root: tk.Tk, key: str) -> None:
        spec = next(s for s in _GAUGES if s.key == key)
        self.status_label = tk.Label(root, text="Connecting...", anchor="w")
        self.status_label.pack(fill="x", padx=4, pady=(4, 0))
        gauge = AnalogGauge(root, spec, minmax_window_s=self._minmax_window_s)
        gauge.pack(padx=8, pady=8)
        self._gauges[spec.key] = gauge

    def _build_toggle_bar(self, root: tk.Tk) -> None:
        """Build one toggle button per gauge, showing its title/value/min/max.

        Packed *before* the scrollable grid in the window, but built after
        it in code, since each button's initial text needs the matching
        AnalogGauge (for status_text()) to already exist.
        """
        toggle_frame = tk.LabelFrame(root, text="Instruments (click to show/hide)")
        toggle_frame.pack(fill="x", padx=8, pady=(8, 0), before=self._grid_container)
        for col in range(_TOGGLE_COLUMNS):
            toggle_frame.columnconfigure(col, weight=1, uniform="toggle")

        select_row = tk.Frame(toggle_frame)
        select_row.grid(row=0, column=0, columnspan=_TOGGLE_COLUMNS, sticky="w", pady=(0, 4))
        tk.Button(select_row, text="Select All", command=self._select_all).pack(side="left")
        deselect_button = tk.Button(select_row, text="Deselect All", command=self._deselect_all)
        deselect_button.pack(side="left", padx=(4, 0))

        for i, spec in enumerate(_GAUGES):
            button = tk.Checkbutton(
                toggle_frame, variable=self._visible[spec.key], indicatoron=False,
                justify="left", font=("TkDefaultFont", 8), command=self._relayout,
            )
            button.grid(row=1 + i // _TOGGLE_COLUMNS, column=i % _TOGGLE_COLUMNS,
                        padx=2, pady=2, sticky="ew")
            self._toggle_buttons[spec.key] = button

    def _build_gauges(self, root: tk.Tk) -> None:
        container = tk.Frame(root)
        container.pack(fill="both", expand=True, padx=4, pady=4)
        container.rowconfigure(0, weight=1)
        container.columnconfigure(0, weight=1)
        self._grid_container = container

        self.canvas = tk.Canvas(container, highlightthickness=0)
        vscroll = tk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        hscroll = tk.Scrollbar(container, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(yscrollcommand=vscroll.set, xscrollcommand=hscroll.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        vscroll.grid(row=0, column=1, sticky="ns")
        hscroll.grid(row=1, column=0, sticky="ew")

        self.inner = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self._gauges = {
            spec.key: AnalogGauge(self.inner, spec, minmax_window_s=self._minmax_window_s)
            for spec in _GAUGES}

        self.canvas.bind("<Configure>", lambda _e: self._relayout())
        self.inner.bind(
            "<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind_all(
            "<MouseWheel>", lambda e: self.canvas.yview_scroll(int(-e.delta / 120), "units"))
        self.canvas.bind_all("<Button-4>", lambda _e: self.canvas.yview_scroll(-1, "units"))
        self.canvas.bind_all("<Button-5>", lambda _e: self.canvas.yview_scroll(1, "units"))

    def _reset_all(self) -> None:
        for gauge in self._gauges.values():
            gauge.reset()

    def _select_all(self) -> None:
        for var in self._visible.values():
            var.set(True)
        self._relayout()

    def _deselect_all(self) -> None:
        for var in self._visible.values():
            var.set(False)
        self._relayout()

    def _relayout(self) -> None:
        """Re-grid the visible gauges, using as many columns as the window width allows."""
        for gauge in self._gauges.values():
            gauge.grid_forget()
        cell_width = _GAUGE_SIZE + 2 * _GAUGE_PADDING
        columns = max(1, self.canvas.winfo_width() // cell_width)
        visible_specs = [spec for spec in _GAUGES if self._visible[spec.key].get()]
        for i, spec in enumerate(visible_specs):
            self._gauges[spec.key].grid(
                row=i // columns, column=i % columns,
                padx=_GAUGE_PADDING, pady=_GAUGE_PADDING,
            )
        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _refresh(self) -> None:
        connected, record, age = self.reader.snapshot
        fresh = record is not None and age <= _STALE_TIMEOUT_S
        if not connected:
            self.status_label.config(text="Disconnected - retrying...")
        elif record is None:
            self.status_label.config(text="Connected, waiting for data...")
        else:
            sim = record.get("sim_name") or "unknown sim"
            self.status_label.config(
                text=f"Connected - {sim}" + ("" if fresh else " (stale data)"))

        for key, gauge in self._gauges.items():
            gauge.set_value(record.get(key) if fresh else None)
            button = self._toggle_buttons.get(key)
            if button is not None:
                button.config(text=f"{gauge.spec.title}\n{gauge.status_text()}")

        self.root.after(_REFRESH_INTERVAL_MS, self._refresh)


def main() -> None:
    """Parse arguments and run the gauges GUI until the window is closed."""
    parser = argparse.ArgumentParser(
        description="Live acceleration/roll-rate gauges from frankenboostproxy's --accel-port"
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    host_group = parser.add_mutually_exclusive_group()
    host_group.add_argument(
        '--host', default='127.0.0.1', metavar='HOST',
        help="frankenboostproxy's --accel-port hostname (default: 127.0.0.1). Mutually "
             "exclusive with --psx-host.",
    )
    host_group.add_argument(
        '--psx-host', default='127.0.0.1', metavar='HOST',
        help="PSX network hostname, used only together with --psx-port (default: "
             "127.0.0.1). Mutually exclusive with --host.",
    )
    port_group = parser.add_mutually_exclusive_group()
    port_group.add_argument(
        '--port', type=int, default=10751, metavar='PORT',
        help="frankenboostproxy's --accel-port (default: 10751). Mutually exclusive "
             "with --psx-port.",
    )
    port_group.add_argument(
        '--psx-port', type=int, default=None, metavar='PORT',
        help="Connect to the PSX network (e.g. a frankenrouter, or the PSX Main Server) "
             "at --psx-host:PSX-PORT instead, and read telemetry from frankenboostproxy's "
             "addon=FRANKENBOOSTPROXY:TELEMETRY:... broadcasts - only present if it was run "
             "with --send-addon, and only as often as its --addon-interval allows. "
             "Mutually exclusive with --port.",
    )
    gauge_keys = ", ".join(spec.key for spec in _GAUGES)
    parser.add_argument(
        '--single-instrument', default=None, metavar='NAME',
        choices=[spec.key for spec in _GAUGES],
        help="Show only this one instrument, with none of the selection/reset buttons "
             f"(e.g. for embedding or screenshotting a single gauge). One of: {gauge_keys}",
    )
    parser.add_argument(
        '--geometry', default=None, metavar='GEOMETRY',
        help='Tk geometry string controlling the window\'s size and/or placement, e.g. '
             '"+100+200" for position only or "900x700+100+200" for size and position - '
             'passed straight through to Tk (default: let the window manager place it, '
             'and auto-size to fit).',
    )
    parser.add_argument(
        '--minmax-window', type=float, default=None, metavar='SECONDS',
        help="Show the min/max values (text and shaded arc) of only the last SECONDS "
             "seconds, a moving window, instead of everything since the start or the "
             "last \"Reset Min/Max\" (default: since the start).",
    )
    args = parser.parse_args()
    if args.minmax_window is not None and args.minmax_window <= 0:
        parser.error("--minmax-window must be more than 0 seconds")

    if args.psx_port is not None:
        reader = AddonReader(args.psx_host, args.psx_port)
    else:
        reader = AccelReader(args.host, args.port)
    reader.start()

    root = tk.Tk()
    GaugeApp(root, reader, single_instrument=args.single_instrument,
             minmax_window_s=args.minmax_window)
    if args.geometry is not None:
        root.geometry(args.geometry)

    def on_close() -> None:
        reader.stop()
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == '__main__':
    main()
