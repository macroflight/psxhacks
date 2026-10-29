"""Accelerations from frankenboostproxy's --accel-port, next to a recording.

frankenboostproxy streams one JSON record per line at 5 Hz (heave_g,
surge_g, sway_g, rates, ...). They are derived from PSX's position data,
interpolated: they lag and smooth, so they are an estimate, not a
calibrated g meter.

The bands compare the vertical acceleration with how pilots report
turbulence: the peak deviation from 1 g (here from the recording's
median heave, which isn't exactly 1 g in level flight) is light from
0.05 g, moderate from 0.2 g, severe from 0.5 g and extreme from 1.5 g.

The PSX boost server's raw feed (frankenboostproxy relays it, e.g. port
10749) is better for the vertical: ~13 Hz, altitude to 0.01 ft, and
PSX's own millisecond stamp, so the vertical acceleration comes from the
altitude on the simulator's clock, free of network jitter
(boost_vertical_stats()).
"""
import json
import socket
import statistics
import time
from typing import Optional

BANDS = (("light", 0.05), ("moderate", 0.2), ("severe", 0.5), ("extreme", 1.5))


def record(path: str, seconds: float, host: str = "127.0.0.1", port: int = 10751) -> int:
    """Write the accel stream's JSON lines to path for seconds; return the count."""
    count = 0
    end = time.time() + seconds
    with socket.create_connection((host, port), timeout=5) as sock, \
            open(path, "w", buffering=1, encoding="utf-8") as out:
        sock.settimeout(1.0)
        buf = b""
        while time.time() < end:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            buf += data
            *complete, buf = buf.split(b"\n")
            for line in complete:
                out.write(line.decode(errors="replace").strip() + "\n")
                count += 1
    return count


def record_raw(path: str, seconds: float, host: str = "127.0.0.1", port: int = 10749) -> int:
    """Write "<epoch> <line>" for each raw boost line to path for seconds; return the count."""
    count = 0
    end = time.time() + seconds
    with socket.create_connection((host, port), timeout=5) as sock, \
            open(path, "w", buffering=1, encoding="utf-8") as out:
        sock.settimeout(1.0)
        buf = b""
        while time.time() < end:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            buf += data
            *complete, buf = buf.split(b"\n")
            now = time.time()
            for line in complete:
                out.write(f"{now:.3f} {line.decode(errors='replace').strip()}\n")
                count += 1
    return count


def _boost_altitudes(path: str) -> list:
    """Return [(sim seconds, altitude m)] from a record_raw() file, on PSX's ms clock."""
    points = []
    last_ms = None
    offset = 0.0
    with open(path, encoding="utf-8") as raw:
        for line in raw:
            parts = line.split(" ", 1)[-1].strip().split(";")
            if len(parts) != 8 or parts[0] != "F":
                continue
            ms = int(parts[7]) % 1000
            if last_ms is not None and ms < last_ms:
                offset += 1.0     # the stamp wraps every second
            last_ms = ms
            points.append((offset + ms / 1000.0, int(parts[1]) / 100.0 * 0.3048))
    return points


def boost_vertical_stats(path: str, span_s: float = 0.5) -> Optional[dict]:
    """Return vertical acceleration statistics (g, from 1 g) like heave_stats(), or None.

    The acceleration is the altitude's second difference over span_s
    either side (shorter jolts are smoothed out).
    """
    points = _boost_altitudes(path)
    if len(points) < 20:
        return None
    accs = []
    j = k = 0
    for i, (t_mid, h_mid) in enumerate(points):
        while points[j][0] < t_mid - span_s:
            j += 1
        k = max(k, i)
        while k < len(points) - 1 and points[k][0] < t_mid + span_s:
            k += 1
        (t_lo, h_lo), (t_hi, h_hi) = points[j], points[k]
        if t_mid - t_lo < 0.8 * span_s or t_hi - t_mid < 0.8 * span_s:
            continue
        accs.append(((h_hi - h_mid) / (t_hi - t_mid) - (h_mid - h_lo) / (t_mid - t_lo)) /
                    ((t_hi - t_lo) / 2.0) / 9.81)
    return _deviation_stats(accs)


def _deviation_stats(values: list) -> Optional[dict]:
    if len(values) < 10:
        return None
    base = statistics.median(values)
    dev = sorted(abs(v - base) for v in values)
    stats = {"samples": len(values), "base_g": base,
             "rms_g": (sum(d * d for d in dev) / len(dev)) ** 0.5,
             "p95_g": dev[int(0.95 * (len(dev) - 1))], "max_g": dev[-1]}
    for i, (name, low) in enumerate(BANDS):
        high = BANDS[i + 1][1] if i + 1 < len(BANDS) else float("inf")
        stats[name] = sum(low <= d < high for d in dev) / len(dev)
    return stats


def heave_stats(path: str) -> Optional[dict]:
    """Return heave deviation statistics (g) and the share of time per band, or None."""
    heave = []
    with open(path, encoding="utf-8") as accel:
        for line in accel:
            try:
                heave.append(float(json.loads(line)["heave_g"]))
            except (ValueError, KeyError, TypeError):
                continue
    return _deviation_stats(heave)


def report(path: str, raw: bool = False) -> str:
    """Return a one-line summary of heave_stats(path), or boost_vertical_stats() if raw."""
    stats = boost_vertical_stats(path) if raw else heave_stats(path)
    if stats is None:
        return f"{path}: no accel data"
    bands = " ".join(f"{name} {stats[name]:.0%}" for name, _ in BANDS)
    what = "vertical" if raw else "heave"
    return (f"{path}: {what} dev rms {stats['rms_g']:.3f} p95 {stats['p95_g']:.3f} "
            f"max {stats['max_g']:.3f} g; {bands}")
