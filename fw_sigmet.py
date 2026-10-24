"""SIGMET download parsing and geometry for frankenweather's --own-sigmets mode.

frankenweather downloads SIGMETs itself from aviationweather.gov instead of
letting PSX do it (see frankenweather.py's own_sigmet_coro()). The JSON
form of the API already has each SIGMET's hazard, polygon, base/top and
validity decoded; this module normalizes it into plain dicts, decides which
entries are usable, rebuilds PSX's own WxSigmet text layout from the raw
SIGMET text, and provides the polygon geometry the web UI needs. Stdlib only,
so both frankenweather and the router's web UI (via fw_webui) can import it.
"""

import math
import re
import time

ISIGMET_URL = "https://aviationweather.gov/api/data/isigmet?format=json"
AIRSIGMET_URL = "https://aviationweather.gov/api/data/airsigmet?format=json"

# Hazard types we know how to handle; anything else is counted as "unknown".
HAZARDS = ("TS", "TC", "VA", "TURB", "ICE", "MTW")
# Hazards frankenweather depicts itself as CB weather zones.
CB_HAZARDS = ("TS", "TC")
# Hazards frankenweather generates turbulence for itself.
TURB_HAZARDS = ("TURB", "MTW")
# Hazards frankenweather handles itself, so PSX doesn't get them.
OWN_HAZARDS = CB_HAZARDS + TURB_HAZARDS
UNKNOWN = "unknown"

# SIGMET presets (router /efb and the SIGMETs page): (name, label, hint).
# OFF: nobody uses SIGMETs, neither frankenweather nor PSX. NORMAL (only
# with --sigmet): frankenweather's SIGMET system handles all of
# OWN_HAZARDS, PSX gets the rest (VA, ICE). PSX: frankenweather's SIGMET
# system is off, as without --sigmet: PSX handles every SIGMET itself, and
# frankenweather only asks it to download fresh ones regularly. See
# preset_command()/current_preset().
PRESETS = (
    ("off", "Off", "no SIGMETs at all, neither frankenweather nor PSX uses any"),
    ("normal", "Normal", "frankenweather handles TS, TC, TURB and MTW, PSX gets VA and ICE"),
    ("psx", "PSX", "frankenweather's SIGMET system is off, PSX handles every SIGMET itself"),
)

_NM_PER_DEG = 60.0

# CB coverage (oktas) frankenweather gives a zone inside a TS SIGMET, by the
# SIGMET's qualifier (how dense the thunderstorms are). First guess, to be
# tuned after test flights. US convective SIGMETs have no qualifier.
TS_QUALIFIER_OKTAS = {"ISOL": 2, "OCNL": 4, "EMBD": 5, "FRQ": 6, "SQL": 7, "CONVECTIVE": 5}
TS_DEFAULT_OKTAS = 4
TC_OKTAS = (6, 8)  # tropical cyclone: picked at random within this range
_TC_POS_RE = re.compile(
    r"\b(?:PSN|NR|CENTRE|CENTER)\s+([NS])(\d{2})(\d{2})\s+([EW])(\d{3})(\d{2})")
# PSX's own WxSigmet header/separator lines (see _sigmet_text()).
_HEADER_RULE = "-" * 37
_ENTRY_RULE = "-" * 22


def _clean_polygon(points: list) -> tuple:
    """Return (polygon, reason) for a list of (lat, lon) points.

    polygon is the list without consecutive duplicates or the closing
    point, or None with a reason if the points don't describe an area.
    An open polygon (no repeated first point) is closed implicitly: some
    issuing offices just leave the closing point out, and the area is
    still unambiguous.
    """
    poly = []
    for pt in points:
        if not poly or pt != poly[-1]:
            poly.append(pt)
    if len(poly) > 1 and poly[0] == poly[-1]:
        poly.pop()
    if len(poly) < 3:
        return None, f"only {len(poly)} distinct point(s), not an area"
    return poly, None


def _coords_to_polygons(coords) -> tuple:
    """Return (polygons, reason) from an aviationweather.gov "coords" value.

    coords is a list of {"lat", "lon"} dicts, or (geom "AREAS", e.g. an area
    split at the date line) a list of such lists.
    """
    if not coords:
        return [], "no coordinates"
    parts = coords if isinstance(coords[0], list) else [coords]
    polygons = []
    for part in parts:
        try:
            points = [(float(p["lat"]), float(p["lon"])) for p in part]
        except (KeyError, TypeError, ValueError):
            return [], "malformed coordinates"
        poly, reason = _clean_polygon(points)
        if poly is None:
            return [], reason
        polygons.append(poly)
    return polygons, None


