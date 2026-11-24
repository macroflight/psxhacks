"""frankenstutter: logs every visible stutter in MSFS 2024 with what the PC was doing then.

Runs on the MSFS PC next to the sim (start it before or after MSFS, or from the start scripts
with $StartFrankenstutter; Ctrl+C stops it). See docs/frankenstutter.md. Sources:

- **PresentMon** (Intel, free, https://github.com/GameTechDev/PresentMon/releases: the console
  "PresentMon-2.x.x-x64.exe", put in ~/.cache/frankenstutter/ or given with --presentmon): the
  time of every frame MSFS presents and every change of the picture on screen, plus per frame
  how long the CPU and the GPU were busy. Needs to run as administrator (or as a member of
  "Performance Log Users"). Without it the agent falls back to
  SimConnect's frame event (coarser: delivery jitter, no CPU/GPU split).
- **SimConnect** (the MSFS 2024 SDK's SimConnect.dll, found in ~/.cache/frankenstutter/, in the
  SDK or via --dll): position, altitude, speed, on ground, camera, sim rate, pause.
- **psutil** (in psxhacks' requirements.txt): CPU per core, MSFS's own CPU, memory, disk reads,
  page faults, network, and which other programs were busy.
- **nvidia-smi** (comes with the NVIDIA driver; skipped on other GPUs): GPU load, VRAM, clocks,
  power, temperature and throttle reasons.

Writes a session folder ~/.cache/frankenstutter/sessions/<UTC start>/ (--out):
  session.json    the PC, GPU, driver, power plan, HAGS, a copy of MSFS's UserCfg.opt, the options
  seconds.csv     one row per second: frame statistics + sim state + system state
  stutters.jsonl  one line per stutter: how long, what kind, a guess at the cause, the context
  markers.csv     notes you type into the agent's window (Enter after the text) when you see one
Then "frankenstutter.py --report FOLDER" (here or on Linux) makes report.md. A read-only web page
and JSON API on port 9787 shows it live (--web-port).

Stutter = a picture held on screen at least --small-ms (default 30 ms: one missed refresh at
60 Hz) and at least --factor times the recent median; medium from --medium-ms (60), major from
--major-ms (150).
"""
import argparse
import bisect
import collections
import contextlib
import csv
import ctypes
import glob
import json
import math
import os
import platform
import shutil
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone

import fst_report
from fst_web import web_thread
from psxhacks_version import get_version

try:
    import psutil
except ImportError:   # the agent still runs (frames + sim), without the system columns
    psutil = None

VERSION = get_version("frankenstutter", __file__)
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "frankenstutter")
STATIONS_URL = "https://weather.rap.ucar.edu/surface/stations.txt"   # as frankenweather's
STATIONS_FILES = (os.path.join(CACHE, "stations.txt"),
                  os.path.join(os.path.expanduser("~"), ".cache", "frankenweather", "stations.txt"))
STATIONS_MAX_AGE_S = 30 * 86400
MSFS_EXES = ("FlightSimulator2024.exe", "FlightSimulator.exe")

# SimConnect (MSFS 2024 SDK)
RECV_EXCEPTION, RECV_OPEN, RECV_QUIT, RECV_EVENT, RECV_EVENT_FRAME = 1, 2, 3, 4, 7
RECV_SIMOBJECT_DATA = 8
PERIOD_SECOND = 4
FLOAT64 = 4
UNUSED = 0xFFFFFFFF
DEF_USER, REQ_USER = 1, 1
EV_FRAME, EV_PAUSE, EV_SIM = 1, 2, 3
SIM_VARS = (("PLANE LATITUDE", "degrees"), ("PLANE LONGITUDE", "degrees"),
            ("PLANE ALTITUDE", "feet"), ("PLANE ALT ABOVE GROUND", "feet"),
            ("GROUND VELOCITY", "knots"), ("AIRSPEED INDICATED", "knots"),
            ("PLANE HEADING DEGREES TRUE", "degrees"), ("SIM ON GROUND", "bool"),
            ("CAMERA STATE", "number"), ("SIMULATION RATE", "number"))
SIM_KEYS = ("lat", "lon", "alt_ft", "agl_ft", "gs_kt", "ias_kt", "hdg", "on_ground", "camera",
            "sim_rate")
CAMERA = {2: "cockpit", 3: "external", 4: "drone", 5: "fixed", 6: "environment", 7: "sixdof",
          8: "gameplay", 9: "showcase", 10: "drone_aircraft", 11: "waiting", 12: "worldmap",
          13: "hangar_rtc", 14: "hangar_custom", 15: "menu_rtc", 16: "in_game_rtc", 17: "replay",
          19: "drone_top_down", 21: "hangar", 24: "ground", 25: "follow_traffic"}

# PresentMon columns, by name in its 1.x / 2.x versions (first one present wins)
# SimConnect frames only after this long without PresentMon's (bursty: a stale SimConnect frame
# made fake "3 s"/"2 min" stutters at a steady 30 fps, MACRO 2026-11-24)
PM_QUIET_S = 5.0
PM_COLS = {
    "app": ("MsBetweenPresents", "msBetweenPresents", "FrameTime"),
    "disp": ("MsBetweenDisplayChange", "msBetweenDisplayChange", "DisplayedTime"),
    "cpu_busy": ("CPUBusy", "MsCPUBusy"),
    "cpu_wait": ("CPUWait", "MsCPUWait"),
    "gpu_busy": ("GPUBusy", "MsGPUBusy", "msGPUActive", "MsGPUActive"),
    "gpu_wait": ("GPUWait", "MsGPUWait"),
    "latency": ("DisplayLatency", "MsUntilDisplayed", "msUntilDisplayed"),
    "type": ("FrameType",),
    "app_name": ("Application",),
}
PM_SESSION = "frankenstutter"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NVSMI_FIELDS = ("utilization.gpu", "memory.used", "memory.total", "clocks.gr", "power.draw",
                "temperature.gpu", "clocks_throttle_reasons.active")


def utc_now() -> datetime:
    """Return the current UTC time."""
    return datetime.now(timezone.utc)


