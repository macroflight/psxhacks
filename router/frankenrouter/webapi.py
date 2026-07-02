"""Web API (REST + HTML UI) for frankenrouter."""
# pylint: disable=fixme,invalid-name,too-many-lines
import asyncio
import datetime
import json
import math
import pathlib
import re
import secrets
import signal
import statistics
import string
import textwrap
import time

from aiohttp import web  # pylint: disable=import-error


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
    '<tr><td>Upstream</td>'
    '<td class="val">{upstream_label}</td></tr>\n'
    '<tr><td>Connection</td>'
    '<td class="{upstream_class}">{upstream_status}</td></tr>\n'
    '<tr><td>Elevation master</td>'
    '<td class="{elevation_source_class}">{elevation_source}</td></tr>\n'
    '<tr><td>Traffic master</td>'
    '<td class="{traffic_source_class}">{traffic_source}</td></tr>\n'
    '<tr><td>Pilot flying</td>'
    '<td class="{pilot_flying_class}">{pilot_flying}</td></tr>\n'
    '<tr><td>Connected simulators</td>'
    '<td class="val">{connected_sims}</td></tr>\n'
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

_FLIGHTINFO_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n<style>body {{ max-width: 72em; }}</style>\n'
    '<script>\n'
    'var autosaveEnabled = localStorage.getItem("flightinfo_autosave") === "1";\n'
    'var autosaveTimer = null;\n'
    'var isDirty = false;\n'
    'var lastKnownVersion = "{last_updated_by}|{last_updated_at}";\n'
    'var BRIEFING_FIELDS = ["portal_account","airline_icao","airframe","captain_code",'
    '"fo_code","dep_airport","arr_airport","flight_number","vatsim_callsign",'
    '"preflight_starts","eobt","observers","route","comments","scratchpad"];\n'
    'function updateAutosaveBtn() {{\n'
    '  var btn = document.getElementById("autosave_btn");\n'
    '  btn.textContent = "Autosave: " + (autosaveEnabled ? "ON" : "OFF");\n'
    '  btn.className = "btn " + (autosaveEnabled ? "btn-green" : "btn-gray");\n'
    '}}\n'
    'function toggleAutosave() {{\n'
    '  autosaveEnabled = !autosaveEnabled;\n'
    '  localStorage.setItem("flightinfo_autosave", autosaveEnabled ? "1" : "0");\n'
    '  updateAutosaveBtn();\n'
    '}}\n'
    'function scheduleAutosave() {{\n'
    '  isDirty = true;\n'
    '  if (!autosaveEnabled) return;\n'
    '  clearTimeout(autosaveTimer);\n'
    '  autosaveTimer = setTimeout(doAutosave, 5000);\n'
    '}}\n'
    'function crewValid() {{\n'
    '  var cap = document.getElementById("captain_code").value.trim();\n'
    '  var fo = document.getElementById("fo_code").value.trim();\n'
    '  return !(cap && fo && cap === fo);\n'
    '}}\n'
    'function doAutosave() {{\n'
    '  autosaveTimer = null;\n'
    '  if (!crewValid()) {{\n'
    '    var el = document.getElementById("autosave_status");\n'
    '    el.textContent = "Captain and FO cannot be the same.";\n'
    '    setTimeout(function() {{ el.textContent = ""; }}, 3000);\n'
    '    return;\n'
    '  }}\n'
    '  var form = document.getElementById("flightinfo_form");\n'
    '  var data = new FormData(form);\n'
    '  fetch("/api/flightinfo", {{method: "POST", body: data, redirect: "manual"}})\n'
    '    .then(function() {{\n'
    '      if (autosaveTimer === null) isDirty = false;\n'
    '      var el = document.getElementById("autosave_status");\n'
    '      el.textContent = "Autosaved";\n'
    '      setTimeout(function() {{ el.textContent = ""; }}, 2000);\n'
    '    }})\n'
    '    .catch(function() {{}});\n'
    '}}\n'
    'function applyBriefing(data) {{\n'
    '  BRIEFING_FIELDS.forEach(function(id) {{\n'
    '    var el = document.getElementById(id);\n'
    '    if (el) el.value = data[id] || "";\n'
    '  }});\n'
    '  var cb = document.querySelector("input[name=\\"seat_swap\\"]");\n'
    '  if (cb) cb.checked = !!data.seat_swap;\n'
    '  var cb2 = document.querySelector("input[name=\\"p1_is_vatpri\\"]");\n'
    '  if (cb2) cb2.checked = !!data.p1_is_vatpri;\n'
    '  if (data.checklist) {{\n'
    '    data.checklist.forEach(function(checked, i) {{\n'
    '      var el = document.getElementById("chk_" + i);\n'
    '      if (el) el.checked = checked;\n'
    '    }});\n'
    '  }}\n'
    '  var note = document.getElementById("last_updated_note");\n'
    '  if (note) note.textContent ='
    ' "Last updated by: " + (data.last_updated_by || "") + " " + (data.last_updated_at || "");\n'
    '}}\n'
    'function playNotification() {{\n'
    '  try {{\n'
    '    var ctx = new (window.AudioContext || window.webkitAudioContext)();\n'
    '    var osc = ctx.createOscillator();\n'
    '    var gain = ctx.createGain();\n'
    '    osc.connect(gain);\n'
    '    gain.connect(ctx.destination);\n'
    '    osc.type = "sine";\n'
    '    osc.frequency.setValueAtTime(880, ctx.currentTime);\n'
    '    osc.frequency.setValueAtTime(1100, ctx.currentTime + 0.08);\n'
    '    gain.gain.setValueAtTime(0.25, ctx.currentTime);\n'
    '    gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.35);\n'
    '    osc.start(ctx.currentTime);\n'
    '    osc.stop(ctx.currentTime + 0.35);\n'
    '  }} catch(e) {{}}\n'
    '}}\n'
    'function pollBriefing() {{\n'
    '  fetch("/api/briefing")\n'
    '    .then(function(r) {{ return r.json(); }})\n'
    '    .then(function(data) {{\n'
    '      var v = (data.last_updated_by || "") + "|" + (data.last_updated_at || "");\n'
    '      if (v !== lastKnownVersion && !isDirty) {{\n'
    '        applyBriefing(data);\n'
    '        lastKnownVersion = v;\n'
    '        playNotification();\n'
    '        var el = document.getElementById("autosave_status");\n'
    '        el.textContent = "Updated from network";\n'
    '        setTimeout(function() {{ el.textContent = ""; }}, 3000);\n'
    '      }}\n'
    '    }})\n'
    '    .catch(function() {{}});\n'
    '}}\n'
    'document.addEventListener("DOMContentLoaded", function() {{\n'
    '  updateAutosaveBtn();\n'
    '  var form = document.getElementById("flightinfo_form");\n'
    '  form.addEventListener("input", scheduleAutosave);\n'
    '  form.addEventListener("change", scheduleAutosave);\n'
    '  form.addEventListener("submit", function(e) {{\n'
    '    if (!crewValid()) {{\n'
    '      e.preventDefault();\n'
    '      document.getElementById("autosave_status").textContent ='
    ' "Captain and FO cannot be the same.";\n'
    '      return;\n'
    '    }}\n'
    '    isDirty = false;\n'
    '  }});\n'
    '  setInterval(pollBriefing, 5000);\n'
    '}});\n'
    '</script>\n'
    '</head>\n<body>\n'
    '<div class="page-title">'
    '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>Flight information</h1>'
    '</div>\n'
    '<p id="last_updated_note" class="note" style="margin:0 0 0.4em">'
    'Last updated by: {last_updated_by} {last_updated_at}</p>\n'
    '{clear_form}'
    '<div class="btn-row">\n'
    '{header_buttons}'
    '</div>\n'
    '<span id="autosave_status" class="note"'
    ' style="display:block;min-height:1.2em;margin:0.2em 0 0.5em"></span>\n'
    '<form id="flightinfo_form" action="/api/flightinfo" method="post">\n'
    '{checklist_html}'
    '<label for="scratchpad">Inflight scratchpad</label>\n'
    '<textarea id="scratchpad" name="scratchpad">{scratchpad}</textarea>\n'
    '<div class="grid3">\n'
    '<div><label for="portal_account">Portal account</label>'
    '<input type="text" id="portal_account" name="portal_account"'
    ' list="dl_portal" value="{portal_account}">'
    '{portal_account_datalist}</div>\n'
    '<div><label for="airline_icao">Airline ICAO</label>'
    '<input type="text" id="airline_icao" name="airline_icao"'
    ' list="dl_icao" value="{airline_icao}">'
    '{airline_icao_datalist}</div>\n'
    '<div><label for="airframe">Airframe</label>'
    '<input type="text" id="airframe" name="airframe"'
    ' list="dl_airframe" value="{airframe}">'
    '{airframe_datalist}</div>\n'
    '</div>\n'
    '{crew_datalist}'
    '<div class="grid4">\n'
    '<div><label for="captain_code">Captain (P1) / callsign suffix</label>'
    '<input type="text" id="captain_code" name="captain_code"'
    ' list="dl_crew" value="{captain_code}"></div>\n'
    '<div><label for="fo_code">First Officer (P2) / callsign suffix</label>'
    '<input type="text" id="fo_code" name="fo_code"'
    ' list="dl_crew" value="{fo_code}"></div>\n'
    '<div><label for="dep_airport">Departure (ICAO)</label>'
    '<input type="text" id="dep_airport" name="dep_airport"'
    ' value="{dep_airport}"></div>\n'
    '<div><label for="arr_airport">Arrival (ICAO)</label>'
    '<input type="text" id="arr_airport" name="arr_airport"'
    ' value="{arr_airport}"></div>\n'
    '</div>\n'
    '<div class="grid4">\n'
    '<div><label for="flight_number">Portal flight number</label>'
    '<input type="text" id="flight_number" name="flight_number"'
    ' value="{flight_number}"></div>\n'
    '<div><label for="vatsim_callsign">VATSIM callsign</label>'
    '<input type="text" id="vatsim_callsign" name="vatsim_callsign"'
    ' value="{vatsim_callsign}"></div>\n'
    '<div><label for="preflight_starts">Preflight starts (HHMMz)</label>'
    '<input type="text" id="preflight_starts" name="preflight_starts"'
    ' value="{preflight_starts}"></div>\n'
    '<div><label for="eobt">EOBT (HHMMz)</label>'
    '<input type="text" id="eobt" name="eobt" value="{eobt}"></div>\n'
    '</div>\n'
    '<div style="display:flex;gap:2em;flex-wrap:wrap">\n'
    '<label class="check"><input type="checkbox" name="seat_swap"'
    ' value="1" {seat_swap_checked}>Seat swap (Captain in right seat)</label>\n'
    '<label class="check"><input type="checkbox" name="p1_is_vatpri"'
    ' value="1" {p1_is_vatpri_checked}>VATPRI swap (Captain is VATPRI)</label>\n'
    '</div>\n'
    '<div class="grid2">\n'
    '<div><label for="observers">Observers</label>'
    '<textarea id="observers" name="observers">{observers}</textarea></div>\n'
    '<div><label for="route">Planned route</label>'
    '<textarea id="route" name="route">{route}</textarea></div>\n'
    '</div>\n'
    '<label for="comments">Comments (SOP to use, etc.)</label>\n'
    '<textarea id="comments" name="comments">{comments}</textarea>\n'
    '</form>\n'
    '{readonly_notice}'
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
        wind_enc = parts[18]                          # "VVVDDDss" or "000DDDss"
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


