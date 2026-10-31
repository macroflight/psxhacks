"""The /efb page's weather parts: shared by frankenrouter and frankenweather.

A compact status and control page for PSX.NET.EFB (or any small screen).
frankenrouter's /efb (router/frankenrouter/webapi.py) adds the router's
status, errors and warnings; frankenweather's own web server serves a
weather-only version (build_page()). ctx is the fw_webui context
(fw_webui.py's module docstring).
"""
# pylint: disable=too-many-lines
import asyncio
import html
import time

from aiohttp import web  # pylint: disable=import-error

import fw_sigmet as _fw_sigmet
import fw_webui as _fw_webui

STALE_TIMEOUT_S = 300.0
# VATSIM METAR only refreshes every 30 minutes by design (frankenweather.py's
# _VATSIM_CACHE_MAX_S), and even that only happens opportunistically inside
# _update_zones() -- flagging it "not getting data" against the generic 5-minute
# threshold above would be a false alarm on every normal cycle. Give it a
# matching cadence plus a buffer instead.
VATSIM_STALE_TIMEOUT_S = 2100.0

CSS = '''\
<style>
* { box-sizing: border-box; }
html, body {
  margin: 0; padding: 0; background: #000; color: #dbe6f5;
  font-family: "Segoe UI", -apple-system, "Helvetica Neue", Arial, sans-serif;
}
body { padding: 0.8rem 1.1rem 1rem; }
.efb-grid { display: grid; gap: 0.7rem; grid-template-columns: repeat(auto-fit, minmax(15rem, 1fr)); }
.efb-grid.wide { grid-template-columns: repeat(auto-fit, minmax(15.5rem, 1fr)); }
.efb-card {
  background: #0d1826; border: 1px solid #1e2f45; border-radius: 10px;
  padding: 0.75rem 0.9rem; min-width: 0;
}
.efb-card.fill-green { background: linear-gradient(135deg, #1f8a4c, #176b3c); border-color: #1f8a4c; }
.efb-card.fill-red { background: linear-gradient(135deg, #8a2f2f, #6b2323); border-color: #8a2f2f; }
.efb-eyebrow {
  color: #7e97ba; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; margin: 0 0 0.5rem;
}
.fill-green .efb-eyebrow, .fill-red .efb-eyebrow { color: rgba(255,255,255,0.75); }
.efb-value { font-size: 1.35rem; font-weight: 700; color: #eef4fb; }
.efb-value small { display: block; font-size: 0.78rem; font-weight: 400; color: #9fb2cc; margin-top: 0.15rem; }
.fill-green .efb-value, .fill-red .efb-value { color: #fff; }
.fill-green .efb-value small, .fill-red .efb-value small { color: rgba(255,255,255,0.8); }
.efb-dot { display: inline-block; width: 0.6rem; height: 0.6rem; border-radius: 50%; margin-right: 0.4rem; }
.efb-chip-row { display: flex; flex-wrap: wrap; gap: 0.55rem; margin-top: 0.4rem; }
.efb-hint { margin: 0.35rem 0 0; font-size: 0.78rem; color: #7e97ba; }
.efb-chip {
  display: inline-flex; align-items: center; gap: 0.4rem; padding: 0.55rem 0.9rem;
  border-radius: 8px; border: 1px solid #24384f; background: #101c2c; color: #cbd8ea;
  font-size: 0.88rem; font-weight: 600; cursor: pointer; text-decoration: none;
}
.efb-chip.active { background: #f4d100; border-color: #f4d100; color: #1a1a1a; }
.efb-chip.active.blue { background: #2058a8; border-color: #3b7fe0; color: #fff; }
.efb-chip.panic, .efb-chip.red { background: #b91c1c; border-color: #ef4444; color: #fff; }
.efb-chip.yellow { background: #f4d100; border-color: #f4d100; color: #1a1a1a; }
#efb-problems { top: 10%; width: 40rem; max-height: 80vh; overflow-y: auto; font-size: 0.88rem; }
#efb-problems h4 { margin: 0.6rem 0 0.2rem; font-size: 0.75rem; letter-spacing: 0.08em;
  text-transform: uppercase; }
#efb-problems ul { margin: 0.2rem 0 0; padding-left: 1.2rem; }
.efb-chip-sm { padding: 0.3rem 0.6rem; font-size: 0.76rem; flex-shrink: 0; white-space: nowrap; }
.efb-popup {
  display: none; position: fixed; left: 50%; top: 20%; transform: translateX(-50%);
  min-width: 15rem; max-width: 90vw; z-index: 10; cursor: pointer;
  background: #0d1826; border: 1px solid #3b7fe0; border-radius: 10px;
  padding: 0.75rem 1rem; box-shadow: 0 8px 30px rgba(0,0,0,0.7);
}
#efb-help { top: 6%; width: 44rem; max-height: 88vh; overflow-y: auto; font-size: 0.88rem; }
#efb-help h4 { margin: 0.7rem 0 0.2rem; color: #7e97ba; font-size: 0.75rem;
  letter-spacing: 0.08em; text-transform: uppercase; }
#efb-help p { margin: 0.15rem 0; }
#efb-help b { color: #eef4fb; }
.efb-section { margin-top: 0.9rem; padding-top: 0.7rem; border-top: 1px solid #1e2f45; }
.efb-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 0.3rem; }
.efb-head .efb-eyebrow { margin: 0; }
#efb-pirep table { border-collapse: collapse; width: 100%; font-size: 0.95rem; }
#efb-pirep td { padding: 0.2rem 0.4rem; }
#efb-pirep tr.cur td { background: #16233a; font-weight: 700; }
.efb-src-row {
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline;
  gap: 0.15rem 0.5rem; font-size: 0.82rem; padding: 0.28rem 0; border-bottom: 1px solid #16233a;
}
.efb-src-row:last-of-type { border-bottom: none; }
.efb-src-row.fixed { flex-wrap: nowrap; }
.efb-src-row.fixed .lbl { white-space: nowrap; flex-shrink: 0; }
.efb-src-row.fixed .val { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; min-width: 0; }
.efb-src-row .lbl { color: #9fb2cc; }
.efb-src-row .val { color: #eef4fb; font-weight: 600; }
.efb-banner {
  border-radius: 8px; padding: 0.65rem 0.9rem; font-size: 0.9rem; font-weight: 600;
  margin-top: 0.7rem; background: #4a3a06; border: 1px solid #8a6d0d; color: #ffdf7a;
}
.efb-foot {
  margin-bottom: 1rem; border-radius: 8px; padding: 0.6rem 0.9rem; font-size: 0.85rem;
  color: #7c8fac; border: 1px solid #1e2f45; background: #0a1120;
}
.efb-foot ul { margin: 0.3rem 0 0; padding-left: 1.2rem; color: #f5a3a3; }
form.efb-inline { display: inline; }
</style>
'''