def pct(values, p: float):
    """Return the p-th percentile of values (nearest rank), or None for none."""
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(math.ceil(p / 100.0 * len(s))) - 1))]


def rnd(v, n: int = 1):
    """Round v to n decimals, keeping None."""
    return None if v is None else round(v, n)


def to_float(text):
    """Return text as a float, or None (PresentMon writes NA)."""
    try:
        v = float(text)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# --------------------------------------------------------------------------- airports

def ucar_coord(text: str):
    """Return degrees from a UCAR stations.txt coordinate ("51 29N", "000 27W"), or None."""
    text = text.strip()
    if len(text) < 4 or text[-1] not in "NSEW":
        return None
    try:
        deg, mins = text[:-1].split()
        val = int(deg) + int(mins) / 60.0
    except ValueError:
        return None
    return -val if text[-1] in "SW" else val


def stations_file():
    """Return a UCAR stations.txt (ours or frankenweather's cache; downloaded when old), or None."""
    fresh = [f for f in STATIONS_FILES if os.path.exists(f) and
             time.time() - os.path.getmtime(f) < STATIONS_MAX_AGE_S]
    if fresh:
        return fresh[0]
    try:
        with urllib.request.urlopen(STATIONS_URL, timeout=20) as resp:
            data = resp.read()
        os.makedirs(CACHE, exist_ok=True)
        with open(STATIONS_FILES[0], "wb") as fh:
            fh.write(data)
        return STATIONS_FILES[0]
    except OSError:
        old = [f for f in STATIONS_FILES if os.path.exists(f)]
        return old[0] if old else None


class Airports:  # pylint: disable=too-few-public-methods
    """Nearest airport (ICAO) from the UCAR station list frankenweather also uses."""

    def __init__(self, path):
        """Load the list (empty without one), sorted by latitude."""
        self.rows = []
        if path:
            seen = set()
            with open(path, encoding="latin-1") as fh:
                for line in fh:
                    icao = line[20:24].strip()
                    if line.startswith("!") or len(line) < 55 or len(icao) != 4 or \
                            not icao.isalnum() or icao in seen:
                        continue
                    lat, lon = ucar_coord(line[39:45]), ucar_coord(line[47:54])
                    if lat is not None and lon is not None:
                        seen.add(icao)
                        self.rows.append((lat, lon, icao, line[3:19].strip().title()))
        self.rows.sort()
        self.lats = [r[0] for r in self.rows]

    def nearest(self, lat, lon, max_nm: float = 40.0):
        """Return (icao, nm) of the nearest airport within max_nm, or (None, None)."""
        if lat is None or not self.rows:
            return None, None
        dlat = max_nm / 60.0
        i0 = bisect.bisect_left(self.lats, lat - dlat)
        i1 = bisect.bisect_right(self.lats, lat + dlat)
        best = (None, None)
        for alat, alon, icao, _ in self.rows[i0:i1]:
            d = nm(lat, lon, alat, alon)
            if d <= max_nm and (best[1] is None or d < best[1]):
                best = (icao, d)
        return best[0], rnd(best[1])


def nm(lat1, lon1, lat2, lon2) -> float:
    """Return the great-circle distance in nm."""
    p1, p2, dl = math.radians(lat1), math.radians(lat2), math.radians(lon2 - lon1)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return 3440.065 * math.acos(max(-1.0, min(1.0, c)))


# --------------------------------------------------------------------------- shared state

class State:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """What the threads share; every access under lock."""

    def __init__(self):
        """Start empty."""
        self.lock = threading.Lock()
        self.frames = []          # (arrival time, dict) since the last second was closed
        self.frame_source = None  # "presentmon" / "simconnect"
        self.sim = {}             # latest SimConnect values (SIM_KEYS) + paused, sim_running
        self.sim_frames = []      # (arrival time, frame rate) of SimConnect frame events
        self.sim_connected = False
        self.system = {}          # latest system sample (sampler thread)
        self.gpu = {}             # latest nvidia-smi sample
        self.pm_status = "not started"
        self.markers = []         # (time, text) typed since the last second
        self.procs = []           # child processes (PresentMon, nvidia-smi): killed at the end
        self.per_core = []        # CPU % per logical core, last second
        self.processes = []       # every process, last second (sampler_thread), busiest first
        self.rows = collections.deque(maxlen=3600)      # the last hour of seconds rows
        self.stutters = collections.deque(maxlen=1000)  # the last stutters
        self.status = {}          # for the web page / API
        self.cpu_clock = {}       # the CPU's effective clock (cpu_clock_thread)
        self.stop = threading.Event()


# --------------------------------------------------------------------------- PresentMon

def find_presentmon(given):
    """Return the PresentMon console exe to use, or None."""
    if given:
        return given if os.path.exists(given) else None
    found = sorted(glob.glob(os.path.join(CACHE, "PresentMon*.exe")), reverse=True)
    for d in (r"C:\Program Files\Intel\PresentMon\PresentMonApplication",
              r"C:\Program Files\Intel\PresentMon"):
        found += sorted(glob.glob(os.path.join(d, "PresentMon*.exe")), reverse=True)
    found = [f for f in found if "service" not in os.path.basename(f).lower()]
    return found[0] if found else shutil.which("PresentMon.exe")


