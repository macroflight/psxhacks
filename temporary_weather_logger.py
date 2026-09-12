"""Temporary diagnostic tool: log PFD-altitude data and flag unexplained jumps live.

Investigates the rare captain's-PFD altitude jump first seen 2026-08-06
(MSLP->SKBO) and reported independently on the PSX forum (Jim Barrett,
KOAK->KDEN, repeated jumps in cruise). See
~/frankenweather_altitude_jump_forum_post.md for the original writeup.

PSX variables used:

  PiBaHeAlTas (Qs121)  ECON.    pitch;bank;heading;true_alt_ft*1000;tas*1000;
                                lat_rad;lon_rad -- true/geometric altitude,
                                unaffected by barometric setting.
  LeftPfdAlt  (Qs562)  DEMAND.  <b|s>qnh_alt_ft;std_alt_ft; -- the captain's
                                PFD altitude tape. Per PSX's own docs: field 1
                                (QNH-referenced) is what's actually displayed
                                when the mode letter is 'b'; field 2
                                (STD/1013.25hPa-referenced, but NOT QNH-
                                independent in practice -- it still reacts to
                                the active weather zone's simulated pressure
                                environment) is displayed when 's'. DEMAND
                                mode needs an explicit "demand=Qs562" to start
                                the ~1Hz update stream -- this script sends
                                that once on connect.
  EcpBaroCp   (Qh30)   DELTA.   Kollsman knob input (one message per detent).
  FocussedWxZone (Qi240) XECON. Which of the 7 weather zones PSX currently
                                treats as "nearest" -- changes on its own as
                                the aircraft crosses zone boundaries, with no
                                addon write involved.
  WxSlowTransit (Qi243) XDELTA. Sent by frankenweather before every weather
                                write to request a smooth transition; logged
                                here for correlation context only.

Also subscribed purely for context (no role in detection, see
_on_weather_var()): WxAloft, WxBasic, Wx1-7, WxMode1-7, Metar1-7, WxClust,
WxCorridorSel/Txt, WxSigmet, WxAutoSet, WxBurst, WxBits, MetarsUp -- so a
reported jump's full weather state is in the log without needing a separate
router traffic capture. This is a plain PSX client (works against a bare
PSX Main Server or any router, no frankenrouter dependency) so it's
self-sufficient to hand to someone else for their own capture.

Detection logic mirrors the batch analysis done against
~/logs-cirrus/masterrouter-traffic-*.psxnet.log: track the offset between
the *currently displayed* PFD reference and true altitude, flag a
discontinuous step that then holds steady (ruling out a single noisy
sample or a back-and-forth oscillation), and note (without auto-dismissing)
whether a reposition, situ reload, Kollsman knob click, or a focused-zone
change happened nearby, since any of those are plausible independent
explanations worth distinguishing from an unexplained jump.
"""
import argparse
import asyncio
import logging
import re
import sys
import time
from collections import deque
from typing import Optional

import psx
from psxhacks_version import get_version

__version__ = get_version("psxutils", __file__)
__MYNAME__ = 'temporary_weather_logger'
__MY_CLIENT_ID__ = 'ALTJUMP'
__MY_DISPLAY_NAME__ = 'Temporary Weather Logger'
__MY_DESCRIPTION__ = (
    'Log PFD-altitude data and flag unexplained jumps live (diagnostic tool)')

_HOLD_CHECK_FT = 40.0        # subsequent samples must stay within this of the new offset
_HOLD_CHECK_N = 4            # this many subsequent samples must hold, not just one
_HOLD_CHECK_WINDOW_S = 8.0   # ...within this many seconds after the jump
_KNOB_WINDOW_S = 15.0
_RELOAD_WINDOW_S = 10.0
_ZONE_SWITCH_WINDOW_S = 15.0
_REPOSITION_WINDOW_S = 10.0
_REPOSITION_LATLON_RAD = 0.01   # ~34nm; bigger sample-to-sample jump than any real flight dynamic
_REPOSITION_ALT_FT = 500.0      # ...or a true-alt jump this big in one Qs121 sample-to-sample step
_CRUISE_WINDOW_S = 60.0
_CRUISE_MAX_FPM = 300.0
_CRUISE_MIN_ALT_FT = 25000.0
_HISTORY_TRIM_S = 120.0         # drop tracking history older than this