def age_label(age_s):
    """Return a short human-readable age string for a data-freshness display."""
    if age_s == float('inf'):
        return 'never'
    if age_s < 60:
        return f"{int(age_s)}s ago"
    if age_s < 3600:
        return f"{int(age_s / 60)}m ago"
    return f"{int(age_s / 3600)}h ago"


def data_status(now, epoch, timeout_s=STALE_TIMEOUT_S):
    """Return (color, label, age_label) describing a 'last received' epoch timestamp."""
    if not epoch:
        return '#ef4444', 'not getting data', 'never'
    age_s = now - epoch
    if age_s > timeout_s:
        return '#ef4444', 'not getting data', age_label(age_s)
    return '#22c55e', 'OK', age_label(age_s)


def bool_chip_row(  # pylint: disable=too-many-arguments,too-many-positional-arguments
        action, field, current, true_label='Enabled', false_label='Disabled', small=False):
    """Render a two-state (true/false) chip toggle row posting an absolute value."""
    cls = 'efb-chip efb-chip-sm' if small else 'efb-chip'

    def _chip(value, label):
        active = ' active' if current == value else ''
        val = '1' if value else '0'
        return (f'<form class="efb-inline" method="post" action="{action}">'
                f'<input type="hidden" name="field" value="{field}">'
                f'<input type="hidden" name="value" value="{val}">'
                f'<button type="submit" class="{cls}{active}">{label}</button></form>\n')
    return f'<div class="efb-chip-row">{_chip(True, true_label)}{_chip(False, false_label)}</div>\n'


# Each preset bundles FrankenWeather's fw_mode together with the turbulence
# and enroute-wind toggles, so one tap sets all three at once instead of
# requiring three separate taps to get a fully "on" or fully "off" state.
# 'mode'/'enroute_wind_enabled' go out in one addon=FRANKENWEATHER:COMMAND:
# message (RouterFWContext.send_fw_settings_cmd, handled by
# frankenweather.py's _handle_fw_command()); 'enabled' (turbulence) is a
# *different* message type, addon=FRANKENWEATHER:TURBCOMMAND: (send_turb_cmd,
# handled by _handle_turb_command()) -- the two are not interchangeable, see
# handle_efb_weather_preset_post().
WEATHER_PRESET_CMDS = {
    'full': {'mode': 'enabled', 'enabled': True, 'enroute_wind_enabled': True},
    'psx_auto': {'mode': 'disabled', 'enabled': False, 'enroute_wind_enabled': False},
    'psx_manual': {'mode': 'paused', 'enabled': False, 'enroute_wind_enabled': False},
}

WEATHER_PRESET_LABELS = (
    ('full', 'Normal'),
    ('psx_auto', 'PSX Auto'),
    ('psx_manual', 'PSX Manual'),
)


def weather_preset(w):
    """Return the weather-mode preset key matching FrankenWeather's mode, or None.

    Only the mode counts: extra turbulence has its own buttons (and
    PANIC turns it off), so Full stays highlighted without it.
    """
    for preset, cmd in WEATHER_PRESET_CMDS.items():
        if w['fw_mode'] == cmd['mode']:
            return preset
    return None


def weather_mode_row(current):
    """Render the 3-way Full/PSX Auto/PSX Manual weather-mode chip row."""
    chips = ''.join(
        f'<form class="efb-inline" method="post" action="/api/efb/weather-preset">'
        f'<input type="hidden" name="preset" value="{preset}">'
        f'<button type="submit" '
        f'class="efb-chip efb-chip-sm{" active" if current == preset else ""}">'
        f'{label}</button></form>\n'
        for preset, label in WEATHER_PRESET_LABELS
    )
    return f'<div class="efb-chip-row">{chips}</div>\n'


# /efb's names for the CB near dep/arr modes (fw_webui.CB_AIRPORT_MODES)
CB_AIRPORT_LABELS = {'off': 'Normal', 'move': 'Move CB', 'remove': 'Remove CB'}


