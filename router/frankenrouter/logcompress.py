"""Compress old router logs with gzip ([log] compress_old).

A traffic log is one file per router start by default
(<router>-traffic-<epoch>.psxnet.log); all but the current run's are
compressed at startup, in a background thread. With rotation
(traffic_max_size, output_max_size) each backup is compressed as it's
rotated out (<name>.1.gz, ...), through RotatingFileHandler's namer and
rotator hooks. Traffic logs shrink to about 17% (WorldFlight 2026 master
router: 5 GB per 37 hours of flying), sim event logs to about 2%. Windows 11
opens .gz files in Explorer; on Linux, zcat/zgrep.
"""
import gzip
import os
import re
import shutil
import tempfile
import unittest


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


def old_traffic_logs(directory, router, current):
    """Return the per-start traffic logs of this router in directory, except current."""
    pattern = re.compile(rf'^{re.escape(router)}-traffic-\d+\.psxnet\.log$')
    current = os.path.basename(current) if current else None
    return sorted(os.path.join(directory, name) for name in os.listdir(directory)
                  if pattern.match(name) and name != current)


def compress_old_traffic_logs(directory, router, current, logger):
    """Compress all old per-start traffic logs; log what was done. Run in a thread."""
    for path in old_traffic_logs(directory, router, current):
        try:
            size = os.path.getsize(path)
            target = gzip_file(path)
            logger.info("Compressed old traffic log %s: %.1f MB -> %.1f MB", path,
                        size / 1e6, os.path.getsize(target) / 1e6)
        except OSError as exc:
            logger.warning("Could not compress old traffic log %s: %s", path, exc)


def rotation_namer(name):
    """RotatingFileHandler.namer: backups get a .gz suffix."""
    return name + '.gz'


def rotation_rotator(source, dest):
    """RotatingFileHandler.rotator: compress the rotated-out log into dest (ends in .gz)."""
    plain = dest[:-3] if dest.endswith('.gz') else dest
    os.replace(source, plain)
    gzip_file(plain)


def use_compression(handler):
    """Make a RotatingFileHandler compress its backups."""
    handler.namer = rotation_namer
    handler.rotator = rotation_rotator


class TestLogCompress(unittest.TestCase):
    """Tests for log compression."""

    def test_gzip_file(self):
        """The compressed file has the same content and time; the original is gone."""
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'r-traffic-1.psxnet.log')
            with open(path, 'w', encoding='utf-8') as f:
                f.write("DATA FROM [upstream] Qs121=1;2;3\n" * 1000)
            os.utime(path, (1000000, 1000000))
            target = gzip_file(path)
            self.assertFalse(os.path.exists(path))
            self.assertEqual(os.path.getmtime(target), 1000000)
            with gzip.open(target, 'rt', encoding='utf-8') as f:
                self.assertEqual(f.read(), "DATA FROM [upstream] Qs121=1;2;3\n" * 1000)

    def test_old_traffic_logs(self):
        """Only this router's per-start traffic logs, not the current one or others."""
        with tempfile.TemporaryDirectory() as d:
            for name in ('m-traffic-100.psxnet.log', 'm-traffic-200.psxnet.log',
                         'm-traffic-150.psxnet.log.gz', 'other-traffic-100.psxnet.log',
                         'm-traffic.psxnet.log', 'm-simevents.log'):
                with open(os.path.join(d, name), 'w', encoding='utf-8'):
                    pass
            old = old_traffic_logs(d, 'm', os.path.join(d, 'm-traffic-200.psxnet.log'))
            self.assertEqual([os.path.basename(p) for p in old], ['m-traffic-100.psxnet.log'])

    def test_rotation(self):
        """A rotating handler with compression keeps gzipped backups."""
        import logging.handlers  # pylint: disable=import-outside-toplevel
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, 'r-simevents.log')
            handler = logging.handlers.RotatingFileHandler(path, maxBytes=200, backupCount=2)
            use_compression(handler)
            logger = logging.getLogger('logcompress-test')
            logger.propagate = False
            logger.addHandler(handler)
            for i in range(30):
                logger.warning("line %02d %s", i, "x" * 40)
            handler.close()
            logger.removeHandler(handler)
            names = sorted(os.listdir(d))
            self.assertEqual(names, ['r-simevents.log', 'r-simevents.log.1.gz',
                                     'r-simevents.log.2.gz'])
            with gzip.open(os.path.join(d, 'r-simevents.log.1.gz'), 'rt') as f:
                self.assertIn('line', f.read())
