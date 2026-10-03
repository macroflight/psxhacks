"""Monitor and control sim-support services, over the PSX network.

Runs in a master sim (later possibly slave sims too). Periodically checks
whether a fixed list of third-party/addon services are running (initially
by looking for a matching OS process), broadcasts their combined status as
a PSX addon= message (addon=FRANKENCONTROL:1:STATUS:<json>) whenever it
changes or at least every --broadcast-interval seconds, and listens for
addon=FRANKENCONTROL:1:COMMAND:<json> messages instructing it to start,
stop, or restart one of them.

Starting/stopping/restarting a service is done by invoking the matching
start_<name>.ps1/stop_<name>.ps1/restart_<name>.ps1 script in start_scripts
(see --start-scripts-dir) - this script never duplicates that logic itself,
so a service's real startup behavior (config file rewriting, correct
router port, etc.) always comes from the single place start_scripts
already defines it.
"""
import argparse
import asyncio
import inspect
import json
import logging
import subprocess
import sys
import time
import traceback
import uuid
from pathlib import Path
from typing import NamedTuple, Optional

import psutil  # pylint: disable=import-error

import psx
from psxhacks_version import get_version

__version__ = get_version("frankencontrol", __file__)
__MYNAME__ = 'frankencontrol'
__MY_CLIENT_ID__ = 'FCTRL'
__MY_DISPLAY_NAME__ = 'FrankenControl'
__MY_DESCRIPTION__ = (
    "Monitor and control sim-support services for PSX, over the PSX network, "
    "via the start_scripts framework.")

ADDON_PREFIX = "FRANKENCONTROL"
PROTOCOL_VERSION = 1

_VALID_ACTIONS = ("start", "stop", "restart")


class ServiceSpec(NamedTuple):
    """Static info about one monitored/controllable service."""

    display_name: str
    # Matches start_<script_name>.ps1 / stop_<script_name>.ps1 / restart_<script_name>.ps1
    # in start_scripts.
    script_name: str
    # How to recognize the service's process - see is_service_running().
    match_kind: str  # "name" | "name_prefix" | "python" | "java_jar"
    match_pattern: str


# Initial service list (per the user's own spec). PSX.NET.Orchestration is
# deliberately not included here: it's not normally run in the master sim
# (it's a slave-sim-only addon per start_psx_net_orchestration.ps1's own
# comment), unlike the others below.
SERVICES: dict[str, ServiceSpec] = {
    "bacars": ServiceSpec("BACARS", "bacars", "name", "PSX.Bacars.UI"),
    "cpdlc": ServiceSpec("HAFAP/CPDLC", "cpdlc", "python", "psx-acars.py"),
    "frankenweather": ServiceSpec(
        "FrankenWeather", "frankenweather", "python", "frankenweather.py"),
    "srsl": ServiceSpec("SRSL-PSX (master)", "srsl_psx_master", "java_jar", "SRSL-PSX.jar"),
    "cmc_psx": ServiceSpec("CMC-PSX", "cmc_psx", "java_jar", "CMC-PSX.jar"),
    "frankentanker": ServiceSpec("FrankenTanker", "frankentanker", "python", "frankentanker.py"),
    "frankenpush": ServiceSpec("FrankenPush", "frankenpush", "python", "frankenpush.py"),
    "simlink_bridge": ServiceSpec(
        "psx_simlink_bridge", "psx_simlink_bridge", "name_prefix", "psx_simlink_bridge"),
}


def _process_matches(info: dict, kind: str, pattern: str) -> bool:
    """Return True if a psutil process_iter() info dict matches (kind, pattern)."""
    name = (info.get('name') or '').lower()
    pattern_lower = pattern.lower()
    if kind == "name":
        return name in (pattern_lower, f"{pattern_lower}.exe")
    if kind == "name_prefix":
        return name.startswith(pattern_lower)
    if kind == "python":
        if not name.startswith("python"):
            return False
        cmdline = info.get('cmdline') or []
        return any(pattern_lower in arg.lower() for arg in cmdline)
    if kind == "java_jar":
        if name not in ("java.exe", "java"):
            return False
        cmdline = info.get('cmdline') or []
        return any(pattern_lower in arg.lower() for arg in cmdline)
    raise ValueError(f"Unknown match kind: {kind}")


def is_service_running(kind: str, pattern: str) -> bool:
    """Return True if any running process matches (kind, pattern)."""
    for proc in psutil.process_iter(['name', 'cmdline']):
        try:
            if _process_matches(proc.info, kind, pattern):
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return False


