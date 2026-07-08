"""Web API (REST + HTML UI) for frankenrouter."""
# pylint: disable=fixme,invalid-name,too-many-lines
import asyncio
import datetime
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

# Add psxhacks root to sys.path so fw_webui (first-party, at the root) can be imported.
_PSXHACKS = str(pathlib.Path(__file__).parent.parent.parent)
if _PSXHACKS not in sys.path:
    sys.path.insert(0, _PSXHACKS)

from aiohttp import web  # noqa: E402  pylint: disable=import-error,wrong-import-position

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
    '{checklist_warning}'
    '<div style="margin-left:auto">'
    '<a href="/" class="btn btn-gray btn-sm">Refresh</a>'
    '</div>'
    '</div>\n'
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1.5em;align-items:start">\n'
    '<div>\n'
    '{critical_errors}'
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
    '<a href="/flightinfo" class="btn btn-gray">Flight Info</a>\n'
    '<a href="/weather" class="btn btn-gray">Weather</a>\n'
    '<a href="/utils" class="btn btn-gray">Utils</a>\n'
    '{sessionpwd_button}'
    '</div>\n'
    '<div>\n'
    '{observer_mode_notice}'
    '{master_buttons}'
    '{observer_mode_button}'
    '<a href="/shutdown" class="btn btn-red">Shutdown router</a>\n'
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
    '<form method="post" action="/api/utils/towing/direction" style="display:inline">'
    '<button class="btn btn-gray">Toggle towing direction{towing_direction}</button></form>\n'
    '<form method="post" action="/api/utils/printer/reset" style="display:inline">'
    '<button class="btn btn-gray">Reset printer</button></form>\n'
    '</body>\n</html>\n'
)

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
    '  navigator.clipboard.writeText(pwd).then(function() {{\n'
    '    var btn = document.getElementById(btn_id);\n'
    '    btn.textContent = "Copied!";\n'
    '    setTimeout(function() {{ btn.textContent = "Copy to clipboard"; }}, 2000);\n'
    '  }});\n'
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

# Read-only briefing fields shown on /flightinfo once Flight Centre has
# linked this sim to a planned flight — everything here (plus seat_swap/
# p1_is_vatpri, handled separately) is now sourced from Flight Centre, not
# editable locally. See CLAUDE.md's Flight Centre / frankenrouter
# integration section.
_BRIEFING_FIELD_LABELS = {
    'portal_account': 'Portal account',
    'airline_icao': 'Airline ICAO',
    'airframe': 'Airframe',
    'captain_code': 'Captain (P1) / callsign suffix',
    'fo_code': 'First Officer (P2) / callsign suffix',
    'dep_airport': 'Departure (ICAO)',
    'arr_airport': 'Arrival (ICAO)',
    'flight_number': 'Portal flight number',
    'vatsim_callsign': 'VATSIM callsign',
    'preflight_starts': 'Preflight starts',
    'eobt': 'EOBT',
    'observers': 'Observers',
    'route': 'Planned route',
    'comments': 'Comments (SOP to use, etc.)',
}