def cb_airport_mode_row(w):
    """Render the Off/Move zone/Remove CB chip row (fw_webui.CB_AIRPORT_MODES).

    Remove CB is only offered by FrankenWeather 1.14.0 or later.
    """
    current, has_modes = w['cb_airport_mode']
    chips = ''.join(
        f'<form class="efb-inline" method="post" action="/api/efb/cb-airport-mode">'
        f'<input type="hidden" name="mode" value="{mode}">'
        f'<button type="submit" '
        f'class="efb-chip efb-chip-sm{" active" if current == mode else ""}">'
        f'{CB_AIRPORT_LABELS.get(mode, label)}</button></form>\n'
        for mode, label, _hint in _fw_webui.CB_AIRPORT_MODES
        if mode != 'remove' or has_modes
    )
    return f'<div class="efb-chip-row">{chips}</div>\n'


# /efb's SIGMET buttons in this order
SIGMET_ORDER = ('normal', 'psx', 'off')


def sigmet_presets():
    """Return fw_sigmet.PRESETS in SIGMET_ORDER."""
    return sorted(_fw_sigmet.PRESETS, key=lambda p: SIGMET_ORDER.index(p[0]))


def sigmet_preset_row(w):
    """Render the Off/Normal/PSX SIGMET preset chip row (fw_sigmet.PRESETS).

    Normal is only offered with FrankenWeather's SIGMET system (--sigmet).
    No chip is highlighted when the settings are a custom choice made on
    the SIGMETs page.
    """
    chips = ''.join(
        f'<form class="efb-inline" method="post" action="/api/efb/sigmet-preset">'
        f'<input type="hidden" name="preset" value="{preset}">'
        f'<button type="submit" '
        f'class="efb-chip efb-chip-sm{" active" if w["sigmet_preset"] == preset else ""}">'
        f'{label}</button></form>\n'
        for preset, label, _hint in sigmet_presets()
        if preset != 'normal' or w['sigmet_enabled']
    )
    return f'<div class="efb-chip-row">{chips}</div>\n'


def sigmet_hint(w):
    """Return the /efb hint line explaining the SIGMET presets."""
    if not w['sigmet_enabled']:
        return ('Off: no SIGMETs at all, neither FrankenWeather nor PSX uses any. '
                'PSX: PSX handles every SIGMET itself. '
                'Start FrankenWeather with --sigmet for Normal.')
    hint = '. '.join(f'{label}: {hint}' for preset, label, hint in _fw_sigmet.PRESETS)
    if w['sigmet_preset'] is None:
        hint += '. Now: a custom choice from the SIGMETs page'
    return hint + '.'


# FrankenWeather turbulence profiles (TURBCOMMAND "profile")
TURB_PROFILES = (('stable', 'Stable'), ('testing', 'Testing'))


def turb_profile_html(w):
    """Render the Stable/Testing turbulence profile chips, or '' for an older FrankenWeather."""
    if w['turb_profile'] is None:
        return ''
    chips = ''.join(
        f'<form class="efb-inline" method="post" action="/api/efb/turb-profile">'
        f'<input type="hidden" name="profile" value="{profile}">'
        f'<button type="submit" '
        f'class="efb-chip efb-chip-sm{" active" if w["turb_profile"] == profile else ""}">'
        f'{label}</button></form>\n'
        for profile, label in TURB_PROFILES)
    # Greyed out while extra turbulence is off: the profile has no effect then
    off = '' if w['turb_enabled'] else ' style="opacity:0.4;pointer-events:none"'
    return (f'<div{off}>'
            '<div class="efb-eyebrow" style="margin-top:0.6rem">Turbulence Profile</div>\n'
            f'<div class="efb-chip-row">{chips}</div></div>\n')


def controls_card(w):
    """Render the weather controls card: every weather/turbulence/SIGMET control, no hints.

    The explanations are in the Help popup (help_html()), to keep
    the card short. If FrankenWeather itself isn't running (no STATE
    message ever received, or none recently -- see data_status()),
    none of these controls can do anything: posting a preset/toggle
    still works mechanically (the router accepts the request), but
    there is no FrankenWeather process on the other end to act on it or
    to report back a new state, so the buttons would appear to do
    nothing and the weather-mode 3-way row would never match any preset
    (see weather_preset()) and so never highlight. Show a dimmed,
    control-free card instead of a set of dead buttons.
    """
    if w['status_label'] != 'OK':
        return (
            '<div class="efb-card" id="efb-controls-card" style="opacity:0.55">\n'
            '<div class="efb-eyebrow">Weather Mode</div>\n'
            '<div class="efb-banner" style="margin-top:0">FrankenWeather is not running '
            '-- weather controls unavailable.</div>\n'
            '</div>\n'
        )
    # Greyed out when the settings already are what PANIC sets
    panic_off = (' disabled style="opacity:0.4;pointer-events:none"'
                 if (not w['turb_enabled'] and w['cb_airport_mode'][0] == 'remove' and
                     w['sigmet_preset'] == 'off') else '')
    return (
        '<div class="efb-card" id="efb-controls-card">\n'
        '<div class="efb-eyebrow">Weather Mode</div>\n' +
        weather_mode_row(weather_preset(w)) +
        '<div class="efb-eyebrow" style="margin-top:0.6rem">Extra Turbulence</div>\n' +
        bool_chip_row(
            '/api/efb/turb-toggle', 'enabled', w['turb_enabled'], small=True) +
        turb_profile_html(w) +
        '<div class="efb-eyebrow" style="margin-top:0.6rem">CB Near Dep/Arr</div>\n' +
        cb_airport_mode_row(w) +
        '<div class="efb-eyebrow" style="margin-top:0.6rem">SIGMETs</div>\n' +
        sigmet_preset_row(w) +
        '<div class="efb-chip-row efb-section">'
        '<form class="efb-inline" method="post" action="/api/efb/panic">'
        f'<button type="submit" class="efb-chip efb-chip-sm panic"{panic_off}>PANIC</button>'
        '</form>' + (
            '<form class="efb-inline" method="post" action="/api/efb/defaults">'
            '<button type="submit" class="efb-chip efb-chip-sm red">RESTORE DEFAULTS</button>'
            '</form>'
            if w['startup_defaults'] and w['startup_differs'] else '') +
        '<button type="button" class="efb-chip efb-chip-sm" onclick="efbHelp()">Help</button>'
        '</div>\n'
        '</div>\n'
    )