def _entry(source: str, item: dict, fields: dict) -> dict:
    """Build one normalized SIGMET dict (see normalize()) from an API item.

    fields holds what differs between the two APIs: raw_hazard, qualifier,
    top, base, raw, fir and series.
    """
    hazard = _hazard(fields["raw_hazard"])
    polygons, reason = _coords_to_polygons(item.get("coords"))
    if hazard == UNKNOWN:
        reason = f"unknown hazard {fields['raw_hazard']!r}"
    valid_from = item.get("validTimeFrom")
    return {
        "key": f"{source}-{fields['fir']}-{fields['series']}-{valid_from}",
        "source": source,
        "fir": fields["fir"],
        "series": fields["series"],
        "hazard": hazard,
        "raw_hazard": fields["raw_hazard"],
        "qualifier": fields["qualifier"] or "",
        "base_ft": fields["base"],
        "top_ft": fields["top"],
        "valid_from": valid_from,
        "valid_to": item.get("validTimeTo"),
        "polygons": polygons if reason is None else [],
        "raw": (fields["raw"] or "").strip(),
        "ok": reason is None,
        "reason": reason,
    }


def _hazard(raw_hazard: str) -> str:
    """Map an API hazard string onto HAZARDS, or UNKNOWN."""
    raw_hazard = (raw_hazard or "").upper()
    if raw_hazard == "CONVECTIVE":  # US convective SIGMET: thunderstorms
        return "TS"
    return raw_hazard if raw_hazard in HAZARDS else UNKNOWN


def _isigmet_entry(item: dict) -> dict:
    """Normalize one international SIGMET (isigmet endpoint)."""
    return _entry("isigmet", item, {
        "raw_hazard": item.get("hazard") or "",
        "qualifier": item.get("qualifier"),
        "top": item.get("top"),
        "base": item.get("base"),
        "raw": item.get("rawSigmet"),
        "fir": item.get("firId") or "?",
        "series": item.get("seriesId") or "?",
    })


def _airsigmet_entry(item: dict) -> dict:
    """Normalize one US domestic SIGMET (airsigmet endpoint)."""
    raw_hazard = item.get("hazard") or ""
    tops = [t for t in (item.get("altitudeHi1"), item.get("altitudeHi2")) if t]
    bases = [b for b in (item.get("altitudeLow1"), item.get("altitudeLow2")) if b]
    return _entry("airsigmet", item, {
        "raw_hazard": raw_hazard,
        "qualifier": "CONVECTIVE" if raw_hazard.upper() == "CONVECTIVE" else "",
        "top": max(tops) if tops else None,
        "base": min(bases) if bases else None,
        "raw": item.get("rawAirSigmet"),
        "fir": item.get("icaoId") or "?",
        "series": item.get("seriesId") or "?",
    })


def normalize(isigmets: list, airsigmets: list, now: float = None) -> list:
    """Return normalized, unexpired SIGMET dicts from the two API responses.

    isigmets is the international SIGMET list (isigmet endpoint),
    airsigmets the US domestic list (airsigmet endpoint, of which only
    SIGMETs are used, not AIRMETs). Each dict has key, source, fir, series,
    hazard (one of HAZARDS or UNKNOWN), raw_hazard, qualifier, base_ft,
    top_ft (feet or None), valid_from/valid_to (epoch), polygons (list of
    lists of (lat, lon), empty unless ok), raw (the SIGMET text), ok, and
    reason (why it isn't ok, else None). Expired SIGMETs are dropped.
    """
    if now is None:
        now = time.time()
    return [e for e in all_entries(isigmets, airsigmets)
            if not e["valid_to"] or e["valid_to"] >= now]


def all_entries(isigmets: list, airsigmets: list) -> list:
    """Return every SIGMET of a download as normalized dicts, nothing left out.

    Like normalize(), but keeping expired SIGMETs too (unknown and
    unparseable ones are kept by normalize() as well, with ok False). Only
    the airsigmet endpoint's SIGMETs are included, not its AIRMETs.
    """
    entries = [_isigmet_entry(item) for item in isigmets or []]
    entries += [_airsigmet_entry(item) for item in airsigmets or []
                if (item.get("airSigmetType") or "").upper() == "SIGMET"]
    return entries


def sigmet_text(entries: list, fetched_at: float) -> str:
    """Return SIGMET text in PSX's own WxSigmet layout, one line per text line.

    The same layout PSX itself shows: a "#DOWNLOADED" header, then one
    "Hazard: <type>" block per SIGMET with its raw text, separated by
    dashed lines. PSX itself separates lines with "^" instead of newlines,
    so convert before writing it to PSX.
    """
    stamp = time.strftime("%H:%M:%S - %d %b %Y", time.gmtime(fetched_at)).upper()
    lines = [f"#DOWNLOADED {stamp} UTC", _HEADER_RULE]
    for entry in entries:
        lines.append(f"Hazard: {entry['raw_hazard'] or entry['hazard']}")
        lines.extend(entry["raw"].splitlines())
        lines.append(_ENTRY_RULE)
    return "\n".join(lines) + "\n"


