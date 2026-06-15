# pylint: disable=invalid-name,too-many-lines
"""A script to simulate wind-generated turbulence for PSX."""

import argparse
import asyncio
import inspect
import logging
import random
import sys
import time
import traceback
from typing import Optional

import psx

from frankenturb import TurbulenceEngine, parse_pibahealtas  # pylint: disable=import-self
from frankenturb.boost import AccelerationComputer, AccelerationState, parse_boost_line
from frankenturb.cb import (
    find_nearest_cb, parse_wx_zone_basic, parse_wx_zone_position, parse_wx_clust,
)
from frankenturb.cb_turbulence import compute_cb_turbulence
from frankenturb.pirep import PirepFetcher, compute_pirep_turbulence
from frankenturb.cape import CapeFetcher, compute_cape_turbulence
from frankenturb.gairmet import GairmetFetcher, compute_gairmet_turbulence


__MYNAME__ = 'frankenturb'
__MY_CLIENT_ID__ = 'TURB'
__MY_DISPLAY_NAME__ = 'FrankenTurb'
__MY_DESCRIPTION__ = 'Wind-driven bumps'


def _isnan(v):
    """Return True if v is float NaN."""
    try:
        return v != v  # pylint: disable=comparison-with-itself
    except TypeError:
        return False


def _intensity_label(intensity):
    """Map 0–1 intensity to a human-readable severity label."""
    if intensity < 0.10:
        return "none"
    if intensity < 0.25:
        return "light"
    if intensity < 0.50:
        return "moderate"
    if intensity < 0.75:
        return "severe"
    return "extreme"


# WxBurst offsets per PSX type.
_BURST_SINK = 0
_BURST_BANK = 100
_BURST_YAW = 200
_BURST_SPD = 300
_BURST_GUST = 400

# CDU status display abbreviations.
_STATUS_KIND = {
    "none": "NONE", "wave": "WAVE", "rotor": "ROTR", "mechanical": "MECH",
    "shear": "SHER", "cb": "CB", "pirep": "PIRP", "cape": "CAPE",
    "gairmet": "GARM",
}
_STATUS_ABBR = {
    "none": "---", "light": "LGT", "moderate": "MOD",
    "severe": "SEV", "extreme": "EXT",
}


def _sign(v):
    """Return +1 or -1 from a float value."""
    return 1 if v >= 0.0 else -1


def _pick_burst(state, intensity):
    """Choose a (base_offset, direction, label) for one WxBurst event.

    For wave turbulence the directional components are deterministic, so we
    read their sign directly.  For random mechanisms (rotor, mechanical, shear)
    direction is random, but the type weights reflect the dominant physical
    effect: rotors are roll-heavy, mechanical is a broad mix, waves are mostly
    vertical.

    Wave candidates are intensity-tiered: real-world experience shows that
    airspeed (SPD) changes dominate at light intensities, with sink and bank
    becoming significant only at medium and severe levels.  SPD (300-series) is
    preferred over GUST (400-series) for the speed effect because it has a more
    visible impact on the PSX airspeed display.

    Returns (base_offset: int, direction: +1/-1, label: str).
    """
    r = random.choice

    if state.kind == 'wave':
        vert_dir = _sign(state.vertical) if not _isnan(state.vertical) else r([-1, 1])
        roll_dir = _sign(state.roll) if not _isnan(state.roll) else r([-1, 1])
        spd_dir = _sign(state.gust) if not _isnan(state.gust) else r([-1, 1])
        if intensity < 0.25:
            # Light: airspeed fluctuations only; sink/bank barely perceptible.
            # Real 747 experience: no instrument indication at light intensity.
            candidates = [
                (_BURST_SPD, spd_dir, "SPD", 6.0),
                (_BURST_GUST, spd_dir, "GUST", 1.0),
                (_BURST_SINK, vert_dir, "SINK", 0.1),
                (_BURST_BANK, roll_dir, "BANK", 0.1),
            ]
        elif intensity < 0.5:
            # Moderate: speed jolts dominate; VS moves barely, pitch 0.5-1 deg max.
            # Real 747 experience: speed jolts primary, tiny VS, almost no pitch change.
            candidates = [
                (_BURST_SPD, spd_dir, "SPD", 5.0),
                (_BURST_GUST, spd_dir, "GUST", 1.0),
                (_BURST_SINK, vert_dir, "SINK", 0.2),
                (_BURST_BANK, roll_dir, "BANK", 0.1),
            ]
        else:
            # Severe: SPD still leads; sink/updraft present but not dominant.
            candidates = [
                (_BURST_SPD, spd_dir, "SPD", 3.0),
                (_BURST_SINK, vert_dir, "SINK", 1.0),
                (_BURST_GUST, spd_dir, "GUST", 0.8),
                (_BURST_BANK, roll_dir, "BANK", 0.5),
            ]
    elif state.kind == 'rotor':
        # Rotors are roll-dominant and highly chaotic.
        candidates = [
            (_BURST_BANK, r([-1, 1]), "BANK", 3.0),
            (_BURST_SINK, r([-1, 1]), "SINK", 1.5),
            (_BURST_YAW, r([-1, 1]), "YAW", 1.0),
            (_BURST_GUST, r([-1, 1]), "GUST", 0.5),
        ]
    elif state.kind == 'mechanical':
        # Broad chaotic mix — vertical and roll roughly equal.
        candidates = [
            (_BURST_SINK, r([-1, 1]), "SINK", 1.5),
            (_BURST_BANK, r([-1, 1]), "BANK", 1.5),
            (_BURST_SPD, r([-1, 1]), "SPD", 1.0),
            (_BURST_YAW, r([-1, 1]), "YAW", 0.5),
        ]
    elif state.kind == 'cb':
        # CBs are dominated by violent up/downdrafts and roll; large SPD changes.
        candidates = [
            (_BURST_SINK, r([-1, 1]), "SINK", 3.0),
            (_BURST_BANK, r([-1, 1]), "BANK", 2.0),
            (_BURST_SPD, r([-1, 1]), "SPD", 1.5),
            (_BURST_YAW, r([-1, 1]), "YAW", 0.5),
        ]
    elif state.kind == 'cape':
        # CAPE convective turbulence: strong updrafts/downdrafts with bank.
        candidates = [
            (_BURST_SINK, r([-1, 1]), "SINK", 2.5),
            (_BURST_BANK, r([-1, 1]), "BANK", 1.5),
            (_BURST_SPD, r([-1, 1]), "SPD", 1.0),
            (_BURST_YAW, r([-1, 1]), "YAW", 0.3),
        ]
    elif state.kind == 'gairmet':
        # G-AIRMET: jet-stream CAT and mountain wave — airspeed-heavy, some sink.
        candidates = [
            (_BURST_SPD, r([-1, 1]), "SPD", 2.0),
            (_BURST_SINK, r([-1, 1]), "SINK", 1.5),
            (_BURST_BANK, r([-1, 1]), "BANK", 1.0),
            (_BURST_YAW, r([-1, 1]), "YAW", 0.5),
        ]
    else:  # shear, shear+*, pirep, none
        candidates = [
            (_BURST_SINK, r([-1, 1]), "SINK", 1.0),
            (_BURST_BANK, r([-1, 1]), "BANK", 1.0),
            (_BURST_YAW, r([-1, 1]), "YAW", 1.0),
            (_BURST_SPD, r([-1, 1]), "SPD", 1.0),
        ]

    total = sum(w for *_, w in candidates)
    pick = random.random() * total
    cumulative = 0.0
    for base, direction, label, weight in candidates:
        cumulative += weight
        if pick <= cumulative:
            return base, direction, label
    base, direction, label, _ = candidates[-1]
    return base, direction, label


