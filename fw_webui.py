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
  ctx.fw_conflict_paused   : bool         — True while this instance is paused
                                            because a second FRANKENWEATHER
                                            instance is on the network
  ctx.color_scheme        : str           — "dark" or "light"
  ctx.cache_get(name)     -> str | None   — PSX variable lookup by name
  ctx.send_manualwx_cmd(cmd: dict)      -> coroutine
  ctx.send_turb_cmd(cmd: dict)          -> coroutine
  ctx.send_mode_cmd(mode: str)          -> coroutine
  ctx.send_fw_settings_cmd(cmd: dict)   -> coroutine
"""
# pylint: disable=invalid-name,too-many-lines
import datetime
import json
import math
import time

from aiohttp import web  # pylint: disable=import-error


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

_TURB_TYPES_ORDER = ('wave', 'rotor', 'mechanical', 'shear', 'cb', 'pirep', 'cape', 'gairmet')
_TURB_KIND_LABELS = {
    'wave': 'Mountain wave', 'rotor': 'Lee rotor', 'mechanical': 'Mechanical',
    'shear': 'Wind shear CAT', 'cb': 'CB proximity', 'pirep': 'PIREP',
    'cape': 'CAPE convective', 'gairmet': 'G-AIRMET',
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    lo_base = int(p.get("lo_base", 45000))
    hi_oktas = int(p.get("hi_oktas", 0))
    hi_base = int(p.get("hi_base", 45000))
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
    if lo_cover and lo_base < 45000:
        sky_tokens.append(f"{lo_cover}{lo_base // 100:03d}")
    hi_cover = _OKTAS_COVER.get(hi_oktas)
    if hi_cover and hi_base < 45000:
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


def _fmt_data_status(now: float, epoch, timeout_s: float = _DATA_STALE_TIMEOUT_S) -> tuple:
    """Return (color, status_label, age_label) describing a 'last received' epoch timestamp."""
    if not epoch:
        return '#ef4444', 'not getting data', 'never'
    age_label = _fmt_relative_epoch(now, epoch)
    if now - epoch > timeout_s:
        return '#ef4444', 'not getting data', age_label
    return '#22c55e', 'OK', age_label


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
        return (
            '<!DOCTYPE html>\n<html>\n<head>\n'
            f'<meta name="color-scheme" content="{color_scheme}" />\n' +
            _COMMON_CSS.format() +
            '\n<style>'
            'body { max-width: none; }'
            '#map { width: min(calc(100vh - 7rem), 72vw);'
            ' height: min(calc(100vh - 7rem), 72vw);'
            ' border-radius: 8px; border: 1px solid #2a2f45; flex-shrink: 0; }'
            '.wx-map-wrap { display: flex; gap: 1.5em; align-items: flex-start; }'
            '.wx-map-side { flex: 1; min-width: 180px; display: flex;'
            ' flex-direction: column; gap: 1em; }'
            '</style>\n' +
            _LEAFLET_HEAD +
            '</head>\n<body>\n'
            '<div class="page-title">'
            '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
            '<h1>Weather</h1>'
            '<div style="margin-left:auto;display:flex;gap:0.5em">'
            '<a href="/weather" class="btn btn-gray btn-sm">Refresh</a>'
            '<a href="/" class="btn btn-gray btn-sm">Back</a>'
            '</div>'
            '</div>\n' + body + '</body>\n</html>\n'
        )

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
        })

    zones_js = json.dumps(zone_data)
    sigmets_js = json.dumps(state.get('sigmets', []))
    ac_alt_ft = state.get('ac_alt_ft') or 0
    turbstate = ctx.fw_turbstate or {}
    turb_src_lat = turbstate.get('source_lat')
    turb_src_lon = turbstate.get('source_lon')
    turb_kind = turbstate.get('active_kind', 'none')
    turb_intensity = turbstate.get('active_intensity', 0.0)
    turb_pct = int(turb_intensity * 100)
    turb_label = _TURB_KIND_LABELS.get(turb_kind, turb_kind) if turbstate else '—'

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
            {'kind': _TURB_KIND_LABELS.get(s['kind'], s['kind']),
             'pct': int(s['intensity'] * 100),
             'reason': s['reason'],
             'active': s['kind'] == turb_kind}
            for s in turb_sources
        ],
    })
    _WIND_TURB_KINDS = {'wave', 'rotor', 'mechanical'}
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
        'function parseSavedView(){'
        'var h=location.hash.slice(1).split(",");'
        'if(h.length===3){var la=parseFloat(h[0]),lo=parseFloat(h[1]),z=parseFloat(h[2]);'
        'if(!isNaN(la)&&!isNaN(lo)&&!isNaN(z))return{lat:la,lon:lo,zoom:z};}'
        'return null;}\n'
        f'var acLat={ac_lat},acLon={ac_lon},acHdg={ac_hdg:.0f},acAlt={ac_alt_ft};\n'
        f'var fwMode={json.dumps(fw_mode)},navMode={json.dumps(nav_mode)};\n'
        f'var zones={zones_js};\n'
        f'var turbInfo={turb_js};\n'
        f'var turbSummary={turb_summary_js};\n'
        f'var sigmets={sigmets_js};\n'
        "var _sv=parseSavedView();\n"
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
        "var bounds=L.latLngBounds([[acLat,acLon]]);\n"
        "sigmets.forEach(function(s){\n"
        "  var pts=s.polygon.map(function(p){return[p[0],p[1]];});\n"
        "  var topFL=Math.round(s.top_ft/100);\n"
        "  L.polygon(pts,{color:'#ef4444',weight:2,opacity:0.9,"
        "fillColor:'#ef4444',fillOpacity:0.15})"
        ".bindTooltip('<b>TS SIGMET</b><br>Top FL'+topFL,"
        "{sticky:true,className:'sigmet-label'})"
        ".addTo(map);\n"
        "});\n"
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
        "  var cbLabel='';\n"
        "  if(wx.cb_raw){\n"
        "    var cr=wx.cb_raw;\n"
        "    cbLabel='<br>⛈ '+cr.oktas+\"/8 \"+cr.base+\"'-\"+cr.top+\"'\";\n"
        "  }\n"
        "  var tip='<b>WX'+z.zone+'</b> '+z.icao+cbLabel;\n"
        "  var tipCls=z.is_focused?'zone-label zone-label-focused':'zone-label';\n"
        "  if(z.is_focused)L.circleMarker([z.lat,z.lon],"
        "{radius:14,color:'#3b82f6',weight:2.5,fill:false,opacity:0.9}).addTo(map);\n"
        "  var cbRow=wx.cb_raw"
        "?'<tr><td style=\"color:#f87171\">⛈ CB</td><td style=\"color:#f87171\">'"
        "+wx.cb_raw.oktas+'/8 '+wx.cb_raw.base+\"'-\"+wx.cb_raw.top+\"'</td></tr>\":'';\n"
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
        "location.hash=c.lat.toFixed(5)+','+c.lng.toFixed(5)+','+z.toFixed(2);"
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
        "        L.push('    SIGMET '+x.idx+': Top FL'+Math.round(x.s.top_ft/100)"
        "+'  ('+x.s.polygon.length+' pts)');\n"
        "      });\n"
        "    }\n"
        "    if(nearby.length>0){\n"
        "      nearby.sort(function(a,b){return a.dist-b.dist;});\n"
        "      L.push('  Nearby (<200 nm):');\n"
        "      nearby.forEach(function(x){\n"
        "        L.push('    SIGMET '+x.idx+': Top FL'+Math.round(x.s.top_ft/100)"
        "+'  '+x.dist+' nm  ('+x.s.polygon.length+' pts)');\n"
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
            turb_summary += ' (low speed — not injected)'
    else:
        turb_summary = 'low speed — not injected' if turb_low_speed and turb_enabled else ''

    _sub = 'font-size:0.78em;color:#64748b;padding:0 0 0.3em 0'

    # QNH display for map side panel
    psx_qnh_str = focused_zd['wx']['qnh'] if focused_zd and focused_zd.get('wx') else None
    msfs_qnh_map = turbstate.get('msfs_qnh_hpa')
    if psx_qnh_str and msfs_qnh_map is not None:
        psx_qnh_hpa = round(int(psx_qnh_str.split()[0]))
        qnh_diff = abs(msfs_qnh_map - psx_qnh_hpa)
        qnh_warn_color = '#f59e0b' if qnh_diff > 1.0 else '#e2e8f0'
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
        '<a href="/weather/settings" class="btn btn-gray btn-sm">Weather zones</a>\n'
        '<a href="/weather/turbulence" class="btn btn-gray btn-sm">Extra turbulence</a>\n'
        '<a href="/weather/enroute-wind" class="btn btn-gray btn-sm">Enroute wind</a>\n'
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


def _build_weather_settings_page(ctx):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Render the /weather/settings HTML page from the cached FRANKENWEATHER state."""
    color_scheme = ctx.color_scheme
    now = time.time()
    state = ctx.fw_state
    received_at = ctx.fw_state_received_at
    age_s = now - received_at if received_at else float('inf')
    stale = state is None or age_s > 300.0

    def _page(body):
        return (
            '<!DOCTYPE html>\n<html>\n<head>\n'
            f'<meta name="color-scheme" content="{color_scheme}" />\n' +
            _COMMON_CSS.format() +
            '\n<style>body { max-width: 64em; }</style>\n</head>\n<body>\n'
            '<div class="page-title">'
            '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
            '<h1>Weather settings</h1>'
            '<div style="margin-left:auto;display:flex;gap:0.5em">'
            '<a href="/weather" class="btn btn-gray btn-sm">Map</a>'
            '<a href="/weather/manual" class="btn btn-gray btn-sm">Manual weather</a>'
            '<a href="/weather/enroute-wind" class="btn btn-gray btn-sm">Enroute wind</a>'
            '<a href="/weather/settings" class="btn btn-gray btn-sm">Refresh</a>'
            '</div>'
            '</div>\n' +
            body +
            '</body>\n</html>\n'
        )

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
        f'<tr><td>Heading / Altitude</td><td class="val">{hdg_str} / {alt_str}</td></tr>\n'
        '</table>\n</div>\n'
    )

    fw_mode = state.get('fw_mode', 'enabled')
    fw_mode_class = 'ok' if fw_mode == 'enabled' else 'warn'
    _MODE_BTN = {
        'enabled': 'btn-green', 'paused': 'btn-amber',
        'disabled': 'btn-red', 'manual': 'btn-blue',
    }
    _MODE_LABEL = {
        'enabled': 'Enable', 'paused': 'Pause',
        'disabled': 'Disable', 'manual': 'Manual',
    }
    _MODE_DESC = {
        'enabled': 'FrankenWeather manages weather zones automatically',
        'paused': 'Weather zones are frozen — no updates from FrankenWeather or PSX',
        'disabled': 'PSX built-in weather active; FrankenWeather inactive',
        'manual': 'Weather set manually via the Manual Weather page',
    }
    other_modes = [m for m in ('enabled', 'paused', 'disabled', 'manual') if m != fw_mode]
    body += (
        '<h2>Mode control</h2>\n'
        '<div class="card ok">\n<table>\n'
        f'<tr><td>Current mode</td><td class="{fw_mode_class}">{fw_mode}</td></tr>\n'
        f'<tr><td></td><td class="note" style="font-size:0.85em">'
        f'{_MODE_DESC[fw_mode]}</td></tr>\n'
        '</table>\n</div>\n'
        '<div class="btn-row">\n'
    )
    for m in other_modes:
        body += (
            f'<form action="/api/weather/mode" method="post" style="display:inline">\n'
            f'<input type="hidden" name="mode" value="{m}">\n'
            f'<button type="submit" class="btn {_MODE_BTN[m]}" title="{_MODE_DESC[m]}">'
            f'{_MODE_LABEL[m]}</button>\n'
            '</form>\n'
        )
    body += '</div>\n'

    zones = state.get('zones', [])
    body += '<h2>Weather zones</h2>\n'
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
                body += f'<tr><td>CB</td><td class="warn">{wx["cb"]}</td></tr>\n'
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
    wind_sync = cfg.get('msfs_wind_sync', False)

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
        '<p class="note">Data from SimConnect (via frankenmsfsbridge) used to sync '
        'PSX weather to what MSFS sees. These settings take effect only when the bridge '
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
        '<tr><td style="padding-right:0.8em">Wind sync<br>'
        '<span style="font-size:0.82em;color:#64748b">Inject MSFS wind at current '
        'altitude into the PSX wind corridor</span></td><td>' +
        _settings_toggle('msfs_wind_sync', 'false' if wind_sync else 'true',
                         'ON' if wind_sync else 'OFF',
                         wind_sync) +
        '</td></tr>\n'
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
         f'at {squeeze[0]} J/kg → min {squeeze[1]} nm ahead</td></tr>\n'
         if squeeze else '') +
        (f'<tr><td>Fake CB override</td><td class="warn">'
         f'{fake_cb[0]} oktas base {fake_cb[1]} ft top {fake_cb[2]} ft</td></tr>\n'
         if fake_cb else '') +
        '</table>\n</div>\n'
    )

    config_file = cfg.get("config_file")
    config_file_exists = cfg.get("config_file_exists", False)
    body += '<h2>Config file</h2>\n'
    if not config_file:
        body += (
            '<div class="card warn"><p style="margin:0">No <code>--config-file</code> given '
            'at startup — settings above cannot be saved or reloaded from disk. Restart '
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

    body += '<hr>\n<a href="/weather" class="btn btn-gray">Back to map</a>\n'
    return _page(body)


def _build_weather_manual_page(ctx):  # pylint: disable=too-many-locals,too-many-statements
    """Render the /weather/manual HTML page for manual weather configuration."""
    color_scheme = ctx.color_scheme
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
        return (
            '<!DOCTYPE html>\n<html>\n<head>\n'
            f'<meta name="color-scheme" content="{color_scheme}" />\n' +
            _COMMON_CSS.format() +
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
            '</style>\n</head>\n<body>\n'
            '<div class="page-title">'
            '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
            '<h1>Manual weather</h1>'
            '<div style="margin-left:auto;display:flex;gap:0.5em">'
            '<a href="/weather" class="btn btn-gray btn-sm">Map</a>'
            '<a href="/weather/settings" class="btn btn-gray btn-sm">Zone settings</a>'
            '<a href="/weather/turbulence" class="btn btn-gray btn-sm">Turbulence</a>'
            '<a href="/weather/enroute-wind" class="btn btn-gray btn-sm">Enroute wind</a>'
            '<a href="/weather/manual" class="btn btn-gray btn-sm">Refresh</a>'
            '</div>'
            '</div>\n' +
            body +
            '</body>\n</html>\n'
        )

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
    if fw_mode != 'manual':
        body += (
            '<form action="/api/weather/mode" method="post">'
            '<input type="hidden" name="mode" value="manual">'
            '<button type="submit" class="btn btn-blue btn-sm">Switch to manual mode</button>'
            '</form>'
        )
    else:
        body += (
            '<form action="/api/weather/mode" method="post">'
            '<input type="hidden" name="mode" value="enabled">'
            '<button type="submit" class="btn btn-green btn-sm">Back to normal</button>'
            '</form>'
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
        return (
            '<!DOCTYPE html>\n<html>\n<head>\n'
            f'<meta name="color-scheme" content="{color_scheme}" />\n' +
            _COMMON_CSS.format() +
            '\n<style>body { max-width: 56em; }'
            'table.turb-types { width:100%; border-collapse:collapse; }'
            'table.turb-types td,table.turb-types th {'
            ' padding:4px 8px; border-bottom:1px solid #334155; }'
            'table.turb-types th { color:#94a3b8; font-size:0.8em; text-align:left; }'
            '</style>\n'
            '<script>setTimeout(function(){location.reload();},30000);</script>\n'
            '</head>\n<body>\n'
            '<div class="page-title">'
            '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
            '<h1>Turbulence</h1>'
            '<div style="margin-left:auto;display:flex;gap:0.5em">'
            '<a href="/weather" class="btn btn-gray btn-sm">Map</a>'
            '<a href="/weather/manual" class="btn btn-gray btn-sm">Manual weather</a>'
            '<a href="/weather/settings" class="btn btn-gray btn-sm">Weather zones</a>'
            '<a href="/weather/enroute-wind" class="btn btn-gray btn-sm">Enroute wind</a>'
            '</div>'
            '</div>\n' +
            body +
            '</body>\n</html>\n'
        )

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
    active_intensity = tstate.get('active_intensity', 0.0)
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
        f'<b>{_TURB_KIND_LABELS.get(active_kind, active_kind)}</b></div>'
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
                f'{marker}<b>{_TURB_KIND_LABELS.get(src["kind"], src["kind"])}</b>'
                f' {src_pct}% — {src["reason"]}</div>'
            )
    if low_speed and enabled:
        status_html += (
            '<div style="font-size:0.82em;color:#f59e0b;margin-top:0.5em">'
            'Ground speed below 30 kt — turbulence computed but not injected. '
            'Values shown are what would be active in flight.</div>'
        )
    status_html += (
        f'<div style="font-size:0.75em;color:#64748b;margin-top:0.5em">Data: {age_str}</div>'
    )
    status_html += '</div>\n'

    _KIND_LABELS = {
        'wave': 'Mountain wave', 'rotor': 'Lee rotor', 'mechanical': 'Mechanical',
        'shear': 'Wind shear', 'cb': 'CB proximity', 'pirep': 'PIREP',
        'cape': 'CAPE convective', 'gairmet': 'G-AIRMET',
    }
    manual_pct = int(manual_turb_intensity * 100)
    kind_opts = ''.join(
        f'<option value="{k}"{" selected" if k == manual_turb_kind else ""}>'
        f'{_KIND_LABELS.get(k, k)}</option>'
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
        'bypassing the automatic engine entirely.</p>'
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
        'distance from the CB zone centre. Higher = CB influence extends further sideways.</div>'
        '</td></tr>'
        f'<tr><td style="{_th}">MSFS influence magnitude (%)</td>'
        f'<td style="{_td}">'
        f'<form method="post" action="/api/weather/turbulence" style="display:inline-flex;'
        f'gap:0.4em;align-items:center">'
        f'<input type="number" name="msfs_turb_magnitude" value="{msfs_turb_magnitude}"'
        f' min="0" max="200" style="width:4.5em">'
        f'<button class="btn btn-gray btn-sm">Set</button></form>'
        f'<div style="{_note}">How much MSFS in-cloud and precipitation data boosts computed '
        'intensity when the aircraft is inside convective weather. 0 = ignore MSFS data.</div>'
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
        'Real-time data read from MSFS via SimConnect (requires the frankenweather MSFS addon). '
        'Used to boost turbulence intensity when the aircraft is inside cloud or precipitation — '
        'e.g. a higher cloud density or active rain amplifies the computed intensity. '
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
    body = intro + stale_banner + status_html + manual_html + global_html
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


def _build_weather_enroute_wind_page(ctx):  # pylint: disable=too-many-locals,too-many-branches
    """Render the /weather/enroute-wind page: flight-plan vs. Open-Meteo per-waypoint wind."""
    color_scheme = ctx.color_scheme
    windstate = ctx.fw_windstate
    now = time.time()

    def _page(body):
        return (
            '<!DOCTYPE html>\n<html>\n<head>\n'
            f'<meta name="color-scheme" content="{color_scheme}" />\n' +
            _COMMON_CSS.format() +
            '\n<style>body { max-width: 64em; }'
            'table.ew-table { border-collapse:collapse;width:100%;font-size:0.88em; }'
            'table.ew-table th { text-align:left;color:#64748b;font-size:0.8em;'
            'padding:2px 6px;border-bottom:1px solid #2a2f45; }'
            'table.ew-table td { padding:2px 6px;border-bottom:1px solid #1c2033; }'
            'tr.ew-passed td { color:#64748b; }'
            'td.ew-diff-hi { color:#f59e0b;font-weight:600; }'
            '</style>\n</head>\n<body>\n'
            '<div class="page-title">'
            '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
            '<h1>Enroute wind</h1>'
            '<div style="margin-left:auto;display:flex;gap:0.5em">'
            '<a href="/weather" class="btn btn-gray btn-sm">Map</a>'
            '<a href="/weather/manual" class="btn btn-gray btn-sm">Manual weather</a>'
            '<a href="/weather/settings" class="btn btn-gray btn-sm">Weather zones</a>'
            '<a href="/weather/enroute-wind" class="btn btn-gray btn-sm">Refresh</a>'
            '</div>'
            '</div>\n' +
            body +
            '</body>\n</html>\n'
        )

    enabled = bool(windstate and windstate.get("enabled"))
    toggle_label = "Disable" if enabled else "Enable"
    toggle_class = "btn-red" if enabled else "btn-green"
    body = (
        '<p class="note">Periodically fetches Open-Meteo pressure-level wind for each '
        "upcoming route waypoint and refreshes PSX's enroute wind corridor, so cruise winds "
        'keep drifting with reality even if the crew never requests a new datalink wind '
        'uplink. Opt-in, and mutually exclusive with MSFS wind sync (enabling one disables '
        'the other).</p>\n'
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
        'on top of the corridor data — 10% subtle, 80% large.</span>'
        '</div>'
        '<form action="/api/weather/enroute-wind/deviation" method="post" '
        'style="display:flex;align-items:center;gap:0.75em">'
        f'<input type="range" name="deviation" min="10" max="80" step="10" value="{deviation}" '
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
    color_scheme = ctx.color_scheme
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        f'<meta name="color-scheme" content="{color_scheme}" />\n' +
        _COMMON_CSS.format() +
        '<script>setTimeout(function(){location.reload();},15000);</script>\n'
        '</head>\n<body>\n'
        '<div class="page-title">'
        '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
        '<h1>FrankenWeather</h1>'
        '</div>\n'
        '<div class="card warn">'
        '<p style="margin:0 0 0.5em;font-size:1.1em"><b>⏸ PAUSED</b></p>'
        '<p style="margin:0">Another FRANKENWEATHER instance is active on the network. '
        'This instance is doing no Open-Meteo/VATSIM downloads and sending nothing to PSX '
        '(no zone weather, no turbulence, no wind corridor) until the other instance '
        'goes away.</p>'
        '</div>\n'
        '</body>\n</html>\n'
    )


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

    @routes.get('/weather/turbulence')
    async def _weather_turb_get(_):
        return web.Response(text=_paused_or(_build_weather_turb_page), content_type='text/html')

    @routes.get('/weather/manual')
    async def _weather_manual_get(_):
        return web.Response(text=_paused_or(_build_weather_manual_page), content_type='text/html')

    @routes.get('/weather/enroute-wind')
    async def _weather_enroute_wind_get(_):
        return web.Response(
            text=_paused_or(_build_weather_enroute_wind_page), content_type='text/html')

    @routes.post('/api/weather/enroute-wind/toggle')
    async def _weather_enroute_wind_toggle(request):
        data = await request.post()
        enabled = data.get('enabled') == '1'
        await ctx.send_fw_settings_cmd({"enroute_wind_enabled": enabled})
        raise web.HTTPFound('/weather/enroute-wind')

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
        raise web.HTTPFound('/weather/manual')

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
        raise web.HTTPFound('/weather/manual')

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
        if 'msfs_wind_sync' in data:
            cmd['msfs_wind_sync'] = data['msfs_wind_sync'].lower() == 'true'
        if cmd:
            await ctx.send_fw_settings_cmd(cmd)
        raise web.HTTPFound('/weather/settings')

    @routes.post('/api/weather/config/save')
    async def _weather_config_save(_):
        await ctx.send_fw_settings_cmd({"config_action": "save"})
        raise web.HTTPFound('/weather/settings')

    @routes.post('/api/weather/config/load')
    async def _weather_config_load(_):
        await ctx.send_fw_settings_cmd({"config_action": "load"})
        raise web.HTTPFound('/weather/settings')

    @routes.post('/api/weather/config/reset')
    async def _weather_config_reset(_):
        await ctx.send_fw_settings_cmd({"config_action": "reset"})
        raise web.HTTPFound('/weather/settings')

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
