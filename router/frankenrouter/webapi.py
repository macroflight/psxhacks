"""Web API (REST + HTML UI) for frankenrouter."""
# pylint: disable=fixme,invalid-name,too-many-lines
import asyncio
import datetime
import html
import json
import pathlib
import re
import secrets
import signal
import statistics
import string
import sys
import textwrap
import time
import traceback
import urllib.parse

# Add psxhacks root to sys.path so fw_webui (first-party, at the root) can be imported.
_PSXHACKS = str(pathlib.Path(__file__).parent.parent.parent)
if _PSXHACKS not in sys.path:
    sys.path.insert(0, _PSXHACKS)

from aiohttp import web  # noqa: E402  pylint: disable=import-error,wrong-import-position

import fw_sigmet as _fw_sigmet  # noqa: E402  pylint: disable=wrong-import-position
import fw_webui as _fw_webui  # noqa: E402  pylint: disable=wrong-import-position

from . import variables as _variables  # noqa: E402  pylint: disable=wrong-import-position


_STATIC_DIR = pathlib.Path(__file__).parent / 'static'

# Dark EFB-style theme shared by all HTML pages.
# CSS braces are doubled ({{ / }}) because these strings are used as format templates.
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


_INDEX_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n<style>body {{ max-width: 64em; }}</style>\n</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Frankentech"></a>'
    '<h1>Frankenrouter &mdash; {this_sim}</h1>'
    '<div style="margin-left:auto">'
    '<a href="/efb" class="btn btn-gray btn-sm">EFB mode</a>'
    '<a href="/" class="btn btn-gray btn-sm">Refresh</a>'
    '</div>'
    '</div>\n'
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5em;align-items:start">\n'
    '<div>\n'
    '{critical_errors}'
    '{forced_disconnect_notice}'
    '<div class="card {upstream_class}">\n'
    '<table>\n'
    '<tr><td>Router type</td>'
    '<td class="val">{router_type}</td></tr>\n'
    '<tr><td>Upstream</td>'
    '<td class="val">{upstream_label}</td></tr>\n'
    '<tr><td>Connection</td>'
    '<td class="{upstream_class}">{upstream_status}</td></tr>\n'
    '{shared_cockpit_rows}'
    '</table>\n'
    '</div>\n'
    '{change_upstream_button}'
    '<a href="/weather" class="btn btn-gray">Weather</a>\n'
    '<a href="/utils" class="btn btn-gray">Utils</a>\n'
    '{sessionpwd_button}'
    '</div>\n'
    '<div>\n'
    '{observer_mode_notice}'
    '{master_buttons}'
    '{observer_mode_button}'
    '{shutdown_button}'
    '</div>\n'
    '</div>\n'
    '</body>\n</html>\n'
)

_UTILS_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Utils</h1>'
    '<div style="margin-left:auto">'
    '<a href="/" class="btn btn-gray btn-sm">Back</a>'
    '</div>'
    '</div>\n'
    '<a href="/utils/windimport" class="btn btn-blue">DLH wind import</a>\n'
    '<a href="/utils/events" class="btn btn-gray">Event log</a>\n'
    '<a href="/services" class="btn btn-gray">Master sim services</a>\n'
    '{sims_link}'
    '<form method="post" action="/api/utils/printer/reset" style="display:inline">'
    '<button class="btn btn-gray">Reset printer</button></form>\n'
    '<form method="post" action="/api/utils/hafap/reset" style="display:inline" '
    'onsubmit="return confirm(\'Reset HAFAP/CPDLC? This can interrupt any '
    'CPDLC message currently in progress.\')">'
    '<button class="btn btn-gray">HAFAP(CPDLC) reset</button></form>\n'
    '{global_bang_button}'
    '{reset_clients_link}'
    '{load_marker_pause_link}'
    '</body>\n</html>\n'
)

_GLOBAL_BANG_CONFIRM = (
    "Forcing a BANG is normally not needed, but might help syncing the "
    "sims up in some cases. Note: sending a bang can result in sound "
    "being played, etc. You have been warned"
)

_RESET_CLIENTS_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Reset clients</h1>'
    '<div style="margin-left:auto">'
    '<a href="/utils" class="btn btn-gray btn-sm">Back</a>'
    '</div>'
    '</div>\n'
    '<div class="card">\n'
    '<p style="margin:0">Select which connected clients should get a fresh '
    'load1/load2/load3 and full state refresh -- the same sequence a client '
    'gets on first connect. Use this if this sim has ended up in a bad '
    'state (e.g. after a situ load) and nothing else has fixed it. Normally '
    'unnoticeable, but may cause a brief visible glitch. Clients matching '
    'this router\'s client_reset_blacklist config start unchecked, but you '
    'can check them anyway if you really want to.</p>\n'
    '</div>\n'
    '{list_section}'
    '{result_section}'
    '</body>\n</html>\n'
)

_RESET_CLIENTS_CHECKBOX_ROW = (
    '<label style="display:block;margin:0.3em 0">'
    '<input type="checkbox" name="clients" value="{client_id}"{checked}> '
    '{display_name}'
    '</label>\n'
)

_RESET_CLIENTS_LIST_SECTION = (
    '<form method="post" action="/api/utils/reset_clients">\n'
    '<div class="btn-row">\n'
    "<button type=\"button\" class=\"btn btn-gray\" onclick=\""
    "document.querySelectorAll('input[name=clients]').forEach(function(cb) "
    "{{ cb.checked = true; }})\">Select all</button>\n"
    "<button type=\"button\" class=\"btn btn-gray\" onclick=\""
    "document.querySelectorAll('input[name=clients]').forEach(function(cb) "
    "{{ cb.checked = false; }})\">Deselect all</button>\n"
    '</div>\n'
    '{checkbox_rows}'
    '<div class="btn-row">\n'
    '<button type="submit" class="btn btn-green">Reset selected</button>\n'
    '</div>\n'
    '</form>\n'
)

_RESET_CLIENTS_EMPTY_SECTION = (
    '<div class="card warn">\n'
    '<p style="margin:0">No eligible clients connected.</p>\n'
    '</div>\n'
)

_RESET_CLIENTS_SENT_SECTION = (
    '<div class="card ok">\n'
    '<p style="margin:0">Reset sent to {count} client(s).</p>\n'
    '</div>\n'
    '<div class="btn-row">\n'
    '<a href="/utils/reset_clients" class="btn btn-gray">Back to list</a>\n'
    '</div>\n'
)

_LOAD_MARKER_PAUSE_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Load marker pause</h1>'
    '<div style="margin-left:auto">'
    '<a href="/utils" class="btn btn-gray btn-sm">Back</a>'
    '</div>'
    '</div>\n'
    '<div class="card">\n'
    '<p style="margin:0">Experimental, unproven mitigation for a PSX Main '
    'Client occasionally ending up with diverged state (wrong engine/gear/'
    'LNAV state, stuck attitude, ...) after a situ load or a fresh connect. '
    'When enabled, this router pauses briefly, both right before and right '
    'after sending each load1/load2/load3 marker to a real PSX Main Client '
    'or Server, restoring some of the small natural gap a live PSX Main '
    'Server would have. This setting is shared network-wide: changing it '
    'here broadcasts the new value to every connected slave sim.</p>\n'
    '</div>\n'
    '{result_section}'
    '<form method="post" action="/api/utils/load_marker_pause">\n'
    '<label class="toggle-row">'
    '<span class="toggle-switch">'
    '<input type="checkbox" name="enabled" value="1"{enabled_checked}>'
    '<span class="toggle-track"></span>'
    '</span>'
    '<span>Enabled</span>'
    '</label>\n'
    '<label for="load_marker_pause_delay">Pause duration (seconds)</label>\n'
    '<input type="text" id="load_marker_pause_delay" name="delay" value="{delay}">\n'
    '<div class="btn-row">\n'
    '<button type="submit" class="btn btn-green">Save</button>\n'
    '</div>\n'
    '</form>\n'
    '</body>\n</html>\n'
)

_LOAD_MARKER_PAUSE_SAVED_SECTION = (
    '<div class="card ok">\n'
    '<p style="margin:0">Saved and broadcast to the network.</p>\n'
    '</div>\n'
)

_LOAD_MARKER_PAUSE_ERROR_SECTION = (
    '<div class="card warn">\n'
    '<p style="margin:0">{message}</p>\n'
    '</div>\n'
)


def _service_action_buttons(key, display_name, running):
    """Build the confirm-gated button group for one service row.

    Only the actions that make sense for the service's current state are
    shown: just Start while stopped, or Stop and Restart while running.
    """
    actions = ('stop', 'restart') if running else ('start',)
    return ''.join(
        f'<form method="post" action="/api/services/{key}/{action}" '
        f'style="display:inline" '
        f'onsubmit="return confirm(\'{action.capitalize()} {display_name}?\')">'
        f'<button class="btn btn-gray btn-sm" style="width:auto;margin:0">'
        f'{action.capitalize()}</button></form> '
        for action in actions
    )


def _service_row_html(key, info):
    """Build one <tr> for the /services table."""
    display_name = info.get('display_name', key)
    running = bool(info.get('running'))
    state_color = '#22c55e' if running else '#9fb2cc'
    state_label = 'RUNNING' if running else 'stopped'
    buttons = _service_action_buttons(key, display_name, running)
    return (
        f'<tr><td>{display_name}</td>'
        f'<td style="color:{state_color}">{state_label}</td>'
        f'<td style="text-align:right;white-space:nowrap">{buttons}</td></tr>\n')


def _build_services_page(router, color_scheme):
    """Build the /services page: FrankenControl's monitored service status + controls.

    Entirely driven by whatever router.frankencontrol_status last cached (see
    cache_frankencontrol_addon() in frankenrouter.py) -- no service list is
    hardcoded here, so this stays in sync with frankencontrol.py's own
    SERVICES registry automatically, with no risk of the two drifting apart.
    """
    color, label, age = _efb_data_status(
        time.time(), router.frankencontrol_status_received_at, timeout_s=120.0)

    services = (router.frankencontrol_status or {}).get('services') or {}
    if services:
        ordered = sorted(
            services.items(), key=lambda kv: kv[1].get('display_name', kv[0]))
        rows = (_service_row_html(key, info) for key, info in ordered)
        body = (
            '<table>'
            '<tr><th style="text-align:left">Service</th>'
            '<th style="text-align:left">Status</th><th></th></tr>\n' +
            ''.join(rows) + '</table>\n')
    else:
        body = '<p>No data received from FrankenControl yet.</p>\n'

    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        f'<meta name="color-scheme" content="{color_scheme}" />\n'
        '<meta http-equiv="refresh" content="5">\n' +
        _COMMON_CSS.format() +
        '\n<style>body { max-width: 64em; }</style>\n'
        '</head>\n<body>\n'
        '<div class="page-title">'
        '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
        '<h1>Master sim services</h1>'
        '<div style="margin-left:auto">'
        '<a href="/services" class="btn btn-gray btn-sm">Refresh</a>'
        '<a href="/utils" class="btn btn-gray btn-sm">Back</a>'
        '</div>'
        '</div>\n'
        f'<p style="color:{color}">FrankenControl data: {label} ({age})</p>\n'
        '<p class="note">Auto-refreshes every 5 s. After starting, stopping, '
        'or restarting a service, it may take up to 60 s for its status '
        'here to update.</p>\n'
        '<p class="note">Not every service here is necessarily configured '
        'to start in your setup (e.g. FrankenTanker, psx_simlink_bridge) - '
        'a "stopped" status for one of those may be expected.</p>\n' +
        body +
        '</body>\n</html>\n'
    )


def _sim_row_html(uuid, info, elevation_source, traffic_source):
    """Build one <tr> for the /utils/sims table (a row we're allowed to disconnect)."""
    sim_name_raw = info.get('simulator_name', 'unknown')
    sim_name = html.escape(sim_name_raw)
    router_name = html.escape(info.get('router_name', 'unknown'))
    age = int(time.time() - info.get('received', time.time()))
    badges = ''
    if sim_name_raw == elevation_source:
        badges += ' <span title="Elevation master">⛰️</span>'
    if sim_name_raw == traffic_source:
        badges += ' <span title="Traffic master">✈️</span>'
    return (
        f'<tr><td>{sim_name}{badges}</td><td>{router_name}</td>'
        f'<td style="white-space:nowrap">{age}s ago</td>'
        '<td style="text-align:right;white-space:nowrap">'
        f'<form method="post" action="/api/sims/{uuid}/disconnect" style="display:inline">'
        '<input type="hidden" name="reason">'
        '<button type="button" class="btn btn-red btn-sm" style="width:auto;margin:0" onclick="'
        f'var r = prompt(\'Reason for disconnecting {sim_name}:\'); '
        'if (r !== null && r.trim() !== \'\') '
        '{ this.form.reason.value = r.trim(); this.form.submit(); }">'
        'Disconnect sim</button>'
        '</form></td></tr>\n')


def _latest_router_per_sim(routerinfo, own_sim):
    """Reduce routerinfo entries to one (most-recently-seen) per slave sim.

    Only type='slave' routers are considered -- a master (or standalone)
    sim can never act on DISCONNECT_SIM (see rules.py's edge-router
    gating), so listing one here would offer a button that silently does
    nothing. This also excludes our own sim. A sim can have more than one
    router connected (e.g. a multi-router shared cockpit) -- each shows up
    as its own ROUTERINFO entry, but "Disconnect sim" targets a sim by
    name, not a specific router, so this collapses them to a single row
    per sim rather than showing duplicates (or, worse, a disconnect
    button for another router within our *own* sim).
    """
    latest = {}
    for uuid, info in routerinfo.items():
        sim_name = info.get('simulator_name')
        if not sim_name or sim_name == own_sim or info.get('type') != 'slave':
            continue
        received = info.get('received', 0)
        if sim_name not in latest or received > latest[sim_name][1].get('received', 0):
            latest[sim_name] = (uuid, info)
    return latest.values()


def _build_sims_page(router, color_scheme):
    """Build the /utils/sims page.

    Lists all connected sims (from FRDP ROUTERINFO), with a confirm-gated
    "Disconnect sim" button next to each one except our own. Marks the
    current elevation/traffic master sim with a small badge. Auto-refreshes
    every 30s so a disconnect can be watched taking effect.
    """
    own_sim = router.config.identity.simulator
    elevation_source = router.sharedinfo.get('elevation_source_simulator', 'NOSIM')
    traffic_source = router.sharedinfo.get('traffic_source_simulator', 'NOSIM')
    rows = ''.join(
        _sim_row_html(uuid, info, elevation_source, traffic_source)
        for uuid, info in sorted(
            _latest_router_per_sim(router.routerinfo, own_sim),
            key=lambda kv: kv[1].get('simulator_name', ''))
    )
    body = (
        '<table>'
        '<tr><th style="text-align:left">Sim</th>'
        '<th style="text-align:left">Router</th>'
        '<th style="text-align:left">Last seen</th><th></th></tr>\n' +
        rows + '</table>\n'
        if rows else '<p>No other connected sims.</p>\n'
    )
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        f'<meta name="color-scheme" content="{color_scheme}" />\n'
        '<meta http-equiv="refresh" content="30">\n' +
        _COMMON_CSS.format() +
        # _COMMON_CSS's td:first-child rule (45% width, dimmed/smaller text)
        # is meant for a 2-column label/value table (used all over the rest
        # of this file) -- here it's a genuine 4-column data table, where
        # it squeezes "Router"/"Last seen"/the button column so badly that
        # "Last seen" wraps and collides with "Router". Reset it the same
        # way the /events page's table does.
        '\n<style>'
        'td:first-child { width: auto; color: inherit; font-size: inherit; padding-right: 0; }'
        'th, td { padding: 0.4em 0.6em 0.4em 0; }'
        '</style>\n'
        '</head>\n<body>\n'
        '<div class="page-title">'
        '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
        '<h1>Connected slave sims</h1>'
        '<div style="margin-left:auto">'
        '<a href="/utils/sims" class="btn btn-gray btn-sm">Refresh</a>'
        '<a href="/utils" class="btn btn-gray btn-sm">Back</a>'
        '</div>'
        '</div>\n'
        '<p class="note">Auto-refreshes every 30 s</p>\n' +
        body +
        '</body>\n</html>\n'
    )