def _parse_psx_wind(wx_str):
    """Parse surface wind from a PSX Wx* weather string.

    The 19th semicolon-separated field encodes VVVDDSS where VVV is
    variability (ignored), DD is direction in tens of degrees, and SS is
    speed in knots.

    Parameters
    ----------
    wx_str :
        Raw value from PSX WxBasic or Wx1–Wx7.

    Returns
    -------
    tuple[int, int] | None
        (direction_deg, speed_kt) or None if parsing fails.

    """
    parts = wx_str.strip().split(';')
    if len(parts) < 19:
        return None
    wind_field = parts[18]
    if len(wind_field) < 7:
        return None
    try:
        dir_deg = (int(wind_field[3:5]) * 10) % 360
        speed_kt = int(wind_field[5:7])
        return dir_deg, speed_kt
    except (ValueError, IndexError):
        return None


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
        self.psx_paused = False

        self.turb_enabled = True
        self.intensity_bias = 100
        self.wind_mode = "live"    # "live", "psx", or "manual"
        self.manual_wind_dir = 0   # degrees — stored even when mode is not "manual"
        self.manual_wind_spd = 0   # knots
        self.psx_wind = None       # (dir_deg, speed_kt) from last PSX fetch, or None
        self.mcduL = None
        self.mcduR = None
        self.mcduC = None
        self.active_mcdus = []
        self.pending_paint_tasks = {}
        self.scratchpad_text = ''
        self.repaint_req_by = set()

        self.turb_int_spd = 0
        self.turb_int_yaw = 0
        self.turb_int_bank = 0
        self.turb_int_sink = 0
        self.turb_int_gust = 0
        self.turbulence_events_per_minute = 5

        self.engine = TurbulenceEngine()
        self._turb_print_count = 0

        self.type_biases = {
            'wave': 100, 'rotor': 100, 'mechanical': 100,
            'shear': 100, 'cb': 100, 'pirep': 100, 'cape': 100, 'gairmet': 100,
        }
        self.type_enabled = {
            'wave': True, 'rotor': True, 'mechanical': True, 'shear': True,
            'cb': True, 'pirep': True, 'cape': True, 'gairmet': True,
        }
        self.pirep_fetcher = PirepFetcher()
        self.cape_fetcher = CapeFetcher()
        self.gairmet_fetcher = GairmetFetcher()
        self.lateral_size_bias = 50  # % of nearest-neighbour zone radius; tune to match radar

        self._cdu_page = 0  # 0=main, 1=types, 2=intens
        self._cdu_sources_snapshot = []  # list of (src_eff, TurbulenceState, is_winner)
        self._cdu_update_kind = "none"
        self._cdu_update_intensity = 0.0
        self._cdu_last_main_update = 0.0

        self.latest_accel_state: Optional[AccelerationState] = None

    def psx_send_and_set(self, psx_variable, new_psx_value):
        """Send variable to PSX and store in local db."""
        self.psx.send(psx_variable, new_psx_value)
        self.psx._set(psx_variable, new_psx_value)  # pylint: disable=protected-access

    async def repaint_all_mcdus(self):
        """Trigger a repaint of all active MCDUs, cancelling any pending paint tasks first."""
        self.logger.debug("Refreshing all active MCDUs, requested by: %s", self.repaint_req_by)
        page_fn = {0: self.paintMainPage, 1: self.paintTypesPage, 2: self.paintIntensPage}
        paint = page_fn.get(self._cdu_page, self.paintMainPage)
        for mcdu in self.active_mcdus:
            existing = self.pending_paint_tasks.get(mcdu)
            if existing and not existing.done():
                existing.cancel()
            self.pending_paint_tasks[mcdu] = asyncio.create_task(paint(mcdu))
        self.repaint_req_by = set()

    def _type_effective_bias(self, kind):
        """Return the combined type bias: 0 when disabled, else type_biases value."""
        if not self.type_enabled.get(kind, True):
            return 0
        return self.type_biases.get(kind, 100)

    def _enter_bias(self, mcdu):
        """Process scratchpad as an intensity bias percentage (0–999) and apply it."""
        if not self.scratchpad_text:
            return
        try:
            v = int(self.scratchpad_text)
            if 0 <= v <= 999:
                self.intensity_bias = v
                self.scratchpad_text = ''
                mcdu.paint(13, 0, "large", "white", " " * 24)
                self.repaint_req_by.add("bias-set")
        except ValueError:
            pass

    def _enter_lat_size_bias(self, mcdu):
        """Process scratchpad as a CB lateral size percentage (0–999) and apply it."""
        if not self.scratchpad_text:
            return
        try:
            v = int(self.scratchpad_text)
            if 0 <= v <= 999:
                self.lateral_size_bias = v
                self.scratchpad_text = ''
                mcdu.paint(13, 0, "large", "white", " " * 24)
                self.repaint_req_by.add("lat-size-bias-set")
        except ValueError:
            pass

    def _enter_type_bias(self, mcdu, kind):
        """Process scratchpad as a type-specific bias percentage (0–999) and apply it."""
        if not self.scratchpad_text:
            return
        try:
            v = int(self.scratchpad_text)
            if 0 <= v <= 999:
                self.type_biases[kind] = v
                self.scratchpad_text = ''
                mcdu.paint(13, 0, "large", "white", " " * 24)
                self.repaint_req_by.add(f"type-bias-{kind}")
        except ValueError:
            pass

    def _load_config(self, path):
        """Load settings from a JSON config file, silently ignoring missing keys."""
        import json  # pylint: disable=import-outside-toplevel
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except FileNotFoundError:
            import pathlib  # pylint: disable=import-outside-toplevel
            p = pathlib.Path(path)
            if p.parent.exists():
                print(f"Config file {path} not found, creating empty file")
                try:
                    p.write_text("{}\n", encoding="utf-8")
                except Exception as exc2:  # pylint: disable=broad-except
                    print(f"Could not create config file: {exc2}")
            else:
                print(f"Config file {path} not found, using defaults")
            return
        except Exception as exc:  # pylint: disable=broad-except
            print(f"Config file load failed: {exc}")
            return

        if "turb_enabled" in cfg:
            self.turb_enabled = bool(cfg["turb_enabled"])
        if "intensity_bias" in cfg:
            self.intensity_bias = int(cfg["intensity_bias"])
        if "lateral_size_bias" in cfg:
            self.lateral_size_bias = int(cfg["lateral_size_bias"])
        for kind in self.type_biases:
            if "type_biases" in cfg and kind in cfg["type_biases"]:
                self.type_biases[kind] = int(cfg["type_biases"][kind])
            if "type_enabled" in cfg and kind in cfg["type_enabled"]:
                self.type_enabled[kind] = bool(cfg["type_enabled"][kind])
        log = self.logger or __import__('logging').getLogger(__MYNAME__)
        log.info("Loaded config from %s", path)

    def _save_config(self, path):
        """Save current settings to a JSON config file."""
        import json  # pylint: disable=import-outside-toplevel
        cfg = {
            "turb_enabled": self.turb_enabled,
            "intensity_bias": self.intensity_bias,
            "lateral_size_bias": self.lateral_size_bias,
            "type_biases": dict(self.type_biases),
            "type_enabled": dict(self.type_enabled),
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(cfg, fh, indent=2)
                fh.write("\n")
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.warning("Config file save failed: %s", exc)
            return
        self.logger.info("Saved config to %s", path)

    def _cycle_wind_mode(self):
        """Cycle the wind source: live → psx → manual → live."""
        if self.wind_mode == "live":
            self.wind_mode = "psx"
            self.engine.clear_fixed_wind()
            if self.psx_connected:
                self._update_psx_wind()
        elif self.wind_mode == "psx":
            self.wind_mode = "manual"
            self.engine.set_fixed_wind(float(self.manual_wind_dir), float(self.manual_wind_spd))
        else:
            self.wind_mode = "live"
            self.engine.clear_fixed_wind()
        self.repaint_req_by.add("wind-src-toggle")

    def _apply_manual_wind(self, mcdu):
        """Apply scratchpad DDD/SS as the manual wind setting."""
        if not self.scratchpad_text:
            return
        try:
            parts = self.scratchpad_text.split('/')
            if len(parts) != 2:
                return
            dir_deg = int(parts[0]) % 360
            speed_kt = int(parts[1])
            if 0 <= speed_kt <= 300:
                self.manual_wind_dir = dir_deg
                self.manual_wind_spd = speed_kt
                if self.wind_mode == "manual":
                    self.engine.set_fixed_wind(float(dir_deg), float(speed_kt))
                self.scratchpad_text = ''
                mcdu.paint(13, 0, "large", "white", " " * 24)
                self.repaint_req_by.add("manual-wind-set")
        except ValueError:
            pass

    def _update_psx_wind(self):
        """Read the focused PSX weather zone and update the fixed wind profile."""
        zone_str = self.psx.get("FocussedWxZone")
        zone = 0
        if zone_str is not None:
            try:
                zone = int(zone_str)
            except ValueError:
                pass
        wx_var = "WxBasic" if zone == 0 else f"Wx{zone}"
        wx_str = self.psx.get(wx_var)
        if not wx_str:
            self.logger.debug("PSX wind: %s not available", wx_var)
            return
        result = _parse_psx_wind(wx_str)
        if result is None:
            self.logger.warning("PSX wind: could not parse %s=%r", wx_var, wx_str)
            return
        dir_deg, speed_kt = result
        self.logger.info("PSX wind: zone=%d %s → %03d°/%dkt", zone, wx_var, dir_deg, speed_kt)
        self.psx_wind = (dir_deg, speed_kt)
        self.engine.set_fixed_wind(float(dir_deg), float(speed_kt))
        self.repaint_req_by.add("psx-wind-update")
        asyncio.create_task(self.repaint_all_mcdus())

    async def psx_wind_coro(self):
        """Refresh the PSX weather zone wind every 30 seconds when PSX wind mode is active."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(30.0)
                if self.wind_mode == "psx" and self.psx_connected:
                    self._update_psx_wind()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    def _handle_keypress(self, mcdu, value):
        """Dispatch a single CDU keypress based on the current page."""
        if value == "CLR":
            self._handle_clr(mcdu)
        elif value == "DEL":
            self.scratchpad_text = self.scratchpad_text[:-1]
            hint = "     CLR=RESET".ljust(24)
            if self.scratchpad_text:
                mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text.ljust(24))
            else:
                mcdu.paint(13, 0, "small", "amber", hint)
        elif value in ('0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '/', '+/-'):
            if len(self.scratchpad_text) < 10:
                self.scratchpad_text += value
                mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)
        elif self._cdu_page == 0:
            self._handle_main_keypress(mcdu, value)
        elif self._cdu_page == 1:
            self._handle_types_keypress(mcdu, value)
        elif self._cdu_page == 2:
            self._handle_intens_keypress(mcdu, value)

    def _handle_clr(self, mcdu):
        """CLR key: clear scratchpad, or RESET all settings when scratchpad is empty."""
        if self.scratchpad_text:
            self.scratchpad_text = ''
            mcdu.paint(13, 0, "small", "amber", "     CLR=RESET".ljust(24))
        else:
            self.turb_enabled = True
            self.intensity_bias = 100
            self.type_biases = {
                'wave': 100, 'rotor': 100, 'mechanical': 100,
                'shear': 100, 'cb': 100, 'pirep': 100, 'cape': 100, 'gairmet': 100,
            }
            self.type_enabled = {
                'wave': True, 'rotor': True, 'mechanical': True, 'shear': True,
                'cb': True, 'pirep': True, 'cape': True, 'gairmet': True,
            }
            self.lateral_size_bias = 50
            self.wind_mode = "live"
            self.psx_wind = None
            self.engine.clear_fixed_wind()
            self.repaint_req_by.add("reset")

    def _handle_main_keypress(self, mcdu, value):  # pylint: disable=too-many-branches
        """Handle key presses on the main page."""
        if value == "1L":
            self.turb_enabled = not self.turb_enabled
            self.repaint_req_by.add("enable-toggle")
        elif value == "1R":
            self._enter_bias(mcdu)
        elif value == "2L":
            self._cycle_wind_mode()
        elif value == "2R":
            self._apply_manual_wind(mcdu)
        elif value == "5L":
            self._cdu_page = 1
            self.repaint_req_by.add("page-types")
        elif value == "5R":
            self._cdu_page = 2
            self.repaint_req_by.add("page-intens")
        elif value == "6L" and self.args.config_file:
            self._save_config(self.args.config_file)
            self.repaint_req_by.add("cfg-saved")
        elif value == "6R" and self.args.config_file:
            self._load_config(self.args.config_file)
            self.repaint_req_by.add("cfg-loaded")

    def _handle_types_keypress(self, _mcdu, value):  # pylint: disable=unused-argument
        """Handle key presses on the type enable/disable subpage."""
        _toggle_map = {
            '1L': 'wave', '1R': 'rotor',
            '2L': 'mechanical', '2R': 'shear',
            '3L': 'cb', '3R': 'pirep',
            '4L': 'cape', '4R': 'gairmet',
        }
        if value in _toggle_map:
            kind = _toggle_map[value]
            self.type_enabled[kind] = not self.type_enabled[kind]
            self.repaint_req_by.add(f"type-toggle-{kind}")
        elif value == "6L":
            self._cdu_page = 0
            self.scratchpad_text = ''
            self.repaint_req_by.add("page-main")

    def _handle_intens_keypress(self, mcdu, value):  # pylint: disable=too-many-branches
        """Handle key presses on the intensity subpage."""
        _bias_map = {
            '1L': 'wave', '1R': 'rotor',
            '2L': 'mechanical', '2R': 'shear',
            '3L': 'cb', '3R': 'pirep',
            '4L': 'cape', '4R': 'gairmet',
        }
        if value in _bias_map:
            self._enter_type_bias(mcdu, _bias_map[value])
        elif value == "5L":
            self._enter_lat_size_bias(mcdu)
        elif value == "6L":
            self._cdu_page = 0
            self.scratchpad_text = ''
            self.repaint_req_by.add("page-main")

    def mcduEvent(self, mcdu, event_type, value=None):
        """Handle CDU C key events."""
        self.logger.debug("MCDU event from %s: %s=%s", mcdu.location, event_type, value)
        if event_type in ["logon", "resume"]:
            asyncio.create_task(self.repaint_all_mcdus())
        elif event_type == "keypress":
            self.repaint_req_by = set()
            self._handle_keypress(mcdu, value)
            if self.repaint_req_by:
                asyncio.create_task(self.repaint_all_mcdus())
        else:
            self.logger.debug(
                "Unhandled MCDU event from %s: %s=%s", mcdu.location, event_type, value)

    def _paint_cdu_source_rows(self, mcdu):  # pylint: disable=too-many-locals
        """Paint rows 5–8 on the main page with the current turbulence source summary."""
        A = "amber"
        C = "cyan"
        S = "small"
        for row_idx in range(4):
            row = 5 + row_idx
            if row_idx < len(self._cdu_sources_snapshot):
                src_eff, src_s, is_winner = self._cdu_sources_snapshot[row_idx]
                marker = ">" if is_winner else " "
                kind = _STATUS_KIND.get(src_s.kind, src_s.kind[:4].upper())
                eff_pct = int(src_eff * 100)
                raw = (src_s.reason or "").upper()
                reason = ''.join(c for c in raw if ord(c) < 128)[:13]
                line = f"{marker}{kind:<4} {eff_pct:3d}% {reason}"
                color = C if is_winner else A
                mcdu.paint(row, 0, S, color, line[:24].ljust(24))
            else:
                mcdu.paint(row, 0, S, A, " " * 24)

    async def paintMainPage(self, mcdu):
        """Paint the main status and control page on the MCDU."""
        await asyncio.sleep(0.5)

        A = "amber"
        C = "cyan"
        L = "large"
        S = "small"

        title = "   TURB ACTIVE          " if self.turb_enabled else "   FRANKENTURB          "
        enable_label = "<DISABLE" if self.turb_enabled else "<ENABLE"
        bias_str = f"{self.intensity_bias}%>"

        if self.wind_mode == "live":
            src_str = "<LIVE"
        elif self.wind_mode == "psx":
            src_str = "<PSX"
        else:
            src_str = "<MANUAL"

        mcdu.clear()
        #                          123456789012345678901234
        mcdu.paint(0, 0, S, A, title)
        mcdu.paint(1, 0, S, A, "TURB")
        mcdu.paint(1, 15, S, A, "INT BIAS>")
        mcdu.paint(2, 0, L, C, f"{enable_label:<12}{bias_str:>12}")
        mcdu.paint(3, 0, S, A, "WIND SRC")
        if self.wind_mode == "manual":
            mcdu.paint(3, 19, S, A, "WIND>")
            wind_str = f"{self.manual_wind_dir:03d}/{self.manual_wind_spd:03d}KT>"
            mcdu.paint(4, 0, L, C, f"{src_str:<12}{wind_str:>12}")
        else:
            mcdu.paint(4, 0, L, C, src_str)
        self._paint_cdu_source_rows(mcdu)
        mcdu.paint(10, 0, L, C, "<TYPES")
        mcdu.paint(10, 17, L, C, "CONFIG>")
        mcdu.paint(11, 6, S, A, "CLR TO RESET")
        if self.args.config_file:
            mcdu.paint(12, 0, L, C, "<SAVE")
            mcdu.paint(12, 19, L, C, "LOAD>")
        if self.scratchpad_text:
            mcdu.paint(13, 0, L, "magenta", self.scratchpad_text)

    async def paintTypesPage(self, mcdu):
        """Paint the turbulence type enable/disable subpage."""
        await asyncio.sleep(0.5)

        A = "amber"
        C = "cyan"
        L = "large"
        S = "small"

        def _en(kind):
            return "ON " if self.type_enabled.get(kind, True) else "OFF"

        mcdu.clear()
        mcdu.paint(0, 0, S, A, "  TYPE ENABLE/DISABLE   ")
        mcdu.paint(1, 0, S, A, "WAVE")
        mcdu.paint(1, 18, S, A, "ROTOR>")
        mcdu.paint(2, 0, L, C, f"<{_en('wave'):<11}{_en('rotor'):>10}>")
        mcdu.paint(3, 0, S, A, "MECHANICAL")
        mcdu.paint(3, 18, S, A, "SHEAR>")
        mcdu.paint(4, 0, L, C, f"<{_en('mechanical'):<11}{_en('shear'):>10}>")
        mcdu.paint(5, 0, S, A, "CB")
        mcdu.paint(5, 18, S, A, "PIREP>")
        mcdu.paint(6, 0, L, C, f"<{_en('cb'):<11}{_en('pirep'):>10}>")
        mcdu.paint(7, 0, S, A, "CAPE")
        mcdu.paint(7, 16, S, A, "GAIRMET>")
        mcdu.paint(8, 0, L, C, f"<{_en('cape'):<11}{_en('gairmet'):>10}>")
        mcdu.paint(11, 0, S, A, "BACK")
        mcdu.paint(12, 0, L, C, "<BACK")
        if self.scratchpad_text:
            mcdu.paint(13, 0, L, "magenta", self.scratchpad_text)

    async def paintIntensPage(self, mcdu):
        """Paint the per-type intensity bias subpage."""
        await asyncio.sleep(0.5)

        A = "amber"
        C = "cyan"
        L = "large"
        S = "small"

        def _b(kind):
            return f"{self.type_biases.get(kind, 100)}%"

        mcdu.clear()
        mcdu.paint(0, 0, S, A, "  TYPE INTENSITIES      ")
        mcdu.paint(1, 0, S, A, "WAVE")
        mcdu.paint(1, 18, S, A, "ROTOR>")
        mcdu.paint(2, 0, L, C, f"<{_b('wave'):<11}{_b('rotor'):>10}>")
        mcdu.paint(3, 0, S, A, "MECHANICAL")
        mcdu.paint(3, 18, S, A, "SHEAR>")
        mcdu.paint(4, 0, L, C, f"<{_b('mechanical'):<11}{_b('shear'):>10}>")
        mcdu.paint(5, 0, S, A, "CB")
        mcdu.paint(5, 18, S, A, "PIREP>")
        mcdu.paint(6, 0, L, C, f"<{_b('cb'):<11}{_b('pirep'):>10}>")
        mcdu.paint(7, 0, S, A, "CAPE")
        mcdu.paint(7, 16, S, A, "GAIRMET>")
        mcdu.paint(8, 0, L, C, f"<{_b('cape'):<11}{_b('gairmet'):>10}>")
        mcdu.paint(9, 0, S, A, "CB LAT SIZE")
        mcdu.paint(10, 0, L, C, f"<{str(self.lateral_size_bias) + '%':<11}")
        mcdu.paint(11, 0, S, A, "BACK")
        mcdu.paint(12, 0, L, C, "<BACK")
        if self.scratchpad_text:
            mcdu.paint(13, 0, L, "magenta", self.scratchpad_text)

    def _get_nearest_cb(self, lat: float, lon: float):
        """Collect CB data from PSX and return the nearest active storm cell.

        Reads WxMode1–7 (zone positions), Wx1–7 and WxBasic (CB profiles),
        WxClust (planet-weather cells), and TimeEarth (simulation clock)
        from the PSX client cache, then delegates to find_nearest_cb.
        Returns a CbInfo instance or None when no CBs are active.
        """
        raw_time = self.psx.get("TimeEarth")
        try:
            time_earth_ms = int(raw_time) if raw_time else int(time.time() * 1000)
        except ValueError:
            time_earth_ms = int(time.time() * 1000)

        zone_positions = {}
        for zone_i in range(1, 8):
            raw = self.psx.get(f"WxMode{zone_i}")
            if raw:
                pos = parse_wx_zone_position(raw)
                if pos is not None:
                    zone_positions[zone_i] = pos

        zone_cb_data = {}
        planet_raw = self.psx.get("WxBasic")
        if planet_raw:
            planet_cb = parse_wx_zone_basic(planet_raw)
            if planet_cb is not None:
                zone_cb_data[0] = planet_cb
        for zone_i in range(1, 8):
            raw = self.psx.get(f"Wx{zone_i}")
            if raw:
                cb_data = parse_wx_zone_basic(raw)
                if cb_data is not None:
                    zone_cb_data[zone_i] = cb_data

        clust_raw = self.psx.get("WxClust")
        clust_positions = parse_wx_clust(clust_raw) if clust_raw else []

        return find_nearest_cb(lat, lon, zone_positions, zone_cb_data,
                               clust_positions, time_earth_ms,
                               lat_scale=self.lateral_size_bias / 100.0)

    async def turbulence_coro(self):  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
        """Compute turbulence, inject WxBurst events into PSX, and log state."""
        myname = inspect.currentframe().f_code.co_name
        last_print = 0.0
        try:
            self.logger.debug("Starting %s", myname)
            loop = asyncio.get_running_loop()
            while True:
                await asyncio.sleep(0.2)

                if not self.psx_connected or self.psx_paused:
                    self.logger.debug("PSX not yet connected or paused, %s sleeping",
                                      myname)
                    continue

                raw = self.psx.get("PiBaHeAlTas")
                if not raw:
                    continue

                try:
                    _, _, _, alt_ft, tas_kt, lat, lon = parse_pibahealtas(raw)
                except ValueError as exc:
                    self.logger.warning("Bad PiBaHeAlTas: %s", exc)
                    continue

                # Suppress turbulence below 30 kt ground speed.
                # Use boost-derived ground speed when available, TAS as fallback.
                accel = self.latest_accel_state
                ground_speed_kt = (accel.ground_speed_kt
                                   if accel is not None else tas_kt)
                if ground_speed_kt < 30.0:
                    continue

                # Wind/terrain, PIREP, CAPE, and G-AIRMET fetches may all do
                # HTTP on first call.  Run them in parallel threads so the
                # event loop stays responsive.
                state, pirep_rec, cape_sample, gairmet_region = await asyncio.gather(
                    loop.run_in_executor(None, self.engine.compute, lat, lon, alt_ft),
                    loop.run_in_executor(
                        None, self.pirep_fetcher.find_relevant, lat, lon, alt_ft),
                    loop.run_in_executor(None, self.cape_fetcher.get, lat, lon),
                    loop.run_in_executor(
                        None, self.gairmet_fetcher.get_active, lat, lon, alt_ft),
                )

                # Save terrain result before non-terrain sources may override state.
                terrain_state = state

                # Bias-adjusted intensity for source comparison.  type_enabled
                # and type_biases are combined; division by 100 cancels in
                # relative comparison so we just multiply for cheaper math.
                def _eff(s):
                    return s.intensity * self._type_effective_bias(s.kind)

                # CB proximity turbulence (fast — no I/O, just PSX cache + math).
                cb = self._get_nearest_cb(lat, lon)
                cb_state = None
                if cb is not None:
                    cb_state = compute_cb_turbulence(alt_ft, cb)
                    if _eff(cb_state) > _eff(state):
                        state = cb_state

                # PIREP turbulence — whichever source is most intense governs.
                pirep_state = None
                if pirep_rec is not None:
                    pirep_state = compute_pirep_turbulence(alt_ft, pirep_rec)
                    if _eff(pirep_state) > _eff(state):
                        state = pirep_state

                # CAPE convective turbulence.
                cape_state = None
                if cape_sample is not None:
                    cape_state = compute_cape_turbulence(alt_ft, cape_sample)
                    if _eff(cape_state) > _eff(state):
                        state = cape_state

                # G-AIRMET declared turbulence area.
                gairmet_state = None
                if gairmet_region is not None:
                    gairmet_state = compute_gairmet_turbulence(alt_ft, gairmet_region)
                    if _eff(gairmet_state) > _eff(state):
                        state = gairmet_state

                # --- Inject WxBurst into PSX ------------------------------------
                # intensity_bias and the per-kind type_bias both scale intensity:
                # each is 0–999 % (100 = 1×).  Combined: bias×type/10000.
                # WxBurst magnitude is capped at 99 (PSX maximum).
                effective_intensity = min(
                    1.0,
                    state.intensity * self.intensity_bias *
                    self._type_effective_bias(state.kind) / 10000.0,
                )
                if self.turb_enabled and effective_intensity >= 0.01:
                    inject_prob = (effective_intensity ** 0.5) * (self.args.rate / 100.0)
                    if random.random() < inject_prob:
                        base, direction, label = _pick_burst(state, effective_intensity)
                        raw_mag = random.randint(1, max(1, int(effective_intensity * 99)))
                        magnitude = min(99, raw_mag)
                        psx_value = direction * (base + magnitude)
                        self.psx_send_and_set("WxBurst", str(psx_value))
                        self.logger.debug("Injected WxBurst=%d (%s%s%02d)",
                                          psx_value, label,
                                          '+' if direction > 0 else '-', magnitude)

                # --- Build sorted source list (console + CDU) ------------------
                all_sources = []
                for src_s in [
                    terrain_state, cb_state, pirep_state, cape_state, gairmet_state
                ]:
                    if src_s is None or src_s.intensity < 0.01:
                        continue
                    src_eff = min(1.0, src_s.intensity * self.intensity_bias *
                                  self._type_effective_bias(src_s.kind) / 10000.0)
                    if src_eff < 0.01:
                        continue
                    all_sources.append((src_eff, src_s))
                all_sources.sort(key=lambda t: t[0], reverse=True)

                # --- Update CDU main page source rows --------------------------
                # Refresh at most every 30 s, and only when something changed.
                now_mono = time.monotonic()
                kind_changed = state.kind != self._cdu_update_kind
                intens_changed = abs(effective_intensity - self._cdu_update_intensity) > 0.05
                sources_changed = kind_changed or intens_changed
                time_due = sources_changed and now_mono - self._cdu_last_main_update >= 30.0
                if time_due:
                    self._cdu_update_kind = state.kind
                    self._cdu_update_intensity = effective_intensity
                    self._cdu_last_main_update = now_mono
                    self._cdu_sources_snapshot = [
                        (e, s, s is state) for e, s in all_sources
                    ]
                    if self._cdu_page == 0:
                        for mcdu in self.active_mcdus:
                            self._paint_cdu_source_rows(mcdu)

                # --- Throttle console output to once per second -----------------
                now = time.monotonic()
                if now - last_print < 1.0:
                    continue
                last_print = now

                intensity_label = _intensity_label(effective_intensity)
                enabled_str = "ON " if self.turb_enabled else "OFF"
                if effective_intensity >= 0.01:
                    kind_str = state.kind
                    vert_str = f"{state.vertical:+.2f}" if not _isnan(state.vertical) else "rand"
                    roll_str = f"{state.roll:+.2f}" if not _isnan(state.roll) else "rand"
                    gust_str = f"{state.gust:+.2f}" if not _isnan(state.gust) else "rand"
                else:
                    kind_str = "none"
                    vert_str = roll_str = gust_str = "---"

                if self._turb_print_count % 20 == 0:
                    self.logger.info(
                        "--- Turbulence %s", "-" * 73)
                    self.logger.info(
                        "     [   ] Position                    Type        Severity          "
                        "Directional components")
                    self.logger.info(
                        "     [   ] lat(°)   lon(°)   alt(ft)  kind        label      (0-1)  "
                        "vert              roll               gust")
                    self.logger.info(
                        "     [   ]                                         none/light/        "
                        "-1=sink  +1=updft  -1=left  +1=right  -1=headwnd  +1=tailwnd")
                    self.logger.info(
                        "     [   ]                                         mod/severe/ext     "
                        "'rand'=random noise at given level")
                    self.logger.info(
                        "--- Turbulence %s", "-" * 73)
                self._turb_print_count += 1

                self.logger.info(
                    "Turbulence [%s] lat=%.3f lon=%.3f alt=%.0fft | "
                    "%-10s | %-8s (%.2f) | vert=%s roll=%s gust=%s",
                    enabled_str, lat, lon, alt_ft,
                    kind_str,
                    intensity_label, effective_intensity,
                    vert_str, roll_str, gust_str,
                )
                for src_eff, src in all_sources:
                    marker = ">" if (src is state) else " "
                    self.logger.info(
                        "           [%s%-10s] %.2f %s",
                        marker, src.kind, src_eff, src.reason or "")
                    if src.kind == 'cb' and cb is not None:
                        self.logger.info(
                            "           [  cb-geo   ] %s brg=%03.0f° rng=%.0fnm "
                            "edge=%+.0fnm base=%.0fft top=%.0fft cov=%d",
                            cb.source, cb.bearing_deg, cb.range_center_nm,
                            cb.range_edge_nm, cb.cloud_base_ft_msl,
                            cb.cloud_top_ft_msl, cb.coverage)
                if self.args.accelerations and accel is not None:
                    self.logger.info(
                        "           [acc] heave=%+.2fG surge=%+.2fG sway=%+.2fG "
                        "| roll=%+.1f°/s pitch=%+.1f°/s yaw=%+.1f°/s | gs=%.0fkt",
                        accel.heave_g, accel.surge_g, accel.sway_g,
                        accel.roll_rate_dps, accel.pitch_rate_dps, accel.yaw_rate_dps,
                        accel.ground_speed_kt)

        except Exception as exc:  # pylint:disable=broad-exception-caught
            self.logger.critical(
                "Unhandled exception %s in %s, shutting down",
                exc, myname)
            self.logger.critical(traceback.format_exc())

    async def _run_boost_session(self, reader, writer):
        """Process lines from one boost server connection until it drops."""
        accel_computer = AccelerationComputer()
        try:
            while True:
                raw_line = await reader.readline()
                if not raw_line:
                    break
                sample = parse_boost_line(raw_line.decode('ascii', errors='ignore'))
                if sample is None:
                    continue
                accel = accel_computer.update(sample)
                if accel is not None:
                    self.latest_accel_state = accel
        except (asyncio.IncompleteReadError, ConnectionResetError, OSError):
            pass
        finally:
            writer.close()
            self.latest_accel_state = None
            self.logger.info("Boost server disconnected")

    async def boost_connection_coro(self):
        """Maintain a connection to the PSX boost server and compute accelerations."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    reader, writer = await asyncio.open_connection(
                        self.args.boost_server_host,
                        self.args.boost_server_port)
                    self.logger.info(
                        "Boost server connected at %s:%d",
                        self.args.boost_server_host, self.args.boost_server_port)
                    await self._run_boost_session(reader, writer)
                except (ConnectionRefusedError, OSError) as exc:
                    self.logger.warning(
                        "Boost server not available (%s), retrying in 5s", exc)
                    await asyncio.sleep(5.0)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

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
            for mcdu in [self.mcduL, self.mcduR, self.mcduC]:
                if mcdu:
                    mcdu.unplug()
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
            if not self.args.no_cdu_interface:
                cdus = self.args.cdus.upper()
                side = self.args.menu_side.upper()
                row = self.args.menu_row
                text = "<TURB" if side == "L" else "TURB>"
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

            # Needed to get elevation above terrain and TAS
            self.psx.subscribe("AcftHeight")
            self.psx.subscribe("PiBaHeAlTas")

            # Needed for PSX wind mode
            self.psx.subscribe("FocussedWxZone")
            self.psx.subscribe("WxBasic")
            for _i in range(1, 8):
                self.psx.subscribe(f"Wx{_i}")

            # Needed for CB proximity detection
            self.psx.subscribe("TimeEarth")
            self.psx.subscribe("WxClust")
            for _i in range(1, 8):
                self.psx.subscribe(f"WxMode{_i}")

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

    def _start_task(self, running, name, coro_fn):
        """Start a named task if it is not already in the running list."""
        if name not in running:
            self.logger.info("Starting %s...", name)
            task = self.taskgroup.create_task(coro_fn(), name=name)
            self.tasks.add(task)
            self.logger.info("Started %s.", name)

    async def monitor_coro(self):
        """Monitor the coroutines and start/restart as needed."""
        try:
            self.logger.debug("Starting %s", inspect.currentframe().f_code.co_name)
            while True:
                running = []
                tasks_ended = set()
                for task in self.tasks:
                    if not task.done():
                        running.append(task.get_name())
                        continue
                    tasks_ended.add(task)
                    if task.cancelled():
                        self.logger.info("Task %s was cancelled", task.get_name())
                    elif task.exception() is None:
                        self.logger.info("Task %s ended peacefully", task.get_name())
                    else:
                        self.logger.info("Task: %s has ended: %s",
                                         task.get_name(), task.exception())
                # Cleanup
                for task in tasks_ended:
                    self.logger.debug("Removing %s from task list", task)
                    self.tasks.discard(task)

                # Ensure the tasks are running
                self._start_task(running, "PSXConnection", self.get_psx_connection_coro)
                self._start_task(running, "Turbulence", self.turbulence_coro)
                self._start_task(running, "PSXWind", self.psx_wind_coro)
                if self.args.accelerations:
                    self._start_task(running, "BoostConnection", self.boost_connection_coro)

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
            type=str, action='store', default='C',
            help="Which CDUs to set up (any combination of L, R, C).",
        )
        parser.add_argument(
            '--menu-side',
            type=str, action='store', default='L', choices=['L', 'R'],
            help="Which side of the CDU menu to place the TURB entry on.",
        )
        parser.add_argument(
            '--menu-row',
            type=int, action='store', default=6,
            help="Row (1-6) of the CDU menu to place the TURB entry on.",
        )
        parser.add_argument(
            '--rate',
            type=int, default=100, metavar='0-100',
            help="Scale injection frequency independently of magnitude. "
                 "100 = normal rate (up to 5 Hz), 1 = 1/100th of normal rate.",
        )
        parser.add_argument(
            '--accelerations',
            action='store_true',
            help="Connect to the PSX boost server and print body-frame accelerations "
                 "alongside turbulence output.",
        )
        parser.add_argument(
            '--boost-server-host',
            type=str, default='127.0.0.1',
            help="Hostname or IP of the PSX boost server.",
        )
        parser.add_argument(
            '--boost-server-port',
            type=int, default=10749,
            help="Port of the PSX boost server.",
        )
        parser.add_argument(
            '--config-file',
            type=str, default=None, metavar='PATH',
            help="JSON config file.  Loaded on startup; SAVE/LOAD buttons appear on CDU.",
        )
        parser.add_argument(
            '--no-cdu-interface',
            action='store_true',
            help="Disable the CDU interface entirely.  Config file and console output "
                 "are unaffected.",
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

        if not 0 <= self.args.rate <= 100:
            parser.error("--rate must be between 0 and 100")

    async def run(self):
        """Start everything."""
        self.handle_args()
        if self.args.config_file:
            self._load_config(self.args.config_file)

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
            asyncio.get_running_loop().set_debug(True)
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