# Friendly name -> Q-code, for the weather variables logged purely for
# context (see _on_weather_var()). psx.py's subscribe() needs the friendly
# name to resolve against PSX's lexicon, but the log itself should read
# consistently in Q-codes throughout, matching the jump-detection variables.
_WEATHER_VAR_QCODES = {
    "WxAloft": "Qs327", "WxBasic": "Qs328",
    "Wx1": "Qs329", "Wx2": "Qs330", "Wx3": "Qs331", "Wx4": "Qs332",
    "Wx5": "Qs333", "Wx6": "Qs334", "Wx7": "Qs335",
    "WxMode1": "Qs336", "WxMode2": "Qs337", "WxMode3": "Qs338", "WxMode4": "Qs339",
    "WxMode5": "Qs340", "WxMode6": "Qs341", "WxMode7": "Qs342",
    "Metar1": "Qs343", "Metar2": "Qs344", "Metar3": "Qs345", "Metar4": "Qs346",
    "Metar5": "Qs347", "Metar6": "Qs348", "Metar7": "Qs349",
    "WxClust": "Qs350", "WxCorridorSel": "Qs497", "WxCorridorTxt": "Qs498",
    "WxSigmet": "Qs499", "WxAutoSet": "Qi139", "WxBurst": "Qi141",
    "WxBits": "Qi264", "MetarsUp": "Qs464",
}