# Basic Mode page (/basicmode/): everything a first-time shared cockpit
# flyer needs, on one page that polls /api/basicmode/status every second.
# Plain string (not a format template), so CSS/JS braces are not doubled;
# the only substitutions are the %%...%% markers in _build_basic_mode_page().
_BASIC_MODE_BODY = '''\
<style>
body { max-width: 52em; }
#portwarning {
    background: #7f1d1d; border: 3px solid #ef4444; border-radius: 0.6em;
    padding: 1.2em 1.4em; margin-bottom: 1em; color: #fff;
}
#portwarning h1 { font-size: 1.8em; margin-bottom: 0.5em; }
#portwarning p { font-size: 1.15em; line-height: 1.45; }
#portwarning .detail { font-size: 0.85em; color: #fecaca; }
.indicator { display: flex; align-items: center; gap: 0.8em; }
.dot { width: 1.4em; height: 1.4em; border-radius: 50%; flex-shrink: 0; background: #4b5563; }
.dot.green { background: #22c55e; box-shadow: 0 0 0.6em #22c55e; }
.dot.amber { background: #f59e0b; }
.dot.red { background: #ef4444; }
.indicator .main { font-size: 1.1em; font-weight: 600; }
.indicator .sub { font-size: 0.88em; color: #94a3b8; }
input[type=password] {
    width: 100%; padding: 0.65em; margin: 0.25em 0 0.75em;
    font-size: 1em; border-radius: 0.45em;
    border: 1px solid #374151; background: #111827; color: #f9fafb;
    font-family: inherit;
}
table.list td:first-child { width: auto; color: inherit; font-size: inherit; }
table.list th { text-align: left; color: #94a3b8; font-weight: 500; font-size: 0.88em; }
table.list th, table.list td { padding: 0.3em 0.8em 0.3em 0; }
.empty { color: #64748b; font-size: 0.9em; }
ol.checklist { padding-left: 0; list-style: none; counter-reset: step; }
ol.checklist li {
    counter-increment: step; display: flex; gap: 0.6em; align-items: flex-start;
    margin: 0.55em 0; line-height: 1.4;
}
ol.checklist li::before { content: counter(step) "."; color: #94a3b8; min-width: 1.4em; }
ol.checklist input { margin-top: 0.3em; flex-shrink: 0; }
ol.checklist .live { font-size: 0.85em; font-weight: 600; margin-left: 0.3em; }
.live-ok { color: #4ade80; } .live-no { color: #f87171; }
#errorbox { color: #fca5a5; font-size: 0.9em; margin: 0.3em 0; }
@keyframes flash { from { background: #475569; } to { background: #1c2033; } }
.card.flash { animation: flash 1.5s ease-out; }
button.btn:disabled { opacity: 0.6; cursor: wait; }
#connectform input:read-only { opacity: 0.55; }
@media (max-width: 40em) { .grid2 { grid-template-columns: 1fr; } }
</style>
</head>
<body>
<div class="page-title">
<img src="/static/frankentech.png" alt="Frankentech">
<h1>Frankenrouter &mdash; Basic Mode</h1>
<span class="note" style="margin-left:auto">Version %%VERSION%%</span>
</div>

<div id="portwarning" style="display:none">
<h1>&#9888; The router could not open port %%LISTEN_PORT%%</h1>
<p>This is almost always because your <b>PSX main server is running</b>
and is already using that port.</p>
<p><b>Stop your sim (including the PSX main server), then restart the
router.</b> This page also retries every few seconds and continues by
itself once the port is free.</p>
<p class="detail" id="porterror"></p>
</div>

<div id="main" style="display:none">

<div class="card" id="upstreamcard">
<div class="indicator">
<div class="dot" id="dot"></div>
<div><div class="main" id="upstreammain">Not connected</div>
<div class="sub" id="upstreamsub"></div></div>
</div>
</div>

<div class="grid2">
<div>
<h2>Master sim</h2>
<form id="connectform" onsubmit="return doToggle()">
<label for="crew">My crew code, 5 characters (other sims will see this)</label>
<input type="text" id="crew" maxlength="5" autocomplete="off" spellcheck="false"
 style="text-transform: uppercase; margin-bottom: 0.2em">
<p class="note" style="margin-top:0">If you do not have a PSCC crew code assigned, make one up.</p>
<label for="host">Hostname or IP address of the master sim</label>
<input type="text" id="host" autocomplete="off" spellcheck="false">
<label for="port">Port</label>
<input type="text" id="port" inputmode="numeric" autocomplete="off">
<label for="password">Password (leave empty if the master sim has none)</label>
<input type="password" id="password" autocomplete="off">
<div id="errorbox"></div>
<button type="submit" class="btn btn-green" id="connbtn">Connect</button>
</form>
</div>

<div>
<h2>What to do</h2>
<ol class="checklist">
<li><input type="checkbox"><span>Always start your router first.</span></li>
<li><input type="checkbox"><span>Enter your crew code, and the IP address (or hostname), port
and password you got from the master sim, and press <b>Connect</b>.</span></li>
<li><input type="checkbox"><span>Check that the router is connected to the master sim: look for
the <b>green</b> indicator above.<span class="live" id="live3"></span></span></li>
<li><input type="checkbox"><span>Start your sim. Your PSX main clients and addons should
automatically connect to the router. Verify that you see them in the list of
connected clients below.</span></li>
<li><input type="checkbox"><span>Make sure you do <b>not</b> run BACARS, a VATSIM CPDLC
client, FrankenWeather, SRSL (except in client mode) or CMC-PSX &ndash; in a
shared cockpit, these only run in the master sim.</span></li>
<li><input type="checkbox"><span>Find your PSX main server and reconfigure it. On the
<b>Network &gt; Main</b> page of Instructor, switch it from &ldquo;The main
server&rdquo; to &ldquo;A main client&rdquo;, connecting to <b>127.0.0.1</b>
port <b>%%LISTEN_PORT%%</b> (Preferences &gt; Basics &gt; Main network).</span></li>
<li><input type="checkbox"><span>Verify that your PSX main client shows up in the
router's client list below. If a PSX main client does not show up, restart
it, or go into <b>Instructor &gt; Network &gt; Main</b> and make sure it is
&ldquo;A main client&rdquo;.</span></li>
<li><input type="checkbox"><span>You should now be connected. Try moving e.g. the MCP ALT
knob &ndash; you should see the MCP ALT number change. The other connected
sims should see the same.</span></li>
<li><input type="checkbox"><span>Fly!</span></li>
</ol>
</div>
</div>

<h2>Your connected clients</h2>
<div class="card" id="clients"><span class="empty">No clients connected yet.</span></div>

<h2>Other slave sims connected to the master sim</h2>
<div class="card" id="sims"><span class="empty">No other sims seen yet.</span></div>

<p class="note"><a href="/" style="color:#64748b">Full router web interface</a></p>
</div>

<div id="shutdown">
<hr>
<button type="button" class="btn btn-red" onclick="doShutdown()">Shutdown router</button>
</div>
<div id="stopped" class="card warn" style="display:none">
<h1 id="stoppedtitle">The router is shutting down...</h1>
<p style="font-size:1.1em;line-height:1.45">Your sim is being disconnected from the
shared cockpit. <b>The router will not start again by itself</b>: to use it again,
you must restart it manually (e.g. double-click the router program again), then
reload this page.</p>
<p class="note" id="stoppedstatus">Waiting for the router to stop...</p>
</div>

<script>
function esc(s) {
  return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
    return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
  });
}
function dur(s) {
  s = Math.max(0, Math.floor(s));
  var h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), sec = s % 60;
  if (h) return h + 'h ' + m + 'm';
  if (m) return m + 'm ' + sec + 's';
  return sec + 's';
}
function remembered(key) {
  try { return window.localStorage.getItem('basicmode.' + key); } catch (e) { return null; }
}
function remember(key, value) {
  try { window.localStorage.setItem('basicmode.' + key, value); } catch (e) { /* ignore */ }
}
var fieldsFilled = false;
var stopped = false;
// Set by the Disconnect button (until the next Connect), so the indicator
// can say "Disconnected" rather than the same "Not connected" as at startup
var userDisconnected = false;
// Flash the indicator card for a moment whenever its status changes
var lastIndicator = null, flashUntil = 0;
// The one Connect/Disconnect button: "Disconnect" whenever the router is
// connected or trying to connect, "Connect" when it's idle
var active = false, pending = false;

function render(st) {
  // A status reply already in flight when Shutdown was pressed must not
  // bring the main page back over the shutdown message
  if (stopped) return;
  var bound = st.listen.bound;
  document.getElementById('portwarning').style.display = bound ? 'none' : 'block';
  document.getElementById('main').style.display = bound ? 'block' : 'none';
  document.getElementById('porterror').textContent = st.listen.error ? 'Details: ' + st.listen.error : '';
  if (!bound) return;

  if (!fieldsFilled) {
    document.getElementById('crew').value = remembered('crew') || '';
    document.getElementById('host').value = remembered('host') || st.upstream.host || '';
    document.getElementById('port').value = remembered('port') || st.upstream.port || '';
    fieldsFilled = true;
  }

  var u = st.upstream, dot = document.getElementById('dot');
  var main = document.getElementById('upstreammain'), sub = document.getElementById('upstreamsub');
  var card = document.getElementById('upstreamcard');
  card.className = 'card';
  if (u.state === 'ok') {
    dot.className = 'dot green'; card.className = 'card ok';
    main.textContent = 'Connected to ' + u.name;
    sub.textContent = u.host + ':' + u.port + ', connected for ' + dur(u.seconds);
  } else if (u.state === 'connected') {
    dot.className = 'dot amber';
    main.textContent = 'Connected to ' + u.name + ', waiting for data...';
    sub.textContent = u.host + ':' + u.port + ', connected for ' + dur(u.seconds);
  } else if (u.state === 'connecting') {
    dot.className = 'dot ' + (u.error ? 'red' : 'amber');
    if (u.error) card.className = 'card warn';
    main.textContent = 'Connecting to ' + u.host + ':' + u.port + '...';
    sub.textContent = u.error || '';
  } else if (userDisconnected && !u.error) {
    dot.className = 'dot red'; card.className = 'card warn';
    main.textContent = 'Disconnected from the master sim';
    sub.textContent = 'Press Connect to connect again.';
  } else {
    dot.className = 'dot' + (u.error ? ' red' : '');
    if (u.error) card.className = 'card warn';
    main.textContent = 'Not connected';
    sub.textContent = u.error || 'Enter the master sim details below and press Connect.';
  }
  var indicator = dot.className + '|' + main.textContent;
  if (lastIndicator !== null && indicator !== lastIndicator) flashUntil = Date.now() + 1500;
  lastIndicator = indicator;
  if (Date.now() < flashUntil) card.className += ' flash';
  if (!pending) {
    active = u.state !== 'idle';
    var btn = document.getElementById('connbtn');
    btn.textContent = active ? 'Disconnect' : 'Connect';
    btn.className = 'btn ' + (active ? 'btn-red' : 'btn-green');
    // Disconnect first to change these
    ['crew', 'host', 'port', 'password'].forEach(function (id) {
      document.getElementById(id).readOnly = active;
    });
  }

  var live3 = document.getElementById('live3');
  live3.textContent = u.state === 'ok' ? '\\u2714 connected' : '\\u2718 not yet';
  live3.className = 'live ' + (u.state === 'ok' ? 'live-ok' : 'live-no');

  var rows = st.clients.map(function (c) {
    return '<tr><td>' + esc(c.ip) + '</td><td>' + esc(c.long || '-') + '</td><td>' +
      esc(c.short || '-') + '</td><td>' + dur(c.seconds) + '</td></tr>';
  }).join('');
  document.getElementById('clients').innerHTML = rows ?
    '<table class="list"><tr><th>IP</th><th>Name</th><th>Short name</th><th>Connected</th></tr>' +
    rows + '</table>' : '<span class="empty">No clients connected yet.</span>';

  rows = st.sims.map(function (s) {
    return '<tr><td>' + esc(s.sim) + '</td><td>' + esc(s.router) + '</td><td>' +
      dur(s.last_seen) + ' ago</td></tr>';
  }).join('');
  document.getElementById('sims').innerHTML = rows ?
    '<table class="list"><tr><th>Sim</th><th>Router</th><th>Last seen</th></tr>' +
    rows + '</table>' : '<span class="empty">No other sims seen yet.</span>';
}

function refresh() {
  return fetch('/api/basicmode/status', {cache: 'no-store'})
    .then(function (r) { return r.json(); })
    .then(render);
}

function poll() {
  refresh()
    .catch(function () { /* router restarting; try again */ })
    .finally(function () { if (!stopped) setTimeout(poll, 1000); });
}

function post(url, body) {
  return fetch(url, {method: 'POST', headers: {'Content-Type': 'application/json'},
                     body: JSON.stringify(body || {})})
    .then(function (r) { return r.json(); });
}

// Show "Connecting..."/"Disconnecting..." on the button until the router
// answers, then refresh the status straight away so the button flips at once
function busy(text) {
  var btn = document.getElementById('connbtn');
  pending = true; btn.disabled = true; btn.textContent = text;
  return function () {
    btn.disabled = false;
    refresh().catch(function () { /* next poll will retry */ })
      .finally(function () { pending = false; });
  };
}

function doToggle() {
  if (active) doDisconnect(); else doConnect();
  return false;
}

function doConnect() {
  var crew = document.getElementById('crew').value.trim().toUpperCase();
  var host = document.getElementById('host').value.trim();
  var port = document.getElementById('port').value.trim();
  var password = document.getElementById('password').value;
  var errorbox = document.getElementById('errorbox');
  errorbox.textContent = '';
  var done = busy('Connecting...');
  post('/api/basicmode/connect', {crew_code: crew, host: host, port: port, password: password})
    .then(function (res) {
      if (res.error) { errorbox.textContent = res.error; return; }
      userDisconnected = false;
      remember('crew', crew); remember('host', host); remember('port', port);
    })
    .catch(function () { errorbox.textContent = 'Could not reach the router.'; })
    .finally(done);
}

function doDisconnect() {
  var errorbox = document.getElementById('errorbox');
  errorbox.textContent = '';
  var done = busy('Disconnecting...');
  post('/api/basicmode/disconnect')
    .then(function () { userDisconnected = true; })
    .catch(function () { errorbox.textContent = 'Could not reach the router.'; })
    .finally(done);
}

function doShutdown() {
  if (!window.confirm('Shut down the router? Your sim will be disconnected from the shared cockpit.')) return;
  stopped = true;
  // Show the "shutting down, restart manually" page first: replace the
  // whole page with just that message, nothing else
  var box = document.getElementById('stopped');
  box.style.display = 'block';
  document.body.innerHTML = '';
  document.body.appendChild(box);
  window.scrollTo(0, 0);
  // ...then shut down, and confirm once the router no longer answers
  setTimeout(function () {
    fetch('/api/shutdown/yes', {method: 'POST'}).catch(function () { /* already gone */ });
    setTimeout(waitForShutdown, 1000);
  }, 300);
}

function waitForShutdown() {
  fetch('/api/basicmode/status', {cache: 'no-store'})
    .then(function () { setTimeout(waitForShutdown, 1000); })
    .catch(function () {
      document.getElementById('stoppedtitle').textContent = 'The router has shut down.';
      document.getElementById('stoppedstatus').textContent =
        'You can close this page. Restart the router manually to use it again.';
    });
}

poll();
</script>
</body>
</html>
'''


