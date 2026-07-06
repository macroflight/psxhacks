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

# PSX's WxCorridorTxt always begins with a literal '#' marker character ahead
# of the first ('^'-delimited) section. All parsing entry points strip it
# (if present) before splitting into sections; build_corridor_a() re-adds it
# when generating a corridor from scratch. Without it, PSX's positional
# Format-A parser reads every fixed-width column one character short,
# silently dropping the leading digit of the first value.
_CORRIDOR_PREFIX = '#'


def _strip_corridor_prefix(corridor_txt: str) -> str:
    """Strip PSX's leading '#' corridor marker, if present."""
    if corridor_txt.startswith(_CORRIDOR_PREFIX):
        return corridor_txt[len(_CORRIDOR_PREFIX):]
    return corridor_txt


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

# Format E, multi-column variant ("N wind records per leg", per the PSX NG FMC
# manual): a section of only 3-digit FL values (e.g. "310   330   350   370")
# that precedes a run of waypoint sections, giving the FL each of that row's
# extra wind-only tokens belongs to. Applies until the next such header.
_E_FL_HEADER_RE = re.compile(r'^\d{3}(\s+\d{3})+$')

# Format E wind-only token (no OAT): 5 digits, {dir/10:02d}{spd:03d}.
_E_WIND_ONLY_RE = re.compile(r'^\d{5}$')

# Format A: a section whose first field is a waypoint name and second field is
# a compact 8-char wind/OAT record. Names may start with a digit (e.g. "2950N",
# an abbreviated lat/lon reporting point common on oceanic/high-latitude routes)
# and run up to the full 11-char name field width (e.g. "S21E174", a 7-char
# lat/lon-derived identifier straight from the PSX NG FMC manual's own example).
_A_WAYPT_RE = re.compile(r'^([A-Z0-9]{1,11})\s+(\d{5}[MP]\d{2})')

# Format A FL header: a section that contains only space-separated altitude values
# (e.g. "19000 23000 29000 31000 33000 35000").
_A_FL_HEADER_RE = re.compile(r'^\d{3,5}(\s+\d{3,5})+$')

# Format B/C data rows: {fl} {ddd/sss} {±oo} repeated N times.
_BC_DATA_ROW_RE = re.compile(r'^(?:FL)?\d{2,5}\s+\d{3}/\d{3}\s+[+-]\d')

# Format D lat-only / lon-only sections following a waypoint-name section.
_D_LAT_SECTION_RE = re.compile(r'^[NS]\d{2}[\d.]+(\s+[NS]\d{2}[\d.]+)*$')
_D_LON_SECTION_RE = re.compile(r'^[EW]\d{3}[\d.]+(\s+[EW]\d{3}[\d.]+)*$')

# One "{fl} {ddd/sss} {±oo}" group within a Format B/C/D data row.
_BCD_GROUP_RE = re.compile(r'(?:FL)?(\d{2,5})\s+(\d{3})/(\d{3})\s+([+-]\d{1,2})')


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def detect_format(corridor_txt: str) -> str:
    """Return the PSX wind corridor format identifier (FORMAT_A … FORMAT_E or FORMAT_UNKNOWN)."""
    if not corridor_txt or not corridor_txt.strip():
        return FORMAT_UNKNOWN
    secs = [s.strip() for s in _strip_corridor_prefix(corridor_txt).split('^')]
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


def _dms_component_to_dec(tok: str, is_lat: bool) -> Optional[float]:
    """Parse one Format-D lat or lon token (e.g. 'N4309.0' or 'W06700.0') to decimal degrees."""
    if not tok or tok[0] not in ('N', 'S', 'E', 'W'):
        return None
    digits = 2 if is_lat else 3
    try:
        deg = int(tok[1:1 + digits])
        minutes = float(tok[1 + digits:])
    except ValueError:
        return None
    val = deg + minutes / 60.0
    return -val if tok[0] in ('S', 'W') else val


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
    """Encode wind as a Format-E 5-char string, e.g. '26026' for 260°/26 kt.

    Speed is clamped to what the fixed-width field can hold (0-999kt) so the
    output is always exactly 5 chars, even given an extreme/glitched value —
    PSX rejects the whole corridor if a single record breaks the fixed width.
    """
    dir_tens = int(round(wind_dir / 10)) % 36
    spd = max(0, min(999, int(round(wind_spd))))
    return f"{dir_tens:02d}{spd:03d}"


def encode_temp_e(oat_c: float) -> str:
    """Encode OAT as a Format-E 3-char string, e.g. 'P13' or 'M26' (|OAT| clamped to 99C)."""
    t = max(-99, min(99, int(round(oat_c))))
    return f"M{-t:02d}" if t < 0 else f"P{t:02d}"