def _build_weather_map_page(router, color_scheme):  # pylint: disable=too-many-locals,too-many-statements
    """Render the /weather page with a Leaflet tile map centred on the aircraft."""
    from frankenrouter import routercache  # pylint: disable=import-outside-toplevel

    now = time.time()
    state = router.frankenweather_state
    received_at = router.frankenweather_received_at
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

    def _cache_get(name):
        key = router.variables.get_keyword_for_name(name) or name
        try:
            return router.cache.get_value(key)
        except routercache.RouterCacheException:
            return None

    focused_zone = None
    try:
        fz_val = _cache_get('FocussedWxZone')
        if fz_val is not None:
            focused_zone = int(fz_val)
    except (ValueError, TypeError):
        pass

    zone_data = []
    for zone in zones:
        zone_num = zone.get('zone')
        source = zone.get('source', 'OM')
        icao = zone.get('icao', '?')
        wx_str = _cache_get(f'Wx{zone_num}') or ''
        is_fake_icao = len(icao) == 4 and icao[0] == 'X' and icao[1:].isdigit()
        metar = None
        if not is_fake_icao:
            metar = _cache_get(f'Metar{zone_num}') or None
        wx = _parse_wx_brief(wx_str) or {}
        if not wx.get('cb_raw'):
            cb_m = re.search(r'CB (\d+)ok (\d+)-(\d+)ft', zone.get('reason', ''))
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
            'is_focused': focused_zone is not None and zone_num == focused_zone,
            'wx': wx,
            'metar': metar,
            'wx_raw': wx_str,
        })

    zones_js = json.dumps(zone_data)
    sigmets_js = json.dumps(state.get('sigmets', []))
    ac_alt_ft = state.get('ac_alt_ft') or 0
    turbstate = router.frankenweather_turbstate or {}
    turb_src_lat = turbstate.get('source_lat')
    turb_src_lon = turbstate.get('source_lon')
    turb_kind = turbstate.get('active_kind', 'none')
    turb_intensity = turbstate.get('active_intensity', 0.0)
    turb_pct = int(turb_intensity * 100)
    turb_label = _TURB_KIND_LABELS.get(turb_kind, turb_kind) if turbstate else '—'
    turb_color = '#22c55e' if turb_intensity < 0.25 else (
        '#f59e0b' if turb_intensity < 0.5 else '#ef4444')
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
    turb_js = 'null'
    if turb_src_lat is not None and turb_src_lon is not None:
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
        "  var pop='<div style=\"min-width:180px\">'"
        "    +'<b>WX'+z.zone+' — '+z.icao+'</b>'"
        "    +'<br><span style=\"color:#94a3b8;font-size:11px\">'+z.source_label+'</span>'"
        "    +(z.reason?'<br><span style=\"font-size:10px;color:#94a3b8\">'+z.reason+'</span>':'')"
        "    +'<table style=\"width:100%;border-collapse:collapse;margin-top:4px\">'"
        "    +(wx.wind?'<tr><td>Wind</td><td>'+wx.wind+'</td></tr>':'')"
        "    +(wx.lo_cloud?'<tr><td>Lo cloud</td><td>'+wx.lo_cloud+'</td></tr>':'')"
        "    +(wx.hi_cloud?'<tr><td>Hi cloud</td><td>'+wx.hi_cloud+'</td></tr>':'')"
        "    +cbRow"
        "    +(wx.vis?'<tr><td>Vis</td><td>'+wx.vis+'</td></tr>':'')"
        "    +(wx.temp?'<tr><td>Temp/QNH</td><td>'+wx.temp+' / '+wx.qnh+'</td></tr>':'')"
        "    +metarRow"
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
        '</script>\n'
    )

    mode_color = '#f59e0b' if nav_mode == 'MANEUVERING' else '#22c55e'
    fw_color = '#f59e0b' if fw_mode != 'enabled' else '#22c55e'
    age_str = f'{int(age_s)}s ago'

    side_panel = (
        '<div class="card" style="font-size:0.85em">\n'
        f'<div style="color:#94a3b8;font-size:0.8em">Data</div>'
        f'<div style="margin-bottom:0.5em"><b style="color:#e2e8f0">{age_str}</b></div>'
        f'<div style="color:#94a3b8;font-size:0.8em">Navigation</div>'
        f'<div style="margin-bottom:0.5em">'
        f'<b style="color:{mode_color}">{nav_mode}</b></div>'
        f'<div style="color:#94a3b8;font-size:0.8em">FrankenWeather</div>'
        f'<div><b style="color:{fw_color}">{fw_mode}</b></div>'
        '</div>\n'
        '<div class="card" style="font-size:0.85em">\n'
        f'<div style="color:#94a3b8;font-size:0.8em">Turbulence</div>'
        f'<div style="margin-bottom:0.25em"><b>{turb_label}</b></div>'
        f'<div><b style="color:{turb_color}">{turb_pct}%</b></div>'
        '</div>\n'
        '<div style="display:flex;flex-direction:column;gap:0.5em">\n'
        '<a href="/weather/settings" class="btn btn-gray btn-sm">Weather zone settings</a>\n'
        '<a href="/weather/turbulence" class="btn btn-gray btn-sm">Turbulence</a>\n'
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
        '<strong style="color:#f1f5f9">PSCC forum</strong> or '
        '<strong style="color:#f1f5f9">Macroflight</strong>:</p>\n'
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


def _build_weather_settings_page(router, color_scheme):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Render the /weather/settings HTML page from the cached FRANKENWEATHER state."""
    from frankenrouter import routercache  # pylint: disable=import-outside-toplevel

    now = time.time()
    state = router.frankenweather_state
    received_at = router.frankenweather_received_at
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

    # -- Header status card --
    age_str = f'{int(age_s)}s ago'
    mode = state.get('mode', '?')
    mode_class = 'warn' if mode == 'MANEUVERING' else 'ok'
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
        f'<tr><td>Mode</td><td class="{mode_class}">{mode}</td></tr>\n'
        f'<tr><td>Aircraft position</td><td class="val">{pos_str}</td></tr>\n'
        f'<tr><td>Heading / Altitude</td><td class="val">{hdg_str} / {alt_str}</td></tr>\n'
        '</table>\n</div>\n'
    )

    # -- Mode control --
    fw_mode = state.get('fw_mode', 'enabled')
    fw_mode_class = 'ok' if fw_mode == 'enabled' else 'warn'
    _MODE_BTN = {'enabled': 'btn-green', 'paused': 'btn-amber', 'disabled': 'btn-red'}
    _MODE_LABEL = {'enabled': 'Enable', 'paused': 'Pause', 'disabled': 'Disable'}
    other_modes = [m for m in ('enabled', 'paused', 'disabled') if m != fw_mode]
    body += (
        '<h2>Mode control</h2>\n'
        '<div class="card ok">\n<table>\n'
        f'<tr><td>Current mode</td><td class="{fw_mode_class}">{fw_mode}</td></tr>\n'
        '</table>\n</div>\n'
        '<div class="btn-row">\n'
    )
    for m in other_modes:
        body += (
            f'<form action="/api/weather/mode" method="post" style="display:inline">\n'
            f'<input type="hidden" name="mode" value="{m}">\n'
            f'<button type="submit" class="btn {_MODE_BTN[m]}">{_MODE_LABEL[m]}</button>\n'
            '</form>\n'
        )
    body += '</div>\n'

    # -- Config --
    cfg = state.get('config', {})
    infront = cfg.get('new_zone_infront_range', [0, 0])
    leftright = cfg.get('new_zone_leftright_range', [0, 0])
    squeeze = cfg.get('cape_squeeze')
    fake_cb = cfg.get('fake_cb')

    def _yn(v):
        return '<span style="color:#4ade80">Yes</span>' if v else 'No'

    body += (
        '<h2>Configuration</h2>\n'
        '<div class="card ok">\n<table>\n'
        f'<tr><td>Cruise altitude</td><td class="val">{cfg.get("cruise_alt", "?")} ft</td></tr>\n'
        f'<tr><td>Cruise behind relocation</td>'
        f'<td class="val">{cfg.get("cruise_behind_dist", "?")} nm</td></tr>\n'
        f'<tr><td>Low-alt relocation</td>'
        f'<td class="val">{cfg.get("low_alt_dist", "?")} nm</td></tr>\n'
        f'<tr><td>Zone range ahead</td>'
        f'<td class="val">{infront[0]}–{infront[1]} nm</td></tr>\n'
        f'<tr><td>Zone range lateral</td>'
        f'<td class="val">{leftright[0]}–{leftright[1]} nm</td></tr>\n'
        f'<tr><td>Min zone separation</td>'
        f'<td class="val">{cfg.get("new_zone_notnear", "?")} nm</td></tr>\n'
        f'<tr><td>Dep/dst airport zone dist</td>'
        f'<td class="val">{cfg.get("arpt_zone_dist", "?")} nm</td></tr>\n' +
        (f'<tr><td>CAPE squeeze</td><td class="val">'
         f'at {squeeze[0]} J/kg → min {squeeze[1]} nm ahead</td></tr>\n'
         if squeeze else '') +
        (f'<tr><td>Fake CB override</td><td class="warn">'
         f'{fake_cb[0]} oktas base {fake_cb[1]} ft top {fake_cb[2]} ft</td></tr>\n'
         if fake_cb else '') +
        ('<tr><td>PSX updates</td><td class="warn">DISABLED</td></tr>\n'
         if cfg.get('disable_psx_weather_updates') else '') +
        f'<tr><td>MSFS in-cloud sync</td>'
        f'<td class="val">{_yn(cfg.get("msfs_in_cloud_sync"))}</td></tr>\n'
        f'<tr><td>MSFS QNH check</td>'
        f'<td class="val">{cfg.get("msfs_qnh_check") or "Off"}</td></tr>\n'
        f'<tr><td>MSFS wind sync</td>'
        f'<td class="val">{_yn(cfg.get("msfs_wind_sync"))}</td></tr>\n'
        '</table>\n</div>\n'
    )

    # -- Zones --
    zones = state.get('zones', [])
    body += '<h2>Weather zones</h2>\n'
    body += '<div style="display:grid;grid-template-columns:1fr 1fr;gap:1em;align-items:start">\n'

    def _cache_get_s(name):
        key = router.variables.get_keyword_for_name(name) or name
        try:
            return router.cache.get_value(key)
        except routercache.RouterCacheException:
            return None

    focused_zone = None
    try:
        fz_val = _cache_get_s('FocussedWxZone')
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
        pos_str2 = f'{lat:.2f}/{lon:.2f}' if lat is not None else '—'
        src_color = '#4ade80' if source == 'VATSIM' else '#94a3b8'

        wx_str = _cache_get_s(f'Wx{zone_num}') or ''
        wx = _parse_wx_brief(wx_str)

        metar_str = None
        if source == 'VATSIM':
            metar_str = _cache_get_s(f'Metar{zone_num}')

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
            f'<p class="note" style="margin:0 0 0.4em">{reason or "—"}</p>\n'
        )
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
    body += '<hr>\n<a href="/weather" class="btn btn-gray">Back to map</a>\n'
    return _page(body)


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


_TURB_TYPES_ORDER = ('wave', 'rotor', 'mechanical', 'shear', 'cb', 'pirep', 'cape', 'gairmet')
_TURB_KIND_LABELS = {
    'wave': 'Mountain wave', 'rotor': 'Lee rotor', 'mechanical': 'Mechanical',
    'shear': 'Wind shear CAT', 'cb': 'CB proximity', 'pirep': 'PIREP',
    'cape': 'CAPE convective', 'gairmet': 'G-AIRMET',
}


def _build_weather_turb_page(router, color_scheme):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Render the /weather/turbulence control page."""
    now = time.time()
    tstate = router.frankenweather_turbstate or {}
    received_at = router.frankenweather_turbstate_received_at
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
            '<a href="/weather/settings" class="btn btn-gray btn-sm">Weather zone settings</a>'
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

    # Status section
    intensity_pct = int(active_intensity * 100)
    intensity_color = '#22c55e' if active_intensity < 0.25 else (
        '#f59e0b' if active_intensity < 0.5 else '#ef4444')
    on_off_label = 'ON' if enabled else 'OFF'

    status_html = (
        '<div class="card">'
        f'<div style="display:flex;align-items:center;gap:1em;flex-wrap:wrap">'
        f'<div><span style="color:#94a3b8;font-size:0.85em">Status</span><br>'
        f'<b style="color:#{"22c55e" if enabled else "ef4444"}">{on_off_label}</b></div>'
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
    status_html += (
        f'<div style="font-size:0.75em;color:#64748b;margin-top:0.5em">Data: {age_str}</div>'
    )
    status_html += '</div>\n'

    # Global controls section
    global_html = (
        '<div class="card">'
        '<h3 style="margin:0 0 0.75em">Global controls</h3>'
        '<table style="border-collapse:collapse;width:auto">'
        '<tr><td style="padding:4px 8px;color:#94a3b8">Intensity bias (%)</td>'
        f'<td style="padding:4px 8px">{_bias_input("intensity_bias", intensity_bias)}</td></tr>'
        '<tr><td style="padding:4px 8px;color:#94a3b8">CB lateral size bias (%)</td>'
        f'<td style="padding:4px 8px">{_bias_input("lateral_size_bias", lat_bias)}</td></tr>'
        '<tr><td style="padding:4px 8px;color:#94a3b8">MSFS influence magnitude (%)</td>'
        f'<td style="padding:4px 8px">'
        f'<form method="post" action="/api/weather/turbulence" style="display:inline-flex;'
        f'gap:0.4em;align-items:center">'
        f'<input type="number" name="msfs_turb_magnitude" value="{msfs_turb_magnitude}"'
        f' min="0" max="200" style="width:4.5em">'
        f'<button class="btn btn-gray btn-sm">Set</button></form>'
        f'</td></tr>'
        '</table>'
        '</div>\n'
    )

    # MSFS bridge card
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
        '<h3 style="margin:0 0 0.75em">MSFS bridge</h3>'
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

    # Wind mode section
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

    # Per-type table section
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
        '<h3 style="margin:0 0 0.75em">Turbulence types</h3>'
        '<table class="turb-types">'
        '<thead><tr><th>Type</th><th>Enable</th><th>Bias</th></tr></thead>'
        '<tbody>' + type_rows + '</tbody>'
        '</table>'
        '</div>\n'
    )

    body = stale_banner + status_html + global_html + msfs_html + wind_html + types_html
    return _page(body)


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


def _evt_describe(evt, get_var_name):  # pylint: disable=too-many-return-statements,too-many-locals
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
        prev_str = f', was: {prev}' if prev is not None else ''
        return f'MCP window — {name}: {value}{prev_str}', f'{key}={value}'
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
            async def handle_web(_):
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
                data = {
                    'rest_api_color_scheme': router.config.listen.rest_api_color_scheme,
                    'this_sim': router.config.identity.simulator,
                    'upstream_label': upstream_label,
                    'upstream_status': (
                        router.upstream.access_level.capitalize()
                        if connected else 'Not connected'),
                    'upstream_class': (
                        'ok' if connected and (
                            router.upstream.access_level == 'crew' or
                            router.config.identity.type == 'master')
                        else 'warn'),
                    'elevation_source': (
                        elevation_source + ' (this sim)'
                        if elevation_source == own_sim else elevation_source),
                    'elevation_source_class': (
                        'warn' if elevation_source == 'NOSIM' else 'ok'),
                    'traffic_source': (
                        traffic_source + ' (this sim)'
                        if traffic_source == own_sim else traffic_source),
                    'traffic_source_class': (
                        'warn' if traffic_source == 'NOSIM' else 'ok'),
                    'pilot_flying': (
                        pilot_flying + ' (this sim)' if pilot_flying == own_sim
                        else pilot_flying),
                    'pilot_flying_class': (
                        'ok' if pilot_flying == own_sim
                        else 'warn' if pilot_flying == 'ALL_CONTROL_LOCKS'
                        else 'val'),
                    'connected_sims': ', '.join(
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
                    ) or 'unknown',
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
                        '' if router.config.identity.type == 'master' else
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
                        '' if not router.config.sharedinfo.checklist else
                        '' if (
                            len(router.flightinfo.get('checklist', [])) ==
                            len(router.config.sharedinfo.checklist) and
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
                autosave_btn = (
                    '<button type="button" id="autosave_btn"'
                    ' onclick="toggleAutosave()" class="btn btn-gray">'
                    'Autosave: OFF</button>\n')
                nav_btns = (
                    '<a href="/" class="btn btn-gray">Back</a>\n'
                    '<a href="/flightinfo" class="btn btn-gray">Refresh</a>\n')
                if is_observer:
                    clear_form = ''
                    header_buttons = nav_btns + autosave_btn
                    readonly_notice = (
                        '<div class="card warn">'
                        '<p style="margin:0">Observer mode: flight info is read-only.</p>'
                        '</div>\n')
                else:
                    clear_form = (
                        '<form id="flightinfo_clear_form"'
                        ' action="/api/flightinfo/clear" method="post"'
                        ' style="display:none"></form>\n')
                    header_buttons = (
                        nav_btns + autosave_btn +
                        '<button type="submit" form="flightinfo_clear_form"'
                        ' class="btn btn-gray">Clear all fields</button>\n'
                        '<button type="submit" form="flightinfo_form"'
                        ' class="btn btn-blue">Save and broadcast</button>\n')
                    readonly_notice = ''
                crew_datalist = (
                    '<datalist id="dl_crew">' +
                    ''.join(
                        f'<option value="{m.portal_name} / {m.callsign_suffix}">'
                        for m in router.config.sharedinfo.crew
                    ) +
                    '</datalist>\n')
                airframe_datalist = (
                    '<datalist id="dl_airframe">' +
                    ''.join(
                        f'<option value="{a}">'
                        for a in router.config.sharedinfo.airframes
                    ) +
                    '</datalist>')
                portal_account_datalist = (
                    '<datalist id="dl_portal">' +
                    ''.join(
                        f'<option value="{a}">'
                        for a in router.config.sharedinfo.portal_accounts
                    ) +
                    '</datalist>')
                airline_icao_datalist = (
                    '<datalist id="dl_icao">' +
                    ''.join(
                        f'<option value="{a}">'
                        for a in router.config.sharedinfo.airline_icao
                    ) +
                    '</datalist>')
                cl_items = router.config.sharedinfo.checklist
                cl_state = router.flightinfo.get('checklist', [])
                cl_disabled = ' disabled' if is_observer else ''
                checklist_html = (
                    '<h2>Pre-pre-flight checklist</h2>\n'
                    '<div class="grid3">\n' +
                    ''.join(
                        f'<label class="toggle-row">'
                        f'<span class="toggle-switch">'
                        f'<input type="checkbox" id="chk_{i}" name="chk_{i}" value="1"'
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
                    **router.flightinfo,
                    'seat_swap_checked': 'checked' if router.flightinfo.get('seat_swap') else '',
                    'p1_is_vatpri_checked': (
                        'checked' if router.flightinfo.get('p1_is_vatpri') else ''),
                    'clear_form': clear_form,
                    'header_buttons': header_buttons,
                    'readonly_notice': readonly_notice,
                    'crew_datalist': crew_datalist,
                    'airframe_datalist': airframe_datalist,
                    'portal_account_datalist': portal_account_datalist,
                    'airline_icao_datalist': airline_icao_datalist,
                    'checklist_html': checklist_html,
                }
                return web.json_response(
                    text=_FLIGHTINFO_PAGE.format(**data), content_type='text/html')

            @routes.post('/api/flightinfo')
            async def handle_flightinfo_post(request):
                upstream = router.upstream
                if upstream is not None and upstream.access_level == 'observer':
                    raise web.HTTPFound('/flightinfo')
                post = await request.post()
                now_z = datetime.datetime.now(
                    datetime.timezone.utc).strftime('%H:%Mz')
                router.flightinfo = {
                    'last_updated_by': router.config.identity.simulator,
                    'last_updated_at': now_z,
                    'portal_account': str(post.get('portal_account', '')),
                    'airline_icao': str(post.get('airline_icao', '')),
                    'airframe': str(post.get('airframe', '')),
                    'captain_code': str(post.get('captain_code', '')),
                    'fo_code': str(post.get('fo_code', '')),
                    'seat_swap': post.get('seat_swap', '') == '1',
                    'p1_is_vatpri': post.get('p1_is_vatpri', '') == '1',
                    'observers': str(post.get('observers', '')),
                    'flight_number': str(post.get('flight_number', '')),
                    'vatsim_callsign': str(post.get('vatsim_callsign', '')),
                    'dep_airport': str(post.get('dep_airport', '')),
                    'arr_airport': str(post.get('arr_airport', '')),
                    'route': str(post.get('route', '')),
                    'preflight_starts': str(post.get('preflight_starts', '')),
                    'eobt': str(post.get('eobt', '')),
                    'comments': str(post.get('comments', '')),
                    'scratchpad': str(post.get('scratchpad', '')),
                    'checklist': [
                        post.get(f'chk_{i}', '') == '1'
                        for i in range(len(router.config.sharedinfo.checklist))
                    ],
                }
                router.logger.info("API: flight information updated and broadcast")
                await router.send_frdp_flightinfo()
                raise web.HTTPFound('/flightinfo')

            @routes.post('/api/flightinfo/clear')
            async def handle_flightinfo_clear(_):
                upstream = router.upstream
                if upstream is not None and upstream.access_level == 'observer':
                    raise web.HTTPFound('/flightinfo')
                now_z = datetime.datetime.now(
                    datetime.timezone.utc).strftime('%H:%Mz')
                router.flightinfo = {
                    k: (False if k in ('seat_swap', 'p1_is_vatpri')
                        else [] if k == 'checklist' else '')
                    for k in router.flightinfo
                }
                router.flightinfo['last_updated_by'] = router.config.identity.simulator
                router.flightinfo['last_updated_at'] = now_z
                router.logger.info("API: flight information cleared and broadcast")
                await router.send_frdp_flightinfo()
                raise web.HTTPFound('/flightinfo')

            @routes.get('/api/briefing')
            async def handle_briefing_get(_):
                return web.json_response(router.flightinfo)

            @routes.get('/weather')
            async def handle_weather_get(_):
                html = _build_weather_map_page(
                    router, router.config.listen.rest_api_color_scheme)
                return web.json_response(text=html, content_type='text/html')

            @routes.get('/weather/settings')
            async def handle_weather_settings_get(_):
                html = _build_weather_settings_page(
                    router, router.config.listen.rest_api_color_scheme)
                return web.json_response(text=html, content_type='text/html')

            @routes.get('/weather/turbulence')
            async def handle_weather_turb_get(_):
                html = _build_weather_turb_page(
                    router, router.config.listen.rest_api_color_scheme)
                return web.json_response(text=html, content_type='text/html')

            @routes.post('/api/weather/turbulence')
            async def handle_weather_turb_post(request):
                data = await request.post()
                cmd = {}
                if 'enabled' in data:
                    cmd['enabled'] = data['enabled'].lower() == 'true'
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
                    tstate = router.frankenweather_turbstate or {}
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
                    line = f"addon=FRANKENWEATHER:TURBCOMMAND:{json.dumps(cmd)}"
                    await router.send_to_upstream(line)
                    await router.client_broadcast(line)
                    await asyncio.sleep(1)
                raise web.HTTPFound('/weather/turbulence')

            @routes.post('/api/weather/mode')
            async def handle_weather_mode(request):
                data = await request.post()
                new_mode = str(data.get('mode', ''))
                if new_mode not in ('enabled', 'paused', 'disabled'):
                    return web.Response(text="Invalid mode", status=400)
                cmd = f'{{"mode":"{new_mode}"}}'
                line = f"addon=FRANKENWEATHER:COMMAND:{cmd}"
                await router.send_to_upstream(line)
                await router.client_broadcast(line)
                await asyncio.sleep(3)
                raise web.HTTPFound('/weather/settings')

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
