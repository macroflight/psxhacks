"""A script to check if the PSX and MSFS altitudes match."""
import argparse
import asyncio
import inspect
import logging
import pathlib
import sys
import time
import traceback
import winsound  # pylint: disable=import-error

import SimConnect  # pylint: disable=import-error
import psx

__MYNAME__ = 'psx_msfs_sync_checker'
__MY_CLIENT_ID__ = 'SYNCCHK'
__MY_DISPLAY_NAME__ = 'PSX-MSFS sync check'
__MY_DESCRIPTION__ = 'Verify PSX and MSFS are in sync'


class Script():  # pylint: disable=too-many-instance-attributes
    """Generic FrankenTech script."""

    def __init__(self):
        """Set up the class."""
        self.args = None
        self.taskgroup = None
        self.tasks = set()
        self.logger = None
        self.psx = None
        self.psx_connected = False
        self.psx_avail = False
        self.psx_subscribe = []

        self.msfs_sm = None
        self.msfs_aq = None

        # Sim data
        self.psx_updated = 0.0
        self.psx_altimeter_std = False
        self.psx_altitude_std = None
        self.psx_altitude_qnh = None
        self.psx_transition_level = None
        self.psx_transition_altitude = None
        self.psx_zone_qnh_hpa = None

        self.msfs_updated = 0.0
        self.msfs_indicated_altitude = None
        self.msfs_indicated_altitude_calibrated = None
        self.msfs_sea_level_pressure = None

        self.in_error = False

    async def compare_coro(self):  # pylint: disable=too-many-branches
        """Compare sim data and warn if mismatch."""
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            last_altitude_diff = 0.0

            while True:
                await asyncio.sleep(1.0)

                # Verify we have the data we need
                if self.psx_altitude_std is None:
                    continue
                if self.psx_altitude_qnh is None:
                    continue
                if self.msfs_indicated_altitude is None:
                    continue
                if self.msfs_indicated_altitude_calibrated is None:
                    continue

                if self.psx_altimeter_std:
                    psx_altitude = self.psx_altitude_std
                    msfs_altitude = self.msfs_indicated_altitude
                else:
                    psx_altitude = self.psx_altitude_qnh
                    msfs_altitude = self.msfs_indicated_altitude_calibrated

                altitude_diff = abs(psx_altitude - msfs_altitude)
                diff_rate = altitude_diff - last_altitude_diff
                last_altitude_diff = altitude_diff

                in_error = False

                psx_data_age = time.perf_counter() - self.psx_updated
                if psx_data_age > 10.0:
                    self.logger.warning("WARNING: PSX data last updated more than 10s ago")
                    in_error = True
                msfs_data_age = time.perf_counter() - self.msfs_updated
                if msfs_data_age > 10.0:
                    self.logger.warning("WARNING: MSFS data last updated more than 10s ago")
                    in_error = True
                self.logger.debug(
                    "Ages: %.1f (%.1f) // %.1f (%.1f) ",
                    psx_data_age, self.psx_updated, msfs_data_age, self.msfs_updated)

                if altitude_diff > self.args.max_altitude_diff:
                    in_error = True
                    self.logger.warning(
                        "WARNING: altitude diff is %.0f feet (change=%.1f). PSX==%.0f (%s), MSFS==%.0f, PSX TA/TL is %d/%d. PSX QNH is %.0f, MSFS QNH is %.0f",  # pylint: disable=line-too-long
                        altitude_diff, diff_rate,
                        psx_altitude,
                        "STD" if self.psx_altimeter_std else "QNH",
                        msfs_altitude,
                        self.psx_transition_altitude, self.psx_transition_level,
                        self.psx_zone_qnh_hpa,
                        self.msfs_sea_level_pressure,
                    )
                else:
                    in_error = False
                    self.logger.info(
                        "OK: altitude diff is %.0f feet (change=%.1f). PSX==%.0f (%s), MSFS==%.0f, PSX TA/TL is %d/%d. PSX QNH is %.0f, MSFS QNH is %.0f",  # pylint: disable=line-too-long
                        altitude_diff, diff_rate, psx_altitude,
                        "STD" if self.psx_altimeter_std else "QNH",
                        msfs_altitude,
                        self.psx_transition_altitude, self.psx_transition_level,
                        self.psx_zone_qnh_hpa,
                        self.msfs_sea_level_pressure,
                    )
                # Play sound on state change
                if in_error != self.in_error:
                    # state change
                    if in_error:
                        if self.args.beep:
                            winsound.Beep(550, 3000)
                    else:
                        if self.args.beep:
                            winsound.Beep(1100, 3000)
                self.in_error = in_error

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    async def get_msfs_data_coro(self):
        """Get data from MSFS using SimConnect."""
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                await asyncio.sleep(self.args.fetch_interval)

                if self.msfs_sm is None:
                    try:
                        self.logger.info("Setting up SimConnect")
                        self.msfs_sm = SimConnect.SimConnect()
                    except ConnectionError:
                        self.logger.debug("Could not connect to MSFS, retrying later")
                        continue
                    # Note the default _time is 2000 to be refreshed every 2 seconds
                    self.msfs_aq = SimConnect.AircraftRequests(self.msfs_sm, _time=1000)
                    self.logger.info("Started SimConnect connection")

                value = self.msfs_aq.get("INDICATED_ALTITUDE")
                if value is None:
                    self.logger.debug("Got no INDICATED_ALTITUDE from SimConnect")
                else:
                    self.msfs_indicated_altitude = float(value)
                    self.logger.debug("Got INDICATED_ALTITUDE from MSFS: %.0f",
                                      self.msfs_indicated_altitude)

                value = self.msfs_aq.get("INDICATED_ALTITUDE_CALIBRATED")
                if value is None:
                    self.logger.debug("Got no INDICATED_ALTITUDE_CALIBRATED from SimConnect")
                else:
                    self.msfs_indicated_altitude_calibrated = float(value)
                    self.logger.debug("Got INDICATED_ALTITUDE_CALIBRATED from MSFS: %.0f",
                                      self.msfs_indicated_altitude_calibrated)

                value = self.msfs_aq.get("SEA_LEVEL_PRESSURE")
                if value is None:
                    self.logger.debug("Got no SEA_LEVEL_PRESSURE from SimConnect")
                else:
                    self.msfs_sea_level_pressure = float(value)
                    self.logger.debug("Got SEA_LEVEL_PRESSURE from MSFS: %.0f",
                                      self.msfs_sea_level_pressure)

                self.msfs_updated = time.perf_counter()

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    async def get_psx_connection_coro(self):
        """Maintain a PSX connection."""
        def connected(*_):
            """Run when connected to PSX."""
            self.logger.info("PSX CONNECTED")
            self.psx_connected = True
            self.psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__}")

        def disconnected():
            """Run when we are disconnected from PSX."""
            self.logger.info("PSX DISCONNECTED")
            self.psx_connected = False
            self.psx_avail = False

        def onresume():
            """Run when load3 is seen, i.e when we have a full set of variables."""
            self.logger.info("PSX RESUMED")
            self.psx.send("demand", "LeftPfdAlt")
            self.psx_connected = True
            self.psx_avail = True

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            self.psx = psx.Client()

            self.psx.onPause = lambda: None
            self.psx.onDisconnect = disconnected
            self.psx.onConnect = lambda: None
            self.psx.onResume = onresume

            self.psx.subscribe("LeftPfdAlt")
            self.psx.subscribe("FmcVnavX")

            self.psx.subscribe("WxBasic")
            self.psx.subscribe("Wx1")
            self.psx.subscribe("Wx2")
            self.psx.subscribe("Wx3")
            self.psx.subscribe("Wx4")
            self.psx.subscribe("Wx5")
            self.psx.subscribe("Wx6")
            self.psx.subscribe("Wx7")
            self.psx.subscribe("FocussedWxZone")

            self.psx.subscribe("id")
            self.psx.subscribe("version", connected)

            self.psx.logger = self.logger.debug

            await self.psx.connect(
                self.args.psx_main_server_host,
                self.args.psx_main_server_port)
            self.logger.warning("psx.connect() returned, this should not happen")

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    async def get_psx_data_coro(self):
        """Get data from PSX."""
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)

            while True:
                await asyncio.sleep(self.args.fetch_interval)
                if not self.psx_avail or not self.psx_connected:
                    self.logger.debug(
                        "Waiting for PSX data to become available (%s/%s)",
                        self.psx_avail, self.psx_connected
                    )
                    continue

                # Get PSX QNH in focused zone
                focuszone = self.psx.get("FocussedWxZone")
                self.logger.debug("PSX focused zone: %s", focuszone)
                zonename = "WxBasic"
                if int(focuszone) > 0:
                    zonename = f"Wx{focuszone}"
                psx_zone_weather = self.psx.get(zonename)
                self.logger.debug("PSX wx in zone %s: %s", zonename, psx_zone_weather)
                # convert INHG to IN,HG and then to HPA
                self.psx_zone_qnh_hpa = 33.865 * (int(psx_zone_weather.split(";")[23]) / 100)
                self.logger.debug("PSX QNH is %s", self.psx_zone_qnh_hpa)

                # Get PSX altitude data
                value = self.psx.get("LeftPfdAlt")
                if value.startswith("##"):
                    self.logger.warning("Got invalid LeftPfdAlt data from PSX")
                    continue
                self.logger.debug("Got %s from PSX", value)
                mode = value[:1]
                if mode == "s":
                    self.psx_altimeter_std = True
                else:
                    self.psx_altimeter_std = False
                (alt_qnh, alt_std, _) = value[1:].split(';')
                self.psx_altitude_std = float(alt_std)
                self.psx_altitude_qnh = float(alt_qnh)
                self.logger.debug(
                    "Got altitudes from PSX: %.1f STD and %.1f QNH",
                    self.psx_altitude_std, self.psx_altitude_qnh)

                # Get PSX TA/TL
                value = self.psx.get("FmcVnavX")
                (_, psx_ta, psx_tl, _) = value.split(';', 3)
                self.psx_transition_altitude = int(psx_ta)
                self.psx_transition_level = int(psx_tl)
                self.logger.debug(
                    "Got FMC data from PSX: TA is %d and TL is %d",
                    self.psx_transition_altitude, self.psx_transition_level)
                self.psx_updated = time.perf_counter()

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down",
                                 exc, inspect.currentframe().f_code.co_name)
            self.logger.critical(traceback.format_exc())

    async def monitor_coro(self):  # pylint: disable=too-many-branches, too-many-statements
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
                name = "PSXFetcher"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.get_psx_data_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                name = "PSXConnection"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.get_psx_connection_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                name = "PSXFetcher"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.get_psx_data_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                name = "MSFSFetcher"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.get_msfs_data_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                name = "Comparator"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.compare_coro(), name=name)
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
            '--fetch-interval',
            type=float, action='store', default=1.0,
            help="How often to poll PSX and MSFS",
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help="Print more debug info. Probably only useful for development.",
        )
        parser.add_argument(
            '--beep',
            action='store_true',
            help="Make sound when altitude diff is too great.",
        )
        parser.add_argument(
            '--max-altitude-diff',
            action='store', type=float, default=300.0,
            help="Warn when the PSX and MSFS altitudes differ by more than this",
        )
        parser.add_argument(
            '--log-file',
            action='store', type=pathlib.Path, default=__MYNAME__ + ".log",
            help="Log output to this file",
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
                logging.FileHandler(self.args.log_file),
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
