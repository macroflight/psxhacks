"""The router's log download and "Report a problem" pages.

/logs lists the router's log files (the three current logs, their rotated
hours and logs under the names of older versions, see logcompress.py) with the time
span each covers, and downloads one file, a ZIP of the chosen files, or a
ZIP of every file covering a chosen time interval. /report takes a
description of a problem and a time interval, and downloads a ZIP of the
description and the logs covering that interval, for the user to keep and
report in the PSCC Discord or the Aerowinx forum.

Only files in the log directory whose names are this router's logs are ever
served. ZIPs are built in a thread into a temporary file and streamed, so a
large one doesn't hold up routing.
"""
import datetime
import gzip
import html
import os
import re
import tempfile
import time
import unittest
import zipfile

from . import logcompress

# A log line starts "2026-11-02 13:58:31,977" (UTC)
_STAMP = re.compile(rb'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')
_KINDS = (('router', 'Router log'), ('traffic', 'Traffic log'), ('simevents', 'Sim events'))
_span_cache = {}


def log_kind(name, sim, router):
    """Return 'router', 'traffic' or 'simevents' if name is one of router's logs, else None.

    Current and rotated logs, and logs under the names of older versions.
    """
    for kind, base in logcompress.log_names(sim, router).items():
        if name == base or logcompress.rotated_re(base).match(name):
            return kind
    if logcompress.old_style_re(router).match(name):
        if name.startswith('frankenrouter-'):
            return 'router'
        return 'simevents' if '-simevents.' in name else 'traffic'
    return None


def _stamp(line):
    m = _STAMP.match(line)
    if not m:
        return None
    try:
        return datetime.datetime.strptime(m[1].decode(), '%Y-%m-%d %H:%M:%S').replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except ValueError:
        return None


def _first_stamp(path):
    opener = gzip.open if path.endswith('.gz') else open
    try:
        with opener(path, 'rb') as f:
            for _ in range(200):
                line = f.readline()
                if not line:
                    break
                t = _stamp(line)
                if t is not None:
                    return t
    except (OSError, EOFError):
        pass
    return None


def _last_stamp(path):
    """Return the last time stamp of a plain log (read from its end), or None."""
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 65536))
            lines = f.read().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        t = _stamp(line)
        if t is not None:
            return t
    return None


def time_span(path):
    """Return (first, last) UTC epoch seconds a log covers (None if unknown).

    The end of a gzipped log is the time it was last written (its
    modification time, kept when it was rotated and compressed). Cached by
    path, size and modification time.
    """
    try:
        st = os.stat(path)
    except OSError:
        return None, None
    key = (path, st.st_size, st.st_mtime)
    if key not in _span_cache:
        first = _first_stamp(path)
        last = st.st_mtime if path.endswith('.gz') else (_last_stamp(path) or st.st_mtime)
        _span_cache[key] = (first, last)
    return _span_cache[key]


def list_logs(directory, sim, router):
    """Return this router's log files, newest first (dicts: name, path, kind, size, first, last)."""
    out = []
    for name in os.listdir(directory):
        kind = log_kind(name, sim, router)
        path = os.path.join(directory, name)
        if kind is None or not os.path.isfile(path):
            continue
        first, last = time_span(path)
        out.append({'name': name, 'path': path, 'kind': kind, 'size': os.path.getsize(path),
                    'first': first, 'last': last,
                    'current': name in logcompress.log_names(sim, router).values()})
    return sorted(out, key=lambda e: (-(e['last'] or 0), e['kind']))


def overlapping(logs, start, end):
    """Return the logs that cover any part of [start, end] (epoch seconds)."""
    return [e for e in logs
            if (e['first'] is None or e['first'] <= end) and
            (e['last'] is None or e['last'] >= start)]


def build_zip(zip_path, logs, extra=None):
    """Write a ZIP of logs (and extra {name: text} files) to zip_path.

    Gzipped logs are stored as they are; plain (current) logs are compressed.
    """
    with zipfile.ZipFile(zip_path, 'w') as zf:
        for name, text in (extra or {}).items():
            zf.writestr(name, text, compress_type=zipfile.ZIP_DEFLATED)
        for e in logs:
            gz = e['name'].endswith('.gz')
            try:
                zf.write(e['path'], e['name'],
                         compress_type=zipfile.ZIP_STORED if gz else zipfile.ZIP_DEFLATED,
                         compresslevel=None if gz else 6)
            except OSError:
                pass        # rotated or deleted meanwhile