def help_html(w):
    """Return the Help popup's explanation of every weather control on the /efb page."""
    def section(title, items):
        return (f'<h4>{title}</h4>\n' +
                ''.join(f'<p><b>{label}:</b> {text}</p>\n' for label, text in items))
    profile = ''
    if w['turb_profile'] is not None:
        profile = section('Turbulence Profile', (
            ('Stable', 'the released turbulence'),
            ('Testing', 'also the changes being tried out before they become the default')))
    return (
        '<div style="text-align:right;color:#7e97ba;font-size:0.75rem">Tap to close</div>\n' +
        section('PANIC and RESTORE DEFAULTS', (
            ('PANIC', 'for when the weather gets too much: disables Extra Turbulence, sets '
                      'CB Near Dep/Arr to Remove CB and SIGMETs to Off, in one press'),
            ('RESTORE DEFAULTS', 'shown when any of the settings above differ from how '
                                 'FrankenWeather started: puts them all back, from its '
                                 'config file and command line options'))) +
        section('Weather Mode', (
            ('Normal', 'FrankenWeather active'),
            ('PSX Auto', "PSX's own METAR weather"),
            ('PSX Manual', 'weather frozen, instructor control'))) +
        section('Extra Turbulence', (
            ('Enabled', "FrankenWeather's own turbulence: wind, terrain, convection, "
                        'CB proximity, etc.'),
            ('Disabled', "only PSX's own turbulence"))) +
        profile +
        section('CB Near Dep/Arr', tuple(
            (CB_AIRPORT_LABELS.get(mode, label), hint)
            for mode, label, hint in _fw_webui.CB_AIRPORT_MODES)) +
        (section('SIGMETs', tuple(
            (label, hint) for _preset, label, hint in sigmet_presets())) +
         ('<p>Now: a custom choice from the SIGMETs page.</p>\n'
          if w['sigmet_preset'] is None else '')
         if w['sigmet_enabled'] else
         f'<h4>SIGMETs</h4>\n<p>{sigmet_hint(w)}</p>\n')
    )


def src_row(label, color, text, fixed=False):
    """Render one labelled/colored status row shared by the data-sources-style cards.

    fixed: always one line (a long value is cut short, in full on hover),
    so the row's height never changes.
    """
    if fixed:
        return (f'<div class="efb-src-row fixed"><span class="lbl">{label}</span>'
                f'<span class="val" title="{html.escape(str(text))}">'
                f'<span class="efb-dot" style="background:{color}"></span>{text}</span></div>\n')
    return (f'<div class="efb-src-row"><span class="lbl">{label}</span>'
            f'<span class="val"><span class="efb-dot" style="background:{color}">'
            f'</span>{text}</span></div>\n')


# FrankenWeather turbulence kinds as fw_webui.py's _TURB_KIND_LABELS shows them
TURB_KIND_LABELS = {
    'wave': 'mountain wave', 'rotor': 'lee rotor', 'mechanical': 'mechanical',
    'shear': 'wind shear CAT', 'cb': 'CB', 'pirep': 'PIREP', 'cape': 'CAPE',
    'gairmet': 'G-AIRMET', 'sigmet': 'SIGMET', 'mtw': 'mountain wave SIGMET',
}


def turb_text(w):
    """Return a short human-readable description of the currently-injected turbulence."""
    if not w['turb_available']:
        return 'no data'
    if not w['turb_enabled']:
        return 'OFF'
    kind = w['turb_kind']
    if kind in (None, 'none'):
        return 'none active'
    # "X+shear": wind shear outweighing X
    label = (TURB_KIND_LABELS['shear'] if kind.endswith('+shear')
             else TURB_KIND_LABELS.get(kind, kind))
    return f"{label}, {w['turb_intensity_pct']}%"


def turb_color(w):
    """Return the extra-turbulence row color: intensity-graded when active, else neutral/alert."""
    if not w['turb_available']:
        return '#ef4444'
    if not w['turb_enabled'] or w['turb_kind'] in (None, 'none'):
        return '#22c55e'
    pct = w['turb_intensity_pct']
    if pct < 25:
        return '#22c55e'
    if pct < 50:
        return '#f4d100'
    return '#ef4444'


def turb_injected(w):
    """Return True when turbulence is actually being injected right now."""
    return w['turb_available'] and w['turb_enabled'] and w['turb_kind'] not in (None, 'none')


def turb_section(w):
    """Render the extra-turbulence rows: always the same three one-line rows.

    Rows that only apply while turbulence is injected show a dash
    otherwise, so flying in and out of turbulence never changes the
    page layout.
    """
    injected = turb_injected(w)
    return (
        '<div class="efb-eyebrow">Extra Turbulence</div>\n' +
        src_row('Now', turb_color(w), turb_text(w), fixed=True) +
        src_row('Reason', '#64748b', w['turb_reason'] if injected else '—', fixed=True) +
        src_row('Trend', '#64748b',
                _fw_sigmet.ramp_text(w['turb_ramp'])
                if injected and w['turb_ramp'] else '—', fixed=True)
    )