def _basic_mode_status(router):
    """Return the /api/basicmode/status dict (everything the Basic Mode page shows)."""
    now = time.perf_counter()
    upstream = {
        'host': router.config.upstream.host,
        'port': router.config.upstream.port,
        'error': router.upstream_last_error,
    }
    if router.is_upstream_connected():
        up = router.upstream
        # Green only once data is actually flowing: the upstream has
        # welcomed us (load3) and sent something on this connection.
        upstream.update({
            'state': 'ok' if router.upstream_data_flowing() else 'connected',
            # "sim/router" once the upstream router has identified itself
            'name': (up.display_name if up.simulator_name != 'unknown sim'
                     else f"{up.ip}:{up.port}"),
            'seconds': now - up.connected_at,
        })
    elif router.upstream_disabled:
        upstream['state'] = 'idle'
    else:
        upstream['state'] = 'connecting'
    clients = [
        {
            'ip': client.ip,
            'short': client.client_provided_id,
            'long': client.client_provided_display_name or client.display_name,
            'seconds': now - client.connected_at,
        }
        for client in router.clients.values()
    ]
    sims = [
        {
            'sim': info.get('simulator_name', 'unknown'),
            'router': info.get('router_name', 'unknown'),
            'last_seen': time.time() - info.get('received', time.time()),
        }
        for _uuid, info in sorted(
            _latest_router_per_sim(router.routerinfo, router.config.identity.simulator),
            key=lambda kv: kv[1].get('simulator_name', ''))
    ]
    return {
        'basic_mode': router.basic_mode,
        'listen': {
            'port': router.config.listen.port,
            'bound': router.proxy_server is not None,
            'error': router.listen_error,
        },
        'upstream': upstream,
        'clients': clients,
        'sims': sims,
    }


def _build_basic_mode_page(router):
    """Build the /basicmode/ page."""
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        f'<meta name="color-scheme" content="{router.config.listen.rest_api_color_scheme}" />\n'
        '<title>Frankenrouter Basic Mode</title>\n' +
        _COMMON_CSS.format() + '\n' +
        _BASIC_MODE_BODY
        .replace('%%LISTEN_PORT%%', str(router.config.listen.port))
        .replace('%%VERSION%%', html.escape(router.version))
    )


def _parse_basic_mode_connect(data):
    """Validate a Basic Mode connect request.

    Returns (request, None) with request = {crew_code, host, port,
    password}, or (None, error message for the user).
    """
    crew_code = str(data.get('crew_code') or '').strip().upper()
    host = str(data.get('host') or '').strip()
    port_text = str(data.get('port') or '').strip()
    password = str(data.get('password') or '')
    if not re.match(r'^[A-Z0-9]{5}$', crew_code):
        return None, "Enter your crew code: exactly 5 letters or digits."
    if not host:
        return None, "Enter the hostname or IP address of the master sim."
    if not re.match(r'^[A-Za-z0-9.:\-\[\]]+$', host):
        return None, "That doesn't look like a hostname or IP address."
    if not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        return None, "The port must be a number between 1 and 65535."
    if password and not re.match(r'^[\x21-\x7e]+$', password):
        return None, "The password may only contain printable ASCII characters, no spaces."
    return {'crew_code': crew_code, 'host': host, 'port': int(port_text),
            'password': password}, None


_WINDIMPORT_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n<style>textarea.mono {{ font-family: monospace; font-size: 0.82em; }}</style>\n'
    '</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>DLH wind import</h1>'
    '<div style="margin-left:auto">'
    '<a href="/utils" class="btn btn-gray btn-sm">Back</a>'
    '</div>'
    '</div>\n'
    '<div class="card">\n'
    '<ol style="margin:0;padding-left:1.3em">\n'
    '<li>Open your flight plan in SimBrief</li>\n'
    '<li>Under <b>Briefing Preview</b>, click <b>Show Details</b></li>\n'
    '<li>Click <b>Copy</b></li>\n'
    '<li>Paste the data into this page</li>\n'
    '<li>Click <b>Import</b></li>\n'
    '<li>Click <b>Send to PSX</b> to send the wind data to PSX</li>\n'
    '</ol>\n'
    '</div>\n'
    '{error_html}'
    '{ofp_section}'
    '{result_section}'
    '</body>\n</html>\n'
)

_WINDIMPORT_OFP_SECTION = (
    '<form method="post" action="/api/utils/windimport">\n'
    '<label for="ofp">Flight plan (OFP) text</label>\n'
    '<textarea id="ofp" name="ofp" class="mono" rows="20"'
    ' style="min-height:12em">{ofp_value}</textarea>\n'
    '<div class="btn-row">\n'
    '<button type="submit" class="btn btn-blue">Import</button>\n'
    '<button type="button" class="btn btn-gray"'
    ' onclick="document.getElementById(\'ofp\').value=\'\'">Clear</button>\n'
    '</div>\n'
    '</form>\n'
)

_WINDIMPORT_RESULT_SECTION = (
    '<div class="card ok">\n'
    '<p style="margin:0">Wind corridor data parsed successfully.</p>\n'
    '</div>\n'
    '<label for="corridor">Wind corridor data</label>\n'
    '<textarea id="corridor" class="mono" rows="20" readonly'
    ' style="min-height:12em">{corridor_display}</textarea>\n'
    '<form method="post" action="/api/utils/windimport/send">\n'
    '<input type="hidden" name="corridor" value="{corridor_value}">\n'
    '<div class="btn-row">\n'
    '<button type="submit" class="btn btn-green">Send to PSX</button>\n'
    '<a href="/utils/windimport" class="btn btn-gray">Start over</a>\n'
    '</div>\n'
    '</form>\n'
)

_WINDIMPORT_SENT_SECTION = (
    '<div class="card ok">\n'
    '<p style="margin:0">Wind corridor sent to PSX.</p>\n'
    '</div>\n'
    '<div class="btn-row">\n'
    '<a href="/utils/windimport" class="btn btn-gray">Import another flight plan</a>\n'
    '</div>\n'
)

_SHUTDOWN_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Shutdown router</h1>'
    '</div>\n'
    '<div class="card warn">\n'
    '<p style="margin:0">All connected clients will be disconnected.</p>\n'
    '</div>\n'
    '<form action="/api/shutdown/yes" method="post">\n'
    '<input type="submit" value="Confirm shutdown" class="btn-red">\n'
    '</form>\n'
    '<hr>\n'
    '<a href="/" class="btn btn-gray">Cancel</a>\n'
    '</body>\n</html>\n'
)

_SHUTDOWN_CONFIRM_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Router shutting down</h1>'
    '</div>\n'
    '<p class="note">The router is shutting down. You can close this window.</p>\n'
    '</body>\n</html>\n'
)

_FILTER_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Filter source control</h1>'
    '</div>\n'
    '{network_source_section}'
    '<hr>\n'
    '<a href="/filter" class="btn btn-gray">Refresh</a>\n'
    '<a href="/" class="btn btn-gray">Back</a>\n'
    '</body>\n</html>\n'
)

_FILTER_PAGE_NETWORK_SOURCE_SECTION = (
    '<div class="card ok">\n'
    '<table>\n'
    '<tr><td>This sim</td><td class="val">{this_sim}</td></tr>\n'
    '<tr><td>Elevation master</td><td class="val">{elevation_source}</td></tr>\n'
    '<tr><td>Traffic master</td><td class="val">{traffic_source}</td></tr>\n'
    '</table>\n'
    '</div>\n'
    '<h2>Elevation</h2>\n'
    '<a href="/api/filter/elevation/start_sending" class="btn btn-amber">'
    'Make me elevation master</a>\n'
    '<a href="/api/filter/elevation/stop_sending" class="btn btn-gray">'
    'Stop sending elevation data</a>\n'
    '<h2>Traffic</h2>\n'
    '<a href="/api/filter/traffic/start_sending" class="btn btn-amber">'
    'Make me traffic master</a>\n'
    '<a href="/api/filter/traffic/stop_sending" class="btn btn-gray">'
    'Stop sending traffic data</a>\n'
)

_FILTER_PAGE_NO_CONTROLS = (
    '<p class="note">Filter source control is only available when connected to a master sim.</p>\n'
)


_UPSTREAM_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n<script>\n'
    'function fillSessionPwd(form) {{\n'
    '  var pwd = prompt("Session password for " + form.host.value + ":" + form.port.value);\n'
    '  if (!pwd) return false;\n'
    '  form.password.value = pwd;\n'
    '  return true;\n'
    '}}\n'
    '</script>\n'
    '<style>body {{ max-width: 64em; }}</style>\n'
    '</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Upstream connection</h1>'
    '<div style="margin-left:auto;display:flex;gap:0.5em">'
    '<a href="/upstream" class="btn btn-gray btn-sm">Refresh</a>'
    '<a href="/" class="btn btn-gray btn-sm">Back</a>'
    '</div>'
    '</div>\n'
    '<div class="card {status_class}">\n'
    '<table>\n'
    '{current_rows}'
    '</table>\n'
    '</div>\n'
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5em;align-items:start">\n'
    '<div>\n'
    '<h2>Connect manually</h2>\n'
    '<form action="/api/upstream" method="post">\n'
    '<label for="host">Host</label>\n'
    '<input type="text" id="host" name="host" value="{host}">\n'
    '<label for="port">Port</label>\n'
    '<input type="text" id="port" name="port" value="{port}">\n'
    '<label for="password">Password</label>\n'
    '<input type="text" id="password" name="password" value="{password}">\n'
    '<input type="submit" value="Connect" class="btn-blue">\n'
    '</form>\n'
    '</div>\n'
    '<div>\n'
    '{presets}'
    '</div>\n'
    '</div>\n'
    '</body>\n</html>\n'
)

_UPSTREAM_PAGE_PRESET_CONNECT = (
    '<form action="/api/upstream" method="post">\n'
    '<input type="hidden" name="host" value="{host}">\n'
    '<input type="hidden" name="port" value="{port}">\n'
    '<input type="hidden" name="password" value="{password}">\n'
    '<button type="submit" class="btn btn-gray">'
    'Connect to {preset_name}<br>'
    '<span style="font-size:0.8em;font-weight:400">{host}:{port}</span>'
    '</button>\n'
    '</form>\n'
)

_UPSTREAM_PAGE_PRESET_SESSION_PWD = (
    '<form action="/api/upstream" method="post" onsubmit="return fillSessionPwd(this)">\n'
    '<input type="hidden" name="host" value="{host}">\n'
    '<input type="hidden" name="port" value="{port}">\n'
    '<input type="hidden" name="password" value="">\n'
    '<button type="submit" class="btn btn-blue">'
    'Connect to {preset_name}<br>'
    '<span style="font-size:0.8em;font-weight:400">with session password</span>'
    '</button>\n'
    '</form>\n'
)

_SESSION_PASSWORD_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n<script>\n'
    'function copyPassword(id, btn_id) {{\n'
    '  var pwd = document.getElementById(id).textContent;\n'
    '  var btn = document.getElementById(btn_id);\n'
    '  function flash(text) {{\n'
    '    btn.textContent = text;\n'
    '    setTimeout(function() {{ btn.textContent = "Copy to clipboard"; }}, 2000);\n'
    '  }}\n'
    '  // navigator.clipboard only exists in secure contexts (HTTPS or\n'
    '  // localhost); the router UI is usually reached over plain HTTP on\n'
    '  // a LAN address, so fall back to the legacy execCommand path.\n'
    '  function legacyCopy() {{\n'
    '    var ta = document.createElement("textarea");\n'
    '    ta.value = pwd;\n'
    '    ta.setAttribute("readonly", "");\n'
    '    ta.style.position = "fixed";\n'
    '    ta.style.opacity = "0";\n'
    '    document.body.appendChild(ta);\n'
    '    ta.select();\n'
    '    var ok = false;\n'
    '    try {{ ok = document.execCommand("copy"); }} catch (e) {{ ok = false; }}\n'
    '    document.body.removeChild(ta);\n'
    '    flash(ok ? "Copied!" : "Copy failed");\n'
    '  }}\n'
    '  if (navigator.clipboard && window.isSecureContext) {{\n'
    '    navigator.clipboard.writeText(pwd).then(function() {{\n'
    '      flash("Copied!");\n'
    '    }}, legacyCopy);\n'
    '  }} else {{\n'
    '    legacyCopy();\n'
    '  }}\n'
    '}}\n'
    '</script>\n'
    '</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Session passwords</h1>'
    '</div>\n'
    '<h2>Full access</h2>\n'
    '<div class="card ok">\n'
    '<p style="margin:0">Grants full read/write access to the sim.</p>\n'
    '</div>\n'
    '{password_section}'
    '<h2>Observer access</h2>\n'
    '<div class="card ok">\n'
    '<p style="margin:0">Grants read-only observer access to the sim.</p>\n'
    '</div>\n'
    '{observer_password_section}'
    '<hr>\n'
    '<a href="/" class="btn btn-gray">Back</a>\n'
    '</body>\n</html>\n'
)

_SESSION_PASSWORD_SET_SECTION = (
    '<div class="card ok">\n'
    '<p class="note" style="margin:0 0 0.4em">Current session password</p>\n'
    '<p id="session_pwd" style="font-size:1.3em;font-weight:600;font-family:monospace;'
    'letter-spacing:0.1em;margin:0">{password}</p>\n'
    '</div>\n'
    '<button type="button" id="copy_btn"'
    ' onclick="copyPassword(\'session_pwd\', \'copy_btn\')" class="btn btn-gray">'
    'Copy to clipboard</button>\n'
    '<form action="/api/sessionpwd/remove" method="post">\n'
    '<input type="submit" value="Remove session password" class="btn-red">\n'
    '</form>\n'
)

_SESSION_PASSWORD_UNSET_SECTION = (
    '<a href="/api/sessionpwd/generate" class="btn btn-green">'
    'Generate session password</a>\n'
)

_OBSERVER_SESSION_PASSWORD_SET_SECTION = (
    '<div class="card ok">\n'
    '<p class="note" style="margin:0 0 0.4em">Current observer session password</p>\n'
    '<p id="observer_pwd" style="font-size:1.3em;font-weight:600;font-family:monospace;'
    'letter-spacing:0.1em;margin:0">{password}</p>\n'
    '</div>\n'
    '<button type="button" id="observer_copy_btn"'
    ' onclick="copyPassword(\'observer_pwd\', \'observer_copy_btn\')" class="btn btn-gray">'
    'Copy to clipboard</button>\n'
    '<form action="/api/observerpwd/remove" method="post">\n'
    '<input type="submit" value="Remove observer session password" class="btn-red">\n'
    '</form>\n'
)

_OBSERVER_SESSION_PASSWORD_UNSET_SECTION = (
    '<a href="/api/observerpwd/generate" class="btn btn-green">'
    'Generate observer session password</a>\n'
)


def _fc_buttons_html(pilot_flying, own_sim):
    """Return flight control filter button HTML for the index page."""
    if pilot_flying == own_sim:
        inner = (
            '<a href="/api/flightcontrols/no_control_locks" class="btn btn-green">'
            'No flight control filters</a>\n'
            '<a href="/api/flightcontrols/all_control_locks" class="btn btn-red">'
            'Filter all flight controls</a>\n'
        )
    elif pilot_flying == 'NO_CONTROL_LOCKS':
        inner = (
            '<a href="/api/flightcontrols/all_control_locks" class="btn btn-red">'
            'Filter all flight controls</a>\n'
            '<a href="/api/flightcontrols/my_controls" class="btn btn-amber">'
            'My controls!</a>\n'
        )
    else:
        inner = (
            '<a href="/api/flightcontrols/no_control_locks" class="btn btn-green">'
            'No flight control filters</a>\n'
            '<a href="/api/flightcontrols/my_controls" class="btn btn-amber">'
            'My controls!</a>\n'
        )
    return f'<div class="btn-row">{inner}</div>\n'


def _evt_fmt_ts(ts, now):
    """Format a unix timestamp as (HH:MM:SS, age_string) in UTC."""
    try:
        t = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        age_s = now - ts
        if age_s < 60:
            age = f'{int(age_s)}s ago'
        elif age_s < 3600:
            age = f'{int(age_s / 60)}m ago'
        else:
            age = f'{int(age_s / 3600)}h ago'
        return t.strftime('%H:%M:%S'), age
    except (TypeError, ValueError, OSError):
        return '?', '?'


