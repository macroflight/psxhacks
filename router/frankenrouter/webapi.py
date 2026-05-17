"""Web API (REST + HTML UI) for frankenrouter."""
# pylint: disable=fixme,invalid-name,too-many-lines
import asyncio
import datetime
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
.btn-row a.btn {{ flex: 1; width: auto; margin: 0; }}
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
</style>'''

_INDEX_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n</head>\n<body>\n'
    '<h1>Frankenrouter &mdash; {this_sim}</h1>\n'
    '<div class="status-area">\n'
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
    '<div class="status-logo">'
    '<img src="/static/frankentech.png" alt="Frankentech">'
    '<a href="/flightinfo" class="btn btn-gray btn-sm">Flight Info</a>'
    '</div>\n'
    '</div>\n'
    '{master_buttons}'
    '<hr>\n'
    '<a href="/upstream" class="btn btn-blue">Change upstream</a>\n'
    '{sessionpwd_button}'
    '<a href="/shutdown" class="btn btn-red">Shutdown router</a>\n'
    '<hr>\n'
    '<a href="/" class="btn btn-gray">Refresh</a>\n'
    '</body>\n</html>\n'
)

_SHUTDOWN_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n'
    '<meta name="color-scheme" content="{rest_api_color_scheme}" />\n' +
    _COMMON_CSS +
    '\n</head>\n<body>\n'
    '<div class="page-title">'
    '<img src="/static/frankentech.png" alt="">'
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
    '<img src="/static/frankentech.png" alt="">'
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
    '<img src="/static/frankentech.png" alt="">'
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
    '</head>\n<body>\n'
    '<div class="page-title">'
    '<img src="/static/frankentech.png" alt="">'
    '<h1>Upstream connection</h1>'
    '</div>\n'
    '<h2>Current connection</h2>\n'
    '<div class="card {status_class}">\n'
    '<table>\n'
    '{current_rows}'
    '</table>\n'
    '</div>\n'
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
    '{presets}'
    '<hr>\n'
    '<a href="/upstream" class="btn btn-gray">Refresh</a>\n'
    '<a href="/" class="btn btn-gray">Back</a>\n'
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
    '<img src="/static/frankentech.png" alt="">'
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
    '  btn.className = "btn btn-sm " + (autosaveEnabled ? "btn-green" : "btn-gray");\n'
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
    'function doAutosave() {{\n'
    '  autosaveTimer = null;\n'
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
    '  form.addEventListener("submit", function() {{ isDirty = false; }});\n'
    '  setInterval(pollBriefing, 5000);\n'
    '}});\n'
    '</script>\n'
    '</head>\n<body>\n'
    '<div class="page-title">'
    '<img src="/static/frankentech.png" alt="">'
    '<h1>Flight information</h1>'
    '</div>\n'
    '<div style="display:flex;align-items:center;gap:1em;margin-bottom:0.75em;">'
    '<p id="last_updated_note" class="note" style="margin:0">'
    'Last updated by: {last_updated_by} {last_updated_at}</p>'
    '<button type="button" id="autosave_btn" onclick="toggleAutosave()">Autosave: OFF</button>'
    '<span id="autosave_status" class="note"></span>'
    '</div>\n'
    '<form id="flightinfo_form" action="/api/flightinfo" method="post">\n'
    '<div class="grid3">\n'
    '<div><label for="portal_account">Portal account</label>'
    '<input type="text" id="portal_account" name="portal_account"'
    ' list="dl_portal" value="{portal_account}">'
    '<datalist id="dl_portal"><option value="pscc@mkro.se"></datalist></div>\n'
    '<div><label for="airline_icao">Airline ICAO</label>'
    '<input type="text" id="airline_icao" name="airline_icao"'
    ' list="dl_icao" value="{airline_icao}">'
    '<datalist id="dl_icao"><option value="BAW"><option value="DLH">'
    '<option value="GST"></datalist></div>\n'
    '<div><label for="airframe">Airframe</label>'
    '<input type="text" id="airframe" name="airframe"'
    ' list="dl_airframe" value="{airframe}">'
    '<datalist id="dl_airframe"><option value="BAW B744 G-CCIVB">'
    '<option value="DLH B744 D-ABVW"><option value="SuperTanker N744ST">'
    '</datalist></div>\n'
    '</div>\n'
    '<div class="grid4">\n'
    '<div><label for="captain_code">Captain (P1)</label>'
    '<input type="text" id="captain_code" name="captain_code"'
    ' value="{captain_code}"></div>\n'
    '<div><label for="fo_code">First Officer (P2)</label>'
    '<input type="text" id="fo_code" name="fo_code" value="{fo_code}"></div>\n'
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
    '<label class="check"><input type="checkbox" name="seat_swap"'
    ' value="1" {seat_swap_checked}>Seat swap (Captain in right seat)</label>\n'
    '<div class="grid2">\n'
    '<div><label for="observers">Observers</label>'
    '<textarea id="observers" name="observers">{observers}</textarea></div>\n'
    '<div><label for="route">Planned route</label>'
    '<textarea id="route" name="route">{route}</textarea></div>\n'
    '</div>\n'
    '<label for="comments">Comments (SOP to use, etc.)</label>\n'
    '<textarea id="comments" name="comments">{comments}</textarea>\n'
    '<label for="scratchpad">Inflight scratchpad</label>\n'
    '<textarea id="scratchpad" name="scratchpad">{scratchpad}</textarea>\n'
    '{action_buttons}'
    '</form>\n'
    '<hr>\n'
    '{readonly_notice}'
    '<a href="/flightinfo" class="btn btn-gray">Refresh</a>\n'
    '<a href="/" class="btn btn-gray">Back</a>\n'
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


class RouterWebAPI:  # pylint: disable=too-few-public-methods
    """Owns the aiohttp application and all REST/HTML route handlers."""

    def __init__(self, router):
        """Store reference to the router instance."""
        self.router = router

    async def run(self, name):  # pylint: disable=too-many-statements,too-many-locals
        """Start the HTTP server and serve until cancelled."""
        from frankenrouter import routercache  # pylint: disable=import-outside-toplevel
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
                is_observer = (
                    connected and router.upstream.access_level == 'observer')
                if router.get_router_type() == 'slave' and not is_observer:
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
                sim_names = sorted({
                    info['simulator_name']
                    for info in router.routerinfo.values()
                    if 'simulator_name' in info
                })
                data = {
                    'rest_api_color_scheme': router.config.listen.rest_api_color_scheme,
                    'this_sim': router.config.identity.simulator,
                    'upstream_label': upstream_label,
                    'upstream_status': (
                        router.upstream.access_level.capitalize()
                        if connected else 'Not connected'),
                    'upstream_class': (
                        'ok' if connected and router.upstream.access_level == 'crew'
                        else 'warn' if connected else 'warn'),
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
                    'connected_sims': ', '.join(sim_names) if sim_names else 'unknown',
                    'master_buttons': master_buttons,
                    'sessionpwd_button': (
                        '<a href="/sessionpwd" class="btn btn-blue">Session password</a>\n'
                        if router.get_router_type() == 'master' else ''
                    ),
                }
                return web.json_response(
                    text=_INDEX_PAGE.format(**data), content_type='text/html')

            @routes.get('/api/flightinfo')
            async def handle_clightinfo_get(_):
                router.logger.info("GOT /flightinfo API call")
                try:
                    acft_state = router.cache.get_value('Qs121')
                    route = router.cache.get_value('Qs376')
                    fltno = router.cache.get_value('Qs401')
                except routercache.RouterCacheException:
                    return web.json_response({'ok_data': False})
                if acft_state is None:
                    return web.json_response({'ok_data': False})

                PiBaHeAlTas = acft_state.split(';')
                pitch = math.degrees(float(PiBaHeAlTas[0]) / 1000000)
                bank = math.degrees(float(PiBaHeAlTas[1]) / 1000000)
                heading_true = math.degrees(float(PiBaHeAlTas[2]))
                alt_true_ft = float(PiBaHeAlTas[3]) / 1000
                tas = float(PiBaHeAlTas[4]) / 1000
                lat = math.degrees(float(PiBaHeAlTas[5]))
                lon = math.degrees(float(PiBaHeAlTas[6]))
                router.logger.info("Returned OK info for /flightinfo API call")
                return web.json_response({
                    'ok_data': True,
                    'latitude_degrees': lat,
                    'longitude_degrees': lon,
                    'altitude_feet': alt_true_ft,
                    'heading_degrees': heading_true,
                    'speed_knots': tas,
                    'pitch_degrees': pitch,
                    'bank_degrees': bank,
                    'route': route,
                    'flight': fltno,
                })

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
                if is_observer:
                    action_buttons = ''
                    readonly_notice = (
                        '<div class="card warn">'
                        '<p style="margin:0">Observer mode: flight info is read-only.</p>'
                        '</div>\n<hr>\n')
                else:
                    action_buttons = (
                        '<input type="submit" value="Save and broadcast" class="btn-blue">\n')
                    readonly_notice = (
                        '<form action="/api/flightinfo/clear" method="post">\n'
                        '<input type="submit" value="Clear all fields" class="btn-gray">\n'
                        '</form>\n<hr>\n')
                data = {
                    'rest_api_color_scheme': router.config.listen.rest_api_color_scheme,
                    **router.flightinfo,
                    'seat_swap_checked': 'checked' if router.flightinfo.get('seat_swap') else '',
                    'action_buttons': action_buttons,
                    'readonly_notice': readonly_notice,
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
                    k: (False if k == 'seat_swap' else '')
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
