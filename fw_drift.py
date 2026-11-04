"""CB cells that drift with the wind (frankenweather --advanced-zones, testing).

A weather zone that has a CB becomes a drifting cell: it keeps the weather
it had when the drift started (a parcel, never re-sampled on the way) and
moves steadily with the cloud-layer wind, the pressure-weighted mean wind
between 850 and 300 hPa, turned 5 degrees to the right. Ordinary
thunderstorm cells move roughly like that (radar studies: 500 hPa wind
direction +5 deg at 70 %, or the 0-6 km mean +5 deg at 135 % of its speed);
multicell propagation and supercell right-moves can't be modelled with one
CB per zone. After a random lifetime of _LIFE_MIN_S.._LIFE_MAX_S the CB
decays over _DECAY_S, and the zone is an ordinary zone again.

PSX always draws a zone's CB 7 nm (CB_OFFSET_NM) from the zone centre, on a
bearing that goes round once an hour (frankenturb/cb.py's cb_bearing_deg(),
Hardy's formula), and the zone elevation (ft) adds to that bearing
(1 ft = 1 degree). So a cell is the CB's own position, and at every step
(DriftCell.place()) the zone's elevation is set 0-359 ft above its own to
keep the bearing the CB had when the drift started (it goes down 6 ft a
minute, and up 360 ft once an hour), and the zone centre is put
CB_OFFSET_NM from the CB, opposite that bearing. The CB moves only with
the wind, and the zone centre with it. Measured on the test sim
(2026-11-04): up to +1,000 ft of zone elevation didn't move the PFD
altitude, and +60 ft over 10 minutes moved the OAT 0.1 C. Moving the
centre round the CB instead (one turn an hour, 7 nm out) would make the
other zones' distances to it swing by 7 nm.

Pure helpers, no PSX connection: frankenweather.py owns the zones and does
the writing.
"""
import math
import random
from dataclasses import dataclass, field
from typing import Optional

from frankenturb.cb import CB_OFFSET_NM, cb_bearing_deg

_NM_TO_M = 1852.0
# Pressure levels (hPa) for the cloud-layer mean wind; each weighted by the
# pressure depth it stands for (trapezoid rule over 850-300 hPa)
_MEAN_WIND_LEVELS_HPA = (850.0, 700.0, 500.0, 400.0, 300.0)
_VEER_DEG = 5.0              # cells move slightly right of the mean wind
_LIFE_MIN_S = 30 * 60.0
_LIFE_MAX_S = 60 * 60.0
_DECAY_S = 5 * 60.0          # the CB fades to nothing over this long at the end
_MIN_SPEED_KT = 3.0          # below this a cell just stays put


def mean_wind_motion(profile, speed_pct: float = 100.0) -> Optional[tuple]:  # pylint: disable=too-many-locals
    """Return (heading_deg, speed_kt) a cell moves with, from a WindProfile, or None.

    heading_deg is the direction the cell moves TOWARDS: the
    pressure-weighted 850-300 hPa mean wind (which blows FROM its
    direction), turned _VEER_DEG to the right, at speed_pct of its speed.
    None without a profile or when the mean wind is calm.
    """
    if profile is None:
        return None
    levels = _MEAN_WIND_LEVELS_HPA
    weights = []
    for i, _ in enumerate(levels):
        upper = levels[i - 1] if i > 0 else levels[0]
        lower = levels[i + 1] if i + 1 < len(levels) else levels[-1]
        weights.append((upper - lower) / 2.0)
    sum_u = sum_v = 0.0
    for hpa, weight in zip(levels, weights):
        spd, frm = profile.wind_at_pressure(hpa)
        towards = math.radians((frm + 180.0) % 360.0)
        sum_u += weight * spd * math.sin(towards)
        sum_v += weight * spd * math.cos(towards)
    total = sum(weights)
    u, v = sum_u / total, sum_v / total
    speed = math.hypot(u, v) * speed_pct / 100.0
    if speed < _MIN_SPEED_KT:
        return None
    heading = (math.degrees(math.atan2(u, v)) + _VEER_DEG) % 360.0
    return heading, speed


def cb_oktas(wx: str) -> int:
    """Return the CB coverage (oktas, field 9) of a Wx<N> zone string, 0 if none."""
    parts = wx.split(';')
    return int(parts[9]) if len(parts) > 11 and parts[9].isdigit() else 0