def datasources_section(w, link_row=None):
    """Render the data-source connectivity/freshness rows.

    link_row: the first row's HTML; by default the router's Router ←
    FrankenWeather (frankenweather's own page shows its PSX connection).
    """
    if link_row is None:
        link_row = src_row('Router ← FrankenWeather', w['src_router_color'],
                           f"{w['src_router_label']}, {w['age_label']}")
    rows = (
        link_row +
        src_row('FrankenWeather ← MSFS', w['src_msfs_color'],
                f"{w['src_msfs_label']}, {w['src_msfs_age']}") +
        src_row('FrankenWeather ← Open-Meteo', w['src_om_color'], w['src_om_label']) +
        src_row(f"FrankenWeather ← {w['src_vatsim_source_name']}", w['src_vatsim_color'],
                f"{w['src_vatsim_label']}, {w['src_vatsim_age']}")
    )
    return (
        '<div class="efb-section">\n'
        '<div class="efb-eyebrow">Weather Status</div>\n' +
        rows +
        '</div>\n'
    )


def decode_wx_wind(wind_field):
    """Decode a Wx-string wind field (VVV+DDD+SS or legacy VVV+DD+SS) into (dir_deg, spd_kt)."""
    try:
        if len(wind_field) == 8:
            return int(wind_field[3:6]), int(wind_field[6:8])
        if len(wind_field) == 7:
            return int(wind_field[3:5]) * 10, int(wind_field[5:7])
    except ValueError:
        pass
    return None, None


def decode_wx(wx_str):
    """Decode a PSX Wx<N>/WxBasic string into display-ready weather fields, or None."""
    if not wx_str:
        return None
    parts = wx_str.split(';')
    if len(parts) < 24:
        return None
    wind_dir, wind_spd = decode_wx_wind(parts[18])
    if wind_dir is None:
        return None
    try:
        gust = int(parts[19])
        return {
            'wind_dir': wind_dir,
            'wind_spd_kt': wind_spd,
            'wind_gust_kt': gust if gust > wind_spd else None,
            'visibility_m': int(parts[20]),
            'temp_c': int(parts[22]),
            'qnh_hpa': round(int(parts[23]) / 2.953, 1),
            'cloud_oktas': int(parts[3]),
            'cloud_base_ft': int(parts[5]),
            'cb_oktas': int(parts[9]),
            'cb_base_ft': int(parts[11]),
            'cb_top_ft': int(parts[10]),
        }
    except (ValueError, IndexError):
        return None


def current_location_dict(ctx):
    """Read PSX's currently-focused weather zone directly and decode its content."""
    raw_zone = ctx.cache_get('FocussedWxZone')
    try:
        zone_num = int(raw_zone) if raw_zone not in (None, '') else 0
    except ValueError:
        zone_num = 0
    if zone_num <= 0:
        wx_key, zone_label = 'WxBasic', 'Global (no zone)'
    else:
        wx_key = f'Wx{zone_num}'
        raw_mode = ctx.cache_get(f'WxMode{zone_num}') or ''
        mode_parts = raw_mode.split(';')
        icao = mode_parts[5][:4] if len(mode_parts) >= 6 else ''
        zone_label = f'Zone {zone_num} ({icao})' if icao else f'Zone {zone_num}'
    fields = decode_wx(ctx.cache_get(wx_key))
    if fields is None:
        return None
    fields['zone_label'] = zone_label
    return fields


def location_wx_card(loc, w, sigmet_hazards=()):
    """Render the extra turbulence rows and the currently-focused zone's weather detail card."""
    if loc is None:
        body = '<p style="margin:0;color:#64748b">No weather data available.</p>\n'
    else:
        gust = f" G{loc['wind_gust_kt']}kt" if loc['wind_gust_kt'] else ''
        clouds = (f"{loc['cloud_oktas']}/8 oktas @ {loc['cloud_base_ft']:,}ft"
                  if loc['cloud_oktas'] > 0 else 'Clear')
        cb = (f"{loc['cb_oktas']}/8 oktas, base {loc['cb_base_ft']:,}ft, "
              f"tops {loc['cb_top_ft']:,}ft" if loc['cb_oktas'] > 0 else 'None')
        body = (
            src_row('Zone', '#3b82f6', loc['zone_label']) +
            src_row('Wind', '#3b82f6',
                    f"{loc['wind_dir']:03d}° / {loc['wind_spd_kt']}kt{gust}") +
            src_row('QNH', '#3b82f6', f"{loc['qnh_hpa']} hPa") +
            src_row('Temperature', '#3b82f6', f"{loc['temp_c']}°C") +
            src_row('Visibility', '#3b82f6', f"{loc['visibility_m']:,} m") +
            src_row('Clouds', '#3b82f6', clouds) +
            src_row('Convective (CB)', '#3b82f6', cb) +
            src_row(
                'In SIGMET area', '#f4d100' if sigmet_hazards else '#64748b',
                ', '.join(sigmet_hazards) if sigmet_hazards else 'No')
        )
    return (
        '<div class="efb-card" id="efb-location-card">\n' +
        turb_section(w) +
        '<div class="efb-section">\n'
        '<div class="efb-eyebrow">Surface Weather</div>\n' +
        body +
        '</div>\n'
        '</div>\n'
    )


PIREP_SHOW_MS = 8000  # the PIREP popup closes by itself after this long
PIREP_RIDE_COLORS = {
    'smooth': '#22c55e', 'light': '#a3e635', 'moderate': '#eab308', 'rough': '#ef4444'}


