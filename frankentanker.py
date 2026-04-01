# pylint: disable=invalid-name
"""A script to manage the water drop system on a 747 SuperTanker.

Usage:

- Start the sim including frankentanker.py
- Set the PSX ZFW to the OEW, e.g 180t
- Enter the planned retardant load and press PLNLOAD
- Press STARTLOAD for a realistic time to fill the tanks OR
- Press QUICKLOAD to instantly fill the tanks

MCDU page layut

   123456789012345678901234
  +------------------------+
0 |                        |
1 |                        |
2 |                        |
3 |                        |
4 |                        |
5 |                        |
6 |                        |
7 |                        |
8 |                        |
9 |                        |
10|                        |
11|                        |
12|                        |
13|                        |
  +------------------------+
sc|                        |
  +------------------------+

   123456789012345678901234
  +------------------------+
0 |      SUPERTANKER       |
1 |                        |
2 | ZFW          RETARDANT |
3 | $zfw   $retardant_load
4 |<OEW           DISARMED>|
5 | $oew                   |
6 |<DROPRATE     STARTLOAD>|
7 | $droprate              |
8 |<PLNLOAD      QUICKLOAD>|
9 | $target                |
10|                        |
11|                        |
12|                        |
13|<JETTISON          DROP>|
  +------------------------+
sc|                        |
  +------------------------+
"""

import argparse
import asyncio
import inspect
import logging
import random
import re
import sys
import time
import traceback

import psx

__MYNAME__ = 'frankentanker'
__MY_CLIENT_ID__ = 'TANKER'
__MY_DISPLAY_NAME__ = 'FrankenTanker'
__MY_DESCRIPTION__ = 'Load and drop H2O'

# Drop rates for the 1, 2 and 4 valves open modes (kg/s)
# EMG rate is used for load jettison (can this be higher than 4 valve rate?)
__DROPRATES__ = {
    "1": 950,
    "2": 2800,
    "4": 5700,
    "EMG": 8000,
}

# TAILHOOK operating conditions
__MAX_HOOK_SPEED_DEVIATION__ = 20  # Speed will vary this much when HOOK is in the water


def lb2kg(lb):
    """Convert pounds to kilos."""
    return float(lb) / 2.20462


def kg2lb(kg):
    """Convert kilos to pounds."""
    return float(kg) * 2.20462


