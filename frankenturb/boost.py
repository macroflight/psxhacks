"""PSX boost server sample parsing and body-frame acceleration computation.

The boost server streams high-frequency (≈50 Hz) position and attitude data
intended for motion-platform drivers.  This module parses those lines and
derives the six-degree-of-freedom accelerations felt by cockpit occupants.

Coordinate conventions
----------------------
Body frame (standard aerospace, right-hand):
  x_b : forward  — positive toward nose
  y_b : right    — positive toward starboard
  z_b : down     — positive toward belly

Translational output (specific force / g, dimensionless):
  heave_g : along −z_b  (+1.0 in steady level flight)
  surge_g : along  x_b  (positive = structural force toward nose,
                          e.g. you feel pushed back during acceleration)
  sway_g  : along  y_b  (positive = structural force to starboard,
                          e.g. you feel pushed right during a left turn)

Angular rate output (body frame, deg/s):
  roll_rate_dps  : p — rotation about x_b (right wing down = positive)
  pitch_rate_dps : q — rotation about y_b (nose up = positive)
  yaw_rate_dps   : r — rotation about z_b (nose right = positive)

Computation
-----------
A rolling buffer of BoostSamples is kept.  Once the buffer spans at least
_MIN_WINDOW_S seconds, first/middle/last samples form a non-uniform three-
point finite-difference stencil that estimates NED inertial acceleration.
The gravity vector is subtracted and the result rotated to the body frame.

Angular rates use a central difference of Euler angles across the full window
span, converted to body rates via the kinematic equations:
  p = φ̇ − ψ̇ sin θ
  q = θ̇ cos φ + ψ̇ cos θ sin φ
  r = −θ̇ sin φ + ψ̇ cos θ cos φ

A longer baseline (≈1 s rather than one inter-sample interval) is used
deliberately: altitude is encoded in 0.01 ft steps, so a short baseline
would produce unacceptable quantisation noise in the vertical acceleration.
"""

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

G_MS2 = 9.80665           # standard gravity (m/s²)
FT_TO_M = 0.3048
KT_TO_MS = 0.514444
M_PER_DEG_LAT = 111_320.0

# Buffer tuning
_MAX_SAMPLES = 120         # keep up to ≈2 s at 60 Hz
_MIN_WINDOW_S = 0.4        # require at least this span before computing


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class BoostSample:  # pylint: disable=too-many-instance-attributes
    """One parsed line from the PSX boost server.

    Protocol (semicolon-separated):
      status ; alt*100 ; hdg*100 ; pitch*100 ; bank*100 ; lat ; lon ; ms_mod
    """

    is_flight: bool       # True = airborne (F), False = on ground (G)
    alt_ft: float         # flightdeck altitude (feet)
    heading_deg: float    # 0–360, clockwise from north
    pitch_deg: float      # positive = nose up
    bank_deg: float       # positive = right wing down
    lat: float            # latitude  (degrees)
    lon: float            # longitude (degrees)
    ms_mod: int           # raw millisecond timestamp modulo 1000
    wall_time: float      # time.monotonic() at time of receipt


@dataclass
class AccelerationState:
    """Body-frame accelerations and angular rates derived from boost data.

    See module docstring for full coordinate-system description.
    """

    heave_g: float = 1.0           # normal load factor  (1.0 in level flight)
    surge_g: float = 0.0           # longitudinal specific force / g
    sway_g: float = 0.0            # lateral specific force / g
    roll_rate_dps: float = 0.0     # body roll  rate (deg/s)
    pitch_rate_dps: float = 0.0    # body pitch rate (deg/s)
    yaw_rate_dps: float = 0.0      # body yaw   rate (deg/s)
    ground_speed_kt: float = 0.0   # horizontal speed over ground (knots)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_boost_line(line: str, wall_time: Optional[float] = None) -> Optional[BoostSample]:
    """Parse one line from the PSX boost server.

    Returns None if the line is malformed.
    """
    if wall_time is None:
        wall_time = time.monotonic()
    parts = line.strip().split(';')
    if len(parts) != 8:
        return None
    try:
        return BoostSample(
            is_flight=parts[0].strip().upper() == 'F',
            alt_ft=int(parts[1]) / 100.0,
            heading_deg=int(parts[2]) / 100.0 % 360.0,
            pitch_deg=int(parts[3]) / 100.0,
            bank_deg=int(parts[4]) / 100.0,
            lat=float(parts[5]),
            lon=float(parts[6]),
            ms_mod=int(parts[7]) % 1000,
            wall_time=wall_time,
        )
    except (ValueError, IndexError):
        return None


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _angle_diff(a: float, b: float) -> float:
    """Signed angular difference b − a in degrees, wrapped to (−180, +180]."""
    return (b - a + 180.0) % 360.0 - 180.0


def _ned_to_body(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    n: float, e: float, d: float,
    psi: float, theta: float, phi: float,
) -> tuple[float, float, float]:
    """Rotate a NED vector into the body frame using ZYX Euler angles.

    Parameters: psi = heading, theta = pitch, phi = bank (all radians).
    """
    cp, sp = math.cos(psi), math.sin(psi)
    ct, st = math.cos(theta), math.sin(theta)
    cr, sr = math.cos(phi), math.sin(phi)

    x_b = ct * cp * n + ct * sp * e - st * d
    y_b = (sr * st * cp - cr * sp) * n + (sr * st * sp + cr * cp) * e + sr * ct * d
    z_b = (cr * st * cp + sr * sp) * n + (cr * st * sp - sr * cp) * e + cr * ct * d
    return x_b, y_b, z_b