def pirep_html(ctx, now=None):
    """Render the PIREP popup: FrankenWeather's ride reports near the current flight level.

    The reports (TURBSTATE "pireps", FrankenWeather after 1.11.0) rate
    every source of turbulence at each level, refreshed every minute and
    on a flight level change; highest level first, the current one marked.
    """
    now = time.time() if now is None else now
    turbstate = ctx.fw_turbstate
    fresh, _, _ = data_status(now, ctx.fw_turbstate_received_at)
    head = ('<div class="efb-eyebrow">PIREPs – reported ride</div>\n'
            '<p class="efb-hint" style="margin:0 0 0.4rem;max-width:24rem">Not real pilot '
            "reports: FrankenWeather's own forecast of the ride here, at each level near "
            'yours, from everything that makes its turbulence (terrain, CBs, convection, '
            'real PIREPs, G-AIRMETs, SIGMETs). Updated every minute and when you change '
            'level, to help pick a smoother level.</p>\n')
    if turbstate is None or fresh != '#22c55e':
        return head + '<p class="efb-hint">No data from FrankenWeather.</p>\n'
    reports = turbstate.get('pireps')
    if not reports:
        return head + '<p class="efb-hint">No reports yet.</p>\n'
    rows = ''
    for rep in sorted(reports, key=lambda x: -x['fl']):
        color = PIREP_RIDE_COLORS.get(rep['ride'], '#cbd8ea')
        rows += (f'<tr{" class=\"cur\"" if rep.get("current") else ""}>'
                 f'<td>FL{rep["fl"]:03d}</td>'
                 f'<td style="color:{color};font-weight:700">{rep["ride"]}</td>'
                 f'<td style="color:#7e97ba">{"◀ you" if rep.get("current") else ""}</td></tr>')
    return head + f'<table>{rows}</table>\n'


def weather_status(ctx, now=None):  # pylint: disable=too-many-locals
    """Return the FrankenWeather status dict shown on /efb (router and standalone)."""
    now = time.time() if now is None else now
    state = ctx.fw_state
    fw_color, fw_label, fw_age = data_status(now, ctx.fw_state_received_at)
    cfg = (state or {}).get('config', {})
    last_om_failed = bool((state or {}).get('last_om_fetch_failed'))
    metar_fallback = bool((state or {}).get('metar_fallback_active'))
    if metar_fallback:
        om_color, om_label = '#f4d100', 'METAR fallback active'
    elif last_om_failed:
        om_color, om_label = '#f4d100', 'retrying'
    elif state is not None:
        om_color, om_label = '#22c55e', 'OK'
    else:
        om_color, om_label = '#64748b', 'unknown'
    # frankenweather's --primary-metar-source (experimental, default
    # 'vatsim') decides which bulk METAR cache is actually kept warm in
    # normal operation (see frankenweather.py's _refresh_primary_metar_cache())
    # -- show freshness for whichever one that is, not always VATSIM,
    # or NOAA-primary installs permanently show "VATSIM METAR -- not
    # getting data, never" even though that's expected and fine.
    primary_metar_source = (state or {}).get('primary_metar_source', 'vatsim')
    if primary_metar_source == 'noaa':
        metar_src_name, metar_src_epoch = 'NOAA METAR', (state or {}).get('noaa_metar_cache_time')
    else:
        metar_src_name, metar_src_epoch = 'VATSIM METAR', (state or {}).get('vatsim_cache_time')
    vatsim_color, vatsim_label, vatsim_age = data_status(
        now, metar_src_epoch, VATSIM_STALE_TIMEOUT_S)

    turbstate = ctx.fw_turbstate
    turb_fresh_color, _, turb_age = data_status(now, ctx.fw_turbstate_received_at)
    turb_available = turbstate is not None and turb_fresh_color == '#22c55e'
    msfs_active = bool((turbstate or {}).get('msfs_active'))
    msfs_color, _, msfs_age = data_status(now, (turbstate or {}).get('msfs_last_seen_epoch'))
    if not msfs_active:
        msfs_color = '#64748b'

    weather_info = {
        'available': state is not None,
        'status_color': fw_color, 'status_label': fw_label, 'age_label': fw_age,
        'fw_mode': (state or {}).get('fw_mode', 'unknown'),
        'enroute_wind_enabled': bool(cfg.get('enroute_wind_enabled', False)),
        # (mode, has_modes): has_modes False for FrankenWeather before
        # 1.14.0, which can only move the zone
        'cb_airport_mode': _fw_webui.cb_airport_mode(cfg),
        'cb_airport_clearance_nm': cfg.get('cb_airport_clearance_nm', 15),
        'disable_psx_sigmets': bool(cfg.get('disable_psx_sigmets', False)),
        'sigmet_enabled': bool(cfg.get('sigmet_enabled', False)),
        # FrankenWeather 2.1.4 or later: DEFAULTS (config_action "startup")
        'startup_defaults': bool(cfg.get('startup_defaults', False)),
        'startup_differs': bool(cfg.get('startup_differs', False)),
        # sigmet_own: switched off by the Off/PSX presets (missing from
        # FrankenWeather versions before the presets: always on)
        'sigmet_preset': _fw_sigmet.current_preset(
            bool(cfg.get('sigmet_enabled', False)) and bool(cfg.get('sigmet_own', True)),
            bool(cfg.get('disable_psx_sigmets', False)),
            {h for h, on in (cfg.get('sigmet_handle') or {}).items() if on}),
        'sigmet_hazards': list((state or {}).get('sigmet_hazards', [])),
        'src_router_color': fw_color, 'src_router_label': fw_label, 'src_router_age': fw_age,
        'src_msfs_color': msfs_color,
        'src_msfs_label': 'connected' if msfs_active else 'not connected',
        'src_msfs_age': msfs_age,
        'src_om_color': om_color, 'src_om_label': om_label,
        'src_vatsim_color': vatsim_color, 'src_vatsim_label': vatsim_label,
        'src_vatsim_age': vatsim_age, 'src_vatsim_source_name': metar_src_name,
        'turb_available': turb_available,
        'turb_enabled': bool((turbstate or {}).get('enabled', True)),
        # "stable"/"testing"; None from a FrankenWeather without profiles
        'turb_profile': (turbstate or {}).get('profile'),
        'turb_kind': (turbstate or {}).get('active_kind', 'none'),
        # What's injected (biases and MSFS factor applied); FrankenWeather
        # before 1.16.0 only sends the computed intensity
        'turb_intensity_pct': round((turbstate or {}).get(
            'effective_intensity', (turbstate or {}).get('active_intensity', 0.0)) * 100),
        'turb_age': turb_age,
        'turb_reason': (turbstate or {}).get('active_reason') or '—',
        # "sigmet_ramp" is FrankenWeather 1.11.0's name for it
        'turb_ramp': ((turbstate or {}).get('intensity_ramp') or
                      (turbstate or {}).get('sigmet_ramp')),
    }
    return weather_info


