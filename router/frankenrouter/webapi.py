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
    '  padding: 2px 6px !important; box-shadow: none !important; }\n'
    '.zone-label::before { display: none !important; }\n'
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


def _build_weather_map_page(router, color_scheme):  # pylint: disable=too-many-locals
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
            '\n<style>body { max-width: 80em; }</style>\n' +
            _LEAFLET_HEAD +
            '</head>\n<body>\n'
            '<div class="page-title">'
            '<a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
            '<h1>Weather</h1>'
            '<div style="margin-left:auto">'
            '<a href="/weather" class="btn btn-gray btn-sm">Refresh</a>'
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
            'source_label': 'VATSIM' if source == 'VATSIM' else 'OpenMeteo',
            'reason': zone.get('reason', ''),
            'is_focused': focused_zone is not None and zone_num == focused_zone,
            'wx': wx,
            'metar': metar,
        })

    zones_js = json.dumps(zone_data)
    script = (
        '<script>\n'
        f'var acLat={ac_lat},acLon={ac_lon},acHdg={ac_hdg:.0f};\n'
        f'var zones={zones_js};\n'
        "var map=L.map('map').setView([acLat,acLon],7);\n"
        "L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',{\n"
        "  attribution:'&copy; <a href=\"https://www.openstreetmap.org/copyright\">"
        "OpenStreetMap</a> &copy; <a href=\"https://carto.com/attributions\">CARTO</a>',\n"
        "  subdomains:'abcd',maxZoom:19\n"
        "}).addTo(map);\n"
        "var acSvg='<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"24\" height=\"24\"'"
        "  +' viewBox=\"-12 -12 24 24\" style=\"transform:rotate('+acHdg+'deg)\">'"
        "  +'<polygon points=\"0,-10 -6,7 6,7\" fill=\"#3b82f6\"/></svg>';\n"
        "L.marker([acLat,acLon],"
        "{icon:L.divIcon({html:acSvg,className:'',iconAnchor:[12,12]})}).addTo(map);\n"
        "var bounds=L.latLngBounds([[acLat,acLon]]);\n"
        "zones.forEach(function(z){\n"
        "  bounds.extend([z.lat,z.lon]);\n"
        "  var c=z.is_focused?'#3b82f6':(z.source==='VATSIM'?'#22c55e':'#94a3b8');\n"
        "  var dot='<div style=\"width:10px;height:10px;border-radius:50%;background:'"
        "    +c+';border:2px solid rgba(255,255,255,0.3)\"></div>';\n"
        "  var m=L.marker([z.lat,z.lon],{icon:L.divIcon({html:dot,className:'',"
        "iconAnchor:[5,5]})}).addTo(map);\n"
        "  var wx=z.wx;\n"
        "  var cbLabel='';\n"
        "  if(wx.cb_raw){\n"
        "    var cr=wx.cb_raw;\n"
        "    cbLabel='<br>⛈ '+cr.oktas+\"/8 \"+cr.base+\"'-\"+cr.top+\"'\";\n"
        "  }\n"
        "  var tip='<b>WX'+z.zone+'</b> '+z.icao+cbLabel;\n"
        "  var tipCls=z.is_focused?'zone-label zone-label-focused':'zone-label';\n"
        "  if(z.is_focused)L.circleMarker([z.lat,z.lon],"
        "{radius:12,color:'#3b82f6',weight:2,fill:false,opacity:0.85}).addTo(map);\n"
        "  m.bindTooltip(tip,{permanent:true,direction:'right',"
        "offset:[8,0],className:tipCls});\n"
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
        "  L.polyline([[acLat,acLon],[z.lat,z.lon]],{color:c,weight:1.5,"
        "dashArray:'5,5',opacity:0.5}).addTo(map);\n"
        "});\n"
        "if(zones.length>0)map.fitBounds(bounds.pad(0.4));\n"
        '</script>\n'
    )

    mode_color = '#f59e0b' if nav_mode == 'MANEUVERING' else '#22c55e'
    fw_color = '#f59e0b' if fw_mode != 'enabled' else '#22c55e'
    age_str = f'{int(age_s)}s ago'
    body = (
        '<div id="map"></div>\n' +
        script +
        '<div style="display:flex;gap:1.5em;margin-top:0.5em;'
        'font-size:0.85em;color:#94a3b8;flex-wrap:wrap;align-items:center">\n'
        f'<span>Data: <b style="color:#e2e8f0">{age_str}</b></span>\n'
        f'<span>Nav: <b style="color:{mode_color}">{nav_mode}</b></span>\n'
        f'<span>FW: <b style="color:{fw_color}">{fw_mode}</b></span>\n'
        '<span style="margin-left:auto">'
        '<a href="/weather/settings" class="btn btn-gray btn-sm">Weather settings</a>'
        '</span>\n'
        '</div>\n'
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
