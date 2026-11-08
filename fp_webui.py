"""frankenpilot's own web UI: the router page's controls, plus its log files.

Mirrors the frankenrouter /frankenpilot page (state, Auto/On/Off, a toggle per
action, recent actions) so frankenpilot can be watched and set without a
router, and adds what only frankenpilot itself has: its log files (one per
run, see docs/frankenpilot.md), to read in the browser or download. Changes
are applied locally through FrankenPilot.on_command(), the same handler the
router's addon=FRANKENPILOT:COMMAND messages use.
"""
import html
import json
import os

from aiohttp import web

_CSS = """<style>
body { font-family: system-ui, sans-serif; background: #1d2330; color: #e4e8f0; margin: 1.5em; }
h1 { margin: 0 0 0.4em 0; } h2 { margin: 1.2em 0 0.4em 0; font-size: 1.1em; }
a { color: #8fb8ff; }
table { border-collapse: collapse; } td { padding: 0.25em 0.8em 0.25em 0; }
button { border: 0; border-radius: 0.4em; padding: 0.3em 0.9em; font-size: 0.95em;
         cursor: pointer; margin-right: 0.3em; }
.on { background: #2b62e3; color: #fff; } .off { background: #6b7385; color: #fff; }
.note { color: #9aa3b5; font-size: 0.9em; }
pre { background: #11151d; padding: 0.8em; overflow-x: auto; font-size: 0.85em; }
</style>"""


def _page(title, body, refresh=None):
    """Return a whole HTML page."""
    meta = f'<meta http-equiv="refresh" content="{refresh}">' if refresh else ''
    return (f'<!DOCTYPE html>\n<html><head><meta charset="utf-8">'
            f'<meta name="color-scheme" content="dark">{meta}<title>{html.escape(title)}</title>'
            f'{_CSS}</head><body>{body}</body></html>\n')


def _log_files(log_dir):
    """Return the log file names, newest first."""
    try:
        return sorted((f for f in os.listdir(log_dir)
                       if f.startswith("frankenpilot_") and f.endswith(".log")), reverse=True)
    except OSError:
        return []


def build_app(pilot):
    """Return the aiohttp application for a FrankenPilot instance."""
    routes = web.RouteTableDef()
    log_dir = pilot.args.log_dir

    @routes.get('/')
    async def index(_):
        st = pilot.state_dict()
        seat = f" in the {html.escape(str(st['seat']))} seat" if st['active'] else ''
        radios = ''.join(
            f'<button name="enabled" value="{v}" class="{"on" if v == st["enabled"] else "off"}">'
            f'{label}</button>' for v, label in (('auto', 'Auto'), ('on', 'On'), ('off', 'Off')))
        rows = ''.join(
            f'<tr><td>{html.escape(info["text"])}</td><td><button name="toggle" '
            f'value="{html.escape(name)}" class="{"on" if info["on"] else "off"}">'
            f'{"On" if info["on"] else "Off"}</button></td></tr>\n'
            for name, info in st['actions'].items())
        recent = ''.join(f'<li>{html.escape(x)}</li>' for x in st['recent']) or '<li>none yet</li>'
        logs = ''.join(
            f'<li><a href="/logs/{html.escape(f)}">{html.escape(f)}</a>'
            f'{" (current)" if f == os.path.basename(pilot.args.log_path or "") else ""}</li>'
            for f in _log_files(log_dir)) or '<li>no log files</li>'
        body = (
            '<h1>frankenpilot</h1>'
            f'<p><b>{"ON" if st["active"] else "off"}</b>{seat} &mdash; mode '
            f'{html.escape(str(st["mode"]))}, phase {html.escape(str(st["phase"]))}, '
            f'version {html.escape(str(st["version"]))}; {html.escape(st["vatsim"])}</p>'
            '<form method="post" action="/api/command">'
            f'<p>frankenpilot: {radios} <span class="note">(auto: on with 0-1 slave sims, '
            'always in local mode)</span></p>'
            '<h2>Actions</h2><table>' + rows + '</table></form>'
            '<h2>Recent actions</h2><ul>' + recent + '</ul>'
            '<h2>Log</h2><p><a href="/log">Current log (last 400 lines)</a></p>'
            f'<p class="note">Log files in {html.escape(log_dir)} (one per run, the newest '
            'kept), click to download:</p><ul>' + logs + '</ul>')
        return web.Response(text=_page("frankenpilot", body, refresh=15),
                            content_type='text/html')

    @routes.post('/api/command')
    async def command(request):
        data = await request.post()
        cmd = {}
        if data.get('enabled') in ('auto', 'on', 'off'):
            cmd['enabled'] = data['enabled']
        toggle = data.get('toggle')
        if toggle in pilot.actions:
            cmd['actions'] = {toggle: not pilot.actions[toggle]}
        if cmd:
            pilot.logger.info("Web UI: command %s", cmd)
            pilot.on_command(json.dumps(cmd))
        raise web.HTTPFound('/')

    @routes.get('/log')
    async def current_log(_):
        path = pilot.args.log_path
        lines = []
        if path:
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    lines = fh.readlines()[-400:]
            except OSError:
                pass
        body = ('<p><a href="/">Back</a> &middot; '
                f'<a href="/logs/{html.escape(os.path.basename(path or ""))}">Download</a></p>'
                f'<pre>{html.escape("".join(lines)) or "empty"}</pre>')
        return web.Response(text=_page("frankenpilot log", body, refresh=10),
                            content_type='text/html')

    @routes.get('/logs/{name}')
    async def download(request):
        name = request.match_info['name']
        if name not in _log_files(log_dir):     # only our own files, no paths
            raise web.HTTPNotFound()
        return web.FileResponse(os.path.join(log_dir, name), headers={
            'Content-Type': 'text/plain; charset=utf-8',
            'Content-Disposition': f'attachment; filename="{name}"'})

    app = web.Application()
    app.add_routes(routes)
    return app


async def serve(pilot, port):
    """Run the web UI on all interfaces, port PORT (until cancelled)."""
    runner = web.AppRunner(build_app(pilot), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    try:
        await site.start()
    except OSError as exc:
        pilot.logger.warning("Web UI: can't listen on port %d: %s", port, exc)
        await runner.cleanup()
        return
    pilot.logger.info("Web UI on http://localhost:%d/", port)