def register_control_routes(routes, ctx, logger, page='/efb'):  # pylint: disable=too-many-locals
    """Register the /efb page's /api/efb/* weather control and PIREP routes.

    Shared by the router (its RouterFWContext) and frankenweather's own
    web server (StandaloneFWContext); page is where a POST redirects to.
    """

    @routes.get('/api/efb/pirep')
    async def handle_efb_pirep_get(_):
        return web.json_response({'html': pirep_html(ctx)},
                                 headers={'Cache-Control': 'no-store'})

    @routes.post('/api/efb/mode')
    async def handle_efb_mode_post(request):
        data = await request.post()
        mode = data.get('mode')
        if mode in ('enabled', 'paused', 'disabled', 'manual'):
            await ctx.send_mode_cmd(mode)
        raise web.HTTPFound(page)

    @routes.post('/api/efb/weather-preset')
    async def handle_efb_weather_preset_post(request):
        data = await request.post()
        cmd = WEATHER_PRESET_CMDS.get(data.get('preset'))
        if cmd:
            await ctx.send_fw_settings_cmd(
                {'mode': cmd['mode'], 'enroute_wind_enabled': cmd['enroute_wind_enabled']})
            await ctx.send_turb_cmd({'enabled': cmd['enabled']})
            await asyncio.sleep(3)
        raise web.HTTPFound(page)

    @routes.post('/api/efb/sigmet-preset')
    async def handle_efb_sigmet_preset_post(request):
        data = await request.post()
        status = {'weather': weather_status(ctx)}
        cmd = _fw_sigmet.preset_command(
            data.get('preset'), status['weather']['sigmet_enabled'])
        if cmd:
            await ctx.send_fw_settings_cmd(cmd)
        raise web.HTTPFound(page)

    @routes.post('/api/efb/cb-airport-mode')
    async def handle_efb_cb_airport_mode_post(request):
        data = await request.post()
        cmd = _fw_webui.cb_airport_mode_command(data.get('mode'))
        if cmd:
            await ctx.send_fw_settings_cmd(cmd)
        raise web.HTTPFound(page)

    @routes.post('/api/efb/panic')
    async def handle_efb_panic_post(_):
        # PANIC: extra turbulence off, remove CBs near dep/arr,
        # SIGMETs off (a frankenweather before 1.14.0 moves the
        # airport's zone instead of removing CBs)
        logger.info("API: EFB PANIC: extra turbulence off, CB near dep/arr "
                    "remove, SIGMETs off")
        status = {'weather': weather_status(ctx)}
        await ctx.send_turb_cmd({'enabled': False})
        await ctx.send_fw_settings_cmd({
            **_fw_webui.cb_airport_mode_command('remove'),
            **_fw_sigmet.preset_command('off', status['weather']['sigmet_enabled'])})
        await asyncio.sleep(3)
        raise web.HTTPFound(page)

    @routes.post('/api/efb/defaults')
    async def handle_efb_defaults_post(_):
        # DEFAULTS: the settings /efb controls back to FrankenWeather's
        # startup values (config file + command line options); needs
        # FrankenWeather 2.1.4 or later (an older one logs an
        # unknown config_action and changes nothing)
        logger.info("API: EFB DEFAULTS: weather settings back to "
                    "FrankenWeather's startup values")
        await ctx.send_fw_settings_cmd({'config_action': 'startup'})
        await asyncio.sleep(3)
        raise web.HTTPFound(page)

    @routes.post('/api/efb/toggle')
    async def handle_efb_toggle_post(request):
        data = await request.post()
        field = data.get('field')
        if field in ('enroute_wind_enabled', 'disable_psx_sigmets'):
            await ctx.send_fw_settings_cmd({field: data.get('value') == '1'})
        raise web.HTTPFound(page)

    @routes.post('/api/efb/turb-toggle')
    async def handle_efb_turb_toggle_post(request):
        data = await request.post()
        if data.get('field') == 'enabled':
            await ctx.send_turb_cmd({'enabled': data.get('value') == '1'})
        raise web.HTTPFound(page)

    @routes.post('/api/efb/turb-profile')
    async def handle_efb_turb_profile_post(request):
        data = await request.post()
        if data.get('profile') in dict(TURB_PROFILES):
            logger.info("API: EFB turbulence profile -> %s", data.get('profile'))
            await ctx.send_turb_cmd({'profile': data.get('profile')})
        raise web.HTTPFound(page)


