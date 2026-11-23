"""frankenstutter's read-only web page and JSON API (see frankenstutter.py).

state is frankenstutter's State: everything is read under state.lock, nothing can be changed.
"""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>frankenstutter</title>
<style>body{font:14px system-ui,sans-serif;margin:16px;background:#111;color:#ddd}
h1{font-size:18px}h2{font-size:15px;margin:18px 0 6px}table{border-collapse:collapse}
td,th{padding:2px 8px;border-bottom:1px solid #333;text-align:right}th{color:#999;font-weight:normal}
td:first-child,th:first-child{text-align:left}.big{font-size:22px}.s{color:#e6c35c}.m{color:#f08a3c}
.M{color:#ff5050}#cores span{display:inline-block;width:14px;margin-right:2px;background:#4a8;
vertical-align:bottom}#cores{height:60px;border-bottom:1px solid #444}</style></head><body>
<h1>frankenstutter <span id="ver"></span></h1><div id="st"></div>
<h2>CPU per core</h2><div id="cores"></div><h2>Busiest processes (last second)</h2>
<table id="pr"></table><h2>Last stutters</h2><table id="su"></table>
<p style="color:#777">JSON: /api/status /api/seconds?n=300 /api/stutters?n=100 /api/processes
/api/session /api/markers</p>
<script>
const f=(v,n=1)=>v==null?'-':(+v).toFixed(n);
async function tick(){try{
const s=await (await fetch('api/status')).json(),r=s.row||{},c=s.last_minute||{};
document.getElementById('ver').textContent=s.version;
document.getElementById('st').innerHTML=`<div class=big>${f(r.fps)} fps &nbsp; p99 ${f(r.disp_p99)} ms
&nbsp; <span class=s>${c.small||0} small</span> <span class=m>${c.medium||0} medium</span>
<span class=M>${c.major||0} major</span> in the last minute</div>
<div>${r.airport||'enroute'} ${f(r.alt_ft,0)} ft ${r.camera||''} | CPU ${f(r.cpu_total)} % (max core
${f(r.cpu_max_core)} %, DPC ${f(r.cpu_dpc_pct)} %, ${f(r.cpu_clock_mhz,0)} MHz) | MSFS ${f(r.msfs_cpu_cores,2)}
cores, ${f(r.msfs_private_gb)} GB, reads ${f(r.msfs_read_mbs)} MB/s | RAM free ${f(r.ram_avail_gb)} GB |
GPU ${f(r.gpu_util,0)} %, VRAM ${f(r.vram_used_gb)}/${f(r.vram_total_gb)} GB, ${f(r.gpu_temp,0)} C |
net ${f(r.net_rx_mbs,2)} MB/s</div><div style="color:#888">PresentMon: ${s.presentmon} | SimConnect:
${s.simconnect?'connected':'not connected'} | logging to ${s.session_dir}</div>`;
document.getElementById('cores').innerHTML=(s.per_core||[]).map(v=>`<span style="height:${0.6*v}px"
title="${f(v,0)} %"></span>`).join('');
const p=await (await fetch('api/processes?n=15')).json();
document.getElementById('pr').innerHTML='<tr><th>process</th><th>CPU cores</th><th>memory GB</th>'+
'<th>read MB/s</th><th>write MB/s</th><th>threads</th></tr>'+p.map(x=>`<tr><td>${x.name}</td>
<td>${f(x.cpu_cores,2)}</td><td>${f(x.private_gb||x.rss_gb,2)}</td><td>${f(x.read_mbs)}</td>
<td>${f(x.write_mbs)}</td><td>${x.threads??'-'}</td></tr>`).join('');
const u=await (await fetch('api/stutters?n=15')).json();
document.getElementById('su').innerHTML='<tr><th>UTC</th><th>ms</th><th>kind</th><th>guess</th>'+
'<th>where</th></tr>'+u.reverse().map(x=>`<tr><td>${x.utc.slice(11,23)}</td><td>${f(x.shown_ms)}</td>
<td class=${{small:'s',medium:'m',major:'M'}[x.severity]}>${x.severity}</td><td>${(x.guess||'').split(' (')[0]}</td>
<td>${(x.row||{}).airport||'enroute'}</td></tr>`).join('');
}catch(e){document.getElementById('st').textContent='agent not answering: '+e}}
tick();setInterval(tick,2000);
</script></body></html>"""


def web_thread(state, bind: str, port: int, session: dict) -> None:
    """Serve the page and the read-only JSON API (nothing can be changed through it)."""
    class Handler(BaseHTTPRequestHandler):
        """GET only."""

        def log_message(self, *_):   # pylint: disable=arguments-differ
            """Log nothing (the console is for the agent's own lines)."""
            return

        def send(self, body, ctype="application/json"):
            """Send body (a text, or anything JSON can take) with status 200."""
            text = body if isinstance(body, str) else json.dumps(body, default=str)
            data = text.encode()
            self.send_response(200)
            self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):   # pylint: disable=invalid-name,too-many-return-statements
            """Answer the page or one of the API's JSON documents."""
            url = urlparse(self.path)
            q = parse_qs(url.query)
            n = int((q.get("n") or ["100"])[0])
            with state.lock:
                if url.path in ("/", "/index.html"):
                    return self.send(PAGE, "text/html")
                if url.path == "/api/status":
                    return self.send({**state.status, "per_core": state.per_core,
                                      "row": state.rows[-1] if state.rows else {}})
                if url.path == "/api/seconds":
                    return self.send(list(state.rows)[-n:])
                if url.path == "/api/stutters":
                    return self.send(list(state.stutters)[-n:])
                if url.path == "/api/processes":
                    return self.send(state.processes[:n])
                if url.path == "/api/session":
                    return self.send(session)
                if url.path == "/api/markers":
                    return self.send(state.status.get("markers", []))
            self.send_response(404)
            self.end_headers()
            return None

    try:
        server = ThreadingHTTPServer((bind, port), Handler)
    except OSError as exc:
        print(f"  web page: cannot listen on {bind}:{port}: {exc}")
        return
    server.daemon_threads = True
    server.serve_forever()
