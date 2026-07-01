"""PSX wind corridor format detection, parsing, and FWIND waypoint injection.

PSX WxCorridorTxt is a caret-delimited (^) string; each section is one row.
Five standard formats are defined in the PSX documentation:

  Format A  Fixed FL header, compact 8-char records: {dir/10:02d}{spd:03d}{M/P}{oat:02d}
            6 wind records per leg.

  Format B  Variable FL, 4-column waypoint groups.
            Row encoding: {fl} {ddd/sss} {±oo} repeated 4 times per row.

  Format C  Variable FL, 3-column waypoint groups.  Same encoding as B.

  Format D  Identical to C but each waypoint group is followed by a section of
            space-separated latitude values and then a section of space-separated
            longitude values, so PSX can place the points on the map.

  Format E  One waypoint per section, inline lat/lon at the start.
            Wind: 5-char {dir/10:02d}{spd:03d}.  OAT: separate {M/P}{oat:02d} field.
            Also carries ITT and DIS fields (unused by the corridor system).

FWIND injection is implemented for Formats A and E.  Formats B, C and D are
detected and left unchanged (the caller receives a log message explaining why).
"""
import math
import re
from typing import Optional

FORMAT_A = 'A'
FORMAT_B = 'B'
FORMAT_C = 'C'
FORMAT_D = 'D'
FORMAT_E = 'E'
FORMAT_UNKNOWN = 'unknown'

_FWIND_NAME = 'FWIND'

# ---------------------------------------------------------------------------
# Compiled regular expressions
# ---------------------------------------------------------------------------

# Format E: a section starting with an inline lat/lon coordinate.
# Minutes field uses 4 chars: either "56.8" or " 6.5" (leading space when < 10).
# We use [ \d] to accept both forms.
_E_WAYPT_RE = re.compile(
    r'^([NS]\d{2}[ \d]\d\.[0-9][EW]\d{3}[ \d]\d\.[0-9])\s+(\S+)')
_E_LATLON_RE = re.compile(
    r'^([NS])(\d{2})([ \d]\d\.[0-9])([EW])(\d{3})([ \d]\d\.[0-9])')

# Format A: a section whose first field is a waypoint name and second field is
# a compact 8-char wind/OAT record.
_A_WAYPT_RE = re.compile(r'^([A-Z][A-Z0-9]{0,5})\s+(\d{5}[MP]\d{2})')

# Format A FL header: a section that contains only space-separated altitude values
# (e.g. "19000 23000 29000 31000 33000 35000").
_A_FL_HEADER_RE = re.compile(r'^\d{3,5}(\s+\d{3,5})+$')

# Format B/C data rows: {fl} {ddd/sss} {±oo} repeated N times.
_BC_DATA_ROW_RE = re.compile(r'^(?:FL)?\d{2,5}\s+\d{3}/\d{3}\s+[+-]\d')

# Format D lat-only / lon-only sections following a waypoint-name section.
_D_LAT_SECTION_RE = re.compile(r'^[NS]\d{2}[\d.]+(\s+[NS]\d{2}[\d.]+)+$')
_D_LON_SECTION_RE = re.compile(r'^[EW]\d{3}[\d.]+(\s+[EW]\d{3}[\d.]+)+$')


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_format(corridor_txt: str) -> str:
    """Return the PSX wind corridor format identifier (FORMAT_A … FORMAT_E or FORMAT_UNKNOWN)."""
    if not corridor_txt or not corridor_txt.strip():
        return FORMAT_UNKNOWN
    secs = [s.strip() for s in corridor_txt.split('^')]
    # Format E: any section starts with an inline lat/lon coordinate.
    if any(_E_WAYPT_RE.match(s) for s in secs):
        return FORMAT_E
    # Format D: lat-only sections followed (or preceded) by lon-only sections.
    has_lat = any(_D_LAT_SECTION_RE.match(s) for s in secs)
    has_lon = any(_D_LON_SECTION_RE.match(s) for s in secs)
    if has_lat and has_lon:
        return FORMAT_D
    # Format A: a numeric FL header section AND at least one compact waypoint section.
    # Requiring both prevents misidentifying Format C's compact variant (which has the
    # same 8-char wind records but no standalone FL header) as Format A.
    has_fl_header = any(_A_FL_HEADER_RE.match(s) for s in secs)
    has_compact_waypt = any(_A_WAYPT_RE.match(s) for s in secs)
    if has_fl_header and has_compact_waypt:
        return FORMAT_A
    # Format B/C: data rows with ddd/sss ±oo encoding.
    data_rows = [s for s in secs if _BC_DATA_ROW_RE.match(s)]
    if data_rows:
        max_cols = max(len(re.findall(r'\d{3}/\d{3}', r)) for r in data_rows)
        return FORMAT_B if max_cols >= 4 else FORMAT_C
    return FORMAT_UNKNOWN


# ---------------------------------------------------------------------------
# Coordinate conversion (Format E / D)
# ---------------------------------------------------------------------------

