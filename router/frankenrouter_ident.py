r"""A script to map use window names to identify PSX clients.

# Installing Python dependencies:

- A relatively modern Python is needed (for asyncio.TaskGroup), e.g 3.13

# Make a venv

C:\fs\python\3.13.5\python.exe -m venv C:\fs\python\venv\test-1
. C:\fs\python\venv\test-1\scripts\Activate.ps1

# Install dependencies:

pip install pywin32
pip install psutil
"""
import argparse
import asyncio
import ctypes
import inspect
import json
import logging
import re
import sys
import traceback

import psutil  # pylint: disable=import-error
import win32gui  # pylint: disable=import-error
import win32process  # pylint: disable=import-error

__MYNAME__ = 'frankenrouter_ident.py'
__MY_DESCRIPTION__ = 'Identify PSX clients by their window title'

VERSION = '0.1'

PSX_SERVER_RECONNECT_DELAY = 1.0

PSX_PROTOCOL_SEPARATOR = b'\r\n'


class Script():
    """Generic FrankenTech script."""

    def __init__(self):
        """Set up the class."""
        self.args = None
        self.taskgroup = None
        self.tasks = set()
        self.logger = None
        self.psx_clients = {}
        self.psx_connection = None
        # Keep this in sync with frankenrouter.py
        self.frdp_version = 1

    def identify_clients(self):  # pylint: disable=too-many-locals,too-many-branches, too-many-statements
        """Identify PSX clients on this machine by their window name."""
        GetWindowText = ctypes.windll.user32.GetWindowTextW  # pylint: disable=invalid-name
        GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW  # pylint: disable=invalid-name
        clients = {}

        def get_hwnds_for_pid(pid):
            def callback(hwnd, hwnds):
                _, found_pid = win32process.GetWindowThreadProcessId(hwnd)
                if found_pid == pid:
                    hwnds.append(hwnd)
                return True
            hwnds = []
            win32gui.EnumWindows(callback, hwnds)
            return hwnds

        def get_window_title_by_handle(hwnd):
            length = GetWindowTextLength(hwnd)
            buff = ctypes.create_unicode_buffer(length + 1)
            GetWindowText(hwnd, buff, length + 1)
            return buff.value

        def add_client(laddr, lport, raddr, rport, name):  # pylint: disable=too-many-arguments,too-many-positional-arguments
            key = f"{laddr}:{lport}"
            clients[key] = {
                "raddr": raddr,
                "rport": rport,
                "laddr": laddr,
                "lport": lport,
                "name": name,
            }

        identified = set()
        for c in psutil.net_connections():
            try:
                remoteport = c.raddr.port
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            if remoteport != 10747:
                continue
            hwnds = get_hwnds_for_pid(c.pid)
            self.logger.info("Checking pid %s (%s:%s), hwnds is %s",
                             c.pid, c.laddr.ip, c.laddr.port, hwnds)
            for hwnd in hwnds:
                name = False
                if (c.laddr.ip, c.laddr.port) in identified:
                    # self.logger.debug("Already identified")
                    continue
                title = get_window_title_by_handle(hwnd)
                self.logger.debug("Title is %s", title)
                if re.match(r".*Precision Simulator.*", title):
                    if not re.match(r".*Instructor.*", title):
                        title = re.sub(r" \[1\] - Precision Simulator", "", title)
                        title = re.sub(r"CLIENT[0-9]* \| ", "", title)
                        name = f"PSX: {title}"
                elif re.match(r".*PSX.Bacars.*", title):
                    name = "BACARS"
                elif re.match(r".*PSX.NET.MSFS.Router.*", title):
                    name = "PSX.NET.Router"
                elif re.match(r".*vPilot.*", title):
                    name = "vPilot"
                elif re.match(r".*PSX.NET.EFB.*", title):
                    name = "PSX.NET.EFB"
                elif re.match(r".*PSX.NET.GateFinder.*", title):
                    name = "Gatefinder"
                elif re.match(r".*PSX.NET.*", title):
                    # self.logger.info("PSX.NET, really %s", title)
                    name = "PSX.NET"
                elif re.match(r".*PSX Sounds.*", title):
                    name = "PSX Sounds"
                elif re.match(r".*ACARS Printer.*", title):
                    name = "Printer"
                else:
                    self.logger.debug(
                        "Non-identified client on %s:%s: %s",
                        c.laddr.ip, c.laddr.port, title)
                if name:
                    self.logger.debug("%s:%s identified as %s", c.laddr.ip, c.laddr.port, name)
                    identified.add((c.laddr.ip, c.laddr.port))
                    add_client(c.laddr.ip, c.laddr.port, c.raddr.ip, c.raddr.port, name)
        self.logger.debug("Returning %s", clients)
        return clients

    async def identify_clients_coro(self):
        """Create mapping between local IP, local port and PSX client name."""

        def equal(a, b):
            return json.dumps(a) == json.dumps(b)

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                await asyncio.sleep(self.args.check_interval)
                psx_clients_new = self.identify_clients()
                self.logger.debug("Identified %d clients", len(self.psx_clients))
                if not equal(self.psx_clients, psx_clients_new):
                    if self.psx_connection is not None:
                        for peername, data in psx_clients_new.items():
                            self.logger.debug("Sending data for %s to router: %s", peername, data)
                            line = f"addon=FRANKENROUTER:{self.frdp_version}:CLIENTINFO:{json.dumps(data)}"  # pylint: disable=line-too-long
                            self.psx_connection['writer'].write(
                                line.encode() + PSX_PROTOCOL_SEPARATOR)
                            await self.psx_connection['writer'].drain()
                        self.psx_clients = psx_clients_new

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    async def router_connection(self):
        """Get data from PSX."""
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            try:
                reader, writer = await asyncio.open_connection(
                    self.args.psx_main_server_host,
                    self.args.psx_main_server_port,
                )
            # At least on Windows we get OSError after ~30s if the PSX
            # server is down or unreachable
            except (ConnectionRefusedError, OSError):
                self.logger.warning(
                    "Router connection refused, sleeping %.1f s before retry",
                    PSX_SERVER_RECONNECT_DELAY,
                )
                await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                self.psx_connection = None
                return
            self.psx_connection = {
                'writer': writer,
            }
            self.psx_connection['writer'].write(
                "name=IDENT:FRANKEN.PY client identifier".encode() +
                PSX_PROTOCOL_SEPARATOR)
            while True:
                # Read and discard any arriving traffic
                try:
                    await reader.readline()
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.logger.info(
                        "Router connection broke (%s), sleeping %.1f s before reconnect",
                        exc,
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                    break
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())
            self.psx_connection = None

    async def monitor_coro(self):
        """Monitor the coroutines and start/restart as needed."""
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                running = []
                tasks_ended = set()
                for task in self.tasks:
                    done = task.done()
                    if done:
                        tasks_ended.add(task)
                        exc = task.exception()
                        if exc is None:
                            self.logger.info("Task %s ended peacefully", task.get_name())
                        else:
                            self.logger.info("Task: %s has ended: %s", task.get_name(), exc)
                    else:
                        try:
                            task.result()
                        except asyncio.InvalidStateError:
                            pass
                        running.append(task.get_name())
                # Cleanup
                for task in tasks_ended:
                    self.logger.debug("Removing %s from task list", task)
                    self.tasks.discard(task)

                # Ensure the tasks are running
                name = "IdentifyClients"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.identify_clients_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                name = "RouterConnection"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.router_connection(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                self.logger.debug("Running Tasks: %s", running)
                # Sleep a while until next check
                await asyncio.sleep(5.0)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    def handle_args(self):
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog=__MYNAME__,
            description=__MY_DESCRIPTION__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument(
            '--psx-main-server-host',
            type=str, action='store', default='127.0.0.1',
            help="Hostname or IP of the PSX main server or router to connect to.",
        )
        parser.add_argument(
            '--psx-main-server-port',
            type=int, action='store', default=10747,
            help="The port of the PSX main server or router to connect to.",
        )
        parser.add_argument(
            '--check-interval',
            type=float, action='store', default=10.0,
            help="How often to look for PSX clients",
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help="Print more debug info. Probably only useful for development.",
        )
        self.args = parser.parse_args()

    async def run(self):
        """Start everything."""
        self.handle_args()

        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
            handlers=[
                logging.StreamHandler(sys.stdout),
            ],
        )
        self.logger = logging.getLogger(__MYNAME__)
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)

        if self.args.debug:
            asyncio.get_event_loop().set_debug(True)
        async with asyncio.TaskGroup() as self.taskgroup:
            task = self.taskgroup.create_task(self.monitor_coro(), name="Monitor")
            self.tasks.add(task)
            print("All tasks created")
        print("All tasks completed")


if __name__ == '__main__':
    asyncio.run(Script().run())