class WeatherLogger:  # pylint: disable=too-many-instance-attributes
    """Log filtered PSX weather/altitude data and flag unexplained PFD jumps."""

    def __init__(self) -> None:
        """Initialise with empty state."""
        self.args: Optional[argparse.Namespace] = None
        self.logger = logging.getLogger(__MYNAME__)
        self.logfile = None

        self.true_alt: Optional[float] = None
        self._last_logged_alt_ft: Optional[int] = None
        self.qs121_hist: deque = deque()   # (ts, true_alt_ft, lat_rad, lon_rad)
        self.qs562_hist: deque = deque()   # (ts, offset_ft, baro, displayed_ft, other_ft)
        self.knob_events: deque = deque()
        self.reload_events: deque = deque()
        self.zone_switch_events: deque = deque()
        self.pending = None   # a candidate jump awaiting hold-confirmation

    # ------------------------------------------------------------------
    # Logging helpers
    # ------------------------------------------------------------------

    def _log_line(self, text: str) -> None:
        """Write one line to the filtered data log, timestamped."""
        if self.logfile is not None:
            self.logfile.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {text}\n")
            self.logfile.flush()

    # ------------------------------------------------------------------
    # PSX variable handlers
    # ------------------------------------------------------------------

    def _on_pibahealtas(self, _key: str, value: str) -> None:
        parts = value.split(';')
        if len(parts) < 7:
            return
        try:
            true_alt = int(parts[3]) / 1000.0
            lat = float(parts[5])
            lon = float(parts[6])
        except ValueError:
            return
        self.true_alt = true_alt
        ts = time.monotonic()
        self.qs121_hist.append((ts, true_alt, lat, lon))
        # Qs121 also carries pitch/bank/heading/lat/lon, which change almost
        # every message even in dead-level cruise -- logging every one of
        # those would drown out everything else. Detection above still sees
        # every sample regardless; only the log write is throttled, on
        # whole-foot altitude change.
        rounded_alt_ft = round(true_alt)
        if rounded_alt_ft != self._last_logged_alt_ft:
            self._last_logged_alt_ft = rounded_alt_ft
            self._log_line(f"Qs121={value}")

    def _on_leftpfdalt(self, _key: str, value: str) -> None:
        self._log_line(f"Qs562={value}")
        if self.true_alt is None:
            return
        m = re.match(r'^([bs]?)(-?\d+);(-?\d+);', value)
        if not m:
            return
        baro, qnh_alt, std_alt = m.group(1), float(m.group(2)), float(m.group(3))
        # Per PSX's own docs, only one of these is actually shown on the PFD,
        # depending on the mode letter -- see the module docstring.
        displayed = qnh_alt if baro == 'b' else std_alt
        other = std_alt if baro == 'b' else qnh_alt
        ts = time.monotonic()
        offset = displayed - self.true_alt
        self.qs562_hist.append((ts, offset, baro, displayed, other, self.true_alt))
        self._check_for_jump()
        self._trim_history()

    def _on_ecpbarocp(self, _key: str, value: str) -> None:
        self.knob_events.append(time.monotonic())
        self._log_line(f"Qh30={value}")

    def _on_focussedwxzone(self, _key: str, value: str) -> None:
        self.zone_switch_events.append(time.monotonic())
        self._log_line(f"Qi240={value}")

    def _on_wxslowtransit(self, _key: str, value: str) -> None:
        self._log_line(f"Qi243={value}")

    def _on_weather_var(self, key: str, value: str) -> None:
        """Log a weather variable with no other role in jump detection.

        Kept purely for context: when a jump is reported, these give the
        full weather state (zone data, METARs, wind corridor, SIGMETs,
        CB clusters) at the time, without needing a second pass through a
        separate router traffic log.
        """
        self._log_line(f"{_WEATHER_VAR_QCODES.get(key, key)}={value}")

    def _on_reload(self) -> None:
        self.reload_events.append(time.monotonic())
        self._log_line("(situ reload: load1/load3)")

    # ------------------------------------------------------------------
    # Jump detection
    # ------------------------------------------------------------------

    def _trim_history(self) -> None:
        cutoff = time.monotonic() - _HISTORY_TRIM_S
        for hist in (self.qs121_hist, self.qs562_hist):
            while hist and hist[0][0] < cutoff:
                hist.popleft()
        for hist in (self.knob_events, self.reload_events, self.zone_switch_events):
            while hist and hist[0] < cutoff:
                hist.popleft()

    def _reposition_near(self, ts: float) -> bool:
        have_prev = False
        pkalt = pklat = pklon = 0.0
        for kts, kalt, klat, klon in self.qs121_hist:
            if have_prev and abs(kts - ts) <= _REPOSITION_WINDOW_S:
                if (abs(klat - pklat) > _REPOSITION_LATLON_RAD or
                        abs(klon - pklon) > _REPOSITION_LATLON_RAD or
                        abs(kalt - pkalt) > _REPOSITION_ALT_FT):
                    return True
            pkalt, pklat, pklon = kalt, klat, klon
            have_prev = True
        return False

    def _vertical_rate_fpm(self, ts: float) -> Optional[float]:
        window = [(t, a) for t, a, _, _ in self.qs121_hist if ts - _CRUISE_WINDOW_S <= t <= ts]
        if len(window) < 2:
            return None
        (t0, a0), (t1, a1) = window[0], window[-1]
        dt_min = (t1 - t0) / 60.0
        if dt_min <= 0:
            return None
        return (a1 - a0) / dt_min

    def _check_for_jump(self) -> None:  # pylint: disable=too-many-locals
        # First, see if a pending candidate's hold window has resolved.
        if self.pending is not None:
            ts, offset, baro = self.pending['ts'], self.pending['offset'], self.pending['baro']
            confirmed_bad = False
            held = 0
            for sts, soffset, sbaro, *_ in self.qs562_hist:
                if sts <= ts:
                    continue
                if sts - ts > _HOLD_CHECK_WINDOW_S:
                    break
                if sbaro != baro or abs(soffset - offset) > _HOLD_CHECK_FT:
                    confirmed_bad = True
                    break
                held += 1
            if confirmed_bad:
                self.pending = None
            elif held >= _HOLD_CHECK_N:
                self._report_jump(self.pending)
                self.pending = None
            elif time.monotonic() - ts > _HOLD_CHECK_WINDOW_S:
                self.pending = None  # window expired without enough confirmations

        if len(self.qs562_hist) < 2 or self.pending is not None:
            return
        ts, offset, baro, _displayed, other, talt = self.qs562_hist[-1]
        pts, poffset, pbaro, _pdisplayed, pother, _ptalt = self.qs562_hist[-2]
        dt_s = ts - pts
        if dt_s <= 0 or dt_s > 2.5 or baro != pbaro:
            return
        delta = offset - poffset
        if abs(delta) < self.args.jump_threshold_ft:
            return
        self.pending = {
            'ts': ts, 'offset': offset, 'baro': baro, 'delta': delta,
            'other_delta': other - pother, 'true_alt': talt, 'vrate': self._vertical_rate_fpm(pts),
        }

    def _report_jump(self, cand: dict) -> None:  # pylint: disable=too-many-locals
        ts = cand['ts']
        knob_near = any(abs(ts - k) <= _KNOB_WINDOW_S for k in self.knob_events)
        reload_near = any(abs(ts - r) <= _RELOAD_WINDOW_S for r in self.reload_events)
        zone_switch_near = any(
            abs(ts - z) <= _ZONE_SWITCH_WINDOW_S for z in self.zone_switch_events)
        repositioned = self._reposition_near(ts)
        stationary = abs(cand['true_alt']) < 50
        vrate = cand['vrate']
        is_cruise = (vrate is not None and abs(vrate) < _CRUISE_MAX_FPM and
                     cand['true_alt'] >= _CRUISE_MIN_ALT_FT)
        both_fields_moved = abs(cand['other_delta'] - cand['delta']) < _HOLD_CHECK_FT

        flags = []
        if knob_near:
            flags.append('KNOB')
        if reload_near:
            flags.append('RELOAD')
        if repositioned:
            flags.append('REPOSITION')
        if stationary:
            flags.append('STATIONARY')
        flagstr = ','.join(flags) if flags else 'UNEXPLAINED'
        cruise_tag = 'CRUISE' if is_cruise else ('CLIMB/DESC' if vrate is not None else '?')
        both_tag = 'BOTH-FIELDS' if both_fields_moved else 'DISPLAYED-FIELD-ONLY'
        zone_tag = ' ZONE-SWITCH-NEAR' if zone_switch_near else ''
        vr = f"{vrate:+.0f}fpm" if vrate is not None else 'n/a'

        msg = (f"*** PFD ALTITUDE JUMP: delta={cand['delta']:+.0f}ft "
               f"other_ref_delta={cand['other_delta']:+.0f}ft [{both_tag}]  "
               f"true_alt={cand['true_alt']:.0f}ft baro={cand['baro']} vrate={vr} "
               f"[{cruise_tag}]  [{flagstr}]{zone_tag}")
        self.logger.warning(msg)
        self._log_line(msg)

    # ------------------------------------------------------------------
    # PSX connection
    # ------------------------------------------------------------------

    async def _psx_coro(self) -> None:
        """Maintain the PSX connection, demand LeftPfdAlt, and subscribe to the rest."""
        def connected(_key: str, _value: str) -> None:
            self.logger.info("PSX connected")
            client.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")
            client.send("clientName", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")
            client.send("demand", "Qs562")

        client = psx.Client()
        client.logger = lambda msg: self.logger.debug("PSX: %s", msg)
        client.onConnect = lambda: None
        client.onPause = self._on_reload
        client.subscribe("version", connected)
        client.subscribe("PiBaHeAlTas", self._on_pibahealtas)
        client.subscribe("LeftPfdAlt", self._on_leftpfdalt)
        client.subscribe("EcpBaroCp", self._on_ecpbarocp)
        client.subscribe("FocussedWxZone", self._on_focussedwxzone)
        client.subscribe("WxSlowTransit", self._on_wxslowtransit)
        # Weather variables with no role in jump detection, logged purely for
        # context (see _on_weather_var()).
        for name in _WEATHER_VAR_QCODES:
            client.subscribe(name, self._on_weather_var)
        await client.connect(self.args.psx_host, self.args.psx_port)
        self.logger.warning("PSX connection ended")

    # ------------------------------------------------------------------
    # Argument parsing and entry point
    # ------------------------------------------------------------------

    def handle_args(self) -> None:
        """Parse command-line arguments."""
        parser = argparse.ArgumentParser(
            prog=__MYNAME__,
            description=__MY_DESCRIPTION__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
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
            '--log-file', default='weather_logger.log', metavar='PATH',
            help="Where to write the filtered raw-data log.")
        parser.add_argument(
            '--jump-threshold-ft', type=float, default=150.0, metavar='FT',
            help="Minimum PFD-altitude step (offset from true altitude) to "
                 "consider a candidate jump.")
        parser.add_argument(
            '--debug', action='store_true',
            help="Enable debug logging.")
        parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
        self.args = parser.parse_args()
        if self.args.psx_port_override is not None:
            if self.args.psx_port_override != self.args.psx_port:
                print(f"WARNING: --psx-port-override={self.args.psx_port_override} "
                      f"overrides --psx-port={self.args.psx_port}", file=sys.stderr)
            self.args.psx_port = self.args.psx_port_override

    async def run(self) -> None:
        """Parse args, open the log file, then run the PSX loop."""
        self.handle_args()
        print(f"temporary_weather_logger version {__version__} starting")
        logging.basicConfig(
            level=logging.DEBUG if self.args.debug else logging.INFO,
            format="%(asctime)s: %(message)s",
            datefmt="%H:%M:%S")
        self.logfile = open(  # pylint: disable=consider-using-with
            self.args.log_file, 'a', encoding='utf-8')
        self.logger.info("Logging filtered data to %s", self.args.log_file)
        try:
            await self._psx_coro()
        finally:
            self.logfile.close()


def main() -> None:
    """Parse arguments and run the logger."""
    wl = WeatherLogger()
    try:
        asyncio.run(wl.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