def presentmon_thread(state: State, exe: str, process_names, out_dir: str) -> None:
    """Run PresentMon for MSFS and feed every frame into state.frames (restarts if it ends)."""
    while not state.stop.is_set():
        cmd = [exe, "--output_stdout", "--no_console_stats", "--stop_existing_session",
               "--session_name", PM_SESSION]
        for name in process_names:
            cmd += ["--process_name", name]
        with state.lock:
            state.pm_status = "starting"
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,  # pylint: disable=consider-using-with
                                    text=True, bufsize=1,
                                    creationflags=NO_WINDOW)
        except OSError as exc:
            with state.lock:
                state.pm_status = f"cannot start: {exc}"
            return
        with state.lock:
            state.procs.append(proc)
        cols, other = None, []
        for line in proc.stdout:
            if state.stop.is_set():
                break
            parts = line.rstrip("\r\n").split(",")
            if cols is None:
                if len(parts) > 5 and any(p in parts for p in PM_COLS["app"]):
                    cols = {k: next((parts.index(c) for c in names if c in parts), None)
                            for k, names in PM_COLS.items()}
                    with open(os.path.join(out_dir, "presentmon-columns.txt"), "w",
                              encoding="utf-8") as fh:
                        fh.write(line)
                    with state.lock:
                        state.pm_status = "running (waiting for MSFS frames)"
                else:
                    other.append(line.strip())
                    if len(other) <= 20:
                        with state.lock:
                            state.pm_status = "says: " + line.strip()[:200]
                continue
            if len(parts) < len(cols):
                continue
            f = {k: (parts[i] if k in ("type", "app_name") else to_float(parts[i]))
                 for k, i in cols.items() if i is not None and i < len(parts)}
            with state.lock:
                state.frames.append((time.time(), f))
                state.frame_source = "presentmon"
                state.pm_status = "running"
        proc.kill()
        with state.lock:
            state.pm_status = ("ended: " + " | ".join(other[-3:]))[:300] if other else "ended"
        if any("access" in o.lower() and "denied" in o.lower() for o in other) or \
                any("administrator" in o.lower() or "elevat" in o.lower() for o in other):
            msg = ("not allowed to trace frames: once, as administrator, run: net localgroup "
                   '"Performance Log Users" %USERNAME% /add (then sign out and in), or run '
                   "frankenstutter as administrator; using SimConnect frames meanwhile")
            print("  PresentMon " + msg, flush=True)
            with state.lock:
                state.pm_status = msg
            return   # no point retrying; the SimConnect fallback carries on
        state.stop.wait(5.0)


# --------------------------------------------------------------------------- SimConnect

def find_dll(given):
    """Return the SimConnect.dll path to use, or None."""
    if given:
        return given
    cands = [os.path.join(CACHE, "SimConnect.dll")]
    for sdk in (r"C:\MSFS 2024 SDK", r"C:\MSFS SDK"):
        cands += glob.glob(os.path.join(sdk, "SimConnect SDK", "lib", "SimConnect.dll"))
    return next((c for c in cands if os.path.exists(c)), None)


def simconnect_thread(state: State, dll_path: str) -> None:  # pylint: disable=too-many-locals
    """Connect to MSFS (retrying every 5 s), keep state.sim current, log frame events."""
    dll = ctypes.WinDLL(dll_path)   # pylint: disable=no-member
    k32 = ctypes.windll.kernel32    # pylint: disable=no-member
    k32.CreateEventW.restype = ctypes.c_void_p
    event = ctypes.c_void_p(k32.CreateEventW(None, False, False, None))
    while not state.stop.is_set():
        handle = ctypes.c_void_p()
        if dll.SimConnect_Open(ctypes.byref(handle), b"msfs-stutter-agent", None, 0, event, 0):
            state.stop.wait(5.0)
            continue

        def call(name, *args):
            return getattr(dll, f"SimConnect_{name}")(handle, *args) & 0xFFFFFFFF
        for var, unit in SIM_VARS:
            call("AddToDataDefinition", DEF_USER, var.encode(), unit.encode(), FLOAT64,
                 ctypes.c_float(0.0), UNUSED)
        call("RequestDataOnSimObject", REQ_USER, DEF_USER, 0, PERIOD_SECOND, 0, 0, 0, 0)
        call("SubscribeToSystemEvent", EV_FRAME, b"Frame")
        call("SubscribeToSystemEvent", EV_PAUSE, b"Pause")
        call("SubscribeToSystemEvent", EV_SIM, b"Sim")
        with state.lock:
            state.sim_connected = True
        ptr, size = ctypes.c_void_p(), ctypes.c_uint32()
        quit_ = False
        while not state.stop.is_set() and not quit_:
            got = False
            while call("GetNextDispatch", ctypes.byref(ptr), ctypes.byref(size)) == 0:
                got = True
                p = ptr.value
                rid = ctypes.c_uint32.from_address(p + 8).value
                req = ctypes.c_uint32.from_address(p + 12).value
                if rid == RECV_SIMOBJECT_DATA and req == REQ_USER:
                    vals = tuple((ctypes.c_double * len(SIM_VARS)).from_address(p + 40))
                    with state.lock:
                        state.sim.update(zip(SIM_KEYS, vals))
                elif rid == RECV_EVENT_FRAME:
                    fps = ctypes.c_float.from_address(p + 24).value
                    with state.lock:
                        state.sim_frames.append((time.time(), fps))
                elif rid == RECV_EVENT:
                    ev, data = (ctypes.c_uint32.from_address(p + 16).value,
                                ctypes.c_uint32.from_address(p + 20).value)
                    with state.lock:
                        if ev == EV_PAUSE:
                            state.sim["paused"] = int(data != 0)
                        elif ev == EV_SIM:
                            state.sim["sim_running"] = int(data != 0)
                elif rid == RECV_QUIT:
                    quit_ = True
                    break
            if not got:
                k32.WaitForSingleObject(event, 50)
        call("Close")
        with state.lock:
            state.sim_connected = False
            state.sim = {}


# --------------------------------------------------------------------------- system sampling

def io_mbs(proc: dict) -> float:
    """Return a process table entry's disk read + write MB/s (0 when unknown)."""
    return (proc.get("read_mbs") or 0) + (proc.get("write_mbs") or 0)


def rate(new, old, dt: float, unit: float):
    """Return (new - old) / unit per second, or None when either is unknown."""
    if new is None or old is None or dt <= 0:
        return None
    return rnd((new - old) / unit / dt, 2)


