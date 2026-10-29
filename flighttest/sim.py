"""Fly a PSX test sim like any other client: reposition, settle, correct the track, record.

Repositioning works the way PSX does it itself: motion freeze
(SimPaused=1), load1, load2, the situation's variables (a .situ file,
with overrides such as the start position), load3, release (SimPaused=0).
After the release, HDG SEL and FLCH are pressed like a pilot would: the
situation's lit HOLD modes keep their old references, so after a low
level reposition the aircraft would turn back and climb.

Everything is sent by Q-code: the PSX wire protocol only accepts Q-codes,
never the symbolic names a .situ file uses (psx_variable_validator
translates them).
"""
import dataclasses
import math
import os
import socket
import sys
import time
from typing import Iterator, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import psx_variable_validator  # noqa: E402  pylint: disable=wrong-import-position

from .geo import distance_nm, track_deg  # noqa: E402  pylint: disable=wrong-import-position

# Situation variables never sent: we freeze and release ourselves, and
# PiBaHeAlTas is what PSX itself reports
SKIP_NAMES = {"SimPaused", "PiBaHeAlTas"}
# Recorded by default: position/attitude, injected turbulence bursts, on
# ground, freeze state, the autopilot servos (ApServo, DEMAND, three
# values -999..999 at ~10 Hz, undocumented; seen e.g. -351;-22;0), the
# control surfaces (SurfAilEleRud, whole degrees, DEMAND), the pilot's
# flight controls (FltControls: elevator;aileron;rudder, -999..999, stay 0
# on the autopilot) and thrust lever angles (Tla, 1/100 deg): how hard
# the autopilot and autothrottle work is a measure of the turbulence as a
# pilot feels it. And the 7 weather zones and their modes.
RECORD_NAMES = (["PiBaHeAlTas", "WxBurst", "OnGround", "SimPaused", "ApServo",
                 "SurfAilEleRud", "FltControls", "Tla"] +
                [f"Wx{n}" for n in range(1, 8)] + [f"WxMode{n}" for n in range(1, 8)])


def qcode(name: str) -> str:
    """Return the Q-code of a PSX variable name (or Q-code)."""
    info = psx_variable_validator.lookup(name)
    if info is None:
        raise KeyError(f"unknown PSX variable {name}")
    return info.keyword


def start_position(lat: float, lon: float, heading: float, alt_ft: float, tas_kt: float) -> str:
    """Return a StartPiBaHeAlVsTasYw value: level flight at the given position."""
    return (f"0;52;0;{round(math.radians(heading) * 1000)};{int(alt_ft)};0;{int(tas_kt)};0;"
            f"{math.radians(lat)};{math.radians(lon)};0")


def read_situ(path: str, overrides: Optional[dict] = None) -> list:
    """Return a .situ file's [name, value] pairs, with overrides (name: value) applied."""
    pairs = []
    with open(os.path.expanduser(path), encoding="latin-1") as situ:
        for line in situ:
            line = line.rstrip("\r\n")
            if "=" not in line or line.startswith(("[", "Description")):
                continue
            pairs.append(line.split("=", 1))
    for name, value in (overrides or {}).items():
        for pair in pairs:
            if pair[0] == name:
                pair[1] = value
                break
        else:
            raise KeyError(f"override {name} not in {path}")
    return pairs


@dataclasses.dataclass
class Attitude:  # pylint: disable=too-many-instance-attributes
    """One PiBaHeAlTas report: degrees, ft (true altitude, not the altimeter), kt."""

    pitch: float
    bank: float
    heading: float
    alt_ft: float
    tas_kt: float
    lat: float
    lon: float

    @classmethod
    def parse(cls, value: str) -> "Attitude":
        """Parse a PiBaHeAlTas value."""
        fields = value.split(";")
        return cls(pitch=int(fields[0]) / 1e5 * 57.2958, bank=int(fields[1]) / 1e5 * 57.2958,
                   heading=math.degrees(float(fields[2])) % 360.0,
                   alt_ft=int(fields[3]) / 1000.0, tas_kt=int(fields[4]) / 1000.0,
                   lat=math.degrees(float(fields[5])), lon=math.degrees(float(fields[6])))