def preset_command(preset: str, sigmet_enabled: bool):
    """Return the frankenweather settings command for a SIGMET preset, or None.

    Without frankenweather's SIGMET system (--sigmet) there is no NORMAL.
    Off and PSX leave the types frankenweather handles alone, so NORMAL
    (or a custom choice on the SIGMETs page) can be restored later.
    """
    if preset not in ("off", "normal", "psx") or (preset == "normal" and not sigmet_enabled):
        return None
    cmd = {"disable_psx_sigmets": preset == "off"}
    if sigmet_enabled:
        cmd["sigmet_own"] = preset == "normal"
        if preset == "normal":
            cmd["sigmet_handle"] = {h: True for h in OWN_HAZARDS}
    return cmd


def current_preset(sigmet_active: bool, psx_off: bool, handled) -> str:
    """Return the SIGMET preset the settings match, or None for a custom choice.

    sigmet_active: frankenweather's SIGMET system is on (--sigmet and not
    switched off). handled is the set of types it handles itself.
    """
    if not sigmet_active:
        return "off" if psx_off else "psx"
    if set(handled) == set(OWN_HAZARDS) and not psx_off:
        return "normal"
    return None


def _duration(seconds: float) -> str:
    """Format seconds as e.g. '45 s' or '2:05'."""
    seconds = max(0, round(seconds))
    return f'{seconds} s' if seconds < 60 else f'{seconds // 60}:{seconds % 60:02d}'


def ramp_text(ramp, now=None):
    """Describe where frankenweather's varying SIGMET turbulence intensity is heading.

    ramp is TURBSTATE's "intensity_ramp" ("sigmet_ramp" in 1.11.0; levels 0-1, epoch end times), or
    None when nothing layered drives the turbulence right now (returns None).
    Used by frankenweather's console and the router's /efb page.
    """
    if not ramp:
        return None
    now = time.time() if now is None else now
    target = round(ramp['target'] * 100)
    # The altitude layer (missing from frankenweather 1.11.0's TURBSTATE)
    layer = ''
    if ramp.get('layer_base_ft') is not None:
        layer = (f"FL{max(0, ramp['layer_base_ft']) / 100:03.0f}-"
                 f"{ramp['layer_top_ft'] / 100:03.0f} ")
    # "ramping" is missing from frankenweather 1.11.0's TURBSTATE
    start = round(ramp['from'] * 100)
    if start == target:
        # A ramp that hardly changes the level isn't worth showing as one
        return (f"{layer}steady at {target}% for {_duration(ramp['hold_end'] - now)} more, "
                "then a new level")
    if ramp.get('ramping', True) and now < ramp['ramp_end']:
        way = 'rising' if target > start else 'easing'
        return (f"{layer}{way} {start}% → {target}%"
                f"{' (abrupt)' if ramp.get('abrupt') else ''}, "
                f"{_duration(ramp['ramp_end'] - now)} left, "
                f"then holds {_duration(ramp['hold_s'])}")
    return (f"{layer}holding at {target}% for {_duration(ramp['hold_end'] - now)} more, "
            "then a new level")


def psx_entries(entries: list, handled=OWN_HAZARDS) -> list:
    """Return the entries to give PSX: usable, and of a type frankenweather doesn't handle.

    handled is the set of hazard types frankenweather handles itself (a
    subset of OWN_HAZARDS, chosen on the SIGMETs page).
    """
    return [e for e in entries if e["ok"] and e["hazard"] not in handled]


def psx_text(entries: list, fetched_at: float, handled=OWN_HAZARDS) -> str:
    """Return the WxSigmet value for PSX: psx_entries() in PSX's layout, "^"-separated."""
    return (sigmet_text(psx_entries(entries, handled), fetched_at)
            .rstrip("\n").replace("\n", "^"))


def _unwrap_lons(lon0: float, polygon: list) -> list:
    """Return the polygon's longitudes as one unbroken run, as seen from lon0.

    Each longitude continues from the previous one (no jump at +-180), and
    the run is then shifted by whole turns so its first point is within
    180 degrees of lon0. Without this, a polygon about 180 degrees away
    from lon0 straddles the far side of a projection centred on lon0, and
    its edges seem to run right through lon0.
    """
    lons = []
    for _, lon in polygon:
        lons.append(lon if not lons else lons[-1] + (lon - lons[-1] + 180.0) % 360.0 - 180.0)
    shift = ((lons[0] - lon0 + 180.0) % 360.0 - 180.0) - (lons[0] - lon0)
    return [lon + shift for lon in lons]


