"""Hourly, compressed router logs.

The router writes three logs, named <sim>-<router>-<kind>.txt (simulator and
router name from the config, made file name safe):

    <sim>-<router>-router.txt      what the router prints (its console)
    <sim>-<router>-traffic.txt     every message received and sent
    <sim>-<router>-simevents.txt   sim events

Each is rotated at the top of every UTC hour to
<sim>-<router>-<kind>-<YYYY-MM-DD_HH>.txt (the hour it covers) and gzipped in
a background thread (...txt.gz). Rotated hours are kept for [log] keep_hours
rotations (hours of router running; 0 = all) and, with [log] keep_gb, only as
many of the newest as fit in that size. Traffic logs shrink to about 17%, sim
event logs to about 2%; Windows 11 opens .gz files in Explorer.

Python's file handler flushes every record to the operating system, so the
data is on disk at once; but Windows doesn't update a file's size and time in
the directory (what Explorer and sync tools such as Syncthing go by) while it
is held open. reopen() closes and reopens the file, every minute, so they
see it too.

Logs under the names of older versions (frankenrouter-<router>.log,
<router>-traffic*.psxnet.log, <router>-simevents.log, rotated or not) are
"old-style": tidy_old_logs() gzips them at startup, and purge_logs() deletes
them once they're older than the oldest hour kept (keep_hours) or, with
keep_gb, before any hourly log.
"""
import gzip
import logging.handlers
import os
import re
import shutil
import tempfile
import threading
import time
import unittest

KINDS = ('router', 'traffic', 'simevents')
HOUR_SUFFIX = '%Y-%m-%d_%H'
_HOUR_RE = r'\d{4}-\d{2}-\d{2}_\d{2}(-\d+)?'


def safe_name(text):
    """Return text with characters that aren't safe in a file name replaced by _."""
    return re.sub(r'[^A-Za-z0-9_.-]+', '_', str(text)).strip('_.') or 'x'


def log_names(sim, router):
    """Return {kind: file name} of a router's three current logs."""
    return {kind: f"{safe_name(sim)}-{safe_name(router)}-{kind}.txt" for kind in KINDS}


def rotated_re(base):
    """Return a regex matching the rotated hours (plain or .gz) of current log base."""
    stem = re.escape(base[:-len('.txt')])
    return re.compile(rf'^{stem}-({_HOUR_RE})\.txt(\.gz)?$')


def old_style_re(router):
    """Return a regex matching a router's logs under the names of older versions."""
    r = re.escape(router)
    return re.compile(
        rf'^(frankenrouter-{r}\.log|{r}-simevents\.log|{r}-traffic(-\d+)?\.psxnet\.log)'
        rf'(\.(\d+|{_HOUR_RE}))?(\.gz)?$')


def gzip_file(path):
    """Compress path to path + '.gz' (keeping its modification time) and remove path.

    Writes to a temporary file first, so an interrupted run leaves the
    original untouched. Returns the new file name.
    """
    target = path + '.gz'
    stat = os.stat(path)
    tmp = target + '.tmp'
    with open(path, 'rb') as src, gzip.open(tmp, 'wb', compresslevel=6) as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)
    os.utime(tmp, (stat.st_atime, stat.st_mtime))
    os.replace(tmp, target)
    os.remove(path)
    return target


def _gzip_quietly(path, logger):
    try:
        gzip_file(path)
    except OSError as exc:
        if logger:
            logger.warning("Could not compress log %s: %s", path, exc)


