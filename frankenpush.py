# pylint: disable=invalid-name
"""FrankenPush - PSCC Flight Centre push connector.

Connects to a local PSX main server (or frankenrouter), reads live flight
data, and streams it to a PSCC Flight Centre portal over an authenticated
WebSocket connection.  No inbound port forwarding is needed — the connection
is always initiated outward from your machine to the portal.

Setup:
  1. Register a "My sim" on the portal (your portal URL)/mysim
  2. Copy the logon key shown on the sim's detail page
  3. Run this addon:
       python frankenpush.py --portal-url https://your-portal/path --logon-key <key>
     or just:
       python frankenpush.py
     and enter the values when prompted.
"""

import argparse
import asyncio
import inspect
import logging
import math
import sys
import traceback

import aiohttp

import psx

__MYNAME__ = 'frankenpush'
__MY_CLIENT_ID__ = 'PUSH'
__MY_DISPLAY_NAME__ = 'FrankenPush'
__MY_DESCRIPTION__ = 'PSCC Flight Centre push connector'

# Matches the portal's WS broadcast rate (web/ws.py _BROADCAST_INTERVAL).
_SEND_INTERVAL = 2.0


class Script():  # pylint: disable=too-many-instance-attributes
    """FrankenPush script."""

    def __init__(self):
        """Initialize script state."""
        self.args = None
        self.taskgroup = None
        self.tasks = set()
        self.logger = None
        self.psx = None
        self.psx_connected = False

        # Latest PSX state — updated by psx callbacks, read by push loop.
        # All None until PSX sends the first value.
        self._lat = None
        self._lon = None
        self._alt_ft = None
        self._heading_deg = None
        self._tas_kt = None
        self._pitch_deg = None
        self._bank_deg = None
        self._tail_number = None
        self._flight_number = None
        self._route_mode = None
        self._route1 = None
        self._route2 = None
        self._eta = None

    # --- PSX callbacks ---

    def _on_position(self, _key, value):
        """Parse PiBaHeAlTas (pitch;bank;heading_rad;alt_ft*1e3;tas_kt*1e3;lat_rad;lon_rad)."""
        try:
            parts = value.split(';')
            if len(parts) != 7:
                return
            pitch_mrad, bank_mrad, heading_rad, alt_x1000, tas_x1000, lat_rad, lon_rad = (
                float(p) for p in parts)
            self._pitch_deg = math.degrees(pitch_mrad / 1_000_000)
            self._bank_deg = math.degrees(bank_mrad / 1_000_000)
            self._heading_deg = math.degrees(heading_rad) % 360
            self._alt_ft = alt_x1000 / 1000
            self._tas_kt = tas_x1000 / 1000
            self._lat = math.degrees(lat_rad)
            self._lon = math.degrees(lon_rad)
        except (ValueError, IndexError):
            pass

    def _on_tail_number(self, _key, value):
        self._tail_number = value.strip().upper() or None

    def _on_flight_number(self, _key, value):
        self._flight_number = value or None

    def _on_route_mode(self, _key, value):
        self._route_mode = value or None

    def _on_route1(self, _key, value):
        self._route1 = value or None

    def _on_route2(self, _key, value):
        self._route2 = value or None

    def _on_eta(self, _key, value):
        self._eta = value or None

    # --- helpers ---

    def _build_update(self):
        return {
            "tail_number": self._tail_number,
            "lat": self._lat,
            "lon": self._lon,
            "alt_ft": self._alt_ft,
            "heading_deg": self._heading_deg,
            "tas_kt": self._tas_kt,
            "pitch_deg": self._pitch_deg,
            "bank_deg": self._bank_deg,
            "flight_number": self._flight_number,
            "route_mode": self._route_mode,
            "route1": self._route1,
            "route2": self._route2,
            "eta": self._eta,
        }

    # --- coroutines ---

    async def get_psx_connection_coro(self):
        """Maintain a connection to the PSX main server."""
        def connected():
            self.logger.info("PSX CONNECTED")
            self.psx_connected = True
            self.psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__}")

        def disconnected():
            self.logger.info("PSX DISCONNECTED")
            self.psx_connected = False
            self._lat = None  # clear position so stale data isn't sent

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            self.psx = psx.Client()
            self.psx.onConnect = connected
            self.psx.onDisconnect = disconnected
            self.psx.onPause = lambda: None
            self.psx.onResume = lambda: None

            # PSX lexicon names (confirmed from session captures):
            #   Qs0 = CfgRego
            #   PiBaHeAlTas = Qs121, FmcFltNo = Qs401,
            #   FmcRteViAcMo = Qs373, FmcRte1 = Qs376, FmcRte2 = Qs377,
            #   ActDestEta = Qi247
            self.psx.subscribe("CfgRego", self._on_tail_number)
            self.psx.subscribe("PiBaHeAlTas", self._on_position)
            self.psx.subscribe("FmcFltNo", self._on_flight_number)
            self.psx.subscribe("FmcRteViAcMo", self._on_route_mode)
            self.psx.subscribe("FmcRte1", self._on_route1)
            self.psx.subscribe("FmcRte2", self._on_route2)
            self.psx.subscribe("ActDestEta", self._on_eta)

            self.psx.logger = self.logger.debug

            await self.psx.connect(self.args.psx_host, self.args.psx_port)
            self.logger.warning("psx.connect() returned unexpectedly")

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    async def push_loop_coro(self):
        """Maintain a WebSocket connection to the portal and send position updates."""
        ws_url = self.args.portal_url.rstrip('/') + '/ws/push'
        headers = {"Authorization": f"Bearer {self.args.logon_key}"}
        backoff = 2.0

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                try:
                    async with aiohttp.ClientSession() as session:
                        self.logger.info("Connecting to portal at %s ...", ws_url)
                        async with session.ws_connect(
                                ws_url, headers=headers, heartbeat=30) as ws:
                            self.logger.info("Connected to portal")
                            backoff = 2.0  # reset on successful connection

                            async def _send():
                                while True:
                                    if self.psx_connected and self._lat is not None:
                                        await ws.send_json(self._build_update())
                                        self.logger.debug("Sent position update")
                                    await asyncio.sleep(_SEND_INTERVAL)

                            async def _drain():
                                # Discard server messages; exits when server
                                # sends CLOSE (e.g. on auth rejection) or the
                                # connection drops.
                                async for _ in ws:
                                    pass

                            send_task = asyncio.ensure_future(_send())
                            drain_task = asyncio.ensure_future(_drain())
                            try:
                                await asyncio.wait(
                                    [send_task, drain_task],
                                    return_when=asyncio.FIRST_COMPLETED)
                                close_code = ws.close_code
                                if close_code == 4001:
                                    self.logger.error(
                                        "Portal rejected logon key — "
                                        "check the key shown on your My sim page")
                                    backoff = 30.0  # long pause on auth failure
                                elif close_code is not None:
                                    self.logger.info(
                                        "Portal closed connection (code %s)", close_code)
                                else:
                                    self.logger.info("Portal connection closed")
                            finally:
                                send_task.cancel()
                                drain_task.cancel()
                                await asyncio.gather(send_task, drain_task,
                                                     return_exceptions=True)

                except aiohttp.ClientResponseError as exc:
                    self.logger.warning("Portal HTTP error %s: %s", exc.status, exc.message)
                except (aiohttp.ClientError, OSError) as exc:
                    self.logger.warning("Portal connection failed: %s", exc)
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    self.logger.warning("Push loop error: %s", exc)

                self.logger.info("Retrying portal connection in %.0f s ...", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

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
                            self.logger.info("Task %s ended: %s", task.get_name(), exc)
                    else:
                        running.append(task.get_name())
                for task in tasks_ended:
                    self.tasks.discard(task)

                for name, coro_fn in [
                    ("PSXConnection", self.get_psx_connection_coro),
                    ("PushLoop", self.push_loop_coro),
                ]:
                    if name not in running:
                        self.logger.info("Starting %s ...", name)
                        task = self.taskgroup.create_task(coro_fn(), name=name)
                        self.tasks.add(task)
                        self.logger.info("Started %s.", name)

                self.logger.debug("Running tasks: %s", running)
                await asyncio.sleep(5.0)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    def handle_args(self):
        """Parse command-line arguments, prompting for missing required values."""
        parser = argparse.ArgumentParser(
            prog=__MYNAME__,
            description=__MY_DESCRIPTION__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument(
            '--psx-host',
            type=str, action='store', default='127.0.0.1',
            help="Hostname or IP of the PSX main server or router to connect to.",
        )
        parser.add_argument(
            '--psx-port',
            type=int, action='store', default=10747,
            help="Port of the PSX main server or router.",
        )
        parser.add_argument(
            '--portal-url',
            type=str, action='store', default='https://mkro.se/flightcentre',
            help="PSCC Flight Centre portal URL.",
        )
        parser.add_argument(
            '--logon-key',
            type=str, action='store', default=None,
            help="Logon key from the 'My sim' page on the portal.",
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help="Print more debug info.",
        )
        self.args = parser.parse_args()

        if not self.args.logon_key:
            print("Enter the logon key shown on the 'My sim' page of the portal.")
            self.args.logon_key = input("Logon key: ").strip()

    async def run(self):
        """Start everything."""
        self.handle_args()

        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
        self.logger = logging.getLogger(__MYNAME__)
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)
            asyncio.get_event_loop().set_debug(True)

        print(f"Connecting to PSX at {self.args.psx_host}:{self.args.psx_port}")
        print(f"Pushing to portal at {self.args.portal_url.rstrip('/')}/ws/push")
        print("Press Ctrl+C to stop.")

        async with asyncio.TaskGroup() as self.taskgroup:
            task = self.taskgroup.create_task(self.monitor_coro(), name="Monitor")
            self.tasks.add(task)
        print("All tasks completed.")


if __name__ == '__main__':
    try:
        asyncio.run(Script().run())
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            input("An error occurred, press Enter to continue...")