def _dms_to_dec(coord: str) -> Optional[tuple]:
    """Parse a Format-E combined lat/lon string to (lat_deg, lon_deg)."""
    m = _E_LATLON_RE.match(coord)
    if not m:
        return None
    lat = (int(m.group(2)) + float(m.group(3)) / 60.0) * (-1 if m.group(1) == 'S' else 1)
    lon = (int(m.group(5)) + float(m.group(6)) / 60.0) * (-1 if m.group(4) == 'W' else 1)
    return lat, lon


def _dec_to_dms(lat: float, lon: float) -> str:
    """Convert decimal degrees to a Format-E combined lat/lon string (e.g. S3356.8E15110.6).

    Uses zero-padded minutes ("06.5") to avoid an embedded space that would
    split the coordinate into two tokens when the section is split on whitespace.
    """
    def _half(deg: float, is_lon: bool) -> str:
        neg = deg < 0
        deg = abs(deg)
        d = int(deg)
        m = round((deg - d) * 60.0, 1)
        if m >= 60.0:
            m -= 60.0
            d += 1
        hem = ('W' if neg else 'E') if is_lon else ('S' if neg else 'N')
        mins = f"{m:.1f}".zfill(4)          # "06.5" not " 6.5"
        return f"{hem}{d:03d}{mins}" if is_lon else f"{hem}{d:02d}{mins}"
    return _half(lat, False) + _half(lon, True)


# ---------------------------------------------------------------------------
# Wind / temperature encoding
# ---------------------------------------------------------------------------

def encode_wind_e(wind_dir: float, wind_spd: float) -> str:
    """Encode wind as a Format-E 5-char string, e.g. '26026' for 260°/26 kt."""
    dir_tens = int(round(wind_dir / 10)) % 36
    return f"{dir_tens:02d}{int(round(wind_spd)):03d}"


def encode_temp_e(oat_c: float) -> str:
    """Encode OAT as a Format-E 3-char string, e.g. 'P13' or 'M26'."""
    t = int(round(oat_c))
    return f"M{-t:02d}" if t < 0 else f"P{t:02d}"


def encode_wind_a(wind_dir: float, wind_spd: float, oat_c: float) -> str:
    """Format-A compact wind/OAT encoding: 8 chars, e.g. '28031M17' for 280°/31kt/−17°C."""
    dir_tens = int(round(wind_dir / 10)) % 36
    t = int(round(oat_c))
    sign = 'M' if t < 0 else 'P'
    return f"{dir_tens:02d}{int(round(wind_spd)):03d}{sign}{abs(t):02d}"


# ---------------------------------------------------------------------------
# FWIND removal
# ---------------------------------------------------------------------------

def _remove_fwind_e(secs: list) -> tuple:
    """Remove Format-E FWIND sections.  Returns (new_secs, removed_flag)."""
    out, removed = [], False
    for sec in secs:
        m = _E_WAYPT_RE.match(sec.strip())
        if m and m.group(2).upper() == _FWIND_NAME:
            removed = True
        else:
            out.append(sec)
    return out, removed


def _remove_fwind_a(secs: list) -> tuple:
    """Remove Format-A FWIND sections.  Returns (new_secs, removed_flag)."""
    out, removed = [], False
    for sec in secs:
        m = _A_WAYPT_RE.match(sec.strip())
        if m and m.group(1).upper() == _FWIND_NAME:
            removed = True
        else:
            out.append(sec)
    return out, removed


# ---------------------------------------------------------------------------
# Waypoint list extraction
# ---------------------------------------------------------------------------

def _waypoints_e(secs: list) -> list:
    """Return [(section_idx, lat, lon, name)] for every Format-E waypoint section."""
    result = []
    for i, sec in enumerate(secs):
        m = _E_WAYPT_RE.match(sec.strip())
        if not m:
            continue
        coords = _dms_to_dec(m.group(1))
        if coords:
            result.append((i, coords[0], coords[1], m.group(2)))
    return result


def _waypoints_a(secs: list) -> list:
    """Return [(section_idx, name)] for every Format-A waypoint section."""
    result = []
    for i, sec in enumerate(secs):
        m = _A_WAYPT_RE.match(sec.strip())
        if m:
            result.append((i, m.group(1)))
    return result


# ---------------------------------------------------------------------------
# Insertion position (Format E uses lat/lon; Format A inserts at position 0)
# ---------------------------------------------------------------------------