def encode_wind_a(wind_dir: float, wind_spd: float, oat_c: float) -> str:
    """Format-A compact wind/OAT encoding: 8 chars, e.g. '28031M17' for 280°/31kt/−17°C.

    Speed (0-999kt) and OAT (|OAT|<=99C) are clamped to what the fixed-width
    fields can hold, so the output is always exactly 8 chars even given an
    extreme/glitched value — PSX rejects the whole corridor if a single
    record breaks the fixed width.
    """
    dir_tens = int(round(wind_dir / 10)) % 36
    spd = max(0, min(999, int(round(wind_spd))))
    t = max(-99, min(99, int(round(oat_c))))
    sign = 'M' if t < 0 else 'P'
    return f"{dir_tens:02d}{spd:03d}{sign}{abs(t):02d}"


def decode_wind_a(rec: str) -> Optional[tuple]:
    """Decode a Format-A 8-char wind/OAT record, e.g. '28031M17' -> (280.0, 31.0, -17.0)."""
    if len(rec) != 8:
        return None
    sign = rec[5]
    if sign not in ('M', 'P'):
        return None
    try:
        dir_deg = (int(rec[0:2]) * 10) % 360
        spd_kt = int(rec[2:5])
        oat_c = int(rec[6:8])
    except ValueError:
        return None
    if sign == 'M':
        oat_c = -oat_c
    return float(dir_deg), float(spd_kt), float(oat_c)


def decode_wind_e(wind_str: str, temp_str: str) -> Optional[tuple]:
    """Decode a Format-E wind (5-char) + OAT (3-char) pair, e.g. '26026','M07' -> (260,26,-7)."""
    if len(wind_str) != 5 or len(temp_str) != 3:
        return None
    sign = temp_str[0]
    if sign not in ('M', 'P'):
        return None
    try:
        dir_deg = (int(wind_str[0:2]) * 10) % 360
        spd_kt = int(wind_str[2:5])
        oat_c = int(temp_str[1:3])
    except ValueError:
        return None
    if sign == 'M':
        oat_c = -oat_c
    return float(dir_deg), float(spd_kt), float(oat_c)


def _decode_wind_only_e(wv: str) -> Optional[tuple]:
    """Decode a Format-E multi-column wind-only 5-digit token, e.g. '28083' -> (280.0, 83.0)."""
    if not _E_WIND_ONLY_RE.match(wv):
        return None
    dir_deg = (int(wv[0:2]) * 10) % 360
    spd_kt = int(wv[2:5])
    return float(dir_deg), float(spd_kt)


def _decode_temp_only_e(temp_str: str) -> Optional[float]:
    """Decode a Format-E OAT field (3-char, sign+2-digit), e.g. 'M46' -> -46.0."""
    if len(temp_str) != 3 or temp_str[0] not in ('M', 'P'):
        return None
    try:
        t = int(temp_str[1:3])
    except ValueError:
        return None
    return float(-t if temp_str[0] == 'M' else t)


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


def _fl_header_a(secs: list) -> Optional[list]:
    """Return the shared Format-A flight-level header (ft values), or None if absent."""
    for sec in secs:
        s = sec.strip()
        if _A_FL_HEADER_RE.match(s):
            return [int(x) for x in s.split()]
    return None


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
    has_prefix = corridor_txt.startswith(_CORRIDOR_PREFIX)
    fmt = detect_format(corridor_txt)
    secs = _strip_corridor_prefix(corridor_txt).split('^')

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
    prefix = _CORRIDOR_PREFIX if has_prefix else ""
    return prefix + "^".join(new_secs), msg


def _extract_e_section(  # pylint: disable=too-many-locals
        stripped: str, current_header_ft: Optional[list]) -> Optional[tuple]:
    """Return (name, {fl_ft: (dir_deg, spd_kt, oat_c)}, lat_deg, lon_deg) for one Format-E section.

    Single-column sections use the waypoint's own FL/OAT directly. Multi-
    column sections (current_header_ft set, matching token count) decode
    each wind-only token against the header's FL and estimate OAT at each
    via the ISA lapse rate anchored to the waypoint's own exact FL/OAT.
    Returns None if the section isn't a (recognisable) waypoint row.
    """
    lm = _E_LATLON_RE.match(stripped)
    if not lm:
        return None
    coord = _dms_to_dec(stripped[:lm.end()])
    # Fields after coord: name, ITT, DIS, FL, TMP, then W/V column(s).
    fields = stripped[lm.end():].strip().split()
    if len(fields) < 6 or fields[0].upper() == _FWIND_NAME:
        return None
    try:
        own_fl_ft = int(fields[3]) * 100
    except ValueError:
        return None
    wv_tokens = fields[5:]
    levels = {}
    if len(wv_tokens) == 1:
        decoded = decode_wind_e(wv_tokens[0], fields[4])
        if decoded:
            levels[own_fl_ft] = decoded
    elif current_header_ft and len(wv_tokens) == len(current_header_ft):
        own_temp_c = _decode_temp_only_e(fields[4])
        if own_temp_c is not None:
            for fl_ft, wv in zip(current_header_ft, wv_tokens):
                wind = _decode_wind_only_e(wv)
                if wind is not None:
                    oat_c = own_temp_c - 1.98 * ((fl_ft - own_fl_ft) / 1000.0)
                    levels[fl_ft] = (wind[0], wind[1], oat_c)
    if not levels:
        return None
    lat, lon = coord if coord else (None, None)
    return fields[0], levels, lat, lon