def _local_xy(lat0: float, lon0: float, lat: float, lon: float) -> tuple:
    """Return (east_nm, north_nm) of lat/lon from lat0/lon0, flat-earth approximation.

    lon is used as given (see _unwrap_lons()), not folded into +-180 of lon0.
    """
    return ((lon - lon0) * _NM_PER_DEG * math.cos(math.radians(lat0)),
            (lat - lat0) * _NM_PER_DEG)


def _inside_xy(pts: list) -> bool:
    """Ray-casting test for the origin inside a polygon of (x, y) points."""
    inside = False
    j = len(pts) - 1
    for i, (xi, yi) in enumerate(pts):
        xj, yj = pts[j]
        if (yi > 0) != (yj > 0) and 0 < (xj - xi) * (0 - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def polygon_distance(lat: float, lon: float, polygon: list) -> tuple:
    """Return (distance_nm, bearing_deg) from lat/lon to the nearest point of polygon.

    0 nm (bearing None) when lat/lon is inside. Uses a flat-earth
    projection centred on lat/lon -- accurate enough for the few hundred
    nm the web UI cares about, and immune to the date line.
    """
    pts = [_local_xy(lat, lon, p[0], lon_u)
           for p, lon_u in zip(polygon, _unwrap_lons(lon, polygon))]
    if _inside_xy(pts):
        return 0.0, None
    nearest = min((_nearest_on_segment(a, b) for a, b in zip(pts, pts[1:] + pts[:1])),
                  key=lambda p: math.hypot(*p))
    return math.hypot(*nearest), math.degrees(math.atan2(*nearest)) % 360.0


def _nearest_on_segment(a: tuple, b: tuple) -> tuple:
    """Return the (x, y) point on segment a-b nearest the origin."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    seg2 = dx * dx + dy * dy
    t = 0.0 if seg2 == 0 else max(0.0, min(1.0, -(a[0] * dx + a[1] * dy) / seg2))
    return a[0] + t * dx, a[1] + t * dy


def inside_depth(lat: float, lon: float, polygon: list):
    """Return how far inside polygon lat/lon is (nm to the nearest edge), or None if outside."""
    pts = [_local_xy(lat, lon, p[0], lon_u)
           for p, lon_u in zip(polygon, _unwrap_lons(lon, polygon))]
    if not _inside_xy(pts):
        return None
    return min(math.hypot(*_nearest_on_segment(a, b))
               for a, b in zip(pts, pts[1:] + pts[:1]))


def entry_distance(lat: float, lon: float, entry: dict) -> tuple:
    """Return polygon_distance() to the nearest of an entry's polygons."""
    return min((polygon_distance(lat, lon, p) for p in entry["polygons"]),
               key=lambda d: d[0], default=(float("inf"), None))


def tc_center(entry: dict):
    """Return the (lat, lon) a TC SIGMET gives for the cyclone's centre, or None.

    E.g. "TC CHAMPI PSN N2235 E12350" or "TC JOSEPHINE OBS AT 0300Z NR
    N1030 W10154". The first position in the text is the observed one.
    """
    m = _TC_POS_RE.search(entry.get("raw") or "")
    if not m:
        return None
    lat = int(m.group(2)) + int(m.group(3)) / 60.0
    lon = int(m.group(5)) + int(m.group(6)) / 60.0
    return (-lat if m.group(1) == "S" else lat, -lon if m.group(4) == "W" else lon)


def points_in_polygon(lat: float, lon: float, polygon: list,  # pylint: disable=too-many-locals
                      max_nm: float, step_nm: float = 10.0) -> list:
    """Return grid points inside polygon within max_nm of lat/lon.

    Each point is (lat, lon, distance_nm, bearing_deg) from lat/lon, on a
    step_nm grid in the same flat-earth projection polygon_distance() uses.
    Used to pick weather zone positions inside a SIGMET area.
    """
    lons = _unwrap_lons(lon, polygon)
    pts = [_local_xy(lat, lon, p[0], lon_u) for p, lon_u in zip(polygon, lons)]
    min_x = max(min(x for x, _ in pts), -max_nm)
    max_x = min(max(x for x, _ in pts), max_nm)
    min_y = max(min(y for _, y in pts), -max_nm)
    max_y = min(max(y for _, y in pts), max_nm)
    cos_lat = max(math.cos(math.radians(lat)), 0.01)
    result = []
    y = min_y
    while y <= max_y:
        x = min_x
        while x <= max_x:
            dist = math.hypot(x, y)
            if dist <= max_nm and _inside_xy([(px - x, py - y) for px, py in pts]):
                p_lon = (lon + x / (_NM_PER_DEG * cos_lat) + 180.0) % 360.0 - 180.0
                result.append((lat + y / _NM_PER_DEG, p_lon, dist,
                               math.degrees(math.atan2(x, y)) % 360.0))
            x += step_nm
        y += step_nm
    return result