def _find_insert_pos_e(waypoints: list, ac_lat: float, ac_lon: float) -> tuple:
    """Return (insert_after_sec_idx, prev_name, next_name) for Format-E FWIND insertion.

    Chooses the leg whose midpoint is closest to the aircraft position.
    """
    if len(waypoints) == 1:
        return waypoints[0][0] - 1, None, waypoints[0][3]

    best_leg, best_dist = 0, float('inf')
    for leg in range(len(waypoints) - 1):
        a_lat, a_lon = waypoints[leg][1], waypoints[leg][2]
        b_lat, b_lon = waypoints[leg + 1][1], waypoints[leg + 1][2]
        mid_lat = (a_lat + b_lat) / 2
        mid_lon = (a_lon + b_lon) / 2
        dist = ((ac_lat - mid_lat) ** 2 +
                ((ac_lon - mid_lon) * math.cos(math.radians(mid_lat))) ** 2)
        if dist < best_dist:
            best_dist = dist
            best_leg = leg

    return (waypoints[best_leg][0],
            waypoints[best_leg][3],
            waypoints[best_leg + 1][3])


# ---------------------------------------------------------------------------
# Format-specific update functions
# ---------------------------------------------------------------------------

def _count_wv_cols(sec: str) -> int:
    """Return the number of W/V columns in a Format-E waypoint section."""
    m = _E_LATLON_RE.match(sec.strip())
    if not m:
        return 1
    # Fields after coord: name, ITT, DIS, FL, TMP, then W/V columns
    return max(1, len(sec.strip()[m.end():].strip().split()) - 5)


def _update_e(secs: list, ac_lat: float, ac_lon: float, ac_alt_ft: float,  # pylint: disable=too-many-arguments,too-many-positional-arguments
              wind_dir: float, wind_spd: float, oat_c: float) -> tuple:
    """Insert FWIND into a Format-E corridor.  Returns (new_secs, log_msg)."""
    secs = _remove_fwind_e(secs)[0]
    waypoints = _waypoints_e(secs)
    if not waypoints:
        return None, "no waypoints found in Format-E corridor"

    insert_after, prev_name, next_name = _find_insert_pos_e(waypoints, ac_lat, ac_lon)
    wind_str = encode_wind_e(wind_dir, wind_spd)
    temp_str = encode_temp_e(oat_c)
    fl = int(ac_alt_ft / 100)
    secs.insert(insert_after + 1,
                f"{_dec_to_dms(ac_lat, ac_lon)} {_FWIND_NAME} 000 000 "
                f"{fl:03d} {temp_str} " +
                " ".join([wind_str] * _count_wv_cols(secs[waypoints[0][0]])))
    msg = (f"Format E: inserted {_FWIND_NAME} "
           f"(wind {wind_str} OAT {temp_str} FL{fl:03d}) " +
           (f"between {prev_name} and {next_name}" if prev_name and next_name
            else f"before {next_name}" if next_name
            else f"after {prev_name}"))
    return secs, msg


def _update_a(secs: list, ac_alt_ft: float,  # pylint: disable=unused-argument
              wind_dir: float, wind_spd: float, oat_c: float) -> tuple:
    """Insert FWIND into a Format-A corridor.  Returns (new_secs, log_msg)."""
    secs, _ = _remove_fwind_a(secs)
    waypoints = _waypoints_a(secs)
    if not waypoints:
        return None, "no waypoints found in Format-A corridor"

    # Count wind records from the first existing waypoint
    sample = secs[waypoints[0][0]].strip().split()
    num_wv = max(1, len(sample) - 1)       # subtract the waypoint name field

    wind_rec = encode_wind_a(wind_dir, wind_spd, oat_c)
    fwind = f"{_FWIND_NAME} " + " ".join([wind_rec] * num_wv)

    # Insert before the first existing waypoint
    first_waypt_idx = waypoints[0][0]
    secs.insert(first_waypt_idx, fwind)

    first_name = waypoints[0][1]
    wind_str = encode_wind_e(wind_dir, wind_spd)
    temp_str = encode_temp_e(oat_c)
    msg = (f"Format A: inserted {_FWIND_NAME} "
           f"(wind {wind_str} OAT {temp_str}) before {first_name}")
    return secs, msg


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def update_corridor(corridor_txt: str,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                    ac_lat: float,
                    ac_lon: float,
                    ac_alt_ft: float,
                    wind_dir: float,
                    wind_spd: float,
                    oat_c: float) -> tuple:
    """Remove any existing FWIND waypoint and insert an updated one.

    Returns ``(new_corridor_txt, log_message)``.  If injection is skipped,
    ``new_corridor_txt`` is ``None`` and ``log_message`` explains why.
    """
    fmt = detect_format(corridor_txt)
    secs = corridor_txt.split('^')

    if fmt == FORMAT_E:
        new_secs, msg = _update_e(secs, ac_lat, ac_lon, ac_alt_ft,
                                  wind_dir, wind_spd, oat_c)
    elif fmt == FORMAT_A:
        new_secs, msg = _update_a(secs, ac_alt_ft, wind_dir, wind_spd, oat_c)
    elif fmt in (FORMAT_B, FORMAT_C, FORMAT_D):
        return None, f"FWIND injection not supported for corridor Format {fmt}"
    else:
        return None, "FWIND injection skipped: unrecognised corridor format"

    if new_secs is None:
        return None, msg
    return "^".join(new_secs), msg