class HourlyLogHandler(logging.handlers.TimedRotatingFileHandler):
    """A <...>.txt log rotated at the top of every UTC hour, gzipped, keep_hours kept."""

    def __init__(self, filename, keep_hours, logger=None):
        """Open filename (appending); rotated hours beyond keep_hours (0 = all) are deleted."""
        self.compress_logger = logger
        super().__init__(filename, when='H', interval=1, backupCount=keep_hours, utc=True,
                         encoding='utf-8')
        self.suffix = HOUR_SUFFIX

    def computeRollover(self, currentTime):
        """Roll over at the next top of the hour (UTC), not an hour from now."""
        return (int(currentTime) // 3600 + 1) * 3600

    def doRollover(self):
        """Move the file to <stem>-<hour>.txt, gzip that in the background, delete the oldest."""
        hour = time.strftime(HOUR_SUFFIX, time.gmtime(self.rolloverAt - self.interval))
        stem = self.baseFilename[:-len('.txt')]
        dest = f"{stem}-{hour}.txt"
        n = 1
        while os.path.exists(dest) or os.path.exists(dest + '.gz'):
            dest = f"{stem}-{hour}-{n}.txt"
            n += 1
        if self.stream:
            self.stream.close()
            self.stream = None
        if os.path.exists(self.baseFilename):
            os.replace(self.baseFilename, dest)
            threading.Thread(target=_gzip_quietly, args=(dest, self.compress_logger),
                             name="compress log", daemon=True).start()
        if self.backupCount > 0:
            for old in self.getFilesToDelete():
                try:
                    os.remove(old)
                except OSError:
                    pass
        self.stream = self._open()
        self.rolloverAt = self.computeRollover(time.time())

    def getFilesToDelete(self):
        """Return the rotated hours beyond keep_hours, oldest first."""
        directory, base = os.path.split(self.baseFilename)
        pattern = rotated_re(base)
        rotated = sorted(os.path.join(directory, name) for name in os.listdir(directory)
                         if pattern.match(name))
        # Plain and .gz of the same hour can both exist while compressing
        hours = sorted({re.sub(r'\.gz$', '', p) for p in rotated})
        drop = set(hours[:max(0, len(hours) - self.backupCount)])
        return [p for p in rotated if re.sub(r'\.gz$', '', p) in drop]

    def reopen(self):
        """Close and reopen the file, so Windows shows its current size and time."""
        with self.lock:
            if self.stream:
                self.stream.close()
                self.stream = self._open()


def _files(directory, pattern):
    """Return [(mtime, path, size)] of the files in directory matching pattern."""
    out = []
    for name in os.listdir(directory):
        if pattern(name):
            path = os.path.join(directory, name)
            try:
                st = os.stat(path)
            except OSError:
                continue
            out.append((st.st_mtime, path, st.st_size))
    return out


def purge_logs(directory, sim, router, keep_hours, max_bytes, logger=None):  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
    """Delete old-style logs past the time limit, and the oldest logs past the size limit.

    Time: once keep_hours hours of a router's hourly logs exist, its
    old-style logs older than the oldest hour kept go. Size (max_bytes, 0 =
    none): the oldest files go first, old-style before hourly, until the
    rotated and old-style logs fit. The three current files never count.
    """
    old = old_style_re(router)
    rotated = [rotated_re(base) for base in log_names(sim, router).values()]
    old_logs = _files(directory, old.match)
    hourly = _files(directory, lambda n: any(r.match(n) for r in rotated))
    doomed = set()
    hours = {re.sub(r'\.gz$', '', p) for _, p, _ in hourly}
    if keep_hours and old_logs and len(hours) >= keep_hours:
        cutoff = min(t for t, _, _ in hourly)
        doomed |= {p for t, p, _ in old_logs if t < cutoff}
    if max_bytes:
        total = sum(s for _, p, s in old_logs + hourly if p not in doomed)
        for _, path, size in sorted(old_logs) + sorted(hourly):
            if total <= max_bytes:
                break
            if path not in doomed:
                doomed.add(path)
                total -= size
    for path in sorted(doomed):
        try:
            os.remove(path)
        except OSError:
            continue
        if logger:
            logger.info("Deleted old log %s ([log] keep_hours/keep_gb)", path)


def tidy_old_logs(directory, sim, router, logger=None):
    """Gzip a router's logs left uncompressed: old-style names, or an interrupted rotation.

    Run in a thread: a big log takes a minute.
    """
    old = old_style_re(router)
    rotated = [rotated_re(base) for base in log_names(sim, router).values()]
    for name in sorted(os.listdir(directory)):
        if name.endswith('.gz') or not (old.match(name) or any(r.match(name) for r in rotated)):
            continue
        path = os.path.join(directory, name)
        try:
            size = os.path.getsize(path)
            target = gzip_file(path)
        except OSError as exc:
            if logger:
                logger.warning("Could not compress old log %s: %s", path, exc)
            continue
        if logger:
            logger.info("Compressed old log %s: %.1f MB -> %.1f MB", path,
                        size / 1e6, os.path.getsize(target) / 1e6)


class TestLogCompress(unittest.TestCase):
    """Tests for the hourly compressed logs."""

    def test_names(self):
        """Current names, safe characters, rotated and old-style patterns."""
        self.assertEqual(log_names('CRAVE', 'Kurt Slave Router'), {
            'router': 'CRAVE-Kurt_Slave_Router-router.txt',
            'traffic': 'CRAVE-Kurt_Slave_Router-traffic.txt',
            'simevents': 'CRAVE-Kurt_Slave_Router-simevents.txt'})
        r = rotated_re('S-r-traffic.txt')
        for name, ok in (('S-r-traffic-2026-11-02_14.txt.gz', True),
                         ('S-r-traffic-2026-11-02_14-1.txt', True),
                         ('S-r-traffic.txt', False), ('S-r-router-2026-11-02_14.txt.gz', False)):
            self.assertEqual(bool(r.match(name)), ok, name)
        old = old_style_re('r')
        for name, ok in (('frankenrouter-r.log', True), ('frankenrouter-r.log.3', True),
                         ('r-traffic-1793468206.psxnet.log', True),
                         ('r-traffic-1793468206.psxnet.log.gz', True),
                         ('r-traffic.psxnet.log.2026-11-02_14.gz', True),
                         ('r-simevents.log.2026-11-02_14-1', True),
                         ('S-r-traffic.txt', False), ('other-traffic.psxnet.log', False)):
            self.assertEqual(bool(old.match(name)), ok, name)

    def test_gzip_file(self):
        """The compressed file has the same content and time; the original is gone."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'S-r-traffic-2026-11-02_14.txt')
            with open(path, 'w', encoding='utf-8') as f:
                f.write("DATA FROM [upstream] Qs121=1;2;3\n" * 1000)
            os.utime(path, (1000000, 1000000))
            target = gzip_file(path)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(os.path.getmtime(target), 1000000)
            with gzip.open(target, 'rt', encoding='utf-8') as f:
                self.assertEqual(f.read(), "DATA FROM [upstream] Qs121=1;2;3\n" * 1000)

    def test_rollover_at_top_of_hour(self):
        """The next rollover is the next whole UTC hour."""
        with tempfile.TemporaryDirectory() as d:
            h = HourlyLogHandler(os.path.join(d, 'S-r-router.txt'), 48)
            self.assertEqual(h.computeRollover(1793620000), 1793620800)    # 13:46:40 -> 14:00
            self.assertEqual(h.computeRollover(1793620800), 1793624400)    # 14:00:00 -> 15:00
            h.close()

    @staticmethod
    def _wait_gz(path):
        for _ in range(100):
            if os.path.exists(path + '.gz') and not os.path.exists(path):
                return
            time.sleep(0.05)

    def test_rotation_names_compression_and_keep(self):
        """Rotated hours are named for the hour, gzipped, and only keep_hours are kept."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'S-r-simevents.txt')
            h = HourlyLogHandler(path, 2)
            logger = logging.getLogger('logcompress-test-rotation')
            logger.propagate = False
            logger.addHandler(h)
            for hour in range(4):       # 14:00, 15:00, 16:00, 17:00 UTC on 2026-11-02
                h.rolloverAt = 4e9      # no rollover while logging
                logger.warning("line in hour %d", hour)
                h.rolloverAt = 1793628000 + hour * 3600 + 3600
                h.doRollover()
                started = time.gmtime(1793628000 + hour * 3600)
                self._wait_gz(os.path.join(
                    d, f"S-r-simevents-{time.strftime(HOUR_SUFFIX, started)}.txt"))
            logger.warning("current")
            h.close()
            logger.removeHandler(h)
            self.assertEqual(sorted(os.listdir(d)), [
                'S-r-simevents-2026-11-02_16.txt.gz', 'S-r-simevents-2026-11-02_17.txt.gz',
                'S-r-simevents.txt'])
            with gzip.open(os.path.join(d, 'S-r-simevents-2026-11-02_17.txt.gz'), 'rt') as f:
                self.assertIn('line in hour 3', f.read())
            with open(path, encoding='utf-8') as f:
                self.assertEqual(f.read().strip(), 'current')

    def test_same_hour_twice(self):
        """A second rotation of the same hour gets its own name."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'S-r-router.txt')
            h = HourlyLogHandler(path, 48)
            for text in ('first', 'second'):
                h.stream.write(text + '\n')
                h.rolloverAt = 1793628000 + 3600
                h.doRollover()
            h.close()
            self._wait_gz(os.path.join(d, 'S-r-router-2026-11-02_14-1.txt'))
            self.assertEqual(sorted(os.listdir(d)), [
                'S-r-router-2026-11-02_14-1.txt.gz', 'S-r-router-2026-11-02_14.txt.gz',
                'S-r-router.txt'])

    def test_reopen_keeps_appending(self):
        """reopen() loses and duplicates nothing."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'S-r-router.txt')
            h = HourlyLogHandler(path, 48)
            logger = logging.getLogger('logcompress-test-reopen')
            logger.propagate = False
            logger.addHandler(h)
            for i in range(5):
                logger.warning("line %d", i)
                h.reopen()
            h.close()
            logger.removeHandler(h)
            with open(path, encoding='utf-8') as f:
                self.assertEqual(f.read().splitlines(), [f"line {i}" for i in range(5)])

    @staticmethod
    def _make(d, files):
        """Create {name: (mtime, size)} files in d."""
        for name, (mtime, size) in files.items():
            path = os.path.join(d, name)
            with open(path, 'wb') as f:
                f.write(b'x' * size)
            os.utime(path, (mtime, mtime))

    def test_purge_old_style_by_time(self):
        """Old-style logs go once keep_hours hours exist and they're older than all of them."""
        with tempfile.TemporaryDirectory() as d:
            self._make(d, {'frankenrouter-m.log.gz': (1000, 10),
                           'm-traffic-1784817451.psxnet.log.gz': (5000, 10),
                           'S-m-traffic-2026-11-02_13.txt.gz': (4000, 10),
                           'S-m-traffic.txt': (100, 10), 'notes.txt': (1, 10)})
            purge_logs(d, 'S', 'm', 2, 0)       # only 1 hour so far: keep everything
            self.assertEqual(len(os.listdir(d)), 5)
            self._make(d, {'S-m-router-2026-11-02_14.txt.gz': (4500, 10)})
            purge_logs(d, 'S', 'm', 2, 0)       # 2 hours: old-style older than 4000 goes
            self.assertEqual(sorted(os.listdir(d)), [
                'S-m-router-2026-11-02_14.txt.gz', 'S-m-traffic-2026-11-02_13.txt.gz',
                'S-m-traffic.txt', 'm-traffic-1784817451.psxnet.log.gz', 'notes.txt'])
            purge_logs(d, 'S', 'm', 0, 0)       # 0 and 0: nothing ever goes
            self.assertEqual(len(os.listdir(d)), 5)

    def test_purge_by_size(self):
        """Over the size limit, old-style go first, then the oldest hours; current never."""
        with tempfile.TemporaryDirectory() as d:
            self._make(d, {'m-simevents.log.1.gz': (9000, 1000),
                           'S-m-traffic-2026-11-02_12.txt.gz': (1000, 1000),
                           'S-m-simevents-2026-11-02_12.txt.gz': (1100, 1000),
                           'S-m-traffic-2026-11-02_13.txt.gz': (2000, 1000),
                           'S-m-traffic.txt': (100, 5000),
                           'S-other-traffic-2026-11-02_10.txt.gz': (1, 1000)})
            purge_logs(d, 'S', 'm', 0, 1500)
            self.assertEqual(sorted(os.listdir(d)), [
                'S-m-traffic-2026-11-02_13.txt.gz', 'S-m-traffic.txt',
                'S-other-traffic-2026-11-02_10.txt.gz'])

    def test_tidy_old_logs(self):
        """Old-style and uncompressed rotated logs are gzipped; nothing else, nothing deleted."""
        with tempfile.TemporaryDirectory() as d:
            names = ('m-traffic-100.psxnet.log', 'frankenrouter-m.log', 'm-traffic.psxnet.log',
                     'm-simevents.log.2', 'm-traffic.psxnet.log.2026-11-02_13.gz',
                     'S-m-router-2026-11-02_14.txt', 'S-m-router.txt', 'S-m-traffic.txt',
                     'other-traffic-100.psxnet.log', '#m-traffic-100.psxnet.log#')
            for name in names:
                with open(os.path.join(d, name), 'w', encoding='utf-8') as f:
                    f.write('x\n')
            tidy_old_logs(d, 'S', 'm')
            self.assertEqual(sorted(os.listdir(d)), sorted([
                'm-traffic-100.psxnet.log.gz', 'frankenrouter-m.log.gz', 'm-traffic.psxnet.log.gz',
                'm-simevents.log.2.gz', 'm-traffic.psxnet.log.2026-11-02_13.gz',
                'S-m-router-2026-11-02_14.txt.gz', 'S-m-router.txt', 'S-m-traffic.txt',
                'other-traffic-100.psxnet.log', '#m-traffic-100.psxnet.log#']))