def _iter_bcd_blocks(secs: list) -> list:
    """Split Format B/C/D sections into columnar blocks: [(names, coords, data_rows), ...].

    Each block is a name-header line (one waypoint name per column), an
    optional lat-line + lon-line pair (Format D only — captured as
    ``coords = (lat_tokens, lon_tokens)``, or None for B/C), and the data
    rows that follow — each holding one "{fl} {ddd/sss} {±oo}" group per
    column, in the same left-to-right order as the names.
    """
    blocks = []
    i, n = 0, len(secs)
    while i < n:
        sec = secs[i].strip()
        if not sec or _BC_DATA_ROW_RE.match(sec):
            i += 1
            continue
        names = sec.split()
        i += 1
        coords = None
        if (i + 1 < n and _D_LAT_SECTION_RE.match(secs[i].strip()) and
                _D_LON_SECTION_RE.match(secs[i + 1].strip())):
            coords = (secs[i].strip().split(), secs[i + 1].strip().split())
            i += 2
        data_rows = []
        while i < n and _BC_DATA_ROW_RE.match(secs[i].strip()):
            data_rows.append(secs[i].strip())
            i += 1
        blocks.append((names, coords, data_rows))
    return blocks


def _extract_bcd(secs: list) -> dict:  # pylint: disable=too-many-locals
    """Extract per-waypoint wind/OAT data (and, for Format D, coordinates) from a B/C/D corridor.

    Each data row holds one "{fl} {ddd/sss} {±oo}" group per waypoint column
    (unlike Format A/E, direction is the full 3-digit heading, not divided
    by 10). Rows whose group count doesn't match the block's waypoint count
    are skipped as malformed. Returns the unified
    ``{name: {"levels": {...}, "lat": .., "lon": ..}}`` shape (lat/lon are
    None for Formats B and C, which carry no coordinates).
    """
    result: dict = {}
    for names, coords, data_rows in _iter_bcd_blocks(secs):
        coord_by_name = {}
        if coords:
            lat_toks, lon_toks = coords
            for name, lat_tok, lon_tok in zip(names, lat_toks, lon_toks):
                lat = _dms_component_to_dec(lat_tok, True)
                lon = _dms_component_to_dec(lon_tok, False)
                if lat is not None and lon is not None:
                    coord_by_name[name] = (lat, lon)
        for row in data_rows:
            groups = _BCD_GROUP_RE.findall(row)
            if len(groups) != len(names):
                continue
            for name, (fl_tok, dir_tok, spd_tok, oat_tok) in zip(names, groups):
                if name.upper() == _FWIND_NAME:
                    continue
                fl_ft = int(fl_tok) if len(fl_tok) >= 4 else int(fl_tok) * 100
                entry = result.setdefault(name, {"levels": {}, "lat": None, "lon": None})
                entry["levels"][fl_ft] = (
                    float(int(dir_tok)), float(int(spd_tok)), float(int(oat_tok)))
                if name in coord_by_name:
                    entry["lat"], entry["lon"] = coord_by_name[name]
    return result