# The popups' JavaScript (PIREP, Help, and the router's errors and warnings)
POPUP_JS = (
    '<script>\n'
    # PIREP button: a popup that closes by itself after a few seconds
    # (or when tapped)
    'var efbPirepTimer = null;\n'
    'function efbPirepClose() {\n'
    '  document.getElementById("efb-pirep").style.display = "none";\n'
    '}\n'
    'function efbProblems() {\n'
    '  document.getElementById("efb-problems").style.display = "block";\n'
    '}\n'
    'function efbProblemsClose() {\n'
    '  document.getElementById("efb-problems").style.display = "none";\n'
    '}\n'
    'function efbHelp() {\n'
    '  document.getElementById("efb-help").style.display = "block";\n'
    '}\n'
    'function efbHelpClose() {\n'
    '  document.getElementById("efb-help").style.display = "none";\n'
    '}\n'
    'function efbPirep() {\n'
    '  fetch("/api/efb/pirep", {cache: "no-store"}).then(function(r) { return r.json(); })'
    '.then(function(p) {\n'
    '    var box = document.getElementById("efb-pirep");\n'
    '    box.innerHTML = p.html;\n'
    '    box.style.display = "block";\n'
    '    if (efbPirepTimer) clearTimeout(efbPirepTimer);\n'
    f'    efbPirepTimer = setTimeout(efbPirepClose, {PIREP_SHOW_MS});\n'
    '  }).catch(function() {});\n'
    '}\n'
    '</script>\n'
)


# ---------------------------------------------------------------------------
# frankenweather's own /efb page (its --web-port server); the router builds
# its own, with the router status added (webapi.py's _build_efb_page())
# ---------------------------------------------------------------------------

def _standalone_status_card(ctx, w):
    """Render frankenweather's own status card: its PSX connection and data sources."""
    psx = getattr(ctx, 'psx_connected', None)
    link_row = src_row('FrankenWeather ← PSX', '#22c55e' if psx else '#ef4444',
                       'connected' if psx else 'not connected')
    # datasources_section() starts with a separator line and its own heading
    return (
        '<div class="efb-card" id="efb-status-card">\n' +
        datasources_section(w, link_row).replace(
            '<div class="efb-section">', '<div>', 1) +
        '</div>\n'
    )


def _standalone_status(ctx):
    """Return /api/efb/status for frankenweather's own /efb page."""
    w = weather_status(ctx)
    loc = current_location_dict(ctx)
    return {
        'weather': w,
        'status_html': _standalone_status_card(ctx, w),
        'controls_html': controls_card(w),
        'help_html': help_html(w),
        'location_html': location_wx_card(loc, w, w['sigmet_hazards']),
    }


def build_page(ctx):
    """Build frankenweather's own /efb page.

    Weather status, the weather controls, extra turbulence and surface weather.
    """
    st = _standalone_status(ctx)
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="dark">\n'
        '<title>FrankenWeather EFB page (not the router)</title>\n' +
        CSS +
        '</head>\n<body>\n'
        # Not to be mistaken for the router's /efb (which has the router status)
        '<div style="margin-bottom:0.7rem;padding:0.5rem 0.9rem;border-radius:8px;'
        'background:#16233a;border:1px solid #3b7fe0;font-size:0.9rem">'
        "<b>FrankenWeather's own EFB page</b> &ndash; not the router's: no router "
        "status or router errors here. With a frankenrouter, use the router's "
        '<b>/efb</b> page instead.</div>\n'
        '<div class="efb-grid wide">' +
        st['status_html'] + st['controls_html'] + st['location_html'] +
        '</div>\n'
        '<div id="efb-pirep" class="efb-popup" onclick="efbPirepClose()"></div>\n'
        '<div id="efb-help" class="efb-popup" onclick="efbHelpClose()">'
        f'{st["help_html"]}</div>\n'
        '<div style="text-align:right;margin-top:0.5rem">'
        '<button type="button" class="efb-chip efb-chip-sm" onclick="efbPirep()">'
        'PIREP</button> '
        '<a href="/weather" class="efb-chip efb-chip-sm">Full weather control panel</a></div>\n'
        '<script>\n'
        'function efbPoll() {\n'
        '  fetch("/api/efb/status").then(function(r) { return r.json(); })'
        '.then(function(s) {\n'
        '    var ids = {"efb-status-card": s.status_html, "efb-controls-card": s.controls_html,\n'
        '               "efb-location-card": s.location_html};\n'
        '    for (var id in ids) {\n'
        '      var el = document.getElementById(id);\n'
        '      if (el) el.outerHTML = ids[id];\n'
        '    }\n'
        '    var hp = document.getElementById("efb-help");\n'
        '    if (hp && hp.style.display != "block") hp.innerHTML = s.help_html;\n'
        '  }).catch(function() {});\n'
        '}\n'
        'setInterval(efbPoll, 5000);\n'
        '</script>\n' +
        POPUP_JS +
        '</body>\n</html>\n'
    )


def register_page_routes(routes, ctx, logger):
    """Register frankenweather's own /efb page, its status API and its controls."""
    @routes.get('/efb')
    async def handle_efb_get(_):
        # Never a cached copy: the state changes all the time
        return web.Response(text=build_page(ctx), content_type='text/html',
                            headers={'Cache-Control': 'no-store'})

    @routes.get('/api/efb/status')
    async def handle_efb_status_get(_):
        return web.json_response(_standalone_status(ctx), headers={'Cache-Control': 'no-store'})

    register_control_routes(routes, ctx, logger)