def sampler_thread(state: State) -> None:  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    """Sample the whole PC and every process (MSFS among them) every second.

    The PC: CPU per core, DPC and interrupt time, memory, page file, disk, network, context
    switches. Each process: CPU (in cores), memory, disk read/write, page faults, threads.
    """
    if psutil is None:
        return
    cores = psutil.cpu_count() or 1
    psutil.cpu_percent(percpu=True)
    psutil.cpu_times_percent()
    prev, prev_t, last = {}, 0.0, {}   # last: pid -> (cpu s, read B, write B, faults, time)
    while not state.stop.wait(1.0):
        now = time.time()
        s = {"cores": cores}
        per = psutil.cpu_percent(percpu=True)
        s["cpu_total"] = rnd(sum(per) / len(per))
        s["cpu_max_core"] = rnd(max(per))
        tp = psutil.cpu_times_percent()
        s["cpu_dpc_pct"] = rnd(getattr(tp, "dpc", None))           # Windows: drivers' deferred work
        s["cpu_interrupt_pct"] = rnd(getattr(tp, "interrupt", None))
        vm = psutil.virtual_memory()
        s["ram_avail_gb"] = rnd(vm.available / 2**30, 2)
        s["ram_used_pct"] = rnd(vm.percent)
        sw = psutil.swap_memory()
        s["pagefile_used_gb"] = rnd(sw.used / 2**30, 2)
        dio, nio, cst = psutil.disk_io_counters(), psutil.net_io_counters(), psutil.cpu_stats()
        cur = {"disk_r": dio.read_bytes if dio else 0, "disk_w": dio.write_bytes if dio else 0,
               "net_rx": nio.bytes_recv, "net_tx": nio.bytes_sent, "ctx": cst.ctx_switches}
        if prev and now > prev_t:
            dt = now - prev_t
            s["disk_read_mbs"] = rnd((cur["disk_r"] - prev["disk_r"]) / 1e6 / dt)
            s["disk_write_mbs"] = rnd((cur["disk_w"] - prev["disk_w"]) / 1e6 / dt)
            s["net_rx_mbs"] = rnd((cur["net_rx"] - prev["net_rx"]) / 1e6 / dt, 2)
            s["net_tx_mbs"] = rnd((cur["net_tx"] - prev["net_tx"]) / 1e6 / dt, 2)
            s["ctx_switches_s"] = int((cur["ctx"] - prev["ctx"]) / dt)
        prev, prev_t = cur, now
        # every process
        table, seen = [], set()
        for p in psutil.process_iter(["name", "cpu_times", "memory_info", "io_counters",
                                      "num_threads"]):
            info = p.info
            seen.add(p.pid)
            t, mem, io = info.get("cpu_times"), info.get("memory_info"), info.get("io_counters")
            if t is None or p.pid == 0:
                continue
            cpu_s = t.user + t.system
            rb, wb = (io.read_bytes, io.write_bytes) if io else (None, None)
            faults = getattr(mem, "num_page_faults", None) if mem else None
            old = last.get(p.pid)
            last[p.pid] = (cpu_s, rb, wb, faults, now)
            if not old or now <= old[4]:
                continue
            dt = now - old[4]
            name = info.get("name") or str(p.pid)
            row = {"pid": p.pid, "name": name, "cpu_cores": rnd((cpu_s - old[0]) / dt, 2),
                   "rss_gb": rnd(mem.rss / 2**30, 2) if mem else None,
                   "private_gb": rnd(getattr(mem, "private", 0) / 2**30, 2) if mem else None,
                   "read_mbs": rate(rb, old[1], dt, 1e6), "write_mbs": rate(wb, old[2], dt, 1e6),
                   "page_faults_s": rate(faults, old[3], dt, 1.0),
                   "threads": info.get("num_threads"), "msfs": name in MSFS_EXES}
            table.append(row)
        for pid in list(last):
            if pid not in seen:
                del last[pid]
        table.sort(key=lambda r: -(r["cpu_cores"] or 0))
        msfs = next((r for r in table if r["msfs"]), None)
        if msfs:
            s.update(msfs_cpu_cores=msfs["cpu_cores"], msfs_ws_gb=msfs["rss_gb"],
                     msfs_private_gb=msfs["private_gb"], msfs_threads=msfs["threads"],
                     msfs_read_mbs=msfs["read_mbs"], msfs_write_mbs=msfs["write_mbs"],
                     msfs_page_faults_s=msfs["page_faults_s"])
        others = [r for r in table if not r["msfs"] and r["name"] != "System Idle Process"]
        busy = [r for r in others if (r["cpu_cores"] or 0) >= 0.05]
        s["top_other"] = ";".join(f"{r['name']}:{r['cpu_cores']:.2f}" for r in busy[:5])
        s["other_cpu_cores"] = rnd(sum(r["cpu_cores"] or 0 for r in others), 2)
        io_rank = sorted(others, key=lambda r: -io_mbs(r))
        s["top_io"] = ";".join(f"{r['name']}:{io_mbs(r):.1f}" for r in io_rank[:3]
                               if io_mbs(r) >= 1)
        with state.lock:
            state.system = s
            state.per_core = per
            state.processes = table


def nvsmi_thread(state: State) -> None:
    """Read nvidia-smi every 500 ms (NVIDIA only; nothing elsewhere)."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return
    cmd = [exe, "--query-gpu=" + ",".join(NVSMI_FIELDS), "--format=csv,noheader,nounits",
           "-lms", "500"]
    while not state.stop.is_set():
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,  # pylint: disable=consider-using-with
                                    text=True, bufsize=1,
                                    creationflags=NO_WINDOW)
        except OSError:
            return
        with state.lock:
            state.procs.append(proc)
        for line in proc.stdout:
            if state.stop.is_set():
                break
            v = [x.strip() for x in line.split(",")]
            if len(v) < len(NVSMI_FIELDS):
                continue
            g = {"gpu_util": to_float(v[0]), "vram_used_gb": rnd((to_float(v[1]) or 0) / 1024, 2),
                 "vram_total_gb": rnd((to_float(v[2]) or 0) / 1024, 2), "gpu_clock": to_float(v[3]),
                 "gpu_power_w": to_float(v[4]), "gpu_temp": to_float(v[5]), "gpu_throttle": v[6]}
            with state.lock:
                state.gpu = g
        proc.kill()
        state.stop.wait(5.0)


def marker_thread(state: State) -> None:
    """Every line typed into the agent's window is a marker (a note with the time)."""
    for line in sys.stdin:
        text = line.strip()
        if text or line == "\n":
            with state.lock:
                state.markers.append((time.time(), text or "(stutter seen)"))
        if state.stop.is_set():
            return