def with_cb_of(wx: str, cb_wx: str) -> str:
    """Return wx (another zone's weather) carrying the CB (fields 9-11) of cb_wx."""
    parts, cb = wx.split(';'), cb_wx.split(';')
    if len(parts) <= 11 or len(cb) <= 11:
        return cb_wx
    parts[9:12] = cb[9:12]
    return ';'.join(parts)


def with_oktas(wx: str, oktas: int) -> str:
    """Return wx with its CB coverage set to oktas."""
    parts = wx.split(';')
    if len(parts) > 9:
        parts[9] = str(max(0, oktas))
    return ';'.join(parts)


@dataclass
class DriftCell:  # pylint: disable=too-many-instance-attributes
    """One drifting CB zone: where its CB is, how it moves, and what it carries."""

    zone: int
    lat: float                  # the CB itself (not the zone centre)
    lon: float
    heading_deg: float          # moves towards
    speed_kt: float
    wx: str                     # the zone weather it keeps (with its CB)
    mode: str                   # the zone's WxMode; only its position is ever changed
    icao: str                   # the zone's WxMode name
    started: float              # time.monotonic()
    clat: float = 0.0           # the zone centre last placed (see place())
    clon: float = 0.0
    test: str = ""              # a test CB (frankenweather's /weather/cbtest): what it is
    stepped_at: float = math.nan   # time.monotonic() of the last step (position_at())
    wind_motion: Optional[tuple] = None   # test CB: (heading, kt) the mean wind would give it
    elev0_ft: float = math.nan  # the zone's own WxMode elevation (mode's is raised from it)
    bearing: Optional[float] = None   # the CB's bearing from the zone centre, held
    lifetime_s: float = field(default_factory=lambda: random.uniform(_LIFE_MIN_S, _LIFE_MAX_S))
    decay_from: Optional[float] = None   # time.monotonic() the decay started
    decay_why: str = ""
    start_oktas: int = 0
    moved_nm: float = 0.0

    def __post_init__(self):
        """Remember the CB coverage the decay starts from, and the zone's own elevation."""
        self.start_oktas = cb_oktas(self.wx)
        if math.isnan(self.elev0_ft):
            self.elev0_ft = self.elev_ft

    def minutes_left(self, now: float) -> float:
        """Return the minutes until the decay starts (0 once decaying)."""
        if self.decay_from is not None:
            return 0.0
        if self.test:
            return math.inf   # a test CB stays until it's removed
        return max(0.0, (self.started + self.lifetime_s - now) / 60.0)

    def start_decay(self, now: float, why: str) -> bool:
        """Start the CB's decay (once); return True if it started now."""
        if self.decay_from is not None:
            return False
        self.decay_from = now
        self.decay_why = why
        self.start_oktas = cb_oktas(self.wx)
        return True

    def decayed_oktas(self, now: float) -> int:
        """Return the CB coverage the decay has reached by now."""
        if self.decay_from is None:
            return cb_oktas(self.wx)
        frac = max(0.0, 1.0 - (now - self.decay_from) / _DECAY_S)
        return int(math.ceil(self.start_oktas * frac)) if frac > 0 else 0

    @property
    def elev_ft(self) -> float:
        """Return the zone's WxMode elevation (ft), which turns PSX's CB bearing."""
        try:
            return float(self.mode.split(';')[3])
        except (IndexError, ValueError):
            return 0.0

    def place(self, geod, time_earth_ms: float, lead_ms: float = 0.0) -> tuple:
        """Set the zone's elevation and centre so PSX draws its CB at the cell lead_ms from now.

        The elevation keeps the CB's bearing from the zone centre (see the
        module docstring); the centre is then CB_OFFSET_NM from the cell,
        opposite that bearing (as PSX computes it from the whole-foot
        elevation written). PSX turns the CB on between writes (6 deg a
        minute), so a lead of half the write interval puts the CB on the
        cell in the middle of it: it swings either side instead of creeping
        away and jumping back (seen in MSFS, 2026-11-04: three small steps,
        then one back). Returns the centre (lat, lon); self.mode gets both.
        """
        if self.bearing is None:   # the bearing the CB has now, where it is
            self.bearing = cb_bearing_deg(int(time_earth_ms), self.elev0_ft)
        self.mode = self.mode_for(geod, time_earth_ms + lead_ms, self.lat, self.lon)
        self.clat, self.clon = wxmode_pos(self.mode)
        return self.clat, self.clon

    def mode_for(self, geod, time_earth_ms: float, lat: float, lon: float) -> str:
        """Return the WxMode that makes PSX draw this zone's CB at (lat, lon) at time_earth_ms.

        Doesn't change the cell (place() does, for PSX itself); also used
        for the feed to PSX.NET.Orchestration, which draws the CBs in MSFS
        from the zones it's given (frankenweather's WXMODES messages).
        """
        if self.bearing is None:
            self.bearing = cb_bearing_deg(int(time_earth_ms), self.elev0_ft)
        at_ms = int(time_earth_ms)
        time_term = cb_bearing_deg(at_ms, 0.0)
        raise_ft = round((self.bearing - time_term - self.elev0_ft) % 360.0) % 360
        elev_ft = round(self.elev0_ft) + raise_ft
        bearing = cb_bearing_deg(at_ms, elev_ft)
        clon, clat, _ = geod.fwd(lons=lon, lats=lat, az=(bearing + 180.0) % 360.0,
                                 dist=CB_OFFSET_NM * _NM_TO_M)
        return wxmode_at(self.mode, clat, ((clon + 180.0) % 360.0) - 180.0, elev_ft)

    def position_at(self, geod, now: float, moving: bool) -> tuple:
        """Return where the CB is at time.monotonic() now: the last step, run on if moving."""
        if not moving or self.speed_kt <= 0.0 or math.isnan(self.stepped_at):
            return self.lat, self.lon
        dist_nm = self.speed_kt * max(0.0, now - self.stepped_at) / 3600.0
        lon, lat, _ = geod.fwd(lons=self.lon, lats=self.lat, az=self.heading_deg,
                               dist=dist_nm * _NM_TO_M)
        return lat, ((lon + 180.0) % 360.0) - 180.0

    def step(self, geod, dt_s: float) -> None:
        """Move the cell along its heading for dt_s seconds."""
        dist_nm = self.speed_kt * dt_s / 3600.0
        if dist_nm <= 0:
            return
        lon, lat, _ = geod.fwd(lons=self.lon, lats=self.lat, az=self.heading_deg,
                               dist=dist_nm * _NM_TO_M)
        self.lat, self.lon = lat, ((lon + 180.0) % 360.0) - 180.0
        self.moved_nm += dist_nm


