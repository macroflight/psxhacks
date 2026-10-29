"""Shared FrankenWeather web UI — page builders and route registration.

Used by both the frankenrouter web API and the standalone frankenweather --web-port server.
The page builders accept a context object (ctx) that abstracts state and cache access.

ctx must provide:
  ctx.fw_state            : dict | None   — FrankenWeather STATE payload
  ctx.fw_turbstate        : dict | None   — TURBSTATE payload
  ctx.fw_windstate        : dict | None   — WINDSTATE payload (enroute wind importer)
  ctx.fw_state_received_at      : float   — epoch of last STATE
  ctx.fw_turbstate_received_at  : float   — epoch of last TURBSTATE
  ctx.fw_windstate_received_at  : float   — epoch of last WINDSTATE
  ctx.fw_sigmetstate      : dict | None   — SIGMETSTATE payload (--sigmet)
  ctx.fw_sigmetstate_received_at : float  — epoch of last SIGMETSTATE
  ctx.fw_conflict_paused   : bool         — True while this instance is paused
                                            because a second FRANKENWEATHER
                                            instance is on the network
  ctx.color_scheme        : str           — "dark" or "light"
  ctx.cache_get(name)     -> str | None   — PSX variable lookup by name
  ctx.send_manualwx_cmd(cmd: dict)      -> coroutine
  ctx.send_turb_cmd(cmd: dict)          -> coroutine
  ctx.send_mode_cmd(mode: str)          -> coroutine
  ctx.send_fw_settings_cmd(cmd: dict)   -> coroutine

Optional, standalone-only (used by register_weather_logs_routes/the Config and logs
page -- absent on the router's context, since that page has no reason to exist
on the router UI):
  ctx.event_log_dir       : str           — directory holding per-run event logs
  ctx.event_log_path      : str | None    — this run's own event log file

Optional, router-only:
  ctx.router_home         : str           — URL of the router's start page
"""
# pylint: disable=invalid-name,too-many-lines
import asyncio
import base64
import datetime
import gzip
from html import escape as _esc
import json
import lzma
import math
import os
import time

from aiohttp import web  # pylint: disable=import-error

import fw_sigmet


def encode_windstate_payload(state: dict) -> str:
    """Gzip-compress and base64-encode a WINDSTATE dict for the wire.

    A long-haul route's per-waypoint/per-level wind comparison can reach
    ~200 KB as plain JSON (dominated by repeated key names like
    "flightplan"/"openmeteo"/"diff"/"dir_deg" etc, once per waypoint/level
    combination) -- large enough that some downstream addons choke on the
    single addon line. That key repetition compresses very well, typically
    down to 15-20% of the original size.
    """
    raw = json.dumps(state, separators=(',', ':')).encode('utf-8')
    return base64.b64encode(gzip.compress(raw, 9)).decode('ascii')


def decode_windstate_payload(encoded: str) -> dict:
    """Reverse encode_windstate_payload()."""
    return json.loads(gzip.decompress(base64.b64decode(encoded)))


def encode_lzma_json_payload(data) -> str:
    """Lzma-compress and base64-encode compact JSON for the wire (SIGMETJSON).

    A day's parsed SIGMET download is ~125 KB of JSON, ~29 KB encoded
    (gzip would be ~38 KB). Takes ~35 ms, so run it in an executor. The
    dictionary is only as large as the input: preset 9's 64 MiB gains
    nothing on 125 KB, but made each call allocate ~700 MB (committed at
    once on Windows), and each receiver reserve 64 MiB to decompress.
    """
    raw = json.dumps(data, separators=(',', ':')).encode('utf-8')
    filters = [{"id": lzma.FILTER_LZMA2, "preset": 9 | lzma.PRESET_EXTREME,
                "dict_size": max(1 << 16, len(raw))}]
    return base64.b64encode(lzma.compress(raw, filters=filters)).decode('ascii')


# CB avoidance near the departure/arrival airport (frankenweather's
# cb_airport_mode): (mode, label, hint), as on the router's /efb too.
CB_AIRPORT_MODES = (
    ("off", "Off", "CBs stay where the weather puts them"),
    ("move", "Move zone",
     "moves the airport's weather zone 23 nm away, so its CB stays at least 15 nm from "
     "the airport; the airport keeps its real METAR"),
    ("remove", "Remove CB",
     "removes every CB that could come within 15 nm of the airport, and the TS/CB in "
     "the METARs PSX gets for those zones; the zones stay put"),
)


def cb_airport_mode(cfg: dict):
    """Return frankenweather's cb_airport_mode from its STATE config dict.

    Before 1.14.0 frankenweather only had avoid_cb_near_airport (on meant
    moving the zone). Returns (mode, has_modes): has_modes False for such
    an older frankenweather, which can't remove CBs.
    """
    if 'cb_airport_mode' in cfg:
        return cfg['cb_airport_mode'], True
    return ('move' if cfg.get('avoid_cb_near_airport') else 'off'), False


def cb_airport_mode_command(mode: str):
    """Return the frankenweather settings command for a cb_airport_mode, or None.

    Also carries the on/off avoid_cb_near_airport that frankenweather
    before 1.14.0 understands (where on means moving the zone).
    """
    if mode not in [m for m, _label, _hint in CB_AIRPORT_MODES]:
        return None
    return {"cb_airport_mode": mode, "avoid_cb_near_airport": mode != "off"}


def decode_lzma_json_payload(encoded: str):
    """Reverse encode_lzma_json_payload(); raises ValueError if malformed."""
    try:
        raw = lzma.decompress(base64.b64decode(encoded))
    except lzma.LZMAError as exc:
        raise ValueError(f"bad lzma data: {exc}") from exc
    return json.loads(raw)


_COMMON_CSS = '''\
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
* {{ box-sizing: border-box; }}
body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    max-width: 34em; margin: 0 auto; padding: 1em;
    background: #0f1117; color: #e2e8f0; min-height: 100vh;
}}
h1 {{ margin-top: 0; font-size: 1.3em; font-weight: 600; }}
h2 {{ font-size: 1em; color: #94a3b8; margin: 1em 0 0.4em; font-weight: 500; }}
.card {{
    border-radius: 0.5em; padding: 0.9em 1em; margin-bottom: 1em;
    background: #1c2033; border: 1px solid #2a2f45;
}}
.ok   {{ border-left: 4px solid #22c55e; }}
.warn {{ border-left: 4px solid #ef4444; }}
table {{ width: 100%; border-collapse: collapse; }}
td {{ padding: 0.25em 0; vertical-align: top; }}
td:first-child {{ width: 45%; color: #94a3b8; font-size: 0.88em; padding-right: 0.5em; }}
td.val {{ font-weight: 500; color: #f1f5f9; }}
td.ok  {{ color: #4ade80; font-weight: 600; }}
td.warn {{ color: #f87171; font-weight: 600; }}
a.btn {{ display: block; text-decoration: none; }}
a.btn, button.btn, input[type=submit] {{
    width: 100%; padding: 0.85em 1em; margin: 0.45em 0;
    font-size: 1.05em; border-radius: 0.5em; border: none; cursor: pointer;
    text-align: center; font-family: inherit; font-weight: 600;
}}
input[type=text] {{
    width: 100%; padding: 0.65em; margin: 0.25em 0 0.75em;
    font-size: 1em; border-radius: 0.45em;
    border: 1px solid #374151; background: #111827; color: #f9fafb;
    font-family: inherit;
}}
label {{ display: block; color: #94a3b8; font-size: 0.9em; margin-top: 0.5em; }}
textarea {{
    width: 100%; padding: 0.65em; margin: 0.25em 0 0.75em;
    font-size: 1em; border-radius: 0.45em;
    border: 1px solid #374151; background: #111827; color: #f9fafb;
    font-family: inherit; resize: vertical; min-height: 4em;
}}
.grid2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 0 1em; }}
.btn-row {{ display: flex; gap: 0.5em; margin: 0.45em 0; }}
.btn-row a.btn, .btn-row button.btn {{ flex: 1; width: auto; margin: 0; }}
.grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 0 1em; }}
.grid4 {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 0 1em; }}
.check {{ display: flex; align-items: center; gap: 0.5em;
    margin: 0.5em 0 0.75em; color: #e2e8f0; cursor: pointer; }}
.btn-sm {{ font-size: 0.78em; padding: 0.45em 0.5em; width: 7em;
    margin-top: 0.4em; text-align: center; }}
form {{ margin: 0; }}
hr {{ margin: 1em 0; border: none; border-top: 1px solid #2a2f45; }}
.btn-amber {{ background: #d97706; color: #fff; }}
.btn-blue  {{ background: #1d4ed8; color: #fff; }}
.btn-gray  {{ background: #374151; color: #e5e7eb; }}
.btn-green {{ background: #16a34a; color: #fff; }}
.btn-red   {{ background: #dc2626; color: #fff; }}
.note {{ font-size: 0.88em; color: #64748b; margin: 0.5em 0; }}
.page-title {{ display: flex; align-items: center; gap: 0.6em; margin-bottom: 1em; }}
.page-title h1 {{ margin: 0; }}
.page-title img {{ width: 48px; height: 48px; border-radius: 50%; flex-shrink: 0; }}
.status-area {{ display: flex; gap: 1em; align-items: center; margin-bottom: 1em; }}
.status-area .card {{ flex: 1; margin-bottom: 0; }}
.status-logo {{ flex-shrink: 0; }}
.status-logo img {{ display: block; width: 7em; height: 7em; object-fit: contain; }}
.toggle-row {{ display: flex; align-items: center; gap: 0.75em; margin: 0.5em 0; cursor: pointer; color: #e2e8f0; }}
.toggle-switch {{ position: relative; display: inline-block; width: 42px; height: 24px; flex-shrink: 0; }}
.toggle-switch input {{ opacity: 0; width: 0; height: 0; position: absolute; }}
.toggle-track {{ position: absolute; inset: 0; background: #374151; border-radius: 12px; transition: background 0.2s; }}
.toggle-track::after {{ content: ""; position: absolute; width: 18px; height: 18px; left: 3px; top: 3px; background: #9ca3af; border-radius: 50%; transition: transform 0.2s, background 0.2s; }}
.toggle-switch input:checked + .toggle-track {{ background: #16a34a; }}
.toggle-switch input:checked + .toggle-track::after {{ transform: translateX(18px); background: #fff; }}
.toggle-switch input:disabled + .toggle-track {{ opacity: 0.5; cursor: not-allowed; }}
</style>'''

_LEAFLET_HEAD = (
    '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>\n'
    '<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>\n'
    '<style>\n'
    '#map { height: 580px; border-radius: 8px; border: 1px solid #2a2f45; }\n'
    '.zone-label { background: rgba(28,32,51,0.92) !important;\n'
    '  border: 1px solid #2a2f45 !important; color: #f1f5f9 !important;\n'
    '  border-radius: 4px !important; font-size: 11px;\n'
    '  padding: 2px 6px !important; box-shadow: none !important;\n'
    '  pointer-events: auto !important; cursor: pointer; }\n'
    '.zone-label::before { border-right-color: #2a2f45 !important; }\n'
    '.dark-popup .leaflet-popup-content-wrapper { background: #1c2033;\n'
    '  border: 1px solid #2a2f45; color: #f1f5f9; border-radius: 6px; }\n'
    '.dark-popup .leaflet-popup-tip { background: #1c2033; }\n'
    '.dark-popup .leaflet-popup-close-button { color: #94a3b8; }\n'
    '.zone-label-focused { border-left: 3px solid #3b82f6 !important; }\n'
    '</style>\n'
)

_TURB_TYPES_ORDER = ('wave', 'rotor', 'mechanical', 'shear', 'cb', 'pirep', 'cape', 'gairmet',
                     'sigmet')
_TURB_KIND_LABELS = {
    'wave': 'Mountain wave', 'rotor': 'Lee rotor', 'mechanical': 'Mechanical',
    'shear': 'Wind shear CAT', 'cb': 'CB proximity', 'pirep': 'PIREP',
    'cape': 'CAPE convective', 'gairmet': 'G-AIRMET', 'sigmet': 'SIGMET',
    'mtw': 'Mountain wave SIGMET',
}
# Testing turbulence profile: likelihood multipliers per event kind
# (frankenturb/turbulence_events.py), and the presets offered
_TURB_EVENT_KINDS = ('moderate', 'severe', 'extreme')
_TURB_EVENT_MAX = {'moderate': 10, 'severe': 100, 'extreme': 1000}
_TURB_EVENT_PRESETS = {
    'realistic': (1.0, 1.0, 1.0),
    'eventful': (3.0, 20.0, 50.0),
    'training': (5.0, 100.0, 500.0),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _turb_kind_label(kind: str) -> str:
    """Return the web UI label of a turbulence kind ("X+shear": wind shear outweighing X)."""
    if kind.endswith('+shear'):
        return _TURB_KIND_LABELS['shear']
    return _TURB_KIND_LABELS.get(kind, kind)


def _sigmets_enabled(ctx) -> bool:
    """Return True if frankenweather runs with --sigmet (so the SIGMETs page exists)."""
    sigstate = getattr(ctx, 'fw_sigmetstate', None) or {}
    return bool(sigstate.get('enabled') or
                ((ctx.fw_state or {}).get('config') or {}).get('sigmet_enabled'))


def _config_page_title(ctx) -> str:
    """Return the Config page's name: logs are only on frankenweather's own web UI."""
    return 'Config and logs' if getattr(ctx, 'event_log_dir', None) else 'Config'


def _weather_nav(ctx, current: str) -> str:
    """Return the nav buttons every weather page has at the top, linking to all of them.

    current is the page being shown ('map', 'zones', 'wind', 'sigmets',
    'turb', 'config'); its button is highlighted. A Refresh button at the
    end reloads the page. In the router's web UI (ctx.router_home), a
    button back to the router's start page comes first.
    The SIGMETs page only exists with frankenweather's --sigmet.
    """
    pages = [('map', '/weather', 'Map'),
             ('zones', '/weather/settings', 'Weather zones'),
             ('wind', '/weather/enroute-wind', 'Enroute wind')]
    if _sigmets_enabled(ctx):
        pages.append(('sigmets', '/weather/sigmets', 'SIGMETs'))
    pages += [('turb', '/weather/turbulence', 'Extra turbulence'),
              ('config', '/weather/config', _config_page_title(ctx))]
    here = next((url for key, url, _ in pages if key == current), '')
    # In the router's web UI: a way back to the router's own start page
    home = getattr(ctx, 'router_home', None)
    home_html = (f'<a href="{home}" class="btn btn-gray btn-sm" '
                 'style="width:auto;white-space:nowrap">⌂ Router home</a>' if home else '')
    return home_html + ''.join(
        f'<a href="{url}" class="btn {"btn-blue" if key == current else "btn-gray"} btn-sm" '
        f'style="width:auto;white-space:nowrap">{label}</a>'
        for key, url, label in pages) + (
        # Reloads in place, so e.g. the map keeps its view (kept in the URL's #)
        f'<a href="{here}" onclick="location.reload();return false" '
        'class="btn btn-gray btn-sm" style="width:auto;white-space:nowrap">↻ Refresh</a>')


def _page_shell(color_scheme, title, nav_html, body, extra_head=''):
    """Wrap body in the common FrankenWeather page chrome (head/title-bar/footer).

    Factors out exactly the markup that used to be copy-pasted byte-for-byte
    across every page builder's own local _page() closure: the <head> open
    (color-scheme meta + _COMMON_CSS), and the page-title bar (home icon +
    <h1>title</h1> + a right-aligned nav button row). extra_head is inserted
    right before </head> for page-specific <style>/<script>/<link> content
    (e.g. Leaflet, a refresh timer, or a page's own table CSS); nav_html is
    the title bar's button row, which intentionally still differs per page
    (which links appear, and which one becomes a same-page "Refresh" link).
    """
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        f'<meta name="color-scheme" content="{color_scheme}" />\n' +
        _COMMON_CSS.format() +
        extra_head +
        '</head>\n<body>\n'
        '<div class="page-title">'
        '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
        f'<h1>{title}</h1>'
        f'<div style="margin-left:auto;display:flex;gap:0.5em;flex-wrap:wrap;'
        f'justify-content:flex-end">{nav_html}</div>'
        '</div>\n' +
        body +
        '</body>\n</html>\n'
    )


def _parse_wx_brief(wx_str):  # pylint: disable=too-many-locals
    """Return a dict of human-readable weather summary fields from a PSX Wx string."""
    if not wx_str:
        return {}
    parts = wx_str.split(';')
    if len(parts) < 24:
        return {}
    try:
        wind_enc = parts[18]
        wind_dir = int(wind_enc[3:6]) if len(wind_enc) >= 8 else 0
        wind_spd = int(wind_enc[6:8]) if len(wind_enc) >= 8 else 0
        wind_gust = int(parts[19])
        gust_str = f' G{wind_gust}' if wind_gust > wind_spd + 5 else ''
        lo_oktas = int(parts[3])
        lo_base = int(parts[5])
        hi_oktas = int(parts[0])
        hi_base = int(parts[2])
        cb_oktas = int(parts[9])
        cb_base = int(parts[11])
        cb_top = int(parts[10])
        vis_m = int(parts[20])
        temp_c = int(parts[22])
        qnh_raw = int(parts[23])
        qnh_hpa = round(qnh_raw / 2.953)
    except (ValueError, IndexError):
        return {}
    return {
        'wind': f'{wind_dir:03d}°/{wind_spd}kt{gust_str}',
        'lo_cloud': f'{lo_oktas} oktas base {lo_base} ft' if lo_oktas else 'None',
        'hi_cloud': f'{hi_oktas} oktas base {hi_base} ft' if hi_oktas else 'None',
        'cb': f'{cb_oktas} oktas {cb_base}–{cb_top} ft' if cb_oktas else 'None',
        'cb_raw': {'oktas': cb_oktas, 'base': cb_base, 'top': cb_top} if cb_oktas else None,
        'vis': f'{vis_m} m' if vis_m < 9999 else '≥10 km',
        'temp': f'{temp_c}°C',
        'qnh': f'{qnh_hpa} hPa',
    }


def _wx_string_to_manual_params(wx_str):  # pylint: disable=too-many-locals
    """Parse a PSX 24-field Wx string into a manual_wx_params dict."""
    parts = wx_str.strip().split(';')
    if len(parts) < 24:
        return None
    try:
        wind_enc = parts[18]
        wind_var = int(wind_enc[0:3]) if len(wind_enc) >= 8 else 0
        wind_dir = int(wind_enc[3:6]) if len(wind_enc) >= 8 else 0
        wind_spd = int(wind_enc[6:8]) if len(wind_enc) >= 8 else 0
        qnh_psx = int(parts[23])
        qnh_hpa = round(qnh_psx / 2.953, 1)
        inv_tmp_raw = int(parts[17])
        inv_tmp_c = round(inv_tmp_raw / 10.0)
        return {
            "hi_oktas": int(parts[0]), "hi_top": int(parts[1]), "hi_base": int(parts[2]),
            "lo_oktas": int(parts[3]), "lo_top": int(parts[4]), "lo_base": int(parts[5]),
            "turb_severity": int(parts[6]),
            "turb_top": int(parts[7]), "turb_base": int(parts[8]),
            "cb_oktas": int(parts[9]), "cb_top": int(parts[10]), "cb_base": int(parts[11]),
            "mb_mode": int(parts[12]), "mb_chance": int(parts[13]),
            "mb_outflow": int(parts[14]),
            "inv_on": int(parts[15]) != 0, "inv_top": int(parts[16]),
            "inv_tmp": inv_tmp_c,
            "wind_dir": wind_dir, "wind_spd": wind_spd,
            "wind_gust": int(parts[19]), "wind_var": wind_var,
            "vis_m": int(parts[20]), "precip": int(parts[21]),
            "surf_temp": int(parts[22]), "qnh_hpa": qnh_hpa,
        }
    except (ValueError, IndexError):
        return None