# --------------------------------------------------------------------------- session info

def run_text(cmd) -> str:
    """Return a command's output, or an error text."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15, check=False,
                              creationflags=NO_WINDOW).stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"({exc})"


def reg_value(path: str, name: str):
    r"""Return a value from HKEY_LOCAL_MACHINE / HKEY_CURRENT_USER (path starts HKLM\ or HKCU\)."""
    try:
        import winreg   # pylint: disable=import-outside-toplevel
    except ImportError:
        return None
    root = winreg.HKEY_LOCAL_MACHINE if path.startswith("HKLM\\") else winreg.HKEY_CURRENT_USER
    try:
        with winreg.OpenKey(root, path[5:]) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def usercfg_paths():
    """Return the UserCfg.opt files of MSFS 2024 that exist (Steam and Microsoft Store)."""
    cands = [os.path.expandvars(r"%APPDATA%\Microsoft Flight Simulator 2024\UserCfg.opt"),
             os.path.expandvars(r"%LOCALAPPDATA%\Packages\Microsoft.Limitless_8wekyb3d8bbwe"
                                r"\LocalCache\UserCfg.opt")]
    return [c for c in cands if os.path.exists(c)]


def session_info(args, out_dir: str, pm_exe) -> dict:
    """Return (and save) what is known about the PC and the settings at the start."""
    info = {"agent_version": VERSION, "start_utc": utc_now().isoformat(timespec="seconds"),
            "options": vars(args), "presentmon": pm_exe, "python": sys.version.split()[0],
            "platform": platform.platform(), "cpu": platform.processor()}
    if os.name == "nt":
        info["cpu"] = reg_value(r"HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0",
                                "ProcessorNameString") or info["cpu"]
        info["power_plan"] = run_text(["powercfg", "/getactivescheme"])
        info["hags_hwschmode"] = reg_value(
            r"HKLM\SYSTEM\CurrentControlSet\Control\GraphicsDrivers", "HwSchMode")  # 2 = on
        info["game_mode"] = reg_value(r"HKCU\Software\Microsoft\GameBar", "AutoGameModeEnabled")
    if psutil:
        info["ram_gb"] = rnd(psutil.virtual_memory().total / 2**30, 1)
        info["cores_logical"] = psutil.cpu_count()
        info["cores_physical"] = psutil.cpu_count(logical=False)
        info["disks"] = [{"mount": p.mountpoint, "fs": p.fstype,
                          "free_gb": rnd(psutil.disk_usage(p.mountpoint).free / 2**30, 1)}
                         for p in psutil.disk_partitions() if "cdrom" not in p.opts]
    if shutil.which("nvidia-smi"):
        info["gpu"] = run_text(["nvidia-smi", "--query-gpu=name,driver_version,memory.total,"
                                "pcie.link.gen.max,pcie.link.width.max", "--format=csv,noheader"])
    for i, path in enumerate(usercfg_paths()):
        dest = os.path.join(out_dir, f"UserCfg{'' if i == 0 else i}.opt")
        shutil.copyfile(path, dest)
        info.setdefault("usercfg", []).append(path)
    with open(os.path.join(out_dir, "session.json"), "w", encoding="utf-8") as fh:
        json.dump(info, fh, indent=1, default=str)
    return info


# --------------------------------------------------------------------------- the main loop

SECOND_COLS = (
    "utc", "source", "frames", "fps", "disp_p50", "disp_p95", "disp_p99", "disp_max",
    "over_20", "over_30", "over_60", "app_fps", "app_p95", "app_max", "cpu_busy_p95",
    "gpu_busy_p95", "stutter_small", "stutter_medium", "stutter_major", "holds", "sim_fps",
    "lat", "lon", "alt_ft", "agl_ft", "gs_kt", "ias_kt", "hdg", "on_ground", "camera", "sim_rate",
    "paused", "airport", "airport_nm",
    "cpu_total", "cpu_max_core", "cpu_dpc_pct", "cpu_interrupt_pct", "cpu_clock_mhz",
    "cpu_perf_pct",
    "ctx_switches_s", "pagefile_used_gb", "msfs_cpu_cores", "other_cpu_cores", "ram_avail_gb",
    "ram_used_pct", "msfs_ws_gb", "msfs_private_gb", "msfs_threads", "msfs_read_mbs",
    "msfs_write_mbs", "msfs_page_faults_s", "disk_read_mbs", "disk_write_mbs", "net_rx_mbs",
    "net_tx_mbs", "gpu_util", "vram_used_gb", "vram_total_gb", "gpu_clock", "gpu_power_w",
    "gpu_temp", "gpu_throttle", "top_other", "top_io")


class Detector:
    """Find stutters in the stream of picture changes."""

    def __init__(self, args):
        """Thresholds from the options."""
        self.args = args
        self.recent = collections.deque(maxlen=240)   # about 4 s at 60 Hz
        self.app_recent = collections.deque(maxlen=8)

    def feed(self, f: dict):
        """Take one frame; return (severity, displayed ms, median ms) for a stutter, or None."""
        disp = f.get("disp")
        app = f.get("app")
        if app is not None:
            self.app_recent.append(f)
        dt = disp if disp is not None else (app if self.args.app_frames else None)
        if dt is None or dt <= 0:
            return None
        med = statistics.median(self.recent) if len(self.recent) >= 30 else None
        self.recent.append(dt)
        if med is None or dt < self.args.small_ms or dt < self.args.factor * med:
            return None
        sev = ("major" if dt >= self.args.major_ms else
               "medium" if dt >= self.args.medium_ms else "small")
        return sev, dt, med

    def cause(self, med: float) -> dict:
        """Return a guess at why, from the last few app frames' CPU and GPU busy times."""
        frames = list(self.app_recent)
        if not frames:
            return {"guess": "unknown (no frame details)"}
        worst = max(frames, key=lambda x: x.get("app") or 0)
        app, cpu, gpu = worst.get("app"), worst.get("cpu_busy"), worst.get("gpu_busy")
        out = {"app_ms": rnd(app), "cpu_busy_ms": rnd(cpu), "gpu_busy_ms": rnd(gpu),
               "cpu_wait_ms": rnd(worst.get("cpu_wait")), "gpu_wait_ms": rnd(worst.get("gpu_wait")),
               "frame_type": worst.get("type")}
        if app is None or app < max(self.args.small_ms, 1.6 * med) * 0.8:
            out["guess"] = ("presentation (frames were made in time, shown late: frame generation, "
                            "VSync, DWM pacing)")
        elif cpu is not None and gpu is not None:
            if cpu >= 0.7 * app and cpu >= 1.3 * gpu:
                out["guess"] = "CPU (the sim's own work: main thread, loading, streaming, add-ons)"
            elif gpu >= 0.7 * app and gpu >= 1.3 * cpu:
                out["guess"] = "GPU (rendering took too long: settings, VRAM, shaders)"
            else:
                out["guess"] = "waiting (neither busy: I/O, locks, a stalled thread)"
        else:
            out["guess"] = "slow frame (no CPU/GPU split from this PresentMon)"
        return out