# ---------------------------------------------------------------------------
# Acceleration computer
# ---------------------------------------------------------------------------

class AccelerationComputer:  # pylint: disable=too-few-public-methods
    """Derive body-frame accelerations from a continuous stream of BoostSamples.

    Feed samples via update(); an AccelerationState is returned once enough
    history has accumulated.  See module docstring for the computation method.
    """

    def __init__(self) -> None:
        """Initialise with an empty sample buffer."""
        self._buf: deque[BoostSample] = deque(maxlen=_MAX_SAMPLES)

    def update(self, sample: BoostSample) -> Optional[AccelerationState]:
        """Add one sample; return AccelerationState when the buffer is ready."""
        self._buf.append(sample)
        if len(self._buf) < 3:
            return None
        span = self._buf[-1].wall_time - self._buf[0].wall_time
        if span < _MIN_WINDOW_S:
            return None
        return self._compute()

    def _compute(self) -> AccelerationState:  # pylint: disable=too-many-locals
        """Compute accelerations from first, middle, and last buffered samples."""
        buf = self._buf
        s0 = buf[0]
        s1 = buf[len(buf) // 2]
        s2 = buf[-1]

        dt01 = s1.wall_time - s0.wall_time
        dt12 = s2.wall_time - s1.wall_time
        if dt01 < 1e-4 or dt12 < 1e-4:
            return AccelerationState()

        # ---- Euler angles and rates at the midpoint (s1) -------------------
        psi = math.radians(s1.heading_deg)
        theta = math.radians(s1.pitch_deg)
        phi = math.radians(s1.bank_deg)

        dt_ang = s2.wall_time - s0.wall_time
        phi_dot = math.radians(_angle_diff(s0.bank_deg, s2.bank_deg)) / dt_ang
        theta_dot = math.radians(_angle_diff(s0.pitch_deg, s2.pitch_deg)) / dt_ang
        psi_dot = math.radians(_angle_diff(s0.heading_deg, s2.heading_deg)) / dt_ang

        # Euler kinematics → body-frame angular rates
        p_rad = phi_dot - psi_dot * math.sin(theta)
        q_rad = theta_dot * math.cos(phi) + psi_dot * math.cos(theta) * math.sin(phi)
        r_rad = -theta_dot * math.sin(phi) + psi_dot * math.cos(theta) * math.cos(phi)

        # ---- NED positions relative to s1 (metres) -------------------------
        cos_lat = math.cos(math.radians(s1.lat))
        m_lon = M_PER_DEG_LAT * cos_lat

        n0 = (s0.lat - s1.lat) * M_PER_DEG_LAT
        e0 = (s0.lon - s1.lon) * m_lon
        d0 = -(s0.alt_ft - s1.alt_ft) * FT_TO_M   # down positive

        n2 = (s2.lat - s1.lat) * M_PER_DEG_LAT
        e2 = (s2.lon - s1.lon) * m_lon
        d2 = -(s2.alt_ft - s1.alt_ft) * FT_TO_M
        # s1 is (0, 0, 0) by construction

        # Ground speed: central-difference horizontal velocity at s1
        gs_n = (n2 - n0) / dt_ang    # m/s north
        gs_e = (e2 - e0) / dt_ang    # m/s east
        ground_speed_kt = math.hypot(gs_n, gs_e) / KT_TO_MS

        # Non-uniform 3-point second-derivative stencil at s1:
        #   a = 2 × [(x2−x1)/dt12 − (x1−x0)/dt01] / (dt01+dt12)
        # with x1 = 0  →  (x1−x0)/dt01 = −x0/dt01
        inv01 = 1.0 / dt01
        inv12 = 1.0 / dt12
        scale = 2.0 / (dt01 + dt12)

        # Horizontal: in coordinated flight centripetal = g·tan(bank), directed
        # 90° right of heading.  Using V·ψ̇ instead requires ground speed from
        # lat/lon, which lags heading when PSX updates position at a lower rate
        # than attitude — causing the centripetal vector to mis-align with psi
        # and leak into sway.  Bank angle has no such timing issue.
        centripetal = G_MS2 * math.tan(phi)
        a_n = -centripetal * math.sin(psi)
        a_e = centripetal * math.cos(psi)
        # Vertical: altitude is encoded at 0.01 ft, so the stencil is reliable.
        a_d = (d2 * inv12 + d0 * inv01) * scale

        # Specific force = inertial acceleration − gravity
        # Gravity in NED: [0, 0, +g_ms2]  (pointing in +Down direction)
        f_n = a_n
        f_e = a_e
        f_d = a_d - G_MS2

        # Rotate specific force to body frame
        f_x, f_y, f_z = _ned_to_body(f_n, f_e, f_d, psi, theta, phi)

        return AccelerationState(
            heave_g=-f_z / G_MS2,
            surge_g=f_x / G_MS2,
            sway_g=f_y / G_MS2,
            roll_rate_dps=math.degrees(p_rad),
            pitch_rate_dps=math.degrees(q_rad),
            yaw_rate_dps=math.degrees(r_rad),
            ground_speed_kt=ground_speed_kt,
        )