def _manual_metar(p, icao="ZZZZ", now=None):  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    """Generate a METAR-like string from manual wx params dict."""
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    wind_dir = int(p.get("wind_dir", 0))
    wind_spd = int(p.get("wind_spd", 0))
    wind_gust = int(p.get("wind_gust", 0))
    wind_var = int(p.get("wind_var", 0))
    vis_m = int(p.get("vis_m", 9999))
    lo_oktas = int(p.get("lo_oktas", 0))
    lo_base = int(p.get("lo_base", 44900))
    hi_oktas = int(p.get("hi_oktas", 0))
    hi_base = int(p.get("hi_base", 44950))
    cb_oktas = int(p.get("cb_oktas", 0))
    cb_base = int(p.get("cb_base", 3000))
    temp = int(p.get("surf_temp", 15))
    qnh_hpa = int(round(float(p.get("qnh_hpa", 1013.25))))
    precip = int(p.get("precip", 0))

    if wind_var > 0 and wind_spd < 6:
        wind_token = "VRB"
    elif wind_var > 0:
        left = (wind_dir - wind_var // 2) % 360
        right = (wind_dir + wind_var // 2) % 360
        wind_token = f"{wind_dir:03d}{wind_spd:02d}"
        if wind_gust > wind_spd + 5:
            wind_token += f"G{wind_gust:02d}"
        wind_token += f"KT {left:03d}V{right:03d}"
    else:
        wind_token = f"{wind_dir:03d}{wind_spd:02d}"
        if wind_gust > wind_spd + 5:
            wind_token += f"G{wind_gust:02d}"
        wind_token += "KT"

    vis_token = "9999" if vis_m >= 9999 else f"{min(vis_m, 9000):04d}"
    precip_tokens = {1: "RA", 2: "-RASN", 3: "+RA"}.get(precip, "")

    _OKTAS_COVER = {0: None, 1: "FEW", 2: "FEW", 3: "SCT", 4: "SCT",
                    5: "BKN", 6: "BKN", 7: "OVC", 8: "OVC"}

    sky_tokens = []
    lo_cover = _OKTAS_COVER.get(lo_oktas)
    if lo_cover and lo_base < 44900:
        sky_tokens.append(f"{lo_cover}{lo_base // 100:03d}")
    hi_cover = _OKTAS_COVER.get(hi_oktas)
    if hi_cover and hi_base < 44950:
        sky_tokens.append(f"{hi_cover}{hi_base // 100:03d}")
    cb_cover = _OKTAS_COVER.get(cb_oktas)
    if cb_cover and cb_oktas > 0:
        sky_tokens.append(f"{cb_cover}{cb_base // 100:03d}CB")
    if not sky_tokens:
        sky_tokens = ["SKC"]

    temp_str = (f"M{abs(temp):02d}" if temp < 0 else f"{temp:02d}")
    dp_str = "00"

    tokens = [icao, now.strftime('%d%H%MZ'), wind_token, vis_token]
    if precip_tokens:
        tokens.append(precip_tokens)
    tokens += sky_tokens
    tokens += [f"{temp_str}/{dp_str}", f"Q{qnh_hpa:04d}"]
    return " ".join(tokens)


# ---------------------------------------------------------------------------
# Page builders
# ---------------------------------------------------------------------------


_DATA_STALE_TIMEOUT_S = 300.0  # matches the frankenweather-stale and MSFS-bridge-timeout thresholds

# Map side panel: PSX/MSFS QNH difference below which the display is shown in green
# rather than orange. Independent of frankenweather's own msfs_qnh_check sync/warn threshold.
#
# Chosen as roughly the point where a ground-level QNH mismatch becomes RVSM-significant.
# Above the transition altitude the altimeter reads pressure altitude off the 1013.25 hPa
# standard setting, so a ground QNH difference only matters at cruise if it represents a
# uniform pressure offset through the whole atmospheric column (a simplification, since real
# weather isn't perfectly barotropic, but reasonable for two independently-modeled sim
# atmospheres drifting apart). Under that assumption the altitude error from a given pressure
# offset scales with dh/dP = R*T/(g*P) (hydrostatic equation) — thinner air aloft means the same
# hPa offset produces a larger altitude error the higher you go: ~70 ft/hPa at FL290 up to
# ~116 ft/hPa at FL410, versus ~27 ft/hPa at sea level. Against RVSM's ~200 ft altimetry-system-
# error tolerance, that puts the RVSM-significant threshold at ~1.7-2.8 hPa across the RVSM band
# (FL290-FL410), centering close to 2 hPa.
_QNH_WARN_THRESHOLD_HPA = 2.0


def _fmt_data_status(now: float, epoch, timeout_s: float = _DATA_STALE_TIMEOUT_S) -> tuple:
    """Return (color, status_label, age_label) describing a 'last received' epoch timestamp."""
    if not epoch:
        return '#ef4444', 'not getting data', 'never'
    age_label = _fmt_relative_epoch(now, epoch)
    if now - epoch > timeout_s:
        return '#ef4444', 'not getting data', age_label
    return '#22c55e', 'OK', age_label


def _zone_cb_position(wxmode: str, time_earth_ms: int):
    """Return (lat, lon) of a local zone's CB, or None if wxmode doesn't parse.

    PSX puts a local zone's CB 7 nm from the zone centre, at a bearing that
    turns once an hour with sim time (Aerowinx/Hardy):
    (minutes * 6 + seconds * 0.1 + zone elevation ft + 2000) % 360. Same
    formula as frankenturb/cb.py's _cb_bearing(), repeated here because the
    router's web UI doesn't include frankenturb.
    """
    try:
        parts = wxmode.split(';')
        lat, lon, elev_ft = float(parts[0]), float(parts[1]), float(parts[3])
    except (ValueError, IndexError, AttributeError):
        return None
    if abs(lat) <= math.pi / 2 and abs(lon) <= math.pi:  # radians, as PSX sends them
        lat, lon = math.degrees(lat), math.degrees(lon)
    t = datetime.datetime.fromtimestamp(time_earth_ms / 1000.0, tz=datetime.timezone.utc)
    bearing = math.radians((t.minute * 6.0 + (t.second + t.microsecond / 1e6) * 0.1 +
                            elev_ft + 2000.0) % 360.0)
    ang = 7.0 * 1852.0 / 6371000.0
    lat_r, lon_r = math.radians(lat), math.radians(lon)
    cb_lat = math.asin(math.sin(lat_r) * math.cos(ang) +
                       math.cos(lat_r) * math.sin(ang) * math.cos(bearing))
    cb_lon = lon_r + math.atan2(math.sin(bearing) * math.sin(ang) * math.cos(lat_r),
                                math.cos(ang) - math.sin(lat_r) * math.sin(cb_lat))
    return round(math.degrees(cb_lat), 5), round((math.degrees(cb_lon) + 540.0) % 360.0 - 180.0, 5)


def _build_weather_map_page(ctx):  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    """Render the /weather page with a Leaflet tile map centred on the aircraft."""
    import re as _re  # pylint: disable=import-outside-toplevel
    color_scheme = ctx.color_scheme
    now = time.time()
    state = ctx.fw_state
    received_at = ctx.fw_state_received_at
    age_s = now - received_at if received_at else float('inf')
    stale = state is None or age_s > 300.0

    def _page(body):
        extra_head = (
            '\n<style>'
            'body { max-width: none; }'
            # Clicking a polygon (e.g. a SIGMET area) gives it keyboard
            # focus, and browsers then draw a focus ring around its bounding box
            '.leaflet-interactive:focus { outline: none; }'
            '#map { width: min(calc(100vh - 7rem), 72vw);'
            ' height: min(calc(100vh - 7rem), 72vw);'
            ' border-radius: 8px; border: 1px solid #2a2f45; flex-shrink: 0; }'
            '.wx-map-wrap { display: flex; gap: 1.5em; align-items: flex-start; }'
            '.wx-map-side { flex: 1; min-width: 180px; display: flex;'
            ' flex-direction: column; gap: 1em; }'
            '</style>\n' +
            _LEAFLET_HEAD
        )
        return _page_shell(color_scheme, 'Weather', _weather_nav(ctx, 'map'), body, extra_head)

    if stale:
        msg = 'No data received from frankenweather, check PSX Instructor station'
        if state is not None:
            age_min = int(age_s // 60)
            msg = (f'No recent frankenweather data (last received {age_min} min ago), '
                   'check PSX Instructor station')
        return _page(f'<div class="card warn"><p style="margin:0">{msg}</p></div>\n'
                     '<a href="/" class="btn btn-gray">Back</a>\n')

    ac_lat = state.get('ac_lat') or 0.0
    ac_lon = state.get('ac_lon') or 0.0
    ac_hdg = state.get('ac_hdg') or 0.0
    zones = state.get('zones', [])
    fw_mode = state.get('fw_mode', 'enabled')
    nav_mode = state.get('mode', '?')

    focused_zone = None
    try:
        fz_val = ctx.cache_get('FocussedWxZone')
        if fz_val is not None:
            focused_zone = int(fz_val)
    except (ValueError, TypeError):
        pass

    try:
        time_earth_ms = int(ctx.cache_get('TimeEarth') or 0) or int(now * 1000)
    except ValueError:
        time_earth_ms = int(now * 1000)
    zone_data = []
    for zone in zones:
        zone_num = zone.get('zone')
        source = zone.get('source', 'OM')
        icao = zone.get('icao', '?')
        wx_str = ctx.cache_get(f'Wx{zone_num}') or ''
        is_fake_icao = len(icao) == 4 and icao[0] == 'X' and icao[1:].isdigit()
        metar = None
        if not is_fake_icao:
            metar = ctx.cache_get(f'Metar{zone_num}') or None
        wx = _parse_wx_brief(wx_str) or {}
        if not wx.get('cb_raw'):
            cb_m = _re.search(r'CB (\d+)ok (\d+)-(\d+)ft', zone.get('reason', ''))
            if cb_m:
                wx['cb_raw'] = {
                    'oktas': int(cb_m.group(1)),
                    'base': int(cb_m.group(2)),
                    'top': int(cb_m.group(3)),
                }
        zone_data.append({
            'zone': zone_num,
            'icao': icao,
            'lat': zone.get('lat') or 0.0,
            'lon': zone.get('lon') or 0.0,
            'source': source,
            'source_label': ('VATSIM' if source == 'VATSIM'
                             else 'PSX auto' if source == 'PSX'
                             else 'OpenMeteo'),
            'reason': zone.get('reason', ''),
            'placement': zone.get('placement', ''),
            'weather_detail': zone.get('weather_detail', ''),
            'is_focused': focused_zone is not None and zone_num == focused_zone,
            'wx': wx,
            'metar': metar,
            'wx_raw': wx_str,
            'cb_target': zone.get('cb_target'),
            'cb_rate': zone.get('cb_rate'),
            'cb_sigmet': zone.get('cb_sigmet'),
            'anchor': bool(zone.get('anchor')),
            'cb_turb': zone.get('cb_turb'),
            # Where PSX actually draws this zone's CB, if it has one
            'cb_pos': (_zone_cb_position(ctx.cache_get(f'WxMode{zone_num}') or '', time_earth_ms)
                       if (wx.get('cb_raw') or {}).get('oktas') else None),
        })

    zones_js = json.dumps(zone_data)
    sigmets_js = json.dumps(state.get('sigmets', []))
    gairmets_js = json.dumps(state.get('gairmets', []))
    ac_alt_ft = state.get('ac_alt_ft') or 0
    turbstate = ctx.fw_turbstate or {}
    turb_src_lat = turbstate.get('source_lat')
    turb_src_lon = turbstate.get('source_lon')
    turb_kind = turbstate.get('active_kind', 'none')
    turb_intensity = turbstate.get('effective_intensity', turbstate.get('active_intensity', 0.0))
    turb_pct = int(turb_intensity * 100)
    turb_label = _turb_kind_label(turb_kind) if turbstate else '—'

    turb_intensity_label = (
        'none' if turb_intensity < 0.10 else
        'light' if turb_intensity < 0.25 else
        'moderate' if turb_intensity < 0.50 else
        'severe' if turb_intensity < 0.75 else
        'extreme'
    )
    turb_sources = turbstate.get('sources', [])
    turb_summary_js = json.dumps({
        'kind': turb_kind,
        'pct': turb_pct,
        'label': turb_label,
        'intensity_label': turb_intensity_label,
        'reason': turbstate.get('active_reason', ''),
        'sources': [
            {'kind': _turb_kind_label(s['kind']),
             'pct': int(s['intensity'] * 100),
             'reason': s['reason'],
             'active': s['kind'] == turb_kind}
            for s in turb_sources
        ],
    })
    _WIND_TURB_KINDS = {'wave', 'rotor', 'mechanical', 'mtw'}
    turb_js = 'null'
    if (turb_src_lat is not None and turb_src_lon is not None and
            turb_kind in _WIND_TURB_KINDS and turb_intensity >= 0.01):
        turb_js = json.dumps({
            'src_lat': turb_src_lat,
            'src_lon': turb_src_lon,
            'kind': turb_kind,
            'reason': turbstate.get('active_reason', ''),
        })
    script = (
        '<script>\n'
        'function destPoint(lat,lon,brng,dist){'
        'var R=6371,d=dist/R,b=brng*Math.PI/180,la=lat*Math.PI/180,lo=lon*Math.PI/180;'
        'var la2=Math.asin(Math.sin(la)*Math.cos(d)+Math.cos(la)*Math.sin(d)*Math.cos(b));'
        'var lo2=lo+Math.atan2(Math.sin(b)*Math.sin(d)*Math.cos(la),'
        'Math.cos(d)-Math.sin(la)*Math.sin(la2));'
        'return[la2*180/Math.PI,lo2*180/Math.PI];}\n'
        'function gcdNm(la1,lo1,la2,lo2){'
        'var R=3440.065,r=Math.PI/180;'
        'var dla=(la2-la1)*r,dlo=(lo2-lo1)*r;'
        'var a=Math.sin(dla/2)*Math.sin(dla/2)+'
        'Math.cos(la1*r)*Math.cos(la2*r)*Math.sin(dlo/2)*Math.sin(dlo/2);'
        'return R*2*Math.atan2(Math.sqrt(a),Math.sqrt(1-a));}\n'
        'function pointInPoly(lat,lon,poly){'
        'var inside=false,n=poly.length;'
        'for(var i=0,j=n-1;i<n;j=i++){'
        'var xi=poly[i][0],yi=poly[i][1],xj=poly[j][0],yj=poly[j][1];'
        'if(((yi>lon)!==(yj>lon))&&(lat<(xj-xi)*(lon-yi)/(yj-yi)+xi))inside=!inside;}'
        'return inside;}\n'
        # The view (lat,lon,zoom) survives the periodic reload, along with
        # when the user last panned/zoomed; 2 minutes after that it's
        # dropped, so the map refits to the aircraft and zones again
        # instead of leaving them to drift off screen.
        'var _VIEW_KEEP_MS=120000;'
        'function parseSavedView(){'
        'var h=location.hash.slice(1).split(",");'
        'if(h.length===4){var la=parseFloat(h[0]),lo=parseFloat(h[1]),z=parseFloat(h[2]),'
        't=parseFloat(h[3]);'
        'if(!isNaN(la)&&!isNaN(lo)&&!isNaN(z)&&!isNaN(t)&&Date.now()-t<_VIEW_KEEP_MS)'
        'return{lat:la,lon:lo,zoom:z,t:t};}'
        'return null;}\n'
        f'var acLat={ac_lat},acLon={ac_lon},acHdg={ac_hdg:.0f},acAlt={ac_alt_ft};\n'
        f'var fwMode={json.dumps(fw_mode)},navMode={json.dumps(nav_mode)};\n'
        f'var zones={zones_js};\n'
        f'var turbInfo={turb_js};\n'
        f'var turbSummary={turb_summary_js};\n'
        f'var sigmets={sigmets_js};\n'
        f'var gairmets={gairmets_js};\n'
        "var _sv=parseSavedView();\n"
        "var _lastUserView=_sv?_sv.t:0;\n"
        "var map=L.map('map',{zoomSnap:0.25,zoomDelta:0.5,wheelPxPerZoomLevel:120})"
        ".setView(_sv?[_sv.lat,_sv.lon]:[acLat,acLon],_sv?_sv.zoom:7);\n"
        "L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png',{\n"
        "  attribution:'Map data: &copy; <a href=\"https://www.openstreetmap.org/copyright\">"
        "OpenStreetMap</a> contributors, SRTM | "
        "Map style: &copy; <a href=\"https://opentopomap.org\">OpenTopoMap</a> (CC-BY-SA)',\n"
        "  subdomains:'abc',maxZoom:17\n"
        "}).addTo(map);\n"
        "var acSvg='<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"36\" height=\"36\"'"
        "  +' viewBox=\"-18 -18 36 36\" style=\"transform:rotate('+acHdg+'deg)\">'"
        "  +'<path d=\"M 0,-16 C 3.5,-13 4,-6 4,1 L 4,12 C 4,14 2,16 0,16"
        " C -2,16 -4,14 -4,12 L -4,1 C -4,-6 -3.5,-13 0,-16 Z\" fill=\"#3b82f6\"/>'"
        "  +'<polygon points=\"-4,1 -17,8 -16,11 -4,6\" fill=\"#3b82f6\"/>'"
        "  +'<polygon points=\"4,1 17,8 16,11 4,6\" fill=\"#3b82f6\"/>'"
        "  +'<rect x=\"-16\" y=\"7\" width=\"3\" height=\"4\" rx=\"0.5\" fill=\"#1e40af\"/>'"
        "  +'<rect x=\"-11\" y=\"4\" width=\"3\" height=\"4\" rx=\"0.5\" fill=\"#1e40af\"/>'"
        "  +'<rect x=\"8\" y=\"4\" width=\"3\" height=\"4\" rx=\"0.5\" fill=\"#1e40af\"/>'"
        "  +'<rect x=\"13\" y=\"7\" width=\"3\" height=\"4\" rx=\"0.5\" fill=\"#1e40af\"/>'"
        "  +'<polygon points=\"-4,12 -9,15 -8,16 -4,14\" fill=\"#3b82f6\"/>'"
        "  +'<polygon points=\"4,12 9,15 8,16 4,14\" fill=\"#3b82f6\"/>'"
        "  +'</svg>';\n"
        "L.marker([acLat,acLon],"
        "{icon:L.divIcon({html:acSvg,className:'',iconAnchor:[18,18]})}).addTo(map);\n"
        # Range rings around the aircraft, labelled at the top of each ring
        "[40,80,160,320,640].forEach(function(nm){\n"
        "  L.circle([acLat,acLon],{radius:nm*1852,color:'#1e293b',weight:1.5,"
        "opacity:0.6,dashArray:'6 6',fill:false,interactive:false}).addTo(map);\n"
        "  L.marker([acLat+nm/60,acLon],{interactive:false,keyboard:false,"
        "icon:L.divIcon({className:'',iconSize:[0,0],"
        "html:'<span style=\"display:inline-block;transform:translate(-50%,-50%);"
        "font-size:10px;line-height:1;padding:1px 4px;border-radius:3px;"
        "background:rgba(255,255,255,0.75);color:#1e293b;white-space:nowrap\">'"
        "+nm+' nm</span>'})}).addTo(map);\n"
        "});\n"
        "var bounds=L.latLngBounds([[acLat,acLon]]);\n"
        # One colour per SIGMET type, with a legend for the types shown
        "var _sigCol={TS:'#ef4444',TC:'#d946ef',VA:'#78716c',TURB:'#f97316',"
        "ICE:'#0ea5e9',MTW:'#16a34a'};\n"
        "function _fl(ft){return ft?'FL'+('00'+Math.round(ft/100)).slice(-3):'SFC';}\n"
        # Every area drawn, with its type and whether frankenweather uses
        # it, so the legend can list just the types in view (_sigLegend())
        "var _sigLayers=[];\n"
        # Areas frankenweather doesn't use (s.used_by: 'PSX', or null when
        # nobody gets them) are drawn dashed and fainter. Older
        # frankenweather versions don't send used_by: draw those solid.
        "function _sigDraw(hz,poly,fw,tip){\n"
        "  var col=_sigCol[hz]||'#64748b';\n"
        # An area across the date line: longitudes made continuous, or
        # Leaflet draws it the long way round the world
        "  poly=poly.map(function(p){return[p[0],p[1]];});\n"
        "  for(var i=1;i<poly.length;i++){"
        "while(poly[i][1]-poly[i-1][1]>180)poly[i][1]-=360;"
        "while(poly[i][1]-poly[i-1][1]<-180)poly[i][1]+=360;}\n"
        "  var lay=L.polygon(poly,{color:col,weight:2,opacity:fw?0.9:0.7,dashArray:fw?null:'6 5',"
        "fillColor:col,fillOpacity:fw?0.15:0.05})"
        ".bindTooltip(tip,{sticky:true,className:'sigmet-label'})"
        ".addTo(map);\n"
        "  _sigLayers.push({kind:hz,col:col,fw:fw,layer:lay});\n"
        "}\n"
        "function _sigWho(u,has){return !has?'':'<br>'+(u==='frankenweather'?"
        "'Used by frankenweather':u?'Not used by frankenweather (PSX gets it)':"
        "'Not used (SIGMETs off)');}\n"
        # The areas near the aircraft and the zones (from the page state)
        "var _sigNear={};\n"
        "function _sigKey(hz,poly){"
        "return hz+':'+poly[0][0].toFixed(3)+','+poly[0][1].toFixed(3);}\n"
        "sigmets.forEach(function(s){\n"
        "  var hz=s.hazard||'TS',has='used_by' in s,fw=!has||s.used_by==='frankenweather';\n"
        "  var pts=s.polygon.map(function(p){return[p[0],p[1]];});\n"
        "  _sigNear[_sigKey(hz,pts)]=true;\n"
        "  _sigDraw(hz,pts,fw,'<b>'+hz+' SIGMET</b><br>'+_fl(s.base_ft)+' – '+_fl(s.top_ft)"
        "+_sigWho(s.used_by,has));\n"
        "});\n"
        # G-AIRMET turbulence areas (US), one colour, dashed when
        # frankenweather doesn't use them (G-AIRMET turbulence switched off)
        "var _gaCol='#a855f7';\n"
        "gairmets.forEach(function(g){\n"
        "  var lay=L.polygon(g.polygon,{color:_gaCol,weight:2,opacity:g.used?0.9:0.7,"
        "dashArray:g.used?null:'6 5',fillColor:_gaCol,fillOpacity:g.used?0.12:0.05})"
        ".bindTooltip('<b>G-AIRMET '+g.severity+' TURB</b> ('+g.due_to+')<br>'"
        "+_fl(g.base_ft)+' – '+_fl(g.top_ft)+'<br>'+(g.used?'Used by frankenweather':"
        "'Not used (G-AIRMET turbulence switched off)'),"
        "{sticky:true,className:'sigmet-label'}).addTo(map);\n"
        "  _sigLayers.push({kind:'GAIRMET',col:_gaCol,fw:g.used,layer:lay});\n"
        "});\n"
        "var _hasCb=zones.some(function(z){return z.cb_pos;});\n"
        "var _hasCbTurb=zones.some(function(z){return z.cb_pos&&z.cb_turb;});\n"
        # The legend: the CB keys, and the weather area types with an area
        # in the current view (redone on every pan and zoom)
        "var _lgDiv=null;\n"
        "function _sigLegend(){\n"
        "  if(!_lgDiv)return;\n"
        "  var vb=map.getBounds(),seen={},order=[],unused=false;\n"
        "  _sigLayers.forEach(function(a){\n"
        "    if(!a.layer.getBounds().intersects(vb))return;\n"
        "    if(!(a.kind in seen)){seen[a.kind]=a.col;order.push(a.kind);}\n"
        "    if(!a.fw)unused=true;\n"
        "  });\n"
        "  var h='';\n"
        "  if(_hasCb)h+='<span style=\"display:inline-block;width:10px;margin-right:4px;"
        "color:#dc2626;font-weight:700;text-align:center\">✕</span>CB actual position';\n"
        "  if(_hasCbTurb)h+=(h?'<br>':'')+'<b>CB turbulence</b> (cloud levels)<br>'"
        "+[['#dc2626','core'],['#eab308','inner flank'],['#22c55e','outer flank']].map(function(k){"
        "return '<span style=\"display:inline-block;width:10px;height:10px;margin-right:4px;"
        "border:1.5px solid '+k[0]+';border-radius:50%;box-sizing:border-box\"></span>'+k[1];})"
        ".join('<br>');\n"
        "  if(_hasCbTurb&&zones.some(function(z){return z.cb_turb&&z.cb_turb.anvil_nm>0;}))"
        "h+='<br><span style=\"display:inline-block;width:10px;height:10px;margin-right:4px;"
        "border:1.5px dashed #a855f7;border-radius:50%;box-sizing:border-box\"></span>"
        "anvil (above the top, downwind)';\n"
        "  if(order.length)h+=(h?'<br>':'')+'<b>Weather areas</b> (in view)<br>'"
        "+order.map(function(k){"
        "return '<span style=\"display:inline-block;width:10px;height:10px;margin-right:4px;"
        "background:'+seen[k]+'\"></span>'+(k==='GAIRMET'?'G-AIRMET turbulence':k);}).join('<br>')"
        "+(unused?'<br><span style=\"display:inline-block;width:10px;height:10px;"
        "margin-right:4px;border:1.5px dashed #475569;box-sizing:border-box\"></span>"
        "not used by frankenweather':'');\n"
        "  _lgDiv.innerHTML=h;_lgDiv.style.display=h?'':'none';\n"
        "}\n"
        "var _lg=L.control({position:'bottomleft'});\n"
        "_lg.onAdd=function(){_lgDiv=L.DomUtil.create('div');"
        "_lgDiv.style.cssText='background:rgba(255,255,255,0.85);color:#1e293b;padding:4px 6px;"
        "border-radius:4px;font-size:11px;line-height:1.5';return _lgDiv;};\n"
        "_lg.addTo(map);\n"
        "map.on('moveend zoomend',_sigLegend);\n"
        # All the other valid SIGMET areas worldwide, from the full download
        # (frankenweather's --sigmet system; the router keeps a copy), so
        # zooming out shows them too. Without it, only the areas above.
        "fetch('/api/sigmet/latest/json',{cache:'no-store'}).then(function(r){"
        "return r.ok?r.json():null;}).catch(function(){return null;}).then(function(d){\n"
        "  var now=Date.now()/1000;\n"
        "  ((d&&d.sigmets)||[]).forEach(function(e){\n"
        "    if(!e.ok||e.expired||(e.valid_from&&e.valid_from>now)||(e.valid_to&&e.valid_to<now))"
        "return;\n"
        "    var hz=e.hazard||'TS';\n"
        "    (e.polygons||[]).forEach(function(poly){\n"
        "      if(!poly.length||_sigKey(hz,poly) in _sigNear)return;\n"
        "      _sigDraw(hz,poly,e.used_by==='frankenweather','<b>'+(e.qualifier?e.qualifier+' ':'')"
        "+hz+' SIGMET</b> '+e.fir+' '+e.series+'<br>'+_fl(e.base_ft)+' – '+_fl(e.top_ft)"
        "+_sigWho(e.used_by,true));\n"
        "    });\n"
        "  });\n"
        "  _sigLegend();\n"
        "});\n"
        "_sigLegend();\n"
        "var _LDIST=55,_secUsed={},_secAssign=[];\n"
        "var _natSecs=zones.map(function(z){"
        "var b=Math.atan2((z.lon-acLon)*Math.cos(acLat*Math.PI/180),"
        "z.lat-acLat)*180/Math.PI;"
        "return Math.round(((b%360+360)%360)/45)%8;});\n"
        "var _zByDist=zones.map(function(_,i){return i;}).sort(function(a,b){"
        "return gcdNm(acLat,acLon,zones[a].lat,zones[a].lon)"
        "-gcdNm(acLat,acLon,zones[b].lat,zones[b].lon);});\n"
        "_zByDist.forEach(function(i){"
        "var ns=_natSecs[i],sec=ns;"
        "for(var d=0;d<8;d++){"
        "if(!_secUsed[(ns+d)%8]){sec=(ns+d)%8;break;}"
        "if(!_secUsed[(ns-d+8)%8]){sec=(ns-d+8)%8;break;}}"
        "_secUsed[sec]=true;_secAssign[i]=sec;});\n"
        "var _zData=[];\n"
        "zones.forEach(function(z,_zi){\n"
        "  bounds.extend([z.lat,z.lon]);\n"
        "  var c=z.is_focused?'#3b82f6':(z.source==='VATSIM'?'#22c55e'"
        ":(z.source==='PSX'?'#a78bfa':'#94a3b8'));\n"
        "  var m=L.circleMarker([z.lat,z.lon],"
        "{radius:9,color:'#fff',weight:2,fillColor:c,fillOpacity:0.9,opacity:0.95})"
        ".addTo(map);\n"
        "  var wx=z.wx;\n"
        "  var cbTargetDiffers=wx.cb_raw&&z.cb_target&&"
        "(z.cb_target.oktas!==wx.cb_raw.oktas||z.cb_target.top!==wx.cb_raw.top);\n"
        "  var cbTargetSuffix=cbTargetDiffers?"
        "' → '+z.cb_target.oktas+\"/8 \"+z.cb_target.top+\"'\":'';\n"
        "  var cbRateSuffix=(z.cb_rate&&(Math.abs(z.cb_rate.top_ft_per_min)>=1||"
        "Math.abs(z.cb_rate.oktas_per_min)>=0.01))?"
        "' ('+(z.cb_rate.top_ft_per_min>=0?'+':'')+z.cb_rate.top_ft_per_min+\"'/min)\":'';\n"
        "  var cbLabel='';\n"
        "  if(wx.cb_raw){\n"
        "    var cr=wx.cb_raw;\n"
        "    cbLabel='<br>⛈ '+cr.oktas+\"/8 \"+cr.base+\"'-\"+cr.top+\"'\""
        "+cbTargetSuffix+cbRateSuffix;\n"
        "  }\n"
        "  if(cbLabel&&z.cb_sigmet)cbLabel+=' · SIGMET';\n"
        "  var tip='<b>WX'+z.zone+'</b> '+z.icao+(z.anchor?' ⚓':'')+cbLabel;\n"
        "  var tipCls=z.is_focused?'zone-label zone-label-focused':'zone-label';\n"
        "  var ct=z.cb_turb;\n"
        # Anvil zone (above the top): core + anvil_nm x (1/3 upwind ... 1
        # downwind), as frankenturb/cb_turbulence.py's _anvil_direction_factor()
        "  if(z.cb_pos&&ct&&ct.anvil_nm>0){\n"
        "    var ap=[];for(var b=0;b<360;b+=10){"
        "var f=ct.anvil_downwind_deg==null?1:1/3+2/3*(1+Math.cos((b-ct.anvil_downwind_deg)"
        "*Math.PI/180))/2;var r=(ct.core_nm+ct.anvil_nm*f)/60;"
        "ap.push([z.cb_pos[0]+r*Math.cos(b*Math.PI/180),"
        "z.cb_pos[1]+r*Math.sin(b*Math.PI/180)/Math.cos(z.cb_pos[0]*Math.PI/180)]);}\n"
        "    L.polygon(ap,{color:'#a855f7',weight:1.5,opacity:0.8,dashArray:'6 4',"
        "fill:false,interactive:false}).addTo(map);\n"
        "  }\n"
        "  if(z.cb_pos&&ct&&ct.shape==='circle'){\n"
        "    [[ct.core_nm+ct.flank_nm,'#22c55e'],[ct.core_nm+ct.flank_nm/2,'#eab308'],"
        "[ct.core_nm,'#dc2626']].forEach(function(r){\n"
        "      if(r[0]>0)L.circle(z.cb_pos,{radius:r[0]*1852,color:r[1],weight:1.5,opacity:0.8,"
        "fillColor:r[1],fillOpacity:0.12,interactive:false}).addTo(map);});\n"
        "  }\n"
        "  if(z.cb_pos)L.marker(z.cb_pos,{icon:L.divIcon({className:'',iconSize:[0,0],"
        "html:'<span style=\"display:inline-block;transform:translate(-50%,-50%);"
        "color:#dc2626;font-weight:700;font-size:16px;line-height:1;"
        "text-shadow:0 0 2px #fff,0 0 2px #fff\">✕</span>'})})"
        ".bindTooltip('<b>CB of WX'+z.zone+'</b><br>'+wx.cb_raw.oktas+\"/8 \"+wx.cb_raw.base"
        "+\"'-\"+wx.cb_raw.top+\"'\",{direction:'top'}).addTo(map);\n"
        "  if(z.is_focused)L.circleMarker([z.lat,z.lon],"
        "{radius:14,color:'#3b82f6',weight:2.5,fill:false,opacity:0.9}).addTo(map);\n"
        "  var cbRow=wx.cb_raw"
        "?'<tr><td style=\"color:#f87171\">⛈ CB</td><td style=\"color:#f87171\">'"
        "+wx.cb_raw.oktas+'/8 '+wx.cb_raw.base+\"'-\"+wx.cb_raw.top+\"'\""
        "+(cbTargetDiffers?'<span style=\"color:#94a3b8\">'+cbTargetSuffix+'</span>':'')"
        "+(cbRateSuffix?'<span style=\"color:#fbbf24;font-size:10px\">'+cbRateSuffix"
        "+'</span>':'')"
        "+'</td></tr>':'';\n"
        "  var metarLbl=z.source==='VATSIM'?'METAR':'METAR (gen)';\n"
        "  var metarRow=z.metar"
        "?'<tr><td style=\"vertical-align:top;color:#94a3b8;font-size:10px;"
        "white-space:nowrap\">'+metarLbl+'</td>"
        "<td style=\"font-family:monospace;font-size:10px;color:#94a3b8;"
        "word-break:break-all\">'+z.metar+'</td></tr>':'';\n"
        "  var detailRows='';\n"
        "  if(z.placement||z.weather_detail){\n"
        "    detailRows+='<tr><td colspan=\"2\" style=\"padding-top:6px\">"
        "<hr style=\"border:none;border-top:1px solid #2a2f45;margin:0 0 4px\"></td></tr>';\n"
        "    if(z.placement)detailRows+='<tr><td style=\"vertical-align:top;"
        "color:#64748b;font-size:10px;white-space:nowrap\">Placement</td>"
        "<td style=\"font-size:10px;color:#94a3b8\">'+z.placement+'</td></tr>';\n"
        "    if(z.weather_detail)detailRows+='<tr><td style=\"vertical-align:top;"
        "color:#64748b;font-size:10px;white-space:nowrap\">Weather</td>"
        "<td style=\"font-size:10px;color:#94a3b8\">'+z.weather_detail+'</td></tr>';\n"
        "  }\n"
        "  var pop='<div style=\"min-width:220px\">'"
        "    +'<b>WX'+z.zone+' — '+z.icao+'</b>'"
        "    +(z.anchor?'<br><span style=\"color:#60a5fa;font-size:11px\">⚓ Anchor zone: "
        "behind the aircraft, CB-free</span>':'')"
        "    +(z.cb_sigmet?'<br><span style=\"color:#f87171;font-size:11px\">⛈ CB from '"
        "+z.cb_sigmet+'</span>':'')"
        "    +'<br><span style=\"color:#94a3b8;font-size:11px\">'+z.source_label+'</span>'"
        "    +'<table style=\"width:100%;border-collapse:collapse;margin-top:4px\">'"
        "    +(wx.wind?'<tr><td>Wind</td><td>'+wx.wind+'</td></tr>':'')"
        "    +(wx.lo_cloud?'<tr><td>Lo cloud</td><td>'+wx.lo_cloud+'</td></tr>':'')"
        "    +(wx.hi_cloud?'<tr><td>Hi cloud</td><td>'+wx.hi_cloud+'</td></tr>':'')"
        "    +cbRow"
        "    +(wx.vis?'<tr><td>Vis</td><td>'+wx.vis+'</td></tr>':'')"
        "    +(wx.temp?'<tr><td>Temp/QNH</td><td>'+wx.temp+' / '+wx.qnh+'</td></tr>':'')"
        "    +metarRow"
        "    +detailRows"
        "    +'</table></div>';\n"
        "  m.bindPopup(pop,{className:'dark-popup',maxWidth:320});\n"
        "  L.polyline([[acLat,acLon],[z.lat,z.lon]],{color:c,weight:2.5,"
        "dashArray:'6,4',opacity:0.75}).addTo(map);\n"
        "  _zData.push({z:z,tip:tip,tipCls:tipCls,sec:_secAssign[_zi]});\n"
        "});\n"
        "if(zones.length>0&&!_sv)map.fitBounds(bounds.pad(0.4));\n"
        # Only user pans/zooms count: attached after our own initial fit
        "setTimeout(function(){map.on('dragstart zoomstart',function(){"
        "_lastUserView=Date.now();});},1000);\n"
        "var _labelLayers=[];\n"
        "var _tfs=["
        "'translateX(-50%) translateY(-100%)',"
        "'translateX(0) translateY(-100%)',"
        "'translateX(0) translateY(-50%)',"
        "'translateX(0) translateY(0)',"
        "'translateX(-50%) translateY(0)',"
        "'translateX(-100%) translateY(0)',"
        "'translateX(-100%) translateY(-50%)',"
        "'translateX(-100%) translateY(-100%)'];\n"
        "function _placeLabels(){\n"
        "  _labelLayers.forEach(function(l){map.removeLayer(l);});\n"
        "  _labelLayers=[];\n"
        "  _zData.forEach(function(item){\n"
        "    var z=item.z,sec=item.sec;\n"
        "    var _ar=sec*45*Math.PI/180;\n"
        "    var _zp=map.latLngToContainerPoint([z.lat,z.lon]);\n"
        "    var _lp=L.point(_zp.x+Math.sin(_ar)*_LDIST,_zp.y-Math.cos(_ar)*_LDIST);\n"
        "    var _ll=map.containerPointToLatLng(_lp);\n"
        "    var leader=L.polyline([[z.lat,z.lon],[_ll.lat,_ll.lng]],"
        "{color:'#6b7280',weight:1,opacity:0.7}).addTo(map);\n"
        "    var _bd=item.z.is_focused"
        "?'rgba(28,32,51,0.95);border-left:3px solid #3b82f6'"
        ":'rgba(28,32,51,0.92)';\n"
        "    var _st='display:inline-block;background:'+_bd+';border:1px solid #2a2f45;"
        "color:#f1f5f9;border-radius:4px;font-size:11px;padding:2px 6px;"
        "white-space:nowrap;cursor:pointer;transform:'+_tfs[sec]+';"
        "pointer-events:auto';\n"
        "    var lbl=L.marker([_ll.lat,_ll.lng],{icon:L.divIcon({"
        "html:'<div style=\"'+_st+'\">'+item.tip+'</div>',"
        "className:'',iconSize:[0,0],iconAnchor:[0,0]})}).addTo(map);\n"
        "    _labelLayers.push(leader,lbl);\n"
        "  });\n"
        "}\n"
        "_placeLabels();\n"
        "map.on('zoomend moveend',_placeLabels);\n"
        "if(turbInfo){\n"
        "  var brng=Math.atan2((turbInfo.src_lon-acLon)*Math.cos(acLat*Math.PI/180),"
        "turbInfo.src_lat-acLat)*180/Math.PI;\n"
        "  var fanPts=[[acLat,acLon]];\n"
        "  for(var a=-30;a<=30;a+=5)fanPts.push(destPoint(acLat,acLon,(brng+a+360)%360,80));\n"
        "  fanPts.push([acLat,acLon]);\n"
        "  L.polygon(fanPts,{color:'#f97316',weight:2.5,fillOpacity:0.25,"
        "opacity:0.9,fillColor:'#f97316'}).addTo(map);\n"
        "  var mtSvg='<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"36\" height=\"30\""
        " viewBox=\"0 0 36 30\" style=\"filter:drop-shadow(0 1px 2px rgba(0,0,0,0.7))\">"
        "<polygon points=\"18,2 34,28 2,28\" fill=\"#7c3aed\" stroke=\"#fff\""
        " stroke-width=\"1.5\"/>"
        "<polygon points=\"26,14 34,28 18,28\" fill=\"#a78bfa\" stroke=\"none\"/>"
        "<polygon points=\"10,20 16,20 13,14\" fill=\"#fff\" opacity=\"0.5\"/>"
        "</svg>';\n"
        "  L.marker([turbInfo.src_lat,turbInfo.src_lon],"
        "{icon:L.divIcon({html:mtSvg,className:'',iconAnchor:[18,28]})}).bindTooltip("
        "'<b>Terrain peak</b><br>'+turbInfo.kind+'<br>'+turbInfo.reason,"
        "{direction:'right',offset:[10,0]}).addTo(map);\n"
        "}\n"
        "setTimeout(function(){"
        "var c=map.getCenter(),z=map.getZoom();"
        "location.hash=c.lat.toFixed(5)+','+c.lng.toFixed(5)+','+z.toFixed(2)"
        "+','+_lastUserView;"
        "location.reload();},30000);\n"
        "function openFeedback(){\n"
        "  document.getElementById('fb-overlay').style.display='flex';\n"
        "  document.getElementById('fb-desc').value='';\n"
        "  document.getElementById('fb-step1').style.display='';\n"
        "  document.getElementById('fb-step2').style.display='none';\n"
        "  document.getElementById('fb-copy-btn').textContent='Copy to clipboard';\n"
        "}\n"
        "function closeFeedback(){"
        "document.getElementById('fb-overlay').style.display='none';}\n"
        "function generateReport(){\n"
        "  var desc=document.getElementById('fb-desc').value.trim();\n"
        "  if(!desc){alert('Please describe your experience first.');return;}\n"
        "  document.getElementById('fb-report').value=buildReport(desc);\n"
        "  document.getElementById('fb-step1').style.display='none';\n"
        "  document.getElementById('fb-step2').style.display='';\n"
        "}\n"
        "function buildReport(desc){\n"
        "  var d=new Date();\n"
        "  var L=['=== FrankenWeather Feedback Report ===',\n"
        "    'Generated: '+d.toISOString(),'',\n"
        "    'POSITION',\n"
        "    '  Lat '+acLat.toFixed(3)+'° Lon '+acLon.toFixed(3)+'°"
        " | Alt '+acAlt+' ft | Hdg '+acHdg+'°','',\n"
        "    'FW MODE: '+fwMode+' | NAV MODE: '+navMode,'',\n"
        "    'WEATHER ZONES'];\n"
        "  zones.forEach(function(z){\n"
        "    var zDist=Math.round(gcdNm(acLat,acLon,z.lat,z.lon));\n"
        "    L.push('  WX'+z.zone+' '+z.icao+' ['+z.source_label+']'"
        "+'  ('+zDist+' nm)');\n"
        "    L.push('    Location: '+z.lat.toFixed(3)+'° '+z.lon.toFixed(3)+'°');\n"
        "    if(z.reason)L.push('    Reason: '+z.reason);\n"
        "    var wx=z.wx,p=[];\n"
        "    if(wx.wind)p.push('Wind: '+wx.wind);\n"
        "    if(wx.lo_cloud)p.push('Lo cloud: '+wx.lo_cloud);\n"
        "    if(wx.hi_cloud)p.push('Hi cloud: '+wx.hi_cloud);\n"
        "    if(wx.cb_raw)p.push('CB: '+wx.cb_raw.oktas+\"/8 \""
        "+wx.cb_raw.base+\"'-\"+wx.cb_raw.top+\"'\");\n"
        "    if(wx.vis)p.push('Vis: '+wx.vis);\n"
        "    if(wx.temp)p.push('Temp/QNH: '+wx.temp+'/'+wx.qnh);\n"
        "    if(p.length)L.push('    '+p.join('  '));\n"
        "    if(z.metar)L.push('    METAR: '+z.metar);\n"
        "    if(z.wx_raw)L.push('    PSX Wx: '+z.wx_raw);\n"
        "    L.push('');\n"
        "  });\n"
        "  L.push('TURBULENCE');\n"
        "  if(turbSummary.pct>0){\n"
        "    L.push('  '+turbSummary.label+' '+turbSummary.pct"
        "+'% ('+turbSummary.intensity_label+')');\n"
        "    if(turbSummary.reason)L.push('  Reason: '+turbSummary.reason);\n"
        "    if(turbInfo)L.push('  Terrain peak: '+turbInfo.src_lat.toFixed(4)+'°"
        " '+turbInfo.src_lon.toFixed(4)+'°');\n"
        "    if(turbSummary.sources&&turbSummary.sources.length>1){\n"
        "      L.push('  Sources:');\n"
        "      turbSummary.sources.forEach(function(s){\n"
        "        var marker=s.active?'  ▶ ':'     ';\n"
        "        L.push(marker+s.kind+' '+s.pct+'%'+(s.reason?' — '+s.reason:''));\n"
        "      });\n"
        "    }\n"
        "  }else{L.push('  None active');}\n"
        "  L.push('');\n"
        "  L.push('ACTIVE SIGMETs');\n"
        "  if(sigmets.length===0){L.push('  None');}else{\n"
        "    var inside=[],nearby=[];\n"
        "    sigmets.forEach(function(s,i){\n"
        "      if(pointInPoly(acLat,acLon,s.polygon)){\n"
        "        inside.push({idx:i+1,s:s});\n"
        "      } else {\n"
        "        var cx=0,cy=0,n=s.polygon.length;\n"
        "        for(var k=0;k<n;k++){cx+=s.polygon[k][0];cy+=s.polygon[k][1];}\n"
        "        var d=gcdNm(acLat,acLon,cx/n,cy/n);\n"
        "        if(d<200)nearby.push({idx:i+1,s:s,dist:Math.round(d)});\n"
        "      }\n"
        "    });\n"
        "    L.push('  Total in region: '+sigmets.length);\n"
        "    if(inside.length===0&&nearby.length===0){\n"
        "      L.push('  None within 200 nm of aircraft');\n"
        "    }\n"
        "    if(inside.length>0){\n"
        "      L.push('  AIRCRAFT INSIDE '+inside.length+' SIGMET(S):');\n"
        "      inside.forEach(function(x){\n"
        "        L.push('    SIGMET '+x.idx+' ('+(x.s.hazard||'TS')+'): Top FL'"
        "+Math.round(x.s.top_ft/100)+'  ('+x.s.polygon.length+' pts)');\n"
        "      });\n"
        "    }\n"
        "    if(nearby.length>0){\n"
        "      nearby.sort(function(a,b){return a.dist-b.dist;});\n"
        "      L.push('  Nearby (<200 nm):');\n"
        "      nearby.forEach(function(x){\n"
        "        L.push('    SIGMET '+x.idx+' ('+(x.s.hazard||'TS')+'): Top FL'"
        "+Math.round(x.s.top_ft/100)+'  '+x.dist+' nm  ('+x.s.polygon.length+' pts)');\n"
        "      });\n"
        "    }\n"
        "  }\n"
        "  L.push('');\n"
        "  L.push('PILOT DESCRIPTION');\n"
        "  L.push('  '+desc.split('\\n').join('\\n  '));\n"
        "  return L.join('\\n');\n"
        "}\n"
        "function copyReport(){\n"
        "  var el=document.getElementById('fb-report');\n"
        "  navigator.clipboard.writeText(el.value).catch(function(){"
        "el.select();document.execCommand('copy');});\n"
        "  document.getElementById('fb-copy-btn').textContent='Copied!';\n"
        "  setTimeout(function(){"
        "document.getElementById('fb-copy-btn').textContent='Copy to clipboard';},2000);\n"
        "}\n"
        "function sendFeedbackEmail(){\n"
        "  var body=document.getElementById('fb-report').value;\n"
        "  window.location.href='mailto:mats.kronberg.game+fwfeedback@gmail.com"
        "?subject=FrankenWeather%20feedback"
        "&body='+encodeURIComponent(body);\n"
        "}\n"
        '</script>\n'
    )

    mode_color = '#f59e0b' if nav_mode == 'MANEUVERING' else '#22c55e'
    _FW_MODE_COLOR = {
        'enabled': '#22c55e', 'paused': '#f59e0b',
        'disabled': '#ef4444', 'manual': '#60a5fa',
    }
    fw_color = _FW_MODE_COLOR.get(fw_mode, '#94a3b8')
    turb_enabled = turbstate.get('enabled', True)
    turb_low_speed = turbstate.get('low_speed', False)
    if not turb_enabled:
        turb_on_color, turb_on_label = '#94a3b8', 'OFF'
    elif turb_low_speed:
        turb_on_color, turb_on_label = '#f59e0b', 'suppressed'
    else:
        turb_on_color, turb_on_label = '#22c55e', 'ON'
    fw_status_color, fw_status_label, fw_age_label = _fmt_data_status(now, received_at)
    msfs_status_color, msfs_status_label, msfs_age_label = _fmt_data_status(
        now, turbstate.get('msfs_last_seen_epoch'))

    # Compact weather summary for the focused zone
    focused_zd = next((z for z in zone_data if z.get('is_focused')), None)
    if focused_zd and focused_zd.get('wx'):
        _fwx = focused_zd['wx']
        _parts = [f'Wx{focused_zd["zone"]} {focused_zd["icao"]}', _fwx['wind']]
        if _fwx.get('cb_raw'):
            _parts.append(f'CB {_fwx["cb_raw"]["oktas"]}ok')
        if _fwx['vis'] != '≥10 km':
            _parts.append(_fwx['vis'])
        wx_zone_summary = ' · '.join(_parts)
    else:
        wx_zone_summary = ''

    # Compact turbulence summary (active kind + intensity, only when something is active)
    if turb_label and turb_label != '—' and turb_pct > 0:
        turb_summary = f'{turb_label} · {turb_pct}%'
        if turb_low_speed:
            turb_summary += ' (below 30 kt — not injected)'
    else:
        turb_summary = 'below 30 kt — not injected' if turb_low_speed and turb_enabled else ''

    _sub = 'font-size:0.78em;color:#64748b;padding:0 0 0.3em 0'

    # QNH display for map side panel
    psx_qnh_str = focused_zd['wx']['qnh'] if focused_zd and focused_zd.get('wx') else None
    msfs_qnh_map = turbstate.get('msfs_qnh_hpa')
    if psx_qnh_str and msfs_qnh_map is not None:
        psx_qnh_hpa = round(int(psx_qnh_str.split()[0]))
        qnh_diff = abs(msfs_qnh_map - psx_qnh_hpa)
        qnh_warn_color = '#22c55e' if qnh_diff < _QNH_WARN_THRESHOLD_HPA else '#f59e0b'
        qnh_display = (
            f'<b style="color:{qnh_warn_color}">'
            f'PSX {psx_qnh_str} / MSFS {msfs_qnh_map:.0f} hPa</b>'
        )
    elif psx_qnh_str:
        qnh_display = f'<b style="color:#e2e8f0">PSX {psx_qnh_str}</b>'
    else:
        qnh_display = None

    enroute_wind_on = bool((state.get("config") or {}).get("enroute_wind_enabled"))
    enroute_wind_color = '#22c55e' if enroute_wind_on else '#64748b'
    enroute_wind_label = 'on' if enroute_wind_on else 'off'

    side_panel = (
        '<div class="card" style="font-size:0.85em">\n'
        '<table style="width:100%;border-collapse:collapse">\n'
        f'<tr><td style="color:#94a3b8;padding:0.1em 0.4em 0.1em 0">Weather zones</td>'
        f'<td style="text-align:right">'
        f'<b style="color:{fw_color}">{fw_mode.upper()}</b></td></tr>\n' +
        (f'<tr><td colspan="2" style="{_sub}">{wx_zone_summary}</td></tr>\n'
         if wx_zone_summary else '') +
        f'<tr><td style="color:#94a3b8;padding:0.1em 0.4em 0.1em 0">Extra turbulence</td>'
        f'<td style="text-align:right">'
        f'<b style="color:{turb_on_color}">{turb_on_label}</b></td></tr>\n' +
        (f'<tr><td colspan="2" style="{_sub}">{turb_summary}</td></tr>\n'
         if turb_summary else '') +
        f'<tr><td style="color:#94a3b8;padding:0.4em 0.4em 0.1em 0">Navigation</td>'
        f'<td style="text-align:right"><b style="color:{mode_color}">{nav_mode}</b></td></tr>\n' +
        (f'<tr><td style="color:#94a3b8;padding:0.4em 0.4em 0.1em 0">QNH</td>'
         f'<td style="text-align:right">{qnh_display}</td></tr>\n'
         if qnh_display else '') +
        f'<tr><td style="color:#94a3b8;padding:0.4em 0.4em 0.1em 0">'
        f'FrankenWeather data</td>'
        f'<td style="text-align:right"><b style="color:{fw_status_color}">'
        f'{fw_status_label}</b></td></tr>\n'
        f'<tr><td colspan="2" style="{_sub}">{fw_age_label}</td></tr>\n' +
        f'<tr><td style="color:#94a3b8;padding:0.4em 0.4em 0.1em 0">MSFS data</td>'
        f'<td style="text-align:right"><b style="color:{msfs_status_color}">'
        f'{msfs_status_label}</b></td></tr>\n'
        f'<tr><td colspan="2" style="{_sub}">{msfs_age_label}</td></tr>\n' +
        ('<tr><td style="color:#94a3b8;padding:0.1em 0.4em 0.1em 0">Enroute wind</td>'
         '<td style="text-align:right">'
         f'<b style="color:{enroute_wind_color}">{enroute_wind_label}</b></td></tr>\n') +
        '</table>\n</div>\n'
        '<div style="display:flex;flex-direction:column;gap:0.5em">\n'
        '<button class="btn btn-blue btn-sm" onclick="openFeedback()">Feedback</button>\n'
        '</div>\n'
    )

    modal = (
        '<div id="fb-overlay" style="display:none;position:fixed;top:0;left:0;'
        'width:100%;height:100%;background:rgba(0,0,0,0.75);z-index:9999;'
        'align-items:center;justify-content:center">\n'
        '<div style="background:#1c2033;border:1px solid #2a2f45;border-radius:8px;'
        'padding:1.5em;width:min(90vw,640px);max-height:90vh;overflow-y:auto">\n'
        '<h2 style="margin:0 0 0.75em;color:#f1f5f9">FrankenWeather Feedback</h2>\n'
        '<div id="fb-step1">\n'
        '<p style="font-size:0.85em;color:#94a3b8;margin-top:0">'
        'Submitting a snapshot of current position, weather zones, and turbulence state. '
        'Please describe what you experienced.</p>\n'
        '<label style="display:block;font-size:0.85em;color:#94a3b8;margin-bottom:0.4em">'
        'Your experience</label>\n'
        '<textarea id="fb-desc" rows="5" style="width:100%;box-sizing:border-box;'
        'background:#0f1120;border:1px solid #2a2f45;border-radius:4px;'
        'color:#f1f5f9;padding:0.5em;font-size:0.9em;resize:vertical" '
        'placeholder="E.g.: Mountain wave turbulence was much stronger than expected '
        'for 33kt winds, or: Perfect match with real-world weather radar"></textarea>\n'
        '<div style="display:flex;gap:0.5em;justify-content:flex-end;margin-top:0.75em">\n'
        '<button class="btn btn-gray" onclick="closeFeedback()">Cancel</button>\n'
        '<button class="btn btn-blue" onclick="generateReport()">Generate report</button>\n'
        '</div>\n</div>\n'
        '<div id="fb-step2" style="display:none">\n'
        '<p style="font-size:0.85em;color:#94a3b8;margin-top:0">'
        'Copy this report and post it to the '
        '<strong style="color:#f1f5f9">PSCC Discord server</strong>, '
        'or use the button below to send it by email:</p>\n'
        '<textarea id="fb-report" rows="18" readonly style="width:100%;box-sizing:border-box;'
        'background:#0f1120;border:1px solid #2a2f45;border-radius:4px;'
        'color:#94a3b8;padding:0.5em;font-family:monospace;font-size:0.8em;'
        'resize:vertical"></textarea>\n'
        '<div style="display:flex;gap:0.5em;justify-content:flex-end;margin-top:0.75em">\n'
        '<button class="btn btn-gray" onclick="'
        'document.getElementById(\'fb-step1\').style.display=\'\';'
        'document.getElementById(\'fb-step2\').style.display=\'none\'">Back</button>\n'
        '<button id="fb-copy-btn" class="btn btn-blue" '
        'onclick="copyReport()">Copy to clipboard</button>\n'
        '<button class="btn btn-green" '
        'onclick="sendFeedbackEmail()">Send feedback via email</button>\n'
        '</div>\n</div>\n'
        '</div>\n</div>\n'
    )
    body = (
        '<div class="wx-map-wrap">\n'
        '<div id="map"></div>\n' +
        script +
        '<div class="wx-map-side">\n' + side_panel + '</div>\n'
        '</div>\n' +
        modal
    )
    return _page(body)


_ZONE_MODE_DESC = {
    'enabled': 'frankenweather fills each of the seven weather zones from live data '
               '(Open-Meteo, METARs, SIGMETs), shown below.',
    'manual': 'The weather you set below is put into all seven weather zones. The zones '
              'are still placed around the aircraft as in Auto.',
    'paused': 'Paused: the weather zones are frozen, with no updates from frankenweather '
              'or PSX.',
    'disabled': 'Disabled: PSX\'s own built-in weather is active; frankenweather does nothing.',
}


def _zone_mode_html(fw_mode: str) -> str:
    """Return the Weather zones page's Auto/Manual switch, plus Pause and Disable."""
    def _button(mode, label, active, title):
        style = ('background:#2563eb;color:#fff;cursor:default' if active
                 else 'background:#1c2033;color:#94a3b8;border:1px solid #2a2f45')
        return ('<form action="/api/weather/mode" method="post" style="margin:0">'
                f'<input type="hidden" name="mode" value="{mode}">'
                f'<button type="submit" class="btn btn-sm" title="{title}" '
                f'style="margin:0;width:auto;{style}"{" disabled" if active else ""}>'
                f'{label}</button></form>')
    switch = (_button('enabled', 'Auto', fw_mode == 'enabled', _ZONE_MODE_DESC['enabled']) +
              _button('manual', 'Manual', fw_mode == 'manual', _ZONE_MODE_DESC['manual']))
    others = ''.join(
        _button(mode, label, fw_mode == mode, _ZONE_MODE_DESC[mode])
        for mode, label in (('paused', 'Pause'), ('disabled', 'Disable')))
    card = 'warn' if fw_mode in ('paused', 'disabled') else 'ok'
    return (
        f'<div class="card {card}" style="display:flex;align-items:center;gap:1em;'
        'flex-wrap:wrap">'
        f'<div style="display:flex;gap:0">{switch}</div>'
        f'<div style="flex:1;min-width:16em;color:#94a3b8;font-size:0.88em">'
        f'{_ZONE_MODE_DESC.get(fw_mode, fw_mode)}</div>'
        f'<div style="display:flex;gap:0.4em">{others}</div></div>\n')


def _build_weather_settings_page(ctx):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Render the /weather/settings (Weather zones) page from the cached FRANKENWEATHER state."""
    color_scheme = ctx.color_scheme
    now = time.time()
    state = ctx.fw_state
    received_at = ctx.fw_state_received_at
    age_s = now - received_at if received_at else float('inf')
    stale = state is None or age_s > 300.0

    def _page(body):
        return _page_shell(
            color_scheme, 'Weather zones', _weather_nav(ctx, 'zones'), body,
            '\n<style>body { max-width: 64em; }</style>\n' + _MANUAL_WX_CSS)

    if stale:
        msg = ('No data received from frankenweather, '
               'check PSX Instructor station')
        if state is not None:
            age_min = int(age_s // 60)
            msg = (f'No recent frankenweather data (last received {age_min} min ago), '
                   'check PSX Instructor station')
        return _page(f'<div class="card warn"><p style="margin:0">{msg}</p></div>\n'
                     '<a href="/weather" class="btn btn-gray">Back</a>\n')

    age_str = f'{int(age_s)}s ago'
    mode = state.get('mode', '?')
    mode_class = 'warn' if mode == 'MANEUVERING' else 'ok'
    _NAV_MODE_DESC = {
        'ENROUTE': 'Normal cruise — weather zones placed ahead of and around the aircraft',
        'MANEUVERING': 'Hold or vectoring detected — weather zones redistributed all around',
    }
    mode_desc = _NAV_MODE_DESC.get(mode, '')
    ac_lat = state.get('ac_lat')
    ac_lon = state.get('ac_lon')
    ac_hdg = state.get('ac_hdg')
    ac_alt = state.get('ac_alt_ft')
    pos_str = (f'{ac_lat:.2f}°, {ac_lon:.2f}°' if ac_lat is not None else '—')
    hdg_str = f'{ac_hdg:.0f}°' if ac_hdg is not None else '—'
    alt_str = f'{int(ac_alt):,} ft' if ac_alt is not None else '—'
    body = (
        '<div class="card ok">\n<table>\n'
        f'<tr><td>Data age</td><td class="val">{age_str}</td></tr>\n'
        f'<tr><td>Mode</td><td class="{mode_class}">{mode}</td></tr>\n' +
        (f'<tr><td></td><td class="note" style="font-size:0.85em">{mode_desc}</td></tr>\n'
         if mode_desc else '') +
        f'<tr><td>Aircraft position</td><td class="val">{pos_str}</td></tr>\n'
        f'<tr><td>Heading / Altitude</td><td class="val">{hdg_str} / {alt_str}</td></tr>\n' +
        _om_usage_row(state) +
        '</table>\n</div>\n'
    )

    fw_mode = state.get('fw_mode', 'enabled')
    body = _zone_mode_html(fw_mode) + body
    if fw_mode == 'manual':
        return _page(body + _manual_weather_section(ctx))

    body += '<h2>Weather zones</h2>\n' + _cb_airport_mode_html(state.get('config', {}))

    zones = state.get('zones', [])
    body += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1em;align-items:start">\n'

    focused_zone = None
    try:
        fz_val = ctx.cache_get('FocussedWxZone')
        if fz_val is not None:
            focused_zone = int(fz_val)
    except (ValueError, TypeError):
        pass

    for zone in zones:
        zone_num = zone.get('zone', '?')
        icao = zone.get('icao', '?')
        lat = zone.get('lat')
        lon = zone.get('lon')
        source = zone.get('source', '?')
        reason = zone.get('reason', '')
        weather_detail = zone.get('weather_detail', '')
        pos_str2 = f'{lat:.2f}/{lon:.2f}' if lat is not None else '—'
        src_color = '#4ade80' if source == 'VATSIM' else '#94a3b8'

        wx_str = ctx.cache_get(f'Wx{zone_num}') or ''
        wx = _parse_wx_brief(wx_str)

        metar_str = None
        if source == 'VATSIM':
            metar_str = ctx.cache_get(f'Metar{zone_num}')

        is_focused = (focused_zone is not None and zone_num == focused_zone)
        border_style = 'border-left: 4px solid #3b82f6' if is_focused else ''
        focused_badge = (
            ' <span style="font-size:0.8em;color:#3b82f6">●</span>'
            if is_focused else ''
        )
        if zone.get('anchor'):
            focused_badge += (' <span style="font-size:0.75em;color:#60a5fa" '
                              'title="Behind the aircraft, CB-free: no other zone is near">'
                              '⚓ anchor</span>')
        if zone.get('cb_sigmet'):
            focused_badge += (' <span style="font-size:0.75em;color:#fecaca;background:#7f1d1d;'
                              'border-radius:3px;padding:0 4px" '
                              f'title="CB from {_esc(zone["cb_sigmet"])}">⛈ SIGMET</span>')
        body += (
            f'<div class="card ok" style="{border_style}">\n'
            f'<div style="display:flex;justify-content:space-between;align-items:baseline;'
            f'margin-bottom:0.4em">\n'
            f'<b>Zone {zone_num} &mdash; {icao}</b>{focused_badge}\n'
            f'<span style="font-size:0.82em;color:{src_color}">{source}</span>\n'
            '</div>\n'
        )
        if weather_detail:
            body += (
                f'<p class="note" style="margin:0 0 0.4em">{weather_detail}</p>\n'
            )
        elif reason:
            body += f'<p class="note" style="margin:0 0 0.4em">{reason}</p>\n'
        if metar_str:
            body += (
                f'<p style="font-family:monospace;font-size:0.82em;'
                f'word-break:break-all;color:#94a3b8;margin:0 0 0.4em">'
                f'{metar_str}</p>\n'
            )
        if wx:
            body += (
                '<table>\n'
                f'<tr><td>Position</td><td class="val">{pos_str2}</td></tr>\n'
                f'<tr><td>Wind</td><td class="val">{wx["wind"]}</td></tr>\n'
                f'<tr><td>Visibility</td><td class="val">{wx["vis"]}</td></tr>\n'
                f'<tr><td>Lo cloud</td><td class="val">{wx["lo_cloud"]}</td></tr>\n'
                f'<tr><td>Hi cloud</td><td class="val">{wx["hi_cloud"]}</td></tr>\n'
            )
            if wx['cb'] != 'None':
                cb_target = zone.get('cb_target')
                cb_rate = zone.get('cb_rate')
                cb_raw = wx.get('cb_raw') or {}
                target_suffix = ''
                if cb_target and (cb_target.get('oktas') != cb_raw.get('oktas') or
                                  cb_target.get('top') != cb_raw.get('top')):
                    target_suffix = (f' <span style="color:#94a3b8">&rarr; '
                                     f'{cb_target["oktas"]}/8 {cb_target["top"]} ft</span>')
                rate_suffix = ''
                top_rate = (cb_rate or {}).get('top_ft_per_min', 0)
                okt_rate = (cb_rate or {}).get('oktas_per_min', 0)
                if cb_rate and (abs(top_rate) >= 1 or abs(okt_rate) >= 0.01):
                    sign = '+' if top_rate >= 0 else ''
                    rate_suffix = (
                        f' <span style="color:#fbbf24;font-size:0.82em">'
                        f'({sign}{top_rate:.0f} ft/min)</span>')
                body += (
                    f'<tr><td>CB</td><td class="warn">{wx["cb"]}'
                    f'{target_suffix}{rate_suffix}</td></tr>\n')
            body += (
                f'<tr><td>Temp / QNH</td><td class="val">{wx["temp"]} / {wx["qnh"]}</td></tr>\n'
                '</table>\n'
            )
        body += '</div>\n'
    body += '</div>\n'

    cfg = state.get('config', {})
    infront = cfg.get('new_zone_infront_range', [0, 0])
    leftright = cfg.get('new_zone_leftright_range', [0, 0])
    squeeze = cfg.get('cape_squeeze')
    fake_cb = cfg.get('fake_cb')

    # MSFS bridge settings and live data
    tstate = ctx.fw_turbstate or {}
    msfs_active = tstate.get('msfs_active', False)
    msfs_in_cloud = tstate.get('msfs_in_cloud')
    msfs_qnh_hpa = tstate.get('msfs_qnh_hpa')
    msfs_wind_vert = tstate.get('msfs_wind_vert')
    msfs_precip_state = tstate.get('msfs_precip_state')
    _PRECIP_LABEL = {2: 'None', 4: 'Rain', 8: 'Snow'}
    in_cloud_sync = cfg.get('msfs_in_cloud_sync', True)
    qnh_check_mode = cfg.get('msfs_qnh_check', 'CHECK')

    def _settings_toggle(field, val, label, active, color='btn-green'):
        inactive_color = 'btn-gray'
        return (
            f'<form method="post" action="/api/weather/settings" style="display:inline">'
            f'<input type="hidden" name="{field}" value="{val}">'
            f'<button class="btn {color if active else inactive_color} btn-sm"'
            f' title="Click to toggle">{label}</button></form>'
        )

    msfs_conn_color = '#22c55e' if msfs_active else '#ef4444'
    msfs_conn_label = 'Connected' if msfs_active else 'Not connected'
    ic_label = ('Yes' if msfs_in_cloud else 'No') if msfs_in_cloud is not None else '—'

    body += '<h2>MSFS bridge</h2>\n'
    body += (
        '<p class="note">Data from PSX.NET.MSFS.Client used to sync '
        'PSX weather to what MSFS sees. These settings take effect only when it '
        'is connected.</p>\n'
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1em;align-items:start">\n'
        '<div class="card ok">\n'
        '<b>Live data</b>\n<table style="margin-top:0.5em">\n'
        f'<tr><td>Bridge</td><td class="val" style="color:{msfs_conn_color}">'
        f'{msfs_conn_label}</td></tr>\n'
        f'<tr><td>In cloud</td><td class="val">{ic_label}</td></tr>\n' +
        (f'<tr><td>MSFS QNH</td><td class="val">{msfs_qnh_hpa:.1f} hPa</td></tr>\n'
         if msfs_qnh_hpa is not None else '') +
        (f'<tr><td>Wind vertical</td><td class="val">'
         f'{msfs_wind_vert:+.0f} kt</td></tr>\n'
         if msfs_wind_vert is not None else '') +
        (f'<tr><td>Precip</td><td class="val">'
         f'{_PRECIP_LABEL.get(msfs_precip_state, str(msfs_precip_state))}</td></tr>\n'
         if msfs_precip_state is not None else '') +
        '</table>\n</div>\n'
        '<div class="card ok">\n'
        '<b>Sync settings</b>\n<table style="margin-top:0.5em">\n'
        '<tr><td style="padding-right:0.8em">In-cloud sync<br>'
        '<span style="font-size:0.82em;color:#64748b">Adjust PSX cloud layers so '
        'aircraft is always inside cloud when MSFS says so</span></td><td>' +
        _settings_toggle('msfs_in_cloud_sync', 'false' if in_cloud_sync else 'true',
                         'ON' if in_cloud_sync else 'OFF',
                         in_cloud_sync) +
        '</td></tr>\n'
        '<tr><td style="padding-right:0.8em">QNH mode<br>'
        '<span style="font-size:0.82em;color:#64748b">CHECK: warn when MSFS and PSX QNH '
        'differ by more than 1 hPa. SYNC: also update PSX QNH to match MSFS</span></td><td>'
        '<div style="display:flex;gap:0.3em">' +
        _settings_toggle('msfs_qnh_check', 'CHECK', 'CHECK',
                         qnh_check_mode == 'CHECK', 'btn-amber') +
        _settings_toggle('msfs_qnh_check', 'SYNC', 'SYNC',
                         qnh_check_mode == 'SYNC', 'btn-green') +
        '</div></td></tr>\n'
        '</table>\n</div>\n'
        '</div>\n'
    )

    body += (
        '<h2>Configuration</h2>\n'
        '<p class="note">These values are read-only here. '
        'Use command-line options (<code>--help</code>) to change them.</p>\n'
        '<div class="card ok">\n<table>\n'
        f'<tr><td>Cruise behind relocation</td>'
        f'<td class="val">{cfg.get("cruise_behind_dist", "?")} nm</td></tr>\n'
        f'<tr><td>Low-alt relocation</td>'
        f'<td class="val">{cfg.get("low_alt_dist", "?")} nm</td></tr>\n'
        f'<tr><td>Zone range ahead</td>'
        f'<td class="val">{infront[0]}–{infront[1]} nm</td></tr>\n'
        f'<tr><td>Zone range lateral</td>'
        f'<td class="val">{leftright[0]}–{leftright[1]} nm</td></tr>\n'
        f'<tr><td>Min zone separation</td>'
        f'<td class="val">{cfg.get("new_zone_notnear", "?")} nm</td></tr>\n' +
        (f'<tr><td>CAPE squeeze</td><td class="val">'
         f'at {squeeze[0]} J/kg → zones placed at most {squeeze[1]} nm ahead</td></tr>\n'
         if squeeze else '') +
        (f'<tr><td>Fake CB override</td><td class="warn">'
         f'{fake_cb[0]} oktas base {fake_cb[1]} ft top {fake_cb[2]} ft</td></tr>\n'
         if fake_cb else '') +
        '</table>\n</div>\n'
    )

    body += ('<p class="note">Saving, loading and resetting settings: see the '
             '<a href="/weather/config">Config page</a>.</p>\n')

    body += '<hr>\n<a href="/weather" class="btn btn-gray">Back to map</a>\n'
    return _page(body)


def _config_file_html(cfg: dict) -> str:
    """Return the Config page's config file section: path, status, save/load/reset buttons."""
    config_file = cfg.get("config_file")
    config_file_exists = cfg.get("config_file_exists", False)
    body = '<h2>Config file</h2>\n'
    if not config_file:
        body += (
            '<div class="card warn"><p style="margin:0">No <code>--config-file</code> given '
            'at startup — settings cannot be saved or reloaded from disk. Restart '
            'frankenweather with <code>--config-file PATH</code> to enable this.</p></div>\n'
        )
    else:
        exists_color = '#22c55e' if config_file_exists else '#94a3b8'
        exists_label = 'exists' if config_file_exists else 'not yet saved'
        body += (
            '<div class="card ok">\n<table>\n'
            f'<tr><td>Path</td><td class="val" style="font-family:monospace">'
            f'{config_file}</td></tr>\n'
            f'<tr><td>Status</td><td class="val" style="color:{exists_color}">'
            f'{exists_label}</td></tr>\n'
            '</table>\n</div>\n'
            '<div class="btn-row">\n'
            '<form action="/api/weather/config/save" method="post" style="display:inline">'
            '<button type="submit" class="btn btn-green btn-sm" '
            'title="Write all current settings to the config file">'
            'Save current settings to file</button></form>\n'
            '<form action="/api/weather/config/load" method="post" style="display:inline">'
            '<button type="submit" class="btn btn-blue btn-sm" '
            'title="Reload settings from the config file, discarding unsaved changes">'
            'Load settings from file</button></form>\n'
            '<form action="/api/weather/config/reset" method="post" style="display:inline">'
            '<button type="submit" class="btn btn-amber btn-sm" '
            'title="Reset all settings to their built-in defaults (does not touch the '
            'config file)">Reset settings to default</button></form>\n'
            '</div>\n'
        )
    return body


def _build_weather_config_page(ctx):
    """Render the /weather/config page: the config file (save/load/reset) and time sync."""
    color_scheme = ctx.color_scheme
    state = ctx.fw_state
    received_at = ctx.fw_state_received_at
    age_s = time.time() - received_at if received_at else float('inf')
    stale = state is None or age_s > 300.0

    def _page(body):
        return _page_shell(color_scheme, _config_page_title(ctx), _weather_nav(ctx, 'config'),
                           body)

    if stale:
        msg = ('No data received from frankenweather, '
               'check PSX Instructor station')
        if state is not None:
            age_min = int(age_s // 60)
            msg = (f'No recent frankenweather data (last received {age_min} min ago), '
                   'check PSX Instructor station')
        return _page(f'<div class="card warn"><p style="margin:0">{msg}</p></div>\n'
                     '<a href="/weather" class="btn btn-gray">Back</a>\n')

    cfg = state.get('config', {})
    on_startup = cfg.get('timesync_on_startup', True)
    on_situ_load = cfg.get('timesync_on_situ_load', True)
    periodic = cfg.get('timesync_periodic', False)

    def _toggle(field, val, label, active, color='btn-green'):
        inactive_color = 'btn-gray'
        return (
            f'<form method="post" action="/api/weather/config" style="display:inline">'
            f'<input type="hidden" name="{field}" value="{val}">'
            f'<button class="btn {color if active else inactive_color} btn-sm"'
            f' title="Click to toggle">{label}</button></form>'
        )

    body = _config_file_html(cfg) + (
        '<h2>Time sync</h2>\n'
        '<p class="note">Keeps PSX\'s clocks (TimeEarth, and both cockpit clocks) in sync '
        "with your computer's real-world time.</p>\n"
        '<div class="card ok">\n<table>\n'
        '<tr><td style="padding-right:0.8em">Sync on startup<br>'
        '<span style="font-size:0.82em;color:#64748b">Sync every time FrankenWeather '
        'connects or reconnects to PSX.</span></td><td>' +
        _toggle('timesync_on_startup', 'false' if on_startup else 'true',
                'ON' if on_startup else 'OFF', on_startup) +
        '</td></tr>\n'
        '<tr><td style="padding-right:0.8em">Sync on situ load<br>'
        '<span style="font-size:0.82em;color:#64748b">Sync whenever PSX finishes loading a '
        "situation — detected via PSX's own load1/load3 pause/resume signal, the only wire-"
        'protocol event that brackets a situ (re)load. Turn off if you want to keep the '
        'aircraft clock a loaded situ brings with it.</span></td><td>' +
        _toggle('timesync_on_situ_load', 'false' if on_situ_load else 'true',
                'ON' if on_situ_load else 'OFF', on_situ_load) +
        '</td></tr>\n'
        '<tr><td style="padding-right:0.8em">Periodic resync<br>'
        '<span style="font-size:0.82em;color:#64748b">Every minute, resync if PSX\'s clock '
        "has drifted more than 1 second from your computer's clock. Useful after flying with "
        'time acceleration, to catch back up once you return to 1x.</span></td><td>' +
        _toggle('timesync_periodic', 'false' if periodic else 'true',
                'ON' if periodic else 'OFF', periodic) +
        '</td></tr>\n'
        '</table>\n</div>\n'
    )
    if getattr(ctx, 'event_log_dir', None):
        body += _event_logs_html(ctx)
    return _page(body)


_MANUAL_WX_CSS = (
    '\n<style>body { max-width: 56em; }'
    '.form-grid { display:grid;grid-template-columns:1fr 1fr;gap:1em; }'
    '.form-section { background:#1c2033;border:1px solid #2a2f45;'
    'border-radius:6px;padding:1em; }'
    '.form-section h3 { margin:0 0 0.75em;font-size:0.95em;color:#94a3b8;'
    'text-transform:uppercase;letter-spacing:0.05em; }'
    '.field-row { display:flex;align-items:center;gap:0.5em;'
    'margin-bottom:0.5em;flex-wrap:wrap; }'
    '.field-label { flex:0 0 7em;font-size:0.88em;color:#94a3b8; }'
    '.field-input { flex:1;min-width:4em; }'
    'input[type=number],input[type=text],select { '
    'background:#0f1117;border:1px solid #2a2f45;border-radius:4px;'
    'color:#e2e8f0;padding:3px 6px;font-size:0.88em; }'
    '.metar-box { font-family:monospace;font-size:0.9em;padding:0.75em;'
    'background:#0f1117;border:1px solid #2a2f45;border-radius:4px;'
    'color:#4ade80;word-break:break-all;margin-bottom:1em; }'
    '.mode-badge-manual { color:#60a5fa; }'
    '.mode-badge-enabled { color:#4ade80; }'
    '</style>\n'
)


def _manual_weather_section(ctx):  # pylint: disable=too-many-locals,too-many-statements
    """Return the Weather zones page's manual weather controls (shown in Manual mode).

    The weather set here is put into all seven weather zones. Needs
    _MANUAL_WX_CSS in the page's <head>.
    """
    state = ctx.fw_state
    params = {}
    if state:
        params = state.get("manual_wx_params", {})
    fw_mode = (state or {}).get("fw_mode", "unknown")

    focused_zone = None
    try:
        fz_val = ctx.cache_get('FocussedWxZone')
        if fz_val is not None:
            focused_zone = int(fz_val)
    except (ValueError, TypeError):
        pass

    def _page(body):
        return body

    mode_color = 'mode-badge-manual' if fw_mode == 'manual' else 'mode-badge-enabled'
    zones = (state or {}).get("zones", [])
    focused_icao = next(
        (z.get("icao", "ZZZZ") for z in zones if z.get("zone") == focused_zone),
        "ZZZZ"
    )
    metar_str = _manual_metar(params, icao=focused_icao) if params else 'No data'
    body = (
        '<p class="note">Manual weather differs from the PSX instructor station weather: '
        'all weather zones will use the same settings defined here; '
        'the zones are still placed automatically around the aircraft as in normal mode; '
        'and extra turbulence continues to be injected unless explicitly disabled on the '
        'Turbulence page.</p>\n'
    ) + (
        '<div class="card ok" style="margin-bottom:1em">'
        f'<div style="display:flex;align-items:center;gap:1.5em;flex-wrap:wrap">'
        f'<div><span style="color:#94a3b8;font-size:0.85em">FW mode</span><br>'
        f'<b class="{mode_color}">{fw_mode}</b></div>'
        f'<div style="flex:1">'
        f'<span style="color:#94a3b8;font-size:0.85em">METAR preview</span><br>'
        f'<span class="metar-box" style="display:block;margin:0;padding:0.3em 0.5em">'
        f'{metar_str}</span></div>'
    )
    if focused_zone is not None:
        body += (
            f'<form action="/api/weather/manual/copy_zone" method="post">'
            f'<input type="hidden" name="zone" value="{focused_zone}">'
            f'<button type="submit" class="btn btn-amber btn-sm">'
            f'Copy zone {focused_zone} weather</button>'
            f'</form>'
        )
    body += '</div></div>\n'

    def _int(key, default=0):
        return int(params.get(key, default))

    def _float(key, default=0.0):
        return float(params.get(key, default))

    def _num_input(name, value, mn, mx, step=1, width="5em"):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        return (
            f'<input type="number" name="{name}" value="{value}" '
            f'min="{mn}" max="{mx}" step="{step}" style="width:{width}">'
        )

    def _select(name, options, current):
        html = f'<select name="{name}">'
        for val, label in options:
            sel = ' selected' if str(val) == str(current) else ''
            html += f'<option value="{val}"{sel}>{label}</option>'
        html += '</select>'
        return html

    mb_mode = _int("mb_mode", 0)
    mb_chance = _int("mb_chance", 0)
    turb_sev = _int("turb_severity", 0)
    inv_on = bool(params.get("inv_on", False))
    precip = _int("precip", 0)

    form_open = '<form action="/api/weather/manual" method="post" id="manual-wx-form">\n'
    form_close = (
        '<div style="margin-top:1.5em;text-align:right">'
        '<button type="submit" class="btn btn-blue" id="apply-btn">Apply settings</button>'
        '</div>\n'
        '</form>\n'
        '<script>\n'
        '(function(){\n'
        '  var form = document.getElementById("manual-wx-form");\n'
        '  var btn  = document.getElementById("apply-btn");\n'
        '  function serialize(){\n'
        '    var d=new FormData(form),out=[];\n'
        '    d.forEach(function(v,k){out.push(k+"="+v);});\n'
        '    return out.sort().join("&");\n'
        '  }\n'
        '  var initial = serialize();\n'
        '  function check(){\n'
        '    var dirty = serialize() !== initial;\n'
        '    btn.className = dirty ? "btn btn-amber" : "btn btn-blue";\n'
        '    btn.textContent = dirty ? "Apply settings •" : "Apply settings";\n'
        '  }\n'
        '  form.addEventListener("input", check);\n'
        '  form.addEventListener("change", check);\n'
        '})();\n'
        '</script>\n'
    )

    body += form_open
    body += '<div class="form-grid">\n'

    body += (
        '<div class="form-section">'
        '<h3>Cloud layers</h3>'
        '<table style="border-collapse:collapse;width:100%">'
        '<tr><th style="color:#64748b;font-size:0.8em;text-align:left;padding:2px 4px">'
        '</th>'
        '<th style="color:#64748b;font-size:0.8em;text-align:center;padding:2px 4px">'
        'Oktas</th>'
        '<th style="color:#64748b;font-size:0.8em;text-align:center;padding:2px 4px">'
        'Base (ft)</th>'
        '<th style="color:#64748b;font-size:0.8em;text-align:center;padding:2px 4px">'
        'Top (ft)</th></tr>\n'
    )
    for layer, prefix, def_base, def_top in [
            ("Lo cloud", "lo", 2000, 10000),
            ("Hi cloud", "hi", 20000, 35000),
            ("CB", "cb", 3000, 35000),
    ]:
        ok = _int(f"{prefix}_oktas")
        base = _int(f"{prefix}_base", def_base)
        top = _int(f"{prefix}_top", def_top)
        body += (
            f'<tr><td style="padding:4px;color:#94a3b8;font-size:0.88em">{layer}</td>'
            f'<td style="padding:4px;text-align:center">'
            f'{_num_input(f"{prefix}_oktas", ok, 0, 8, 1, "3.5em")}</td>'
            f'<td style="padding:4px;text-align:center">'
            f'{_num_input(f"{prefix}_base", base, 0, 60000, 100, "5.5em")}</td>'
            f'<td style="padding:4px;text-align:center">'
            f'{_num_input(f"{prefix}_top", top, 0, 60000, 100, "5.5em")}</td>'
            f'</tr>\n'
        )
    body += '</table></div>\n'

    body += (
        '<div class="form-section">'
        '<h3>Surface wind</h3>'
        '<table style="border-collapse:collapse;width:100%">'
    )
    wind_fields = [
        ("Dir (°)", "wind_dir", _int("wind_dir"), 0, 360, 1, "4.5em"),
        ("Speed (kt)", "wind_spd", _int("wind_spd"), 0, 150, 1, "4.5em"),
        ("Gust (kt)", "wind_gust", _int("wind_gust"), 0, 200, 1, "4.5em"),
        ("Variability (°)", "wind_var", _int("wind_var"), 0, 180, 1, "4.5em"),
    ]
    for label, name, val, mn, mx, step, width in wind_fields:
        body += (
            f'<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">{label}</td>'
            f'<td style="padding:3px 4px">{_num_input(name, val, mn, mx, step, width)}'
            f'</td></tr>\n'
        )
    body += '</table></div>\n'

    body += (
        '<div class="form-section">'
        '<h3>Conditions</h3>'
        '<table style="border-collapse:collapse;width:100%">'
    )
    body += (
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Surface temp (°C)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("surf_temp", _int("surf_temp", 15), -60, 50, 1, "4.5em")}</td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">QNH (hPa)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("qnh_hpa", round(_float("qnh_hpa", 1013.25)), 880, 1084, 1, "5em")}'
        '</td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Visibility (m)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("vis_m", _int("vis_m", 9999), 100, 9999, 1, "5em")}</td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Precipitation</td>'
        '<td style="padding:3px 4px">' +
        _select("precip",
                [("0", "None"), ("1", "Light"), ("2", "Moderate"), ("3", "Heavy")],
                precip) +
        '</td></tr>\n'
    )
    body += '</table></div>\n'

    body += (
        '<div class="form-section">'
        '<h3>Turbulence</h3>'
        '<table style="border-collapse:collapse;width:100%">'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Severity</td>'
        '<td style="padding:3px 4px">' +
        _select("turb_severity",
                [("0", "None"), ("1", "Light"), ("2", "Moderate"), ("3", "Severe")],
                turb_sev) +
        '</td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Base (ft)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("turb_base", _int("turb_base", 0), 0, 60000, 100, "5.5em")}</td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Top (ft)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("turb_top", _int("turb_top", 5000), 0, 60000, 100, "5.5em")}</td></tr>\n'
        '</table></div>\n'
    )

    inv_checked = ' checked' if inv_on else ''
    body += (
        '<div class="form-section">'
        '<h3>Inversion</h3>'
        '<table style="border-collapse:collapse;width:100%">'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Enable</td>'
        f'<td style="padding:3px 4px">'
        f'<input type="checkbox" name="inv_on" value="1"{inv_checked}></td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Altitude (ft)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("inv_top", _int("inv_top", 2320), 0, 20000, 10, "5.5em")}</td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Temp delta (°C)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("inv_tmp", _int("inv_tmp", 5), -20, 40, 1, "4.5em")}</td></tr>\n'
        '</table></div>\n'
    )

    _MB_OPTS = [("0", "Off / random"), ("1", "Left"), ("2", "On-track"), ("3", "Right")]
    body += (
        '<div class="form-section" style="grid-column:1/-1">'
        '<h3>Microburst</h3>'
        '<div style="display:flex;gap:2em;flex-wrap:wrap">'
        '<table style="border-collapse:collapse">'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Direction</td>'
        f'<td style="padding:3px 4px">{_select("mb_mode", _MB_OPTS, mb_mode)}</td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Chance (%)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("mb_chance", mb_chance, 0, 100, 1, "4.5em")}'
        f'<span style="color:#64748b;font-size:0.8em;margin-left:0.4em">'
        f'(0 = off; random mode uses only this field)</span></td></tr>\n'
        '<tr><td style="padding:3px 4px;color:#94a3b8;font-size:0.88em">Outflow (ft)</td>'
        f'<td style="padding:3px 4px">'
        f'{_num_input("mb_outflow", _int("mb_outflow", 400), 0, 5000, 50, "5em")}'
        f'<span style="color:#64748b;font-size:0.8em;margin-left:0.4em">'
        f'(ignored in random mode)</span></td></tr>\n'
        '</table></div></div>\n'
    )

    body += '</div>\n'
    body += form_close
    return _page(body)


def _build_weather_turb_page(ctx):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Render the /weather/turbulence control page."""
    color_scheme = ctx.color_scheme
    now = time.time()
    tstate = ctx.fw_turbstate or {}
    received_at = ctx.fw_turbstate_received_at
    age_s = now - received_at if received_at else float('inf')

    def _page(body):
        extra_head = (
            '\n<style>body { max-width: 56em; }'
            'table.turb-types { width:100%; border-collapse:collapse; }'
            'table.turb-types td,table.turb-types th {'
            ' padding:4px 8px; border-bottom:1px solid #334155; }'
            'table.turb-types th { color:#94a3b8; font-size:0.8em; text-align:left; }'
            '</style>\n'
            '<script>setTimeout(function(){location.reload();},30000);</script>\n'
        )
        return _page_shell(color_scheme, 'Extra turbulence', _weather_nav(ctx, 'turb'), body,
                           extra_head)

    stale_banner = ''
    if not tstate:
        stale_banner = (
            '<div class="card warn"><p style="margin:0">No turbulence data received. '
            'Start frankenweather with turbulence enabled to use these controls.</p></div>\n'
        )
    elif age_s > 300.0:
        stale_banner = (
            f'<div class="card warn"><p style="margin:0">Stale data '
            f'({int(age_s)}s old) — frankenweather may be disconnected.</p></div>\n'
        )

    enabled = tstate.get('enabled', True)
    manual_turb_enabled = tstate.get('manual_turb_enabled', False)
    manual_turb_kind = tstate.get('manual_turb_kind', 'mechanical')
    manual_turb_intensity = float(tstate.get('manual_turb_intensity', 0.3))
    intensity_bias = tstate.get('intensity_bias', 100)
    lat_bias = tstate.get('lateral_size_bias', 50)
    wind_mode = tstate.get('wind_mode', 'live')
    manual_dir = tstate.get('manual_wind_dir', 0)
    manual_spd = tstate.get('manual_wind_spd', 0)
    type_enabled = tstate.get('type_enabled', {k: True for k in _TURB_TYPES_ORDER})
    type_biases = tstate.get('type_biases', {k: 100 for k in _TURB_TYPES_ORDER})
    active_kind = tstate.get('active_kind', 'none')
    # What's injected (biases and MSFS factor applied); before 1.16.0 only
    # the computed intensity
    active_intensity = tstate.get('effective_intensity', tstate.get('active_intensity', 0.0))
    active_reason = tstate.get('active_reason', '')
    sources = tstate.get('sources', [])
    msfs_active = tstate.get('msfs_active', False)
    msfs_in_cloud = tstate.get('msfs_in_cloud')
    msfs_cloud_density = tstate.get('msfs_cloud_density')
    msfs_wind_vert = tstate.get('msfs_wind_vert')
    msfs_precip_state = tstate.get('msfs_precip_state')
    msfs_turb_factor = tstate.get('msfs_turb_factor', 1.0)
    msfs_turb_magnitude = tstate.get('msfs_turb_magnitude', 100)

    age_str = 'never' if math.isinf(age_s) else f'{int(age_s)}s ago'

    def _bias_input(field, current):
        return (
            f'<form method="post" action="/api/weather/turbulence" style="display:inline-flex;'
            f'gap:0.4em;align-items:center">'
            f'<input type="number" name="{field}" value="{current}" min="0" max="999"'
            f' style="width:4.5em">'
            f'<button class="btn btn-gray btn-sm">Set</button></form>'
        )

    def _toggle_btn(field, toggle_val, label, color):
        return (
            f'<form method="post" action="/api/weather/turbulence" style="display:inline">'
            f'<input type="hidden" name="{field}" value="{toggle_val}">'
            f'<button class="btn btn-{color} btn-sm">{label}</button></form>'
        )

    low_speed = tstate.get('low_speed', False)
    intensity_pct = int(active_intensity * 100)
    intensity_color = '#22c55e' if active_intensity < 0.25 else (
        '#f59e0b' if active_intensity < 0.5 else '#ef4444')
    if not enabled:
        on_off_label, on_off_color = 'OFF', 'ef4444'
    elif low_speed:
        on_off_label, on_off_color = 'SUPPRESSED', 'f59e0b'
    else:
        on_off_label, on_off_color = 'ON', '22c55e'

    status_html = (
        '<div class="card">'
        f'<div style="display:flex;align-items:center;gap:1em;flex-wrap:wrap">'
        f'<div><span style="color:#94a3b8;font-size:0.85em">Status</span><br>'
        f'<b style="color:#{on_off_color}">{on_off_label}</b></div>'
        f'<div><span style="color:#94a3b8;font-size:0.85em">Active type</span><br>'
        f'<b>{_turb_kind_label(active_kind)}</b></div>'
        f'<div><span style="color:#94a3b8;font-size:0.85em">Intensity</span><br>'
        f'<b style="color:{intensity_color}">{intensity_pct}%</b></div>'
        f'<div style="flex:1"><span style="color:#94a3b8;font-size:0.85em">Reason</span><br>'
        f'<span style="font-size:0.85em">{active_reason or "—"}</span></div>'
        f'<div style="margin-left:auto">'
    ) + _toggle_btn('enabled', 'false' if enabled else 'true',
                    'Disable' if enabled else 'Enable',
                    'red' if enabled else 'green') + (
        '</div></div>'
    )

    if sources:
        status_html += '<div style="margin-top:0.5em;font-size:0.85em;color:#94a3b8">Sources:</div>'
        for src in sources:
            src_pct = int(src['intensity'] * 100)
            is_active = src['kind'] == active_kind
            marker = '▶ ' if is_active else '   '
            status_html += (
                f'<div style="font-size:0.8em;margin-left:1em">'
                f'{marker}<b>{_turb_kind_label(src["kind"])}</b>'
                f' {src_pct}% — {src["reason"]}</div>'
            )
    if low_speed and enabled:
        status_html += (
            '<div style="font-size:0.82em;color:#f59e0b;margin-top:0.5em">'
            'Below 30 kt — turbulence computed but not injected. '
            'Values shown are what would be active in flight.</div>'
        )
    status_html += (
        f'<div style="font-size:0.75em;color:#64748b;margin-top:0.5em">Data: {age_str}</div>'
    )
    status_html += '</div>\n'

    manual_pct = int(manual_turb_intensity * 100)
    kind_opts = ''.join(
        f'<option value="{k}"{" selected" if k == manual_turb_kind else ""}>'
        f'{_TURB_KIND_LABELS.get(k, k)}</option>'
        for k in _TURB_TYPES_ORDER
    )
    manual_active_note = (
        '<div style="background:#2a1f00;border:1px solid #f59e0b;border-radius:4px;'
        'padding:0.4em 0.7em;font-size:0.85em;color:#f59e0b;margin-bottom:0.75em">'
        'Manual mode is <b>active</b> — the type and intensity below are injected directly, '
        'overriding all automatic sources (terrain, CB, PIREP, CAPE, SIGMET). '
        'Global controls and turbulence type settings below have no effect.'
        '</div>'
    ) if manual_turb_enabled else ''
    manual_html = (
        '<div class="card">'
        '<h3 style="margin:0 0 0.5em">Manual turbulence override</h3>'
        '<p style="font-size:0.85em;color:#94a3b8;margin:0 0 0.75em">When enabled, '
        'directly injects one turbulence type at a fixed intensity, '
        'bypassing the automatic engine entirely (only while turbulence is ON).</p>'
        f'{manual_active_note}'
        f'<div style="margin-bottom:0.75em">'
    ) + _toggle_btn(
        'manual_turb_enabled',
        'false' if manual_turb_enabled else 'true',
        'Disable manual override' if manual_turb_enabled else 'Enable manual override',
        'red' if manual_turb_enabled else 'blue',
    ) + (
        '</div>'
        '<table style="border-collapse:collapse;width:auto">'
        '<tr><td style="padding:4px 8px;color:#94a3b8">Type</td>'
        '<td style="padding:4px 8px">'
        '<form method="post" action="/api/weather/turbulence"'
        ' style="display:inline-flex;gap:0.4em;align-items:center">'
        f'<select name="manual_turb_kind">{kind_opts}</select>'
        '<button class="btn btn-gray btn-sm">Set</button></form>'
        '</td></tr>'
        '<tr><td style="padding:4px 8px;color:#94a3b8">Intensity (0–100%)</td>'
        '<td style="padding:4px 8px">'
        '<form method="post" action="/api/weather/turbulence"'
        ' style="display:inline-flex;gap:0.4em;align-items:center">'
        f'<input type="number" name="manual_turb_intensity_pct" value="{manual_pct}"'
        ' min="0" max="100" style="width:4.5em">'
        '<button class="btn btn-gray btn-sm">Set</button></form>'
        '</td></tr>'
        '</table>'
        '</div>\n'
    )

    _th = 'padding:4px 8px;color:#94a3b8;vertical-align:top'
    _td = 'padding:4px 8px'
    _note = 'font-size:0.8em;color:#64748b'
    global_html = (
        '<div class="card">'
        '<h3 style="margin:0 0 0.5em">Global controls</h3>'
        '<p style="font-size:0.85em;color:#94a3b8;margin:0 0 0.75em">'
        'These settings scale and tune the automatic engine. '
        'They apply on top of the per-type settings below and are not used in manual override mode.'
        '</p>'
        '<table style="border-collapse:collapse;width:100%">'
        f'<tr><td style="{_th}">Intensity bias (%)</td>'
        f'<td style="{_td}">{_bias_input("intensity_bias", intensity_bias)}'
        f'<div style="{_note}">Scales all computed turbulence intensity up or down. '
        '100 = no change, 50 = half intensity, 200 = double.</div>'
        '</td></tr>'
        f'<tr><td style="{_th}">CB lateral size bias (%)</td>'
        f'<td style="{_td}">{_bias_input("lateral_size_bias", lat_bias)}'
        f'<div style="{_note}">Controls how quickly CB turbulence fades with lateral '
        'distance from the CB. Higher = CB influence extends further sideways.</div>'
        '</td></tr>'
        f'<tr><td style="{_th}">MSFS influence magnitude (%)</td>'
        f'<td style="{_td}">'
        f'<form method="post" action="/api/weather/turbulence" style="display:inline-flex;'
        f'gap:0.4em;align-items:center">'
        f'<input type="number" name="msfs_turb_magnitude" value="{msfs_turb_magnitude}"'
        f' min="0" max="200" style="width:4.5em">'
        f'<button class="btn btn-gray btn-sm">Set</button></form>'
        f'<div style="{_note}">How much being in MSFS cloud boosts the computed intensity '
        '(at most ×1.3 at 100%, ×1.6 at 200%). 0 = ignore MSFS data.</div>'
        '</td></tr>'
        '</table>'
        '</div>\n'
    )

    def _fmt_opt(v, fmt='{:.1f}'):
        return fmt.format(v) if v is not None else '—'

    def _precip_label(state):
        if state is None:
            return '—'
        if state & 4:
            return 'Rain'
        if state & 8:
            return 'Snow'
        return 'None'

    msfs_status_color = '#22c55e' if msfs_active else '#64748b'
    msfs_status_label = 'Active' if msfs_active else 'No data'
    in_cloud_label = ('Yes' if msfs_in_cloud else 'No') if msfs_in_cloud is not None else '—'
    factor_color = '#f59e0b' if abs(msfs_turb_factor - 1.0) > 0.05 else '#94a3b8'
    msfs_html = (
        '<div class="card">'
        '<h3 style="margin:0 0 0.5em">MSFS bridge</h3>'
        '<p style="font-size:0.85em;color:#94a3b8;margin:0 0 0.75em">'
        'Real-time data read from MSFS via PSX.NET.MSFS.Client. '
        'Used to boost turbulence intensity a little (at most ×1.3) when the aircraft is '
        'inside MSFS cloud, more with a higher cloud density or vertical wind. '
        'The Turb factor shows the current multiplier being applied.</p>'
        '<div style="display:flex;gap:1.5em;flex-wrap:wrap;font-size:0.9em">'
        f'<div><span style="color:#94a3b8">Status</span><br>'
        f'<b style="color:{msfs_status_color}">{msfs_status_label}</b></div>'
        f'<div><span style="color:#94a3b8">In cloud</span><br><b>{in_cloud_label}</b></div>'
        f'<div><span style="color:#94a3b8">Cloud density</span><br>'
        f'<b>{_fmt_opt(msfs_cloud_density)}</b></div>'
        f'<div><span style="color:#94a3b8">Vert wind</span><br>'
        f'<b>{_fmt_opt(msfs_wind_vert)} kt</b></div>'
        f'<div><span style="color:#94a3b8">Precip type</span><br>'
        f'<b>{_precip_label(msfs_precip_state)}</b></div>'
        f'<div><span style="color:#94a3b8">Turb factor</span><br>'
        f'<b style="color:{factor_color}">×{msfs_turb_factor:.2f}</b></div>'
        '</div>'
        '</div>\n'
    )

    wind_mode_btns = ''
    for mode in ('live', 'psx', 'manual'):
        active = wind_mode == mode
        color = 'blue' if active else 'gray'
        wind_mode_btns += (
            f'<form method="post" action="/api/weather/turbulence" style="display:inline">'
            f'<input type="hidden" name="wind_mode" value="{mode}">'
            f'<button class="btn btn-{color} btn-sm">{mode.title()}</button></form> '
        )
    manual_row = ''
    if wind_mode == 'manual':
        manual_row = (
            '<form method="post" action="/api/weather/turbulence" '
            'style="margin-top:0.5em;display:flex;gap:0.5em;align-items:center">'
            f'<label>Dir (°): <input type="number" name="manual_wind_dir" '
            f'value="{manual_dir}" min="0" max="359" style="width:4em"></label>'
            f'<label>Speed (kt): <input type="number" name="manual_wind_spd" '
            f'value="{manual_spd}" min="0" max="300" style="width:4em"></label>'
            '<button class="btn btn-gray btn-sm">Set</button>'
            '</form>'
        )
    wind_html = (
        '<div class="card">'
        '<h3 style="margin:0 0 0.75em">Wind source</h3>'
    ) + wind_mode_btns + manual_row + '</div>\n'

    type_rows = ''
    for kind in _TURB_TYPES_ORDER:
        is_on = type_enabled.get(kind, True)
        bias = type_biases.get(kind, 100)
        toggle_label = 'ON' if is_on else 'OFF'
        toggle_color = 'green' if is_on else 'red'
        type_rows += (
            f'<tr>'
            f'<td>{_TURB_KIND_LABELS.get(kind, kind)}</td>'
            f'<td>'
            f'<form method="post" action="/api/weather/turbulence" style="display:inline">'
            f'<input type="hidden" name="type_toggle" value="{kind}">'
            f'<button class="btn btn-{toggle_color} btn-sm">{toggle_label}</button></form>'
            f'</td>'
            f'<td>'
            f'<form method="post" action="/api/weather/turbulence"'
            f' style="display:inline-flex;gap:0.4em;align-items:center">'
            f'<input type="number" name="type_bias_value" value="{bias}"'
            f' min="0" max="999" style="width:4.5em">'
            f'<input type="hidden" name="type_bias_kind" value="{kind}">'
            f'<button class="btn btn-gray btn-sm">Set</button></form>'
            f'</td>'
            f'</tr>'
        )
    types_html = (
        '<div class="card">'
        '<h3 style="margin:0 0 0.5em">Turbulence types</h3>'
        '<p style="font-size:0.85em;color:#94a3b8;margin:0 0 0.75em">'
        'Enable or disable each automatically generated turbulence type, and adjust its '
        'intensity with a bias value. 100 = normal intensity, 0 = disabled, up to 999 = '
        'roughly 10× amplification. The highest-intensity active type is what gets injected '
        'into PSX. Has no effect when manual override is active.'
        '</p>'
        '<table class="turb-types">'
        '<thead><tr><th>Type</th><th>Enable</th><th>Bias (0–999)</th></tr></thead>'
        '<tbody>' + type_rows + '</tbody>'
        '</table>'
        '</div>\n'
    )

    intro = (
        '<p class="note">FrankenWeather adds <b>extra turbulence</b> on top of what PSX already '
        'provides based on the weather in the active weather zone. Sources include mountain wave '
        'and lee-rotor turbulence from terrain and wind, CB proximity from convective weather '
        'in the zone, wind shear from jet streams, and turbulence from SIGMET areas. '
        'Disabling extra turbulence does not affect the weather zone weather itself.</p>\n'
    )
    body = intro + stale_banner + status_html + _turb_events_html(tstate) + manual_html
    body += global_html
    body += msfs_html + wind_html + types_html
    return _page(body)


def _fmt_relative_epoch(now: float, epoch) -> str:
    """Format an epoch relative to now as e.g. '12m ago' / 'in 34m', or 'never'/'—'."""
    if not epoch:
        return "never"
    delta = epoch - now
    mins = abs(delta) // 60
    secs = abs(delta) % 60
    span = f'{int(mins)}m{int(secs):02d}s' if mins else f'{int(secs)}s'
    return f'{span} ago' if delta <= 0 else f'in {span}'


_SIGMET_TYPE_ORDER = fw_sigmet.HAZARDS + (fw_sigmet.UNKNOWN,)


def _sigmet_label(entry: dict) -> str:
    """Return a short human-readable SIGMET name, e.g. 'KZWY CHARLIE 23 FRQ'."""
    label = f"{entry.get('fir', '?')} {entry.get('series', '?')}"
    if entry.get('qualifier'):
        label += f" {entry['qualifier']}"
    return label


def _sigmet_level(feet, zero_label: str = '—') -> str:
    """Format a SIGMET base/top in feet as e.g. 'FL360', or zero_label for 0 ('SFC' for a base)."""
    if feet is None:
        return '?'
    if feet == 0:
        return zero_label
    return f"FL{feet // 100:03.0f}"


_SIGMET_NEARBY_NM = 320.0  # SIGMETs page: also list every area at least this close


def _sigmet_row(hazard: str, entry: dict, dist: float, brg, zones: list) -> str:
    """Return one <tr> of the SIGMETs page's 'nearest and nearby' table."""
    inside = dist == 0.0
    where = '<b>INSIDE</b>' if inside else f'{dist:.0f} nm / {brg:03.0f}°'
    levels = (f"{_sigmet_level(entry.get('base_ft'), 'SFC')} – "
              f"{_sigmet_level(entry.get('top_ft'))}")
    zone_cell = ''
    if hazard in fw_sigmet.CB_HAZARDS:
        inside_zones = [
            f"Wx{z['zone']}" for z in zones
            if z.get('lat') is not None and any(
                fw_sigmet.polygon_distance(z['lat'], z['lon'], poly)[0] == 0.0
                for poly in entry['polygons'])]
        zone_cell = ', '.join(inside_zones) or 'none'
    style = ' style="background:#3b1d1d"' if inside else ''
    valid_to = (time.strftime("%H%MZ", time.gmtime(entry["valid_to"]))
                if entry.get("valid_to") else "—")
    return (f'<tr{style}><td>{hazard}</td>'
            f'<td>{_esc(_sigmet_label(entry))}</td>'
            f'<td>{where}</td><td>{levels}</td>'
            f'<td>{valid_to}</td>'
            f'<td>{zone_cell}</td></tr>')


def _sigmet_nearest_rows(entries: list, ac_lat, ac_lon, zones: list) -> str:
    """Return the rows of the SIGMETs page's 'nearest and nearby' table.

    Per type: the nearest area, plus every other area whose nearest point
    is less than _SIGMET_NEARBY_NM away, nearest first.
    """
    rows = ''
    for hazard in fw_sigmet.HAZARDS:
        usable = [e for e in entries if e['ok'] and e['hazard'] == hazard]
        if not usable:
            rows += f'<tr><td>{hazard}</td><td colspan="5" style="color:#64748b">none</td></tr>'
            continue
        if ac_lat is None:
            rows += (f'<tr><td>{hazard}</td><td colspan="5" style="color:#64748b">'
                     f'{len(usable)} active, aircraft position unknown</td></tr>')
            continue
        ranked = sorted(((*fw_sigmet.entry_distance(ac_lat, ac_lon, e), e) for e in usable),
                        key=lambda d: d[0])
        for i, (dist, brg, entry) in enumerate(ranked):
            if i > 0 and dist >= _SIGMET_NEARBY_NM:
                break
            rows += _sigmet_row(hazard, entry, dist, brg, zones)
    return rows


_SIGMET_HANDLE_TYPES = {
    'TS': ('Thunderstorms (TS)',
           'frankenweather places weather zones in these areas and gives them CBs'),
    'TC': ('Tropical cyclones (TC)',
           'frankenweather places a cluster of weather zones with CBs around the cyclone'),
    'TURB': ('Turbulence (TURB)', 'frankenweather generates the turbulence itself'),
    'MTW': ('Mountain wave (MTW)',
            'frankenweather strengthens its own terrain mountain wave in these areas'),
}


def _om_usage_row(state: dict) -> str:
    """Return the status card's Open-Meteo usage row ('' for frankenweather before 1.14.0)."""
    usage = state.get('om_usage')
    if not usage:
        return ''
    text = (f"{usage['last_hour']:,.0f} calls last hour, {usage['last_24h']:,.0f} last 24 h "
            "(limit 10,000 a day)")
    if state.get('om_backoff_s'):
        text += f"; refused or failing, waiting {state['om_backoff_s']} s"
    elif state.get('last_om_fetch_failed'):
        text += "; last zone fetch failed"
    title = ', '.join(f"{k} {v:,.0f}" for k, v in usage.get('by_kind_24h', {}).items())
    return (f'<tr><td>Open-Meteo</td><td class="val" title="last 24 h: {_esc(title)}">'
            f'{text}</td></tr>\n')


def _cb_airport_mode_html(cfg: dict) -> str:
    """Return the Weather zones page's CB near dep/arr buttons (Off/Move zone/Remove CB)."""
    current, has_modes = cb_airport_mode(cfg)
    buttons = ''
    for mode, label, _hint in CB_AIRPORT_MODES:
        if mode == 'remove' and not has_modes:
            continue
        active = mode == current
        style = ('background:#2563eb;color:#fff;cursor:default' if active
                 else 'background:#1c2033;color:#94a3b8;border:1px solid #2a2f45')
        buttons += (
            '<form action="/api/weather/cb-airport-mode" method="post" style="margin:0">'
            f'<input type="hidden" name="mode" value="{mode}">'
            f'<button type="submit" class="btn btn-sm" style="margin:0;width:auto;{style}"'
            f'{" disabled" if active else ""}>{label}</button></form>')
    hints = '; '.join(f'<b>{label}</b>: {hint}' for mode, label, hint in CB_AIRPORT_MODES
                      if mode != 'remove' or has_modes)
    return (
        '<div class="card"><div style="display:flex;gap:0;align-items:center;flex-wrap:wrap">'
        '<b style="color:#e2e8f0;margin-right:0.8em">CB near dep/arr</b>'
        f'{buttons}</div>'
        f'<p class="note" style="margin:0.5em 0 0">{hints}.</p></div>\n')


def _sigmet_preset_html(current) -> str:
    """Return the SIGMETs page's preset buttons (Off/Normal/PSX, as on the router's /efb).

    current is the preset the settings match (fw_sigmet.current_preset()),
    or None when the types below are a custom choice.
    """
    buttons = ''
    for preset, label, _hint in fw_sigmet.PRESETS:
        active = preset == current
        style = ('background:#2563eb;color:#fff;cursor:default' if active
                 else 'background:#1c2033;color:#94a3b8;border:1px solid #2a2f45')
        buttons += (
            '<form action="/api/weather/sigmets/preset" method="post" style="margin:0">'
            f'<input type="hidden" name="preset" value="{preset}">'
            f'<button type="submit" class="btn btn-sm" style="margin:0;width:auto;{style}"'
            f'{" disabled" if active else ""}>{label}</button></form>')
    hints = '; '.join(f'<b>{label}</b>: {hint}' for _p, label, hint in fw_sigmet.PRESETS)
    custom = '' if current else ' The current choice below is custom.'
    return (
        '<div class="card"><div style="display:flex;gap:0;align-items:center">'
        '<b style="color:#e2e8f0;margin-right:0.8em">Preset</b>'
        f'{buttons}</div>'
        f'<p class="note" style="margin:0.5em 0 0">{hints}.{custom}</p></div>\n')


def _sigmet_handle_html(handle: dict) -> str:
    """Return the SIGMETs page's 'who handles which type' choice and Download now button.

    One row per type with two buttons, "frankenweather" and "PSX", the
    current choice highlighted, and a line saying what that choice means.
    """
    rows = ''
    for hazard in fw_sigmet.OWN_HAZARDS:
        name, fw_does = _SIGMET_HANDLE_TYPES[hazard]
        by_fw = bool(handle.get(hazard, True))
        buttons = ''
        for value, label, active in (('1', 'frankenweather', by_fw), ('0', 'PSX', not by_fw)):
            style = ('background:#2563eb;color:#fff;cursor:default' if active
                     else 'background:#1c2033;color:#94a3b8;border:1px solid #2a2f45')
            buttons += (
                '<form action="/api/weather/sigmets/handle" method="post" style="margin:0">'
                f'<input type="hidden" name="hazard" value="{hazard}">'
                f'<input type="hidden" name="enabled" value="{value}">'
                f'<button type="submit" class="btn btn-sm" style="margin:0;width:auto;{style}"'
                f'{" disabled" if active else ""}>{label}</button></form>')
        meaning = (f'{fw_does}. Not sent to PSX.' if by_fw else
                   'Sent to PSX, which simulates these itself.')
        rows += (
            '<tr><td style="width:auto;white-space:nowrap">'
            f'<b style="color:#e2e8f0">{name}</b></td>'
            f'<td style="white-space:nowrap"><div style="display:flex;gap:0">{buttons}</div></td>'
            f'<td style="color:#94a3b8;font-size:0.85em">{meaning}</td></tr>')
    return (
        '<p class="note">frankenweather downloads SIGMETs from aviationweather.gov itself '
        'instead of letting PSX do it. For each type below, choose who handles it: '
        'frankenweather (PSX doesn\'t get these SIGMETs) or PSX (they are passed on to PSX). '
        'Volcanic ash (VA) and icing (ICE) always go to PSX, unless the preset is Off. '
        'Save the choice to the config file on the <a href="/weather/config">Config '
        'page</a>.</p>\n'
        '<div class="card"><table class="sg"><tr><th>Type</th><th>Handled by</th>'
        f'<th>What that means</th></tr>{rows}</table></div>\n')


def _build_weather_sigmets_page(ctx):  # pylint: disable=too-many-locals
    """Render the /weather/sigmets page: frankenweather's own downloaded SIGMETs (--sigmet)."""
    color_scheme = ctx.color_scheme
    sigstate = ctx.fw_sigmetstate
    now = time.time()
    nav_html = _weather_nav(ctx, 'sigmets')
    extra_head = (
        '\n<style>body { max-width: 64em; }'
        'table.sg { border-collapse:collapse;width:100%;font-size:0.88em; }'
        'table.sg th { text-align:left;color:#64748b;font-size:0.8em;'
        'padding:2px 6px;border-bottom:1px solid #2a2f45; }'
        'table.sg td { width:auto;padding:2px 6px;border-bottom:1px solid #1c2033;'
        'color:#e2e8f0;font-size:1em; }'
        'pre.sg-raw { max-height:30em;overflow:auto;background:#111827;padding:0.75em;'
        'border-radius:0.45em;font-size:0.8em;white-space:pre-wrap; }'
        'details summary { cursor:pointer;color:#94a3b8; }'
        '</style>\n'
    )
    state = ctx.fw_state or {}
    config = state.get('config') or {}
    # The SIGMET data carries its own copy of the settings: on frankenweather's
    # own web UI it's there before the PSX connection is up (unlike fw_state).
    if sigstate:
        config = {**config, 'sigmet_enabled': sigstate.get('enabled'),
                  'sigmet_handle': {h: h in sigstate.get('handled', [])
                                    for h in fw_sigmet.OWN_HAZARDS},
                  'sigmet_own': sigstate.get('own', config.get('sigmet_own', True)),
                  'disable_psx_sigmets': sigstate.get(
                      'psx_off', config.get('disable_psx_sigmets', False))}
    elif not state:
        body = ('<div class="card warn"><p style="margin:0">No data from frankenweather '
                'yet.</p></div>\n')
        return _page_shell(color_scheme, 'SIGMETs', nav_html, body, extra_head)
    if not config.get('sigmet_enabled'):
        body = ('<div class="card warn"><p style="margin:0">frankenweather\'s SIGMET system is '
                'off. Start frankenweather with <code>--sigmet</code> to use it.</p></div>\n')
        return _page_shell(color_scheme, 'SIGMETs', nav_html, body, extra_head)
    handle = config.get('sigmet_handle') or {}
    own = bool(config.get('sigmet_own', True))
    psx_off = bool(config.get('disable_psx_sigmets'))
    toggle_html = _sigmet_preset_html(fw_sigmet.current_preset(
        own, psx_off, {h for h in fw_sigmet.OWN_HAZARDS if handle.get(h, True)}))
    if own:
        toggle_html += _sigmet_handle_html(handle)
    else:
        toggle_html += (
            '<div class="card warn"><p style="margin:0">frankenweather\'s SIGMET system is '
            'switched off: ' + (
                '<b>no SIGMETs are used at all</b>, neither by frankenweather nor by PSX.'
                if psx_off else
                'PSX handles every SIGMET itself, and frankenweather only asks it to '
                'download fresh ones regularly.') +
            ' Choose Normal above to switch it back on. The SIGMETs below are from '
            'frankenweather\'s last own download and may be old.</p></div>\n')
    if not sigstate:
        body = toggle_html + ('<div class="card warn"><p style="margin:0">No SIGMET data from '
                              'frankenweather yet.</p></div>\n')
        return _page_shell(color_scheme, 'SIGMETs', nav_html, body, extra_head)

    entries = sigstate.get('entries') or []
    ac_lat, ac_lon = state.get('ac_lat'), state.get('ac_lon')
    error = sigstate.get('error')
    body = toggle_html + (
        f'<div class="card {"warn" if error else "ok"}">'
        '<table><tr><td>Source</td>'
        f'<td class="val">{_esc(sigstate.get("source") or "?")}</td></tr>'
        f'<tr><td>Last download</td><td class="val">'
        f'{_fmt_relative_epoch(now, sigstate.get("fetched_at"))}</td></tr>'
        f'<tr><td>Next automatic download</td><td class="val">'
        f'{_fmt_relative_epoch(now, sigstate.get("next_at"))} '
        f'<span style="color:#94a3b8;font-weight:400">(every '
        f'{(sigstate.get("interval_s") or 600) / 60:.0f} minutes)</span></td></tr>'
        f'<tr><td>SIGMETs</td><td class="val">{len(entries)}</td></tr>'
    )
    if error:
        body += f'<tr><td>Last error</td><td class="warn">{_esc(error)}</td></tr>'
    body += ('</table>'
             '<form action="/api/weather/sigmets/refresh" method="post" style="margin:0.5em 0 0">'
             '<button type="submit" class="btn btn-gray btn-sm" style="width:auto">'
             'Download SIGMETs now</button>'
             '<span style="color:#94a3b8;font-size:0.8em;margin-left:0.6em">fetches a fresh '
             'set from aviationweather.gov right away, instead of waiting for the next '
             'automatic download</span></form></div>\n')

    counts = {h: [0, 0] for h in _SIGMET_TYPE_ORDER}
    for e in entries:
        counts.setdefault(e['hazard'], [0, 0])[0 if e['ok'] else 1] += 1
    body += ('<h2>By type</h2><div class="card"><table class="sg">'
             '<tr><th>Type</th><th>Parsed</th><th>Could not parse</th></tr>')
    for hazard, (good, bad) in counts.items():
        bad_cell = f'<b style="color:#f87171">{bad}</b>' if bad else '0'
        body += f'<tr><td>{hazard}</td><td>{good}</td><td>{bad_cell}</td></tr>'
    body += '</table></div>\n'

    body += ('<h2>Nearest and nearby</h2><div class="card"><table class="sg">'
             '<tr><th>Type</th><th>SIGMET</th><th>Distance / bearing</th><th>Base – top</th>'
             '<th>Valid to</th><th>Weather zones inside</th></tr>')
    body += _sigmet_nearest_rows(entries, ac_lat, ac_lon, state.get('zones') or [])
    body += ('</table><p style="color:#64748b;font-size:0.8em;margin:0.5em 0 0">'
             'For each type: the nearest area, and every other area less than '
             f'{_SIGMET_NEARBY_NM:.0f} nm away. Distance is to the nearest edge of the '
             'area. Weather zones are only listed for TS and TC.</p></div>\n')

    bad = [e for e in entries if not e['ok']]
    if bad:
        body += f'<h2>Could not parse ({len(bad)})</h2><div class="card">'
        for e in bad:
            body += (f'<details><summary>{_esc(e["raw_hazard"] or "?")} '
                     f'{_esc(_sigmet_label(e))}: {_esc(e["reason"] or "")}</summary>'
                     f'<pre class="sg-raw">{_esc(e["raw"])}</pre></details>')
        body += '</div>\n'

    raw = fw_sigmet.sigmet_text(entries, sigstate.get('fetched_at') or now)
    body += ('<h2>Raw SIGMET text</h2><div class="card">'
             '<a href="/weather/sigmets/raw.txt" download="sigmets.txt">Download as text file</a>'
             ' &middot; <a href="/weather/sigmets/psx.txt" download="sigmets-psx.txt">'
             'Download the text given to PSX</a>'
             f'<pre class="sg-raw">{_esc(raw)}</pre></div>\n')
    return _page_shell(color_scheme, 'SIGMETs', nav_html, body, extra_head)


def _build_weather_enroute_wind_page(ctx):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Render the /weather/enroute-wind page: flight-plan vs. Open-Meteo per-waypoint wind."""
    color_scheme = ctx.color_scheme
    windstate = ctx.fw_windstate
    now = time.time()

    def _page(body):
        extra_head = (
            '\n<style>body { max-width: 64em; }'
            'table.ew-table { border-collapse:collapse;width:100%;font-size:0.88em; }'
            'table.ew-table th { text-align:left;color:#64748b;font-size:0.8em;'
            'padding:2px 6px;border-bottom:1px solid #2a2f45; }'
            'table.ew-table td { padding:2px 6px;border-bottom:1px solid #1c2033; }'
            'tr.ew-passed td { color:#64748b; }'
            'td.ew-diff-hi { color:#f59e0b;font-weight:600; }'
            '</style>\n'
        )
        return _page_shell(color_scheme, 'Enroute wind', _weather_nav(ctx, 'wind'), body,
                           extra_head)

    enabled = bool(windstate and windstate.get("enabled"))
    toggle_label = "Disable" if enabled else "Enable"
    toggle_class = "btn-red" if enabled else "btn-green"
    body = (
        '<p class="note">Periodically fetches Open-Meteo pressure-level wind for each '
        "upcoming route waypoint and refreshes PSX's enroute wind corridor, so cruise winds "
        'keep drifting with reality even if the crew never requests a new datalink wind '
        'uplink. On by default.</p>\n'
        '<div class="card" style="display:flex;align-items:center;gap:1.5em;flex-wrap:wrap">'
        f'<div><span style="color:#94a3b8;font-size:0.85em">Importer</span><br>'
        f'<b style="color:{"#4ade80" if enabled else "#64748b"}">'
        f'{"ENABLED" if enabled else "disabled"}</b></div>'
        f'<form action="/api/weather/enroute-wind/toggle" method="post">'
        f'<input type="hidden" name="enabled" value="{0 if enabled else 1}">'
        f'<button type="submit" class="btn {toggle_class} btn-sm">{toggle_label}</button>'
        f'</form>'
    )
    if windstate:
        last_str = _fmt_relative_epoch(now, windstate.get("last_fetch_epoch"))
        next_str = _fmt_relative_epoch(now, windstate.get("next_fetch_epoch"))
        body += (
            f'<div><span style="color:#94a3b8;font-size:0.85em">Last fetch</span><br>'
            f'<b>{last_str}</b></div>'
            f'<div><span style="color:#94a3b8;font-size:0.85em">Next fetch</span><br>'
            f'<b>{next_str}</b></div>'
        )
    body += '</div>\n'

    deviation = (windstate or {}).get("deviation", 30)
    body += (
        '<div class="card">'
        '<div style="display:flex;align-items:center;gap:1em;flex-wrap:wrap">'
        '<div style="flex:1;min-width:14em">'
        '<span style="color:#94a3b8;font-size:0.85em">Random wind/OAT deviation (Qs497)</span><br>'
        '<span style="font-size:0.8em;color:#64748b">Simulates forecast inaccuracy PSX applies '
        'on top of the corridor data — 0 none, 10% subtle, 80% large.</span>'
        '</div>'
        '<form action="/api/weather/enroute-wind/deviation" method="post" '
        'style="display:flex;align-items:center;gap:0.75em">'
        f'<input type="range" name="deviation" min="0" max="80" step="10" value="{deviation}" '
        'oninput="document.getElementById(\'ew-dev-label\').textContent = this.value + \'%\'">'
        f'<b id="ew-dev-label" style="min-width:3em;text-align:right">{deviation}%</b>'
        '<button type="submit" class="btn btn-blue btn-sm">Set</button>'
        '</form>'
        '</div>'
        '</div>\n'
    )

    if not windstate or not windstate.get("waypoints"):
        body += (
            '<div class="card warn"><p style="margin:0">'
            'No route waypoints yet — load a route into the FMC to begin.</p></div>\n')
        return _page(body)

    snapshot_unparseable = windstate.get("has_snapshot") and not windstate.get("snapshot_parseable")
    if snapshot_unparseable:
        raw_lines = (windstate.get("snapshot_raw") or "").replace('^', '\n')
        body += (
            '<div class="card warn">'
            '<p><b>Flight plan wind corridor could not be parsed</b> '
            '(unsupported format, or empty) — no per-waypoint comparison is available, '
            'but the downloaded Open-Meteo wind below is still shown. '
            'Raw flight-plan wind corridor:</p>'
            f'<pre style="white-space:pre-wrap;font-size:0.85em;color:#94a3b8;margin:0">'
            f'{raw_lines}</pre>'
            '</div>\n')

    if not windstate.get("has_snapshot"):
        body += (
            '<div class="card"><p style="margin:0">'
            'No flight-plan wind corridor snapshot captured yet — this is captured '
            'automatically the first time PSX loads wind data that FrankenWeather did not '
            'write itself (e.g. a situ load or route import).</p></div>\n')

    table = (
        '<table class="ew-table">\n'
        '<thead><tr><th>Waypoint</th><th>FL</th><th>Flight plan</th>'
        '<th>Open-Meteo</th><th>Diff</th></tr></thead>\n<tbody>\n'
    )
    for wp in windstate["waypoints"]:
        row_class = ' class="ew-passed"' if wp["passed"] else ''
        levels = wp["levels"] or [None]
        matched_note = ''
        if wp.get("matched_name"):
            matched_note = f' <span style="color:#64748b">(~{wp["matched_name"]})</span>'
        for j, lvl in enumerate(levels):
            if j == 0:
                name_cell = wp["name"] + matched_note + (' (passed)' if wp["passed"] else '')
            else:
                name_cell = ''
            if lvl is None:
                table += (
                    f'<tr{row_class}><td>{name_cell}</td><td colspan="4">'
                    'no wind data yet</td></tr>\n')
                continue
            fp = lvl["flightplan"]
            om = lvl["openmeteo"]
            diff = lvl["diff"]
            fp_str = (f'{fp["dir_deg"]:03.0f}°/{fp["spd_kt"]:.0f}kt {fp["oat_c"]:+.0f}°C'
                      if fp else '—')
            om_str = (f'{om["dir_deg"]:03.0f}°/{om["spd_kt"]:.0f}kt {om["oat_c"]:+.0f}°C'
                      if om else '—')
            if diff:
                diff_cls = ' class="ew-diff-hi"' if abs(diff["spd_kt"]) >= 15 else ''
                diff_str = (f'<span{diff_cls}>dir{diff["dir_deg"]:+.0f}° '
                            f'spd{diff["spd_kt"]:+.0f}kt oat{diff["oat_c"]:+.0f}°C</span>')
            else:
                diff_str = '—'
            table += (
                f'<tr{row_class}><td>{name_cell}</td><td>FL{lvl["fl_ft"] // 100:03d}</td>'
                f'<td>{fp_str}</td><td>{om_str}</td><td>{diff_str}</td></tr>\n')
    table += '</tbody>\n</table>\n'
    body += table
    return _page(body)


def _build_conflict_paused_page(ctx):
    """Render a prominent 'paused' page in place of any normal /weather page.

    Shown instead of the map/turbulence/wind/settings pages while this
    instance is paused because a second FRANKENWEATHER instance is active
    on the network (see ctx.fw_conflict_paused) — deliberately no map or
    data tables, since none of that is being updated while paused.
    """
    body = (
        '<div class="card warn">'
        '<p style="margin:0 0 0.5em;font-size:1.1em"><b>⏸ PAUSED</b></p>'
        '<p style="margin:0">Another FRANKENWEATHER instance is active on the network. '
        'This instance is doing no Open-Meteo/VATSIM downloads and sending nothing to PSX '
        '(no zone weather, no turbulence, no wind corridor) until the other instance '
        'goes away.</p>'
        '</div>\n'
    )
    return _page_shell(
        ctx.color_scheme, 'FrankenWeather', '', body,
        '<script>setTimeout(function(){location.reload();},15000);</script>\n')


# ---------------------------------------------------------------------------
# Route registration — call from both router and standalone server
# ---------------------------------------------------------------------------


def register_weather_routes(routes, ctx):  # pylint: disable=too-many-statements,too-many-locals
    """Register all /weather and /api/weather/* routes onto an aiohttp RouteTableDef."""

    def _paused_or(build_page):
        return _build_conflict_paused_page(ctx) if ctx.fw_conflict_paused else build_page(ctx)

    @routes.get('/weather')
    async def _weather_get(_):
        return web.Response(text=_paused_or(_build_weather_map_page), content_type='text/html')

    @routes.get('/weather/settings')
    async def _weather_settings_get(_):
        return web.Response(
            text=_paused_or(_build_weather_settings_page), content_type='text/html')

    @routes.get('/weather/config')
    async def _weather_config_get(_):
        return web.Response(
            text=_paused_or(_build_weather_config_page), content_type='text/html')

    @routes.get('/weather/turbulence')
    async def _weather_turb_get(_):
        return web.Response(text=_paused_or(_build_weather_turb_page), content_type='text/html')

    @routes.get('/weather/manual')
    async def _weather_manual_get(_):
        # Manual weather is now part of the Weather zones page (its Manual mode)
        raise web.HTTPFound('/weather/settings')

    @routes.get('/weather/enroute-wind')
    async def _weather_enroute_wind_get(_):
        return web.Response(
            text=_paused_or(_build_weather_enroute_wind_page), content_type='text/html')

    @routes.get('/weather/sigmets')
    async def _weather_sigmets_get(_):
        return web.Response(
            text=_paused_or(_build_weather_sigmets_page), content_type='text/html')

    @routes.get('/weather/sigmets/{which:(raw|psx)}.txt')
    async def _weather_sigmets_text(request):
        sigstate = ctx.fw_sigmetstate
        if not sigstate:
            return web.Response(text="No SIGMET data\n", status=404)
        entries = sigstate.get('entries') or []
        name = "sigmets.txt"
        if request.match_info['which'] == 'psx':
            entries = fw_sigmet.psx_entries(
                entries, set(sigstate.get('handled', fw_sigmet.OWN_HAZARDS)))
            name = "sigmets-psx.txt"
        text = fw_sigmet.sigmet_text(entries, sigstate.get('fetched_at') or time.time())
        return web.Response(text=text, content_type='text/plain', headers={
            'Content-Disposition': f'attachment; filename="{name}"'})

    @routes.post('/api/weather/sigmets/refresh')
    async def _weather_sigmets_refresh(_):
        await ctx.send_fw_settings_cmd({"sigmet_refresh": True})
        await asyncio.sleep(3)  # usually enough for the download to finish
        raise web.HTTPFound('/weather/sigmets')

    @routes.post('/api/weather/sigmets/preset')
    async def _weather_sigmets_preset(request):
        data = await request.post()
        cmd = fw_sigmet.preset_command(data.get('preset'), True)
        if cmd:
            await ctx.send_fw_settings_cmd(cmd)
        raise web.HTTPFound('/weather/sigmets')

    @routes.post('/api/weather/sigmets/handle')
    async def _weather_sigmets_handle(request):
        data = await request.post()
        hazard = data.get('hazard')
        if hazard in fw_sigmet.OWN_HAZARDS:
            await ctx.send_fw_settings_cmd(
                {"sigmet_handle": {hazard: data.get('enabled') == '1'}})
        raise web.HTTPFound('/weather/sigmets')

    @routes.post('/api/weather/enroute-wind/toggle')
    async def _weather_enroute_wind_toggle(request):
        data = await request.post()
        enabled = data.get('enabled') == '1'
        await ctx.send_fw_settings_cmd({"enroute_wind_enabled": enabled})
        raise web.HTTPFound('/weather/enroute-wind')

    @routes.post('/api/weather/cb-airport-mode')
    async def _weather_cb_airport_mode(request):
        data = await request.post()
        cmd = cb_airport_mode_command(data.get('mode'))
        if cmd:
            await ctx.send_fw_settings_cmd(cmd)
        raise web.HTTPFound('/weather/settings')

    @routes.post('/api/weather/enroute-wind/deviation')
    async def _weather_enroute_wind_deviation(request):
        data = await request.post()
        try:
            deviation = int(data.get('deviation', 30))
        except (TypeError, ValueError):
            deviation = 30
        await ctx.send_fw_settings_cmd({"enroute_wind_deviation": deviation})
        raise web.HTTPFound('/weather/enroute-wind')

    @routes.post('/api/weather/manual')
    async def _weather_manual_post(request):
        data = await request.post()
        cmd = {}
        int_fields = (
            "hi_oktas", "hi_top", "hi_base",
            "lo_oktas", "lo_top", "lo_base",
            "cb_oktas", "cb_top", "cb_base",
            "turb_severity", "turb_top", "turb_base",
            "mb_mode", "mb_chance", "mb_outflow",
            "inv_top", "inv_tmp",
            "wind_dir", "wind_spd", "wind_gust", "wind_var",
            "precip", "vis_m", "surf_temp",
        )
        for field in int_fields:
            if field in data:
                cmd[field] = int(data[field])
        if "qnh_hpa" in data:
            cmd["qnh_hpa"] = float(data["qnh_hpa"])
        cmd["inv_on"] = "inv_on" in data
        if cmd:
            await ctx.send_manualwx_cmd(cmd)
        raise web.HTTPFound('/weather/settings')

    @routes.post('/api/weather/manual/copy_zone')
    async def _weather_manual_copy(request):
        data = await request.post()
        try:
            zone_num = int(data.get('zone', '1'))
        except (ValueError, TypeError):
            zone_num = 1
        wx_str = ctx.cache_get(f'Wx{zone_num}')
        if wx_str:
            cmd = _wx_string_to_manual_params(wx_str)
            if cmd:
                await ctx.send_manualwx_cmd(cmd)
        raise web.HTTPFound('/weather/settings')

    @routes.post('/api/weather/settings')
    async def _weather_settings_post(request):
        data = await request.post()
        cmd = {}
        if 'msfs_in_cloud_sync' in data:
            cmd['msfs_in_cloud_sync'] = data['msfs_in_cloud_sync'].lower() == 'true'
        if 'msfs_qnh_check' in data:
            val = str(data['msfs_qnh_check'])
            if val in ('CHECK', 'SYNC'):
                cmd['msfs_qnh_check'] = val
        if cmd:
            await ctx.send_fw_settings_cmd(cmd)
        raise web.HTTPFound('/weather/settings')

    @routes.post('/api/weather/config')
    async def _weather_config_post(request):
        data = await request.post()
        cmd = {}
        for field in ('timesync_on_startup', 'timesync_on_situ_load', 'timesync_periodic'):
            if field in data:
                cmd[field] = data[field].lower() == 'true'
        if cmd:
            await ctx.send_fw_settings_cmd(cmd)
        raise web.HTTPFound('/weather/config')

    @routes.post('/api/weather/config/save')
    async def _weather_config_save(_):
        await ctx.send_fw_settings_cmd({"config_action": "save"})
        raise web.HTTPFound('/weather/config')

    @routes.post('/api/weather/config/load')
    async def _weather_config_load(_):
        await ctx.send_fw_settings_cmd({"config_action": "load"})
        raise web.HTTPFound('/weather/config')

    @routes.post('/api/weather/config/reset')
    async def _weather_config_reset(_):
        await ctx.send_fw_settings_cmd({"config_action": "reset"})
        raise web.HTTPFound('/weather/config')

    @routes.post('/api/weather/turbulence')
    async def _weather_turb_post(request):  # pylint: disable=too-many-branches
        data = await request.post()
        cmd = {}
        if 'enabled' in data:
            cmd['enabled'] = data['enabled'].lower() == 'true'
        if 'manual_turb_enabled' in data:
            cmd['manual_turb_enabled'] = data['manual_turb_enabled'].lower() == 'true'
        if 'manual_turb_kind' in data:
            cmd['manual_turb_kind'] = str(data['manual_turb_kind'])
        if 'manual_turb_intensity_pct' in data:
            cmd['manual_turb_intensity'] = int(data['manual_turb_intensity_pct']) / 100.0
        if 'profile' in data:
            cmd['profile'] = str(data['profile'])
        if 'intensity_bias' in data:
            cmd['intensity_bias'] = int(data['intensity_bias'])
        if 'lateral_size_bias' in data:
            cmd['lateral_size_bias'] = int(data['lateral_size_bias'])
        if 'wind_mode' in data:
            cmd['wind_mode'] = str(data['wind_mode'])
        if 'manual_wind_dir' in data:
            cmd['manual_wind_dir'] = int(data['manual_wind_dir'])
        if 'manual_wind_spd' in data:
            cmd['manual_wind_spd'] = int(data['manual_wind_spd'])
        if 'type_toggle' in data:
            kind = str(data['type_toggle'])
            tstate = ctx.fw_turbstate or {}
            current = tstate.get('type_enabled', {}).get(kind, True)
            cmd['type_enabled'] = {kind: not current}
        if 'type_bias_kind' in data and 'type_bias_value' in data:
            cmd['type_bias'] = {
                'kind': str(data['type_bias_kind']),
                'value': int(data['type_bias_value']),
            }
        if 'msfs_turb_magnitude' in data:
            cmd['msfs_turb_magnitude'] = int(data['msfs_turb_magnitude'])
        if data.get('event_preset') in _TURB_EVENT_PRESETS:
            cmd['event_mult'] = dict(zip(_TURB_EVENT_KINDS,
                                         _TURB_EVENT_PRESETS[data['event_preset']]))
        elif any(f'event_mult_{k}' in data for k in _TURB_EVENT_KINDS):
            cmd['event_mult'] = {k: float(data[f'event_mult_{k}']) for k in _TURB_EVENT_KINDS
                                 if f'event_mult_{k}' in data}
        if data.get('event_trigger') in _TURB_EVENT_KINDS:
            cmd['trigger'] = data['event_trigger']
        if cmd:
            await ctx.send_turb_cmd(cmd)
        raise web.HTTPFound('/weather/turbulence')

    @routes.post('/api/weather/mode')
    async def _weather_mode_post(request):
        data = await request.post()
        new_mode = str(data.get('mode', ''))
        if new_mode not in ('enabled', 'paused', 'disabled', 'manual'):
            return web.Response(text="Invalid mode", status=400)
        await ctx.send_mode_cmd(new_mode)
        raise web.HTTPFound('/weather/settings')


# ---------------------------------------------------------------------------
# Event log browser — standalone-only, see register_weather_logs_routes()
# ---------------------------------------------------------------------------


def _fmt_bytes(n: int) -> str:
    """Format a byte count as e.g. '1.3 MB'."""
    size = float(n)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if size < 1024 or unit == 'GB':
            return f'{size:.0f} {unit}' if unit == 'B' else f'{size:.1f} {unit}'
        size /= 1024
    return f'{size:.1f} GB'


def _list_event_logs(log_dir: str) -> list:
    """Return (name, size_bytes, mtime_epoch) for every event log, newest first."""
    try:
        names = os.listdir(log_dir)
    except OSError:
        return []
    entries = []
    for name in names:
        if not (name.startswith('frankenweather_') and name.endswith('.log')):
            continue
        path = os.path.join(log_dir, name)
        try:
            stat = os.stat(path)
        except OSError:
            continue
        entries.append((name, stat.st_size, stat.st_mtime))
    entries.sort(key=lambda e: e[2], reverse=True)
    return entries


def _turb_events_html(tstate: dict) -> str:
    """Return the turbulence page's events card: likelihoods, trigger, the last events."""
    testing = tstate.get('profile') == 'testing'
    mult = tstate.get('event_mult') or dict.fromkeys(_TURB_EVENT_KINDS, 1.0)
    active = tstate.get('event_active')
    html = (
        '<div class="card"><h3 style="margin:0 0 0.5em">Turbulence events</h3>'
        '<p style="font-size:0.85em;color:#94a3b8;margin:0 0 0.75em">'
        'Testing profile only. The continuous turbulence is at most light chop; moderate '
        'patches, severe clusters (5-30 s) and extreme encounters come as rare events, at '
        'real-world odds for the kind of area the aircraft is in. The likelihoods make a kind '
        'more (or less) likely without making it stronger; 0 turns it off. '
        'Severe and extreme events are not softened for the autopilot.</p>')
    if not testing:
        html += ('<p style="font-size:0.85em;color:#f59e0b;margin:0">The turbulence profile is '
                 f'"{_esc(tstate.get("profile", "stable"))}": no events.</p>')
    html += (
        f'<p style="font-size:0.85em;margin:0 0 0.5em">Area: '
        f'<b>{_esc(tstate.get("event_area", "clean"))}</b> '
        f'{_esc(tstate.get("event_area_reason", ""))}'
        f'{f" — <b>{_esc(active)} event in progress</b>" if active else ""}</p>'
        '<form method="post" action="/api/weather/turbulence"'
        ' style="display:inline-flex;gap:0.6em;align-items:center;flex-wrap:wrap">')
    for kind in _TURB_EVENT_KINDS:
        html += (f'<label>{kind.capitalize()} ×<input type="number" name="event_mult_{kind}"'
                 f' value="{mult.get(kind, 1.0):g}" min="0" max="{_TURB_EVENT_MAX[kind]}"'
                 ' step="any" style="width:4.5em"></label>')
    html += '<button class="btn btn-gray btn-sm">Set</button></form><div style="margin-top:0.5em">'
    for preset, values in _TURB_EVENT_PRESETS.items():
        html += (
            '<form method="post" action="/api/weather/turbulence" style="display:inline">'
            f'<input type="hidden" name="event_preset" value="{preset}">'
            f'<button class="btn btn-blue btn-sm">{preset.capitalize()} '
            f'({"/".join(f"{v:g}" for v in values)})</button></form> ')
    html += '</div>'
    if testing:
        html += '<div style="margin-top:0.5em;font-size:0.85em;color:#94a3b8">Trigger now: '
        for kind in _TURB_EVENT_KINDS:
            html += (
                '<form method="post" action="/api/weather/turbulence" style="display:inline">'
                f'<input type="hidden" name="event_trigger" value="{kind}">'
                f'<button class="btn btn-red btn-sm">{kind}</button></form> ')
        html += '</div>'
    events = tstate.get('events') or []
    if events:
        html += ('<table style="border-collapse:collapse;margin-top:0.75em;font-size:0.85em">'
                 '<tr><th>UTC</th><th>Kind</th><th>Area</th><th>Length</th><th>Peak</th>'
                 '<th>Position</th></tr>')
        for ev in events:
            peak = f'{ev["peak_g"]:.1f} g' if ev.get('peak_g') else '-'
            html += (
                f'<tr><td>{_esc(ev.get("utc", ""))}</td><td>{_esc(ev.get("kind", ""))}'
                f'{" (triggered)" if ev.get("triggered") else ""}</td>'
                f'<td title="{_esc(ev.get("reason", ""))}">{_esc(ev.get("area", ""))}</td>'
                f'<td>{ev.get("duration_s", 0)} s</td><td>{peak}</td>'
                f'<td>{_esc(ev.get("position", ""))}</td></tr>')
        html += '</table>'
    return html + '</div>\n'


def _event_logs_html(ctx) -> str:
    """Return the Config and logs page's Logs section (frankenweather's own web UI only)."""

    def _page(body):
        return '<h2 id="logs">Logs</h2>\n' + body

    log_dir = ctx.event_log_dir
    current = ctx.event_log_path
    entries = _list_event_logs(log_dir)

    body = (
        '<p class="note">Each frankenweather run writes its own console output to a '
        'timestamped log file here (the last '
        f'{len(entries)} run(s) are kept). If you hit a problem, download the log covering '
        'that time and send it along with your report.</p>\n'
        f'<p class="note">Directory: <code>{log_dir}</code></p>\n'
    )

    if not entries:
        body += '<div class="card warn"><p style="margin:0">No log files found.</p></div>\n'
        return _page(body)

    now = time.time()
    rows = []
    for name, size, mtime in entries:
        is_current = current is not None and os.path.basename(current) == name
        badge = ' <span style="color:#22c55e">(current)</span>' if is_current else ''
        rows.append(
            '<tr>'
            f'<td>{name}{badge}</td>'
            f'<td style="text-align:right">{_fmt_bytes(size)}</td>'
            f'<td style="text-align:right">{_fmt_relative_epoch(now, mtime)}</td>'
            f'<td style="text-align:right">'
            f'<a href="/weather/logs/download/{name}" class="btn btn-gray btn-sm">Download</a>'
            '</td></tr>\n'
        )
    body += (
        '<div class="card"><table style="width:100%;border-collapse:collapse">\n'
        '<tr><th style="text-align:left">File</th><th style="text-align:right">Size</th>'
        '<th style="text-align:right">Last written</th><th></th></tr>\n' +
        ''.join(rows) +
        '</table></div>\n'
    )
    return _page(body)


def register_weather_logs_routes(routes, ctx):
    """Register the log download route, and /weather/logs as a redirect (standalone only).

    Not called from the router's web API -- the router has no reason to
    expose frankenweather's own per-run event logs (see ctx.event_log_dir/
    ctx.event_log_path in the module docstring, which only the standalone
    context provides).
    """

    @routes.get('/weather/logs')
    async def _weather_logs_get(_):
        raise web.HTTPFound('/weather/config#logs')  # now part of the Config and logs page

    @routes.get('/weather/logs/download/{name}')
    async def _weather_logs_download(request):
        name = request.match_info['name']
        log_dir = ctx.event_log_dir
        # Exact membership check against the real directory listing, rather
        # than trusting/sanitizing the requested name into a path -- this is
        # immune to path traversal (../, absolute paths, symlinks in the
        # name, etc.) since anything not literally one of these existing
        # filenames is rejected outright.
        valid_names = {n for n, _, _ in _list_event_logs(log_dir)}
        if name not in valid_names:
            return web.Response(text="Not found", status=404)
        return web.FileResponse(
            os.path.join(log_dir, name),
            headers={'Content-Disposition': f'attachment; filename="{name}"'})