def cpu_clock_thread(state: State) -> None:
    """Windows: the CPU's real clock, every second (psutil only gives the nominal one).

    "% Processor Performance" x the nominal "Processor Frequency" = the effective clock, which
    drops when a power limit (such as an XTU cap) or heat holds the CPU back.
    """
    exe = shutil.which("typeperf")
    if not exe:
        return
    cmd = [exe, r"\Processor Information(_Total)\% Processor Performance",
           r"\Processor Information(_Total)\Processor Frequency", "-si", "1"]
    while not state.stop.is_set():
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,  # pylint: disable=consider-using-with
                                    text=True, bufsize=1, creationflags=NO_WINDOW)
        except OSError:
            return
        with state.lock:
            state.procs.append(proc)
        for line in proc.stdout:
            if state.stop.is_set():
                break
            v = [x.strip().strip('"') for x in line.split(",")]
            perf, base = (to_float(v[1]), to_float(v[2])) if len(v) >= 3 else (None, None)
            if perf is not None and base:
                with state.lock:
                    state.cpu_clock = {"cpu_clock_mhz": rnd(base * perf / 100.0, 0),
                                       "cpu_perf_pct": rnd(perf)}
        proc.kill()
        state.stop.wait(5.0)


def parse_args():
    """Return the command line options."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--presentmon", help="PresentMon console exe (default: PresentMon*.exe in "
                    f"{CACHE}, Intel's install folder or the PATH)")
    ap.add_argument("--no-presentmon", action="store_true", help="SimConnect frames only")
    ap.add_argument("--dll", help=f"SimConnect.dll (default: in {CACHE} or the MSFS 2024 SDK)")
    ap.add_argument("--process", action="append",
                    help="MSFS exe name (default: " + " and ".join(MSFS_EXES) + ")")
    ap.add_argument("--small-ms", type=float, default=30.0)
    ap.add_argument("--medium-ms", type=float, default=60.0)
    ap.add_argument("--major-ms", type=float, default=150.0)
    ap.add_argument("--factor", type=float, default=1.75,
                    help="a stutter is also this many times the recent median (1.75: one "
                    "repeated refresh at 60 Hz, 33.3 ms, counts)")
    ap.add_argument("--hold-ms", type=float, default=fst_report.HOLD_MS,
                    help="a picture held this long is a pause, a menu or a load, not a stutter")
    ap.add_argument("--app-frames", action="store_true",
                    help="detect on MSFS's own frames when PresentMon gives no display times")
    ap.add_argument("--simconnect-stutter-ms", type=float, default=100.0,
                    help="without PresentMon: a gap between SimConnect frame events this long")
    ap.add_argument("--out", default=os.path.join(CACHE, "sessions"),
                    help="where session folders go")
    ap.add_argument("--report", nargs="+", metavar="FOLDER",
                    help="write report.md for these session folders (and print it), then exit")
    ap.add_argument("--web-port", type=int, default=9787, help="the page and JSON API (0: off)")
    ap.add_argument("--web-bind", default="0.0.0.0",
                    help="address to listen on (127.0.0.1: this PC only)")
    ap.add_argument("--psx-port-override", help=argparse.SUPPRESS)   # start scripts: unused
    ap.add_argument("--replay", help=argparse.SUPPRESS)   # a PresentMon CSV, for testing anywhere
    return ap.parse_args()


def replay_thread(state: State, path: str) -> None:
    """Feed a recorded PresentMon CSV in at its own pace (testing without MSFS)."""
    with open(path, encoding="utf-8") as fh:
        header = fh.readline().strip().split(",")
        cols = {k: next((header.index(c) for c in n if c in header), None)
                for k, n in PM_COLS.items()}
        for line in fh:
            parts = line.strip().split(",")
            f = {k: (parts[i] if k in ("type", "app_name") else to_float(parts[i]))
                 for k, i in cols.items() if i is not None and i < len(parts)}
            with state.lock:
                state.frames.append((time.time(), f))
                state.frame_source = "presentmon"
                state.pm_status = "replay"
            time.sleep((f.get("app") or 16.7) / 1000.0)


def iso(t: float, spec: str = "seconds") -> str:
    """Return a unix time as an ISO UTC text."""
    return datetime.fromtimestamp(t, timezone.utc).isoformat(timespec=spec)


class Agent:  # pylint: disable=too-many-instance-attributes
    """The once-a-second loop: gather, detect, write the logs, keep the web state."""

    def __init__(self, args, state: State, out_dir: str, pm_exe):
        """Open the session's log files."""
        self.args, self.state, self.out_dir, self.pm_exe = args, state, out_dir, pm_exe
        self.airports = Airports(stations_file())
        self.det = Detector(args)
        self.files = contextlib.ExitStack()

        def opened(name, mode):
            return self.files.enter_context(open(os.path.join(out_dir, name), mode, newline="",
                                                 encoding="utf-8"))
        self.sec_fh = opened("seconds.csv", "w")
        self.sec = csv.DictWriter(self.sec_fh, SECOND_COLS, extrasaction="ignore")
        self.sec.writeheader()
        self.stut_fh = opened("stutters.jsonl", "a")
        self.mark_fh = opened("markers.csv", "a")
        self.marks = csv.writer(self.mark_fh)
        self.proc_fh = opened("processes.csv", "w")
        self.procs_csv = csv.writer(self.proc_fh)
        self.procs_csv.writerow(("utc", "rank", "name", "pid", "cpu_cores", "private_gb",
                                 "read_mbs", "write_mbs", "page_faults_s", "threads"))
        self.all_markers = []
        self.history = collections.deque(maxlen=10)   # the last rows, for a stutter's context
        self.minute = collections.deque()             # (time, severity) of the last 60 s
        self.totals = collections.Counter()
        self.last_status = 0.0
        self.last_sim_frame = None
        self.last_pm_frames = 0.0                     # when PresentMon last gave frames
        self.settle_until = 0.0                       # no stutters before this (after a hold)

    def take(self):
        """Return everything the threads gathered since the last second (and reset it)."""
        st = self.state
        with st.lock:
            got = {"frames": st.frames, "sim_frames": st.sim_frames, "markers": st.markers,
                   "sim": dict(st.sim), "system": {**st.system, **st.gpu, **st.cpu_clock},
                   "source": st.frame_source, "pm_status": st.pm_status,
                   "sim_ok": st.sim_connected, "processes": list(st.processes)}
            st.frames, st.sim_frames, st.markers = [], [], []
        return got

    def frame_stats(self, row: dict, got: dict) -> list:  # pylint: disable=too-many-locals
        """Add the frame statistics to row; return the stutters (time, sev, ms, median, cause)."""
        frames, sim_frames = got["frames"], got["sim_frames"]
        stutters = []
        disp = [f["disp"] for _, f in frames if f.get("disp") is not None]
        app = [f["app"] for _, f in frames if f.get("app") is not None]
        now = time.time()
        if frames:
            self.last_pm_frames = now
            row["source"] = got["source"]
            for t, f in frames:
                hit = self.det.feed(f)
                if hit:
                    stutters.append((t, *hit, self.det.cause(hit[2])))
        # SimConnect frame gaps only without PresentMon for PM_QUIET_S (its output is bursty)
        use_sim = not frames and now - self.last_pm_frames >= PM_QUIET_S
        if use_sim and sim_frames:
            row["source"] = "simconnect"
        for t, _ in sim_frames:
            if use_sim and self.last_sim_frame is not None:
                gap = (t - self.last_sim_frame) * 1000.0
                disp.append(gap)
                if gap >= self.args.simconnect_stutter_ms:
                    sev = "major" if gap >= self.args.major_ms else "medium"
                    stutters.append((t, sev, gap, None, {"guess": "unknown (SimConnect only)"}))
            self.last_sim_frame = t
        if sim_frames:
            row["sim_fps"] = rnd(statistics.mean(r for _, r in sim_frames))
        disp = [d for d in disp if d < self.args.hold_ms]   # a hold isn't a frame time
        if disp:
            row.update(frames=len(disp), fps=rnd(1000.0 * len(disp) / max(1.0, sum(disp))),
                       disp_p50=rnd(pct(disp, 50)), disp_p95=rnd(pct(disp, 95)),
                       disp_p99=rnd(pct(disp, 99)), disp_max=rnd(max(disp)),
                       over_20=sum(d > 20 for d in disp), over_30=sum(d > 30 for d in disp),
                       over_60=sum(d > 60 for d in disp))
        if app:
            row.update(app_fps=rnd(1000.0 * len(app) / max(1.0, sum(app))),
                       app_p95=rnd(pct(app, 95)), app_max=rnd(max(app)))
        for key, name in (("cpu_busy", "cpu_busy_p95"), ("gpu_busy", "gpu_busy_p95")):
            vals = [f[key] for _, f in frames if f.get(key) is not None]
            if vals:
                row[name] = rnd(pct(vals, 95))
        stutters = self.real_stutters(row, stutters)
        counts = collections.Counter(s[1] for s in stutters)
        row.update(stutter_small=counts["small"], stutter_medium=counts["medium"],
                   stutter_major=counts["major"])
        return stutters

    def real_stutters(self, row: dict, stutters: list) -> list:
        """Return the stutters without holds (--hold-ms or longer, paused, SETTLE_S after)."""
        paused = (row.get("paused") or 0) >= 1
        if paused:
            self.settle_until = time.time() + fst_report.SETTLE_S
        out = []
        for stutter in stutters:
            t, dt = stutter[0], stutter[2]
            if dt >= self.args.hold_ms:
                self.settle_until = max(self.settle_until, t + fst_report.SETTLE_S)
                self.totals["hold"] += 1
                print(f"  hold {dt / 1000:.1f} s at {iso(t)[11:19]}Z (pause, menu or load): "
                      "not a stutter", flush=True)
            elif paused or t < self.settle_until:
                self.totals["hold"] += 1
            else:
                out.append(stutter)
        row["holds"] = len(stutters) - len(out)
        return out

    def build_row(self, now: float, got: dict) -> dict:
        """Return this second's row: sim state, nearest airport, system and GPU."""
        sim = got["sim"]
        row = {"utc": iso(now)}
        row.update({k: (rnd(v, 5) if k in ("lat", "lon") else rnd(v)) for k, v in sim.items()
                    if k in SIM_KEYS or k == "paused"})
        if row.get("camera") is not None:
            row["camera"] = CAMERA.get(int(row["camera"]), str(int(row["camera"])))
        row["airport"], row["airport_nm"] = self.airports.nearest(sim.get("lat"), sim.get("lon"))
        row.update(got["system"])
        return row

    def write(self, row: dict, got: dict, stutters: list) -> None:  # pylint: disable=too-many-locals
        """Write the row, the busiest processes, the stutters and the markers."""
        self.sec.writerow(row)
        processes = got["processes"]
        heavy_io = [p for p in processes[10:] if io_mbs(p) >= 5]
        for rank, p in enumerate(processes[:10] + heavy_io[:5], start=1):
            self.procs_csv.writerow((row["utc"], rank, p["name"], p["pid"], p["cpu_cores"],
                                     p.get("private_gb"), p.get("read_mbs"), p.get("write_mbs"),
                                     p.get("page_faults_s"), p.get("threads")))
        self.history.append(row)
        for t, sev, dt, med, cause in stutters:
            ctx = {k: max((r.get(k) or 0) for r in self.history) for k in (
                "msfs_read_mbs", "disk_read_mbs", "net_rx_mbs", "msfs_page_faults_s",
                "cpu_max_core", "other_cpu_cores", "vram_used_gb", "cpu_dpc_pct")}
            ctx["cpu_clock_mhz_min"] = min((r.get("cpu_clock_mhz") or 0) for r in self.history)
            rec = {"utc": iso(t, "milliseconds"), "severity": sev, "shown_ms": rnd(dt),
                   "median_ms": rnd(med), **cause, "max_last_10s": ctx, "row": row}
            self.stut_fh.write(json.dumps(rec, default=str) + "\n")
            with self.state.lock:
                self.state.stutters.append(rec)
            self.minute.append((t, sev))
            self.totals[sev] += 1
        for t, text in got["markers"]:
            self.marks.writerow([iso(t), text, row.get("airport"), row.get("lat"), row.get("lon")])
            print(f"  marker noted: {text}")
            self.all_markers.append({"utc": iso(t), "text": text, "airport": row.get("airport")})
        for fh in (self.sec_fh, self.proc_fh, self.stut_fh, self.mark_fh):
            fh.flush()

    def tick(self) -> None:
        """Do one second."""
        now = time.time()
        got = self.take()
        row = self.build_row(now, got)
        stutters = self.frame_stats(row, got)
        self.write(row, got, stutters)
        while self.minute and self.minute[0][0] < now - 60:
            self.minute.popleft()
        last_min = collections.Counter(s for _, s in self.minute)
        pm_status = got["pm_status"] if self.pm_exe or self.args.replay else "off"
        with self.state.lock:
            self.state.rows.append(row)
            self.state.status = {"version": VERSION, "session_dir": self.out_dir,
                                 "presentmon": pm_status, "simconnect": got["sim_ok"],
                                 "frame_source": got["source"], "last_minute": dict(last_min),
                                 "stutters_total": dict(self.totals),
                                 "markers": self.all_markers[-50:]}
        if now - self.last_status >= 10:
            self.last_status = now
            where = (f"{row.get('airport') or 'enroute'} {row.get('alt_ft') or 0:.0f} ft "
                     f"{row.get('camera') or ''}") if got["sim_ok"] else "no SimConnect"
            print(f"{row['utc'][11:19]}Z {row.get('fps') or 0:5.1f} fps p99 "
                  f"{row.get('disp_p99') or 0:5.1f} ms | last min: {last_min['small']} small, "
                  f"{last_min['medium']} medium, {last_min['major']} major | {where} | "
                  f"PresentMon {pm_status}", flush=True)

    def close(self) -> None:
        """Close the log files."""
        self.files.close()