def _evt_describe(evt, get_var_name):  # pylint: disable=too-many-return-statements,too-many-locals,too-many-statements
    """Return (description, raw_str) for a sim event dict."""
    etype = evt.get('type', '?')
    if etype == 'var_change':
        key = evt.get('key', '?')
        value = evt.get('value', '?')
        prev = evt.get('prev')
        name = get_var_name(key)
        source = evt.get('source', '')
        src_str = f' (from {source})' if source else ''
        prev_str = f', was: {prev}' if prev is not None else ''
        return f'{name}{src_str}: {value}{prev_str}', f'{key}={value}'
    if etype == 'mcp_window':
        key = evt.get('key', '?')
        value = evt.get('value', '?')
        prev = evt.get('prev')
        name = get_var_name(key)

        def _spd_disp(v):
            if key == 'Qi32' and v is not None:
                try:
                    n = int(v)
                    if n > 950:
                        return '---'
                    if n >= 400:
                        return f'M{n / 1000:.3f}'
                    return f'{n} kt'
                except (ValueError, TypeError):
                    pass
            return str(v) if v is not None else None

        disp_value = _spd_disp(value)
        disp_prev = _spd_disp(prev)
        prev_str = f', was: {disp_prev}' if prev is not None else ''
        return f'MCP window — {name}: {disp_value}{prev_str}', f'{key}={disp_value}'
    if etype == 'fma_change':
        thr = evt.get('thr') or '—'
        roll = evt.get('roll') or '—'
        pitch = evt.get('pitch') or '—'
        roll_armed = evt.get('roll_armed', '')
        pitch_armed = evt.get('pitch_armed', '')
        armed_str = (f' (armed: {roll_armed or "—"} / {pitch_armed or "—"})'
                     if roll_armed or pitch_armed else '')
        return f'FMA — A/T: {thr} | ROL: {roll} | PTH: {pitch}{armed_str}', ''
    if etype == 'pnf_mode_change':
        value = evt.get('value', 0)
        prev = evt.get('prev', 0)
        labels_new = _variables.pnf_mode_labels(value)
        labels_prev = _variables.pnf_mode_labels(prev)
        detail = f'{labels_prev or ["(none)"]} → {labels_new or ["(none)"]}'
        return 'PNF mode changed', detail
    if etype in ('bang', 'start', 'load1', 'load2', 'load3'):
        source = evt.get('source', '')
        src_str = f' from {source}' if source else ''
        labels = {
            'bang': 'Client reconnect (bang)',
            'start': 'Client start',
            'load1': 'Situation load started',
            'load2': 'Situation loading',
            'load3': 'Situation load complete',
        }
        return f'{labels.get(etype, etype)}{src_str}', ''
    if etype == 'sharedinfo_change':
        field = evt.get('field', '?')
        value = evt.get('value', '?')
        prev = evt.get('prev', '?')
        reason = evt.get('reason', '')
        reason_str = f' ({reason})' if reason else ''
        labels = {
            'pilot_flying_simulator': 'Flight controls',
            'elevation_source_simulator': 'Elevation master',
            'traffic_source_simulator': 'Traffic master',
        }
        label = labels.get(field, field)
        return f'{label}: {prev} → {value}{reason_str}', ''
    if etype in ('ingress_filtered', 'egress_filtered'):
        key = evt.get('key', '?')
        value = evt.get('value', '?')
        name = get_var_name(key)
        source = evt.get('source', '')
        reason = evt.get('reason', '')
        src_str = f' (from {source})' if source else ''
        ftype = 'Ingress' if etype == 'ingress_filtered' else 'Egress'
        return (
            f'{ftype} filtered — {name}{src_str}{": " + reason if reason else ""}',
            f'{key}={value}')
    if etype == 'client_connected':
        client_name = evt.get('client_name', '?')
        if evt.get('is_frankenrouter'):
            return f'Frankenrouter connected: {client_name} ({evt.get("client_sim", "")})', ''
        return f'Client connected: {client_name}', ''
    if etype == 'upstream_connected':
        host = evt.get('upstream_host', '?')
        port = evt.get('upstream_port', '?')
        return f'Connected to upstream {host}:{port}', ''
    return etype, ''


def _evt_row_html(evt, own_sim, own_router, now, get_var_name):  # pylint: disable=too-many-locals
    """Return an HTML <tr> string for one event row."""
    ts = evt.get('ts', 0)
    sim = evt.get('sim', '?')
    rtr = evt.get('router', '?')
    received_at = evt.get('received_at')
    ts_str, age_str = _evt_fmt_ts(ts, now)
    desc, raw = _evt_describe(evt, get_var_name)
    is_own = sim == own_sim and rtr == own_router
    src_color = '#94a3b8' if is_own else '#60a5fa'
    star = '★ ' if is_own else ''
    recv_note = ''
    if received_at is not None and abs(received_at - ts) > 5:
        recv_note = (
            f' <span style="color:#64748b;font-size:0.82em">'
            f'(rcvd {received_at - ts:+.0f}s)</span>')
    raw_cell = (
        f'<td style="font-family:monospace;font-size:0.82em;color:#64748b">{raw}</td>'
        if raw else '<td></td>')
    return (
        f'<tr>'
        f'<td style="white-space:nowrap">'
        f'<span style="font-family:monospace">{ts_str}</span>'
        f'<br><span style="color:#64748b;font-size:0.82em">{age_str}{recv_note}</span>'
        f'</td>'
        f'<td style="color:{src_color};font-size:0.88em;white-space:nowrap">'
        f'{star}{sim}<br><span style="color:#475569">{rtr}</span></td>'
        f'<td>{desc}</td>'
        f'{raw_cell}'
        f'</tr>\n'
    )


def _build_events_page(router, color_scheme):
    """Build the HTML event log page."""
    now = time.time()
    events = sorted(router.all_sim_events, key=lambda e: e.get('ts', 0), reverse=True)
    own_sim = router.config.identity.simulator
    own_router = router.config.identity.router
    get_var_name = router.variables.get_variable_name
    rows = [_evt_row_html(e, own_sim, own_router, now, get_var_name) for e in events]
    if not rows:
        body = (
            '<div class="card">'
            '<p style="margin:0;color:#64748b">No events recorded yet.</p>'
            '</div>\n')
    else:
        body = (
            '<div class="card" style="padding:0;overflow-x:auto">\n'
            '<table style="font-size:0.9em">\n'
            '<thead><tr style="color:#94a3b8;font-size:0.82em">'
            '<th style="padding:0.5em 0.75em;text-align:left;white-space:nowrap">'
            'Time (UTC)</th>'
            '<th style="padding:0.5em 0.75em;text-align:left">Sim/Router</th>'
            '<th style="padding:0.5em 0.75em;text-align:left">Event</th>'
            '<th style="padding:0.5em 0.75em;text-align:left">Raw</th>'
            '</tr></thead>\n'
            '<tbody>\n' + ''.join(rows) + '</tbody></table></div>\n'
        )

    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        f'<meta name="color-scheme" content="{color_scheme}" />\n'
        '<meta http-equiv="refresh" content="30">\n' +
        _COMMON_CSS.format() +
        '\n<style>'
        'body { max-width: none; }'
        'table { font-size: 0.9em; }'
        'th, td { padding: 0.4em 0.75em; border-bottom: 1px solid #2a2f45; }'
        'th { background: #161929; }'
        'tr:last-child td { border-bottom: none; }'
        'td:first-child { width: 6em; }'
        '</style>\n'
        '</head>\n<body>\n'
        '<div class="page-title">'
        '<a href="/"><img src="/static/paib.png" alt="Home"></a>'
        '<h1>Event log</h1>'
        '<div style="margin-left:auto">'
        '<a href="/utils" class="btn btn-gray btn-sm">Back</a>'
        '</div>'
        '</div>\n'
        f'<p class="note">{len(events)} event(s) — auto-refreshes every 30 s'
        f' — ★ = this router</p>\n' +
        body +
        '</body>\n</html>\n'
    )


_EFB_STALE_TIMEOUT_S = 300.0
# VATSIM METAR only refreshes every 30 minutes by design (frankenweather.py's
# _VATSIM_CACHE_MAX_S), and even that only happens opportunistically inside
# _update_zones() -- flagging it "not getting data" against the generic 5-minute
# threshold above would be a false alarm on every normal cycle. Give it a
# matching cadence plus a buffer instead.
_EFB_VATSIM_STALE_TIMEOUT_S = 2100.0

