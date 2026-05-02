"""A script to map use window names to identify PSX clients.

A relatively modern Python is needed (for asyncio.TaskGroup), e.g 3.13

Modules needed: pywin32 and psutil
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
__MY_DESCRIPTION__ = 'Identify PSX clients by process name or window title'

VERSION = '0.1'

PSX_SERVER_RECONNECT_DELAY = 1.0

PSX_PROTOCOL_SEPARATOR = b'\r\n'


class Script():  # pylint: disable=too-many-instance-attributes
    """Generic FrankenTech script."""

    def __init__(self):
        """Set up the class."""
        self.args = None
        self.taskgroup = None
        self.tasks = set()
        self.logger = None
        self.psx_clients = {}
        self.psx_connection = None
        self.psx_connection_is_new = False
        # Keep this in sync with frankenrouter.py
        self.frdp_version = 1

    def identify_clients(self):  # pylint: disable=too-many-locals,too-many-branches, too-many-statements
        """Identify PSX clients on this machine by process name or window title."""
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

        def add_client(laddr, lport, raddr, rport, client_provided_id, display_name):  # pylint: disable=too-many-arguments,too-many-positional-arguments
            key = f"{laddr}:{lport}"
            clients[key] = {
                "raddr": raddr,
                "rport": rport,
                "laddr": laddr,
                "lport": lport,
                "client_provided_id": client_provided_id,
                "display_name": display_name,
            }

        identified = set()
        for c in psutil.net_connections():
            try:
                remoteport = c.raddr.port
            except Exception:  # pylint: disable=broad-exception-caught
                continue
            if remoteport != self.args.psx_main_server_port:
                continue
            if (c.laddr.ip, c.laddr.port) in identified:
                continue

            ident = None

            try:
                proc_name = psutil.Process(c.pid).name()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                proc_name = ""

            if re.match(r"CockpitSimulator", proc_name):
                ident = ("CSBRIDGE", "Cockpit Simulator Bridge")

            if not ident:
                hwnds = get_hwnds_for_pid(c.pid)
                self.logger.info("Checking pid %s (%s:%s), hwnds is %s",
                                 c.pid, c.laddr.ip, c.laddr.port, hwnds)
                for hwnd in hwnds:
                    title = get_window_title_by_handle(hwnd)
                    self.logger.debug("Title is %s", title)
                    if re.match(r".*PSX.NET.GateFinder.*", title):
                        ident = ("GATEFIND", "PSX.NET GateFinder")
                    elif re.match(r".*ACARS Printer.*", title):
                        ident = ("PRINTER", "AcarsPrint App for thermal printers")
                    elif re.match(r"^PSX.NET$", title):
                        ident = ("PSXNET", "PSX.NET")
                    else:
                        self.logger.debug(
                            "Non-identified client on %s:%s: %s",
                            c.laddr.ip, c.laddr.port, title)
                    if ident:
                        break

            if ident:
                client_provided_id, display_name = ident
                self.logger.debug("%s:%s identified as %s (%s)",
                                  c.laddr.ip, c.laddr.port, client_provided_id, display_name)
                identified.add((c.laddr.ip, c.laddr.port))
                add_client(c.laddr.ip, c.laddr.port, c.raddr.ip, c.raddr.port,
                           client_provided_id, display_name)
        self.logger.debug("Returning %s", clients)
        return clients

    async def identify_clients_coro(self):
        """Create mapping between local IP, local port and PSX client name."""
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                await asyncio.sleep(self.args.check_interval)
                psx_clients_new = self.identify_clients()
                self.logger.debug("Identified %d clients", len(psx_clients_new))

                if self.psx_connection is not None:
                    if self.psx_connection_is_new:
                        # Fresh connection: re-send all currently known clients
                        to_send = psx_clients_new
                        self.psx_connection_is_new = False
                    else:
                        # Normal: only send new or changed entries
                        to_send = {k: v for k, v in psx_clients_new.items()
                                   if k not in self.psx_clients or self.psx_clients[k] != v}
                    for peername, data in to_send.items():
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
            self.psx_connection_is_new = True
            self.psx_connection['writer'].write(
                "name=IDENT:FRANKEN.PY client identifier".encode() +
                PSX_PROTOCOL_SEPARATOR)
            while True:
                # Read and discard any arriving traffic
                try:
                    data = await reader.readline()
                    if not data:
                        self.logger.info(
                            "Router connection closed, sleeping %.1f s before reconnect",
                            PSX_SERVER_RECONNECT_DELAY,
                        )
                        await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                        break
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.logger.info(
                        "Router connection broke (%s), sleeping %.1f s before reconnect",
                        exc,
                        PSX_SERVER_RECONNECT_DELAY,
                    )
                    await asyncio.sleep(PSX_SERVER_RECONNECT_DELAY)
                    break
            self.psx_connection = None
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
            asyncio.get_event_loop().set_debug(True)
        async with asyncio.TaskGroup() as self.taskgroup:
            task = self.taskgroup.create_task(self.monitor_coro(), name="Monitor")
            self.tasks.add(task)
            print("All tasks created")
        print("All tasks completed")


if __name__ == '__main__':
    asyncio.run(Script().run())