def start_threads(args, state: State, out_dir: str, pm_exe, info: dict) -> None:
    """Start every gathering thread (and say what is missing)."""
    threads = []
    if args.replay:
        threads.append(threading.Thread(target=replay_thread, args=(state, args.replay),
                                        daemon=True))
    elif pm_exe:
        print(f"  PresentMon: {pm_exe}")
        threads.append(threading.Thread(target=presentmon_thread, daemon=True,
                                        args=(state, pm_exe, args.process or MSFS_EXES, out_dir)))
    else:
        print(f"  PresentMon not found (put PresentMon-2.x.x-x64.exe in {CACHE} or use "
              "--presentmon PATH): falling back to SimConnect frame events (coarser)")
    dll = find_dll(args.dll) if os.name == "nt" else None
    if dll:
        print(f"  SimConnect: {dll}")
        threads.append(threading.Thread(target=simconnect_thread, args=(state, dll), daemon=True))
    else:
        print("  SimConnect.dll not found (or not Windows): no position, altitude or camera")
    if args.web_port:
        print(f"  web page and API: http://localhost:{args.web_port}/ (read-only)")
        threads.append(threading.Thread(target=web_thread, daemon=True,
                                        args=(state, args.web_bind, args.web_port, info)))
    for target in (sampler_thread, nvsmi_thread, cpu_clock_thread, marker_thread):
        threads.append(threading.Thread(target=target, args=(state,), daemon=True))
    for t in threads:
        t.start()


