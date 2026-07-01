# pylint: disable=invalid-name,too-many-lines
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
3 | $zfw   $retardant_load |
4 |               DISARMED>|
5 |                        |
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

Some likely numbers:

Global 747-400 Supertanker, N744ST
Up to 74000 l or water or retardant

MTOW 396t
MTOW ~300t on firefighting mission

Normal drop height: 400-800 ft
Normal drop speed: 145-155 kt

Normal BCF OEW is 162-165t
Add RDS weight: ~12t

We will use an OEW of 175 t
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
__TURB_INT_STEPS__ = [0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 99]

__DROPRATE_DEFAULT__ = "CL2"
__DROPRATES__ = {
    "CL2": 1200,
    "CL4": 2400,
    "CL6": 3600,
    "CL8": 4800,
    "CL10": 6000,
    "EMG": 8000,
}


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

        self.args = None
        self.taskgroup = None
        self.tasks = set()
        self.logger = None
        self.psx = None
        self.psx_connected = False
        self.psx_paused = False

        self.last_drop_sound_played = 0

        # ZFW from PSX (kg)
        self.psx_zfw = None

        # The amount of retardant on board (kg).
        self.retardant_load = None

        # The target retardant load (when loading)
        self.retardant_load_target = None

        # The drop rate (key from __DROPRATES__, excluding "EMG")
        self.droprate = __DROPRATE_DEFAULT__

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

        # MCDU heads for the Left, Right and Center CDUs
        self.mcduL = None
        self.mcduR = None
        self.mcduC = None

        # Pending repaint tasks, keyed by MCDU, to allow cancellation
        self.pending_paint_tasks = {}

        # CDU scratchpad buffer
        self.scratchpad_text = ''

        self.repaint_req_by = set()

        self.mcdu_page = "tanker"
        self.turb_enable = False
        self.turb_int_spd = 0
        self.turb_int_yaw = 0
        self.turb_int_bank = 0
        self.turb_int_sink = 0
        self.turb_int_gust = 0
        self.turbulence_events_per_minute = 5
        self.turbulence_max_alt = 0

    async def repaint_all_mcdus(self):
        """Trigger a repaint of all MCDUs, cancelling any pending paint tasks first."""
        self.logger.debug("Refreshing all active MCDUs, requested by: %s",
                          self.repaint_req_by)
        for mcdu in self.active_mcdus:
            existing = self.pending_paint_tasks.get(mcdu)
            if existing and not existing.done():
                existing.cancel()
            if self.mcdu_page == "turbulence":
                self.pending_paint_tasks[mcdu] = asyncio.create_task(
                    self.paintTurbulencePage(mcdu))
            else:
                self.pending_paint_tasks[mcdu] = asyncio.create_task(
                    self.paintTankerPage(mcdu))
        self.repaint_req_by = set()

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
                self.repaint_req_by.add("drop-message")
                asyncio.create_task(self.repaint_all_mcdus())
            else:
                self.logger.info("System not armed, cannot drop")
        elif command == "STOP":
            self.dropping = False
            self.repaint_req_by.add("stop-message")
            asyncio.create_task(self.repaint_all_mcdus())
        elif command == "ARM":
            self.system_armed = True
            self.repaint_req_by.add("arm-message")
            asyncio.create_task(self.repaint_all_mcdus())
        elif command == "DISARM":
            self.system_armed = False
            self.dropping = False
            self.repaint_req_by.add("disarm-message")
            asyncio.create_task(self.repaint_all_mcdus())
        elif command == "JETTISON":
            self.dropping = True
            self.system_armed = True
            self.droprate = "EMG"
            self.repaint_req_by.add("jettison-message")
            asyncio.create_task(self.repaint_all_mcdus())
        elif command == "EXTEND":
            if self.system_armed:
                self.tailhook_extended = True
                self.repaint_req_by.add("extend-message")
                asyncio.create_task(self.repaint_all_mcdus())
            else:
                self.logger.info("System not armed")
        elif command == "RETRACT":
            if self.tailhook_extended:
                self.tailhook_extended = False
                self.system_armed = False
                self.repaint_req_by.add("retract-message")
                asyncio.create_task(self.repaint_all_mcdus())
        else:
            self.logger.warning("Got unsupported addon message: %s", value)

    def psx_zfw_change(self, _, value):
        """Call when the PSX ZFW changes."""
        self.psx_zfw = lb2kg(float(value))
        self.repaint_req_by.add("zfw-change")
        asyncio.create_task(self.repaint_all_mcdus())
        self.logger.debug("PSX ZFW changed to %.1f t", self.psx_zfw / 1000)

    def reset_tank_system(self):
        """Reset initial state."""
        self.loading = False
        self.loading_hook = False
        self.dropping = False
        self.droprate = __DROPRATE_DEFAULT__
        self.system_armed = False
        self.tailhook_extended = False
        self.repaint_req_by.add("system-reset")
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
                await asyncio.sleep(self.args.cdu_update_interval)

                if not self.psx_connected or self.psx_paused:
                    self.logger.debug("PSX not yet connected or paused, %s sleeping",
                                      myname)
                    last_ran = time.perf_counter()
                    continue

                # Wait for PSX connection to be established
                if self.psx_zfw is None:
                    self.logger.debug("PSX ZFW not yet available, %s sleeping", myname)
                    last_ran = time.perf_counter()
                    continue

                # Retardant load is always calculated as actual PSX ZFW - OEW
                self.retardant_load = self.psx_zfw - self.args.oew

                # We start by assuming that the ZFW will remain unchanged
                new_zfw = self.psx_zfw

                # If retardant load ends up greater than the tank
                # capacity, adjust ZFW.
                if self.retardant_load > self.args.retardant_load_max:
                    new_zfw = self.args.oew + self.args.retardant_load_max
                    self.retardant_load = self.args.retardant_load_max
                    self.logger.info("Too much load, reducing ZFW to %.0f kg", new_zfw)
                    self.repaint_req_by.add("load-too-high")

                if self.retardant_load < 0:
                    self.logger.info("Retardant load less than zero: %.1f", self.retardant_load)
                    self.retardant_load = 0.0
                    new_zfw = self.args.oew + 100
                    self.repaint_req_by.add("load-less-than-zero")

                elapsed = time.perf_counter() - last_ran
                last_ran = time.perf_counter()

                if self.loading and self.loading_hook:
                    self.logger.info("Cannot use pump and HOOK at the same time, stopping pump")
                    self.loading = False
                    self.repaint_req_by.add("pump-and-hook-err")

                if self.loading and self.dropping:
                    self.logger.info("Cannot load and drop at the same time, resetting system")
                    self.reset_tank_system()

                # Handle retardant loading
                if self.loading:
                    self.logger.info(
                        "Retardant load in progress, rate %.0f kg/s, load %.0f kg",
                        self.loadrate, self.retardant_load)
                    self.retardant_load += elapsed * self.loadrate
                    if self.retardant_load >= self.retardant_load_target:
                        self.retardant_load = self.retardant_load_target
                        self.reset_tank_system()
                        self.logger.info("Retardant load target reached, stopping load")
                    elif self.retardant_load >= self.args.retardant_load_max:
                        self.retardant_load = self.args.retardant_load_max
                        self.reset_tank_system()
                        self.logger.info("Retardant tanks full, stopping load")
                    self.repaint_req_by.add("loading")
                    new_zfw = self.args.oew + self.retardant_load

                #
                # Handle retardant dropping
                #

                # Cannot drop if system is not armed
                if self.dropping:
                    if not self.system_armed:
                        self.logger.info("System not armed, cannot drop")
                        self.dropping = False
                        self.repaint_req_by.add("drop-not-armed")

                if self.dropping:
                    self.repaint_req_by.add("dropping")
                    # Play drop sound via PSXSounds every N seconds
                    time_since_last_played = time.perf_counter() - self.last_drop_sound_played
                    if time_since_last_played > 6.0:
                        # Sound is ~5.5 seconds long
                        self.psx.send("addon", "PSNDB;;;;frankentankerdrop.mp3/")
                        self.last_drop_sound_played = time.perf_counter()

                    self.retardant_load -= elapsed * __DROPRATES__[self.droprate]
                    self.logger.info(
                        "Retardant drop in progress, drop rate %.0f kg/s, remaining load %.0f kg",
                        __DROPRATES__[self.droprate], self.retardant_load)
                    if self.retardant_load < 0:
                        self.retardant_load = 0
                        self.reset_tank_system()
                        self.logger.info("Retardant load empty, stopping drop")

                    new_zfw = self.args.oew + self.retardant_load

                #
                # TAILHOOK MODE
                #
                if self.tailhook_extended:
                    raw_height = self.psx.get("AcftHeight")
                    raw_pibahealtas = self.psx.get("PiBaHeAlTas")
                    if raw_height is None or raw_pibahealtas is None:
                        self.logger.debug(
                            "AcftHeight or PiBaHeAlTas not yet available, skipping hook")
                    else:
                        height = float(raw_height)
                        tas = float(raw_pibahealtas.split(";")[4]) / 1000

                        self.logger.info(
                            "HOOK extended - height is %.0f ft TAS is %.0f kt", height, tas)
                        hook_use_possible = True
                        if height < self.args.hook_min_height:
                            self.logger.info("TOO LOW, HOOK retracting! (%.0f < %.0f)",
                                             height, self.args.hook_min_height)
                            hook_use_possible = False
                            self.reset_tank_system()
                        else:
                            if tas < self.args.hook_min_speed:
                                self.logger.info("Too slow for HOOK: %.0f < %.0f",
                                                 tas, self.args.hook_min_speed)
                                hook_use_possible = False
                            if tas > self.args.hook_max_speed:
                                self.logger.info("Too fast for HOOK: %.0f > %.0f",
                                                 tas, self.args.hook_max_speed)
                                hook_use_possible = False
                            if height > self.args.hook_max_height:
                                self.logger.info("Too high for HOOK! (%.0f > %.0f)",
                                                 height, self.args.hook_max_height)
                                hook_use_possible = False
                        if hook_use_possible:
                            # Inject random speed deviation when HOOK is in the water
                            if height < self.args.hook_max_height + 10:
                                psx_wxburst = 300 + random.randint(
                                    0, self.args.hook_speed_fluctuations)
                                if random.randint(-100, 100) > 0:
                                    psx_wxburst = -psx_wxburst
                                self.logger.info("HOOK in the water, injecting WxBurst=%d",
                                                 psx_wxburst)
                                self.psx_send_and_set("WxBurst", psx_wxburst)
                            self.logger.info("HOOK is scooping water!")
                            self.loading_hook = True
                            # loading rate is random due to hydrodynamic mumbo-jumbo
                            self.retardant_load += (
                                elapsed * self.loadrate_hook * random.uniform(0.2, 1.8))
                            if self.retardant_load >= self.retardant_load_target:
                                self.retardant_load = self.retardant_load_target
                                self.reset_tank_system()
                                self.logger.info(
                                    "Retardant target reached, retracting and disarming")
                            elif self.retardant_load >= self.args.retardant_load_max:
                                self.retardant_load = self.args.retardant_load_max
                                self.reset_tank_system()
                                self.logger.info(
                                    "Retardant tanks filled, retracting and disarming")
                            new_zfw = self.args.oew + self.retardant_load
                            self.repaint_req_by.add("scooping")
                        else:
                            self.loading_hook = False
                else:
                    self.loading_hook = False
                updated = self.update_psx_zfw(new_zfw)
                if updated:
                    self.repaint_req_by.add("zfw-updated")

                # If we changed anything relevant, repaint MCDU page
                if len(self.repaint_req_by) > 0:
                    asyncio.create_task(self.repaint_all_mcdus())

        except Exception as exc:  # pylint:disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, myname)
            self.logger.critical(traceback.format_exc())

    async def turbulence_coro(self):
        """Apply turbulence effects based on self.turb_* settings."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(0.2)

                if not self.psx_connected or self.psx_paused:
                    self.logger.debug("PSX not yet connected or paused, %s sleeping",
                                      myname)
                    continue

                if self.turb_enable:
                    if self.turbulence_max_alt > 0:
                        raw_height = self.psx.get("AcftHeight")
                        if raw_height is not None and float(raw_height) > self.turbulence_max_alt:
                            continue

                    # 300 cycles/minute at 200 ms; convert events/min to probability/cycle
                    inject_prob = self.turbulence_events_per_minute / 300.0
                    candidates = [
                        (intensity, base, label)
                        for intensity, base, label in (
                            (self.turb_int_spd, 300, "SPD"),
                            (self.turb_int_gust, 400, "GUST"),
                            (self.turb_int_yaw, 200, "YAW"),
                            (self.turb_int_bank, 100, "BANK"),
                            (self.turb_int_sink, 0, "SINK"),
                        )
                        if intensity > 0
                    ]
                    if candidates and random.random() < inject_prob:
                        intensity, base, label = random.choice(candidates)
                        changesize = random.randint(0, intensity)
                        direction = random.choice([-1, 1])
                        psx_value = direction * (base + changesize)
                        self.logger.info("TURB %s: injecting WxBurst=%d", label, psx_value)
                        self.psx_send_and_set("WxBurst", psx_value)

        except Exception as exc:  # pylint:disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, myname)
            self.logger.critical(traceback.format_exc())

    def update_psx_zfw(self, new_zfw):
        """If the ZFW changed, push the new one to PSX."""
        if self.psx_zfw is None:
            return False
        new_zfw_lb = int(kg2lb(new_zfw))
        if new_zfw_lb != int(kg2lb(self.psx_zfw)):
            self.logger.info(
                "Updating PSX ZFW from %.1f kg to approx %.1f kg == %d lbs",
                self.psx_zfw, new_zfw, new_zfw_lb)
            self.psx_send_and_set("TrueZfw", str(new_zfw_lb))
            return True
        return False

    def mcduEvent(self, mcdu, event_type, value=None):  # pylint: disable=too-many-branches,too-many-statements
        """Call made by an MCDU when it has something to report or request."""
        self.logger.debug("MCDU event from %s: %s=%s", mcdu.location, event_type, value)
        if event_type in ["logon", "resume"]:
            self.mcdu_page = "tanker"
            asyncio.create_task(self.repaint_all_mcdus())
        elif event_type == "keypress":  # pylint: disable=too-many-nested-blocks
            self.repaint_req_by = set()
            if value == "CLR":
                self.scratchpad_text = ''
                mcdu.paint(13, 0, "large", "white", " " * 24)
            elif value in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9']:
                if len(self.scratchpad_text) < 10:
                    self.scratchpad_text += value
                    mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)
            elif self.mcdu_page == "turbulence":
                if value == "1L":  # ENABLE/DISABLE turbulence effects
                    self.turb_enable = not self.turb_enable
                    self.repaint_req_by.add("turb-enable-toggle")
                elif value == "2L":  # SPD intensity
                    if self.scratchpad_text:
                        try:
                            v = int(self.scratchpad_text)
                            if 0 <= v <= 99:
                                self.turb_int_spd = v
                                self.scratchpad_text = ''
                                mcdu.paint(13, 0, "large", "white", " " * 24)
                        except ValueError:
                            pass
                    else:
                        self.turb_int_spd = next(
                            (s for s in __TURB_INT_STEPS__ if s > self.turb_int_spd),
                            __TURB_INT_STEPS__[0])
                    self.repaint_req_by.add("turb-spd-press")
                elif value == "2R":  # YAW intensity
                    if self.scratchpad_text:
                        try:
                            v = int(self.scratchpad_text)
                            if 0 <= v <= 99:
                                self.turb_int_yaw = v
                                self.scratchpad_text = ''
                                mcdu.paint(13, 0, "large", "white", " " * 24)
                        except ValueError:
                            pass
                    else:
                        self.turb_int_yaw = next(
                            (s for s in __TURB_INT_STEPS__ if s > self.turb_int_yaw),
                            __TURB_INT_STEPS__[0])
                    self.repaint_req_by.add("turb-yaw-press")
                elif value == "3L":  # BANK intensity
                    if self.scratchpad_text:
                        try:
                            v = int(self.scratchpad_text)
                            if 0 <= v <= 99:
                                self.turb_int_bank = v
                                self.scratchpad_text = ''
                                mcdu.paint(13, 0, "large", "white", " " * 24)
                        except ValueError:
                            pass
                    else:
                        self.turb_int_bank = next(
                            (s for s in __TURB_INT_STEPS__ if s > self.turb_int_bank),
                            __TURB_INT_STEPS__[0])
                    self.repaint_req_by.add("turb-bank-press")
                elif value == "3R":  # SINK intensity
                    if self.scratchpad_text:
                        try:
                            v = int(self.scratchpad_text)
                            if 0 <= v <= 99:
                                self.turb_int_sink = v
                                self.scratchpad_text = ''
                                mcdu.paint(13, 0, "large", "white", " " * 24)
                        except ValueError:
                            pass
                    else:
                        self.turb_int_sink = next(
                            (s for s in __TURB_INT_STEPS__ if s > self.turb_int_sink),
                            __TURB_INT_STEPS__[0])
                    self.repaint_req_by.add("turb-sink-press")
                elif value == "4L":  # GUST intensity
                    if self.scratchpad_text:
                        try:
                            v = int(self.scratchpad_text)
                            if 0 <= v <= 99:
                                self.turb_int_gust = v
                                self.scratchpad_text = ''
                                mcdu.paint(13, 0, "large", "white", " " * 24)
                        except ValueError:
                            pass
                    else:
                        self.turb_int_gust = next(
                            (s for s in __TURB_INT_STEPS__ if s > self.turb_int_gust),
                            __TURB_INT_STEPS__[0])
                    self.repaint_req_by.add("turb-gust-press")
                elif value == "DEL":
                    self.turbulence_max_alt = 0
                    self.scratchpad_text = ''
                    mcdu.paint(13, 0, "large", "white", " " * 24)
                    self.repaint_req_by.add("turb-maxalt-del")
                elif value == "5L":  # MAXALT: set max altitude for turbulence injection
                    if self.scratchpad_text:
                        try:
                            v = int(self.scratchpad_text)
                            if v >= 0:
                                self.turbulence_max_alt = v
                                self.scratchpad_text = ''
                                mcdu.paint(13, 0, "large", "white", " " * 24)
                        except ValueError:
                            pass
                    else:
                        self.turbulence_max_alt = 0
                    self.repaint_req_by.add("turb-maxalt-press")
                elif value == "5R":  # RATE: set events per minute from scratchpad
                    if self.scratchpad_text:
                        try:
                            v = int(self.scratchpad_text)
                            if v > 0:
                                self.turbulence_events_per_minute = min(v, 150)
                                self.scratchpad_text = ''
                                mcdu.paint(13, 0, "large", "white", " " * 24)
                        except ValueError:
                            pass
                    self.repaint_req_by.add("turb-rate-press")
                elif value == "6L":  # BACK to tanker page
                    self.mcdu_page = "tanker"
                    self.repaint_req_by.add("turb-back-press")
                elif value == "6R":  # RESET all turbulence settings
                    self.turb_enable = False
                    self.turb_int_spd = 0
                    self.turb_int_yaw = 0
                    self.turb_int_bank = 0
                    self.turb_int_sink = 0
                    self.turb_int_gust = 0
                    self.turbulence_events_per_minute = 5
                    self.turbulence_max_alt = 0
                    self.repaint_req_by.add("turb-reset-press")
            elif value == "2R":  # DISARMED/ARMED toggle
                if self.loading:
                    self.logger.info("Cannot arm system while loading")
                else:
                    self.system_armed = not self.system_armed
                    self.logger.info("ARM/DISARM pressed, state is now %s", self.system_armed)
                self.repaint_req_by.add("arm-disarm-press")
            elif value == "3L":  # DROPRATE: cycle through valve counts (EMG excluded)
                keys = [k for k in __DROPRATES__ if k != "EMG"]
                current = self.droprate if self.droprate in keys else __DROPRATE_DEFAULT__
                self.droprate = keys[(keys.index(current) + 1) % len(keys)]
                self.repaint_req_by.add("droprate-press")
            elif value == "3R":  # STARTLOAD
                self.loading = True
                self.repaint_req_by.add("startload-press")
            elif value == "4L":  # PLNLOAD: set retardant load target from scratchpad (tonnes → kg)
                if self.scratchpad_text:
                    try:
                        self.retardant_load_target = float(self.scratchpad_text) * 1000.0
                        self.retardant_load_target = min(
                            self.retardant_load_target, self.args.retardant_load_max)
                        self.retardant_load_target = max(
                            self.retardant_load_target, 0)
                        self.scratchpad_text = ''
                        mcdu.paint(13, 0, "large", "white", " " * 24)
                        self.repaint_req_by.add("plnload-press")
                    except ValueError:
                        pass
                else:
                    # Default to max load
                    self.retardant_load_target = self.args.retardant_load_max
                    self.repaint_req_by.add("plnload-press-max")
            elif value == "6R":  # DROP/STOP toggle
                if self.dropping:
                    self.dropping = False
                else:
                    if self.system_armed:
                        self.dropping = True
                    else:
                        self.logger.info("System not armed")
                self.repaint_req_by.add("drop-stop-toggle-press")
            elif value == "6L":  # JETTISON: emergency full-rate drop
                self.dropping = True
                self.system_armed = True
                self.droprate = "EMG"
                self.repaint_req_by.add("jettison-press")
            elif value == "5L":  # Navigate to turbulence page
                self.mcdu_page = "turbulence"
                self.repaint_req_by.add("turb-nav-press")
            elif value == "5R":  # EXTEND/RETRACT tailhook toggle
                if self.tailhook_extended:
                    self.tailhook_extended = False
                    self.system_armed = False
                else:
                    if self.system_armed:
                        self.tailhook_extended = True
                    else:
                        self.logger.info("System not armed")
                self.repaint_req_by.add("hook-extend-retract-press")
            elif value == "4R":  # QUICKLOAD: instant fill to target
                self.loading = False
                self.retardant_load = min(
                    self.retardant_load_target,
                    self.args.retardant_load_max)
                self.update_psx_zfw(self.args.oew + self.retardant_load)
                self.repaint_req_by.add("quickload-press")
            elif value == '.':
                if len(self.scratchpad_text) < 10:
                    self.scratchpad_text += value
                    mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)
            if len(self.repaint_req_by) > 0:
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

        try:
            ret_t = self.retardant_load / 1000.0
        except TypeError:
            ret_t = 0.0

        arm_label = "     ARM>" if not self.system_armed else "  DISARM>"
        mcdu.clear()
        #                          123456789012345678901234
        if self.dropping:
            dropping_label = "EMERG" if self.droprate == "EMG" else self.droprate
            mcdu.paint(0, 0, L, R, f"     DROPPING {dropping_label:5s}     ")
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
        zfw_str = f"{self.psx_zfw / 1000:6.1f}" if self.psx_zfw is not None else "   ---"
        mcdu.paint(3, 0, L, C, f" {zfw_str}          {ret_t:6.1f} ")
        mcdu.paint(4, 0, L, C, f"               {arm_label}")
        mcdu.paint(6, 0, L, C, "<DROPRATE     STARTLOAD>")
        droprate_label = "EMERG" if self.droprate == "EMG" else f"{self.droprate}"
        mcdu.paint(7, 0, S, C, f" {droprate_label}")
        mcdu.paint(8, 0, L, C, "<PLNLOAD      QUICKLOAD>")
        mcdu.paint(9, 0, S, C, f" {self.retardant_load_target / 1000:.1f} ")
        hook_label = "HOOK RETRACT>" if self.tailhook_extended else " HOOK EXTEND>"
        mcdu.paint(10, 0, L, C, f"<TURB      {hook_label}")
        if self.system_armed:
            drop_label = "          STOP>" if self.dropping else "          DROP>"
        else:
            drop_label = "          STOP>" if self.dropping else "    DROP INHBT>"

        mcdu.paint(12, 0, L, C, f"<JETTISON{drop_label}")
        if self.scratchpad_text:
            mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)

    async def paintTurbulencePage(self, mcdu):
        """Paint the turbulence intensity settings page on the MCDU."""
        await asyncio.sleep(0.5)

        A = "amber"
        C = "cyan"
        L = "large"
        S = "small"

        enable_label = "<DISABLE" if self.turb_enable else "<ENABLE"
        title = "        BOUNCING        " if self.turb_enable else "      TURBULENCE        "

        mcdu.clear()
        #                          123456789012345678901234
        mcdu.paint(0, 0, S, A, title)
        mcdu.paint(2, 0, L, C, f"{enable_label:<24}")
        mcdu.paint(4, 0, L, C, "<SPD                YAW>")
        mcdu.paint(5, 0, S, C, f" {self.turb_int_spd:<21}{self.turb_int_yaw:>2}")
        mcdu.paint(6, 0, L, C, "<BANK              SINK>")
        mcdu.paint(7, 0, S, C, f" {self.turb_int_bank:<21}{self.turb_int_sink:>2}")
        mcdu.paint(8, 0, L, C, "<GUST                   ")
        mcdu.paint(9, 0, S, C, f" {self.turb_int_gust}")
        mcdu.paint(10, 0, L, C, "<MAXALT            RATE>")
        maxalt_str = "DISABLED" if self.turbulence_max_alt == 0 else str(self.turbulence_max_alt)
        mcdu.paint(11, 0, S, C, f" {maxalt_str:<20}{self.turbulence_events_per_minute:>3}")
        mcdu.paint(12, 0, L, C, "<BACK             RESET>")
        if self.scratchpad_text:
            mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)

    async def get_psx_connection_coro(self):  # pylint: disable=too-many-statements
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
            if self.mcduC:
                self.mcduC.unplug()
            for task in self.pending_paint_tasks.values():
                task.cancel()
            self.pending_paint_tasks.clear()
            self.active_mcdus.clear()

        def onresume():
            """Run when load3 is seen, i.e when we have a full set of variables."""
            self.logger.info("PSX RESUMED")
            self.psx.send("demand", "LeftPfdAlt")
            self.psx_connected = True
            self.psx_paused = False
            self.active_mcdus.clear()
            cdus = self.args.cdus.upper()
            side = self.args.menu_side.upper()
            row = self.args.menu_row
            text = "<TANK" if side == "L" else "TANK>"
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

            self.psx.subscribe("TrueZfw", self.psx_zfw_change)

            self.psx.subscribe("addon", self.addon_message_handler)

            # Needed to get elevation above terrain and TAS
            self.psx.subscribe("AcftHeight")
            self.psx.subscribe("PiBaHeAlTas")

            self.psx.subscribe("id")
            self.psx.subscribe("version", connected)

            self.psx.logger = self.logger.debug

            await self.psx.connect(
                self.args.psx_host,
                self.args.psx_port)
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

                name = "Turbulence"
                if name not in running:
                    self.logger.info("Starting %s...", name)
                    task = self.taskgroup.create_task(
                        self.turbulence_coro(), name=name)
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
            '--psx-host',
            type=str, action='store', default='127.0.0.1',
            help="Hostname or IP of the PSX main server or router to connect to.",
        )
        parser.add_argument(
            '--psx-port',
            type=int, action='store', default=10747,
            help="The port of the PSX main server or router to connect to.",
        )
        parser.add_argument(
            '--psx-main-server-host',
            type=str, action='store', default=None,
            help=argparse.SUPPRESS,
        )
        parser.add_argument(
            '--psx-main-server-port',
            type=int, action='store', default=None,
            help=argparse.SUPPRESS,
        )
        parser.add_argument(
            '--cdus',
            type=str, action='store', default='LR',
            help="Which CDUs to set up (any combination of L, R, C).",
        )
        parser.add_argument(
            '--menu-side',
            type=str, action='store', default='L', choices=['L', 'R'],
            help="Which side of the CDU menu to place the TANK entry on.",
        )
        parser.add_argument(
            '--menu-row',
            type=int, action='store', default=4,
            help="Row (1-6) of the CDU menu to place the TANK entry on.",
        )
        parser.add_argument(
            '--hook-min-height',
            type=float, action='store', default=30,
            help="The HOOK will not load water if your height is less than this (ft)",
        )
        parser.add_argument(
            '--hook-max-height',
            type=float, action='store', default=120,
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
            '--cdu-update-interval',
            type=float, action='store', default=1.0,
            help="How often we update the ZFW and CDU display"
        )
        parser.add_argument(
            '--oew',
            type=float, action='store', default=191420.0,
            help="OEW (kg)",
        )
        parser.add_argument(
            '--debug',
            action='store_true',
            help="Print more debug info. Probably only useful for development.",
        )
        self.args = parser.parse_args()
        if self.args.psx_main_server_host is not None:
            print("WARNING: --psx-main-server-host is deprecated, use --psx-host", file=sys.stderr)
            if self.args.psx_host == '127.0.0.1':
                self.args.psx_host = self.args.psx_main_server_host
        if self.args.psx_main_server_port is not None:
            print("WARNING: --psx-main-server-port is deprecated, use --psx-port", file=sys.stderr)
            if self.args.psx_port == 10747:
                self.args.psx_port = self.args.psx_main_server_port

        # The target retardant load (when loading)
        self.retardant_load_target = self.args.retardant_load_max

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
    try:
        asyncio.run(Script().run())
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            input("An error occurred, press Enter to continue...")