def fmt_time(t):
    """Return epoch seconds as 'YYYY-MM-DD HH:MM' UTC, or '?'."""
    return '?' if t is None else time.strftime('%Y-%m-%d %H:%M', time.gmtime(t))


def fmt_size(n):
    """Return a size in bytes as e.g. '12.3 MB'."""
    return f"{n / 1e6:.1f} MB" if n >= 100e3 else f"{n / 1e3:.0f} kB"


def parse_utc(text):
    """Parse a 'YYYY-MM-DDTHH:MM' (datetime-local input, UTC) into epoch seconds, or None."""
    try:
        return datetime.datetime.strptime(text.strip(), '%Y-%m-%dT%H:%M').replace(
            tzinfo=datetime.timezone.utc).timestamp()
    except (ValueError, AttributeError):
        return None


def input_time(t):
    """Return epoch seconds as a datetime-local input value (UTC)."""
    return time.strftime('%Y-%m-%dT%H:%M', time.gmtime(t))


def logs_table(logs):
    """Return the HTML rows of the log file table on /logs."""
    names = dict(_KINDS)
    rows = []
    for e in logs:
        name = html.escape(e['name'])
        current = ' <span class="note">(current)</span>' if e['current'] else ''
        rows.append(
            f'<tr><td><input type="checkbox" name="file" value="{name}"></td>'
            f'<td>{names[e["kind"]]}</td>'
            f'<td><a href="/logs/file/{name}">{name}</a>{current}</td>'
            f'<td>{fmt_time(e["first"])} &ndash; {fmt_time(e["last"])}</td>'
            f'<td>{fmt_size(e["size"])}</td></tr>')
    return '\n'.join(rows) or '<tr><td colspan="5">No log files</td></tr>'


class TestLogPages(unittest.TestCase):
    """Tests for the log page helpers."""

    def test_log_kind(self):
        """Recognise the router's logs, current, rotated and old-style; nothing else."""
        for name, kind in (('S-m-router.txt', 'router'),
                           ('S-m-router-2026-11-02_14.txt.gz', 'router'),
                           ('S-m-traffic.txt', 'traffic'),
                           ('S-m-traffic-2026-11-02_14-1.txt.gz', 'traffic'),
                           ('S-m-simevents.txt', 'simevents'),
                           ('frankenrouter-m.log', 'router'),
                           ('frankenrouter-m.log.2026-11-02_14.gz', 'router'),
                           ('m-traffic-1793468206.psxnet.log.gz', 'traffic'),
                           ('m-traffic.psxnet.log.1.gz', 'traffic'),
                           ('m-simevents.log', 'simevents'),
                           ('S-other-traffic.txt', None), ('r.toml', None),
                           ('S-m-router.txt.tmp', None), ('../etc/passwd', None)):
            self.assertEqual(log_kind(name, 'S', 'm'), kind, name)

    def test_spans_and_overlap(self):
        """First and last time stamps; files overlapping an interval; the ZIP."""
        with tempfile.TemporaryDirectory() as d:
            plain = os.path.join(d, 'S-m-traffic.txt')
            with open(plain, 'w', encoding='utf-8') as f:
                f.write("2026-11-02 14:00:01,000: DATA FROM [upstream] Qs121=1\n"
                        "2026-11-02 14:20:30,000: DATA FROM [upstream] Qs121=2\n")
            old = os.path.join(d, 'S-m-traffic-2026-11-02_13.txt')
            with open(old, 'w', encoding='utf-8') as f:
                f.write("2026-11-02 13:00:00,500: hello\n")
            os.utime(old, (0, 1793628000))           # 2026-11-02 14:00 UTC
            logcompress.gzip_file(old)
            with open(os.path.join(d, 'r.toml'), 'w', encoding='utf-8') as f:
                f.write('x')
            logs = list_logs(d, 'S', 'm')
            self.assertEqual([e['name'] for e in logs],
                             ['S-m-traffic.txt', 'S-m-traffic-2026-11-02_13.txt.gz'])
            self.assertEqual(fmt_time(logs[0]['first']) + ' ' + fmt_time(logs[0]['last']),
                             '2026-11-02 14:00 2026-11-02 14:20')
            self.assertEqual(fmt_time(logs[1]['first']) + ' ' + fmt_time(logs[1]['last']),
                             '2026-11-02 13:00 2026-11-02 14:00')
            at = parse_utc('2026-11-02T14:10')
            self.assertEqual([e['name'] for e in overlapping(logs, at, at + 60)],
                             ['S-m-traffic.txt'])
            at = parse_utc('2026-11-02T13:30')
            self.assertEqual(len(overlapping(logs, at, at + 3600)), 2)
            zpath = os.path.join(d, 'out.zip')
            build_zip(zpath, logs, {'problem.txt': 'it broke'})
            with zipfile.ZipFile(zpath) as zf:
                self.assertEqual(sorted(zf.namelist()), [
                    'S-m-traffic-2026-11-02_13.txt.gz', 'S-m-traffic.txt', 'problem.txt'])
                self.assertEqual(zf.getinfo('S-m-traffic-2026-11-02_13.txt.gz').compress_type,
                                 zipfile.ZIP_STORED)
                self.assertEqual(zf.read('problem.txt'), b'it broke')