@dataclasses.dataclass
class Target:  # pylint: disable=too-many-instance-attributes
    """How the aircraft should be flying: started at lat/lon, on heading at alt_ft (true)."""

    lat: float
    lon: float
    heading: float
    alt_ft: float
    pos_nm: float = 3.0       # from the start point plus the distance flown
    alt_tol_ft: float = 400.0
    hdg_tol_deg: float = 10.0
    max_bank_deg: float = 5.0
    max_vs_fpm: float = 300.0


class Sim:
    """A connection to a PSX router or main server for test flying."""

    def __init__(self, host: str = "127.0.0.1", port: int = 10747, name: str = "FLIGHTTEST"):
        """Talk to PSX at host:port, identifying as name."""
        self.host = host
        self.port = port
        self.name = name
        self._attitude_q = qcode("PiBaHeAlTas")

    def _connect(self) -> socket.socket:
        sock = socket.create_connection((self.host, self.port))
        sock.settimeout(1.0)
        sock.sendall(f"name={self.name}:flighttest\n".encode())
        return sock

    def send(self, lines: list, settle_s: float = 3.0, interval_s: float = 0.0) -> None:
        """Send raw lines (e.g. ["Qi129=1"]) after letting the router welcome us."""
        with self._connect() as sock:
            time.sleep(settle_s)
            for line in lines:
                sock.sendall((line + "\n").encode())
                time.sleep(interval_s)
            time.sleep(1.0)

    def set(self, name: str, value) -> None:
        """Send one variable by name."""
        self.send([f"{qcode(name)}={value}"])

    def load_situ(self, path: str, overrides: Optional[dict] = None,
                  engage_modes: bool = True) -> int:
        """Load a situation (with overrides) like PSX does; return the number of variables sent."""
        lines = [f"{qcode(name)}={value}" for name, value in read_situ(path, overrides)
                 if name not in SKIP_NAMES]
        paused = qcode("SimPaused")
        with self._connect() as sock:
            time.sleep(3.0)
            for line in [f"{paused}=1", "load1", "load2"] + lines + ["load3"]:
                sock.sendall((line + "\n").encode())
            time.sleep(2.0)
            sock.sendall(f"{paused}=0\n".encode())
            time.sleep(2.0)
            # Press HDG SEL and FLCH (MCP buttons are pressed with 1)
            for name in (("McpPshHdgSel", "McpPshFlc") if engage_modes else ()):
                sock.sendall(f"{qcode(name)}=1\n".encode())
                time.sleep(1.0)
        return len(lines)

    def lines(self, seconds: float, demand: tuple = ()) -> Iterator[tuple]:
        """Yield (time, key, value) for everything PSX sends during seconds.

        demand: Q-codes of DEMAND variables, which PSX only sends once asked.
        """
        with self._connect() as sock:
            for code in demand:
                sock.sendall(f"demand={code}\n".encode())
            buf = b""
            end = time.time() + seconds
            while time.time() < end:
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                buf += data
                *complete, buf = buf.split(b"\n")
                now = time.time()
                for raw in complete:
                    line = raw.rstrip(b"\r").decode(errors="replace")
                    key, _, value = line.partition("=")
                    yield now, key, value

    def attitudes(self, seconds: float) -> Iterator[tuple]:
        """Yield (time, Attitude) during seconds."""
        for now, key, value in self.lines(seconds):
            if key == self._attitude_q:
                yield now, Attitude.parse(value)

    def wait_stable(self, target: Target, timeout: float = 150.0,  # pylint: disable=too-many-locals
                    hold: float = 20.0) -> tuple:
        """Wait until flying stably as target says for hold s in a row; return (ok, reason).

        Gives up at once (not stable) if no autopilot (CMD L/C/R) is
        engaged for AP_OFF_S: e.g. a control column input disconnects it.
        """
        start = time.time()
        stable_since = None
        history = []   # (time, alt) for the vertical speed
        reason = "no data"
        autopilot = _AutopilotWatch()
        for now, key, value in self.lines(timeout):
            if autopilot.update(now, key, value):
                return False, f"autopilot disengaged; last {reason}"
            if key != self._attitude_q:
                continue
            att = Attitude.parse(value)
            history = [h for h in history if now - h[0] <= 10.0] + [(now, att.alt_ft)]
            vs_fpm = ((history[-1][1] - history[0][1]) /
                      max(1e-3, history[-1][0] - history[0][0]) * 60.0)
            flown_nm = att.tas_kt * (now - start) / 3600.0 * 1.3   # generous: wind
            failing = [check for check, ok in (
                ("position", distance_nm(target.lat, target.lon, att.lat, att.lon) <=
                 target.pos_nm + flown_nm),
                ("altitude", abs(att.alt_ft - target.alt_ft) <= target.alt_tol_ft),
                ("heading", abs((att.heading - target.heading + 180.0) % 360.0 - 180.0) <=
                 target.hdg_tol_deg),
                ("bank", abs(att.bank) < target.max_bank_deg),
                ("vertical speed", abs(vs_fpm) < target.max_vs_fpm and len(history) > 10),
            ) if not ok]
            reason = (f"pos N{att.lat:.3f} E{att.lon:.3f} alt {att.alt_ft:.0f} "
                      f"hdg {att.heading:.0f} bank {att.bank:.1f} vs {vs_fpm:.0f} "
                      f"tas {att.tas_kt:.0f}; failing: {', '.join(failing) or 'none'}")
            if failing:
                stable_since = None
            elif stable_since is None:
                stable_since = now
            elif now - stable_since >= hold:
                return True, reason
        return False, reason

    def ensure_alt_hold(self, alt_ft: float, seconds: float = 10.0) -> bool:
        """Press ALT HOLD if FLCH is still active within 500 ft of alt_ft; return True if pressed.

        After a reposition FLCH doesn't always hand over to ALT HOLD; it
        then keeps chasing the speed with pitch, e.g. climbing away in
        strong gusts (2026-10-25: from 1,500 to 7,400 ft).
        """
        flch_q, hold_q = qcode("McpPshFlc"), qcode("McpPshAltHld")
        lit: dict = {}
        alt = None
        for _, key, value in self.lines(seconds):
            if key in (flch_q, hold_q):
                lit[key] = value not in ("", "0")
            elif key == self._attitude_q:
                alt = Attitude.parse(value).alt_ft
        if lit.get(flch_q) and not lit.get(hold_q) and alt is not None and \
                abs(alt - alt_ft) < 500.0:
            self.send([f"{hold_q}=1"], settle_s=1.0)
            return True
        return False

    def measure_track(self, seconds: float = 15.0) -> Optional[float]:
        """Return the ground track (deg true) flown over seconds, or None without data."""
        points = [(att.lat, att.lon) for _, att in self.attitudes(seconds)]
        if len(points) < 2:
            return None
        return track_deg(*points[0], *points[-1])

    def correct_track(self, track: float, seconds: float = 15.0) -> int:
        """Turn the heading window (HDG SEL) so the ground track is track; return the change."""
        flown = self.measure_track(seconds)
        if flown is None:
            return 0
        change = round((track - flown + 180.0) % 360.0 - 180.0)
        knob = qcode("McpTurnHdg")   # one degree per click
        self.send([f"{knob}={1 if change > 0 else -1}" for _ in range(abs(change))],
                  settle_s=1.0, interval_s=0.15)
        return change

    def record(self, path: str, seconds: float, names=RECORD_NAMES) -> int:
        """Write "<epoch> <Qcode>=<value>" lines for names during seconds; return the count."""
        wanted = {qcode(name) for name in names}
        demand = tuple(code for code in wanted
                       if psx_variable_validator.lookup(code).mode == "DEMAND")
        count = 0
        with open(path, "w", buffering=1, encoding="utf-8") as out:
            for now, key, value in self.lines(seconds, demand):
                if key in wanted:
                    out.write(f"{now:.3f} {key}={value}\n")
                    count += 1
        return count


# How long no autopilot may be engaged before wait_stable() gives up
AP_OFF_S = 3.0


class _AutopilotWatch:  # pylint: disable=too-few-public-methods
    """Tracks the CMD L/C/R lights; tells when none has been lit for AP_OFF_S."""

    def __init__(self):
        self._codes = {qcode(f"McpPshCmd{side}") for side in "LCR"}
        self._lit: dict = {}
        self._off_since: Optional[float] = None

    def update(self, now: float, key: str, value: str) -> bool:
        """Take one line from PSX; return True if the autopilot has been off too long."""
        if key in self._codes:
            self._lit[key] = value not in ("", "0")
        if len(self._lit) < len(self._codes) or any(self._lit.values()):
            self._off_since = None
            return False
        if self._off_since is None:
            self._off_since = now
        return now - self._off_since >= AP_OFF_S