def pos_text(lat: float, lon: float) -> str:
    """Return a position as degrees and decimal minutes, e.g. "N10 00.7 W067 39.6"."""
    def dm(value, pos, neg, width):
        deg, tenths = divmod(round(abs(value) * 600.0), 600)   # rounded first: no "60.0"
        return f"{pos if value >= 0 else neg}{deg:0{width}d} {tenths / 10.0:04.1f}"
    return f"{dm(lat, 'N', 'S', 2)} {dm(lon, 'E', 'W', 3)}"


def wxmode_at(mode: str, lat: float, lon: float, elev_ft: Optional[float] = None) -> str:
    """Return a WxMode<N> string moved to (lat, lon) (and elev_ft), everything else unchanged."""
    parts = mode.split(';')
    parts[0] = f"{math.radians(lat):.8f}"
    parts[1] = f"{math.radians(lon):.8f}"
    if elev_ft is not None and len(parts) > 3:
        parts[3] = str(int(round(elev_ft)))
    return ';'.join(parts)


def cb_position(geod, mode: str, time_earth_ms: float) -> Optional[tuple]:
    """Return (lat, lon) of the CB PSX draws for a zone with this WxMode now, or None."""
    pos = wxmode_pos(mode)
    if pos is None:
        return None
    try:
        elev_ft = float(mode.split(';')[3])
    except (IndexError, ValueError):
        elev_ft = 0.0
    bearing = cb_bearing_deg(int(time_earth_ms), elev_ft)
    lon, lat, _ = geod.fwd(lons=pos[1], lats=pos[0], az=bearing, dist=CB_OFFSET_NM * _NM_TO_M)
    return lat, ((lon + 180.0) % 360.0) - 180.0


def wxmode_pos(mode: str) -> Optional[tuple]:
    """Return (lat, lon) in degrees from a WxMode<N> string, or None."""
    try:
        parts = mode.split(';')
        return math.degrees(float(parts[0])), math.degrees(float(parts[1]))
    except (AttributeError, IndexError, ValueError):
        return None