# --- The pages ------------------------------------------------------------------------------

_PAGE = (
    '<!DOCTYPE html>\n<html>\n<head>\n<title>{title}</title>\n'
    '<meta name="color-scheme" content="{scheme}" />\n{css}\n'
    '<style>body {{ max-width: 64em; }} td {{ padding: 0.3em 0.5em 0.3em 0; }}'
    ' td:first-child {{ width: auto; }} input[type=datetime-local] {{ padding: 0.5em;'
    ' border-radius: 0.45em; border: 1px solid #374151; background: #111827;'
    ' color: #f9fafb; font-family: inherit; }}</style>\n</head>\n<body>\n'
    '<div class="page-title"><a href="/"><img src="/static/frankentech.png" alt="Home"></a>'
    '<h1>{title}</h1><div style="margin-left:auto">'
    '<a href="/" class="btn btn-gray btn-sm">Back</a></div></div>\n{body}\n</body>\n</html>\n'
)

_REPORT_ADVICE = (
    "Report the problem in the PSCC Discord or the Aerowinx forum, and keep this file: "
    "Macroflight may ask for it.")


def _interval_inputs(start, end):
    return (f'<div class="grid2"><div><label>From (UTC)</label>'
            f'<input type="datetime-local" name="from" value="{input_time(start)}"></div>'
            f'<div><label>To (UTC)</label>'
            f'<input type="datetime-local" name="to" value="{input_time(end)}"></div></div>')


def _safe(text):
    return re.sub(r'[^A-Za-z0-9_-]+', '_', text).strip('_') or 'router'