class Script:  # pylint: disable=too-many-instance-attributes
    """FrankenControl's state and coroutines."""

    def __init__(self) -> None:
        """Initialize state; handle_args() fills in self.args once run() starts."""
        self.logger = logging.getLogger(__MYNAME__)
        self.args: Optional[argparse.Namespace] = None
        self.psx: Optional[psx.Client] = None
        self.psx_connected = False
        self.instance_uuid = str(uuid.uuid4())
        self.service_running: dict[str, bool] = {key: False for key in SERVICES}
        self.taskgroup: Optional[asyncio.TaskGroup] = None
        self.tasks: set = set()

    # ------------------------------------------------------------------
    # Service checking
    # ------------------------------------------------------------------

    def _check_services(self) -> bool:
        """Re-check every service's running status. Return True if anything changed."""
        changed = False
        for key, spec in SERVICES.items():
            running = is_service_running(spec.match_kind, spec.match_pattern)
            if running != self.service_running[key]:
                self.logger.info(
                    "%s (%s): %s -> %s", key, spec.display_name,
                    self.service_running[key], running)
                self.service_running[key] = running
                changed = True
        return changed

    def _build_status_payload(self) -> dict:
        return {
            "uuid": self.instance_uuid,
            "timestamp": time.time(),
            "services": {
                key: {
                    "display_name": spec.display_name,
                    "running": self.service_running[key],
                }
                for key, spec in SERVICES.items()
            },
        }

    def _print_status_summary(self) -> None:
        """Print a short one-line status summary, for the console/log only."""
        summary = " ".join(
            f"{spec.display_name}={'UP' if self.service_running[key] else 'DOWN'}"
            for key, spec in SERVICES.items())
        self.logger.info("Status: %s", summary)

    async def _broadcast_status(self) -> None:
        if not self.psx_connected:
            return
        payload = json.dumps(self._build_status_payload())
        self.psx.send("addon", f"{ADDON_PREFIX}:{PROTOCOL_VERSION}:STATUS:{payload}")
        self._print_status_summary()

    # ------------------------------------------------------------------
    # Service control (start_scripts)
    # ------------------------------------------------------------------

    def _script_path(self, verb: str, script_name: str) -> Path:
        return self.args.start_scripts_dir / f"{verb}_{script_name}.ps1"

    def _run_powershell(self, script_path: Path) -> None:
        if not script_path.exists():
            self.logger.error("Script not found: %s", script_path)
            return
        self.logger.info("Running: %s", script_path)
        # Fire-and-forget: start_<x>.ps1 for several services blocks for that
        # service's entire lifetime (it IS that service's own console window),
        # so waiting here would hang this process forever. This matches how
        # startsim_master.ps1/startsim_slave.ps1 themselves launch these
        # scripts - a non-blocking Start-Process, one level up.
        creationflags = subprocess.CREATE_NEW_CONSOLE if sys.platform == 'win32' else 0
        subprocess.Popen(  # pylint: disable=consider-using-with
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script_path)],
            creationflags=creationflags)

    def _execute_command(self, action: str, service: str) -> None:
        if service not in SERVICES:
            self.logger.warning("Unknown service in command: %s", service)
            return
        if action not in _VALID_ACTIONS:
            self.logger.warning("Unknown action in command: %s", action)
            return
        spec = SERVICES[service]
        self.logger.info("ACTION: %s %s", action, spec.display_name)
        self._run_powershell(self._script_path(action, spec.script_name))

    # ------------------------------------------------------------------
    # PSX protocol
    # ------------------------------------------------------------------

    def _handle_addon(self, _key: str, value: str) -> None:
        prefix = f"{ADDON_PREFIX}:{PROTOCOL_VERSION}:COMMAND:"
        if not value.startswith(prefix):
            return
        try:
            data = json.loads(value[len(prefix):])
            action = data["action"]
            service = data["service"]
        except (ValueError, KeyError) as exc:
            self.logger.warning("Malformed %s command: %s (%s)", ADDON_PREFIX, value[:160], exc)
            return
        self.logger.info("Command received: %s %s", action, service)
        self._execute_command(action, service)

    # ------------------------------------------------------------------
    # Coroutines
    # ------------------------------------------------------------------

    async def get_psx_connection_coro(self) -> None:
        """Maintain the PSX connection."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)

            def connected(*_args) -> None:
                self.logger.info("PSX connected")
                self.psx_connected = True
                self.psx.send(
                    "name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")
                self.psx.send(
                    "clientName", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")

            def disconnected() -> None:
                self.logger.info("PSX disconnected")
                self.psx_connected = False

            self.psx = psx.Client()
            self.psx.logger = lambda msg: self.logger.debug("PSX: %s", msg)
            self.psx.onConnect = lambda: None
            self.psx.onDisconnect = disconnected
            self.psx.onPause = lambda: None
            self.psx.onResume = lambda: None
            self.psx.subscribe("id")
            self.psx.subscribe("version", connected)
            self.psx.subscribe("addon", self._handle_addon)

            await self.psx.connect(self.args.psx_host, self.args.psx_port)
            self.logger.warning("psx.connect() returned - this should not happen")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def check_services_coro(self) -> None:
        """Periodically re-check every service's running status."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            # First pass immediately, so a real status is available even
            # before the first --check-interval has elapsed.
            self._check_services()
            while True:
                await asyncio.sleep(self.args.check_interval)
                if self._check_services():
                    await self._broadcast_status()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def broadcast_heartbeat_coro(self) -> None:
        """Broadcast the full status every --broadcast-interval, regardless of changes."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(self.args.broadcast_interval)
                await self._broadcast_status()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def monitor_coro(self) -> None:
        """Monitor coroutines and restart them if they exit."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                running = []
                ended_tasks: set = set()
                for task in self.tasks:
                    if task.done():
                        ended_tasks.add(task)
                        exc = task.exception()
                        if exc:
                            self.logger.info("Task %s ended: %s", task.get_name(), exc)
                        else:
                            self.logger.info("Task %s ended peacefully", task.get_name())
                    else:
                        running.append(task.get_name())
                for task in ended_tasks:
                    self.tasks.discard(task)

                coros = [
                    ("PSXConnection", self.get_psx_connection_coro),
                    ("CheckServices", self.check_services_coro),
                    ("BroadcastHeartbeat", self.broadcast_heartbeat_coro),
                ]
                for name, coro_fn in coros:
                    if name not in running:
                        self.logger.info("Starting %s...", name)
                        task = self.taskgroup.create_task(coro_fn(), name=name)
                        self.tasks.add(task)

                await asyncio.sleep(5.0)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def handle_args(self) -> None:
        """Parse command-line arguments."""
        parser = argparse.ArgumentParser(
            prog=__MYNAME__,
            description=__MY_DESCRIPTION__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
        parser.add_argument(
            '--psx-host', default='127.0.0.1', metavar='HOST',
            help="PSX server or router hostname.")
        parser.add_argument(
            '--psx-port', type=int, default=10747, metavar='PORT',
            help="PSX server or router port.")
        parser.add_argument(
            '--psx-port-override', type=int, default=None, metavar='PORT',
            help="Override --psx-port with this value (a warning is printed). "
                 "Used by start_scripts to force connecting to the correct router port.")
        parser.add_argument(
            '--check-interval', type=float, default=30.0, metavar='SECONDS',
            help="How often to check each service's running status.")
        parser.add_argument(
            '--broadcast-interval', type=float, default=60.0, metavar='SECONDS',
            help="How often to broadcast full status regardless of changes "
                 "(a change always broadcasts immediately, independent of this).")
        parser.add_argument(
            '--start-scripts-dir', type=Path,
            default=Path(__file__).resolve().parent / "start_scripts", metavar='DIR',
            help="Directory containing the start_<x>.ps1/stop_<x>.ps1/restart_<x>.ps1 "
                 "scripts used to control services.")
        parser.add_argument(
            '--debug', action='store_true',
            help="Enable debug logging.")
        self.args = parser.parse_args()
        if self.args.psx_port_override is not None:
            if self.args.psx_port_override != self.args.psx_port:
                print(f"WARNING: --psx-port-override={self.args.psx_port_override} "
                      f"overrides --psx-port={self.args.psx_port}", file=sys.stderr)
            self.args.psx_port = self.args.psx_port_override

    async def run(self) -> None:
        """Parse args and run the main task group."""
        self.handle_args()
        logging.basicConfig(
            level=logging.DEBUG if self.args.debug else logging.INFO,
            format="%(asctime)s: %(message)s",
            datefmt="%H:%M:%S")
        print(f"frankencontrol version {__version__} starting")
        if not self.args.start_scripts_dir.is_dir():
            self.logger.warning(
                "start_scripts dir not found: %s -- start/stop/restart commands will fail "
                "until --start-scripts-dir points at a real one",
                self.args.start_scripts_dir)

        async with asyncio.TaskGroup() as self.taskgroup:
            task = self.taskgroup.create_task(self.monitor_coro(), name="Monitor")
            self.tasks.add(task)


if __name__ == '__main__':
    try:
        asyncio.run(Script().run())
    except KeyboardInterrupt:
        pass
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