_FLIGHTINFO_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n<style>\n'
    'body {{ max-width: 72em; }}\n'
    '.rb-grid {{ display:grid; grid-template-columns:repeat(3,1fr);'
    ' gap:0.6em 1.2em; margin:0.6em 0 1em; }}\n'
    '.rb-grid div {{ min-width:0; }}\n'
    '.rb-label {{ display:block; font-size:0.8em; color:#94a3b8; }}\n'
    '.rb-value {{ display:block; font-size:1em; word-wrap:break-word; }}\n'
    '#scratchpad_save.dirty {{ background:#b45309; }}\n'
    '</style>\n'
    '<script>\n'
    'var scratchpadDirty = false;\n'
    'var lastKnownVersion = "{last_updated_by}|{last_updated_at}";\n'
    'var READONLY_FIELDS = ["portal_account","airline_icao","airframe","captain_code",'
    '"fo_code","dep_airport","arr_airport","flight_number","vatsim_callsign",'
    '"preflight_starts","eobt","observers","route","comments"];\n'
    'function applyBriefing(data) {{\n'
    '  READONLY_FIELDS.forEach(function(id) {{\n'
    '    var el = document.getElementById("rb_" + id);\n'
    '    if (el) el.textContent = data[id] || "—";\n'
    '  }});\n'
    '  var seat = document.getElementById("rb_seat_swap");\n'
    '  if (seat) seat.textContent = data.seat_swap ? "Yes" : "No";\n'
    '  var vatpri = document.getElementById("rb_p1_is_vatpri");\n'
    '  if (vatpri) vatpri.textContent = data.p1_is_vatpri ? "Yes" : "No";\n'
    '  var linked = !!data.flight_plan_id;\n'
    '  var banner = document.getElementById("unlinked_banner");\n'
    '  var content = document.getElementById("linked_content");\n'
    '  if (banner) banner.style.display = linked ? "none" : "";\n'
    '  if (content) content.style.display = linked ? "" : "none";\n'
    '  if (data.checklist) {{\n'
    '    data.checklist.forEach(function(checked, i) {{\n'
    '      var el = document.getElementById("chk_" + i);\n'
    '      if (el) el.checked = checked;\n'
    '    }});\n'
    '  }}\n'
    '  if (!scratchpadDirty) {{\n'
    '    var sp = document.getElementById("scratchpad");\n'
    '    if (sp) sp.value = data.scratchpad || "";\n'
    '  }}\n'
    '  var note = document.getElementById("last_updated_note");\n'
    '  if (note) note.textContent ='
    ' "Last updated by: " + (data.last_updated_by || "") + " " + (data.last_updated_at || "");\n'
    '}}\n'
    'function pollBriefing() {{\n'
    '  fetch("/api/briefing")\n'
    '    .then(function(r) {{ return r.json(); }})\n'
    '    .then(function(data) {{\n'
    '      var v = (data.last_updated_by || "") + "|" + (data.last_updated_at || "");\n'
    '      if (v !== lastKnownVersion) {{\n'
    '        applyBriefing(data);\n'
    '        lastKnownVersion = v;\n'
    '      }}\n'
    '    }})\n'
    '    .catch(function() {{}});\n'
    '}}\n'
    'function toggleChecklistItem(index, checked) {{\n'
    '  var data = new FormData();\n'
    '  data.set("index", index);\n'
    '  data.set("checked", checked ? "1" : "0");\n'
    '  fetch("/api/flightinfo/checklist", {{method: "POST", body: data}}).catch(function() {{}});\n'
    '}}\n'
    'function saveScratchpad() {{\n'
    '  var data = new FormData();\n'
    '  data.set("scratchpad", document.getElementById("scratchpad").value);\n'
    '  fetch("/api/flightinfo/scratchpad", {{method: "POST", body: data}})\n'
    '    .then(function() {{\n'
    '      scratchpadDirty = false;\n'
    '      document.getElementById("scratchpad_save").classList.remove("dirty");\n'
    '    }})\n'
    '    .catch(function() {{}});\n'
    '}}\n'
    'document.addEventListener("DOMContentLoaded", function() {{\n'
    '  document.querySelectorAll(".cl-toggle").forEach(function(el) {{\n'
    '    el.addEventListener("change", function() {{\n'
    '      toggleChecklistItem(el.dataset.index, el.checked);\n'
    '    }});\n'
    '  }});\n'
    '  var sp = document.getElementById("scratchpad");\n'
    '  if (sp) {{\n'
    '    sp.addEventListener("input", function() {{\n'
    '      scratchpadDirty = true;\n'
    '      document.getElementById("scratchpad_save").classList.add("dirty");\n'
    '    }});\n'
    '  }}\n'
    '  var saveBtn = document.getElementById("scratchpad_save");\n'
    '  if (saveBtn) saveBtn.addEventListener("click", saveScratchpad);\n'
    '  setInterval(pollBriefing, 5000);\n'
    '}});\n'
    '</script>\n'
    '</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Flight information</h1>'
    '</div>\n'
    '<div class="btn-row">\n'
    '<a href="/" class="btn btn-gray">Back</a>\n'
    '<a href="/flightinfo" class="btn btn-gray">Refresh</a>\n'
    '</div>\n'
    '<div id="unlinked_banner" class="card warn" style="display:{unlinked_display}">'
    '<p style="margin:0">Sim is not linked to a planned flight in Flight Centre.'
    ' Plan a flight in Flight Centre with the same tail number and callsign'
    ' and it will show up here.</p>'
    '</div>\n'
    '<div id="linked_content" style="display:{linked_display}">\n'
    '<p id="last_updated_note" class="note" style="margin:0 0 0.4em">'
    'Last updated by: {last_updated_by} {last_updated_at}</p>\n'
    '{readonly_notice}'
    '<div class="rb-grid">\n'
    '{briefing_html}'
    '</div>\n'
    '{checklist_html}'
    '<label for="scratchpad">Inflight scratchpad</label>\n'
    '<textarea id="scratchpad" name="scratchpad"{scratchpad_disabled}>{scratchpad}</textarea>\n'
    '<button type="button" id="scratchpad_save" class="btn btn-blue"{scratchpad_save_disabled}>'
    'Save</button>\n'
    '</div>\n'
    '</body>\n</html>\n'
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
                # checklist_items comes from Flight Centre (see CLAUDE.md's
                # Flight Centre / frankenrouter integration section); an
                # empty/missing list (including "not linked") just means no
                # warning below, matching /flightinfo's own display.
                cl_items = router.flightinfo.get('checklist_items') or []
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
                    'checklist_warning': (
                        '' if not cl_items else
                        '' if (
                            len(router.flightinfo.get('checklist', [])) == len(cl_items) and
                            all(router.flightinfo['checklist'])
                        ) else
                        '<a href="/flightinfo" style="background:#92400e;color:#fbbf24;'
                        'padding:0.2em 0.7em;border-radius:0.4em;font-size:0.85em;'
                        'font-weight:600;white-space:nowrap;text-decoration:none">'
                        'Checklist incomplete</a>'
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

            @routes.get('/flightinfo')
            async def handle_flightinfo_get(_):
                upstream = router.upstream
                is_observer = (
                    upstream is not None and upstream.access_level == 'observer')
                readonly_notice = (
                    '<div class="card warn">'
                    '<p style="margin:0">Observer mode: flight info is read-only.</p>'
                    '</div>\n') if is_observer else ''

                flightinfo = router.flightinfo
                linked = flightinfo.get('flight_plan_id') is not None
                briefing_html = ''.join(
                    f'<div><span class="rb-label">{label}</span>'
                    f'<span class="rb-value" id="rb_{key}">'
                    f'{flightinfo.get(key) or "—"}</span></div>\n'
                    for key, label in _BRIEFING_FIELD_LABELS.items()
                )
                briefing_html += (
                    f'<div><span class="rb-label">Seat swap (Captain in right seat)</span>'
                    f'<span class="rb-value" id="rb_seat_swap">'
                    f'{"Yes" if flightinfo.get("seat_swap") else "No"}</span></div>\n'
                    f'<div><span class="rb-label">VATPRI swap (P1 is VATPRI)</span>'
                    f'<span class="rb-value" id="rb_p1_is_vatpri">'
                    f'{"Yes" if flightinfo.get("p1_is_vatpri") else "No"}</span></div>\n'
                )

                # checklist_items comes from Flight Centre (see CLAUDE.md's
                # Flight Centre / frankenrouter integration section); an
                # empty/missing list means "no checklist configured" and the
                # section below simply doesn't render. When unlinked this is
                # moot anyway — linked_content (see _FLIGHTINFO_PAGE) is
                # hidden entirely in favor of the unlinked_banner message.
                cl_items = flightinfo.get('checklist_items') or []
                cl_state = flightinfo.get('checklist', [])
                cl_disabled = ' disabled' if is_observer else ''
                checklist_html = (
                    '<h2>Pre-pre-flight checklist</h2>\n'
                    '<div class="grid3">\n' +
                    ''.join(
                        f'<label class="toggle-row">'
                        f'<span class="toggle-switch">'
                        f'<input type="checkbox" id="chk_{i}" class="cl-toggle" data-index="{i}"'
                        f'{"  checked" if i < len(cl_state) and cl_state[i] else ""}'
                        f'{cl_disabled}>'
                        f'<span class="toggle-track"></span>'
                        f'</span>'
                        f'<span>{item[:1].upper() + item[1:]}</span>'
                        f'</label>\n'
                        for i, item in enumerate(cl_items)
                    ) +
                    '</div>\n'
                ) if cl_items else ''

                data = {
                    'rest_api_color_scheme': router.config.listen.rest_api_color_scheme,
                    **flightinfo,
                    'readonly_notice': readonly_notice,
                    'briefing_html': briefing_html,
                    'checklist_html': checklist_html,
                    'linked_display': '' if linked else 'none',
                    'unlinked_display': 'none' if linked else '',
                    'scratchpad': flightinfo.get('scratchpad') or '',
                    'scratchpad_disabled': ' disabled' if is_observer else '',
                    'scratchpad_save_disabled': ' disabled' if is_observer else '',
                }
                return web.json_response(
                    text=_FLIGHTINFO_PAGE.format(**data), content_type='text/html')

            @routes.post('/api/flightinfo/checklist')
            async def handle_flightinfo_checklist_post(request):
                """Toggle a single checklist item.

                Sent immediately on click by the router UI rather than
                batched with other edits, since a checklist state matters
                to the whole crew right away.
                """
                upstream = router.upstream
                if upstream is not None and upstream.access_level == 'observer':
                    return web.json_response({'error': 'observer'}, status=403)
                post = await request.post()
                items = router.flightinfo.get('checklist_items') or []
                try:
                    index = int(post.get('index', ''))
                except (TypeError, ValueError):
                    return web.json_response({'error': 'bad index'}, status=400)
                if not 0 <= index < len(items):
                    return web.json_response({'error': 'bad index'}, status=400)
                checklist = list(router.flightinfo.get('checklist') or [])
                while len(checklist) < len(items):
                    checklist.append(False)
                checklist[index] = post.get('checked', '') == '1'
                router.flightinfo = {
                    **router.flightinfo,
                    'checklist': checklist,
                    # Explicitly overridden (not inherited from the prior FC
                    # push) so frankenpush's echo-suppression check sees this
                    # as a genuine local edit to relay back — see CLAUDE.md's
                    # Flight Centre / frankenrouter integration section.
                    'source': 'router',
                    'last_updated_by': router.config.identity.simulator,
                    'last_updated_at': datetime.datetime.now(
                        datetime.timezone.utc).strftime('%H:%Mz'),
                }
                router.logger.info("API: checklist item %d toggled", index)
                await router.send_frdp_flightinfo()
                return web.json_response({'ok': True})

            @routes.post('/api/flightinfo/scratchpad')
            async def handle_flightinfo_scratchpad_post(request):
                """Save the inflight scratchpad.

                Explicit Save button on the router UI, not autosaved on
                every keystroke.
                """
                upstream = router.upstream
                if upstream is not None and upstream.access_level == 'observer':
                    return web.json_response({'error': 'observer'}, status=403)
                post = await request.post()
                router.flightinfo = {
                    **router.flightinfo,
                    'scratchpad': str(post.get('scratchpad', '')),
                    # See the checklist handler above for why this is
                    # explicitly overridden rather than inherited.
                    'source': 'router',
                    'last_updated_by': router.config.identity.simulator,
                    'last_updated_at': datetime.datetime.now(
                        datetime.timezone.utc).strftime('%H:%Mz'),
                }
                router.logger.info("API: scratchpad saved and broadcast")
                await router.send_frdp_flightinfo()
                return web.json_response({'ok': True})

            @routes.get('/api/briefing')
            async def handle_briefing_get(_):
                return web.json_response(router.flightinfo)

            _fw_webui.register_weather_routes(
                routes,
                RouterFWContext(router, router.config.listen.rest_api_color_scheme),
            )

            @routes.get('/utils')
            async def handle_utils_get(_):
                cs = router.config.listen.rest_api_color_scheme
                tow_key = router.variables.get_keyword_for_name('Towing') or 'Towing'
                try:
                    from frankenrouter import routercache as _rc  # pylint: disable=import-outside-toplevel,no-name-in-module
                    tow_val = str(router.cache.get_value(tow_key))
                    tow_dir = (
                        ' (currently: pushback)' if tow_val[:1] == '1'
                        else ' (currently: pull forward)'
                    )
                except Exception:  # pylint: disable=broad-exception-caught
                    tow_dir = ''
                return web.Response(
                    text=_UTILS_PAGE.format(
                        rest_api_color_scheme=cs,
                        towing_direction=tow_dir),
                    content_type='text/html')

            @routes.get('/utils/events')
            async def handle_events_get(_):
                cs = router.config.listen.rest_api_color_scheme
                return web.Response(
                    text=_build_events_page(router, cs),
                    content_type='text/html')

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

            @routes.post('/api/utils/towing/direction')
            async def handle_towing_direction(_):
                from frankenrouter import routercache as _rc  # pylint: disable=import-outside-toplevel,no-name-in-module
                key = router.variables.get_keyword_for_name('Towing') or 'Towing'
                try:
                    current = router.cache.get_value(key)
                except _rc.RouterCacheException:
                    current = None
                current = str(current) if current is not None else None
                if current and len(current) >= 1:
                    new_dir = '1' if current[0] != '1' else '2'
                    new_towing = new_dir + current[1:]
                    router.logger.info("API: towing direction %s -> %s", current, new_towing)
                    await router.send_to_upstream(f"{key}={new_towing}")
                    router.cache.update(key, new_towing)
                    await router.client_broadcast(f"{key}={new_towing}")
                else:
                    router.logger.warning("API: Towing variable not available")
                raise web.HTTPFound('/utils')

            @routes.post('/api/utils/printer/reset')
            async def handle_printer_reset(_):
                router.logger.info("API: resetting printer (Qi115=1)")
                await router.send_to_upstream("Qi115=1")
                raise web.HTTPFound('/utils')

            @routes.get('/shutdown')
            async def handle_shutdown_get(_):
                data = {'rest_api_color_scheme': router.config.listen.rest_api_color_scheme}
                return web.json_response(
                    text=_SHUTDOWN_PAGE.format(**data), content_type='text/html')

            @routes.post('/api/shutdown/yes')
            async def handle_shutdown_yes(_):
                router.logger.info("API: shutdown requested via web interface")
                loop = asyncio.get_running_loop()

                def _do_shutdown():
                    signal.signal(signal.SIGINT, signal.SIG_DFL)
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
            router.logger.critical(__import__('traceback').format_exc())