class Script():  # pylint: disable=too-many-instance-attributes
    """Generic FrankenTech script."""

    def __init__(self):
        """Set up the class."""
        self.active_mcdus = []

        # Allowed ZFW range
        self.psx_zfw_min = 160000.0
        self.psx_zfw_max = 290000.0

        self.args = None
        self.taskgroup = None
        self.tasks = set()
        self.logger = None
        self.psx = None
        self.psx_connected = False

        # ZFW from PSX (kg)
        self.psx_zfw = None

        # The amount of retardant on board (kg).
        self.retardant_load = None

        # The target retardant load (when loading)
        self.retardant_load_target = None

        # This is the ZFW excluding retardant
        self.oew = 0.0

        # The drop rate: "1", "2", or "4"
        self.droprate = "4"

        # Realistic load rate (kg / second)
        self.loadrate = 60.0

        # Load rate using HOOK
        self.loadrate_hook = 1200.0

        # If we are currently dropping retardant
        self.dropping = False

        # If we are currently loading retardant
        self.loading = False
        self.loading_hook = False

        # If the dropping system is armed
        self.system_armed = False

        # If the tail hook is extended
        self.tailhook_extended = False

        # MCDU heads for the Left and Right CDUs
        self.mcduL = None
        self.mcduR = None

        # Pending repaint tasks, keyed by MCDU, to allow cancellation
        self.pending_paint_tasks = {}

        # CDU scratchpad buffer
        self.scratchpad_text = ''

    async def repaint_all_mcdus(self):
        """Trigger a repaint of all MCDUs, cancelling any pending paint tasks first."""
        self.logger.debug("Refreshing all active MCDUs")
        for mcdu in self.active_mcdus:
            existing = self.pending_paint_tasks.get(mcdu)
            if existing and not existing.done():
                existing.cancel()
            self.pending_paint_tasks[mcdu] = asyncio.create_task(
                self.paintTankerPage(mcdu))

    def psx_send_and_set(self, psx_variable, new_psx_value):
        """Send variable to PSX and store in local db."""
        self.psx.send(psx_variable, new_psx_value)
        self.psx._set(psx_variable, new_psx_value)  # pylint: disable=protected-access

    def addon_message_handler(self, _, value):  # pylint: disable=too-many-branches
        """Handle received addon messages."""
        if not re.match(r"^FRANKENTANKER:", value):
            return
        try:
            command = value.split(":", 1)[1]
        except IndexError:
            self.logger.warning("Got unsupported addon message: %s", value)
            return
        if command == "DROP":
            if self.system_armed:
                self.dropping = True
                asyncio.create_task(self.repaint_all_mcdus())
            else:
                self.logger.info("System not armed, cannot drop")
        elif command == "STOP":
            self.dropping = False
        elif command == "ARM":
            self.system_armed = True
            asyncio.create_task(self.repaint_all_mcdus())
        elif command == "DISARM":
            self.system_armed = False
            self.dropping = False
            asyncio.create_task(self.repaint_all_mcdus())
        elif command == "JETTISON":
            self.dropping = True
            self.system_armed = True
            self.droprate = "EMG"
            asyncio.create_task(self.repaint_all_mcdus())
        elif command == "EXTEND":
            if self.system_armed:
                self.tailhook_extended = True
                asyncio.create_task(self.repaint_all_mcdus())
            else:
                self.logger.info("System not armed")
        elif command == "RETRACT":
            if self.tailhook_extended:
                self.tailhook_extended = False
                self.system_armed = False
                asyncio.create_task(self.repaint_all_mcdus())
        else:
            self.logger.warning("Got unsupported addon message: %s", value)

    def psx_zfw_change(self, _, value):
        """Call when the PSX ZFW changes."""
        self.psx_zfw = lb2kg(float(value))
        asyncio.create_task(self.repaint_all_mcdus())
        self.logger.debug("PSX ZFW changed to %.1f t", self.psx_zfw / 1000)

    def reset_tank_system(self):
        """Reset initial state."""
        self.loading = False
        self.loading_hook = False
        self.system_armed = False
        self.tailhook_extended = False
        asyncio.create_task(self.repaint_all_mcdus())

    async def tank_control_coro(self):  # pylint: disable=too-many-branches,too-many-statements
        """Check system state and adjust PSX ZFW and other variables as needed.

        Also trigged CDU page re-draw if any data has changed.

        """
        myname = inspect.currentframe().f_code.co_name
        try:  # pylint:disable=too-many-nested-blocks
            self.logger.debug("Starting %s", myname)
            last_ran = time.perf_counter()
            while True:
                repaint = False
                await asyncio.sleep(1.0)
                if not self.psx_connected:
                    self.logger.debug("PSX not yet connected, %s sleeping",
                                      myname)
                    last_ran = time.perf_counter()
                    continue

                # We need to add a margin of 10kg here since
                # self.psx_zfw will be converted to lb and then
                # rounded to int before being read back.
                if self.oew < self.psx_zfw_min - 10 or self.oew > self.psx_zfw + 10:
                    self.logger.info(
                        (
                            "You must enter a valid OEW: now %.1f, (valid is %.1f...%.1f)" +
                            ", system disabled"
                        ),
                        self.oew / 1000, self.psx_zfw_min / 1000, self.psx_zfw / 1000)
                    self.reset_tank_system()
                    continue

                if self.retardant_load > self.args.retardant_load_max:
                    self.retardant_load = self.args.retardant_load_max
                    repaint = True

                if self.retardant_load < self.args.retardant_load_min:
                    self.retardant_load = self.args.retardant_load_min
                    repaint = True

                elapsed = time.perf_counter() - last_ran
                last_ran = time.perf_counter()
                self.logger.debug("%s waking up after %.1f s, system is %s",
                                  myname, elapsed,
                                  "ARMED" if self.system_armed else "not armed"
                                  )

                # Ensure PSX ZFW updated
                new_zfw = self.retardant_load + self.oew
                new_zfw_lb = int(kg2lb(new_zfw))

                if new_zfw_lb != int(kg2lb(self.psx_zfw)):
                    self.logger.debug(
                        "%f != %f: Sending new ZFW to PSX: %.1f kg == %d lbs",
                        new_zfw, self.psx_zfw,
                        new_zfw, new_zfw_lb)
                    self.psx_send_and_set("TrueZfw", str(new_zfw_lb))
                    repaint = True

                if self.loading and self.loading_hook:
                    self.logger.info("Cannot use pump and HOOK at the same time, stopping pump")
                    self.loading = False
                    repaint = True

                if self.loading and self.dropping:
                    self.logger.info("Cannot load and drop at the same time, resetting system")
                    self.loading = False
                    self.dropping = False
                    self.system_armed = False
                    repaint = True

                # Handle retardant loading
                if self.loading:
                    self.logger.info("Retardant load in progress, rate %.0f kg/s", self.loadrate)
                    self.retardant_load += elapsed * self.loadrate
                    if self.retardant_load >= self.retardant_load_target:
                        self.retardant_load = self.retardant_load_target
                        self.loading = False
                        self.logger.info("Retardant load target reached, stopping load")
                    elif self.retardant_load >= self.args.retardant_load_max:
                        self.retardant_load = self.args.retardant_load_max
                        self.loading = False
                        self.logger.info("Retardant tanks full, stopping load")
                    repaint = True

                # Handle retardant dropping
                if self.dropping:
                    if not self.system_armed:
                        self.logger.info("System not armed, cannot drop")
                        self.dropping = False
                        repaint = True

                if self.dropping:
                    self.retardant_load -= elapsed * __DROPRATES__[self.droprate]
                    self.logger.info(
                        "Retardant drop in progress, drop rate %.0f kg/s, remaining load %.0f kg",
                        __DROPRATES__[self.droprate], self.retardant_load)
                    if self.retardant_load <= self.args.retardant_load_min:
                        self.retardant_load = self.args.retardant_load_min
                        self.dropping = False
                        self.system_armed = False
                        self.logger.info("Retardant load empty, stopping drop")
                    repaint = True

                # If the TAILHOOK is extended and we are at the
                # correct height and airspeed, load some more redardant
                if self.tailhook_extended:
                    raw_height = self.psx.get("AcftHeight")
                    raw_pibahealtas = self.psx.get("PiBaHeAlTas")
                    if raw_height is None or raw_pibahealtas is None:
                        self.logger.debug(
                            "AcftHeight or PiBaHeAlTas not yet available, skipping hook")
                    else:
                        height = float(raw_height)
                        tas = float(raw_pibahealtas.split(";")[4]) / 1000

                        # Inject random speed deviation when HOOK is in the water
                        # defined as upper limit of loading height plus 10 ft
                        if height < self.args.hook_max_height + 10:
                            psx_wxburst = 300 + random.randint(
                                0, self.args.hook_speed_fluctuations)
                            if random.randint(-100, 100) > 0:
                                psx_wxburst = -psx_wxburst
                            self.logger.info("HOOK in the water, injecting WxBurst=%d", psx_wxburst)
                            self.psx_send_and_set("WxBurst", psx_wxburst)

                        self.logger.info(
                            "HOOK extended - height is %.0f ft TAS is %.0f kt", height, tas)
                        hook_use_possible = True
                        if tas < self.args.hook_min_speed:
                            self.logger.info("Too slow for HOOK")
                            hook_use_possible = False
                        if tas > self.args.hook_max_speed:
                            self.logger.info("Too fast for HOOK")
                            hook_use_possible = False
                        if height < self.args.hook_min_height:
                            self.logger.info("TOO LOW, HOOK retracting!")
                            hook_use_possible = False
                            self.tailhook_extended = False
                            self.system_armed = False
                            self.loading_hook = False
                            repaint = True
                        if height > self.args.hook_max_height:
                            self.logger.info("Too high for HOOK")
                            hook_use_possible = False
                        if hook_use_possible:
                            self.logger.info("HOOK loading in progress")
                            self.loading_hook = True
                            self.retardant_load += elapsed * self.loadrate_hook
                            if self.retardant_load >= self.retardant_load_target:
                                self.retardant_load = self.retardant_load_target
                                self.tailhook_extended = False
                                self.system_armed = False
                                self.loading_hook = False
                                self.logger.info(
                                    "Retardant target reached, retracting and disarming")
                            elif self.retardant_load >= self.args.retardant_load_max:
                                self.retardant_load = self.args.retardant_load_max
                                self.tailhook_extended = False
                                self.system_armed = False
                                self.loading_hook = False
                                self.logger.info(
                                    "Retardant tanks filled, retracting and disarming")
                            repaint = True
                        else:
                            self.loading_hook = False
                else:
                    self.loading_hook = False

                # If we changed anything relevant, repaint MCDU page
                if repaint:
                    self.logger.debug("Adjusted some data, repainting")
                    asyncio.create_task(self.repaint_all_mcdus())

        except Exception as exc:  # pylint:disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, myname)
            self.logger.critical(traceback.format_exc())

    def mcduEvent(self, mcdu, event_type, value=None):  # pylint: disable=too-many-branches,too-many-statements
        """Call made by an MCDU when it has something to report or request."""
        self.logger.debug("MCDU event from %s: %s=%s", mcdu.location, event_type, value)
        if event_type in ["logon", "resume"]:
            asyncio.create_task(self.repaint_all_mcdus())
        elif event_type == "keypress":
            repaint = False
            if value == "CLR":
                self.scratchpad_text = ''
                mcdu.paint(13, 0, "large", "white", " " * 24)
            elif value == "2L":  # OEW: accept scratchpad value (tonnes)
                if self.scratchpad_text:
                    try:
                        self.oew = 1000 * float(self.scratchpad_text)
                        # Assume any ZFW weight above OEW is retardant)
                        self.retardant_load = max(0, self.psx_zfw - self.oew)
                        self.scratchpad_text = ''
                        mcdu.paint(13, 0, "large", "white", " " * 24)
                        repaint = True
                    except ValueError:
                        pass
                else:
                    # Default to current ZFW
                    self.oew = min(self.args.default_oew, self.psx_zfw)
                    self.retardant_load = max(0, self.psx_zfw - self.oew)
                    repaint = True
            elif value == "2R":  # DISARMED/ARMED toggle
                if self.loading:
                    self.logger.info("Cannot arm system while loading")
                else:
                    self.system_armed = not self.system_armed
                    self.logger.info("ARM/DISARM pressed, state is now %s", self.system_armed)
                repaint = True
            elif value == "3L":  # DROPRATE: cycle through valve counts
                keys = list(__DROPRATES__.keys())
                self.droprate = keys[(keys.index(self.droprate) + 1) % len(keys)]
                repaint = True
            elif value == "3R":  # STARTLOAD
                self.loading = True
                repaint = True
            elif value == "4L":  # PLNLOAD: set retardant load target from scratchpad (tonnes → kg)
                if self.scratchpad_text:
                    try:
                        self.retardant_load_target = float(self.scratchpad_text) * 1000.0
                        self.retardant_load_target = min(
                            self.retardant_load_target, self.args.retardant_load_max)
                        self.retardant_load_target = max(
                            self.retardant_load_target, self.args.retardant_load_min)
                        self.scratchpad_text = ''
                        mcdu.paint(13, 0, "large", "white", " " * 24)
                        repaint = True
                    except ValueError:
                        pass
                else:
                    # Default to max load
                    self.retardant_load_target = self.args.retardant_load_max
                    repaint = True
            elif value == "6R":  # DROP/STOP toggle
                if self.dropping:
                    self.dropping = False
                else:
                    if self.system_armed:
                        self.dropping = True
                    else:
                        self.logger.info("System not armed")
                repaint = True
            elif value == "6L":  # JETTISON: emergency full-rate drop
                self.dropping = True
                self.system_armed = True
                self.droprate = "EMG"
                repaint = True
            elif value == "5R":  # EXTEND/RETRACT tailhook toggle
                if self.tailhook_extended:
                    self.tailhook_extended = False
                    self.system_armed = False
                else:
                    if self.system_armed:
                        self.tailhook_extended = True
                    else:
                        self.logger.info("System not armed")
                repaint = True
            elif value == "4R":  # QUICKLOAD: instant fill to target
                self.loading = False
                self.retardant_load = min(self.retardant_load_target, self.args.retardant_load_max)
                repaint = True
            elif value in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '.']:
                if len(self.scratchpad_text) < 10:
                    self.scratchpad_text += value
                    mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)
            if repaint:
                asyncio.create_task(self.repaint_all_mcdus())
        else:
            self.logger.debug(
                "Unhandled MCDU event from %s: %s=%s", mcdu.location, event_type, value)

    async def paintTankerPage(self, mcdu):
        """Paint the main SuperTanker water drop status page on the MCDU."""
        # Allow PSX enough time to paint <ACT. Cosmetical only.
        await asyncio.sleep(0.5)

        A = "amber"
        C = "cyan"
        R = "red"
        W = "white"
        L = "large"
        S = "small"

        ret_t = self.retardant_load / 1000.0
        arm_label = "     ARM>" if not self.system_armed else "  DISARM>"
        mcdu.clear()
        #                          123456789012345678901234
        if self.dropping:
            mcdu.paint(0, 0, L, R, "       DROPPING         ")
        elif self.loading_hook:
            mcdu.paint(0, 0, L, R, " I IDENTIFY AS A CL-415 ")
        elif self.tailhook_extended:
            mcdu.paint(0, 0, L, R, "     HOOK EXTENDED      ")
        elif self.system_armed:
            mcdu.paint(0, 0, L, R, "     SYSTEM ARMED       ")
        elif self.loading:
            mcdu.paint(0, 0, L, W, "       LOADING...       ")
        else:
            mcdu.paint(0, 0, S, A, "      SUPERTANKER       ")
        # row 1: empty
        mcdu.paint(2, 0, S, C, " ZFW           RETARDANT")
        mcdu.paint(3, 0, L, C, f" {self.psx_zfw / 1000:6.1f}          {ret_t:6.1f} ")
        mcdu.paint(4, 0, L, C, f"<OEW           {arm_label}")
        mcdu.paint(5, 0, S, C, f" {self.oew / 1000:6.1f}                ")
        mcdu.paint(6, 0, L, C, "<DROPRATE     STARTLOAD>")
        droprate_label = "EMERG" if self.droprate == "EMG" else f"{self.droprate} VALVE(S)"
        mcdu.paint(7, 0, S, C, f" {droprate_label}")
        mcdu.paint(8, 0, L, C, "<PLNLOAD      QUICKLOAD>")
        mcdu.paint(9, 0, S, C, f" {self.retardant_load_target / 1000:.1f} ")
        hook_label = "HOOK RETRACT>" if self.tailhook_extended else " HOOK EXTEND>"
        mcdu.paint(10, 0, L, C, f"           {hook_label}")
        if self.system_armed:
            drop_label = "          STOP>" if self.dropping else "          DROP>"
        else:
            drop_label = "          STOP>" if self.dropping else "    DROP INHBT>"

        mcdu.paint(12, 0, L, C, f"<JETTISON{drop_label}")
        if self.scratchpad_text:
            mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)

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
            if self.mcduL:
                self.mcduL.unplug()
            if self.mcduR:
                self.mcduR.unplug()
            for task in self.pending_paint_tasks.values():
                task.cancel()
            self.pending_paint_tasks.clear()
            self.active_mcdus.clear()

        def onresume():
            """Run when load3 is seen, i.e when we have a full set of variables."""
            self.logger.info("PSX RESUMED")
            self.psx.send("demand", "LeftPfdAlt")
            self.psx_connected = True
            if self.mcduL is None:
                self.mcduL = psx.MCDU("L", "L", 5, "<TANK", self.mcduEvent)
                self.mcduR = psx.MCDU("R", "L", 5, "<TANK", self.mcduEvent)
            self.mcduL.plugin_to(self.psx)
            self.mcduR.plugin_to(self.psx)
            self.active_mcdus.clear()
            self.active_mcdus.append(self.mcduL)
            self.active_mcdus.append(self.mcduR)

        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            self.psx = psx.Client()

            self.psx.onPause = lambda: None
            self.psx.onDisconnect = disconnected
            self.psx.onConnect = lambda: None
            self.psx.onResume = onresume

            self.psx.subscribe("TrueZfw", self.psx_zfw_change)

            self.psx.subscribe("addon", self.addon_message_handler)

            # Needed to get elevation above terrain and TAS
            self.psx.subscribe("AcftHeight")
            self.psx.subscribe("PiBaHeAlTas")

            self.psx.subscribe("id")
            self.psx.subscribe("version", connected)

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
                # Cleanup
                for task in tasks_ended:
                    self.logger.debug("Removing %s from task list", task)
                    self.tasks.discard(task)

                # Ensure the tasks are running
                name = "PSXConnection"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.get_psx_connection_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                name = "TankControl"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.tank_control_coro(), name=name)
                    self.tasks.add(task)
                    self.logger.info("Started %s.", name)

                self.logger.debug("Running Tasks: %s", running)
                # Sleep a while until next check
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
            '--hook-min-height',
            type=float, action='store', default=60,
            help="The HOOK will not load water if your height is less than this (ft)",
        )
        parser.add_argument(
            '--hook-max-height',
            type=float, action='store', default=200,
            help="The HOOK will not load water if your height is greater than this (ft)",
        )
        parser.add_argument(
            '--hook-min-speed',
            type=float, action='store', default=120,
            help="The HOOK will not load water if your speed is less than this (kts TAS)",
        )
        parser.add_argument(
            '--hook-max-speed',
            type=float, action='store', default=180,
            help="The HOOK will not load water if your speed is greater than this (kts TAS)",
        )
        parser.add_argument(
            '--hook-speed-fluctuations',
            type=int, action='store', default=30,
            help="Limit for random airspeed fluctuations when HOOK in water (kts)",
        )
        parser.add_argument(
            '--retardant-load-max',
            type=float, action='store', default=74000.0,
            help="How much retardant we can carry (kg)",
        )
        parser.add_argument(
            '--retardant-load-min',
            type=float, action='store', default=0.0,
            help="How little retardant we can carry (kg)",
        )
        parser.add_argument(
            '--default-oew',
            type=float, action='store', default=180000.0,
            help="Default OEW (kg)",
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help="Print more debug info. Probably only useful for development.",
        )
        self.args = parser.parse_args()

        # Initialize some default state
        self.retardant_load = self.args.retardant_load_min

        # The target retardant load (when loading)
        self.retardant_load_target = 0.5 * self.args.retardant_load_max

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