def main() -> None:
    """Start the threads and write a row every second until Ctrl+C."""
    args = parse_args()
    if args.report:
        fst_report.report(args.report)
        return
    out_dir = os.path.join(args.out, utc_now().strftime("%Y%m%d-%H%M%SZ"))
    os.makedirs(out_dir, exist_ok=True)
    state = State()
    pm_exe = None if args.no_presentmon else find_presentmon(args.presentmon)
    info = session_info(args, out_dir, pm_exe)
    print(f"frankenstutter {VERSION}: logging to {out_dir}")
    print(f"  CPU {info.get('cpu')}, RAM {info.get('ram_gb')} GB, GPU {info.get('gpu', '?')}")
    if psutil is None:
        print("  psutil missing (pip install psutil): no CPU/memory/disk/network columns")
    agent = Agent(args, state, out_dir, pm_exe)
    print(f"  {len(agent.airports.rows)} airports" if agent.airports.rows else
          "  no airport list (no internet?): no nearest airport")
    start_threads(args, state, out_dir, pm_exe, info)
    print("Type a note and Enter (or just Enter) when you see a stutter; Ctrl+C stops.\n")
    next_tick = time.time() + 1.0
    try:
        while True:
            time.sleep(max(0.0, next_tick - time.time()))
            next_tick += 1.0
            agent.tick()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop.set()
        with state.lock:
            for proc in state.procs:
                if proc.poll() is None:
                    proc.kill()
        if pm_exe:   # PresentMon's trace session outlives a killed PresentMon: end it
            run_text([pm_exe, "--terminate_existing_session", "--session_name", PM_SESSION])
        agent.close()
        print(f"\nStopped. Logs in {out_dir}\nReport: frankenstutter.py --report \"{out_dir}\"")


if __name__ == "__main__":
    main()