def add_routes(routes, router, common_css):  # pylint: disable=too-many-statements,too-many-locals
    """Add the /logs and /report pages to the router's web API routes."""
    import asyncio  # pylint: disable=import-outside-toplevel
    from aiohttp import web  # pylint: disable=import-outside-toplevel,import-error

    def page(title, body):
        css = common_css.format()
        return web.Response(
            text=_PAGE.format(title=title, scheme=router.config.listen.rest_api_color_scheme,
                              css=css, body=body),
            content_type='text/html')

    def logs():
        return list_logs(router.config.log.directory, router.config.identity.simulator,
                         router.config.identity.router)

    async def send_file(request, path, filename):
        resp = web.StreamResponse(headers={
            'Content-Type': 'application/octet-stream',
            'Content-Disposition': f'attachment; filename="{filename}"'})
        await resp.prepare(request)
        with open(path, 'rb') as f:
            while True:
                chunk = await asyncio.to_thread(f.read, 1 << 20)
                if not chunk:
                    break
                await resp.write(chunk)
        await resp.write_eof()
        return resp

    async def send_zip(request, chosen, filename, extra=None):
        fd, tmp = tempfile.mkstemp(suffix='.zip', prefix='frankenrouter-')
        os.close(fd)
        try:
            await asyncio.to_thread(build_zip, tmp, chosen, extra)
            router.logger.info("Web UI: sending %s (%d log files)", filename, len(chosen))
            return await send_file(request, tmp, filename)
        finally:
            os.remove(tmp)

    def zip_name(prefix):
        return (f"{prefix}-{_safe(router.config.identity.simulator)}-"
                f"{time.strftime('%Y%m%d-%H%MZ', time.gmtime())}.zip")

    @routes.get('/logs')
    async def handle_logs_get(_):
        now = time.time()
        body = (
            '<p class="note">All times UTC. Each log rotates every hour; the hour is in its name.'
            ' Click a file to download it.</p>\n'
            '<form method="post" action="/logs/download">\n'
            '<table><tr><td></td><td class="note">Log</td><td class="note">File</td>'
            '<td class="note">Covers</td><td class="note">Size</td></tr>\n'
            f'{logs_table(logs())}\n</table>\n'
            '<button class="btn btn-blue">Download the selected files (ZIP if more than one)'
            '</button>\n</form>\n<hr>\n'
            '<h2>Download all logs covering a time interval (ZIP)</h2>\n'
            '<form method="post" action="/logs/interval">\n'
            f'{_interval_inputs(now - 3600, now)}'
            '<button class="btn btn-blue">Download the interval</button>\n'
            '<div class="btn-row">'
            '<button class="btn btn-gray" name="hours" value="1">Last hour</button>'
            '<button class="btn btn-gray" name="hours" value="3">Last 3 hours</button>'
            '<button class="btn btn-gray" name="hours" value="24">Last 24 hours</button>'
            '</div>\n</form>\n')
        return page("Download logs", body)

    @routes.get('/logs/file/{name}')
    async def handle_log_file(request):
        name = request.match_info['name']
        for e in logs():
            if e['name'] == name:
                return await send_file(request, e['path'], name)
        raise web.HTTPNotFound(text="No such log file")

    @routes.post('/logs/download')
    async def handle_logs_download(request):
        post = await request.post()
        wanted = set(post.getall('file', []))
        chosen = [e for e in logs() if e['name'] in wanted]
        if not chosen:
            raise web.HTTPFound('/logs')
        if len(chosen) == 1:
            return await send_file(request, chosen[0]['path'], chosen[0]['name'])
        return await send_zip(request, chosen, zip_name('logs'))

    @routes.post('/logs/interval')
    async def handle_logs_interval(request):
        post = await request.post()
        if post.get('hours'):
            end = time.time()
            start = end - float(post['hours']) * 3600
        else:
            start, end = parse_utc(post.get('from', '')), parse_utc(post.get('to', ''))
            if start is None or end is None or end < start:
                return page("Download logs", '<div class="card warn">Choose a valid interval '
                            '(from before to).</div><a href="/logs" class="btn btn-gray">Back</a>')
        chosen = overlapping(logs(), start, end)
        if not chosen:
            return page("Download logs", '<div class="card warn">No logs cover that interval.'
                        '</div><a href="/logs" class="btn btn-gray">Back</a>')
        return await send_zip(request, chosen, zip_name('logs'))

    @routes.get('/report')
    async def handle_report_get(_):
        now = time.time()
        body = (
            '<p>Describe what you saw in your sim, and choose the time it happened. You get a ZIP'
            ' file with your description and this router\'s logs for that time.</p>\n'
            f'<p class="note">{_REPORT_ADVICE}</p>\n'
            '<form method="post" action="/report">\n'
            '<label>What happened? (required)</label>'
            '<textarea name="description" rows="8" required></textarea>\n'
            '<label>Your name or role (optional)</label><input type="text" name="reporter">\n'
            f'{_interval_inputs(now - 3600, now)}'
            '<button class="btn btn-amber">Create the problem report (ZIP)</button>\n</form>\n')
        return page("Report a problem", body)

    @routes.post('/report')
    async def handle_report_post(request):
        post = await request.post()
        description = post.get('description', '').strip()
        start, end = parse_utc(post.get('from', '')), parse_utc(post.get('to', ''))
        if not description or start is None or end is None or end < start:
            return page("Report a problem", '<div class="card warn">Describe the problem and'
                        ' choose a valid interval (from before to).</div>'
                        '<a href="/report" class="btn btn-gray">Back</a>')
        chosen = overlapping(logs(), start, end)
        ident = router.config.identity
        text = (
            f"Problem report from sim {ident.simulator}, router {ident.router} "
            f"({ident.type}), frankenrouter {router.version}\n"
            f"Reported by: {post.get('reporter', '').strip() or '-'}\n"
            f"Created: {fmt_time(time.time())} UTC\n"
            f"Time of the problem: {fmt_time(start)} - {fmt_time(end)} UTC\n"
            f"Log files: {', '.join(e['name'] for e in chosen) or 'none cover this time'}\n\n"
            f"What happened:\n{description}\n\n{_REPORT_ADVICE}\n")
        router.logger.info("Web UI: problem report created for %s - %s UTC", fmt_time(start),
                           fmt_time(end))
        return await send_zip(request, chosen, zip_name('problem'),
                              {'problem.txt': text.replace('\n', '\r\n')})