def extract_waypoint_data(corridor_txt: str) -> dict:  # pylint: disable=too-many-locals,too-many-branches
    """Extract per-waypoint wind/OAT data and coordinates from a corridor, for snapshot comparison.

    Returns ``{name: {"levels": {fl_ft: (dir, spd, oat)}, "lat": lat_deg, "lon": lon_deg}}``.
    ``lat``/``lon`` are ``None`` for Formats A, B and C, which carry no
    coordinates at all; Formats D and E carry them inline per waypoint.

    Format A yields a full multi-level grid (its shared FL header applies to
    every waypoint).  Format E yields either a single level per waypoint (its
    own FL field, for the 1-wind-record-per-leg variant) or a multi-level
    grid keyed off the most recent FL header line (for the N-wind-records-
    per-leg variant) — OAT is exact at the waypoint's own FL and estimated
    at the other header levels via the standard ISA lapse rate anchored to
    that own value, since this variant carries only one OAT per waypoint.
    Formats B, C and D yield a full multi-level grid per waypoint, read from
    their columnar "{fl} {ddd/sss} {±oo}" data rows.
    """
    if not corridor_txt or not corridor_txt.strip():
        return {}
    fmt = detect_format(corridor_txt)
    secs = _strip_corridor_prefix(corridor_txt).split('^')
    result: dict = {}

    if fmt == FORMAT_A:
        fl_header = _fl_header_a(secs)
        if not fl_header:
            return {}
        for i, name in _waypoints_a(secs):
            if name.upper() == _FWIND_NAME:
                continue
            fields = secs[i].strip().split()[1:]
            levels = {}
            for fl, rec in zip(fl_header, fields):
                decoded = decode_wind_a(rec)
                if decoded:
                    levels[fl] = decoded
            if levels:
                result[name] = {"levels": levels, "lat": None, "lon": None}

    elif fmt == FORMAT_E:
        current_header_ft = None
        for sec in secs:
            stripped = sec.strip()
            if _E_FL_HEADER_RE.match(stripped):
                current_header_ft = [int(x) * 100 for x in stripped.split()]
                continue
            parsed = _extract_e_section(stripped, current_header_ft)
            if parsed:
                name, levels, lat, lon = parsed
                result[name] = {"levels": levels, "lat": lat, "lon": lon}

    elif fmt in (FORMAT_B, FORMAT_C, FORMAT_D):
        result = _extract_bcd(secs)

    return result


def extract_waypoint_winds(corridor_txt: str) -> dict:
    """Return ``{name: {fl_ft: (dir_deg, spd_kt, oat_c)}}`` — see extract_waypoint_data()."""
    return {name: data["levels"] for name, data in extract_waypoint_data(corridor_txt).items()}


def extract_waypoint_coords(corridor_txt: str) -> dict:
    """Return ``{name: (lat_deg, lon_deg)}`` for waypoints whose corridor entry has coordinates.

    Only Formats D and E carry inline coordinates; waypoints from Formats A,
    B and C (or any Format-D/E waypoint whose coordinate token failed to
    parse) are simply absent from the result.
    """
    return {
        name: (data["lat"], data["lon"])
        for name, data in extract_waypoint_data(corridor_txt).items()
        if data["lat"] is not None and data["lon"] is not None
    }


# PSX's Format-A rows are positional/fixed-width, not whitespace-tokenized:
# the header row is this many blank columns followed by each FL value
# right-justified in a value-width field (no separators); waypoint rows are
# the name left-justified in the name-width field followed by each 8-char
# wind/OAT record right-justified in a value-width field. Reverse-engineered
# from a real PSX-exported corridor after an unpadded, naively-joined header
# ("10000 18000 ...") was misparsed by PSX as a phantom waypoint "0000" with
# an altitude of 0ft. Our own parsing (detect_format, extract_waypoint_winds)
# already tolerates arbitrary whitespace via str.strip()/str.split(), so only
# generation needed this fix.
_FORMAT_A_HEADER_PAD = 10
_FORMAT_A_NAME_WIDTH = 11
_FORMAT_A_VALUE_WIDTH = 9


def build_corridor_a(waypoints: list, fl_list_ft: list, wind_by_index: dict) -> str:
    """Build a fresh Format-A corridor from a route and a per-waypoint wind grid.

    ``waypoints`` is ``[(name, lat, lon), ...]`` in route order (lat/lon are
    unused here but kept for symmetry with the route data callers already have).
    ``wind_by_index`` is ``{waypoint_index: {fl_ft: (dir_deg, spd_kt, oat_c)}}``,
    keyed by position in ``waypoints`` rather than by name, since route
    waypoint names are not guaranteed unique (e.g. a leg revisiting a fix).
    A waypoint/level with no data falls back to calm wind at an ISA-lapse
    temperature estimate for that level.
    """
    header = ' ' * _FORMAT_A_HEADER_PAD + ''.join(
        str(int(fl)).rjust(_FORMAT_A_VALUE_WIDTH) for fl in fl_list_ft)
    sections = [header]
    for i, entry in enumerate(waypoints):
        name = str(entry[0]).strip().upper()[:_FORMAT_A_NAME_WIDTH]
        winds = wind_by_index.get(i, {})
        recs = []
        for fl in fl_list_ft:
            data = winds.get(fl)
            if data is None:
                isa_oat_c = 15.0 - 1.98 * (fl / 1000.0)
                data = (0.0, 0.0, isa_oat_c)
            recs.append(encode_wind_a(*data))
        row = name.ljust(_FORMAT_A_NAME_WIDTH) + ''.join(
            rec.rjust(_FORMAT_A_VALUE_WIDTH) for rec in recs)
        sections.append(row)
    return _CORRIDOR_PREFIX + "^".join(sections)
