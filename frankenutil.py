# pylint: disable=invalid-name
"""FrankenUtil - miscellaneous PSX utilities."""

import argparse
import asyncio
import inspect
import logging
import math
import sys
import traceback

from pyproj import Geod

import psx

__MYNAME__ = 'frankenutil'
__MY_CLIENT_ID__ = 'UTIL'
__MY_DISPLAY_NAME__ = 'FrankenUtil'
__MY_DESCRIPTION__ = 'Miscellaneous PSX utilities'


class Script():  # pylint: disable=too-many-instance-attributes
    """FrankenUtil script."""

    def __init__(self):
        """Set up the class."""
        self.active_mcdus = []

        self.args = None
        self.taskgroup = None
        self.tasks = set()
        self.logger = None
        self.psx = None
        self.psx_connected = False
        self.psx_paused = False

        self.mcduL = None
        self.mcduR = None
        self.mcduC = None

        self.pending_paint_tasks = {}
        self.repaint_req_by = set()
        self.mcdu_page = "main"

        self.geod = Geod(ellps="WGS84")

        self.slew_enabled = True
        self.tow_enabled = True
        self.scratchpad_text = ''

    def psx_send_and_set(self, psx_variable, new_psx_value):
        """Send variable to PSX and store in local db."""
        self.psx.send(psx_variable, new_psx_value)
        self.psx._set(psx_variable, new_psx_value)  # pylint: disable=protected-access

    def towing_var_changed(self, _key, _value):
        """Repaint towing page when a towing-related PSX variable changes."""
        if self.mcdu_page == "towing":
            self.repaint_req_by.add("towing-var-changed")
            asyncio.create_task(self.repaint_all_mcdus())

    async def slew_monitor_coro(self):
        """Disable slew when ground speed exceeds 5 kt."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(10.0)
                if not self.psx_connected or self.psx_paused:
                    continue
                self.psx.send("demand", "GroundSpeed")
                await asyncio.sleep(0.5)
                raw = self.psx.get("GroundSpeed")
                if raw is None:
                    continue
                gs = float(raw)
                was_slew = self.slew_enabled
                was_tow = self.tow_enabled
                self.slew_enabled = gs <= 5.0
                self.tow_enabled = gs <= 20.0
                self.logger.debug(
                    "GroundSpeed %.1f kt, slew_enabled=%s tow_enabled=%s",
                    gs, self.slew_enabled, self.tow_enabled)
                if self.slew_enabled != was_slew or self.tow_enabled != was_tow:
                    self.repaint_req_by.add("speed-change")
                    asyncio.create_task(self.repaint_all_mcdus())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, myname)
            self.logger.critical(traceback.format_exc())

    def do_slew(self, direction, amount):
        """Slew the aircraft by amount metres (move) or degrees (rotate)."""
        if not self.slew_enabled:
            self.logger.info("Slew blocked: ground speed > 5 kt")
            return
        pos = self.psx.get('PiBaHeAlTas')
        if pos is None:
            self.logger.warning("PiBaHeAlTas not available, cannot slew")
            return
        pos_elems = pos.split(';', 6)
        heading_r = float(pos_elems[2])
        lat = float(pos_elems[5])
        lon = float(pos_elems[6])
        if direction == 'FORWARD':
            lon, lat, _ = self.geod.fwd(lons=lon, lats=lat, az=heading_r, dist=amount,
                                        radians=True)
        elif direction == 'BACKWARD':
            lon, lat, _ = self.geod.fwd(lons=lon, lats=lat, az=heading_r, dist=-amount,
                                        radians=True)
        elif direction == 'LEFT':
            lon, lat, _ = self.geod.fwd(lons=lon, lats=lat, az=heading_r - math.pi / 2,
                                        dist=amount, radians=True)
        elif direction == 'RIGHT':
            lon, lat, _ = self.geod.fwd(lons=lon, lats=lat, az=heading_r + math.pi / 2,
                                        dist=amount, radians=True)
        elif direction == 'NOSELEFT':
            heading_r -= math.radians(amount)
        elif direction == 'NOSERIGHT':
            heading_r += math.radians(amount)
        heading_mrad = int(1000 * heading_r)
        self.logger.info("Slew %s %.1f", direction, amount)
        self.psx_send_and_set('StartPiBaHeAlVsTasYw',
                              f'1;0;0;{heading_mrad};0;0;0;0;{lat};{lon};0')

    def do_ground(self):
        """Lower aircraft elevation by 1000 units if not already at ground level."""
        raw = self.psx.get('Elev')
        if raw is None:
            self.logger.warning("Elev not available, cannot lower")
            return
        try:
            elev = int(raw)
        except ValueError:
            self.logger.warning("Cannot parse Elev: %s", raw)
            return
        if elev <= -100000:
            self.logger.info("Elev already at ground level (%d)", elev)
            return
        new_elev = elev - 1000
        self.logger.info("Ground: Elev %d -> %d, Qi198=%d",
                         elev, new_elev, self.args.ground_force_value)
        self.psx_send_and_set('Elev', str(new_elev))
        self.psx_send_and_set('Qi198', str(self.args.ground_force_value))

    def do_towing_start(self):
        """Start towing (set mode to 20)."""
        if self.psx.get('ParkBrkLev') != "1":
            self.logger.warning("Towing start blocked: parking brake not set")
            return
        current = self.psx.get('Towing')
        if current is None or len(current) < 3:
            self.logger.warning("Towing variable not available")
            return
        new_towing = current[0] + "98" + current[3:]
        self.logger.info("Towing start: %s -> %s", current, new_towing)
        self.psx_send_and_set('Towing', new_towing)

    def do_towing_stop(self):
        """Stop towing (set mode to 98)."""
        current = self.psx.get('Towing')
        if current is None or len(current) < 3:
            self.logger.warning("Towing variable not available")
            return
        new_towing = current[0] + "20" + current[3:]
        self.logger.info("Towing stop: %s -> %s", current, new_towing)
        self.psx_send_and_set('Towing', new_towing)

    def do_towing_direction(self):
        """Toggle towing direction between forward and backward."""
        current = self.psx.get('Towing')
        if current is None or len(current) < 1:
            self.logger.warning("Towing variable not available")
            return
        new_dir = "1" if current[0] != "1" else "2"
        new_towing = new_dir + current[1:]
        self.logger.info("Towing direction: %s -> %s", current, new_towing)
        self.psx_send_and_set('Towing', new_towing)

    async def repaint_all_mcdus(self):
        """Trigger a repaint of all MCDUs, cancelling any pending paint tasks first."""
        self.logger.debug("Refreshing all active MCDUs, requested by: %s", self.repaint_req_by)
        for mcdu in self.active_mcdus:
            existing = self.pending_paint_tasks.get(mcdu)
            if existing and not existing.done():
                existing.cancel()
            if self.mcdu_page == "slew":
                self.pending_paint_tasks[mcdu] = asyncio.create_task(self.paintSlewPage(mcdu))
            elif self.mcdu_page == "towing":
                self.pending_paint_tasks[mcdu] = asyncio.create_task(self.paintTowingPage(mcdu))
            else:
                self.pending_paint_tasks[mcdu] = asyncio.create_task(self.paintMainPage(mcdu))
        self.repaint_req_by = set()

    def mcduEvent(self, mcdu, event_type, value=None):  # pylint: disable=too-many-branches,too-many-statements
        """Handle MCDU events."""
        self.logger.debug("MCDU event from %s: %s=%s", mcdu.location, event_type, value)
        if event_type in ["logon", "resume"]:
            self.mcdu_page = "main"
            asyncio.create_task(self.repaint_all_mcdus())
        elif event_type == "keypress":
            self.repaint_req_by = set()
            if self.mcdu_page == "main":
                if value == "1L":
                    self.mcdu_page = "slew"
                    self.repaint_req_by.add("slew-nav-press")
                elif value == "2L":
                    self.mcdu_page = "towing"
                    self.repaint_req_by.add("towing-nav-press")
                elif value == "3L":
                    self.logger.info("Resetting printer (Qi115=1)")
                    self.psx.send("Qi115", "1")
                elif value == "4L":
                    self.do_ground()
            elif self.mcdu_page == "slew":
                if value == "1L":
                    self.do_slew('NOSELEFT', 1)
                elif value == "2L":
                    self.do_slew('NOSELEFT', 5)
                elif value == "1R":
                    self.do_slew('NOSERIGHT', 1)
                elif value == "2R":
                    self.do_slew('NOSERIGHT', 5)
                elif value == "3L":
                    self.do_slew('FORWARD', 1)
                elif value == "4L":
                    self.do_slew('LEFT', 1)
                elif value == "3R":
                    self.do_slew('BACKWARD', 1)
                elif value == "4R":
                    self.do_slew('RIGHT', 1)
                elif value == "6L":
                    self.mcdu_page = "main"
                    self.repaint_req_by.add("slew-back-press")
            elif self.mcdu_page == "towing":
                if value == "CLR":
                    self.scratchpad_text = ''
                    mcdu.paint(13, 0, "large", "white", " " * 24)
                elif value in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
                    if len(self.scratchpad_text) < 3:
                        self.scratchpad_text += value
                        mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)
                elif value == "6L":
                    self.mcdu_page = "main"
                    self.scratchpad_text = ''
                    self.repaint_req_by.add("towing-back-press")
                elif not self.tow_enabled:
                    pass
                elif value == "1L":
                    self.do_towing_start()
                    self.repaint_req_by.add("towing-start")
                elif value == "1R":
                    self.do_towing_stop()
                    self.repaint_req_by.add("towing-stop")
                elif value == "2L":
                    self.do_towing_direction()
                    self.repaint_req_by.add("towing-direction")
                elif value == "3L":
                    towing = self.psx.get('Towing')
                    if self.scratchpad_text and towing and len(towing) >= 6:
                        try:
                            hdg = int(self.scratchpad_text) % 360
                            self.psx_send_and_set('Towing', towing[:3] + f"{hdg:03d}")
                            self.scratchpad_text = ''
                            mcdu.paint(13, 0, "large", "white", " " * 24)
                            self.repaint_req_by.add("towing-hdg-set")
                        except ValueError:
                            pass
                elif value == "3R":
                    if self.scratchpad_text:
                        try:
                            radius = int(self.scratchpad_text)
                            self.psx_send_and_set('TowTurnRadius', str(radius))
                            self.scratchpad_text = ''
                            mcdu.paint(13, 0, "large", "white", " " * 24)
                            self.repaint_req_by.add("towing-radius-set")
                        except ValueError:
                            pass
            if self.repaint_req_by:
                asyncio.create_task(self.repaint_all_mcdus())
        else:
            self.logger.debug(
                "Unhandled MCDU event from %s: %s=%s", mcdu.location, event_type, value)

    async def paintMainPage(self, mcdu):
        """Paint the FTECH UTILS main menu page."""
        await asyncio.sleep(0.5)
        A = "amber"
        C = "cyan"
        L = "large"
        S = "small"
        mcdu.clear()
        #                      123456789012345678901234
        mcdu.paint(0, 0, S, A, "      FTECH UTILS       ")
        mcdu.paint(2, 0, L, C, "<SLEW                   ")
        mcdu.paint(4, 0, L, C, "<TOWING                 ")
        mcdu.paint(6, 0, L, C, "<RST PRINT              ")
        mcdu.paint(8, 0, L, C, "<GROUND                 ")

    async def paintSlewPage(self, mcdu):
        """Paint the SLEW menu page."""
        await asyncio.sleep(0.5)
        A = "amber"
        C = "cyan"
        L = "large"
        S = "small"
        mcdu.clear()
        #                     123456789012345678901234
        if self.slew_enabled:
            mcdu.paint(0, 0, S, A, "          SLEW          ")
        else:
            mcdu.paint(0, 0, S, "red", "     SLEW LOCKED        ")
        mcdu.paint(2, 0, L, C, "<NOSE L1        NOSE R1>")
        mcdu.paint(4, 0, L, C, "<NOSE L5        NOSE R5>")
        mcdu.paint(6, 0, L, C, "<FORW 1          BACK 1>")
        mcdu.paint(8, 0, L, C, "<LEFT 1         RIGHT 1>")
        mcdu.paint(12, 0, L, C, "<BACK                   ")

    async def paintTowingPage(self, mcdu):  # pylint: disable=too-many-branches
        """Paint the TOWING menu page."""
        await asyncio.sleep(0.5)
        A = "amber"
        C = "cyan"
        L = "large"
        S = "small"
        towing = self.psx.get('Towing')
        if towing is None or len(towing) < 6:
            title = "       TOWING N/A       "
            hdg_str = "---"
        else:
            direction = "PUSH" if towing[0] == "1" else "PULL"
            mode = towing[1:3]
            if mode == "10":
                status = "STOPPED"
            elif mode == "15":
                status = "STOPPED"
            elif mode == "20":
                status = "STOPPING"
            elif mode == "98":
                status = "STARTING"
            elif mode == "97":
                status = "STARTED"
            else:
                status = "UNKN"
            title = f"TOW MODE {direction}: {status}"
            try:
                hdg_str = f"{int(towing[3:6]):03d}"
            except ValueError:
                hdg_str = "???"
        radius_raw = self.psx.get('TowTurnRadius')
        if radius_raw is None:
            radius_str = "---"
        else:
            try:
                radius_str = f"{int(radius_raw):3d}"
            except ValueError:
                radius_str = "???"
        mcdu.clear()
        #                      123456789012345678901234
        if self.tow_enabled:
            mcdu.paint(0, 0, S, A, title)
        else:
            mcdu.paint(0, 0, S, "red", "    TOWING LOCKED       ")
        mcdu.paint(2, 0, L, C, "<START            STOP>")
        mcdu.paint(4, 0, L, C, "<TOGGLE MODE            ")
        mcdu.paint(5, 0, S, C, " TARGET HDG      RADIUS ")
        mcdu.paint(6, 0, L, C, f" {hdg_str}                {radius_str:>3}>")
        mcdu.paint(12, 0, L, C, "<BACK                   ")
        if self.scratchpad_text:
            mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)

    async def get_psx_connection_coro(self):  # pylint: disable=too-many-statements
        """Maintain a PSX connection."""
        def connected(*_):
            self.logger.info("PSX CONNECTED")
            self.psx_connected = True
            self.psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__}")

        def disconnected():
            self.logger.info("PSX DISCONNECTED")
            self.psx_connected = False
            for mcdu in [self.mcduL, self.mcduR, self.mcduC]:
                if mcdu:
                    mcdu.unplug()
            for task in self.pending_paint_tasks.values():
                task.cancel()
            self.pending_paint_tasks.clear()
            self.active_mcdus.clear()

        def onresume():
            self.logger.info("PSX RESUMED")
            self.psx_connected = True
            self.psx_paused = False
            self.active_mcdus.clear()
            cdus = self.args.cdus.upper()
            side = self.args.menu_side.upper()
            row = self.args.menu_row
            text = "<FTECH" if side == "L" else "FTECH>"
            if "L" in cdus:
                if self.mcduL is None:
                    self.mcduL = psx.MCDU("L", side, row, text, self.mcduEvent)
                self.mcduL.plugin_to(self.psx)
                self.active_mcdus.append(self.mcduL)
            if "R" in cdus:
                if self.mcduR is None:
                    self.mcduR = psx.MCDU("R", side, row, text, self.mcduEvent)
                self.mcduR.plugin_to(self.psx)
                self.active_mcdus.append(self.mcduR)
            if "C" in cdus:
                if self.mcduC is None:
                    self.mcduC = psx.MCDU("C", side, row, text, self.mcduEvent)
                self.mcduC.plugin_to(self.psx)
                self.active_mcdus.append(self.mcduC)

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            self.psx = psx.Client()

            self.psx.onPause = lambda: setattr(self, 'psx_paused', True)
            self.psx.onDisconnect = disconnected
            self.psx.onConnect = lambda: None
            self.psx.onResume = onresume

            self.psx.subscribe("id")
            self.psx.subscribe("version", connected)
            self.psx.subscribe("PiBaHeAlTas")
            self.psx.subscribe("StartPiBaHeAlVsTasYw")
            self.psx.subscribe("GroundSpeed")
            self.psx.subscribe("Elev")
            self.psx.subscribe("Towing", self.towing_var_changed)
            self.psx.subscribe("TowTurnRadius", self.towing_var_changed)
            self.psx.subscribe("ParkBrkLev")

            self.psx.logger = self.logger.debug

            await self.psx.connect(
                self.args.psx_main_server_host,
                self.args.psx_main_server_port)
            self.logger.warning("psx.connect() returned, this should not happen")

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
                            self.logger.info("Task: %s has ended: %s", task.get_name(), exc)
                    else:
                        running.append(task.get_name())
                for task in tasks_ended:
                    self.logger.debug("Removing %s from task list", task)
                    self.tasks.discard(task)

                name = "PSXConnection"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.get_psx_connection_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                name = "SlewMonitor"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.slew_monitor_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                self.logger.debug("Running Tasks: %s", running)
                await asyncio.sleep(5.0)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
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
            '--cdus',
            type=str, action='store', default='LRC',
            help="Which CDUs to set up (any combination of L, R, C).",
        )
        parser.add_argument(
            '--menu-side',
            type=str, action='store', default='L', choices=['L', 'R'],
            help="Which side of the CDU menu to place the FTECH entry on.",
        )
        parser.add_argument(
            '--menu-row',
            type=int, action='store', default=5,
            help="Row (1-6) of the CDU menu to place the FTECH entry on.",
        )
        parser.add_argument(
            '--ground-force-value',
            type=int, action='store', default=-999000,
            help="Value sent to Qi198 when GROUND is pressed.",
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
            handlers=[logging.StreamHandler(sys.stdout)],
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
    try:
        asyncio.run(Script().run())
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            input("An error occurred, press Enter to continue...")