_EFB_CSS = '''\
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
.efb-chip-sm { padding: 0.3rem 0.6rem; font-size: 0.76rem; flex-shrink: 0; white-space: nowrap; }
#efb-pirep {
  display: none; position: fixed; left: 50%; top: 20%; transform: translateX(-50%);
  min-width: 15rem; max-width: 90vw; z-index: 10; cursor: pointer;
  background: #0d1826; border: 1px solid #3b7fe0; border-radius: 10px;
  padding: 0.75rem 1rem; box-shadow: 0 8px 30px rgba(0,0,0,0.7);
}
#efb-pirep table { border-collapse: collapse; width: 100%; font-size: 0.95rem; }
#efb-pirep td { padding: 0.2rem 0.4rem; }
#efb-pirep tr.cur td { background: #16233a; font-weight: 700; }
.efb-src-row {
  display: flex; flex-wrap: wrap; justify-content: space-between; align-items: baseline;
  gap: 0.15rem 0.5rem; font-size: 0.82rem; padding: 0.28rem 0; border-bottom: 1px solid #16233a;
}
.efb-src-row:last-of-type { border-bottom: none; }
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


def _efb_age_label(age_s):
    """Return a short human-readable age string for a data-freshness display."""
    if age_s == float('inf'):
        return 'never'
    if age_s < 60:
        return f"{int(age_s)}s ago"
    if age_s < 3600:
        return f"{int(age_s / 60)}m ago"
    return f"{int(age_s / 3600)}h ago"


def _efb_data_status(now, epoch, timeout_s=_EFB_STALE_TIMEOUT_S):
    """Return (color, label, age_label) describing a 'last received' epoch timestamp."""
    if not epoch:
        return '#ef4444', 'not getting data', 'never'
    age_s = now - epoch
    if age_s > timeout_s:
        return '#ef4444', 'not getting data', _efb_age_label(age_s)
    return '#22c55e', 'OK', _efb_age_label(age_s)


def _efb_status_dict(router, ctx):  # pylint: disable=too-many-locals
    """Build the compact router+frankenweather status dict served by /efb and its JSON API."""
    now = time.time()
    connected = router.upstream is not None
    host = router.config.upstream.host
    port = router.config.upstream.port
    preset_name = next(
        (u.name for u in router.config.upstreams if u.host == host and u.port == port),
        None,
    )
    upstream_label = f"{preset_name} ({host}:{port})" if preset_name else f"{host}:{port}"
    own_sim = router.config.identity.simulator
    standalone = router.config.identity.type == 'standalone'
    elevation_source = router.sharedinfo.get('elevation_source_simulator', 'unknown')
    traffic_source = router.sharedinfo.get('traffic_source_simulator', 'unknown')
    pilot_flying = router.sharedinfo.get('pilot_flying_simulator', 'unknown')

    def _disp(value):
        return f"{value} (this sim)" if value == own_sim else value

    router_info = {
        'connected': connected,
        'upstream_label': upstream_label,
        'client_count': len(router.clients),
        'standalone': standalone,
        'is_slave': router.config.identity.type == 'slave',
        'errors': list(router.sharedinfo.get('errors', [])),
    }
    if not standalone:
        connected_sims = ', '.join(
            f"{s} (observer)" if any(
                i.get('observer_mode')
                for i in router.routerinfo.values()
                if i.get('simulator_name') == s
            ) else s
            for s in sorted({
                i['simulator_name']
                for i in router.routerinfo.values()
                if 'simulator_name' in i
            })
        ) or 'unknown'
        router_info.update({
            'elevation_master': _disp(elevation_source),
            'elevation_ok': elevation_source != 'NOSIM',
            'traffic_master': _disp(traffic_source),
            'traffic_ok': traffic_source != 'NOSIM',
            'flight_control_locks': _disp(pilot_flying),
            'flight_control_locks_ok': pilot_flying == own_sim,
            'connected_sims': connected_sims,
        })

    state = ctx.fw_state
    fw_color, fw_label, fw_age = _efb_data_status(now, ctx.fw_state_received_at)
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
    vatsim_color, vatsim_label, vatsim_age = _efb_data_status(
        now, metar_src_epoch, _EFB_VATSIM_STALE_TIMEOUT_S)

    turbstate = ctx.fw_turbstate
    turb_fresh_color, _, turb_age = _efb_data_status(now, ctx.fw_turbstate_received_at)
    turb_available = turbstate is not None and turb_fresh_color == '#22c55e'
    msfs_active = bool((turbstate or {}).get('msfs_active'))
    msfs_color, _, msfs_age = _efb_data_status(now, (turbstate or {}).get('msfs_last_seen_epoch'))
    if not msfs_active:
        msfs_color = '#64748b'

    weather_info = {
        'available': state is not None,
        'status_color': fw_color, 'status_label': fw_label, 'age_label': fw_age,
        'fw_mode': (state or {}).get('fw_mode', 'unknown'),
        'enroute_wind_enabled': bool(cfg.get('enroute_wind_enabled', False)),
        # (mode, has_modes): has_modes False for FrankenWeather before
        # 1.13.0, which can only move the zone
        'cb_airport_mode': _fw_webui.cb_airport_mode(cfg),
        'cb_airport_clearance_nm': cfg.get('cb_airport_clearance_nm', 15),
        'disable_psx_sigmets': bool(cfg.get('disable_psx_sigmets', False)),
        'sigmet_enabled': bool(cfg.get('sigmet_enabled', False)),
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
        'turb_kind': (turbstate or {}).get('active_kind', 'none'),
        'turb_intensity_pct': round((turbstate or {}).get('active_intensity', 0.0) * 100),
        'turb_age': turb_age,
        'turb_reason': (turbstate or {}).get('active_reason') or '—',
        # "sigmet_ramp" is FrankenWeather 1.11.0's name for it
        'turb_ramp': ((turbstate or {}).get('intensity_ramp') or
                      (turbstate or {}).get('sigmet_ramp')),
    }
    return {'router': router_info, 'weather': weather_info}


def _efb_router_card(info):
    """Render the frankenrouter connection status card."""
    dot = '#22c55e' if info['connected'] else '#ef4444'
    label = 'Connected' if info['connected'] else 'Disconnected'
    rows = ''
    if not info['standalone']:
        for title, ok, val in (
                ('Elevation master', info['elevation_ok'], info['elevation_master']),
                ('Traffic master', info['traffic_ok'], info['traffic_master']),
                ('Flight control locks', info['flight_control_locks_ok'],
                 info['flight_control_locks']),
        ):
            c = '#22c55e' if ok else '#f4d100'
            rows += (f'<div style="margin-top:0.35rem;font-size:0.85rem">'
                     f'<span style="color:#7e97ba">{title}:</span> '
                     f'<span style="color:{c};font-weight:600">{val}</span></div>\n')
        rows += (f'<div style="margin-top:0.35rem;font-size:0.85rem">'
                 f'<span style="color:#7e97ba">Connected simulators:</span> '
                 f'<span style="color:#cbd8ea;font-weight:600">'
                 f'{info["connected_sims"]}</span></div>\n')
    return (
        '<div class="efb-card" id="efb-router-card">\n'
        '<div class="efb-eyebrow">Router Status</div>\n'
        f'<div class="efb-value"><span class="efb-dot" style="background:{dot}"></span>'
        f'{label}<small>{info["upstream_label"]} — {info["client_count"]} client(s)</small>'
        '</div>\n' + rows +
        '</div>\n'
    )


def _efb_bool_chip_row(  # pylint: disable=too-many-arguments,too-many-positional-arguments
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
_WEATHER_PRESET_CMDS = {
    'full': {'mode': 'enabled', 'enabled': True, 'enroute_wind_enabled': True},
    'psx_auto': {'mode': 'disabled', 'enabled': False, 'enroute_wind_enabled': False},
    'psx_manual': {'mode': 'paused', 'enabled': False, 'enroute_wind_enabled': False},
}

_WEATHER_PRESET_LABELS = (
    ('full', 'Full'),
    ('psx_auto', 'PSX Auto'),
    ('psx_manual', 'PSX Manual'),
)


def _efb_weather_preset(w):
    """Return the current combined weather-mode preset key, or None if mixed/custom."""
    mode, turb, enroute = w['fw_mode'], w['turb_enabled'], w['enroute_wind_enabled']
    for preset, cmd in _WEATHER_PRESET_CMDS.items():
        if (mode, turb, enroute) == (cmd['mode'], cmd['enabled'], cmd['enroute_wind_enabled']):
            return preset
    return None


def _efb_weather_mode_row(current):
    """Render the 3-way Full/PSX Auto/PSX Manual weather-mode chip row."""
    chips = ''.join(
        f'<form class="efb-inline" method="post" action="/api/efb/weather-preset">'
        f'<input type="hidden" name="preset" value="{preset}">'
        f'<button type="submit" '
        f'class="efb-chip efb-chip-sm{" active" if current == preset else ""}">'
        f'{label}</button></form>\n'
        for preset, label in _WEATHER_PRESET_LABELS
    )
    return f'<div class="efb-chip-row">{chips}</div>\n'


def _efb_cb_airport_mode_row(w):
    """Render the Off/Move zone/Remove CB chip row (fw_webui.CB_AIRPORT_MODES).

    Remove CB is only offered by FrankenWeather 1.13.0 or later.
    """
    current, has_modes = w['cb_airport_mode']
    chips = ''.join(
        f'<form class="efb-inline" method="post" action="/api/efb/cb-airport-mode">'
        f'<input type="hidden" name="mode" value="{mode}">'
        f'<button type="submit" '
        f'class="efb-chip efb-chip-sm{" active" if current == mode else ""}">'
        f'{label}</button></form>\n'
        for mode, label, _hint in _fw_webui.CB_AIRPORT_MODES
        if mode != 'remove' or has_modes
    )
    return f'<div class="efb-chip-row">{chips}</div>\n'


def _efb_sigmet_preset_row(w):
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
        for preset, label, _hint in _fw_sigmet.PRESETS
        if preset != 'normal' or w['sigmet_enabled']
    )
    return f'<div class="efb-chip-row">{chips}</div>\n'


def _efb_sigmet_hint(w):
    """Return the /efb hint line explaining the SIGMET presets."""
    if not w['sigmet_enabled']:
        return ('Off: no SIGMETs at all, neither FrankenWeather nor PSX uses any. '
                'PSX: PSX handles every SIGMET itself. '
                'Start FrankenWeather with --sigmet for Normal.')
    hint = '. '.join(f'{label}: {hint}' for preset, label, hint in _fw_sigmet.PRESETS)
    if w['sigmet_preset'] is None:
        hint += '. Now: a custom choice from the SIGMETs page'
    return hint + '.'


def _efb_controls_card(w):
    """Render the weather-mode, turbulence, and CB-avoidance control card.

    If FrankenWeather itself isn't running (no STATE message ever
    received, or none recently -- see _efb_data_status()), none of
    these controls can do anything: posting a preset/toggle still
    works mechanically (the router accepts the request), but there is
    no FrankenWeather process on the other end to act on it or to
    report back a new state, so the buttons would appear to do nothing
    and the weather-mode 3-way row would never match any preset (see
    _efb_weather_preset()) and so never highlight. Show a dimmed,
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
    return (
        '<div class="efb-card" id="efb-controls-card">\n'
        '<div class="efb-eyebrow">Weather Mode</div>\n' +
        _efb_weather_mode_row(_efb_weather_preset(w)) +
        '<p class="efb-hint">Full: FrankenWeather active. PSX Auto: '
        "PSX's own METAR weather. PSX Manual: weather frozen, instructor "
        'control.</p>\n'
        '<div class="efb-eyebrow" style="margin-top:0.6rem">Extra Turbulence</div>\n' +
        _efb_bool_chip_row(
            '/api/efb/turb-toggle', 'enabled', w['turb_enabled'], small=True) +
        '<p class="efb-hint">Wind, terrain, convection, CB proximity, etc.</p>\n'
        '<div class="efb-eyebrow" style="margin-top:0.6rem">CB Near Dep/Arr</div>\n' +
        _efb_cb_airport_mode_row(w) +
        '<p class="efb-hint">Move zone: moves the airport\'s weather zone so its CB stays '
        '15 nm away. Remove CB: removes every CB within 15 nm.</p>\n'
        '<div class="efb-eyebrow" style="margin-top:0.6rem">SIGMETs</div>\n' +
        _efb_sigmet_preset_row(w) +
        f'<p class="efb-hint">{_efb_sigmet_hint(w)}</p>\n'
        '</div>\n'
    )


def _efb_src_row(label, color, text):
    """Render one labelled/colored status row shared by the data-sources-style cards."""
    return (f'<div class="efb-src-row"><span class="lbl">{label}</span>'
            f'<span class="val"><span class="efb-dot" style="background:{color}">'
            f'</span>{text}</span></div>\n')


def _efb_turb_text(w):
    """Return a short human-readable description of the currently-injected turbulence."""
    if not w['turb_available']:
        return 'no data'
    if not w['turb_enabled']:
        return 'OFF'
    if w['turb_kind'] in (None, 'none'):
        return 'none active'
    return f"{w['turb_kind']}, {w['turb_intensity_pct']}%"


def _efb_turb_color(w):
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


def _efb_turb_injected(w):
    """Return True when turbulence is actually being injected right now."""
    return w['turb_available'] and w['turb_enabled'] and w['turb_kind'] not in (None, 'none')


def _efb_datasources_card(w):
    """Render the extra-turbulence status + data-source connectivity/freshness card."""
    reason_row = (
        _efb_src_row('Turbulence reason', '#64748b', w['turb_reason'])
        if _efb_turb_injected(w) else ''
    ) + (
        _efb_src_row('Intensity trend', '#64748b', _fw_sigmet.ramp_text(w['turb_ramp']))
        if _efb_turb_injected(w) and w['turb_ramp'] else ''
    )
    rows = (
        _efb_src_row('Extra turbulence', _efb_turb_color(w), _efb_turb_text(w)) +
        reason_row +
        _efb_src_row('Router ← FrankenWeather', w['src_router_color'],
                     f"{w['src_router_label']}, {w['age_label']}") +
        _efb_src_row('FrankenWeather ← MSFS', w['src_msfs_color'],
                     f"{w['src_msfs_label']}, {w['src_msfs_age']}") +
        _efb_src_row('FrankenWeather ← Open-Meteo', w['src_om_color'], w['src_om_label']) +
        _efb_src_row(f"FrankenWeather ← {w['src_vatsim_source_name']}", w['src_vatsim_color'],
                     f"{w['src_vatsim_label']}, {w['src_vatsim_age']}")
    )
    return (
        '<div class="efb-card" id="efb-datasources-card">\n'
        '<div class="efb-eyebrow">Weather Status</div>\n' +
        rows +
        '</div>\n'
    )


def _efb_decode_wx_wind(wind_field):
    """Decode a Wx-string wind field (VVV+DDD+SS or legacy VVV+DD+SS) into (dir_deg, spd_kt)."""
    try:
        if len(wind_field) == 8:
            return int(wind_field[3:6]), int(wind_field[6:8])
        if len(wind_field) == 7:
            return int(wind_field[3:5]) * 10, int(wind_field[5:7])
    except ValueError:
        pass
    return None, None


def _efb_decode_wx(wx_str):
    """Decode a PSX Wx<N>/WxBasic string into display-ready weather fields, or None."""
    if not wx_str:
        return None
    parts = wx_str.split(';')
    if len(parts) < 24:
        return None
    wind_dir, wind_spd = _efb_decode_wx_wind(parts[18])
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


def _efb_current_location_dict(ctx):
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
    fields = _efb_decode_wx(ctx.cache_get(wx_key))
    if fields is None:
        return None
    fields['zone_label'] = zone_label
    return fields


def _efb_location_wx_card(loc, sigmet_hazards=()):
    """Render the currently-focused zone's weather detail card."""
    if loc is None:
        body = '<p style="margin:0;color:#64748b">No weather data available.</p>\n'
    else:
        gust = f" G{loc['wind_gust_kt']}kt" if loc['wind_gust_kt'] else ''
        clouds = (f"{loc['cloud_oktas']}/8 oktas @ {loc['cloud_base_ft']:,}ft"
                  if loc['cloud_oktas'] > 0 else 'Clear')
        cb = (f"{loc['cb_oktas']}/8 oktas, base {loc['cb_base_ft']:,}ft, "
              f"tops {loc['cb_top_ft']:,}ft" if loc['cb_oktas'] > 0 else 'None')
        body = (
            _efb_src_row('Zone', '#3b82f6', loc['zone_label']) +
            _efb_src_row('Wind', '#3b82f6',
                         f"{loc['wind_dir']:03d}° / {loc['wind_spd_kt']}kt{gust}") +
            _efb_src_row('QNH', '#3b82f6', f"{loc['qnh_hpa']} hPa") +
            _efb_src_row('Temperature', '#3b82f6', f"{loc['temp_c']}°C") +
            _efb_src_row('Visibility', '#3b82f6', f"{loc['visibility_m']:,} m") +
            _efb_src_row('Clouds', '#3b82f6', clouds) +
            _efb_src_row('Convective (CB)', '#3b82f6', cb) +
            _efb_src_row(
                'In SIGMET area', '#f4d100' if sigmet_hazards else '#64748b',
                ', '.join(sigmet_hazards) if sigmet_hazards else 'No')
        )
    return (
        '<div class="efb-card" id="efb-location-card">\n'
        '<div class="efb-eyebrow">Current Location Weather</div>\n' +
        body +
        '</div>\n'
    )


def _efb_errors_html(errors):
    """Render the critical-errors banner, or '' when there are none."""
    if not errors:
        return ''
    return (
        '<div class="efb-foot">'
        '<b style="color:#f5a3a3">Critical errors</b><ul>' +
        ''.join(f'<li>{e}</li>' for e in errors) + '</ul></div>\n'
    )


def _efb_claim_sources_html(info):
    """Render a one-tap "claim elevation/traffic master" banner, or '' if not needed.

    Slave-router-only: a master router is already the network's authority
    for elevation/traffic (claiming "itself" is meaningless there), and
    standalone has no network-wide elevation/traffic master concept at
    all (see _efb_status_dict()). Shown whenever either source is
    unclaimed (SHAREDINFO reports NOSIM for it).
    """
    if not info['is_slave']:
        return ''
    if info['elevation_ok'] and info['traffic_ok']:
        return ''
    return (
        '<div class="efb-banner">'
        'No sim is currently sending elevation and/or traffic data. '
        '<form class="efb-inline" method="post" action="/api/efb/claim-sources" '
        'style="margin-left:0.4rem">'
        '<button type="submit" class="efb-chip efb-chip-sm">'
        'Make this sim elevation and traffic master</button></form>'
        '</div>\n'
    )


_EFB_PIREP_SHOW_MS = 8000  # the PIREP popup closes by itself after this long
_PIREP_RIDE_COLORS = {
    'smooth': '#22c55e', 'light': '#a3e635', 'moderate': '#eab308', 'rough': '#ef4444'}


def _efb_pirep_html(ctx, now=None):
    """Render the PIREP popup: FrankenWeather's ride reports near the current flight level.

    The reports (TURBSTATE "pireps", FrankenWeather after 1.11.0) rate
    every source of turbulence at each level, refreshed every minute and
    on a flight level change; highest level first, the current one marked.
    """
    now = time.time() if now is None else now
    turbstate = ctx.fw_turbstate
    fresh, _, _ = _efb_data_status(now, ctx.fw_turbstate_received_at)
    head = '<div class="efb-eyebrow">PIREPs – reported ride</div>\n'
    if turbstate is None or fresh != '#22c55e':
        return head + '<p class="efb-hint">No data from FrankenWeather.</p>\n'
    reports = turbstate.get('pireps')
    if not reports:
        return head + '<p class="efb-hint">No reports yet.</p>\n'
    rows = ''
    for rep in sorted(reports, key=lambda x: -x['fl']):
        color = _PIREP_RIDE_COLORS.get(rep['ride'], '#cbd8ea')
        rows += (f'<tr{" class=\"cur\"" if rep.get("current") else ""}>'
                 f'<td>FL{rep["fl"]:03d}</td>'
                 f'<td style="color:{color};font-weight:700">{rep["ride"]}</td>'
                 f'<td style="color:#7e97ba">{"◀ you" if rep.get("current") else ""}</td></tr>')
    return head + f'<table>{rows}</table>\n'


def _build_efb_page(router, ctx, page_url):
    """Build the compact /efb status+control page for the PSX.NET EFB app."""
    status = _efb_status_dict(router, ctx)
    r, w = status['router'], status['weather']
    loc = _efb_current_location_dict(ctx)
    music_link = ''
    if router.config.listen.rest_api_efb_music_link_enabled:
        music_url = (
            'https://flightofthepenguins.se/music/player?efb_return=' +
            urllib.parse.quote(page_url, safe='')
        )
        music_link = (
            f'<a href="{music_url}" '
            'class="efb-chip efb-chip-sm">In-flight music</a> '
        )
    return (
        '<!DOCTYPE html>\n<html>\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="color-scheme" content="dark">\n' +
        _EFB_CSS +
        '</head>\n<body>\n'
        f'<div id="efb-errors">{_efb_errors_html(r["errors"])}</div>\n'
        f'<div id="efb-claim-sources">{_efb_claim_sources_html(r)}</div>\n'
        '<div class="efb-grid wide">' +
        _efb_router_card(r) + _efb_datasources_card(w) +
        _efb_controls_card(w) + _efb_location_wx_card(loc, w['sigmet_hazards']) +
        '</div>\n'
        '<div id="efb-pirep" onclick="efbPirepClose()"></div>\n'
        '<div style="text-align:right;margin-top:0.5rem">'
        '<button type="button" class="efb-chip efb-chip-sm" onclick="efbPirep()">'
        'PIREP</button> ' +
        music_link +
        '<a href="/" class="efb-chip efb-chip-sm">Full router/weather control panel</a></div>\n'
        '<script>\n'
        'function efbPoll() {\n'
        '  fetch("/api/efb/status").then(function(r) { return r.json(); })'
        '.then(function(s) {\n'
        '    var ec = document.getElementById("efb-errors");\n'
        '    var cs = document.getElementById("efb-claim-sources");\n'
        '    var rc = document.getElementById("efb-router-card");\n'
        '    var cc = document.getElementById("efb-controls-card");\n'
        '    var dc = document.getElementById("efb-datasources-card");\n'
        '    var lc = document.getElementById("efb-location-card");\n'
        '    if (ec) ec.innerHTML = s.errors_html;\n'
        '    if (cs) cs.innerHTML = s.claim_sources_html;\n'
        '    if (rc) rc.outerHTML = s.router_html;\n'
        '    if (cc) cc.outerHTML = s.controls_html;\n'
        '    if (dc) dc.outerHTML = s.datasources_html;\n'
        '    if (lc) lc.outerHTML = s.location_html;\n'
        '  }).catch(function() {});\n'
        '}\n'
        'setInterval(efbPoll, 5000);\n'
        # PIREP button: a popup that closes by itself after a few seconds
        # (or when tapped)
        'var efbPirepTimer = null;\n'
        'function efbPirepClose() {\n'
        '  document.getElementById("efb-pirep").style.display = "none";\n'
        '}\n'
        'function efbPirep() {\n'
        '  fetch("/api/efb/pirep", {cache: "no-store"}).then(function(r) { return r.json(); })'
        '.then(function(p) {\n'
        '    var box = document.getElementById("efb-pirep");\n'
        '    box.innerHTML = p.html;\n'
        '    box.style.display = "block";\n'
        '    if (efbPirepTimer) clearTimeout(efbPirepTimer);\n'
        f'    efbPirepTimer = setTimeout(efbPirepClose, {_EFB_PIREP_SHOW_MS});\n'
        '  }).catch(function() {});\n'
        '}\n'
        '</script>\n'
        '</body>\n</html>\n'
    )


class RouterFWContext:
    """Adapts a router instance to the fw_webui context protocol."""

    def __init__(self, router, color_scheme):
        """Store router reference and color scheme."""
        self._router = router
        self.color_scheme = color_scheme

    @property
    def fw_state(self):
        """Return cached FrankenWeather STATE dict."""
        return self._router.frankenweather_state

    @property
    def fw_turbstate(self):
        """Return cached TURBSTATE dict."""
        return self._router.frankenweather_turbstate

    @property
    def fw_state_received_at(self):
        """Return epoch of last STATE receive."""
        return self._router.frankenweather_received_at

    @property
    def fw_turbstate_received_at(self):
        """Return epoch of last TURBSTATE receive."""
        return self._router.frankenweather_turbstate_received_at

    @property
    def fw_windstate(self):
        """Return cached WINDSTATE dict."""
        return self._router.frankenweather_windstate

    @property
    def fw_windstate_received_at(self):
        """Return epoch of last WINDSTATE receive."""
        return self._router.frankenweather_windstate_received_at

    @property
    def router_home(self):
        """Return the router's start page URL, for the weather pages' "Router home" button."""
        return '/'

    @property
    def fw_sigmetstate(self):
        """Return cached SIGMETSTATE dict."""
        return self._router.frankenweather_sigmetstate

    @property
    def fw_sigmetstate_received_at(self):
        """Return epoch of last SIGMETSTATE receive."""
        return self._router.frankenweather_sigmetstate_received_at

    @property
    def fw_conflict_paused(self):
        """Return False: the router assumes only one FRANKENWEATHER instance exists.

        A paused instance stops sending zone/turbulence/wind data (see
        frankenweather.py's _conflict_paused()), so this is never something
        the router itself needs to detect or display.
        """
        return False

    def cache_get(self, name):
        """Return a PSX variable value by name, or None if absent."""
        from frankenrouter import routercache  # pylint: disable=import-outside-toplevel
        key = self._router.variables.get_keyword_for_name(name) or name
        try:
            return self._router.cache.get_value(key)
        except routercache.RouterCacheException:
            return None

    async def send_manualwx_cmd(self, cmd):
        """Send a MANUALWXCOMMAND to PSX and all clients."""
        line = f"addon=FRANKENWEATHER:MANUALWXCOMMAND:{json.dumps(cmd)}"
        await self._router.send_to_upstream(line)
        await self._router.client_broadcast(line)
        await asyncio.sleep(1)

    async def send_turb_cmd(self, cmd):
        """Send a TURBCOMMAND to PSX and all clients."""
        line = f"addon=FRANKENWEATHER:TURBCOMMAND:{json.dumps(cmd)}"
        await self._router.send_to_upstream(line)
        await self._router.client_broadcast(line)
        await asyncio.sleep(1)

    async def send_mode_cmd(self, mode):
        """Send a FW mode change command to PSX and all clients."""
        line = f'addon=FRANKENWEATHER:COMMAND:{{"mode":"{mode}"}}'
        await self._router.send_to_upstream(line)
        await self._router.client_broadcast(line)
        await asyncio.sleep(3)

    async def send_fw_settings_cmd(self, cmd):
        """Send a FW settings command to PSX and all clients."""
        line = f"addon=FRANKENWEATHER:COMMAND:{json.dumps(cmd)}"
        await self._router.send_to_upstream(line)
        await self._router.client_broadcast(line)
        await asyncio.sleep(1)


class RouterWebAPI:  # pylint: disable=too-few-public-methods
    """Owns the aiohttp application and all REST/HTML route handlers."""

    def __init__(self, router):
        """Store reference to the router instance."""
        self.router = router

    async def run(self, name):  # pylint: disable=too-many-statements,too-many-locals
        """Start the HTTP server and serve until cancelled."""
        router = self.router

        try:
            routes = web.RouteTableDef()

            @routes.get('/')
            async def handle_web(_):  # pylint: disable=too-many-locals
                connected = router.upstream is not None
                host = router.config.upstream.host
                port = router.config.upstream.port
                preset_name = next(
                    (u.name for u in router.config.upstreams
                     if u.host == host and u.port == port),
                    None,
                )
                upstream_label = (
                    f"{preset_name} ({host}:{port})" if preset_name else f"{host}:{port}"
                )
                own_sim = router.config.identity.simulator
                elevation_source = router.sharedinfo.get('elevation_source_simulator', 'unknown')
                traffic_source = router.sharedinfo.get('traffic_source_simulator', 'unknown')
                pilot_flying = router.sharedinfo.get('pilot_flying_simulator', 'unknown')
                if router.get_router_type() == 'slave' and not (
                        connected and router.upstream.access_level == 'observer'):
                    if elevation_source == own_sim:
                        elev_btn = (
                            '<a href="/api/filter/elevation/stop_sending" class="btn btn-red">'
                            'Stop being the elevation master</a>\n'
                        )
                    elif elevation_source == 'NOSIM':
                        elev_btn = (
                            '<a href="/api/filter/elevation/start_sending" class="btn btn-green">'
                            'Make me elevation master</a>\n'
                        )
                    else:
                        elev_btn = (
                            '<a href="/api/filter/elevation/start_sending" class="btn btn-red">'
                            'Make me elevation master</a>\n'
                        )
                    if traffic_source == own_sim:
                        traffic_btn = (
                            '<a href="/api/filter/traffic/stop_sending" class="btn btn-red">'
                            'Stop being the traffic master</a>\n'
                        )
                    elif traffic_source == 'NOSIM':
                        traffic_btn = (
                            '<a href="/api/filter/traffic/start_sending" class="btn btn-green">'
                            'Make me traffic master</a>\n'
                        )
                    else:
                        traffic_btn = (
                            '<a href="/api/filter/traffic/start_sending" class="btn btn-red">'
                            'Make me traffic master</a>\n'
                        )
                    master_buttons = elev_btn + traffic_btn + _fc_buttons_html(
                        pilot_flying, own_sim)
                else:
                    master_buttons = ''
                errors = router.sharedinfo.get('errors', [])
                errors_html = (
                    '<div class="card warn">\n<b>Critical errors</b>\n'
                    '<ul style="margin:0.4em 0 0;padding-left:1.4em">\n' +
                    ''.join(f'<li>{e}</li>\n' for e in errors) +
                    '</ul>\n</div>\n'
                    if errors else ''
                )
                identity_type = router.config.identity.type
                is_standalone = identity_type == 'standalone'
                if is_standalone:
                    shared_cockpit_rows = ''
                else:
                    elev_disp = (elevation_source + ' (this sim)'
                                 if elevation_source == own_sim else elevation_source)
                    elev_cls = 'warn' if elevation_source == 'NOSIM' else 'ok'
                    traf_disp = (traffic_source + ' (this sim)'
                                 if traffic_source == own_sim else traffic_source)
                    traf_cls = 'warn' if traffic_source == 'NOSIM' else 'ok'
                    pf_disp = (pilot_flying + ' (this sim)'
                               if pilot_flying == own_sim else pilot_flying)
                    pf_cls = ('ok' if pilot_flying == own_sim
                              else 'warn' if pilot_flying == 'ALL_CONTROL_LOCKS'
                              else 'val')
                    connected_sims = ', '.join(
                        f"{s} (observer)" if any(
                            i.get('observer_mode')
                            for i in router.routerinfo.values()
                            if i.get('simulator_name') == s
                        ) else s
                        for s in sorted({
                            i['simulator_name']
                            for i in router.routerinfo.values()
                            if 'simulator_name' in i
                        })
                    ) or 'unknown'
                    shared_cockpit_rows = (
                        f'<tr><td>Elevation master</td>'
                        f'<td class="{elev_cls}">{elev_disp}</td></tr>\n'
                        f'<tr><td>Traffic master</td>'
                        f'<td class="{traf_cls}">{traf_disp}</td></tr>\n'
                        f'<tr><td>Pilot flying</td>'
                        f'<td class="{pf_cls}">{pf_disp}</td></tr>\n'
                        f'<tr><td>Connected simulators</td>'
                        f'<td class="val">{connected_sims}</td></tr>\n'
                    )
                data = {
                    'rest_api_color_scheme': router.config.listen.rest_api_color_scheme,
                    'this_sim': own_sim,
                    'router_type': identity_type,
                    'upstream_label': upstream_label,
                    'upstream_status': (
                        'Connected' if connected and identity_type in ('master', 'standalone')
                        else router.upstream.access_level.capitalize() if connected
                        else 'Not connected'),
                    'upstream_class': (
                        'ok' if connected and (
                            router.upstream.access_level == 'crew' or
                            identity_type in ('master', 'standalone'))
                        else 'warn'),
                    'shared_cockpit_rows': shared_cockpit_rows,
                    'master_buttons': master_buttons,
                    'critical_errors': errors_html,
                    'forced_disconnect_notice': (
                        '<div class="card warn">\n'
                        '<b>Forcibly disconnected from upstream</b> '
                        f'by {html.escape(router.upstream_disabled_by or "unknown")}.<br>\n'
                        f'Reason: {html.escape(router.upstream_disabled_reason)}\n'
                        '<form method="post" action="/api/upstream/reconnect"'
                        ' style="display:inline">'
                        '<button class="btn btn-blue">Reconnect to last upstream</button>'
                        '</form>\n'
                        '</div>\n'
                        if router.upstream_disabled_reason and not connected else ''
                    ),
                    'observer_mode_notice': (
                        '<div class="card warn">\n'
                        '<b>Observer mode active</b> &mdash; '
                        'key-value writes from local clients are blocked; '
                        'this also prevents active observer ground crew duties'
                        ' like pushback and external power connect/disconnect.\n'
                        '</div>\n'
                        if router.observer_mode else ''
                    ),
                    'observer_mode_button': (
                        '<a href="/api/observermode/disable" class="btn btn-green">'
                        'Disable observer mode</a>\n'
                        if router.observer_mode else
                        '' if identity_type != 'slave' else
                        '<a href="/api/observermode/enable" class="btn btn-amber">'
                        'Enable read-only observer mode</a>\n'
                    ),
                    'sessionpwd_button': (
                        '<a href="/sessionpwd" class="btn btn-blue">Session password</a>\n'
                        if router.get_router_type() == 'master' else ''
                    ),
                    'change_upstream_button': (
                        '' if router.config.identity.type == 'master' else
                        '<a href="/upstream" class="btn btn-blue">Change upstream</a>\n'
                    ),
                    'shutdown_button': (
                        '<a href="/shutdown" class="btn btn-red">Shutdown router</a>\n'
                        if router.config.listen.rest_api_shutdown_enabled else ''
                    ),
                }
                return web.json_response(
                    text=_INDEX_PAGE.format(**data), content_type='text/html')

            @routes.get('/api/stats')
            async def handle_stats_get(request):
                params = request.rel_url.query
                history = 0
                try:
                    history = int(params['history'])
                except (KeyError, ValueError):
                    pass
                response = {
                    'upstream_queue': router.messagequeue_from_upstream.qsize(),
                    'client_queue': router.messagequeue_from_clients.qsize(),
                }
                if len(router.message_write_times) > 1:
                    response['write_times_ms'] = {
                        'max': 1000 * max(router.message_write_times),
                        'median': 1000 * statistics.median(router.message_write_times),
                        'mean': 1000 * statistics.mean(router.message_write_times),
                        'stdev': 1000 * statistics.stdev(router.message_write_times),
                    }
                if len(router.log_times) > 1:
                    response['log_times_ms'] = {
                        'max': 1000 * max(router.log_times),
                        'median': 1000 * statistics.median(router.log_times),
                        'mean': 1000 * statistics.mean(router.log_times),
                        'stdev': 1000 * statistics.stdev(router.log_times),
                    }
                if len(router.writes_counter) > 1:
                    response['writes_per_second'] = {
                        'last': router.writes_counter[1]['count'],
                    }
                    if history > 0:
                        response['writes_per_second']['history'] = list(
                            router.writes_counter)[1:history]
                if len(router.message_counter) > 1:
                    response['messages_per_second'] = {
                        'last': router.message_counter[1]['count'],
                    }
                    if history > 0:
                        response['messages_per_second']['history'] = list(
                            router.message_counter)[1:history]
                return web.json_response(response)

            @routes.get('/api/clients')
            async def handle_clients_get(_):
                clients = []
                for client in router.clients.values():
                    thisclient = {
                        'ip': client.ip,
                        'id': client.client_id,
                        'port': client.port,
                        'display_name': client.display_name,
                        'messages_sent': client.messages_sent,
                        'messages_received': client.messages_received,
                        'client_provided_id': client.client_provided_id,
                        'client_provided_display_name': client.client_provided_display_name,
                        'write_buffer_size': client.writer.transport.get_write_buffer_size(),
                    }
                    if len(client.message_write_times) > 1:
                        thisclient['write_times_ms'] = {
                            'max': 1000 * max(client.message_write_times),
                            'median': 1000 * statistics.median(client.message_write_times),
                            'mean': 1000 * statistics.mean(client.message_write_times),
                            'stdev': 1000 * statistics.stdev(client.message_write_times),
                        }
                    bucket = int(time.time() - 1.0)
                    if bucket in client.received_stats:
                        thisclient['received_messages_per_second'] = client.received_stats[bucket]['received_messages']  # pylint: disable=line-too-long
                        thisclient['received_bytes_per_second'] = client.received_stats[bucket]['received_bytes']  # pylint: disable=line-too-long
                    if bucket in client.sent_stats:
                        thisclient['sent_messages_per_second'] = client.sent_stats[bucket]['sent_messages']  # pylint: disable=line-too-long
                        thisclient['sent_bytes_per_second'] = client.sent_stats[bucket]['sent_bytes']  # pylint: disable=line-too-long
                    clients.append(thisclient)
                return web.json_response(clients)

            @routes.post('/api/disconnect')
            async def handle_client_disconnect(request):
                data = await request.post()
                client_id = int(data.get('client_id'))
                for client in router.clients.values():
                    if client.client_id == client_id:
                        await router.close_client_connection(client)
                        return web.Response(text=f"Client connection {client_id} closed")
                return web.Response(text=f"Client connection {client_id} not found")

            @routes.get('/api/routerinfo')
            async def handle_routerinfo_get(_):
                return web.json_response(router.routerinfo)

            @routes.get('/filter')
            async def handle_web_filter_get(_):
                data = {'rest_api_color_scheme': router.config.listen.rest_api_color_scheme}
                if router.get_router_type() == 'slave':
                    data['network_source_section'] = _FILTER_PAGE_NETWORK_SOURCE_SECTION.format(
                        this_sim=router.config.identity.simulator,
                        elevation_source=router.sharedinfo.get(
                            'elevation_source_simulator', 'unknown'),
                        traffic_source=router.sharedinfo.get(
                            'traffic_source_simulator', 'unknown'),
                    )
                else:
                    data['network_source_section'] = _FILTER_PAGE_NO_CONTROLS
                return web.json_response(
                    text=_FILTER_PAGE.format(**data), content_type='text/html')

            @routes.get('/api/filter/elevation/start_sending')
            async def handle_filter_elevation_start_sending(_):
                sim = router.config.identity.simulator
                router.logger.info("API: sending ELEVATION_SOURCE:%s upstream", sim)
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:ELEVATION_SOURCE:{sim}")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/api/filter/elevation/stop_sending')
            async def handle_filter_elevation_stop_sending(_):
                router.logger.info("API: sending ELEVATION_SOURCE:NOSIM upstream")
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:ELEVATION_SOURCE:NOSIM")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/api/filter/traffic/start_sending')
            async def handle_filter_traffic_start_sending(_):
                sim = router.config.identity.simulator
                router.logger.info("API: sending TRAFFIC_SOURCE:%s upstream", sim)
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:TRAFFIC_SOURCE:{sim}")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/api/filter/traffic/stop_sending')
            async def handle_filter_traffic_stop_sending(_):
                router.logger.info("API: sending TRAFFIC_SOURCE:NOSIM upstream")
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:TRAFFIC_SOURCE:NOSIM")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/api/flightcontrols/my_controls')
            async def handle_flightcontrols_my_controls(_):
                sim = router.config.identity.simulator
                router.logger.info("API: sending FLIGHTCONTROLS:%s upstream", sim)
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:FLIGHTCONTROLS:{sim}")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/api/flightcontrols/no_control_locks')
            async def handle_flightcontrols_no_control_locks(_):
                router.logger.info("API: sending FLIGHTCONTROLS:NO_CONTROL_LOCKS upstream")
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:FLIGHTCONTROLS:NO_CONTROL_LOCKS")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/api/flightcontrols/all_control_locks')
            async def handle_flightcontrols_all_control_locks(_):
                router.logger.info("API: sending FLIGHTCONTROLS:ALL_CONTROL_LOCKS upstream")
                await router.send_to_upstream(
                    f"addon=FRANKENROUTER:{router.frdp_version}:FLIGHTCONTROLS:ALL_CONTROL_LOCKS")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/api/observermode/enable')
            async def handle_observermode_enable(_):
                router.logger.info("API: enabling observer mode")
                router.observer_mode = True
                if router.is_upstream_connected():
                    own_sim = router.config.identity.simulator
                    if router.sharedinfo.get('elevation_source_simulator') == own_sim:
                        router.logger.info("API: observer mode: releasing elevation master")
                        await router.send_to_upstream(
                            f"addon=FRANKENROUTER:{router.frdp_version}"
                            f":ELEVATION_SOURCE:NOSIM")
                    if router.sharedinfo.get('traffic_source_simulator') == own_sim:
                        router.logger.info("API: observer mode: releasing traffic master")
                        await router.send_to_upstream(
                            f"addon=FRANKENROUTER:{router.frdp_version}"
                            f":TRAFFIC_SOURCE:NOSIM")
                    if router.sharedinfo.get('pilot_flying_simulator') == own_sim:
                        router.logger.info("API: observer mode: releasing flight controls")
                        await router.send_to_upstream(
                            f"addon=FRANKENROUTER:{router.frdp_version}"
                            f":FLIGHTCONTROLS:NO_CONTROL_LOCKS")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/api/observermode/disable')
            async def handle_observermode_disable(_):
                router.logger.info("API: disabling observer mode")
                router.observer_mode = False
                await router.send_to_upstream("bang")
                await asyncio.sleep(1)
                raise web.HTTPFound('/')

            @routes.get('/sessionpwd')
            async def handle_sessionpwd_get(_):
                if router.session_password:
                    pwd_section = _SESSION_PASSWORD_SET_SECTION.format(
                        password=router.session_password)
                else:
                    pwd_section = _SESSION_PASSWORD_UNSET_SECTION
                if router.observer_session_password:
                    observer_pwd_section = _OBSERVER_SESSION_PASSWORD_SET_SECTION.format(
                        password=router.observer_session_password)
                else:
                    observer_pwd_section = _OBSERVER_SESSION_PASSWORD_UNSET_SECTION
                data = {
                    'rest_api_color_scheme': router.config.listen.rest_api_color_scheme,
                    'password_section': pwd_section,
                    'observer_password_section': observer_pwd_section,
                }
                return web.json_response(
                    text=_SESSION_PASSWORD_PAGE.format(**data), content_type='text/html')

            @routes.get('/api/sessionpwd/generate')
            async def handle_sessionpwd_generate(_):
                alphabet = string.ascii_letters + string.digits
                router.session_password = ''.join(
                    secrets.choice(alphabet) for _ in range(20))
                router.logger.info("Session password generated")
                raise web.HTTPFound('/sessionpwd')

            @routes.post('/api/sessionpwd/remove')
            async def handle_sessionpwd_remove(_):
                router.session_password = None
                router.logger.info("Session password removed")
                raise web.HTTPFound('/sessionpwd')

            @routes.get('/api/observerpwd/generate')
            async def handle_observerpwd_generate(_):
                alphabet = string.ascii_letters + string.digits
                router.observer_session_password = ''.join(
                    secrets.choice(alphabet) for _ in range(20))
                router.logger.info("Observer session password generated")
                raise web.HTTPFound('/sessionpwd')

            @routes.post('/api/observerpwd/remove')
            async def handle_observerpwd_remove(_):
                router.observer_session_password = None
                router.logger.info("Observer session password removed")
                raise web.HTTPFound('/sessionpwd')

            @routes.get('/upstream')
            async def handle_web_upstream_get(_):
                connected = router.upstream is not None
                host = router.config.upstream.host
                port = router.config.upstream.port
                status_class = 'ok' if connected else 'warn'
                status_text = 'Connected' if connected else 'Not connected'
                preset_name = next(
                    (u.name for u in router.config.upstreams
                     if u.host == host and u.port == port),
                    None,
                )
                current_rows = (
                    f'<tr><td>Status</td><td class="{status_class}">{status_text}</td></tr>\n'
                    f'<tr><td>Host</td><td class="val">{host}:{port}</td></tr>\n'
                )
                if preset_name:
                    current_rows += (
                        f'<tr><td>Name</td><td class="val">{preset_name}</td></tr>\n'
                    )
                presets_html = ""
                if router.config.upstreams:
                    presets_html = '<h2>Predefined upstreams</h2>\n'
                    for upstream in router.config.upstreams:
                        if upstream.use_session_password:
                            presets_html += _UPSTREAM_PAGE_PRESET_SESSION_PWD.format(
                                preset_name=upstream.name,
                                host=upstream.host,
                                port=upstream.port,
                            )
                        else:
                            presets_html += _UPSTREAM_PAGE_PRESET_CONNECT.format(
                                preset_name=upstream.name,
                                host=upstream.host,
                                port=upstream.port,
                                password=upstream.password or "",
                            )
                data = {
                    'rest_api_color_scheme': router.config.listen.rest_api_color_scheme,
                    'host': host,
                    'port': port,
                    'status_class': status_class,
                    'current_rows': current_rows,
                    'password': router.config.upstream.password or "",
                    'presets': presets_html,
                }
                return web.json_response(
                    text=_UPSTREAM_PAGE.format(**data), content_type='text/html')

            @routes.post('/api/upstream')
            async def handle_upstream_set(request):
                data = await request.post()
                new_host = data.get('host')
                new_password = data.get('password')
                new_port = int(data.get('port'))
                router.logger.info(
                    "Got request to change upstream to %s:%s:%s",
                    new_host, new_port, new_password)
                router.logger.info(
                    "Current upstream is %s:%s (connected=%s)",
                    router.config.upstream.host,
                    router.config.upstream.port,
                    router.is_upstream_connected(),
                )
                reconnect = False
                if new_host != router.config.upstream.host:
                    router.config.upstream.host = new_host
                    reconnect = True
                if new_port != router.config.upstream.port:
                    router.config.upstream.port = new_port
                    reconnect = True
                if new_password != router.config.upstream.password:
                    router.config.upstream.password = new_password
                    reconnect = True
                if not reconnect:
                    return web.Response(text="Already connected to that host/port/password")
                router.logger.info(
                    "Will change upstream to %s:%s:%s",
                    router.config.upstream.host,
                    router.config.upstream.port,
                    router.config.upstream.password,
                )
                router.upstream_reconnect_requested = True
                await asyncio.sleep(5)  # typical reconnect time
                raise web.HTTPFound('/upstream')

            @routes.get('/api/upstream')
            async def handle_upstream_get(_):
                if router.is_upstream_connected():
                    res = {
                        'connected': True,
                        'host': router.upstream.ip,
                        'port': router.upstream.port,
                        'display_name': router.upstream.display_name,
                        'messages_sent': router.upstream.messages_sent,
                        'messages_received': router.upstream.messages_received,
                    }
                else:
                    res = {'connected': False}
                return web.json_response(res)

            @routes.post('/api/upstream/reconnect')
            async def handle_upstream_reconnect(_):
                router.logger.info(
                    "API: reconnect to last upstream requested (was disabled by %s)",
                    router.upstream_disabled_by)
                router.upstream_disabled = False
                router.connection_state_changed()
                raise web.HTTPFound('/')

            @routes.get('/basicmode')
            async def handle_basic_mode_redirect(_):
                raise web.HTTPFound('/basicmode/')

            @routes.get('/basicmode/')
            async def handle_basic_mode_page(_):
                if not router.basic_mode:
                    raise web.HTTPFound('/')
                return web.Response(
                    text=_build_basic_mode_page(router), content_type='text/html')

            @routes.get('/api/basicmode/status')
            async def handle_basic_mode_status(_):
                return web.json_response(_basic_mode_status(router))

            @routes.post('/api/basicmode/connect')
            async def handle_basic_mode_connect(request):
                if not router.basic_mode:
                    return web.json_response({'error': "Not in Basic Mode"}, status=403)
                try:
                    data = await request.json()
                except (ValueError, UnicodeDecodeError):
                    data = {}
                req, error = _parse_basic_mode_connect(data)
                if error:
                    return web.json_response({'error': error})
                await router.basic_mode_connect(**req)
                return web.json_response({'ok': True})

            @routes.post('/api/basicmode/disconnect')
            async def handle_basic_mode_disconnect(_):
                if not router.basic_mode:
                    return web.json_response({'error': "Not in Basic Mode"}, status=403)
                await router.basic_mode_disconnect()
                return web.json_response({'ok': True})

            @routes.get('/api/sharedinfo')
            async def handle_sharedinfo(_):
                res = router.sharedinfo
                res['master_uuid'] = router.sharedinfo['master_uuid']
                return web.json_response(res)

            @routes.post('/api/sharedinfo')
            async def handle_sharedinfo_post(request):
                # FIXME: refuse unless we are the sharedinfo master
                data = await request.post()
                new_simulator = data.get('pilot_flying_simulator')
                changes = 0
                if (
                        new_simulator is not None and
                        new_simulator != router.sharedinfo["pilot_flying_simulator"]
                ):
                    router.logger.info(
                        "REST API changed pilot flying simulator to %s", new_simulator)
                    changes += 1
                    router.sharedinfo["pilot_flying_simulator"] = new_simulator
                if changes == 0:
                    return web.Response(text="Nothing was changed")
                router.logger.info("API: sharedinfo changed to %s", router.sharedinfo)
                router.connection_state_changed()
                return web.Response(text=f"{changes} SHAREDINFO variables changed")

            @routes.get('/api/blocklist')
            async def handle_blocklist_get(_):
                return web.json_response(list(router.blocklist))

            @routes.get('/api/blocklist/reset')
            async def handle_blocklist_reset(_):
                router.blocklist = set()
                router.logger.info("API: blocklist was reset")
                return web.Response(text="Block list reset")

            @routes.post('/api/blocklist/add')
            async def handle_blocklist_post_add(request):
                data = await request.post()
                address = str(data.get('address'))
                router.logger.info("API: %s added to blocklist", address)
                router.blocklist.add(address)
                return web.json_response(list(router.blocklist))

            @routes.post('/api/blocklist/remove')
            async def handle_blocklist_post_remove(request):
                data = await request.post()
                address = str(data.get('address'))
                router.blocklist.discard(address)
                router.logger.info("API: %s removed from blocklist", address)
                return web.json_response(list(router.blocklist))

            @routes.post('/api/vpilotprint/message')
            async def handle_print(request):
                data = await request.post()
                token = str(data.get('token'))
                title = str(data.get('title'))
                message = str(data.get('message'))
                priority = str(data.get('priority'))

                if re.match(
                        r".*(Connected. Running version|Disconnected from network)",
                        message
                ):
                    router.logger.info(
                        "vPilot title=%s message not printed: %s", title, message)
                    return web.Response(text="OK")

                router.logger.info(
                    "vPilot message: token=%s, title=%s, message=%s, priority=%s",
                    token, title, message, priority)

                # FIXME: filter invalid characters

                text = textwrap.wrap(message, width=40)
                text = '^'.join(text)
                text = f"From {title} via {router.config.identity.simulator}:^{text}"
                text = text.upper()

                router.cache.update("Qs119", text)
                await router.send_to_upstream(f"Qs119={text}")
                await router.client_broadcast(f"Qs119={text}")
                return web.Response(text="OK")

            _efb_ctx = RouterFWContext(router, router.config.listen.rest_api_color_scheme)

            _fw_webui.register_weather_routes(routes, _efb_ctx)

            @routes.get('/efb')
            async def handle_efb_get(request):
                # Served state (router/weather/turb status) changes continuously
                # and can be changed from elsewhere (FrankenWeather's own web UI,
                # a config file load, etc.) -- never let a browser serve a stale
                # cached copy of this page on reload.
                return web.Response(
                    text=_build_efb_page(router, _efb_ctx, str(request.url)),
                    content_type='text/html',
                    headers={'Cache-Control': 'no-store'})

            @routes.get('/api/efb/pirep')
            async def handle_efb_pirep_get(_):
                return web.json_response({'html': _efb_pirep_html(_efb_ctx)},
                                         headers={'Cache-Control': 'no-store'})

            @routes.get('/api/sigmet/latest/json')
            async def handle_sigmet_latest_json_get(_):
                # The parsed SIGMET download, as frankenweather's own web
                # port serves it, from its last FRANKENWEATHER:SIGMETJSON
                if router.frankenweather_sigmetjson is None:
                    return web.Response(status=404, text=(
                        "No SIGMETs received from frankenweather yet "
                        "(it sends them only when started with --sigmet)\n"))
                return web.json_response(
                    router.frankenweather_sigmetjson,
                    headers={'Content-Disposition': 'inline; filename="sigmets.json"',
                             'Cache-Control': 'no-store'})

            @routes.get('/api/efb/status')
            async def handle_efb_status_get(_):
                status = _efb_status_dict(router, _efb_ctx)
                r, w = status['router'], status['weather']
                loc = _efb_current_location_dict(_efb_ctx)
                return web.json_response({
                    'router': r,
                    'weather': w,
                    'errors_html': _efb_errors_html(r['errors']),
                    'claim_sources_html': _efb_claim_sources_html(r),
                    'router_html': _efb_router_card(r),
                    'controls_html': _efb_controls_card(w),
                    'datasources_html': _efb_datasources_card(w),
                    'location_html': _efb_location_wx_card(loc, w['sigmet_hazards']),
                }, headers={'Cache-Control': 'no-store'})

            @routes.post('/api/efb/mode')
            async def handle_efb_mode_post(request):
                data = await request.post()
                mode = data.get('mode')
                if mode in ('enabled', 'paused', 'disabled', 'manual'):
                    await _efb_ctx.send_mode_cmd(mode)
                raise web.HTTPFound('/efb')

            @routes.post('/api/efb/weather-preset')
            async def handle_efb_weather_preset_post(request):
                data = await request.post()
                cmd = _WEATHER_PRESET_CMDS.get(data.get('preset'))
                if cmd:
                    await _efb_ctx.send_fw_settings_cmd(
                        {'mode': cmd['mode'], 'enroute_wind_enabled': cmd['enroute_wind_enabled']})
                    await _efb_ctx.send_turb_cmd({'enabled': cmd['enabled']})
                    await asyncio.sleep(3)
                raise web.HTTPFound('/efb')

            @routes.post('/api/efb/sigmet-preset')
            async def handle_efb_sigmet_preset_post(request):
                data = await request.post()
                status = _efb_status_dict(router, _efb_ctx)
                cmd = _fw_sigmet.preset_command(
                    data.get('preset'), status['weather']['sigmet_enabled'])
                if cmd:
                    await _efb_ctx.send_fw_settings_cmd(cmd)
                raise web.HTTPFound('/efb')

            @routes.post('/api/efb/cb-airport-mode')
            async def handle_efb_cb_airport_mode_post(request):
                data = await request.post()
                cmd = _fw_webui.cb_airport_mode_command(data.get('mode'))
                if cmd:
                    await _efb_ctx.send_fw_settings_cmd(cmd)
                raise web.HTTPFound('/efb')

            @routes.post('/api/efb/toggle')
            async def handle_efb_toggle_post(request):
                data = await request.post()
                field = data.get('field')
                if field in ('enroute_wind_enabled', 'disable_psx_sigmets'):
                    await _efb_ctx.send_fw_settings_cmd({field: data.get('value') == '1'})
                raise web.HTTPFound('/efb')

            @routes.post('/api/efb/turb-toggle')
            async def handle_efb_turb_toggle_post(request):
                data = await request.post()
                if data.get('field') == 'enabled':
                    await _efb_ctx.send_turb_cmd({'enabled': data.get('value') == '1'})
                raise web.HTTPFound('/efb')

            @routes.post('/api/efb/claim-sources')
            async def handle_efb_claim_sources_post(_):
                # Slave-router-only (enforced here too, not just by hiding
                # the button on /efb): a master is already the network's
                # elevation/traffic authority, so claiming "itself" is
                # meaningless there -- see _efb_claim_sources_html().
                if router.config.identity.type != 'slave':
                    raise web.HTTPFound('/efb')
                sim = router.config.identity.simulator
                if router.sharedinfo.get('elevation_source_simulator', 'NOSIM') == 'NOSIM':
                    router.logger.info("API: EFB claiming elevation source for %s", sim)
                    await router.send_to_upstream(
                        f"addon=FRANKENROUTER:{router.frdp_version}:ELEVATION_SOURCE:{sim}")
                if router.sharedinfo.get('traffic_source_simulator', 'NOSIM') == 'NOSIM':
                    router.logger.info("API: EFB claiming traffic source for %s", sim)
                    await router.send_to_upstream(
                        f"addon=FRANKENROUTER:{router.frdp_version}:TRAFFIC_SOURCE:{sim}")
                await asyncio.sleep(1)
                raise web.HTTPFound('/efb')

            @routes.get('/utils')
            async def handle_utils_get(_):
                cs = router.config.listen.rest_api_color_scheme
                sims_link = (
                    '' if router.config.identity.type == 'standalone' else
                    '<a href="/utils/sims" class="btn btn-gray">Connected slave sims</a>\n'
                )
                global_bang_button = (
                    '<form method="post" action="/api/utils/global_bang" style="display:inline" '
                    f'onsubmit="return confirm(\'{_GLOBAL_BANG_CONFIRM}\')">'
                    '<button class="btn btn-gray">Global BANG</button></form>\n'
                    if router.config.identity.type in ('master', 'slave') else ''
                )
                reset_clients_link = (
                    '<a href="/utils/reset_clients" class="btn btn-gray">Reset clients</a>\n'
                    if router.config.identity.type == 'slave' else ''
                )
                load_marker_pause_link = (
                    '<a href="/utils/load_marker_pause" class="btn btn-gray">'
                    'Load marker pause (experimental)</a>\n'
                    if router.config.identity.type == 'master' else ''
                )
                return web.Response(
                    text=_UTILS_PAGE.format(
                        rest_api_color_scheme=cs,
                        sims_link=sims_link,
                        reset_clients_link=reset_clients_link,
                        global_bang_button=global_bang_button,
                        load_marker_pause_link=load_marker_pause_link),
                    content_type='text/html')

            @routes.get('/utils/events')
            async def handle_events_get(_):
                cs = router.config.listen.rest_api_color_scheme
                return web.Response(
                    text=_build_events_page(router, cs),
                    content_type='text/html')

            @routes.get('/utils/sims')
            async def handle_sims_get(_):
                if router.config.identity.type == 'standalone':
                    return web.Response(
                        text="Not available on a standalone router", status=404)
                cs = router.config.listen.rest_api_color_scheme
                return web.Response(
                    text=_build_sims_page(router, cs),
                    content_type='text/html')

            @routes.post('/api/sims/{uuid}/disconnect')
            async def handle_sims_disconnect(request):
                if router.config.identity.type == 'standalone':
                    return web.Response(
                        text="Not available on a standalone router", status=404)
                target_uuid = request.match_info['uuid']
                data = await request.post()
                reason = (data.get('reason') or '').strip()
                if not reason:
                    return web.Response(text="A reason is required", status=400)
                target = router.routerinfo.get(target_uuid)
                if target is None or 'simulator_name' not in target:
                    return web.Response(text="Unknown sim", status=404)
                target_sim = target['simulator_name']
                router.logger.info(
                    "API: requesting disconnect of sim %s, reason: %s", target_sim, reason)
                payload = json.dumps({
                    'from_sim': router.config.identity.simulator,
                    'target_sim': target_sim,
                    'reason': reason,
                })
                line = f"addon=FRANKENROUTER:{router.frdp_version}:DISCONNECT_SIM:{payload}"
                # Flooded onward by every router that forwards it (see
                # handle_addon_frankenrouter_disconnect_sim() in rules.py), so
                # seeding it in both directions from here reaches target_sim
                # no matter where it sits in the topology.
                await router.send_to_upstream(line)
                await router.client_broadcast(line)
                raise web.HTTPFound('/utils/sims')

            @routes.get('/utils/windimport')
            async def handle_windimport_get(_):
                cs = router.config.listen.rest_api_color_scheme
                ofp_section = _WINDIMPORT_OFP_SECTION.format(ofp_value='')
                return web.Response(
                    text=_WINDIMPORT_PAGE.format(
                        rest_api_color_scheme=cs,
                        error_html='',
                        ofp_section=ofp_section,
                        result_section='',
                    ),
                    content_type='text/html')

            @routes.post('/api/utils/windimport')
            async def handle_windimport_post(request):
                from frankenrouter import windimporter  # pylint: disable=import-outside-toplevel,no-name-in-module
                cs = router.config.listen.rest_api_color_scheme
                post = await request.post()
                ofp_text = str(post.get('ofp', ''))
                try:
                    wind_data = windimporter.parse_ofp(ofp_text)
                except windimporter.WindImporterException as exc:
                    error_html = (
                        f'<div class="card warn">'
                        f'<p style="margin:0"><b>Import failed:</b> {exc}</p>'
                        f'</div>\n')
                    ofp_section = _WINDIMPORT_OFP_SECTION.format(
                        ofp_value=ofp_text.replace('<', '&lt;').replace('&', '&amp;'))
                    return web.Response(
                        text=_WINDIMPORT_PAGE.format(
                            rest_api_color_scheme=cs,
                            error_html=error_html,
                            ofp_section=ofp_section,
                            result_section='',
                        ),
                        content_type='text/html')
                result_section = _WINDIMPORT_RESULT_SECTION.format(
                    corridor_display=wind_data,
                    corridor_value=wind_data.replace('"', '&quot;'),
                )
                return web.Response(
                    text=_WINDIMPORT_PAGE.format(
                        rest_api_color_scheme=cs,
                        error_html='',
                        ofp_section='',
                        result_section=result_section,
                    ),
                    content_type='text/html')

            @routes.post('/api/utils/windimport/send')
            async def handle_windimport_send(request):
                from frankenrouter import windimporter  # pylint: disable=import-outside-toplevel,no-name-in-module
                cs = router.config.listen.rest_api_color_scheme
                post = await request.post()
                wind_data = str(post.get('corridor', ''))
                corridor_psx = windimporter.to_psx_corridor(wind_data)
                router.logger.info("API: sending wind corridor to PSX (%d chars)",
                                   len(corridor_psx))
                await router.send_to_upstream(f"Qs498={corridor_psx}")
                await router.send_to_upstream("Qs497=200")
                router.cache.update("Qs498", corridor_psx)
                await router.client_broadcast(f"Qs498={corridor_psx}")
                return web.Response(
                    text=_WINDIMPORT_PAGE.format(
                        rest_api_color_scheme=cs,
                        error_html='',
                        ofp_section='',
                        result_section=_WINDIMPORT_SENT_SECTION,
                    ),
                    content_type='text/html')

            @routes.post('/api/utils/printer/reset')
            async def handle_printer_reset(_):
                router.logger.info("API: resetting printer (Qi115=1)")
                await router.send_to_upstream("Qi115=1")
                raise web.HTTPFound('/utils')

            @routes.post('/api/utils/hafap/reset')
            async def handle_hafap_reset(_):
                router.logger.info("API: resetting HAFAP/CPDLC (addon=HAFAP:1:RESET)")
                await router.send_to_upstream("addon=HAFAP:1:RESET")
                await router.client_broadcast("addon=HAFAP:1:RESET")
                raise web.HTTPFound('/utils')

            @routes.post('/api/utils/global_bang')
            async def handle_global_bang(_):
                # Master (and standalone, though the button isn't shown for
                # it) is directly connected to the real PSX Main Server, so
                # it can bang it right away. A slave has no such connection
                # -- it sends MASTER_BANG upstream instead, which bubbles up
                # the router chain (see handle_addon_frankenrouter_master_bang
                # in rules.py) until it reaches the router that can.
                if router.is_sharedinfo_authority():
                    router.logger.info("API: global BANG requested -- sending bang upstream")
                    await router.send_to_upstream("bang")
                else:
                    router.logger.info(
                        "API: global BANG requested -- forwarding MASTER_BANG upstream")
                    await router.send_to_upstream(
                        f"addon=FRANKENROUTER:{router.frdp_version}:MASTER_BANG")
                raise web.HTTPFound('/utils')

            @routes.get('/utils/reset_clients')
            async def handle_reset_clients_get(_):
                # Slave-router-only (enforced here too, not just by hiding
                # the link on /utils): deliberately local to this
                # router's own clients, see resettable_clients().
                if router.config.identity.type != 'slave':
                    raise web.HTTPFound('/utils')
                cs = router.config.listen.rest_api_color_scheme
                blacklist = set(router.config.filtering.client_reset_blacklist)
                candidates = router.resettable_clients()
                if candidates:
                    checkbox_rows = ''.join(
                        _RESET_CLIENTS_CHECKBOX_ROW.format(
                            client_id=client.client_id,
                            checked='' if client.display_name in blacklist else ' checked',
                            display_name=html.escape(client.display_name),
                        )
                        for client in candidates
                    )
                    list_section = _RESET_CLIENTS_LIST_SECTION.format(checkbox_rows=checkbox_rows)
                else:
                    list_section = _RESET_CLIENTS_EMPTY_SECTION
                return web.Response(
                    text=_RESET_CLIENTS_PAGE.format(
                        rest_api_color_scheme=cs,
                        list_section=list_section,
                        result_section='',
                    ),
                    content_type='text/html')

            @routes.post('/api/utils/reset_clients')
            async def handle_reset_clients_send(request):
                # Slave-router-only (enforced here too, not just by hiding
                # the link on /utils): deliberately local to this
                # router's own clients, see reset_client_welcomes().
                if router.config.identity.type != 'slave':
                    raise web.HTTPFound('/utils')
                cs = router.config.listen.rest_api_color_scheme
                post = await request.post()
                selected_ids = set()
                for raw_id in post.getall('clients', []):
                    try:
                        selected_ids.add(int(raw_id))
                    except (TypeError, ValueError):
                        continue
                targets = [
                    client for client in router.resettable_clients()
                    if client.client_id in selected_ids
                ]
                router.logger.info(
                    "API: resetting %d selected client welcome(s)", len(targets))
                await router.reset_client_welcomes(targets)
                return web.Response(
                    text=_RESET_CLIENTS_PAGE.format(
                        rest_api_color_scheme=cs,
                        list_section='',
                        result_section=_RESET_CLIENTS_SENT_SECTION.format(count=len(targets)),
                    ),
                    content_type='text/html')

            @routes.get('/utils/load_marker_pause')
            async def handle_load_marker_pause_get(_):
                # Master-router-only (enforced here too, not just by hiding
                # the link on /utils): this router is the only one that can
                # change the network-wide setting, see
                # send_frdp_sharedinfo()/handle_addon_frankenrouter_sharedinfo().
                if router.config.identity.type != 'master':
                    raise web.HTTPFound('/utils')
                cs = router.config.listen.rest_api_color_scheme
                return web.Response(
                    text=_LOAD_MARKER_PAUSE_PAGE.format(
                        rest_api_color_scheme=cs,
                        result_section='',
                        enabled_checked=' checked' if router.load_marker_pause_enabled else '',
                        delay=f"{router.load_marker_pause_delay:.3f}"),
                    content_type='text/html')

            @routes.post('/api/utils/load_marker_pause')
            async def handle_load_marker_pause_post(request):
                # Master-router-only, see handle_load_marker_pause_get().
                if router.config.identity.type != 'master':
                    raise web.HTTPFound('/utils')
                cs = router.config.listen.rest_api_color_scheme
                post = await request.post()
                enabled = post.get('enabled') == '1'
                raw_delay = post.get('delay', '')
                try:
                    delay = float(raw_delay)
                except (TypeError, ValueError):
                    delay = -1.0
                if delay < 0:
                    return web.Response(
                        text=_LOAD_MARKER_PAUSE_PAGE.format(
                            rest_api_color_scheme=cs,
                            result_section=_LOAD_MARKER_PAUSE_ERROR_SECTION.format(
                                message="Pause duration must be a non-negative number "
                                        "of seconds"),
                            enabled_checked=' checked' if enabled else '',
                            delay=html.escape(raw_delay)),
                        content_type='text/html', status=400)

                prev_enabled = router.load_marker_pause_enabled
                prev_delay = router.load_marker_pause_delay
                router.load_marker_pause_enabled = enabled
                router.load_marker_pause_delay = delay
                if (enabled, delay) != (prev_enabled, prev_delay):
                    router.logger.info(
                        "API: load_marker_pause changed: enabled=%s delay=%.3f "
                        "(was enabled=%s delay=%.3f)",
                        enabled, delay, prev_enabled, prev_delay)
                    if enabled != prev_enabled:
                        router.record_sim_event(
                            'sharedinfo_change', field='load_marker_pause_enabled',
                            value=enabled, prev=prev_enabled, reason='changed via /utils')
                    if delay != prev_delay:
                        router.record_sim_event(
                            'sharedinfo_change', field='load_marker_pause_delay',
                            value=delay, prev=prev_delay, reason='changed via /utils')
                    # Broadcast the new state to the network now rather
                    # than waiting for the next periodic SHAREDINFO send.
                    router.frdp_sharedinfo_requested = True
                return web.Response(
                    text=_LOAD_MARKER_PAUSE_PAGE.format(
                        rest_api_color_scheme=cs,
                        result_section=_LOAD_MARKER_PAUSE_SAVED_SECTION,
                        enabled_checked=' checked' if enabled else '',
                        delay=f"{delay:.3f}"),
                    content_type='text/html')

            @routes.get('/services')
            async def handle_services_get(_):
                cs = router.config.listen.rest_api_color_scheme
                return web.Response(
                    text=_build_services_page(router, cs), content_type='text/html')

            @routes.post('/api/services/{service}/{action}')
            async def handle_services_action(request):
                service = request.match_info['service']
                action = request.match_info['action']
                if action not in ('start', 'stop', 'restart'):
                    return web.Response(text=f"Invalid action: {action}", status=400)
                router.logger.info("API: requesting %s on service %s", action, service)
                payload = json.dumps({"action": action, "service": service})
                line = f"addon=FRANKENCONTROL:1:COMMAND:{payload}"
                # FrankenControl could be connected on either side of this
                # router (same pattern as the HAFAP/CPDLC reset button above).
                await router.send_to_upstream(line)
                await router.client_broadcast(line)
                raise web.HTTPFound('/services')

            @routes.get('/shutdown')
            async def handle_shutdown_get(_):
                if not router.config.listen.rest_api_shutdown_enabled:
                    return web.Response(
                        text="Shutdown via the web UI is disabled "
                             "(rest_api_shutdown_enabled is not set in the config file).",
                        status=403)
                data = {'rest_api_color_scheme': router.config.listen.rest_api_color_scheme}
                return web.json_response(
                    text=_SHUTDOWN_PAGE.format(**data), content_type='text/html')

            @routes.post('/api/shutdown/yes')
            async def handle_shutdown_yes(_):
                # Deliberately NOT gated on rest_api_shutdown_enabled: that flag
                # only controls the human-facing button/confirmation page (an
                # accidental-tap risk on a shared/EFB-embedded screen) -- a
                # direct call to this API (e.g. from a start script) should
                # always work, matching how most users already shut the
                # router down "by other means".
                router.logger.info("API: shutdown requested via web interface")
                router.shutdown_reason = "from the web interface"
                loop = asyncio.get_running_loop()

                def _do_shutdown():
                    # signal.SIG_DFL means "let the OS terminate the process" for
                    # SIGINT, NOT "raise KeyboardInterrupt" -- that's a different
                    # thing, signal.default_int_handler. Using SIG_DFL here (as a
                    # previous version of this code did) meant this button just
                    # hard-killed the router, skipping the graceful "exit"-to-
                    # every-client / clean-socket-close shutdown path entirely.
                    signal.signal(signal.SIGINT, signal.default_int_handler)
                    signal.raise_signal(signal.SIGINT)

                loop.call_later(0.5, _do_shutdown)
                data = {'rest_api_color_scheme': router.config.listen.rest_api_color_scheme}
                return web.json_response(
                    text=_SHUTDOWN_CONFIRM_PAGE.format(**data), content_type='text/html')

            @web.middleware
            async def cors_middleware(request, handler):
                response = await handler(request)
                response.headers['Access-Control-Allow-Origin'] = '*'
                return response

            app = web.Application(middlewares=[cors_middleware])
            app.add_routes(routes)
            app.router.add_static('/static', _STATIC_DIR)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, '0.0.0.0', router.config.listen.rest_api_port)
            await site.start()
            while True:
                await asyncio.sleep(3600.0)

        except asyncio.exceptions.CancelledError:
            router.logger.info("Task %s was cancelled, cleanup and exit", name)
            await runner.cleanup()
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            router.logger.critical(
                "Unhandled exception %s in %s, shutting down", exc, name)
            router.logger.critical(traceback.format_exc())
