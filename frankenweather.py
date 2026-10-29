"""FrankenWeather - Dynamic real-world weather zones for PSX."""
# pylint: disable=invalid-name,duplicate-code,too-many-lines
import argparse
import asyncio
import collections
import copy
import dataclasses
import glob
import inspect
import json
import logging
import math
import os
import pathlib
import random
import re
import statistics
import sys
import textwrap
import threading
import time
import tomllib
import traceback
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO
from typing import Optional

import aiohttp

try:
    from PIL import Image as _PIL_Image
    _HAS_PIL = True
except ImportError:
    _PIL_Image = None
    _HAS_PIL = False
from pyproj import Geod

import psx
import wind_corridor
from frankenturb import (
    TurbulenceEngine, TurbulenceState, parse_pibahealtas,
    compute_cb_turbulence,
    PirepFetcher, compute_pirep_turbulence,
    CapeFetcher, compute_cape_turbulence,
    GairmetFetcher, compute_gairmet_turbulence,
)
from frankenturb.boundary_layer import compute_boundary_layer_turbulence
from frankenturb.pirep import SEV_INTENSITY as _PIREP_SEV_INTENSITY
from frankenturb.cb import (
    find_nearest_cb, parse_wx_zone_basic, parse_wx_zone_position, parse_wx_clust,
    zone_cb_radius_nm,
)
from frankenturb.cb_turbulence import cb_flank_width_nm
from frankenturb.disk_cache import DiskCache
from frankenturb.openmeteo import CLIENT as _OM_CLIENT
from frankenturb import turbulence_events as _turb_events
from frankenturb.intensity_profile import (
    DEFAULT_BANDS, REALISTIC_BANDS, REALISTIC_SEV_BANDS, GustinessProfile,
    LayeredIntensityProfile)
from fw_cb import (
    WX_DEFAULTS as _WX_DEFAULTS,
    CB_TOP_MAX_FT as _CB_TOP_MAX_FT,
    cape_to_cb_oktas as _cape_to_cb_oktas,
    cb_base_ft as _cb_base_ft,
    cb_top_ceiling_ft as _cb_top_ceiling_ft,
    cb_tops_ft as _cb_tops_ft,
    om_cb_fields as _om_cb_fields,
    apply_om_cb as _apply_om_cb,
    apply_fake_cb as _apply_fake_cb,
)
import fw_webui as _fw_webui  # pylint: disable=wrong-import-order
import fw_sigmet as _fw_sigmet  # pylint: disable=wrong-import-order
from psxhacks_version import get_version


__MYNAME__ = 'frankenweather'
__MY_CLIENT_ID__ = 'WEATHER'
__MY_DISPLAY_NAME__ = 'FrankenWeather'
__MY_DESCRIPTION__ = 'Dynamic real-world weather zones for PSX using Open-Meteo'
__version__ = get_version("frankenweather", __file__)

_OM_VARS = (
    "temperature_2m,relative_humidity_2m,weather_code,cloud_cover,pressure_msl,"
    "wind_speed_10m,wind_direction_10m,wind_gusts_10m,visibility"
)

# On-disk caches for zone-weather and enroute-wind Open-Meteo fetches, so
# restarting frankenweather doesn't have to re-fetch data it already had
# moments ago. Zone weather counts as fresh for _OM_ZONE_FRESH_S (Open-Meteo's
# "current" conditions only change every 15 minutes) but is kept for
# _OM_ZONE_KEEP_S, so the zones can still be updated (new METARs, settings)
# from older data while Open-Meteo is unreachable.
_OM_ZONE_FRESH_S = 900.0
_OM_ZONE_KEEP_S = 24 * 3600.0
_OM_ZONE_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "frankenweather", "om_zones.json")
_OM_ENROUTE_CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".cache", "frankenweather", "om_enroute.json")

# Per-run event log: every console line, one timestamped file per run, so a
# problem report (e.g. "Open-Meteo wasn't reachable for 10 minutes") can be
# diagnosed from a downloadable file instead of reconstructed from memory --
# see the web UI's Config and logs page. Same ~/.cache/frankenweather/ directory
# as the caches above, so it exists and is writable on both Linux and
# Windows without any extra setup.
_EVENT_LOG_DIR = os.path.join(os.path.expanduser("~"), ".cache", "frankenweather", "logs")
_EVENT_LOG_GLOB = "frankenweather_*.log"
_EVENT_LOG_RETAIN = 10  # keep only the most recent N per-run log files
# --sigmet: SIGMETs we could not use are saved here, one file each, so
# the parser can be improved later (see _save_unparsed_sigmets()).
_SIGMET_UNPARSED_DIR = os.path.join(_EVENT_LOG_DIR, "sigmet-unparsed")
# How often log_flush_coro() fsyncs the log files, so a log copied off disk
# mid-flight (e.g. from ~/.cache/frankenweather/logs on Windows) is never
# more than this far behind -- same cadence as frankenrouter's own logs.
_LOG_FLUSH_INTERVAL_S = 60.0


def _om_pos_key(lat: float, lon: float) -> str:
    """Return a stable disk-cache key for an Open-Meteo fetch position."""
    return f"{lat:.3f},{lon:.3f}"


# Bucket size for _om_zone_cache_key(), matching CapeFetcher's/WindFetcher's
# own CACHE_DEG_GRID in frankenturb/ -- see that function's docstring.
_OM_ZONE_CACHE_DEG_GRID = 1.0


def _om_hour_index(data: dict, ts: float) -> Optional[int]:
    """Return the index of the hour nearest ts in an Open-Meteo reply (unixtime), within 30 min."""
    times = (data.get("hourly") or {}).get("time") or []
    if not times or not isinstance(times[0], (int, float)):
        return None
    idx = min(range(len(times)), key=lambda i: abs(times[i] - ts))
    return idx if abs(times[idx] - ts) <= 1800 else None


def _om_zone_cache_key(lat: float, lon: float) -> str:
    """Return a coarse-grid disk-cache key for a zone-weather Open-Meteo fetch.

    Buckets to a _OM_ZONE_CACHE_DEG_GRID° grid (matching frankenturb's
    CapeFetcher/WindFetcher bucket size) instead of _om_pos_key()'s exact
    position, so a zone relocated a short distance from a moments-ago fetch
    -- or two zones/relocations landing in roughly the same area, which
    happens often given the ~150-250nm forward placement cone -- can reuse
    that entry instead of missing and forcing a fresh Open-Meteo call.
    Freshness is unaffected: _om_zone_cache's own max_age_s (_REFRESH_MAX_S,
    matching the zone refresh cadence) still governs how old a served entry
    can be, independent of this bucketing.

    Deliberately a separate function from _om_pos_key(), which the enroute-
    wind cache still uses at full precision -- that feature compares
    Open-Meteo wind against the flight-plan's wind at a *specific* waypoint,
    where coarsening position would blur the very comparison it exists to
    show.
    """
    return (f"{math.floor(lat / _OM_ZONE_CACHE_DEG_GRID)},"
            f"{math.floor(lon / _OM_ZONE_CACHE_DEG_GRID)}")


def _prepare_event_log() -> Optional[str]:
    """Create this run's per-run log file path, pruning old runs first.

    Returns None (never raises) if the log directory can't be created --
    e.g. an unwritable home directory -- since this is a best-effort
    convenience log, not something that should ever prevent frankenweather
    from starting. Console logging (and --log, if given) work either way.
    """
    try:
        os.makedirs(_EVENT_LOG_DIR, exist_ok=True)
        existing = sorted(glob.glob(os.path.join(_EVENT_LOG_DIR, _EVENT_LOG_GLOB)))
        # Prune down to RETAIN-1 now, so this run's new file brings the
        # directory back up to exactly RETAIN once it starts being written.
        for old_path in existing[:max(0, len(existing) - (_EVENT_LOG_RETAIN - 1))]:
            try:
                os.remove(old_path)
            except OSError:
                pass
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return os.path.join(_EVENT_LOG_DIR, f"frankenweather_{ts}.log")
    except OSError:
        return None


# Enroute wind importer: fetch Open-Meteo pressure-level wind/temperature for
# each not-yet-passed route waypoint and inject it into WxCorridorTxt as a
# fresh Format-A corridor, so PSX's enroute wind keeps drifting with reality
# even if the crew never requests a new datalink wind uplink.
_ENROUTE_FETCH_INTERVAL_S = 3600.0     # background refresh cadence ("hourly")
# Enroute wind downloads cover 2 days (forecast_days=2): served from the
# on-disk cache for _OM_ENROUTE_FRESH_S (newer forecast runs are out by
# then), and kept for as long as they cover the current hour, as a fallback
# while Open-Meteo is unreachable.
_OM_ENROUTE_FRESH_S = 6 * 3600.0
_OM_ENROUTE_KEEP_S = 48 * 3600.0
# Max distance (nm) for matching a live FMC waypoint to a flight-plan snapshot
# waypoint by coordinates when the names don't match verbatim — e.g. NAT track
# points, where Format E only carries PSX's own lat/lon-derived name (like
# "48N05") rather than the FMC's name for the same point (like "4850N"). Loose
# enough for rounding in either name's coordinate encoding, tight enough to
# stay well under real waypoint spacing (oceanic points are typically tens to
# hundreds of nm apart).
_ENROUTE_COORD_MATCH_NM = 5.0
# Target flight levels (ft) for the generated corridor, each mapped to the
# nearest Open-Meteo standard pressure level below. The PSX NG FMC manual
# titles this corridor format "Format A — 6 wind records per leg": PSX's own
# reader expects exactly this many columns, not merely "up to" this many, so
# this tuple's length is load-bearing, not just a starting default.
_ENROUTE_FL_LIST_FT = (10000, 18000, 24000, 30000, 34000, 39000)
# Bounds for a flight-level list to be considered safe to build a Format-A
# corridor with — reusing the flight plan's captured levels is a nicety, but
# generating a structurally valid corridor always takes precedence. Format A
# needs *exactly* len(_ENROUTE_FL_LIST_FT) columns: a captured snapshot
# spanning a sliding multi-level window (e.g. a Format-E flight plan with a
# different 4-level window per climb/cruise/descent segment) unions to some
# other count across all its waypoints, which PSX has rejected/malformed
# (e.g. "below 1000ft minimum, 0ft" from a misparsed header; a 9-column
# corridor built from such a union) — such lists are discarded in favor of
# the default rather than sent as-is.
_FL_LIST_MIN_FT = 1000
_FL_LIST_MAX_FT = 60000
_FL_LIST_LEN = len(_ENROUTE_FL_LIST_FT)


def _valid_fl_list(levels: list) -> bool:
    """Return True if a flight-level list is safe to build a Format-A corridor with."""
    if len(levels) != _FL_LIST_LEN:
        return False
    return all(_FL_LIST_MIN_FT <= fl <= _FL_LIST_MAX_FT for fl in levels)


# Open-Meteo standard pressure levels (hPa) → approximate ISA altitude (ft).
# Used to pick, for each target flight level above, the closest hPa level to
# request temperature/wind for.
_OM_PRESSURE_LEVELS_FT = {
    1000: 364, 975: 1266, 950: 1773, 925: 2299, 900: 2844, 850: 4781,
    800: 6562, 700: 9882, 600: 13801, 500: 18289, 400: 23574, 300: 30065,
    250: 33999, 200: 38662, 150: 44647, 100: 53083, 70: 60663, 50: 68000,
    30: 77000,
}


def _nearest_om_hpa(target_ft: float) -> int:
    """Return the Open-Meteo pressure level (hPa) whose ISA altitude is closest to target_ft."""
    return min(_OM_PRESSURE_LEVELS_FT, key=lambda hpa: abs(_OM_PRESSURE_LEVELS_FT[hpa] - target_ft))


def _om_hval(hourly: dict, key: str, hour: int, default: float) -> float:
    """Return hourly[key][hour] as a float, falling back to default.

    Open-Meteo's hourly arrays can hold None for hours it has no data
    for (not just be short), so an index that exists is not enough —
    the value at that index must also be checked before float()'ing it.
    """
    lst = hourly.get(key) or []
    val = lst[hour] if hour < len(lst) else None
    return float(val) if val is not None else float(default)


_UCAR_STATIONS_URL = "https://weather.rap.ucar.edu/surface/stations.txt"
_STATIONS_CACHE = os.path.join(os.path.expanduser("~"), ".cache", "frankenweather", "stations.txt")
# os.path.expanduser("~") resolves to the right home dir on both Linux and Windows.
# Only ever loaded if it actually exists (see _load_config_file) — nothing is written
# here unless the user explicitly saves from the web UI.
_DEFAULT_CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".frankenweather.toml")
_VATSIM_ALL_URL = "https://metar.vatsim.net/all"
_VATSIM_CACHE_MAX_S = 1800             # re-fetch VATSIM METARs every 30 minutes
# EXPERIMENTAL (--metar-live-refresh): metar.vatsim.net also serves a
# comma-separated subset of stations at the same path (confirmed live:
# https://metar.vatsim.net/EGLL,KLAX returns both in one response, same
# one-line-per-station format as /all; an unknown ICAO is silently omitted,
# never an error). Used for a short-timeout, per-cycle refresh of just the
# airports actually in use as zones, so they can be fresher than the 30-
# minute bulk cache without polling /all more often for everyone else.
_VATSIM_LIVE_URL = "https://metar.vatsim.net/{icaos}"
_VATSIM_LIVE_TIMEOUT_S = 30.0
# Secondary METAR source used only for the METAR-fallback path, when VATSIM's
# own (ATC/pilot-reported) network doesn't cover a station near enough to be
# useful -- NOAA's hourly cycle files have much broader, non-VATSIM-dependent
# global coverage. Each file is a bulk dump of every METAR received in that
# UTC hour, refreshed hourly, so re-fetching more often than that is pointless.
_NOAA_METAR_CYCLE_URL = "https://tgftp.nws.noaa.gov/data/observations/metar/cycles/{hour:02d}Z.TXT"
_NOAA_METAR_CACHE_MAX_S = 1800
_METAR_FALLBACK_ZONE_COUNT = 7
_SIGMET_REFRESH_MAX_S = 1800           # re-request PSX's own SIGMET download every 30 minutes
_OWN_SIGMET_REFRESH_S = 600            # --sigmet: own SIGMET download every 10 minutes
_OWN_SIGMET_TIMEOUT = aiohttp.ClientTimeout(total=30)
# "Anchor" zone: when every zone would be more than _ANCHOR_TRIGGER_NM
# away, one is kept _ANCHOR_BEHIND_NM behind the aircraft (CB-free, out of
# sight of the radar and too far from the zones ahead to shrink their CBs),
# so PSX's local weather (from the nearest zone) doesn't come from far
# away. Moved back once _ANCHOR_RELOCATE_NM behind. See _maintain_anchor_zone().
_ANCHOR_TRIGGER_NM = 150.0
_ANCHOR_BEHIND_NM = 50.0
_ANCHOR_RELOCATE_NM = 100.0
# --sigmet: weather zones placed inside TS/TC SIGMET areas, so the CBs
# frankenweather depicts there (instead of PSX) show up on the radar. See
# _place_sigmet_zones().
_SIGMET_ZONE_RANGE_NM = 300.0      # only areas this close (roughly weather radar range)
_SIGMET_ZONES_PER_TS = 2           # zones per TS area
_SIGMET_ZONES_PER_TC = 3           # zones per TC area, a tight cluster
_SIGMET_TC_CLUSTER_SEP_NM = 20.0   # separation between zones in a TC cluster
_SIGMET_PLACE_INTERVAL_S = 60.0    # move at most one zone this often
# --sigmet: turbulence frankenweather generates itself for TURB/MTW
# SIGMETs (PSX doesn't get them). The intensity varies over time between
# 10% and 75%, mostly light (frankenturb/intensity_profile.py), scaled per
# qualifier: SIGMETs are issued for severe turbulence (moderate goes in
# AIRMETs). It fades in over the first _SIGMET_TURB_FADE_NM inside the
# area and _SIGMET_TURB_FADE_FT inside its base/top. See
# _sigmet_turbulence().
_SIGMET_TURB_QUALIFIER_SCALE = {"SEV": 1.0, "MOD": 2.0 / 3.0}
# Depth of the altitude layers with independent levels (and blend either
# side of a boundary), per type: TURB is mostly thin clear-air turbulence
# layers, a mountain wave is much deeper, so a level change helps less.
_SIGMET_TURB_LAYERS = {"TURB": (2000.0, 300.0), "MTW": (6000.0, 500.0)}
# Testing profile: turbulence everywhere inside a TS/TC SIGMET area, below
# its top, between the zones' CBs (see _sigmet_convective_turbulence()):
# the zones' CB turbulence only reaches half way to the next zone, so a
# whole FRQ TS area at FL350 was smooth (flight test 2026-10-25). Varies like
# a TURB SIGMET's (TURB layers), scaled by the SIGMET's wording, on top of
# a floor: the TURB levels alone left a FRQ TS area at FL350 at 0-2 % for
# minutes (flight test 2026-10-25). It's the air between the cells (the
# zones' CB turbulence is the cells): mostly light, a FRQ area 10-36 %;
# 15 % + 70 % of the level gave a sustained 45 %, too much for a large area
# (user). Qualifier -> (floor, scale).
_SIGMET_CONV = {"ISOL": (0.03, 0.15), "OCNL": (0.06, 0.25), "FRQ": (0.10, 0.35),
                "EMBD": (0.10, 0.35), "OBSC": (0.08, 0.30), "SQL": (0.12, 0.45),
                "CONVECTIVE": (0.08, 0.30)}
_SIGMET_CONV_DEFAULT = (0.06, 0.25)
# Testing profile: wave/MTW bursts that don't drive FLCH into oscillation
# (_pick_wave_burst_testing()). A wave phase changing this fast (per s, a
# sine of about a 2 min period) gives sink bursts at the full rate.
_WAVE_PHASE_RATE_FULL = 0.05
# ... and keep this fraction of their speed bursts. Gust bursts become
# speed bursts and bank/yaw ones are dropped: GUST weather-vanes the
# aircraft, the yaw a 747 pilot found unrealistic in mountain wave (test
# sim 2026-10-28, 30 %: heading p95 1.7 deg / bank 6.6 deg with GUST,
# 0.01 / 0.04 with speed and up/down bursts only), and pilots expect a lot
# of speed fluctuation there. FLCH holds speed with pitch, so speed kicks
# make it chase (EGPF FLCH descent replayed, 2026-10-25: -4300 fpm and 4
# swings over 2000 fpm with them, -2400 / 0 without); accepted (user,
# 2026-10-28): crews change to V/S in that. Half of them made a 45 % wave
# FLCH descent swing 17 times by over 2000 fpm (-4240 to +395 fpm, TAS
# +-15 kt p95; SINK only: none, -2140 to -1240 fpm), so a quarter.
_WAVE_SPD_KEEP = 0.25
# Below this intensity a wave's bursts are only speed/gust ones
# (_pick_burst()); without them it would be glassy smooth, so the testing
# profile makes them up/down bursts there instead (rate-gated like the rest)
_WAVE_LIGHT_BELOW = 0.25
# Testing profile: the continuous turbulence's up/down bursts one way
# within this many seconds add up to at most the largest single burst
# (_updown_budget()), whatever the kind. A run of them a second or two
# apart pumps PSX's lightly damped pitch ringing (3.5-4 s a cycle): in
# wave to +-0.6 g, pitch 0-6.6 deg, from bursts of ~0.2 g each (NZZC MTW
# FL250, night campaign), and in wind shear at 34 % to +-2 g, pitch -5.7
# to +13.8 deg (SCIZ FL330, 2026-10-26). Turbulence events' bursts (and
# manual turbulence) are not limited: their effect is meant to be real.
_UPDOWN_WINDOW_S = 8.0
# Testing profile: an MTW SIGMET lifts the terrain model's own wave to at
# most this factor times it plus this much (_mtw_state()).
_MTW_LIFT_FACTOR = 2.0
_MTW_LIFT_ADD = 0.20
_SIGMET_CONV_TC = (0.12, 0.45)
# Gustiness of physically computed turbulence: a layered multiplier around
# 1 (frankenturb/intensity_profile.py GustinessProfile) per family of
# turbulence kinds (_gust_family()): (low, high, layer_ft, blend_ft).
# Mountain wave is deep and steady, wind-shear clear-air turbulence thin
# and variable. Wind shear was 0.4-1.6 (up to 80% at full shear) until a
# pilot found it far too strong (2026-10-24); now at most 60%. Rotor and
# mechanical turbulence, near the ground like low-level wind shear, have
# the same range: they were 0.5-1.5, so mechanical turbulence in a strong
# wind could reach 75% on an approach (MECH_MAX 0.5, 2026-10-25). Keep
# frankenturb's TERRAIN_GUST_MAX in step with "lowlevel".
_TURB_GUST = {
    "shear": (0.5, 1.2, 2000.0, 300.0),
    "wave": (0.7, 1.3, 6000.0, 500.0),
    "lowlevel": (0.5, 1.2, 2000.0, 300.0),   # rotor, mechanical
    "cape": (0.5, 1.5, 2000.0, 300.0),
}
# G-AIRMET turbulence areas vary like a TURB SIGMET (_gairmet_vary()): a
# layered level (0-75%) scaled by the area's severity relative to SEV
# (frankenturb/gairmet.py: SEV 0.75, so MOD gets two thirds), in 2000 ft
# layers.
_GAIRMET_SEV_INTENSITY = 0.75
_GAIRMET_LAYERS = (2000.0, 300.0)
# "PIREPs" (router /efb PIREP button): the ride at whole flight levels this
# far above and below the current one (FL010-FL470), refreshed this often,
# on a flight level change and when the driving source changes
# (pirep_levels_coro()). A level's ride is rated by the long-run share of
# time its turbulence is at _PIREP_MODERATE effective intensity or more
# (moderate or worse): a smooth layer of a SEV SIGMET is there 9% of the
# time, a rough one 63%; a MOD area's 4% to 48%.
_PIREP_RANGE_FT = 4000
_PIREP_INTERVAL_S = 60.0
_PIREP_MODERATE = 0.25
_PIREP_RIDES = ((0.10, "smooth"), (0.20, "light"), (0.35, "moderate"), (math.inf, "rough"))
# Testing profile: the continuous turbulence is at most light chop
# (_TURB_BG_MAX; a SEV TURB SIGMET's REALISTIC_SEV_BANDS sit at 22-34 %),
# and moderate and worse come as events. Rated against 25 % every TURB
# SIGMET level came out "rough" (frankensim 2026-10-29, FAJO E01 at
# FL270-350, while FL310 had smooth spells). So a level is smooth when
# it's at light chop or more less than a quarter of the time, else light;
# moderate near a CB and rough inside one, where moderate and severe
# events are most frequent (_turb_events.AREA_RATES "cb_near"/"cb_core").
# A SEV TURB SIGMET's levels are light chop 75-91 % of the time whatever
# their roughness, so they all read light.
_PIREP_LIGHT_CHOP = 0.22
_PIREP_RIDES_TESTING = ((0.25, "smooth"), (math.inf, "light"))
_SIGMET_TURB_FADE_NM = 10.0
_SIGMET_TURB_FADE_FT = 1000.0
# SigmetOn (Qi262) is a 0-15 bitmask. Bits 1 and 4 are documented; bit 2
# was live-tested (2026-10-23) to be PSX's own periodic SIGMET download.
# Bit 8 is unknown and must never be touched. See _write_sigmet_on().
_SIGMET_EMBED_BIT = 1                 # SIGMETs embedded in the planet weather model
_SIGMET_AUTO_DOWNLOAD_BIT = 2         # PSX downloads SIGMETs itself
_SIGMET_DOWNLOAD_BIT = 4              # trigger an immediate download (self-clearing)
_SIGMET_KNOWN_BITS = _SIGMET_EMBED_BIT | _SIGMET_DOWNLOAD_BIT
# AeroCkBxs (Qi184) is a 3-bit Aerodynamics-page-checkboxes mask (Max=7):
# bit 1 "Altitude indicators with OAT error", bit 2 "CG varies with fuel
# distribution", bit 4 "5th engine installed" -- confirmed against the
# real PSX UI. Only bit 1 is ours to enforce; bits 2/4 are aircraft-type
# configuration and must never be touched. See handle_aero_ckbxs().
_AERO_OAT_ERROR_BIT = 1
_DATIS_LINE_MAX_CHARS = 24            # PSX MetarsUp (Qs464): max chars per '^'-delimited line
# Split a MetarsUp (Qs464) value into per-airport blocks. PSX terminates every
# airport's block -- found or not -- with a blank line, i.e. two consecutive
# '^' (see _handle_metars_up()), so blocks never contain an empty segment
# themselves and splitting on '^^' is exact.
_DATIS_BLOCK_SEP = '^^'
# Header for the printer message frankenweather sends after PSX's own FMC
# ALTN weather-request printout when it contains an UNAVAIL airport we have
# VATSIM data for -- see _handle_printer_text(). In practice this write
# lands before PSX's own message finishes rendering and replaces it
# entirely (live-confirmed, only one printout comes out), rather than
# appearing as a genuinely separate second message -- one line, well under
# _DATIS_LINE_MAX_CHARS.
_ALTN_SUPPLEMENT_HEADER = "FRANKENWEATHER ALTN WX^^"
# Header PSX itself prints for both D-ATIS and ALTN weather replies (live-
# confirmed identical for both) -- required (not just "UNAVAIL" present) in
# _handle_printer_text() so an unrelated printout that happens to contain
# the literal text "<ICAO> UNAVAIL" for some other reason can't misfire it.
_DATIS_PRINT_HEADER = "ACARS D-ATIS:"
_AIRPORT_SNAP_NM = 25.0               # snap zone to a real airport if within this radius
# CB-near-airport avoidance (self._cb_airport_mode, see _CB_AIRPORT_MODES):
# PSX always places a zone's CB exactly _CB_TO_ZONE_OFFSET_NM from the zone centre
# (bearing varies with sim time/zone elevation, but the radius never does -- see
# frankenturb/cb.py's _cb_bearing()/_CB_OFFSET_NM, same figure). So either
# - "move": offset the dep/dst zone centre itself by at least
#   _CB_AIRPORT_CLEARANCE_NM + _CB_TO_ZONE_OFFSET_NM from the real airport, in any
#   fixed direction (_arpt_coverage_needed()), or
# - "remove": remove the CB of every zone centred within that distance of the
#   dep/dst airport (_cb_removed_near_airport()),
# and no CB can come within _CB_AIRPORT_CLEARANCE_NM of the airport -- no need to
# track the (constantly rotating) bearing at all.
_CB_AIRPORT_MODES = ("off", "move", "remove")
_CB_TO_ZONE_OFFSET_NM = 7.0
_CB_AIRPORT_CLEARANCE_NM = 15.0
_CB_AVOIDANCE_BEARING_DEG = 0.0       # "move": fixed direction to offset in; arbitrary but stable
_REPOSITION_DIST_NM = 500.0           # zone this far away → aircraft was repositioned
_REFRESH_MAX_S = 300                  # always refresh weather after this many seconds
_ZONE_PROTECTED_LOG_INTERVAL_S = 300   # throttle for the "due but protected" log line
_PUSH_COOLDOWN_S = 5.0                # ignore Wx echo-backs for this long after our write
# Weather variables frankenweather keeps at its own value: if another client
# (or a situ load) changes one, handle_owned_change() logs it and
# _restore_owned() writes ours back, _OWNED_RESTORE_DELAY_S after the last
# such change (a situ load changes them all in one burst).
_OWNED_KEYS = ("WxAutoSet", "WxBasic") + tuple(
    f"{prefix}{i}" for prefix in ("WxMode", "Wx", "Metar") for i in range(1, 8))
_OWNED_RESTORE_DELAY_S = 2.0
_SITU_LOAD_GRACE_S = 5.0              # changes this long after a situ load (load3) are from it
# Safety valve: a variable restored more than _OWNED_RESTORE_MAX times within
# _OWNED_RESTORE_WINDOW_S is fighting someone; stop restoring it (error logged)
# until the window has passed
_OWNED_RESTORE_MAX = 5
_OWNED_RESTORE_WINDOW_S = 120.0
# METAR TEMPO/PROBnn trend-group CB modulation (see Script._trend_cb_contribution):
# how long a TEMPO segment's "on"/"off" state is held before randomly toggling again.
# Deliberately not tied to the ~5-minute weather refresh cadence, so the on/off
# transitions don't line up with (and look driven by) our own refresh cycle.
_TEMPO_HOLD_MIN_S = 600.0             # 10 minutes
_TEMPO_HOLD_MAX_S = 1800.0            # 30 minutes
_TEMPO_MIN_OKTAS = 2                  # lowest CB coverage used while a TEMPO segment is "on"
_MSFS_BRIDGE_TIMEOUT_S = 300.0        # stop using bridge data after this silence period
_MSFS_TURB_FACTOR_MAX = 1.3           # most turbulence is multiplied by in MSFS cloud
_TERRAIN_SMOOTH_S = 30.0              # see Script._smooth_terrain()
_TERRAIN_SMOOTH_JUMP_NM = 3.0         # a position jump this far restarts the smoothing (reposition)
# Rolling window for the MSFS in-cloud duty cycle used by cloud sync (_msfs_cloud_oktas):
# long enough to smooth out a brief punch through a wisp of cirrus, short enough to still
# ramp up to full coverage reasonably quickly in a genuine sustained layer.
_MSFS_CLOUD_TIME_WINDOW_S = 120.0
# Only samples within this many feet of the current altitude count towards the duty
# cycle above. Without this, climbing/descending into a thick layer would have its
# oktas estimate dragged down by older clear-air samples from well above/below the
# layer, delaying the coverage ramp-up right when icing risk starts (immediately on
# entering a thick layer) -- altitude proximity invalidates that stale history fast.
_MSFS_CLOUD_ALT_BAND_FT = 1500.0
# Floor for the synced hi-cloud layer's base (_apply_msfs_sync): near the
# ground (e.g. fog/low stratus right after takeoff, MSFS reporting
# in-cloud at low altitude), alt - margin can go negative. Live traffic
# analysis (2026-10-19, CYVR departure) showed PSX silently correcting any
# such negative hiCloudBase to exactly 100ft ~10ms after accepting it --
# same tight-window self-correction signature as the Wx sentinel/CB-top
# ceiling cases in fw_cb.py's CB_TOP_MAX_FT comment. Clamping to PSX's own
# observed floor here avoids ever sending it a value it has to correct.
_MSFS_CLOUD_BASE_FLOOR_FT = 100.0
# WxBasic (planet weather) is only written while on the ground (below this
# TAS) or while the aircraft is actually in it (FocussedWxZone 0) -- see
# _write_wxbasic(). Live traffic analysis (2026-10-20, CYVR-DNMM): every
# cruise PFD altitude snap >=9ft coincided with a WxBasic write in the same
# zone batch, against 0 of 260 batches without one.
_WXBASIC_MAX_WRITE_TAS_KT = 50.0
# --sync-tropopause in flight (TAS >= _TROPO_INFLIGHT_MIN_TAS_KT): every
# WxAloft write can snap pending smooth transits like WxBasic does
# (2026-10-22 VHHH-EGLL: 7 of 17 cruise batches with a WxAloft write
# jumped the PFD up to 81ft, 0 of 45 without). The real tropopause only
# changes on a weather-system scale, so in flight we write the median of
# the last _TROPO_SMOOTH_WINDOW_S of samples, and only when it differs
# from what PSX has by at least one of the _TROPO_MIN_CHANGE_* amounts,
# at most once per _TROPO_MIN_WRITE_INTERVAL_S. On the ground every new
# value is written straight away. See _write_tropopause().
# PSX's own "auto" tropopause is fine for most purposes, so we only set a
# fixed one from reliable data: not during the first
# _TROPO_STARTUP_HOLDOFF_S after startup (WxAloft is returned to auto
# instead), and only from the median of at least _TROPO_MIN_SAMPLES
# samples. A jump of more than _TROPO_RESET_DIST_NM between two samples
# (situ load, reposition) discards the earlier ones. PSX caps the
# tropopause at _TROPO_MAX_ALT_FT; writing more just reads back as the cap.
_TROPO_INFLIGHT_MIN_TAS_KT = 50.0
_TROPO_STARTUP_HOLDOFF_S = 900.0
_TROPO_MIN_SAMPLES = 3
_TROPO_RESET_DIST_NM = 150.0
_TROPO_MAX_ALT_FT = 50000.0
_TROPO_SMOOTH_WINDOW_S = 3600.0
_TROPO_MIN_WRITE_INTERVAL_S = 1800.0
_TROPO_MIN_CHANGE_FT = 3000.0
_TROPO_MIN_CHANGE_C = 5.0
_TROPO_MIN_CHANGE_WIND_KT = 30.0    # vector difference
_NM_TO_M = 1852.0
# How long a paused instance waits without hearing from the conflicting
# (higher-UUID) FRANKENWEATHER instance before resuming — see _note_conflict()
# and _conflict_paused().
_CONFLICT_TIMEOUT_S = 120.0
# How often a paused instance reminds the console that it's paused.
_CONFLICT_LOG_INTERVAL_S = 60.0
# PSX Qi243 ("WxSlowTransit"): a timing window (ms) during which any weather
# variable write PSX receives is handled as a smooth transit instead of an
# instant jump (per the PSX forum's own description of the variable) — once a
# transit is smooth it stays smooth even after the window itself expires. We
# never want an instant jump (an abrupt QNH/OAT/wind change reads on the
# instruments as if the aircraft teleported), and never need PSX's fast-mode
# transit either, so the window can just be generous.
_WX_SLOW_TRANSIT_MS = 2000

# CB growth/decay ramping (--cb-growth, cruise mode only): see
# Script._ramp_cb_wx()/docs/frankenweather.md. Instead of a CB's oktas/top
# snapping instantly to its forecast target, they ramp gradually -- growth
# is sized at creation so a CB doesn't look like it "popped into view" at a
# distance where it'd be noticeable, and (subject to the rate cap below)
# finishes growing before the aircraft reaches it; shrinking/removal always
# uses the fixed decay rate below, since a departing zone doesn't need to
# hurry. Base (field 11) is never ramped -- it's applied straight from the
# forecast every cycle.
_CB_GROWTH_START_PCT_RANGES = (
    # (dist_nm threshold, low_pct, high_pct) -- highest threshold first; the
    # first range whose threshold the distance exceeds applies, picking a
    # uniformly random percentage within it (never a single fixed value) --
    # see _cb_growth_start_pct(). A CB this far out is too far away to
    # assess precisely even at the high end of its range, so starting
    # closer to fully-formed doesn't read as a "pop".
    (200.0, 0.70, 1.00),
    (150.0, 0.50, 0.80),
    (100.0, 0.30, 0.60),
    (50.0, 0.10, 0.40),
)
_CB_GROWTH_START_PCT_RANGE_BELOW = (0.05, 0.20)  # dist_nm <= 50
_CB_GROWTH_MIN_TOP_ABOVE_BASE_FT = 3000.0   # "a few thousand feet above the base"
_CB_GROWTH_MIN_OKTAS = 1                    # never less than 1/8 once a CB exists
_CB_GROWTH_ASSUMED_GS_KT = 420.0            # ETA fallback if ac_tas_kt isn't known yet
# Hard cap on the ETA-sized growth rate, regardless of distance -- avoiding
# the "pops up right next to the aircraft" effect matters more than a
# close-in CB finishing growth before arrival, so a CB created with little
# runway left (e.g. one appearing in an already-nearby zone) simply arrives
# still under-grown rather than rushing to finish in a minute or two. This
# is the floor of the CAPE-scaled cap below (i.e. the cap for weak/no-CAPE
# CBs, e.g. one forced by a TS SIGMET/METAR override with little CAPE
# behind it) -- higher CAPE only ever raises it.
_CB_GROWTH_MAX_TOP_FT_PER_MIN = 1000.0
_CB_GROWTH_MAX_OKTAS_PER_MIN = 0.15
# Real updrafts scale roughly with sqrt(2*CAPE) (parcel theory's unmixed
# max vertical velocity); real-world updrafts run well under that unmixed
# max (entrainment), and the visible cloud *top* rises slower still than
# the internal updraft. _CB_GROWTH_CAPE_UPDRAFT_FRACTION folds both of
# those discounts into one empirical factor, picked so the resulting top
# growth rate lands in the ~1500-4000 ft/min range meteorology references
# give for an actively-growing CB at moderate-to-severe CAPE (1500-3000
# J/kg) -- see the growth-rate/CAPE discussion in
# docs/Changelog-frankenweather.md. sqrt(CAPE) has no ceiling of its own,
# so _CB_GROWTH_MAX_TOP_FT_PER_MIN_CEILING caps the result at the fast end
# of that same real-world range -- without it, extreme CAPE (rare, but
# Open-Meteo can report it) would keep accelerating the rate even after
# cb_tops_ft()'s own CB_TOP_MAX_FT forecast ceiling (reached by CAPE ~1250
# J/kg) stops giving it any more height left to climb towards.
_CB_GROWTH_CAPE_UPDRAFT_FRACTION = 0.25
_CB_GROWTH_MAX_TOP_FT_PER_MIN_CEILING = 4000.0
_MPS_TO_FT_PER_MIN = 196.85
_CB_DECAY_TOP_FT_PER_MIN = 150.0            # fixed, real-CB-collapse-plausible rate
_CB_DECAY_OKTAS_PER_MIN = 0.1               # ~1 okta every 10 minutes
# Without --cb-growth a disappearing CB fades instead (_fade_cb_wx()):
# 8 oktas / 40000 ft gone in ~13-15 min
_CB_FADE_OKTAS_PER_MIN = 0.6
_CB_FADE_TOP_FT_PER_MIN = 2500.0

_HDG_WINDOW_S = 300.0                 # heading-change detection window (5 min)
_MANEUVER_ENTER_DEG = 180.0           # total hdg change to enter maneuvering mode
_MANEUVER_EXIT_DEG = 60.0             # total hdg change to exit maneuvering mode

# ---------------------------------------------------------------------------
# Radar (RainViewer) and lightning (Blitzortung) constants
# ---------------------------------------------------------------------------

_RV_API = "https://api.rainviewer.com/public/weather-maps.json"
_RV_TILE = "https://tilecache.rainviewer.com"
_RV_ZOOM = 6           # zoom 6 → ~22 km/pixel at equator, ~5°×5° per tile
_RV_TILE_PX = 256
_RV_ECHO_RADIUS = 3    # pixel radius to sample around target point
_RV_TIMEOUT = aiohttp.ClientTimeout(total=15)
_RV_DENSITY_SCAN_NM = 150.0   # radius of local echo scan around aircraft
_RV_DENSITY_GRID = 5          # NxN sample grid (5×5 = 25 points)
_RV_DENSITY_THRESHOLD = 0.25  # echo≥2 fraction triggering full CB squeeze
# The CB squeeze never places a new zone closer than this ahead of the
# aircraft (unless --new-zone-infront-range's own MIN is already lower): a
# real crew would be deviating around a CB well before it got this close,
# so a CB that first appears nearer than this looks like it popped up out
# of nowhere. Also keeps squeezed zones from being relocated again within
# minutes (2026-10-21 DNMM-VVDN: squeezed to 50nm, zones lived 3-11min).
_CB_SQUEEZE_MIN_FWD_NM = 100.0
# A zone already within _CB_SQUEEZE_MIN_FWD_NM that gets a CB (a new or
# moved TS SIGMET, CAPE, METAR...) only gets a small one, kept until the
# zone is moved: a 45,000 ft CB appearing 10 nm away is startling. A CB it
# already had there may not grow. See Script._limit_close_cb().
_CB_CLOSE_MAX_OKTAS = 2
_CB_CLOSE_MAX_TOP_FT = 15000.0
# No new limit this long after frankenweather starts: PSX may already show
# the zones' CBs, and SIGMET, METAR and CAPE CBs only arrive over the first
# updates. A restart inside Typhoon IN-FA's TC SIGMET (frankensim
# 2026-10-29) limited the zones near the aircraft while their TC CBs
# weren't applied yet, cutting 44,999 ft CBs to 15,000 ft.
_CB_CLOSE_START_GRACE_S = 120.0
# Inside a TS/TC SIGMET area (frankenweather handling them, --sigmet) zones
# are placed further from the aircraft and from each other: PSX draws a
# CB's echo, and frankenweather sizes its turbulence, by the distance to
# the nearest other zone, and the all-around placement (startup,
# reposition, maneuvering) could put zones anywhere from 0 nm, packed 50 nm
# apart. In Typhoon IN-FA (frankensim 2026-10-29) that gave small echoes,
# small cores, and new zones 70 nm away capped at 15,000 ft. Spreading the
# 7 zones also covers more of the area ahead with CBs. 90 nm apart didn't
# fit: forward placements (150-250 nm ahead, 100 nm either side) hold about
# three zones that far apart, so most fell back to the best of the 10
# tries and ended up 46-76 nm apart (frankensim 2026-10-29).
_CB_AREA_MIN_DIST_NM = 60.0
# ... and the close-CB limit (_limit_close_cb()) only reaches this far
# there, not _CB_SQUEEZE_MIN_FWD_NM: CBs are expected all over such an
# area, so a new cell 60-100 nm away isn't startling, but maneuvering there
# (a hold, a deviation) placed zones 60-100 nm away and cut their TS CBs
# to 2 oktas, 15,000 ft (test sim 2026-10-29, TS SIGMET NTTT A1).
_CB_CLOSE_AREA_NM = 40.0
_CB_AREA_ZONE_SEP_NM = 70.0

_BZ_URL = "https://data.blitzortung.org/Data_1/strikes"
_BZ_LOOKBACK_MIN = 20  # fetch this many recent minute-files of strike data
_BZ_RADIUS_NM = 80.0   # strike within this radius → lightning signal for CB gate
_BZ_CACHE_S = 240.0    # re-fetch Blitzortung no more often than this

# ---------------------------------------------------------------------------
# Coordinate parsing (UCAR stations.txt format)
# ---------------------------------------------------------------------------


def _parse_ucar_lat(s: str) -> Optional[float]:
    s = s.strip()
    if len(s) < 4:
        return None
    hem = s[-1].upper()
    if hem not in ('N', 'S'):
        return None
    try:
        parts = s[:-1].split()
        val = int(parts[0]) + float(parts[1]) / 60.0
        return -val if hem == 'S' else val
    except (ValueError, IndexError):
        return None


def _parse_ucar_lon(s: str) -> Optional[float]:
    s = s.strip()
    if len(s) < 5:
        return None
    hem = s[-1].upper()
    if hem not in ('E', 'W'):
        return None
    try:
        parts = s[:-1].split()
        val = int(parts[0]) + float(parts[1]) / 60.0
        return -val if hem == 'W' else val
    except (ValueError, IndexError):
        return None


# ICAO -> elevation (m) from stations.txt, filled by _parse_airports_lines()
_AIRPORT_ELEV_M: dict = {}


def _airport_elev_m(icao: str, default: float) -> float:
    """Return an airport's elevation in metres (stations.txt), or default if unknown."""
    return _AIRPORT_ELEV_M.get(icao, default)


def _parse_airports_lines(lines) -> dict:
    airports: dict = {}
    for line in lines:
        if not line or line[0] == '!' or len(line) < 55:
            continue
        icao = line[20:24]
        if not (icao[:1].isalpha() and icao.isalnum() and icao != 'ICAO'):
            continue
        lat = _parse_ucar_lat(line[39:45])
        lon = _parse_ucar_lon(line[47:54])
        if lat is None or lon is None:
            continue
        airports[icao] = (lat, lon)
        try:
            _AIRPORT_ELEV_M[icao] = float(line[55:59])
        except ValueError:
            pass
    return airports


def load_airports(path: str) -> dict:
    """Load ICAO → (lat_deg, lon_deg) from a local UCAR stations.txt file."""
    with open(path, encoding="latin-1") as fh:
        return _parse_airports_lines(fh)


def download_airports(url: str) -> dict:
    """Download and parse UCAR stations.txt from url."""
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode('latin-1')
    return _parse_airports_lines(text.splitlines())


def get_airports(url: str, cache_path: str) -> tuple:
    """Return airports dict loaded from cache, or downloaded and cached if absent.

    Returns ``(airports, source_description)`` where source_description is a
    human-readable string suitable for logging.
    """
    if os.path.exists(cache_path):
        return load_airports(cache_path), f"cache ({cache_path})"
    with urllib.request.urlopen(url, timeout=30) as resp:
        text = resp.read().decode('latin-1')
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    with open(cache_path, "w", encoding="latin-1") as fh:
        fh.write(text)
    return _parse_airports_lines(text.splitlines()), url


# ---------------------------------------------------------------------------
# FMC route waypoint parsing (mirrors frankenpush.py's _parse_route_waypoints
# and psccfc/connector/frdp.py — kept in sync by hand)
# ---------------------------------------------------------------------------

_FMC_WAYPOINT_PREFIX_LEN = 10
_FMC_WAYPOINT_SENTINEL_LATLON = "9.0/9.0"

# Field 1 of an FmcRte entry tags the waypoint with whatever it's "on": an
# airway/track identifier for a plain enroute leg (e.g. "N261B", "P2", or a
# North Atlantic Track "NATW"), or a SID/STAR/approach construct name for a
# terminal-procedure leg (e.g. "CELTK7", "SIRI1H", "APPR_TRANS", "ILS_27L",
# "MISSED_APPR"). Airway/track identifiers always lead with 1-2 letters then
# digits (NAT tracks are the sole all-letter exception); procedure names lead
# with a longer alphabetic fix name before any digit — that's the reliable
# split, since plenty of legitimate enroute fixes (e.g. NAT-track waypoints)
# would otherwise look identical to STAR fixes by name alone.
_AIRWAY_TAG_RE = re.compile(r'[A-Z]{1,2}\d{1,4}[A-Z]?')
_NAT_TRACK_TAG_RE = re.compile(r'NAT[A-Z]')


def _is_airway_or_track_tag(tag: str) -> bool:
    """Return True if an FmcRte field-1 tag looks like an airway/track id, not a procedure name."""
    return bool(_AIRWAY_TAG_RE.fullmatch(tag) or _NAT_TRACK_TAG_RE.fullmatch(tag))


def _parse_fmc_route_waypoints(route: str) -> list:
    """Extract [(name, lat_deg, lon_deg), ...] entries from a PSX FmcRte string.

    Waypoints tagged with a SID/STAR/approach procedure name (see
    _is_airway_or_track_tag) are excluded — this also transparently covers
    every "("-prefixed pseudo-waypoint (top of descent, intercepts,
    runway-relative markers, ...), since those are always procedure-tagged,
    and real dispatch wind corridors never cover SID/STAR/approach construct
    fixes in the first place.
    """
    if not route or "#" not in route:
        return []
    _header, _sep, body = route.partition("#")
    waypoints = []
    for entry in body.split(";"):
        if not entry:
            continue
        fields = entry.split("'")
        if len(fields) < 4:
            continue
        if fields[1] and not _is_airway_or_track_tag(fields[1]):
            continue  # part of a SID/STAR/approach procedure — skip
        name = fields[0][_FMC_WAYPOINT_PREFIX_LEN:].strip().upper()
        latlon = fields[3]
        if not name or latlon == _FMC_WAYPOINT_SENTINEL_LATLON:
            continue
        lat_str, _sep2, lon_str = latlon.partition("/")
        try:
            lat_deg = math.degrees(float(lat_str))
            lon_deg = math.degrees(float(lon_str))
        except ValueError:
            continue
        waypoints.append((name, lat_deg, lon_deg))
    return waypoints


def _is_name_suffix(candidate: tuple, full: list) -> bool:
    """Return True if `candidate` is a non-empty tail-match of the `full` name sequence."""
    if not candidate or len(candidate) > len(full):
        return False
    return full[len(full) - len(candidate):] == list(candidate)


# ---------------------------------------------------------------------------
# PSX weather field builders
# ---------------------------------------------------------------------------

def _cloud_pct_to_oktas(pct: float) -> int:
    """Convert cloud cover percentage to oktas (0–8)."""
    if pct < 6.25:
        return 0  # SKC
    if pct < 25.0:
        return 2  # FEW
    if pct < 50.0:
        return 4  # SCT
    if pct < 87.5:
        return 6  # BKN
    return 8      # OVC


def _hpa_to_psx_qnh(hpa: float) -> int:
    """Convert hPa to inHg×100 (PSX QNH format)."""
    return int(round(hpa * 2.953))


def _cloud_base_ft(wmo_code: int) -> int:
    """Estimate cloud base in feet from WMO weather code."""
    if wmo_code in (45, 48):
        return 100
    if wmo_code in (51, 53, 55, 56, 57):
        return 500
    if wmo_code in (61, 63, 65, 66, 67):
        return 1000
    if wmo_code in (71, 73, 75, 77):
        return 800
    if wmo_code in (80, 81, 82, 85, 86, 95, 96, 99):
        return 1500
    return 2000


# Reverse map: CB oktas → representative CAPE (J/kg), used to estimate
# regional convective intensity from already-computed zone weather strings.
_OKTAS_TO_CAPE = {0: 0.0, 2: 300.0, 4: 1000.0, 6: 2250.0, 8: 4000.0}


def _parse_vatsim_metar_lines(text: str) -> dict:
    """Parse a VATSIM METAR dump (bulk /all or a live ICAO-list fetch) into icao -> raw line."""
    metars: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if parts:
            icao = parts[0]
            if len(icao) == 4 and icao.upper() == icao and icao.isalnum():
                metars[icao] = line
    return metars


_METAR_WIND_RE = re.compile(r'(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT')
_METAR_SKY_RE = re.compile(r'^(FEW|SCT|BKN|OVC)(\d{3})(CB|TCU)?')
_METAR_SKY_OKTAS = {'FEW': 2, 'SCT': 4, 'BKN': 6, 'OVC': 8}
_METAR_TEMP_RE = re.compile(r'^(M?\d{1,2})/(M?\d{1,2})$')
_METAR_QNH_RE = re.compile(r'^Q(\d{4})$')
_METAR_ALT_RE = re.compile(r'^A(\d{4})$')
# Trend forecast group markers (ICAO Annex 3 2-hour trend, appended to some
# METARs, mainly at European stations) and the ICAO-nonstandard PROBnn
# probability prefix that VATSIM-sourced reports occasionally carry (PROBnn
# is formally a TAF-only construct, but real-world feeds don't always stick
# to that, so it's handled defensively here). Content in these groups
# describes conditions that may occur *during the trend period*, not the
# current observation -- see _parse_metar()'s handling below.
_METAR_TREND_MARKERS = ('TEMPO', 'BECMG')
_METAR_PROB_RE = re.compile(r'^PROB(\d{2})$')


def _update_metar_qnh(metar: str, qnh_hpa: float) -> str:
    """Replace the Q-format QNH token in a METAR string with a new value."""
    return re.sub(r'\bQ\d{4}\b', f"Q{int(round(qnh_hpa)):04d}", metar)


_METAR_OBS_TIME_RE = re.compile(r'\b(\d{2})(\d{2})(\d{2})Z\b')

# A real METAR this old is more likely a dead/non-reporting station (e.g. an
# airport whose real-world ATC has been shut down, such as several in
# Ukraine since the 2022 airspace closure -- VATSIM keeps echoing their last
# real observation indefinitely, sometimes years old) than a genuinely
# current one. Prefer Open-Meteo over trusting it in that case.
_METAR_MAX_AGE_HOURS = 2.0


def _noaa_metar_better(new: str, old: Optional[str], now: datetime) -> bool:
    """Return whether NOAA report new should replace old for the same station.

    NOAA's cycle files repeat each station many times and are not in
    observation-time order: older reports get re-sent after newer ones
    (seen live: 13 stations in one file whose last line wasn't their
    newest report), and an uncorrected original can be re-sent after its
    COR. So: newest observation time wins; at equal time a COR beats a
    non-COR; a copy that is just old with its remarks cut off never
    replaces it; otherwise the later line wins (corrections without a
    COR marker are seen in the wild too). Same rules as
    compare_metars.py's _keep_newest().
    """
    if old is None:
        return True
    old_age = _metar_age_hours(old, now)
    if old_age is None:
        return True
    new_age = _metar_age_hours(new, now)
    if new_age is None or new_age > old_age:
        return False
    if new_age < old_age:
        return True
    new_cor, old_cor = " COR " in f" {new} ", " COR " in f" {old} "
    if old_cor and not new_cor:
        return False
    return not (new_cor == old_cor and old.startswith(new))


def _metar_age_hours(metar: str, now: Optional[datetime] = None) -> Optional[float]:
    """Return how many hours old metar's own DDHHMMZ observation time is.

    METAR only carries day-of-month/hour/minute, not month/year, so a
    report could in principle be from any past month sharing that
    day-of-month/time -- e.g. a station that hasn't reported since 2022.
    We only need to know whether it's recent, so this checks the two
    candidates that could plausibly be "recent" (this month, and last
    month, to handle reports from the tail end of the previous month when
    `now` is early in a new one) and returns the smaller resulting
    non-negative age. Returns None if no observation-time group is found.
    """
    m = _METAR_OBS_TIME_RE.search(metar)
    if not m:
        return None
    day, hour, minute = (int(g) for g in m.groups())
    now = now or datetime.now(timezone.utc)
    best_age_hours = None
    year, month = now.year, now.month
    for _ in range(2):
        try:
            candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            candidate = None
        if candidate is not None:
            age_hours = (now - candidate).total_seconds() / 3600.0
            if age_hours >= 0 and (best_age_hours is None or age_hours < best_age_hours):
                best_age_hours = age_hours
        month -= 1
        if month == 0:
            month, year = 12, year - 1
    return best_age_hours


def _metar_is_fresh(metar: str, now: Optional[datetime] = None) -> bool:
    """Return True if metar's own observation time is within _METAR_MAX_AGE_HOURS."""
    age = _metar_age_hours(metar, now)
    return age is not None and age <= _METAR_MAX_AGE_HOURS


# The planet zone's 4 CB cells (WxClust) are parked at Null Island (0N 0E),
# where they're harmless (no CB coverage there anyway, see _without_cb())
# and where maps showing WxClust (the router's /efb map) put them out of the
# way. PSX replaces a set with any cell more than about 250 nm from the
# aircraft within some 15 s (test sim 2026-10-27), so they're checked and
# re-parked every _WXCLUST_PARK_S.
_WXCLUST_PARKED = ";".join(["0.00000000"] * 8)
_WXCLUST_PARK_S = 10.0


def _without_cb(wx_str: str) -> str:
    """Return a Wx string with no CB coverage, for WxBasic (the planet zone).

    PSX gives the planet zone 4 CB cells of its own (WxClust) wherever it
    likes, with the planet zone's CB coverage, base and top; they're drawn
    on the radar while the aircraft is in the planet zone (test sim
    2026-10-28: not at all while it's in one of our 7 zones). CBs there would
    be unintended ones the moment the aircraft leaves our zones, so the
    planet zone never gets any from us (a copy of zone 1, manual weather;
    MSFS sync doesn't write it at all).
    """
    fields = wx_str.split(";")
    if len(fields) > 9 and fields[9] not in ("0", ""):
        fields[9] = "0"
    return ";".join(fields)


def _metar_age_text(age_h: Optional[float]) -> str:
    """Return age_h as e.g. "2h old", or "undated" if it is unknown (no DDHHMMZ group)."""
    return "undated" if age_h is None else f"{age_h:.0f}h old"


_SIGMET_HAZARDS = frozenset({'TS', 'TURB', 'ICE', 'VA', 'TC', 'MTW'})
_SIGMET_COORD_RE = re.compile(r'([NS])(\d{2})(\d{2})\s*([EW])(\d{3})(\d{2})')
_SIGMET_COORD_GAP_MAX = 12            # max chars between coords still one polygon
_SIGMET_LINE_RE = re.compile(r'\bLINE\b', re.IGNORECASE)
_SIGMET_ALT_RE = re.compile(
    r'(?:(?:TOP\s+)?ABV\s+FL(\d{3}))'
    r'|(?:TOP\s+FL(\d{3}))'
    r'|(?:FL(\d{3})/(?:FL)?(\d{3}))'
    r'|(?:SFC/FL(\d{3}))'
    r'|(?:(\d+)FT/FL(\d{3}))'
    r'|(?:SFC/(\d+)FT)')


def _sigmet_alt_range(text: str) -> tuple:  # pylint: disable=too-many-return-statements
    """Return (base_ft, top_ft) from SIGMET text, defaulting to (0, 35000) if not found."""
    m = _SIGMET_ALT_RE.search(text)
    if not m:
        return 0, 35000
    if m.group(1):       # "TOP ABV FL500" or bare "ABV FL360" -- top only
        return 0, int(m.group(1)) * 100
    if m.group(2):       # TOP FL260
        return 0, int(m.group(2)) * 100
    if m.group(3):       # FL140/180 or FL065/080
        return int(m.group(3)) * 100, int(m.group(4)) * 100
    if m.group(5):       # SFC/FL100
        return 0, int(m.group(5)) * 100
    if m.group(6):       # 8000FT/FL180
        return int(m.group(6)), int(m.group(7)) * 100
    if m.group(8):       # SFC/6000FT
        return 0, int(m.group(8))
    return 0, 35000


def _sigmet_first_polygon(text: str) -> list:
    """Return the first closed-area coordinate list (>=3 points) in SIGMET text, or [].

    Takes only the first run of coordinates close enough together to
    plausibly be one polygon -- a later run (e.g. a separate forecast-time
    polygon in the same entry) is ignored in favor of the near-term/observed
    one. Entries describing an open boundary ("N OF LINE ...", "WI 60NM WID
    LINE BTN ...") rather than a closed area are skipped outright: connecting
    a boundary line's own endpoints into a polygon would draw a shape
    nothing like the real (unbounded) hazard area, which is worse than
    having none. Not aiming for perfect coverage here -- real-world SIGMET
    text is inconsistent enough that PSX's own built-in parser doesn't get
    every entry right either.
    """
    matches = list(_SIGMET_COORD_RE.finditer(text))
    if not matches:
        return []
    clusters = [[matches[0]]]
    for prev, cur in zip(matches, matches[1:]):
        if cur.start() - prev.end() <= _SIGMET_COORD_GAP_MAX:
            clusters[-1].append(cur)
        else:
            clusters.append([cur])
    for cluster in clusters:
        if len(cluster) < 3:
            continue
        preceding = text[max(0, cluster[0].start() - 25):cluster[0].start()]
        if _SIGMET_LINE_RE.search(preceding):
            continue
        return [
            ((-1 if m.group(1) == 'S' else 1) * (int(m.group(2)) + int(m.group(3)) / 60.0),
             (-1 if m.group(4) == 'W' else 1) * (int(m.group(5)) + int(m.group(6)) / 60.0))
            for m in cluster
        ]
    return []


# ---------------------------------------------------------------------------
# Radar helpers (RainViewer colour scheme 2 — logic shared with fw_scanner.py)
# ---------------------------------------------------------------------------

def _rv_pixel_echo_strength(r: int, g: int, b: int, a: int) -> int:
    """Map an RGBA pixel from RainViewer scheme 2 to echo strength 0–3."""
    if a < 30:
        return 0
    if b > max(r, g):
        return 1   # blue-dominant: light rain (15-30 dBZ)
    if g >= r:
        return 2   # green-dominant: moderate (35-50 dBZ)
    return 3       # warm (yellow/orange/red): heavy / CB core (50+ dBZ)


def _rv_tile_xy(lat: float, lon: float, zoom: int) -> tuple:
    """Convert lat/lon to slippy-map tile (tx, ty) at given zoom."""
    n = 2 ** zoom
    tx = int((lon + 180) / 360 * n)
    lat_r = math.radians(lat)
    ty = int((1 - math.log(math.tan(lat_r) + 1 / math.cos(lat_r)) / math.pi) / 2 * n)
    return tx, ty


def _rv_pixel_in_tile(lat: float, lon: float, zoom: int) -> tuple:
    """Pixel (x, y) within the tile that contains this lat/lon."""
    n = 2 ** zoom
    lat_r = math.radians(lat)
    px = int(((lon + 180) / 360 * n * _RV_TILE_PX) % _RV_TILE_PX)
    log_term = math.log(math.tan(lat_r) + 1 / math.cos(lat_r))
    py = int(((1 - log_term / math.pi) / 2 * n * _RV_TILE_PX) % _RV_TILE_PX)
    return px, py


def _rv_tile_echo(img: object, px: int, py: int) -> int:
    """Maximum echo strength in a small box around pixel (px, py) in a PIL image."""
    w, h = img.size
    best = 0
    for dy in range(-_RV_ECHO_RADIUS, _RV_ECHO_RADIUS + 1):
        for dx in range(-_RV_ECHO_RADIUS, _RV_ECHO_RADIUS + 1):
            x, y = px + dx, py + dy
            if 0 <= x < w and 0 <= y < h:
                best = max(best, _rv_pixel_echo_strength(*img.getpixel((x, y))))
    return best


def _point_in_polygon(lat: float, lon: float, polygon: list) -> bool:
    """Ray-casting point-in-polygon test for (lat, lon) vertex lists."""
    n = len(polygon)
    if n < 3:
        return False
    inside = False
    j = n - 1
    for i in range(n):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        if ((yi > lat) != (yj > lat)) and \
                (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi):
            inside = not inside
        j = i
    return inside


def _parse_sigmets(raw: str) -> list:
    """Parse WxSigmet string into a list of active SIGMET dicts, any recognized hazard.

    Each dict has 'hazard' (e.g. 'TS'/'TURB'/'ICE'/'VA'/'TC'/'MTW'), 'polygon'
    (list of (lat, lon) tuples, >=3 points) and 'base_ft'/'top_ft'. Entries
    with an unrecognized hazard tag, or no usable polygon (see
    _sigmet_first_polygon), are skipped. PSX's WxSigmet feed also includes
    unstructured US "CONVECTIVE SIGMET" text blocks (no "Hazard:" tag, areas
    given as distance+bearing from named navaids rather than lat/lon) --
    those would need a navaid position database to resolve and aren't
    parsed; they're silently skipped here since they have no "Hazard:" line
    to match in the first place.
    """
    result = []
    lines = raw.split('^')
    idx = 0
    while idx < len(lines):
        stripped = lines[idx].strip()
        # PSX's own casing for this line is unconfirmed -- samples seen have used
        # both "Hazard:" and "HAZARD:" -- so match case-insensitively throughout.
        if not stripped.upper().startswith('HAZARD:'):
            idx += 1
            continue
        hazard = stripped[len('Hazard:'):].strip().upper()
        idx += 1
        entry_lines = []
        while idx < len(lines):
            ln = lines[idx].strip()
            if ln.startswith('---') or ln.upper().startswith('HAZARD:'):
                break
            entry_lines.append(ln)
            idx += 1
        if hazard not in _SIGMET_HAZARDS:
            continue
        text = ' '.join(entry_lines)
        coords = _sigmet_first_polygon(text)
        if len(coords) < 3:
            continue
        base_ft, top_ft = _sigmet_alt_range(text)
        if hazard == 'TS':
            # _sigmet_cb_override() wants a sane minimum CB top even when the
            # SIGMET text itself under-reports it.
            top_ft = max(top_ft, 25000)
        result.append({'hazard': hazard, 'polygon': coords, 'base_ft': base_ft, 'top_ft': top_ft})
    return result


# Hail; not GS (small hail/snow pellets, mostly winter showers -- shower
# precipitation for _METAR_SH_RE)
_METAR_TIME_RE = re.compile(r'^\d{6}Z$')
_METAR_GR_RE = re.compile(r'^[+-]?(SH|TS|VC|FZ|BL|DR|MI|PR)?GR$')
_METAR_SH_RE = re.compile(r'^[+-]?SH[A-Z]{2,4}$')
_METAR_FC_RE = re.compile(r'^[+-]?FC$')
_METAR_VIS4_RE = re.compile(r'^\d{4}$')
_METAR_VISSM_RE = re.compile(r'^(\d+(?:/\d+)?)SM$')


def _metar_vis(token: str) -> 'int | None':
    """Parse a METAR visibility token; return metres or None if not a vis token."""
    if _METAR_VIS4_RE.match(token):
        return int(token)
    m = _METAR_VISSM_RE.match(token)
    if m:
        p = m.group(1).split('/')
        sm = float(p[0]) / float(p[1]) if len(p) == 2 else float(p[0])
        return int(sm * 1609.34)
    return None


def _metar_parse_temp(s: str) -> int:
    """Parse a METAR temperature field (possibly M-prefixed negative)."""
    return -int(s[1:]) if s.startswith('M') else int(s)


def _metar_wx_token(token: str, out: dict) -> None:
    """Update convective flags in parsed METAR dict from a present-weather token.

    Only real weather groups count: 'TS' in e.g. the station VTSP (Phuket)
    used to give a permanent 4-okta CB. TSNO ("thunderstorm information
    not available") isn't one. Recent weather (RETS) counts as vicinity.
    """
    if not _METAR_PRESENT_WX_RE.match(token):
        return
    if token.startswith('RE'):
        if 'TS' in token:
            out['ts_oktas'] = max(out['ts_oktas'], 2)
    elif 'TS' in token:
        if token.startswith('+'):
            out['ts_oktas'] = max(out['ts_oktas'], 8)
        elif token.startswith('-') or 'VC' in token:
            out['ts_oktas'] = max(out['ts_oktas'], 2)
        else:
            out['ts_oktas'] = max(out['ts_oktas'], 4)
    elif _METAR_GR_RE.match(token):
        out['ts_oktas'] = max(out['ts_oktas'], 4)
    elif _METAR_FC_RE.match(token):
        out['ts_oktas'] = max(out['ts_oktas'], 8)
    elif _METAR_SH_RE.match(token):
        out['showers'] = True


def _parse_trend_segment(tokens: list) -> dict:
    """Parse the convective signal (CB oktas, showers) out of one trend segment.

    Trend segments (TEMPO/BECMG, optionally PROBnn-prefixed) describe
    conditions that may occur during the trend period, not the current
    observation -- see _parse_metar(). Only the convective fields are
    extracted here; FrankenWeather does not model trend-driven visibility
    or general (non-CB) cloud changes, only CB coverage.
    """
    seg: dict = {'ts_oktas': 0, 'showers': False}
    for token in tokens:
        m = _METAR_SKY_RE.match(token)
        if m:
            cloud_type = m.group(3)
            if cloud_type == 'CB':
                seg['ts_oktas'] = max(seg['ts_oktas'], _METAR_SKY_OKTAS[m.group(1)])
            elif cloud_type == 'TCU':
                seg['showers'] = True
            continue
        _metar_wx_token(token, seg)
    return seg


def _parse_metar_trends(tokens: list) -> list:
    """Split trend-group tokens (everything after the main body, before RMK) into segments.

    Each segment is one TEMPO or BECMG group, optionally preceded by a
    PROBnn probability prefix (e.g. "PROB40 TEMPO ..."). PROBnn is formally
    a TAF-only construct per ICAO Annex 3, and there it's always followed
    by TEMPO/BECMG/an FM group -- but real-world METAR feeds (e.g. VATSIM)
    sometimes carry a bare "PROBnn <weather-elements>" with no following
    TEMPO/BECMG at all, so that's recognized too, as its own 'PROB' segment
    type (see Script._trend_cb_contribution() for how 'PROB' vs 'TEMPO' are
    applied differently). Returns a list of {'type': 'TEMPO'|'BECMG'|'PROB',
    'prob': int|None, 'ts_oktas': int, 'showers': bool}.
    """
    segments = []
    i = 0
    n = len(tokens)
    while i < n:
        prob = None
        m = _METAR_PROB_RE.match(tokens[i])
        if m:
            prob = int(m.group(1))
            i += 1
            if i >= n:
                break
        if i < n and tokens[i] in _METAR_TREND_MARKERS:
            seg_type = tokens[i]
            i += 1
        elif prob is not None:
            # Bare "PROBnn <weather-elements>" with no TEMPO/BECMG --
            # sustained (not fluctuating) for the whole observation if the
            # roll hits; see _trend_cb_contribution().
            seg_type = 'PROB'
        else:
            # Stray/unrecognized token (e.g. a FMhhmm time group on its
            # own) -- skip it and keep looking rather than aborting the
            # whole scan.
            i += 1
            continue
        seg_tokens = []
        while (i < n and tokens[i] not in _METAR_TREND_MARKERS and
               not _METAR_PROB_RE.match(tokens[i])):
            seg_tokens.append(tokens[i])
            i += 1
        seg = _parse_trend_segment(seg_tokens)
        segments.append({'type': seg_type, 'prob': prob, **seg})
    return segments


def _parse_metar(raw: str) -> dict:  # pylint: disable=too-many-branches,too-many-statements
    """Parse a raw METAR string into a dict of weather fields.

    Only the main observation body -- everything before the first
    TEMPO/BECMG/PROBnn trend group, or the RMK remarks section, whichever
    comes first -- feeds the returned 'ts_oktas'/'showers'/'sky' fields.
    Trend-group content describes conditions that may occur during the
    trend period, not the current observation, and is returned separately
    in 'trends' for the caller to decide whether/how to apply (see
    Script._trend_cb_contribution()).
    """
    out: dict = {
        'wind_dir': 0, 'wind_var': False, 'wind_spd': 0, 'wind_gust': 0,
        'vis_m': 10000, 'sky': [], 'temp_c': 15, 'dp_c': 10, 'qnh_hpa': 1013.0,
        'ts_oktas': 0, 'showers': False, 'trends': [],
    }
    # Skip the header (METAR/SPECI, station, DDHHMMZ): station codes can
    # look like weather (LTGP Gazipasa "LTG", SHTS "SH"+"TS")
    all_tokens = raw.split()
    header = next((i + 1 for i, t in enumerate(all_tokens[:4]) if _METAR_TIME_RE.match(t)),
                  min(1, len(all_tokens)))
    all_tokens = all_tokens[header:]
    body_end = len(all_tokens)
    for idx, token in enumerate(all_tokens):
        if (token == 'RMK' or token in _METAR_TREND_MARKERS or
                _METAR_PROB_RE.match(token)):
            body_end = idx
            break
    for token in all_tokens[:body_end]:
        m = _METAR_WIND_RE.match(token)
        if m:
            out['wind_var'] = m.group(1) == 'VRB'
            out['wind_dir'] = 0 if out['wind_var'] else int(m.group(1))
            out['wind_spd'] = int(m.group(2))
            out['wind_gust'] = int(m.group(3)) if m.group(3) else 0
            continue
        vis = _metar_vis(token)
        if vis is not None:
            out['vis_m'] = vis
            continue
        if token in ('SKC', 'CLR', 'NSC'):
            out['sky'] = []
            continue
        if token == 'CAVOK':
            out['vis_m'] = 10000
            out['sky'] = []
            continue
        m = _METAR_SKY_RE.match(token)
        if m:
            oktas = _METAR_SKY_OKTAS[m.group(1)]
            out['sky'].append((oktas, int(m.group(2)) * 100))
            cloud_type = m.group(3)
            if cloud_type == 'CB':
                out['ts_oktas'] = max(out['ts_oktas'], oktas)
            elif cloud_type == 'TCU':
                out['showers'] = True
            continue
        m = _METAR_TEMP_RE.match(token)
        if m:
            out['temp_c'] = _metar_parse_temp(m.group(1))
            out['dp_c'] = _metar_parse_temp(m.group(2))
            continue
        m = _METAR_QNH_RE.match(token)
        if m:
            out['qnh_hpa'] = float(m.group(1))
            continue
        m = _METAR_ALT_RE.match(token)
        if m:
            out['qnh_hpa'] = round(int(m.group(1)) / 2.953)
            continue
        _metar_wx_token(token, out)
    # Trend groups: everything from body_end up to (not including) RMK.
    trend_tokens = all_tokens[body_end:]
    if trend_tokens and trend_tokens[0] != 'RMK':
        try:
            rmk_idx = trend_tokens.index('RMK')
        except ValueError:
            rmk_idx = len(trend_tokens)
        out['trends'] = _parse_metar_trends(trend_tokens[:rmk_idx])
    # LTG (lightning, e.g. LTGICCG) in remarks; DSNT = distant (~VCTS),
    # otherwise overhead. Whole groups only ("ALTG" isn't lightning).
    ltg = next((i for i, t in enumerate(all_tokens) if t.startswith('LTG')), None)
    if ltg is not None:
        if 'DSNT' in all_tokens[ltg:ltg + 4]:
            out['ts_oktas'] = max(out['ts_oktas'], 2)
        else:
            out['ts_oktas'] = max(out['ts_oktas'], 4)
    return out


_CB_MIN_DEPTH_FT = 5000   # see _cb_top_above_zone()


def _cb_top_above_zone(wx: str, elev_ft: int) -> str:
    """Return wx with its CB top as a height above the zone, and above the base.

    The top ends up at least _CB_MIN_DEPTH_FT above the base.

    CB tops are worked out above sea level (the tropopause, SIGMET flight
    levels), but PSX's Wx heights are above the zone's elevation (live at
    SKBO: METAR FEW030 -> cloud base 3000, zone elevation 8360 ft), so a
    CB there topped out 8360 ft too high. A CB forced (TS METAR/SIGMET)
    with a high base and little CAPE could also end up with its base
    above its top, leaving no CB turbulence at all.
    """
    parts = wx.split(';')
    if len(parts) < 12 or parts[9] in ('', '0'):
        return wx
    base = int(parts[11])
    top = int(parts[10]) - max(0, elev_ft)
    parts[10] = str(min(_CB_TOP_MAX_FT, max(top, base + _CB_MIN_DEPTH_FT)))
    return ';'.join(parts)


def _max_cb(wx: str, other: str) -> str:
    """Return wx with the larger CB coverage and higher top of wx and other (base from wx)."""
    a, b = wx.split(';'), other.split(';')
    a[9] = str(max(int(a[9]), int(b[9])))
    a[10] = str(max(int(a[10]), int(b[10])))
    return ';'.join(a)


def metar_to_wx_string(parsed: dict, lat: Optional[float] = None,
                       month: Optional[int] = None) -> str:
    """Convert a parsed METAR dict to a PSX Wx semicolon string.

    A thunderstorm's CB top is the highest at lat/month (tropopause plus
    an overshoot, see fw_cb.cb_top_ceiling_ft()) for 8 oktas, 5000 ft less
    for 4 and 12000 ft less for 2. It used to be 45000/40000/30000 ft
    everywhere, also in winter.
    """
    data = list(_WX_DEFAULTS)

    best_cov, best_base = 0, 2000
    for oktas, base_ft in parsed['sky']:
        if oktas > best_cov or (oktas == best_cov and base_ft < best_base):
            best_cov, best_base = oktas, base_ft
    if best_cov > 0:
        data[3] = str(best_cov)
        data[4] = str(best_base + 1500)
        data[5] = str(best_base)

    ts_oktas = parsed.get('ts_oktas', 0)
    if ts_oktas > 0:
        cb_base = _cb_base_ft(parsed['temp_c'], parsed['dp_c'])
        ceiling = _cb_top_ceiling_ft(lat, month)
        if ts_oktas >= 8:
            cb_tops = ceiling
        elif ts_oktas >= 4:
            cb_tops = ceiling - 5000
        else:
            cb_tops = ceiling - 12000
        data[9] = str(ts_oktas)
        data[10] = str(cb_tops)
        data[11] = str(cb_base)

    data[18] = f"000{parsed['wind_dir']:03d}{parsed['wind_spd']:02d}"
    data[19] = str(max(parsed['wind_gust'], parsed['wind_spd']))
    data[20] = str(max(min(int(parsed['vis_m']), 9999), 100))
    data[22] = str(int(round(parsed['temp_c'])))
    data[23] = str(_hpa_to_psx_qnh(parsed['qnh_hpa']))

    return ";".join(data)


def _wx_wind_qnh(wx: str) -> tuple:
    """Extract (wind_dir_deg, wind_spd_kt, qnh_hpa) from a PSX Wx semicolon string.

    Field 18 is the wind encoding ("000" + 3-digit dir + 2-digit speed,
    written by both metar_to_wx_string() and om_to_wx_string()); field 23 is
    QNH in PSX's own inHg*100 units. Used only to log when a zone's weather
    content changes between update cycles -- see _update_zones().
    """
    parts = wx.split(';')
    wind_field = parts[18]
    wind_dir = int(wind_field[3:6])
    wind_spd = int(wind_field[6:8])
    qnh_hpa = int(parts[23]) / 2.953
    return wind_dir, wind_spd, qnh_hpa


def om_to_wx_string(om: dict, radar_echo: int = 0,  # pylint: disable=too-many-locals
                    lightning: bool = False) -> str:
    """Convert Open-Meteo current-weather dict to PSX Wx semicolon string."""
    data = list(_WX_DEFAULTS)
    cur = om.get("current", {})

    wind_dir = int(round(float(cur.get("wind_direction_10m", 0)) / 10.0)) * 10
    wind_spd = int(round(float(cur.get("wind_speed_10m", 0))))
    wind_gust = int(round(float(cur.get("wind_gusts_10m", 0))))
    vis_m = int(min(float(cur.get("visibility", 9999)), 9999))
    wmo_code = int(cur.get("weather_code", 0))
    cloud_pct = float(cur.get("cloud_cover", 0))
    temp_c = int(round(float(cur.get("temperature_2m", 15))))
    qnh_hpa = float(cur.get("pressure_msl", 1013.25))

    cloud_oktas = _cloud_pct_to_oktas(cloud_pct)
    if cloud_oktas > 0:
        cloud_base_ft = _cloud_base_ft(wmo_code)
        cloud_top_ft = cloud_base_ft + 1500
        data[3] = str(cloud_oktas)
        data[4] = str(cloud_top_ft)
        data[5] = str(cloud_base_ft)

    cb_oktas, cb_tops, cb_base = _om_cb_fields(om, radar_echo=radar_echo, lightning=lightning)
    if cb_oktas > 0:
        data[9] = str(cb_oktas)
        data[10] = str(cb_tops)
        data[11] = str(cb_base)

    data[18] = f"000{wind_dir:03d}{wind_spd:02d}"
    data[19] = str(max(wind_gust, wind_spd))
    data[20] = str(max(vis_m, 100))
    data[22] = str(temp_c)
    data[23] = str(_hpa_to_psx_qnh(qnh_hpa))

    return ";".join(data)


def build_wxmode_string(lat_deg: float, lon_deg: float,
                        elevation_m: float, month: int, icao: str) -> str:
    """Build PSX WxMode string: lat_rad;lon_rad;320;elev_ft;MM;ICAOrwy.

    The elevation field is in feet: PSX's own zones (WxAutoSet=1) give
    SKBO 8360, SVBM 2044 (live, 2026-10-24). It used to be written in
    metres (and 0 for METAR zones).
    """
    lat_rad = math.radians(lat_deg)
    lon_rad = math.radians(lon_deg)
    elev_ft = int(round(elevation_m / 0.3048))
    return f"{lat_rad:.8f};{lon_rad:.8f};320;{elev_ft};{month:02d};{icao}00n"


# PSX echoes every WxMode write back slightly rewritten (lat/lon rounded in
# the last digit, name suffix "00n" -> "01p"), so an exact-string compare
# against PSX's value never matches and every zone position was being
# re-sent on every update (2026-10-21 DNMM-VVDN: ~1700 redundant WxMode
# writes vs 321 real moves). PSX keeps ~8 significant digits, so a
# longitude near pi comes back up to ~1.6e-7 rad off (2026-10-26 night
# campaign: own placements logged as "changed by another client").
# Positions closer than this (radians, ~6 m) count as unchanged.
_WXMODE_POS_EPS_RAD = 1e-6


def _wxmode_equivalent(a: Optional[str], b: Optional[str]) -> bool:
    """Return True if WxMode strings a and b describe the same zone placement.

    Ignores PSX's own rewriting of what we sent: last-digit lat/lon rounding
    and the 3-character suffix after the ICAO in the name field.
    """
    if a is None or b is None:
        return False
    pa, pb = a.split(';'), b.split(';')
    if len(pa) != 6 or len(pb) != 6:
        return a == b
    try:
        if (abs(float(pa[0]) - float(pb[0])) > _WXMODE_POS_EPS_RAD or
                abs(float(pa[1]) - float(pb[1])) > _WXMODE_POS_EPS_RAD):
            return False
    except ValueError:
        return a == b
    return pa[2:5] == pb[2:5] and pa[5][:-3] == pb[5][:-3]


def _wx_equivalent(a: Optional[str], b: Optional[str]) -> bool:
    """Return True if Wx strings a and b are the same zone weather.

    Ignores PSX's own rewriting of what we sent: it drops the zero padding
    of the wind speed in field 18 ("00024008" comes back as "0002408").
    """
    if a is None or b is None:
        return False
    pa, pb = a.split(';'), b.split(';')
    if len(pa) != len(pb) or len(pa) <= 18:
        return a == b
    wa, wb = pa[18], pb[18]
    if wa != wb:
        if not (wa.isdigit() and wb.isdigit() and len(wa) > 6 and len(wb) > 6 and
                wa[:6] == wb[:6] and int(wa[6:]) == int(wb[6:])):
            return False
    return pa[:18] == pb[:18] and pa[19:] == pb[19:]


def _strip_cb(wx: str) -> str:
    """Return a Wx<N> zone string with its CB coverage (field 9) set to 0."""
    parts = wx.split(';')
    if len(parts) > 9:
        parts[9] = '0'
    return ';'.join(parts)


# What makes PSX build a CB from a METAR (live-tested 2026-10-24): TS in
# present or recent weather (TS, +TSRA, VCTS, RETSRA...) and CB cloud groups
# (FEW030CB, ///CB...). Showers, hail, squalls, funnel clouds, TCU, trend
# groups and remarks don't. See _strip_convective_metar().
_METAR_DESCRIPTOR_CODES = frozenset(('MI', 'PR', 'BC', 'DR', 'BL', 'SH', 'FZ'))
_METAR_PRESENT_WX_RE = re.compile(
    r'^(RE)?([+-]|VC)?((?:MI|PR|BC|DR|BL|SH|TS|FZ|DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|'
    r'VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)+)$')
_METAR_CB_SKY_RE = re.compile(r'^((?:FEW|SCT|BKN|OVC)(?:\d{3}|///)|///|//////)CB$')
_METAR_MAX_LEN = 200          # PSX's Metar1-7 limit (Variables.txt Max=200)
# Remarks added to the METARs frankenweather gives PSX that aren't real ones
METAR_NOTE_MODEL = "FRANKENWX MODEL WX"        # no METAR: weather from Open-Meteo
METAR_NOTE_CB_REMOVED = "FRANKENWX CB REMOVED"  # real METAR, TS/CB taken out
METAR_NOTE_QNH_SYNC = "FRANKENWX QNH SYNC"      # real METAR, QNH from MSFS


def _strip_ts_cb_token(token: str) -> Optional[str]:
    """Return a METAR token without TS/CB, None to drop it (see _strip_convective_metar())."""
    m = _METAR_CB_SKY_RE.match(token)
    if m:
        return None if m.group(1)[0] == '/' else m.group(1)
    m = _METAR_PRESENT_WX_RE.match(token)
    if not m:
        return token
    codes = [m.group(3)[k:k + 2] for k in range(0, len(m.group(3)), 2)]
    if 'TS' not in codes:
        return token
    kept = [c for c in codes if c != 'TS']
    if not any(c not in _METAR_DESCRIPTOR_CODES for c in kept):
        return None
    return (m.group(1) or '') + (m.group(2) or '') + ''.join(kept)


def _strip_convective_metar(raw: str) -> str:
    """Return a METAR without anything PSX builds a CB from, noted in its remarks.

    For the METAR given to PSX for a zone whose CB frankenweather removes
    (Remove CB near dep/arr): PSX rebuilds a zone's weather from its METAR
    whenever the METAR changes, and makes a CB from TS and CB groups (see
    _METAR_CB_SKY_RE above). Only the observation itself is edited, up to
    the first trend group or the remarks: TS goes from present and recent
    weather (keeping the precipitation: +TSRA becomes +RA, VCTS and RETS
    go) and CB from cloud groups (FEW020CB becomes FEW020, ///CB goes).
    Returns raw itself when there is nothing to remove.
    """
    tokens = raw.split()
    out = []
    for idx, token in enumerate(tokens):
        if (token == 'RMK' or token in _METAR_TREND_MARKERS or
                _METAR_PROB_RE.match(token)):
            out.extend(tokens[idx:])
            break
        if idx == 0 or (idx == 1 and tokens[0] in ('METAR', 'SPECI')):
            out.append(token)  # station
            continue
        stripped = _strip_ts_cb_token(token)
        if stripped is not None:
            out.append(stripped)
    if out == tokens:
        return raw
    return _metar_with_note(' '.join(out), METAR_NOTE_CB_REMOVED)


def _metar_with_note(metar: str, note: str) -> str:
    """Return metar with a frankenweather remark added (once), within PSX's length limit.

    The note goes at the end of the remarks, or in a new RMK section. If
    that would be too long for PSX, the real remarks make way for it.
    """
    if note in metar.split(' RMK ', 1)[-1]:
        return metar
    noted = f"{metar} {note}" if ' RMK ' in f" {metar} " else f"{metar} RMK {note}"
    if len(noted) > _METAR_MAX_LEN:
        noted = f"{metar.split(' RMK ', 1)[0]} RMK {note}"
    return noted


def _parse_wxaloft_tropopause(wxaloft: Optional[str]) -> Optional[tuple]:
    """Return (alt_ft, temp_c, wind_u_kt, wind_v_kt) from a "Constantly" WxAloft, else None.

    None if any of the three tropopause fields is in PSX's own "auto" mode
    (lowercase letter) or the string doesn't parse. See _write_tropopause()
    for the format.
    """
    m = re.match(r"A(\d+)T(\d+)W\d{3}(\d{3})(\d+);", wxaloft or "")
    if not m:
        return None
    dir_rad = math.radians(int(m.group(3)))
    speed = int(m.group(4))
    return (float(m.group(1)), -float(m.group(2)),
            speed * math.sin(dir_rad), speed * math.cos(dir_rad))


def _tropo_change_significant(current: tuple, target: tuple) -> bool:
    """Return True if target differs from current by any of the _TROPO_MIN_CHANGE_* amounts."""
    return (abs(target[0] - current[0]) >= _TROPO_MIN_CHANGE_FT or
            abs(target[1] - current[1]) >= _TROPO_MIN_CHANGE_C or
            math.hypot(target[2] - current[2], target[3] - current[3]) >= _TROPO_MIN_CHANGE_WIND_KT)


def _parse_cb_arg(s: str) -> tuple:
    """Parse --fake-cb=O:B:T into (oktas, base_ft, top_ft)."""
    parts = s.split(':')
    if len(parts) != 3:
        raise argparse.ArgumentTypeError("expected O:B:T (e.g. 6:3000:45000)")
    try:
        oktas, base_ft, top_ft = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("all three values must be integers") from exc
    if not 0 <= oktas <= 8:
        raise argparse.ArgumentTypeError("coverage (O) must be 0–8 oktas")
    if base_ft >= top_ft:
        raise argparse.ArgumentTypeError("base (B) must be below top (T)")
    return (oktas, base_ft, top_ft)


def _parse_cape_squeeze(s: str) -> tuple:
    """Parse --cape-squeeze=CAPE:MIN_FWD into (cape_threshold_jkg, min_fwd_nm)."""
    parts = s.split(':')
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected CAPE:MIN_FWD (e.g. 2000:50)")
    try:
        cape, min_fwd = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("values must be numbers") from exc
    if cape <= 0:
        raise argparse.ArgumentTypeError("CAPE threshold must be positive")
    if min_fwd < 0:
        raise argparse.ArgumentTypeError("MIN_FWD must be non-negative")
    return (cape, min_fwd)


def _parse_nm_range(s: str) -> tuple:
    """Parse a MIN,MAX nm argument into a (min_nm, max_nm) float tuple."""
    parts = s.split(',')
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("expected MIN,MAX (e.g. 150,250)")
    try:
        lo, hi = float(parts[0]), float(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("values must be numbers") from exc
    if lo < 0 or hi < 0:
        raise argparse.ArgumentTypeError("values must be non-negative")
    if lo > hi:
        raise argparse.ArgumentTypeError("MIN must be no greater than MAX")
    return (lo, hi)


def _isnan(v):
    """Return True if v is float NaN."""
    try:
        return v != v  # pylint: disable=comparison-with-itself
    except TypeError:
        return False


def _parse_psx_wind(wx_str):
    """Parse surface wind direction and speed from a PSX Wx* weather string.

    The 19th field encodes VVVDDSS (VVV=variability, DD=direction/10, SS=speed kt).
    Returns (direction_deg, speed_kt) or None.
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


def _intensity_label(intensity, testing=False):
    """Map 0–1 intensity to a human-readable severity label.

    testing: the testing profile's calibration, where the continuous
    turbulence up to 0.34 is light chop (REALISTIC_SEV_BANDS) and
    moderate patches (0.38-0.50, _turb_events.MODERATE_LEVEL) are
    moderate, 0.50 included; a CB core (_TURB_CB_CORE) severe.
    """
    if intensity < 0.10:
        return "none"
    if intensity < (0.35 if testing else 0.25):
        return "light"
    if intensity < (0.55 if testing else 0.50):
        return "moderate"
    if intensity < 0.75:
        return "severe"
    return "extreme"


# WxBurst channel offsets
_BURST_SINK = 0
_BURST_BANK = 100
_BURST_YAW = 200
_BURST_SPD = 300
_BURST_GUST = 400
_BURST_NAMES = {_BURST_SINK: "SINK", _BURST_BANK: "BANK", _BURST_YAW: "YAW",
                _BURST_SPD: "SPD", _BURST_GUST: "GUST"}
_BURST_SUMMARY_S = 60.0               # the burst summary log line's period (_log_burst_summary())


def _burst_channels_text(bursts: list) -> str:
    """Return e.g. "SINK 3 +2/-1, SPD 8 +3/-5" for (base, direction, size) bursts."""
    channels = []
    for base, name in _BURST_NAMES.items():
        dirs = [d for b, d, _ in bursts if b == base]
        if dirs:
            channels.append(f"{name} {len(dirs)} +{dirs.count(1)}/-{dirs.count(-1)}")
    return ", ".join(channels)


# Turbulence profile (TURBCOMMAND "profile", /efb): "stable" is the
# released behaviour; "testing" also turns on changes being evaluated
# before they become the default (see _turb_testing()).
_TURB_PROFILES = ("stable", "testing")

_TURB_TYPES = ('wave', 'rotor', 'mechanical', 'shear', 'cb', 'pirep', 'cape', 'gairmet',
               'sigmet')

# ---------------------------------------------------------------------------
# --config-file (TOML): every setting the web GUI can change, in one place.
# Each tuple is (toml_key, attribute_name, cast); manual weather params map
# 1:1 by key into self._manual_wx_params instead, since that's already a dict.
# ---------------------------------------------------------------------------
_CONFIG_MSFS_FIELDS = (
    ("in_cloud_sync", "_msfs_in_cloud_sync", bool),
    ("qnh_check", "_msfs_qnh_check", str),
)
_CONFIG_ENROUTE_WIND_FIELDS = (
    ("enabled", "_enroute_wind_enabled", bool),
    ("deviation", "_enroute_wind_deviation", int),
)
_CONFIG_TIMESYNC_FIELDS = (
    ("on_startup", "_timesync_on_startup", bool),
    ("on_situ_load", "_timesync_on_situ_load", bool),
    ("periodic", "_timesync_periodic", bool),
)
_CONFIG_SIGMET_FIELDS = (
    ("use_sigmets", "_use_sigmets", bool),
    ("own", "_sigmet_own", bool),
    ("disable_psx_sigmets", "_disable_psx_sigmets", bool),
)
_CONFIG_CB_AVOIDANCE_FIELDS = (
    ("mode", "_cb_airport_mode", str),
)
_CONFIG_MANUAL_WX_FIELDS = (
    "hi_oktas", "hi_top", "hi_base", "lo_oktas", "lo_top", "lo_base",
    "cb_oktas", "cb_top", "cb_base", "turb_severity", "turb_top", "turb_base",
    "mb_mode", "mb_chance", "mb_outflow", "inv_on", "inv_top", "inv_tmp",
    "wind_dir", "wind_spd", "wind_gust", "wind_var", "precip", "vis_m",
    "surf_temp", "qnh_hpa",
)
_CONFIG_TURB_FIELDS = (
    ("enabled", "_turb_enabled", bool),
    ("manual_turb_enabled", "_turb_manual_turb_enabled", bool),
    ("manual_turb_kind", "_turb_manual_turb_kind", str),
    ("manual_turb_intensity", "_turb_manual_turb_intensity", float),
    ("intensity_bias", "_turb_intensity_bias", int),
    ("lateral_size_bias", "_turb_lateral_size_bias", int),
    ("wind_mode", "_turb_wind_mode", str),
    ("manual_wind_dir", "_turb_manual_wind_dir", int),
    ("manual_wind_spd", "_turb_manual_wind_spd", int),
    ("msfs_turb_magnitude", "_turb_msfs_magnitude", int),
    ("profile", "_turb_profile", str),
)


def _toml_format_value(value) -> str:
    """Format a scalar as a TOML value literal (bool/int/float/str only)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'


def _dict_to_toml(config: dict) -> str:
    """Serialize a {section: {key: scalar|dict}} dict to TOML text.

    Purpose-built for frankenweather's own config schema (flat sections with
    at most one level of sub-tables, scalar values only) — not a general TOML
    writer. There's no TOML-writing library in this project's dependencies
    (only stdlib tomllib, which is read-only), and the schema is simple
    enough that a small hand-written serializer is clearer than adding one.
    """
    lines = []

    def emit_table(path: list, table: dict) -> None:
        scalars = {k: v for k, v in table.items() if not isinstance(v, dict)}
        subtables = {k: v for k, v in table.items() if isinstance(v, dict)}
        lines.append(f"[{'.'.join(path)}]")
        for key, value in scalars.items():
            lines.append(f"{key} = {_toml_format_value(value)}")
        lines.append("")
        for key, value in subtables.items():
            emit_table(path + [key], value)

    for section, table in config.items():
        emit_table([section], table)
    return "\n".join(lines).rstrip("\n") + "\n"


def _sign(v):
    """Return +1 or -1 from a float."""
    return 1 if v >= 0.0 else -1


# On the ground (takeoff/landing roll, from _TURB_MIN_TAS_KT) gusts still
# yaw and roll the aircraft but can't move it up or down. Bursts there are
# capped at _GROUND_TURB_CAP and limited to YAW/BANK. Live-tested on a
# heavy 747 (2026-10-24): PSX itself scales a yaw burst with airspeed
# squared (heading change about 1.1e-6 x strength x kt^2 degrees, i.e.
# ~0.2 deg at 80 kt and ~0.6 deg at 140 kt for strength 29, held until
# steered out); bank is proportional to strength (back to level in ~0.8 s);
# SPD bursts had no visible effect on the ground.
_TURB_MIN_TAS_KT = 30.0
_GROUND_TURB_CAP = 0.30
# Testing profile: the continuous turbulence is at most light chop (p95
# about 0.09 g at FL350); moderate and stronger come as events
# (frankenturb/turbulence_events.py, _turb_event_step()), at real-world
# odds times the crew's multipliers (0 turns a kind off, at most
# _TURB_EVENT_MULT_MAX).
_TURB_BG_MAX = 0.34
# ... except inside a CB's cloud (area "cb_core"): severe, with the
# core's severe events on top and, like events, without the up/down burst
# limiter. Capped at light chop, a typhoon's CB
# core at FL340 was calmer than its edge during a moderate patch
# (frankensim 2026-10-29: 34 % inside, 40-46 % outside); at 38-50 % the
# autopilot still had no trouble at all (pilot: "could be even stronger",
# then "more than" 45-60 %). The stable profile gives a CB interior 38-66 %;
# 60 %+ upset the aircraft at cruise on the autopilot in the calibration
# (docs/turbulence-calibration.md): in a CB core a pilot should hand-fly.
_TURB_CB_CORE = (0.55, 0.70)
_TURB_EVENT_MULT_MAX = {"moderate": 10.0, "severe": 100.0, "extreme": 1000.0}
# Every this many seconds a line about the flight, the weather and the
# turbulence goes to the log, so a pilot's report (too rough, too
# smooth, just right) can be checked without --debug (_log_flight_status())
_FLIGHT_LOG_S = 60.0


def _pick_ground_burst():
    """Choose (base_offset, direction, label) for one WxBurst on the ground."""
    r = random.choice
    return r([
        (_BURST_YAW, r([-1, 1]), 'yaw'),
        (_BURST_BANK, r([-1, 1]), 'bank'),
    ])


def _turb_type(kind: str) -> str:
    """Return the turbulence type (_TURB_TYPES) a kind's settings come from.

    "X+shear" is wind shear outweighing X (frankenturb/turbulence.py), so
    the wind shear settings apply; they used to be ignored for it. "mtw"
    (a mountain wave SIGMET, _sigmet_turbulence()) is a SIGMET.
    """
    if kind.endswith("+shear"):
        return "shear"
    return "sigmet" if kind == "mtw" else kind


def _pick_burst(state, intensity):
    """Choose (base_offset, direction, label) for one WxBurst event."""
    r = random.choice
    if state.kind in ('wave', 'mtw'):
        vert_dir = _sign(state.vertical) if not _isnan(state.vertical) else r([-1, 1])
        roll_dir = _sign(state.roll) if not _isnan(state.roll) else r([-1, 1])
        spd_dir = _sign(state.gust) if not _isnan(state.gust) else r([-1, 1])
        if intensity < 0.25:
            return r([(_BURST_SPD, spd_dir, 'spd'), (_BURST_GUST, spd_dir, 'gust')])
        if intensity < 0.50:
            return r([
                (_BURST_SINK, vert_dir, 'sink'),
                (_BURST_SPD, spd_dir, 'spd'),
                (_BURST_GUST, spd_dir, 'gust'),
            ])
        return r([
            (_BURST_SINK, vert_dir, 'sink'),
            (_BURST_BANK, roll_dir, 'bank'),
            (_BURST_SPD, spd_dir, 'spd'),
        ])
    if state.kind == 'rotor':
        return r([
            (_BURST_SINK, r([-1, 1]), 'sink'),
            (_BURST_BANK, r([-1, 1]), 'bank'),
            (_BURST_BANK, r([-1, 1]), 'bank'),
            (_BURST_YAW, r([-1, 1]), 'yaw'),
        ])
    if _turb_type(state.kind) == 'shear':
        return r([
            (_BURST_SINK, r([-1, 1]), 'sink'),
            (_BURST_BANK, r([-1, 1]), 'bank'),
        ])
    # mechanical / cb / pirep / cape / gairmet — broad random mix
    return r([
        (_BURST_SINK, r([-1, 1]), 'sink'),
        (_BURST_BANK, r([-1, 1]), 'bank'),
        (_BURST_YAW, r([-1, 1]), 'yaw'),
        (_BURST_SPD, r([-1, 1]), 'spd'),
    ])


# ---------------------------------------------------------------------------
# Standalone web UI context (adapts Script to fw_webui protocol)
# ---------------------------------------------------------------------------

class StandaloneFWContext:
    """Adapts a Script instance to the fw_webui context protocol."""

    def __init__(self, fw, color_scheme='dark'):
        """Store frankenweather instance and color scheme."""
        self._fw = fw
        self.color_scheme = color_scheme

    @property
    def fw_state(self):
        """Return the last-broadcast FrankenWeather STATE dict."""
        return self._fw._web_state  # pylint: disable=protected-access

    @property
    def fw_turbstate(self):
        """Return the last-broadcast TURBSTATE dict."""
        return self._fw._web_turbstate  # pylint: disable=protected-access

    @property
    def fw_state_received_at(self):
        """Return epoch of last STATE broadcast."""
        return self._fw._web_state_received_at  # pylint: disable=protected-access

    @property
    def fw_turbstate_received_at(self):
        """Return epoch of last TURBSTATE broadcast."""
        return self._fw._web_turbstate_received_at  # pylint: disable=protected-access

    @property
    def fw_windstate(self):
        """Return the last-broadcast WINDSTATE dict."""
        return self._fw._web_windstate  # pylint: disable=protected-access

    @property
    def fw_windstate_received_at(self):
        """Return epoch of last WINDSTATE broadcast."""
        return self._fw._web_windstate_received_at  # pylint: disable=protected-access

    @property
    def fw_sigmetstate(self):
        """Return the last-broadcast SIGMETSTATE dict."""
        return self._fw._web_sigmetstate  # pylint: disable=protected-access

    @property
    def fw_sigmetstate_received_at(self):
        """Return epoch of last SIGMETSTATE broadcast."""
        return self._fw._web_sigmetstate_received_at  # pylint: disable=protected-access

    @property
    def fw_conflict_paused(self):
        """Return True while this instance is paused for a FRANKENWEATHER conflict."""
        return self._fw._conflict_uuid is not None  # pylint: disable=protected-access

    @property
    def event_log_dir(self):
        """Return the per-run event log directory (see the Config and logs page).

        Only present on the standalone context -- the router UI has no
        need for this page (see fw_webui.py's nav gating), so RouterFWContext
        deliberately has no such attribute.
        """
        return _EVENT_LOG_DIR

    @property
    def event_log_path(self):
        """Return this run's own event log file path, or None if unavailable."""
        return self._fw._event_log_path  # pylint: disable=protected-access

    def cache_get(self, name):
        """Return a PSX-variable-like value from local frankenweather state."""
        return self._fw._web_cache_get(name)  # pylint: disable=protected-access

    async def send_manualwx_cmd(self, cmd):
        """Apply a manual wx command directly to the running frankenweather instance."""
        self._fw._handle_manual_wx_command(json.dumps(cmd))  # pylint: disable=protected-access

    async def send_turb_cmd(self, cmd):
        """Apply a turbulence command directly to the running frankenweather instance."""
        self._fw._handle_turb_command(json.dumps(cmd))  # pylint: disable=protected-access

    async def send_mode_cmd(self, mode):
        """Apply a mode change directly to the running frankenweather instance."""
        self._fw._handle_fw_command(json.dumps({"mode": mode}))  # pylint: disable=protected-access
        await asyncio.sleep(3)

    async def send_fw_settings_cmd(self, cmd):
        """Apply MSFS settings toggles directly to the running frankenweather instance."""
        self._fw._handle_fw_command(json.dumps(cmd))  # pylint: disable=protected-access


# ---------------------------------------------------------------------------
# Main script class
# ---------------------------------------------------------------------------

class Script:  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """FrankenWeather script."""

    def __init__(self):  # pylint: disable=too-many-statements
        """Initialise script state."""
        self.args = None
        self.taskgroup = None
        self.tasks: set = set()
        self.logger = None
        self._event_log_path: Optional[str] = None
        self.psx = None
        self.psx_connected = False
        self.psx_paused = False

        self.airports: dict = {}
        self.geod = Geod(ellps="WGS84")

        # Aircraft state
        self.ac_lat: Optional[float] = None
        self.ac_lon: Optional[float] = None
        self.ac_hdg: Optional[float] = None
        self.ac_alt_ft: Optional[float] = None
        self.ac_tas_kt: Optional[float] = None

        # VATSIM METAR cache: ICAO → raw METAR string
        self.vatsim_cache: dict = {}
        self.vatsim_cache_time = 0.0
        # NOAA METAR cache (secondary source, METAR-fallback path only): ICAO → raw METAR
        self.noaa_metar_cache: dict = {}
        self.noaa_metar_cache_time = 0.0

        # Last MetarsUp (Qs464) value we wrote ourselves, so we can recognize
        # our own write reflected back through psx_send_and_set()'s local
        # cache update (or an eventual network echo) instead of re-processing
        # it as a new D-ATIS request. See _handle_metars_up().
        self._datis_last_written: Optional[str] = None
        # Same self-echo guard, for the ALTN-weather print supplement --
        # see _handle_printer_text().
        self._altn_supplement_last_written: Optional[str] = None

        # METAR TEMPO/PROBnn trend-group state, keyed by ICAO (not zone
        # number -- a zone's bound station can change as it relocates, but
        # the PROB roll / TEMPO on-off state belongs to the station's
        # observation, not to whichever zone happens to be pointed at it
        # right now). See _trend_cb_contribution().
        self._metar_trend_state: dict = {}

        # On-disk Open-Meteo caches — see _OM_ZONE_CACHE_PATH for why the
        # max age matches each fetch's own existing refresh cadence.
        self._om_zone_cache = DiskCache(_OM_ZONE_CACHE_PATH, max_age_s=_OM_ZONE_KEEP_S)
        self._om_enroute_cache = DiskCache(_OM_ENROUTE_CACHE_PATH, max_age_s=_OM_ENROUTE_KEEP_S)

        # Our current desired zone values (zone_num → string)
        self.zone_wx: dict = {}
        self.zone_mode: dict = {}
        # zone_num -> (lat, lon, elev_m) of the elevation first sent in that
        # zone's WxMode at that position; see _stable_zone_elev().
        self._zone_elev: dict[int, tuple] = {}
        # zone_num -> (zone_positions entry, (lat, lon, icao)): the broadcast
        # position/airport _update_zones() picked the first time it snapped
        # that zone at that placement. Reused until the zone is relocated, so
        # a zone never hops between nearby airports as METARs go stale/fresh
        # or other zones' choices change (2026-10-21 DNMM-VVDN: OYTZ/OYIB
        # ping-pong, 18nm apart).
        self._zone_snap: dict[int, tuple] = {}
        self.zone_is_metar: dict = {}
        # zone_num -> "VATSIM" or "NOAA", only meaningful when zone_is_metar
        # is True. See --primary-metar-source / _metar_source_label().
        self.zone_metar_source: dict = {}
        # zone -> station-only METAR last given to PSX, see _station_only_metar()
        self._zone_station_metar: dict = {}
        self.last_write_time = 0.0
        # _OWNED_KEYS: key -> the value we last gave PSX (see handle_owned_change())
        self._owned_values: dict[str, str] = {}
        self._owned_restore_due = 0.0
        self._owned_restore_task: Optional[asyncio.Task] = None
        self._owned_restore_times: dict[str, collections.deque] = {}
        self._owned_restore_blocked: set = set()
        self._last_situ_load_mono = 0.0
        self._owned_restore_situ = False  # the pending restore follows a situ load

        # --cb-growth ramp state (see _ramp_cb_wx()): zone_num → dict of
        # current_oktas/current_top_ft/growth_rate_*_per_min/last_update_ts,
        # present only while that zone's CB is actively growing/decaying.
        self.zone_cb_ramp: dict = {}
        # zone_num → (zone position, max oktas, max top ft) for a zone within
        # _CB_SQUEEZE_MIN_FWD_NM, see _limit_close_cb(); and the zone
        # position each zone_wx was sent for
        self.zone_cb_close_cap: dict = {}
        self._cb_close_grace_until = time.monotonic() + _CB_CLOSE_START_GRACE_S
        self._zones_placed_before_sigmets = False   # see the zone update loop
        self._zone_wx_pos: dict = {}
        # zone_num → distance (nm) to the aircraft captured at the moment the
        # zone was last (re)placed; consumed (popped) by _ramp_cb_wx() the
        # first time it sees a CB there, so it only ever reflects placement,
        # never a later update.
        self.zone_cb_create_dist_nm: dict = {}
        # zone_num → {'oktas','base','top'} forecast target (pre-ramp), or
        # None if no CB is forecast there -- set every _update_zones() cycle
        # regardless of --cb-growth, so the web UI/console can always show
        # the target even when ramping itself is inactive (in which case
        # current == target, since nothing is ramped).
        self.zone_cb_target: dict = {}

        # Fixed zone positions: zone_num (1-7) → (lat, lon, icao)
        self.zone_positions: dict = {}
        self.zone_relocated_time: dict = {}  # zone_num → last relocation timestamp
        # zone_num → last time we logged "due but protected" for it -- a zone
        # can sit protected for many minutes at a time (see _check_and_relocate()),
        # so this is throttled rather than logged on every single check.
        self._zone_protected_last_logged: dict = {}

        # FMC route state — dep/dst airports get a dedicated zone when within range
        self.fmc_dep_icao: Optional[str] = None
        self.fmc_dst_icao: Optional[str] = None
        self.fmc_changed_event: asyncio.Event = asyncio.Event()

        # Enroute wind importer (Open-Meteo-driven periodic WxCorridor refresh)
        # route_waypoints mirrors PSX's live (front-trimmed) FmcRte list;
        # _enroute_waypoints is our own persistent, index-stable superset for
        # the whole flight (see _update_fmc_route_waypoints).
        self.route_waypoints: list = []           # [(name, lat_deg, lon_deg), ...] in route order
        self._enroute_waypoints: list = []        # persistent per-flight superset, same shape
        self._enroute_wind_enabled: bool = True    # on by default; can be disabled from the web UI
        self._enroute_wind_deviation: int = 30     # Qs497 Random slider position x 10, 0-80
        # Exact text of the last corridor we ourselves sent (the enroute
        # wind importer) — compared by value, not time, in
        # _handle_corridor to tell our own echo apart from an external load.
        self._corridor_last_own_value: Optional[str] = None
        self._corridor_snapshot_txt: Optional[str] = None
        self._corridor_snapshot_waypoints: dict = {}   # name → {fl_ft: (dir,spd,oat)}
        self._corridor_snapshot_coords: dict = {}      # name → (lat_deg, lon_deg), D/E only
        self._waypoint_passed: set = set()             # indices into _enroute_waypoints
        self._waypoint_om_wind: dict = {}       # _enroute_waypoints index → {fl_ft: (dir,spd,oat)}
        self._enroute_last_fetch_time: float = 0.0     # epoch
        self._enroute_next_fetch_time: float = 0.0     # epoch
        self._enroute_wind_changed_event: asyncio.Event = asyncio.Event()
        self._enroute_log_path: Optional[str] = None   # fixed per-flight --save-logs base path
        self._enroute_last_corridor_txt: Optional[str] = None  # last corridor we generated
        self._web_windstate: Optional[dict] = None
        self._web_windstate_received_at: float = 0.0
        # --sigmet: frankenweather's own SIGMET download (see
        # own_sigmet_coro()) -- normalized entries (fw_sigmet.normalize()),
        # when they were fetched, and the last download error, if any.
        # --sigmet: frankenweather's SIGMET system (see own_sigmet_coro()),
        # whether it's switched on (off with the "PSX" and "Off" SIGMET
        # presets: then everything is as without --sigmet, see
        # _sigmet_active()), and which SIGMET types it handles itself (the
        # rest go to PSX), chosen on the SIGMETs page and saved in the
        # config file.
        self._sigmet_enabled: bool = False   # see handle_args()
        self._sigmet_own: bool = True
        self._sigmet_handle: dict = {h: True for h in _fw_sigmet.OWN_HAZARDS}
        # The varying intensity of TURB/MTW SIGMET turbulence, one per type,
        # each with its own timeline per altitude layer (see
        # _sigmet_turbulence()), and the altitude it was last asked for.
        self._sigmet_turb_profiles: dict = {
            h: LayeredIntensityProfile(layer_ft=_SIGMET_TURB_LAYERS[h][0],
                                       blend_ft=_SIGMET_TURB_LAYERS[h][1])
            for h in _fw_sigmet.TURB_HAZARDS}
        self._sigmet_turb_alt_ft: float = 0.0
        # Testing profile: the varying level of TS/TC area turbulence
        self._sigmet_conv_profile = LayeredIntensityProfile(
            layer_ft=_SIGMET_TURB_LAYERS["TURB"][0], blend_ft=_SIGMET_TURB_LAYERS["TURB"][1])
        # The TURB/MTW SIGMET areas at the aircraft's position, for PIREPs:
        # [(hazard, lateral factor, entry)], see _sigmet_turbulence()
        self._sigmet_turb_areas: list = []
        # Per type the factor (edge fade x qualifier) its SIGMET level was
        # scaled by, see _sigmet_turbulence()
        self._sigmet_turb_scale: dict = {}
        # Gustiness multipliers for physically computed turbulence, per
        # family (_TURB_GUST, _gust_family())
        self._gust_profiles: dict = {
            fam: GustinessProfile(low, high, layer_ft=layer_ft, blend_ft=blend_ft)
            for fam, (low, high, layer_ft, blend_ft) in _TURB_GUST.items()}
        # The varying level of G-AIRMET turbulence (_gairmet_vary())
        self._gairmet_turb_profile = LayeredIntensityProfile(
            layer_ft=_GAIRMET_LAYERS[0], blend_ft=_GAIRMET_LAYERS[1])
        # The varying level of PIREP turbulence (_pirep_vary()), same layers
        self._pirep_turb_profile = LayeredIntensityProfile(
            layer_ft=_GAIRMET_LAYERS[0], blend_ft=_GAIRMET_LAYERS[1])
        # What drives the injected turbulence's intensity over time, for the
        # ramp shown on the console and the router's /efb page (TURBSTATE
        # "intensity_ramp"): (name, layered profile, scale to intensity) and
        # the altitude, or None. See _turb_ramp_status().
        self._turb_driver: Optional[tuple] = None
        self._turb_driver_alt_ft: float = 0.0
        # The PIREP ride reports near the aircraft (pirep_levels_coro()),
        # and the event that asks for a fresh set (flight level change)
        self._pirep_levels: Optional[list] = None
        self._pirep_refresh_event = asyncio.Event()
        # Whether we currently own PSX's SIGMETs (see _sync_own_sigmets()),
        # PSX's last reported WxSigmet text, and the last text we wrote.
        self._own_sigmets_applied: bool = False
        # The current anchor zone (see _maintain_anchor_zone()), and the
        # zone kept CB-free: zone -> (lat, lon) it was at when made CB-free.
        # It stops being CB-free when released or moved anywhere else.
        self._anchor_zone: Optional[int] = None
        self._cb_free_zones: dict = {}
        self._sigmet_place_last: float = 0.0  # see _place_sigmet_zones()
        # zone -> label of the SIGMET that gave its current CB (e.g. "TS
        # SIGMET MPZL 03 EMBD"), or None; shown in the log and the web UI.
        self.zone_cb_sigmet: dict = {}
        # Set when we take over the SIGMETs: _place_sigmet_zones() then
        # places every zone it wants at once instead of one per interval.
        self._sigmet_place_burst: bool = False
        self._psx_sigmet_raw: Optional[str] = None
        self._own_sigmet_written: Optional[str] = None
        self._own_sigmet_entries: list = []
        # Every SIGMET of the last successful download, expired ones too
        # (fw_sigmet.all_entries()), for /api/sigmet/latest/raw
        self._own_sigmet_downloaded: list = []
        self._own_sigmet_fetched_at: Optional[float] = None
        self._own_sigmet_error: Optional[str] = None
        self._sigmetstate_changed_event: asyncio.Event = asyncio.Event()
        # Wakes own_sigmet_coro() right away (a setting changed); with
        # _own_sigmet_download_now also set (the web UI's "Download now"
        # button, sigmet_refresh command) it downloads straight away too.
        self._own_sigmet_refresh_event: asyncio.Event = asyncio.Event()
        self._own_sigmet_download_now: bool = False
        self._own_sigmet_next_at: Optional[float] = None  # next automatic download (epoch)
        self._web_sigmetstate: Optional[dict] = None
        self._web_sigmetstate_received_at: float = 0.0

        # All parsed SIGMETs (any recognized hazard type), and ts_sigmets, the
        # TS-only subset used to lift WMO/showers CB suppression when CAPE agrees.
        self.sigmets: list = []
        self.ts_sigmets: list = []

        # MSFS bridge state (via PSX.NET.MSFS.Client's addon=FRANKENMSFSBRIDGE: messages)
        self.msfs_in_cloud: Optional[bool] = None
        self.msfs_qnh_hpa: Optional[float] = None
        self.msfs_cloud_density: Optional[float] = None   # 0.0-1.0 (SimConnect ENV CLOUD DENSITY)
        self.msfs_wind_vert: Optional[float] = None       # kt, positive = up
        self.msfs_precip_state: Optional[int] = None      # 2=none, 4=rain, 8=snow
        self._msfs_bridge_last_seen: Optional[float] = None  # monotonic, for the timeout check
        self._msfs_bridge_last_seen_epoch: Optional[float] = None  # time.time(), for web display
        # Rolling (monotonic_time, in_cloud) samples, one per bridge update, used to
        # compute a recent time-in-cloud duty cycle for cloud sync (see _msfs_cloud_oktas).
        self._msfs_in_cloud_history: list = []
        # Runtime toggles for MSFS sync features
        self._msfs_in_cloud_sync: bool = True
        self._msfs_qnh_check: str = "CHECK"   # "CHECK" or "SYNC"
        # zone_key -> (time.monotonic(), diff hPa) of the last "QNH mismatch" warning
        self._qnh_mismatch_warned: dict = {}
        # Runtime toggles for PSX clock sync (see _sync_psx_clock/time_sync_coro)
        self._timesync_on_startup: bool = True    # on every PSX connect/reconnect
        self._timesync_on_situ_load: bool = True  # on every PSX situ load (load1..load3)
        self._timesync_periodic: bool = False     # every 60s if drifted >1s from real time
        # Whether to periodically tell PSX to (re)download its own SIGMET
        # data (see sigmet_refresh_coro). Needed because frankenweather
        # disabling PSX's own METAR downloads also silently disables its
        # SIGMET downloads (they share a fetch cycle in PSX) -- with this
        # off, PSX's SIGMET data (and our own ts_sigmets, parsed from the
        # same WxSigmet/Qs499 PSX populates) goes stale for the whole
        # flight. See _write_sigmet_on() for the bitmask this drives.
        self._use_sigmets: bool = True
        # PSX gets no SIGMETs (the "Off" SIGMET preset, which also switches
        # our SIGMET system off: PSX's own "embedded" setting is turned
        # off). Set in the config file with our SIGMET system on, PSX's
        # SIGMET data is emptied instead ("embedded in the planet weather
        # model" stays on; see _sync_own_sigmets()).
        self._disable_psx_sigmets: bool = False
        # Last-seen SigmetOn (Qi262) value from PSX, tracked via subscription
        # purely so _write_sigmet_on() can read-modify-write without ever
        # touching the unknown bits (2, 8).
        self._sigmet_on_bits: int = 0
        # Last-seen AeroCkBxs (Qi184) value from PSX, tracked via
        # subscription -- see handle_aero_ckbxs(), which also enforces bit
        # 1 ("Altitude indicators with OAT error") staying set, without
        # ever touching bits 2/4 (aircraft-type configuration).
        self._aero_ckbxs_bits: int = 0
        # Last-seen raw WxAloft (Qs327) string from PSX, tracked via
        # subscription -- see --sync-tropopause/_write_tropopause(), which
        # needs it to read-modify-write: the trailing ";cv<lat> <lon>"
        # position reference is PSX's own and is never ours to construct,
        # only to preserve verbatim.
        self._wxaloft_raw: Optional[str] = None
        # --sync-tropopause smoothing (see _write_tropopause()): recent
        # (monotonic time, result) samples, result being
        # (alt_ft, temp_c, wind_u_kt, wind_v_kt) or None if none was found,
        # when we last wrote WxAloft in flight, when we started (for
        # _TROPO_STARTUP_HOLDOFF_S) and where the last sample was taken.
        self._tropo_samples: list = []
        self._tropo_last_write_mono: Optional[float] = None
        self._tropo_start_mono = time.monotonic()
        self._tropo_last_pos: Optional[tuple] = None
        # How to keep CBs at least _CB_AIRPORT_CLEARANCE_NM from the
        # departure/destination airport, e.g. so a VATSIM approach doesn't go
        # through a CB nobody else has (_CB_AIRPORT_MODES): "off" (default),
        # "move" the airport's zone (_arpt_coverage_needed(), keeping the
        # airport's real weather/METAR, see _update_zones()'s dep/dst bypass)
        # or "remove" the CBs near it (_cb_removed_near_airport()).
        self._cb_airport_mode: str = "off"
        self.focused_zone: int = 0          # 0 = WxBasic, 1-7 = Wx1-Wx7
        self.cloud_sync_last_alt_ft: float = 0.0

        # Maneuvering mode detection
        self._hdg_history: list = []   # [(monotonic_time, heading_deg)]
        self._maneuvering: bool = False

        # Radar (RainViewer) tile cache — keyed by (tx, ty), cleared on new frame
        self._rv_frame_path: Optional[str] = None
        self._rv_tile_cache: dict = {}

        # Blitzortung lightning strikes — list of (lat, lon) from last _BZ_LOOKBACK_MIN minutes
        self._bz_strikes: list = []
        self._bz_fetch_time: float = 0.0

        # API state broadcast
        self._instance_uuid: str = str(uuid.uuid4())
        self.zone_reason: dict = {}               # zone_num → short reason string
        self.zone_placement_reason: dict = {}     # zone_num → placement description
        self.zone_weather_detail: dict = {}       # zone_num → detailed weather source explanation
        self._state_changed_event: asyncio.Event = asyncio.Event()

        # Conflict detection — suspend PSX changes when a higher-UUID instance is present
        self._conflict_uuid: Optional[str] = None
        self._conflict_last_seen: float = 0.0
        self._conflict_last_logged: float = 0.0
        # Bookkeeping for the *other* side of a conflict: we're the one
        # staying active, but still want to log it — see _note_conflict().
        self._conflict_peer_uuid: Optional[str] = None
        self._conflict_peer_last_logged: float = 0.0

        # Operational mode: "enabled" | "paused" | "disabled" | "manual"
        # paused  = stop updating PSX weather; keep WxAutoSet=0 (existing zones remain)
        # disabled = stop updating; set WxAutoSet=1 (PSX resumes its own auto-weather)
        # manual  = all zones get the same manually-configured weather
        self._fw_mode: str = "enabled"
        # True while the METAR-fallback path (see _apply_metar_fallback()) has
        # taken over all 7 zones + WxBasic from cached VATSIM/NOAA METAR text,
        # because Open-Meteo has been down long enough that _zones_all_stale()
        # trips. Never hands control to PSX's own WxAutoSet -- PSX's own
        # fallback beyond real METAR coverage is essentially random (see
        # project_frankenweather_altitude_jump memory), so real METAR data,
        # however far away, is always at least as good.
        self._metar_fallback_active: bool = False
        # True whenever the most recent Open-Meteo fetch attempt failed, set
        # on every _update_zones() call regardless of whether that failure
        # was enough to trigger the full METAR-fallback handoff (i.e. this
        # can be True while zones are still within useful range and
        # frankenweather is quietly serving frozen weather). Drives the
        # periodic om_failure_banner_coro() warning so an ongoing outage is
        # never silent, even before/without a full handoff.
        self._last_om_fetch_failed: bool = False
        # Age of the oldest Open-Meteo zone data used by the last zone update
        # (cached data while Open-Meteo is unreachable, see _fetch_om_batch())
        self._om_zone_data_age_s: float = 0.0
        # True if the last zone update had no Open-Meteo data at all and
        # used METARs only
        self._zones_metar_only: bool = False
        # Diagnostic-only: forces _fetch_om_batch() to behave as if every
        # request failed, without touching the network, so the METAR-fallback
        # handoff can be triggered on demand for testing
        # (addon=FRANKENWEATHER:DIAG:simulate_openmeteo_download_failure:1|0).
        # See _handle_fw_addon().
        self._diag_simulate_om_failure: bool = False
        # Diagnostic-only: forces _zones_all_stale() to return True, so the
        # METAR-fallback handoff itself can be triggered without needing the
        # aircraft to actually fly far enough for real zone staleness
        # (addon=FRANKENWEATHER:DIAG:force_zones_stale:1|0). See _handle_fw_addon().
        self._diag_force_zones_stale: bool = False
        # Diagnostic-only: makes _nearest_metar_stations() (the METAR-fallback
        # path only -- not the D-ATIS override or normal per-zone METAR
        # matching, both of which keep using the real cache) act as if
        # self.vatsim_cache were empty, without touching the real cached data
        # or its refresh cycle, so the NOAA-secondary-source and
        # no-data-at-all branches of _apply_metar_fallback() can be exercised
        # on demand (addon=FRANKENWEATHER:DIAG:simulate_vatsim_failure:1|0).
        # See _handle_fw_addon().
        self._diag_simulate_vatsim_failure: bool = False
        # Diagnostic-only: shifts the pressure_msl reported by Open-Meteo for
        # every zone by this many hPa before it's turned into a QNH, so the
        # gap between our OM-derived weather and PSX's own default/METAR
        # weather can be widened on demand (addon=FRANKENWEATHER:DIAG:
        # qnh_offset_hpa:<float>). See _handle_fw_addon() / _update_zones().
        self._diag_qnh_offset_hpa: float = 0.0
        # Manual weather parameters (used when _fw_mode == "manual")
        self._manual_wx_force_update: bool = False
        self._manual_wx_params: dict = {
            # Not 45000/45000 -- see fw_cb.py's _NO_HI_CLOUD_CEILING_FT /
            # _NO_LO_CLOUD_CEILING_FT (PSX corrects both 45000 itself, and
            # a hi/lo pair sharing one value, within ~40ms of accepting
            # either).
            "hi_oktas": 0, "hi_top": 44950, "hi_base": 44950,
            "lo_oktas": 0, "lo_top": 44900, "lo_base": 44900,
            "cb_oktas": 0, "cb_top": 35000, "cb_base": 3000,
            "turb_severity": 0, "turb_top": 5000, "turb_base": 0,
            "mb_mode": 0, "mb_chance": 0, "mb_outflow": 400,
            "inv_on": False, "inv_top": 2320, "inv_tmp": 5,
            "wind_dir": 0, "wind_spd": 0, "wind_gust": 0, "wind_var": 0,
            "precip": 0, "vis_m": 9999,
            "surf_temp": 15, "qnh_hpa": 1013.25,
        }

        # -------------------------------------------------------------------
        # Turbulence subsystem (merged from frankenturb)
        # -------------------------------------------------------------------
        self._turb_enabled: bool = True
        self._turb_low_speed: bool = True
        self._turb_manual_turb_enabled: bool = False
        self._turb_manual_turb_kind: str = "mechanical"
        self._turb_manual_turb_intensity: float = 0.3
        self._turb_intensity_bias: int = 100   # 0-999 %
        self._turb_wind_mode: str = "live"     # "live", "psx", or "manual"
        self._turb_manual_wind_dir: int = 0
        self._turb_manual_wind_spd: int = 0
        self._turb_psx_wind = None             # (dir_deg, speed_kt) or None
        self._turb_lateral_size_bias: int = 50
        self._turb_rate: int = 100             # 0-100, injection rate scale
        self._turb_profile: str = "stable"     # _TURB_PROFILES
        # Testing profile: rare turbulence events (_turb_event_step()), the
        # crew's multiplier per kind, the kind of area the aircraft is in
        # ((area, reason), _turb_area_class()) and the last events, for the
        # web UI
        self._turb_events = _turb_events.EventScheduler()
        self._turb_event_mult: dict = dict.fromkeys(_turb_events.KINDS, 1.0)
        self._turb_area: tuple = ("clean", "")
        self._turb_event_shown = None
        self._turb_event_burst_sent = False   # this tick, see _turb_event_step()
        self._turb_event_log: collections.deque = collections.deque(maxlen=20)
        self._flight_log_last = 0.0
        # (monotonic time, vertical) of the last wave/MTW burst decision,
        # see _pick_wave_burst_testing()
        self._wave_vert_prev: Optional[tuple] = None
        # (monotonic time, direction, size) of the recent up/down bursts,
        # see _updown_budget()
        self._updown_bursts: collections.deque = collections.deque()
        # Bursts since the last summary line (_log_burst_summary())
        self._burst_summary_start = time.monotonic()
        self._burst_summary: list = []
        # Other clients' WxBursts since then (handle_other_wxburst()): (base,
        # direction, size), and lightning flashes
        self._other_burst_summary: list = []
        self._other_lightning = 0
        self._own_write: Optional[str] = None   # key psx_send_and_set() is echoing locally
        self._turb_type_enabled: dict = {k: True for k in _TURB_TYPES}
        self._turb_type_biases: dict = {k: 100 for k in _TURB_TYPES}
        self._turb_msfs_magnitude: int = 100  # 0-200%; scales MSFS turbulence influence
        self._turb_msfs_factor: float = 1.0   # last computed raw factor (for display)
        # What's injected: intensity x biases x MSFS factor (for display)
        self._turb_effective_intensity: float = 0.0
        # (intensity, monotonic time, state, lat, lon) of the smoothed
        # terrain model turbulence, see _smooth_terrain()
        self._terrain_smooth: Optional[tuple] = None
        self._turb_engine: Optional[TurbulenceEngine] = None
        self._turb_state: Optional[TurbulenceState] = None
        self._turb_sources: list = []
        self._turb_print_count: int = 0
        self._turb_state_changed_event: asyncio.Event = asyncio.Event()
        self._turb_pirep_fetcher = None
        self._turb_cape_fetcher = None
        self._turb_gairmet_fetcher = None
        # The turbulence engine and fetchers aren't thread-safe; both
        # turbulence_coro() and pirep_levels_coro() call them in executor
        # threads, so each is called under its own lock (_turb_locked()).
        self._turb_locks: dict = {name: threading.Lock()
                                  for name in ("engine", "pirep", "cape", "gairmet")}

        # Standalone web UI state cache (populated by broadcast coroutines)
        self._web_state: Optional[dict] = None
        self._web_turbstate: Optional[dict] = None
        self._web_state_received_at: float = 0.0
        self._web_turbstate_received_at: float = 0.0

        # Snapshot of every web-GUI-configurable setting at its built-in default,
        # captured before any --config-file load — used by "reset to default".
        self._default_config_dict: dict = self._build_config_dict()

    # ------------------------------------------------------------------
    # PSX helpers
    # ------------------------------------------------------------------

    def _psx_send(self, key: str, value: str) -> bool:
        """Send key=value to PSX, unless a higher-UUID FRANKENWEATHER instance is active.

        Central chokepoint for every weather-related PSX write (zone
        weather, turbulence, wind corridor, STATE/TURBSTATE/WINDSTATE
        broadcasts) so a conflict-paused instance (see _conflict_paused())
        never writes to the shared PSX network no matter which subsystem is
        calling. Returns True if actually sent.
        """
        if self._conflict_paused():
            self.logger.debug("PSX send suppressed (conflict pause): %s", key)
            return False
        self.logger.debug("→ PSX %s = %s", key, value)
        self.psx.send(key, value)
        return True

    def _psx_has(self, key: str, current: Optional[str], value: str) -> bool:
        """Return True if PSX's current value of key is value, or PSX's rewriting of it."""
        if current == value:
            return True
        if key.startswith("WxMode"):
            return _wxmode_equivalent(current, value)
        return self._is_wx_key(key) and _wx_equivalent(current, value)

    def psx_send_and_set(self, key: str, value: str, force: bool = False) -> None:
        """Send a PSX key=value and update the local variable cache.

        Skips the actual send if PSX's own subscribed-variable cache
        (`self.psx.get(key)`, populated from every incoming update, not just
        our own writes) already holds this exact value -- re-sending data
        PSX already has is believed to be able to make PSX treat the
        injection as an instant snap instead of a smooth transit (see
        project_frankenweather_altitude_jump memory, 2026-10-14 case: a
        byte-identical resend of the focused zone's weather was immediately
        followed by an instant altitude jump). Because the cache comes from
        subscriptions, not a separate tracked dict, it self-heals whenever
        PSX's own state diverges from what we last sent for any reason --
        a reconnect (PSX resends current values on connect), or PSX's own
        GUI/manual weather controls being used while FrankenWeather was in
        "disabled" mode -- without needing any explicit cache-clear logic.
        PSX's own rewriting of what we sent (WxMode lat/lon rounding and
        suffix, Wx wind speed padding) counts as the same value.
        Pass force=True for values that must always be transmitted
        regardless of the cache, such as Qi243 (a one-shot mode-gate pulse,
        not a persistent state value PSX "already has").
        """
        if key in _OWNED_KEYS:
            self._owned_values[key] = value
        current = self.psx.get(key)
        if not force and self._psx_has(key, current, value):
            self.logger.debug("→ PSX %s = %s SKIPPED (unchanged)", key, value)
            return
        if not self._psx_send(key, value):
            return
        self._own_write = key
        try:
            self.psx._set(key, value)  # pylint: disable=protected-access
        finally:
            self._own_write = None

    def _station_only_metar(self, zone_num: int, icao: str, now: datetime) -> str:
        """Return the METAR for a zone whose weather we build ourselves.

        "<icao> <DDHHMMZ> RMK FRANKENWX MODEL WX": no weather groups on
        purpose. PSX rebuilds a zone's weather from its METAR whenever the
        METAR's weather content changes, undoing everything not in it --
        SIGMET CBs, our CB tops, cloud layers (live-tested 2026-10-23). A
        station-and-time-only METAR doesn't trigger that, not even with the
        remark (live-tested 2026-10-24), which tells the Instructor weather
        page why it's so short. It replaces the earlier METAR there; an
        empty one is ignored by PSX, leaving a stale METAR there. The time
        is that of the zone's placement: the same string is reused for as
        long as the zone keeps its name, so it isn't resent.
        """
        cached = self._zone_station_metar.get(zone_num)
        if cached is not None and cached.split(" ", 1)[0] == icao:
            return cached
        metar = f"{icao} {now.strftime('%d%H%MZ')} RMK {METAR_NOTE_MODEL}"
        self._zone_station_metar[zone_num] = metar
        return metar

    def _stable_zone_elev(self, zone_num: int, lat: float, lon: float, elev_m: float) -> float:
        """Return the elevation to send in zone_num's WxMode, fixed for as long as it stays put.

        The first elevation used at a position sticks until the zone moves.
        Open-Meteo's reported elevation for the same point varies between
        fetches, and differs from the airport's, so a zone switching between METAR
        and Open-Meteo data would otherwise get its WxMode rewritten with a
        new elevation at an unchanged position (2026-10-21 DNMM-VVDN: 61 such
        rewrites). Any WxMode rewrite is something PSX may treat as a zone
        reposition, so avoid it unless the zone actually moved.
        """
        cached = self._zone_elev.get(zone_num)
        if cached is not None and cached[0] == lat and cached[1] == lon:
            return cached[2]
        self._zone_elev[zone_num] = (lat, lon, elev_m)
        return elev_m

    def _wxbasic_writable(self) -> bool:
        """Return True if WxBasic may be written now: on the ground, or the aircraft is in it.

        In flight the aircraft is virtually always inside one of our 7 zones,
        so WxBasic's content doesn't affect it -- but writing WxBasic does:
        it correlates with PSX applying in-progress zone transits instantly
        (PFD altitude snaps), see _WXBASIC_MAX_WRITE_TAS_KT.
        """
        if self.focused_zone == 0:
            return True
        return self.ac_tas_kt is not None and self.ac_tas_kt < _WXBASIC_MAX_WRITE_TAS_KT

    def _write_wxbasic(self, wx_str: str) -> None:
        """Write WxBasic if _wxbasic_writable(), otherwise skip it entirely; never with CBs."""
        if not self._wxbasic_writable():
            self.logger.debug("WxBasic write skipped (airborne, aircraft not in WxBasic)")
            return
        wx_str = _without_cb(wx_str)
        self.zone_wx[0] = wx_str
        self.psx_send_and_set("WxBasic", wx_str)

    def _clear_planet_cb(self, value: Optional[str]) -> bool:
        """Set the planet zone's CB coverage to 0 if it isn't; return True if it was written.

        Whoever put CBs there (a situ, PSX, another client), see _without_cb().
        Only the CB coverage changes, so it's written in flight too, unlike
        other WxBasic changes (_wxbasic_writable()): CB-field-only changes
        didn't move the PFD (test sim 2026-10-28, FL350 and FL100). Not while
        PSX is in charge of the weather (mode "disabled").
        """
        if not value or self._fw_mode == "disabled" or not self.psx_connected:
            return False
        cleared = _without_cb(value)
        if cleared == value:
            return False
        self.logger.info("Planet zone (WxBasic) had %s oktas of CB: set to 0 (PSX's own CB cells "
                         "would show when the aircraft is outside our zones)", value.split(";")[9])
        self._inject_wx_slow_transit()
        self.psx_send_and_set("WxBasic", cleared)
        return True

    def _inject_wx_slow_transit(self) -> None:
        """Ask PSX to smooth-transit the weather write(s) about to follow.

        Sent immediately before every zone weather, MSFS sync, or wind
        corridor write, so a zone relocation, a new area becoming active, or
        an MSFS QNH sync never shows up on the instruments as an abrupt jump.
        See _WX_SLOW_TRANSIT_MS for why a fixed, generous value is always
        safe here: nothing FrankenWeather writes ever needs PSX's fast/instant
        transit mode.
        """
        self.psx_send_and_set("Qi243", str(_WX_SLOW_TRANSIT_MS), force=True)

    def _sync_psx_clock(self) -> None:
        """Sync PSX clocks (TimeEarth, TimeClockL, TimeClockR) to current real-world time.

        Called from three independently-toggleable triggers (see
        _timesync_on_startup / _timesync_on_situ_load / _timesync_periodic):
        on every PSX connect/reconnect, on every situ load, and periodically
        if PSX's clock has drifted from real time. See time_sync_coro() for
        the periodic drift check.
        """
        ms = int(time.time() * 1000)
        self.logger.info("Syncing PSX clocks to real time: %d ms", ms)
        for key in ("Qs123", "Qs124", "Qs125"):
            self.psx_send_and_set(key, str(ms))

    # ------------------------------------------------------------------
    # PSX event handlers
    # ------------------------------------------------------------------

    def handle_piba(self, _key: str, value: str) -> None:
        """Update aircraft position from PiBaHeAlTas."""
        try:
            parts = value.split(';')
            if len(parts) < 7:
                return
            self.ac_hdg = math.degrees(float(parts[2])) % 360
            self.ac_alt_ft = float(parts[3]) / 1000.0
            self.ac_tas_kt = float(parts[4]) / 1000.0
            self.ac_lat = math.degrees(float(parts[5]))
            self.ac_lon = math.degrees(float(parts[6]))
        except (ValueError, IndexError):
            return
        if (self.cloud_sync_last_alt_ft is not None and
                abs(self.ac_alt_ft - self.cloud_sync_last_alt_ft) > 500):
            self.cloud_sync_last_alt_ft = self.ac_alt_ft
            self._apply_msfs_sync()

    @staticmethod
    def _is_wx_key(key: str) -> bool:
        """Return True for a zone's weather content: WxBasic or Wx1..Wx7."""
        return key == "WxBasic" or (key.startswith("Wx") and key[2:].isdigit())

    def _owned_restore_wanted(self, key: str) -> bool:
        """Return True if an _OWNED_KEYS variable should be kept at our value right now.

        Not while PSX itself is in charge of weather (WxAutoSet=1, set only
        when _fw_mode is "disabled"). Not zone weather content while the
        METAR-fallback path is active: fallback zones get their content from
        PSX's own METAR parser (via Metar<N>), so what we last wrote there is
        stale -- stomping PSX's recompute back would fight it indefinitely.
        WxBasic only when _wxbasic_writable().
        """
        if self._fw_mode == "disabled":
            return False
        if self._is_wx_key(key) and self._metar_fallback_active:
            return False
        if key == "WxBasic" and not self._wxbasic_writable():
            return False
        return key in self._owned_values

    def _owned_matches(self, key: str, value: Optional[str]) -> bool:
        """Return True if PSX's value for an _OWNED_KEYS variable is (equivalent to) ours."""
        desired = self._owned_values.get(key)
        return desired is not None and self._psx_has(key, value, desired)

    def handle_owned_change(self, key: str, value: str) -> None:
        """Restore a weather variable we control if someone else changed it.

        Called for every update of an _OWNED_KEYS variable, our own writes
        too (psx.py's local cache update), which match and are ignored. A
        situ load is expected to change them (logged as such); any other
        change during normal operation is another client overriding us and
        is logged as a warning. Either way our value is written back by
        _restore_owned(), shortly after the last change.

        Zone weather content changing within _PUSH_COOLDOWN_S of our own
        last write is PSX rebuilding a zone from the METAR we just gave it,
        not another client, and is left alone (as before) -- except while a
        situ is loading (between load1 and load3), whose burst would
        otherwise hide behind the first restore.
        """
        if key == "WxBasic" and self._clear_planet_cb(value):
            return
        if self._owned_matches(key, value) or not self._owned_restore_wanted(key):
            focused_key = "WxBasic" if self.focused_zone == 0 else f"Wx{self.focused_zone}"
            if key == focused_key and self._fw_mode != "disabled":
                self._apply_msfs_sync()
            return
        elapsed = time.time() - self.last_write_time
        if self._is_wx_key(key) and not self.psx_paused and elapsed < _PUSH_COOLDOWN_S:
            self.logger.debug("← PSX %s changed %.1fs after our last write — ignoring echo",
                              key, elapsed)
            return
        if (self.psx_paused or
                time.monotonic() - self._last_situ_load_mono < _SITU_LOAD_GRACE_S):
            self.logger.debug("Situ load changed %s — restoring ours", key)
            self._owned_restore_situ = True
        elif key in self._owned_restore_blocked:
            self.logger.warning("%s changed by another client — not restoring ours, "
                                "it keeps being changed (theirs: %s)", key, value)
        else:
            self.logger.warning("%s changed by another client — restoring ours (theirs: %s)",
                                key, value)
        self._owned_restore_due = time.monotonic() + _OWNED_RESTORE_DELAY_S
        if self._owned_restore_task is None or self._owned_restore_task.done():
            self._owned_restore_task = asyncio.get_running_loop().create_task(
                self._restore_owned(), name="RestoreOwnedWx")

    def _owned_restore_allowed(self, key: str) -> bool:
        """Return False (error logged once) if key was restored too often lately (safety valve)."""
        now = time.monotonic()
        times = self._owned_restore_times.setdefault(key, collections.deque())
        while times and now - times[0] > _OWNED_RESTORE_WINDOW_S:
            times.popleft()
        if len(times) >= _OWNED_RESTORE_MAX:
            if key not in self._owned_restore_blocked:
                self._owned_restore_blocked.add(key)
                self.logger.error(
                    "%s restored %d times in %.0f s — someone keeps changing it; "
                    "no longer restoring it until that stops",
                    key, len(times), _OWNED_RESTORE_WINDOW_S)
            return False
        if key in self._owned_restore_blocked:
            self._owned_restore_blocked.discard(key)
            self.logger.info("%s: restoring it again", key)
        times.append(now)
        return True

    async def _restore_owned(self) -> None:
        """Write our value back to every _OWNED_KEYS variable someone else changed.

        Waits until _OWNED_RESTORE_DELAY_S after the last change and until a
        situ load has finished, then writes in _update_zones()' order: zone
        positions (WxMode) first, a second for PSX to move the zones, then
        their weather (Wx, WxBasic) and METARs.
        """
        while True:
            wait = self._owned_restore_due - time.monotonic()
            if self.psx_paused:
                wait = max(wait, 0.5)
            if wait <= 0:
                break
            await asyncio.sleep(wait)
        situ_load, self._owned_restore_situ = self._owned_restore_situ, False
        if not self.psx_connected:
            return
        first = [key for key in _OWNED_KEYS if not self._is_wx_key(key) and
                 not key.startswith("Metar")]
        later = ([key for key in _OWNED_KEYS if self._is_wx_key(key)] +
                 [key for key in _OWNED_KEYS if key.startswith("Metar")])
        restored = []
        for group in (first, later):
            if group is later and restored:
                await asyncio.sleep(1.0)
            for key in group:
                if (not self._owned_restore_wanted(key) or
                        self._owned_matches(key, self.psx.get(key)) or
                        not self._owned_restore_allowed(key)):
                    continue
                if not restored:
                    self._inject_wx_slow_transit()
                if group is later:
                    self.last_write_time = time.time()
                self.psx_send_and_set(key, self._owned_values[key])
                restored.append(key)
        if restored:
            self.logger.info("Restored our %s%s", " ".join(restored),
                             " (changed by a situ load)" if situ_load else "")
            self._state_changed_event.set()

    def handle_focused_zone(self, _key: str, value: str) -> None:
        """Track the PSX focused weather zone and re-apply MSFS sync."""
        self.focused_zone = int(value)
        self._apply_msfs_sync()

    def _web_cache_get(self, name: str) -> Optional[str]:
        """Provide PSX variable lookups for the standalone web UI."""
        if name == 'FocussedWxZone':
            return str(self.focused_zone)
        m = re.match(r'^Wx(\d+)$', name)
        if m:
            zone_num = int(m.group(1))
            return self.zone_wx.get(zone_num)
        if self.psx and re.match(r'^Metar\d+$', name):
            return self.psx.get(name)
        if self.psx:
            return self.psx.get(name)
        return None

    def _update_fmc_arpts(self) -> None:
        """Refresh fmc_dep_icao/fmc_dst_icao from the current PSX FMC route state."""
        if not self.psx:
            return
        mode_str = self.psx.get("FmcRteViAcMo") or ""
        if len(mode_str) < 3 or mode_str[2] not in ('1', '2'):
            if self.fmc_dep_icao is not None or self.fmc_dst_icao is not None:
                self.logger.info("FMC: no active route — dep/dst arpt zones released")
            self.fmc_dep_icao = None
            self.fmc_dst_icao = None
            return
        rte_key = "FmcRte1" if mode_str[2] == '1' else "FmcRte2"
        rte_str = self.psx.get(rte_key) or ""
        fields = rte_str.split(';')
        dep_raw = fields[0].strip() if len(fields) > 0 else ''
        dst_raw = fields[1].strip() if len(fields) > 1 else ''
        dep = dep_raw if len(dep_raw) == 4 and dep_raw.isalnum() else None
        dst = dst_raw if len(dst_raw) == 4 and dst_raw.isalnum() else None
        if dep != self.fmc_dep_icao or dst != self.fmc_dst_icao:
            self.logger.info("FMC route %s: dep=%s dst=%s", mode_str[2], dep, dst)
        self.fmc_dep_icao = dep
        self.fmc_dst_icao = dst

    def handle_fmc_change(self, _key: str, _value: str) -> None:
        """Update dep/dst airports when FMC route mode or route data changes."""
        self._update_fmc_arpts()
        self._update_fmc_route_waypoints()
        self.fmc_changed_event.set()

    # ------------------------------------------------------------------
    # Enroute wind importer — route waypoint tracking
    # ------------------------------------------------------------------

    def _update_fmc_route_waypoints(self) -> None:
        """Refresh route_waypoints [(name, lat, lon), ...] from the live FMC route state.

        route_waypoints mirrors PSX's own FmcRte list exactly, which PSX
        trims from the front as waypoints are passed. _enroute_waypoints is
        our own persistent superset for the whole flight: waypoints already
        trimmed by PSX stay in it (marked passed) so the enroute-wind page
        keeps showing them, instead of disappearing the moment PSX drops them.
        """
        if not self.psx:
            return
        mode_str = self.psx.get("FmcRteViAcMo") or ""
        if len(mode_str) < 3 or mode_str[2] not in ('1', '2'):
            if self.route_waypoints:
                self.logger.info("FMC: no active route — enroute wind waypoint list cleared")
                # The FMC route just reset (flight ended) — clear the flight-plan
                # snapshot; the next flight's first WxCorridor load will recapture
                # one, and its first corridor update will start a fresh log file.
                self._corridor_snapshot_txt = None
                self._corridor_snapshot_waypoints = {}
                self._corridor_snapshot_coords = {}
            self._set_route_waypoints([])
            return
        rte_key = "FmcRte1" if mode_str[2] == '1' else "FmcRte2"
        rte_str = self.psx.get(rte_key) or ""
        new_live = _parse_fmc_route_waypoints(rte_str)
        new_live_names = tuple(w[0] for w in new_live)
        old_live_names = tuple(w[0] for w in self.route_waypoints)
        if new_live_names == old_live_names:
            return
        self.route_waypoints = new_live
        persistent_names = [w[0] for w in self._enroute_waypoints]
        if persistent_names and _is_name_suffix(new_live_names, persistent_names):
            dropped = set(persistent_names) - set(new_live_names)
            newly_passed = [
                i for i, name in enumerate(persistent_names)
                if name in dropped and i not in self._waypoint_passed
            ]
            for i in newly_passed:
                self._waypoint_passed.add(i)
                self.logger.info(
                    "Enroute wind: passed %s — freezing its fetched wind",
                    self._enroute_waypoints[i][0])
            if newly_passed:
                # Just a waypoint being passed — the wind data for every
                # remaining waypoint is unchanged, so PSX's own corridor is
                # left in place rather than resent: PSX recalculates when it
                # receives WxCorridorTxt, and that's pointless work when
                # nothing about the actual wind data changed. Only update the
                # UI (so the page can dim the passed waypoint).
                self._enroute_wind_changed_event.set()
            return
        self.logger.info(
            "FMC route changed (%d → %d waypoints, not a simple pass-trim) — "
            "resetting enroute wind fetch state",
            len(persistent_names), len(new_live_names))
        self._set_route_waypoints(new_live)

    def _set_route_waypoints(self, waypoints: list) -> None:
        """Replace both the live and persistent waypoint lists; refetch everything."""
        self.route_waypoints = waypoints
        self._enroute_waypoints = list(waypoints)
        self._waypoint_passed = set()
        self._waypoint_om_wind = {}
        self._enroute_next_fetch_time = 0.0  # fetch immediately on the next cycle
        if not waypoints:
            self._enroute_log_path = None  # next flight starts a fresh log file
        self._enroute_wind_changed_event.set()

    def _current_fl_list_ft(self) -> list:
        """Return the flight levels to use for the generated corridor.

        If the captured flight-plan snapshot parsed successfully, reuse its
        exact flight levels (the union of levels seen across its waypoints)
        so the flight-plan-vs-Open-Meteo diff compares the same levels
        instead of needing to interpolate between two different grids —
        but only if that list is safe to build a corridor with (see
        _valid_fl_list). Falls back to a fixed default set otherwise (no
        snapshot yet, an unsupported/unparseable corridor format, or a
        parsed level list that looks unsafe) — generating a structurally
        correct corridor always takes precedence over matching the flight
        plan's levels.
        """
        if self._corridor_snapshot_waypoints:
            levels = sorted({
                fl for lvls in self._corridor_snapshot_waypoints.values() for fl in lvls
            })
            if _valid_fl_list(levels):
                return levels
        return list(_ENROUTE_FL_LIST_FT)

    @staticmethod
    def _corridor_pretty(corridor_txt: Optional[str]) -> str:
        """Render a '^'-delimited PSX wind corridor as one section per line, for readability."""
        if not corridor_txt:
            return "(none)"
        return corridor_txt.replace('^', '\n')

    @staticmethod
    def _fmt_epoch(epoch: Optional[float]) -> str:
        """Format a Unix epoch as a UTC timestamp string, or 'never' if falsy."""
        if not epoch:
            return "never"
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    @staticmethod
    def _fmt_wind(w: Optional[dict]) -> str:
        """Format a {dir_deg, spd_kt, oat_c} dict as e.g. '260/26kt -7C', or '—' if absent."""
        if not w:
            return "—"
        return f"{w['dir_deg']:03.0f}/{w['spd_kt']:.0f}kt {w['oat_c']:+.0f}C"

    @staticmethod
    def _fmt_diff(d: Optional[dict]) -> str:
        """Format a diff {dir_deg, spd_kt, oat_c} dict as signed deltas, or '—' if absent."""
        if not d:
            return "—"
        return f"dir{d['dir_deg']:+.0f} spd{d['spd_kt']:+.0f}kt oat{d['oat_c']:+.0f}C"

    def _render_enroute_wind_text(self, state: dict) -> str:
        """Render the enroute-wind windstate dict as a human-readable text report."""
        lines = [
            f"FrankenWeather enroute wind report — {self._fmt_epoch(time.time())}",
            f"Enroute wind importer enabled: {state['enabled']}",
            f"Last Open-Meteo fetch: {self._fmt_epoch(state['last_fetch_epoch'])}",
            f"Next Open-Meteo fetch: {self._fmt_epoch(state['next_fetch_epoch'])}",
            "",
        ]
        if not state["has_snapshot"]:
            lines.append("No flight-plan wind corridor snapshot captured yet.")
        elif not state["snapshot_parseable"]:
            lines.append(
                "Flight plan wind corridor could not be parsed (unsupported format) — "
                "no flight-plan-vs-Open-Meteo comparison is available.")
            lines.append("Raw flight-plan wind corridor:")
            lines.append(self._corridor_pretty(state.get("snapshot_raw")))
        else:
            lines.append(
                "Per-waypoint flight-plan vs. Open-Meteo wind "
                "(diff = open-meteo minus flight-plan):")
            for wp in state["waypoints"]:
                status = "PASSED" if wp["passed"] else "ahead"
                lines.append(
                    f"  {wp['name']:6s} [{status:6s}] lat={wp['lat']:.3f} lon={wp['lon']:.3f}")
                if not wp["levels"]:
                    lines.append("      (no wind data yet)")
                for lvl in wp["levels"]:
                    lines.append(
                        f"      FL{lvl['fl_ft'] // 100:03d}: "
                        f"flight-plan={self._fmt_wind(lvl['flightplan']):18s} "
                        f"open-meteo={self._fmt_wind(lvl['openmeteo']):18s} "
                        f"diff={self._fmt_diff(lvl['diff'])}")
        lines.append("")
        lines.append("Flight-plan wind corridor (raw, as first captured):")
        lines.append(self._corridor_pretty(self._corridor_snapshot_txt))
        lines.append("")
        lines.append("Last Open-Meteo-generated wind corridor sent to PSX:")
        lines.append(self._corridor_pretty(self._enroute_last_corridor_txt))
        return "\n".join(lines)

    def _save_enroute_wind_log(self) -> None:
        """If --save-logs is set, write a JSON + human-readable enroute wind log per flight.

        Called after every WxCorridor refresh (not just at flight end), so the
        files stay current throughout the flight — handy for development
        without waiting for the flight to finish. The base filename is fixed
        the first time this runs for a given flight and reused for every
        subsequent write; _set_route_waypoints([]) clears it on flight end so
        the next flight gets a fresh pair of files.
        """
        if not self.args or not self.args.save_logs:
            return
        if self._enroute_log_path is None:
            os.makedirs(self.args.save_logs, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self._enroute_log_path = os.path.join(self.args.save_logs, f"enroute_wind_{ts}")
        try:
            state = self._build_windstate_dict()
            with open(f"{self._enroute_log_path}.json", "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
            with open(f"{self._enroute_log_path}.txt", "w", encoding="utf-8") as fh:
                fh.write(self._render_enroute_wind_text(state))
        except OSError as exc:
            self.logger.warning("Enroute wind: failed to save log: %s", exc)

    def _sigmet_active(self) -> bool:
        """Return True if frankenweather's SIGMET system is on (--sigmet, not switched off)."""
        return self._sigmet_enabled and self._sigmet_own

    def handle_sigmet_change(self, _key: str, value: str) -> None:
        """Re-parse all SIGMETs when PSX downloads updated SIGMET data.

        Not while we own the SIGMETs: PSX then only has our own filtered
        text, and our own download is the source of truth. Without our
        SIGMET system (no --sigmet, or switched off) they are only shown on
        the map; frankenweather doesn't use them.
        """
        self._psx_sigmet_raw = value
        if self._own_sigmets_applied:
            return
        self.sigmets = _parse_sigmets(value)
        self.ts_sigmets = ([s for s in self.sigmets if s['hazard'] == 'TS']
                           if self._sigmet_active() else [])
        self.logger.info("SIGMETs: %d active areas (%d TS) (raw %d bytes)",
                         len(self.sigmets), len(self.ts_sigmets), len(value))

    def handle_sigmet_on(self, _key: str, value: str) -> None:
        """Track PSX's own live SigmetOn bitmask, for _write_sigmet_on()'s read-modify-write."""
        try:
            self._sigmet_on_bits = int(value)
        except ValueError:
            pass

    def handle_aero_ckbxs(self, _key: str, value: str) -> None:
        """Track PSX's AeroCkBxs bitmask; enforce the OAT-error altimetry bit stays set.

        frankenweather should always want bit 1 ("Altitude indicators
        with OAT error") on, so PSX's altimetry correctly reacts to OAT
        (including SIGMET-driven freezing-temperature overrides) the way
        real aircraft do (per Aerowinx/Hardy). Runs every time PSX reports
        this value -- at connect/reconnect (PSX resends every subscribed
        variable's current value, so this needs no separate "on connect"
        hook) and whenever it changes for any other reason, e.g. someone
        unchecked it via PSX's own GUI -- so it gets caught and
        re-enforced regardless of when or how it happened, not just at
        startup/reconnect/re-enable. Read-modify-write: bits 2 ("CG varies
        with fuel distribution") and 4 ("5th engine installed") are
        aircraft-type configuration and are never touched.
        """
        try:
            self._aero_ckbxs_bits = int(value)
        except ValueError:
            return
        if not self._aero_ckbxs_bits & _AERO_OAT_ERROR_BIT:
            self.psx_send_and_set(
                "AeroCkBxs", str(self._aero_ckbxs_bits | _AERO_OAT_ERROR_BIT))

    def handle_wxaloft(self, _key: str, value: str) -> None:
        """Track PSX's raw WxAloft (Qs327) string, for _write_tropopause()'s read-modify-write."""
        self._wxaloft_raw = value

    def _write_sigmet_on(self, embed: bool, trigger_download: bool,
                         auto_download: Optional[bool] = None) -> None:
        """Write SigmetOn (Qi262), touching only the embed/download bits.

        Read-modify-write against the last value PSX itself reported
        (self._sigmet_on_bits), not a bare literal -- SigmetOn is a 0-15
        bitmask and bit 8 is undocumented; whatever it means, this must
        never change it. PSX's own periodic download (bit 2) is only
        changed when auto_download is given (--sigmet).

        Arms Qi243 (WxSlowTransit) first, same as every other PSX weather
        write -- found via forensic log analysis of a real flight where an
        instant PFD altitude snap landed ~17s after the last Qi243 arm had
        expired, right as a SigmetOn write landed. Later confirmed by
        Aerowinx (Hardy) that PSX already auto-smooths SIGMET-driven
        changes regardless of Qi243's state, so this call is not actually
        necessary -- kept anyway per the user's own call: cheap, harmless
        insurance, not worth reverting.
        """
        self._inject_wx_slow_transit()
        new_value = self._sigmet_on_bits & ~_SIGMET_KNOWN_BITS
        if embed:
            new_value |= _SIGMET_EMBED_BIT
        if trigger_download:
            new_value |= _SIGMET_DOWNLOAD_BIT
        if auto_download is True:
            new_value |= _SIGMET_AUTO_DOWNLOAD_BIT
        elif auto_download is False:
            new_value &= ~_SIGMET_AUTO_DOWNLOAD_BIT
        self.psx_send_and_set("SigmetOn", str(new_value))

    def _write_tropopause(self) -> None:
        """EXPERIMENTAL (--sync-tropopause): set WxAloft's tropopause fields from Open-Meteo.

        Computes tropopause altitude/temperature/wind at the aircraft's
        current position from the same Open-Meteo pressure-level profile
        (and the same cache) the turbulence engine already fetches for
        itself (see WindProfile.tropopause(), a WMO lapse-rate detection),
        then writes PSX's "Constantly" form for all three Planet Weather
        page tropopause fields in one go. Wind variability is always
        written as 0 (no ensemble-spread data available from Open-Meteo)
        and gust is always set equal to speed, to avoid PSX's own
        gust-must-be->=-speed auto-bump producing a transient mismatch
        (confirmed live, 2026-10-15, decoding this exact format).

        WxAloft (Qs327) format, decoded live against a real PSX instance:
        [aA]<alt ft>[tT]<abs temp C>[wW]<variability><dir><speed>;<gust>;cv<lat> <lon>
        Lowercase letter = that field is in its "auto" PSX mode (variable
        by latitude/season for alt+temp, by jet-stream-constellation for
        wind); uppercase = "Constantly", with the following number(s) as
        the fixed override. variability/direction are fixed 3-digit
        zero-padded fields; speed/gust are variable-width, no padding.
        The trailing ";cv<lat> <lon>" is PSX's own position reference --
        never ours to construct, only to preserve verbatim via
        self._wxaloft_raw (see handle_wxaloft()).

        Arms Qi243 first: this is literally Aerowinx's own example of
        "weather data" to arm before injecting (Qi243's own documentation
        uses "tropopause data" as its example), unlike SigmetOn above.

        Only reliable data is written (see _tropo_target() and
        _TROPO_STARTUP_HOLDOFF_S): until there is some, WxAloft is
        returned to PSX's own auto mode. In flight, a WxAloft write can
        still snap pending smooth transits and jump the PFD altitude (see
        _TROPO_INFLIGHT_MIN_TAS_KT), so it is only written when the target
        differs significantly from PSX's current value
        (_tropo_change_significant()), and at most once per
        _TROPO_MIN_WRITE_INTERVAL_S. On the ground a new target is
        written straight away.
        """
        if self._wxaloft_raw is None or self.ac_lat is None or self.ac_lon is None:
            return
        if self._turb_engine is None:
            return
        idx = self._wxaloft_raw.find(";cv")
        if idx == -1:
            self.logger.warning(
                "WxAloft in unexpected format, not updating tropopause: %s", self._wxaloft_raw)
            return
        suffix = self._wxaloft_raw[idx:]
        profile = self._turb_engine.wind_fetcher.get(
            self.ac_lat, self.ac_lon, datetime.now(timezone.utc))
        now_mono = time.monotonic()
        # Unknown TAS counts as in flight: the cautious choice.
        in_flight = self.ac_tas_kt is None or self.ac_tas_kt >= _TROPO_INFLIGHT_MIN_TAS_KT
        target, reason = self._tropo_target(
            profile.tropopause() if profile is not None else None, now_mono)
        if (in_flight and self._tropo_last_write_mono is not None and
                now_mono - self._tropo_last_write_mono < _TROPO_MIN_WRITE_INTERVAL_S):
            return
        if target is None:
            # No data of our own: hand the tropopause back to PSX's own
            # "auto" modes rather than leave an earlier fixed value of ours
            # in place indefinitely -- lowercase letters, numbers kept as-is
            # (the same toggle the Planet Weather page itself sends).
            new_value = re.sub(r"^[aA](\d+)[tT](\d+)[wW]",
                               lambda m: f"a{m.group(1)}t{m.group(2)}w", self._wxaloft_raw)
            what = (f"{'no wind profile' if profile is None else reason}"
                    " — returning WxAloft to PSX auto")
        else:
            current = _parse_wxaloft_tropopause(self._wxaloft_raw)
            if in_flight and current is not None and not _tropo_change_significant(current, target):
                return
            speed = round(math.hypot(target[2], target[3]))
            direction = round(math.degrees(math.atan2(target[2], target[3]))) % 360
            new_value = (f"A{round(target[0])}T{round(abs(target[1]))}"
                         f"W{0:03d}{direction:03d}{speed};{speed}{suffix}")
            what = (f"{round(target[0])}ft -{round(abs(target[1]))}C"
                    f" {direction:03d}°/{speed}kt (smoothed)")
        if new_value == self._wxaloft_raw:
            return
        self.logger.info("Tropopause: %s (WxAloft %s → %s)", what, self._wxaloft_raw, new_value)
        self._inject_wx_slow_transit()
        self.psx_send_and_set("WxAloft", new_value)
        if in_flight:
            self._tropo_last_write_mono = now_mono

    def _tropo_target(self, result: Optional[tuple], now_mono: float) -> tuple:
        """Record a tropopause sample and return (target, reason).

        result is WindProfile.tropopause()'s return value (None if none was
        found), taken at the aircraft's current position. target is the
        (alt_ft, temp_c, u_kt, v_kt) to aim for: the per-component median
        of every sample found within the last _TROPO_SMOOTH_WINDOW_S, so a
        single forecast flipping between two tropopause layers can't
        trigger a write, with the altitude capped at _TROPO_MAX_ALT_FT.
        target is None (and reason says why) during the startup holdoff or
        with fewer than _TROPO_MIN_SAMPLES samples; a jump of more than
        _TROPO_RESET_DIST_NM since the last sample discards the earlier
        ones, as they describe somewhere else.
        """
        pos = (self.ac_lat, self.ac_lon)
        if (self._tropo_last_pos is not None and
                self._dist_nm(*self._tropo_last_pos, *pos) > _TROPO_RESET_DIST_NM):
            self.logger.info("Tropopause: aircraft repositioned, discarding %d earlier sample(s)",
                             len(self._tropo_samples))
            self._tropo_samples = []
        self._tropo_last_pos = pos
        sample = None
        if result is not None:
            alt_m, temp_c, speed_kt, dir_deg = result
            dir_rad = math.radians(dir_deg)
            sample = (alt_m / 0.3048, temp_c,
                      speed_kt * math.sin(dir_rad), speed_kt * math.cos(dir_rad))
        self._tropo_samples.append((now_mono, sample))
        self._tropo_samples = [
            s for s in self._tropo_samples if now_mono - s[0] <= _TROPO_SMOOTH_WINDOW_S]
        holdoff_left = _TROPO_STARTUP_HOLDOFF_S - (now_mono - self._tropo_start_mono)
        if holdoff_left > 0:
            return None, f"startup, waiting {holdoff_left / 60:.0f} more min"
        valid = [s for _, s in self._tropo_samples if s is not None]
        if len(valid) < _TROPO_MIN_SAMPLES:
            return None, f"only {len(valid)} of {_TROPO_MIN_SAMPLES} samples needed"
        target = [statistics.median(s[k] for s in valid) for k in range(4)]
        target[0] = min(target[0], _TROPO_MAX_ALT_FT)
        return tuple(target), None

    def _aircraft_sigmet_hazards(self) -> list:
        """Return the sorted, deduplicated hazard types of every SIGMET the aircraft is in.

        Empty if the aircraft isn't inside any parsed SIGMET polygon (which,
        per _parse_sigmets()/_sigmet_first_polygon(), excludes any SIGMET we
        couldn't represent as a closed area). Usually one hazard, but
        overlapping SIGMETs (e.g. TS inside a wider TURB area) can give more.
        """
        if self.ac_lat is None or self.ac_lon is None:
            return []
        return sorted({
            sig['hazard'] for sig in self.sigmets
            if _point_in_polygon(self.ac_lat, self.ac_lon, sig['polygon'])
        })

    def _handle_addon(self, _key: str, value: str) -> None:
        """Process addon messages: FRANKENMSFSBRIDGE (slave sim) and FRANKENWEATHER (conflict)."""
        if value.startswith("FRANKENWEATHER:"):
            self._handle_fw_addon(value)
            return
        prefix = "FRANKENMSFSBRIDGE:"
        if not value.startswith(prefix):
            return
        try:
            data = json.loads(value[len(prefix):])
        except (ValueError, KeyError):
            self.logger.warning("Malformed FRANKENMSFSBRIDGE addon: %s", value[:80])
            return
        changed = False
        if "in_cloud" in data:
            new_cloud = bool(data["in_cloud"])
            if new_cloud != self.msfs_in_cloud:
                self.logger.info("MSFS in-cloud (bridge): %s → %s",
                                 self.msfs_in_cloud, new_cloud)
                self.msfs_in_cloud = new_cloud
                changed = True
            if self.ac_alt_ft is not None:
                now_mono = time.monotonic()
                self._msfs_in_cloud_history.append((now_mono, new_cloud, self.ac_alt_ft))
                cutoff = now_mono - _MSFS_CLOUD_TIME_WINDOW_S
                self._msfs_in_cloud_history = [
                    s for s in self._msfs_in_cloud_history if s[0] >= cutoff]
        if "qnh_hpa" in data:
            new_qnh = float(data["qnh_hpa"])
            prev = self.msfs_qnh_hpa
            self.msfs_qnh_hpa = new_qnh
            if prev is None or abs(new_qnh - prev) > 0.5:
                changed = True
        for _key, _attr, _cast in (
                ("cloud_density", "msfs_cloud_density", float),
                ("wind_vert", "msfs_wind_vert", float),
                ("precip_state", "msfs_precip_state", int),
        ):
            if _key in data:
                setattr(self, _attr, _cast(data[_key]))
        self._msfs_bridge_last_seen = time.monotonic()
        self._msfs_bridge_last_seen_epoch = time.time()
        if changed:
            self._apply_msfs_sync()

    def _handle_fw_addon(self, value: str) -> None:  # pylint: disable=too-many-return-statements
        """Dispatch FRANKENWEATHER addon messages: commands, or a STATE/TURBSTATE/WINDSTATE msg."""
        rest = value[len("FRANKENWEATHER:"):]
        if rest.startswith("COMMAND:"):
            self._handle_fw_command(rest[len("COMMAND:"):])
            return
        if rest.startswith("TURBCOMMAND:"):
            self._handle_turb_command(rest[len("TURBCOMMAND:"):])
            return
        if rest.startswith("MANUALWXCOMMAND:"):
            self._handle_manual_wx_command(rest[len("MANUALWXCOMMAND:"):])
            return
        if rest.startswith("DIAG:simulate_openmeteo_download_failure:"):
            flag = rest[len("DIAG:simulate_openmeteo_download_failure:"):]
            self._diag_simulate_om_failure = flag == "1"
            self.logger.warning(
                "DIAG: simulate_openmeteo_download_failure set to %s",
                self._diag_simulate_om_failure)
            # Trigger the next zone update immediately rather than waiting
            # for the normal _REFRESH_MAX_S cycle -- same flag the manual
            # weather panel uses to force an out-of-cycle update.
            self._manual_wx_force_update = True
            return
        if rest.startswith("DIAG:force_zones_stale:"):
            flag = rest[len("DIAG:force_zones_stale:"):]
            self._diag_force_zones_stale = flag == "1"
            self.logger.warning(
                "DIAG: force_zones_stale set to %s", self._diag_force_zones_stale)
            self._manual_wx_force_update = True
            return
        if rest.startswith("DIAG:simulate_vatsim_failure:"):
            flag = rest[len("DIAG:simulate_vatsim_failure:"):]
            self._diag_simulate_vatsim_failure = flag == "1"
            self.logger.warning(
                "DIAG: simulate_vatsim_failure set to %s", self._diag_simulate_vatsim_failure)
            self._manual_wx_force_update = True
            return
        if rest.startswith("DIAG:qnh_offset_hpa:"):
            raw = rest[len("DIAG:qnh_offset_hpa:"):]
            try:
                self._diag_qnh_offset_hpa = float(raw)
            except ValueError:
                self.logger.warning("DIAG: malformed qnh_offset_hpa value %r", raw)
                return
            self.logger.warning(
                "DIAG: qnh_offset_hpa set to %s", self._diag_qnh_offset_hpa)
            self._manual_wx_force_update = True
            return
        # STATE/TURBSTATE/WINDSTATE/SIGMETSTATE/SIGMETJSON are the self-broadcast message types
        # that carry a sending instance's UUID — used only for detecting
        # another FRANKENWEATHER instance on the network, the payload itself
        # is otherwise ignored here (frankenweather.py never consumes another
        # instance's data, only the router UI does).
        for prefix in ("STATE:", "TURBSTATE:", "WINDSTATE:", "SIGMETSTATE:", "SIGMETJSON:"):
            if not rest.startswith(prefix):
                continue
            body = rest[len(prefix):]
            colon = body.find(':')
            if colon > 0:
                self._note_conflict(body[:colon])
            return

    def _note_conflict(self, recv_uuid: str) -> None:
        """Record a peer FRANKENWEATHER instance's UUID and log/react accordingly.

        UUID v4 strings (hex+hyphens) compare correctly as plain strings; the
        lexicographically larger UUID is the one that stays active, mirroring
        whichever instance the other one would also pick (both sides see the
        same two UUIDs and use the same tie-break, so exactly one pauses).
        Both instances log the conflict — not just the one that pauses — so
        it's visible on whichever console you happen to be watching.
        """
        if recv_uuid == self._instance_uuid:
            return
        now = time.monotonic()
        if recv_uuid > self._instance_uuid:
            if self._conflict_uuid != recv_uuid:
                self.logger.error(
                    "CONFLICT: another FRANKENWEATHER instance detected "
                    "(UUID %s > ours %s) — pausing all weather/turbulence "
                    "downloads and PSX writes", recv_uuid, self._instance_uuid)
            self._conflict_uuid = recv_uuid
            self._conflict_last_seen = now
            return
        # We win the tie-break and stay active — but still log it, throttled
        # the same way _conflict_paused() throttles the loser's reminder.
        if (self._conflict_peer_uuid != recv_uuid or
                now - self._conflict_peer_last_logged >= _CONFLICT_LOG_INTERVAL_S):
            self.logger.error(
                "CONFLICT: another FRANKENWEATHER instance detected "
                "(UUID %s < ours %s) — it should be pausing itself; "
                "we continue normally", recv_uuid, self._instance_uuid)
            self._conflict_peer_last_logged = now
        self._conflict_peer_uuid = recv_uuid

    def _conflict_paused(self) -> bool:
        """Return True while a higher-UUID FRANKENWEATHER instance is active.

        While paused, every coroutine bails out at the top of its loop (see
        callers) and _psx_send() refuses to send — the only thing still
        running is the addon subscription that keeps hearing from the other
        instance. Resumes automatically once _CONFLICT_TIMEOUT_S passes
        without hearing from it.
        """
        if self._conflict_uuid is None:
            return False
        age = time.monotonic() - self._conflict_last_seen
        if age < _CONFLICT_TIMEOUT_S:
            now = time.monotonic()
            if now - self._conflict_last_logged >= _CONFLICT_LOG_INTERVAL_S:
                self._conflict_last_logged = now
                self.logger.warning(
                    "*** PAUSED: FRANKENWEATHER %s is active on the network (ours: %s) — "
                    "no downloads, no PSX writes (last seen %.0fs ago) ***",
                    self._conflict_uuid, self._instance_uuid, age)
            return True
        self.logger.info(
            "Conflict with FRANKENWEATHER %s expired (%.0fs) — resuming",
            self._conflict_uuid, age)
        self._conflict_uuid = None
        return False

    def _handle_fw_command(self, json_str: str) -> None:  # pylint: disable=too-many-branches,too-many-statements
        """Apply a FRANKENWEATHER:COMMAND message received via PSX addon."""
        try:
            cmd = json.loads(json_str)
        except ValueError:
            self.logger.warning("Malformed FRANKENWEATHER COMMAND: %s", json_str[:80])
            return
        settings_changed = False
        if "config_action" in cmd:
            action = cmd["config_action"]
            if action == "save":
                self._save_config_file()
            elif action == "load":
                self._load_config_file()
                settings_changed = True
            elif action == "reset":
                self._apply_config_dict(copy.deepcopy(self._default_config_dict))
                self.logger.info("Settings reset to default (not saved to config file)")
                settings_changed = True
            else:
                self.logger.warning("config_action: unknown action %r", action)
        if "msfs_in_cloud_sync" in cmd:
            self._msfs_in_cloud_sync = bool(cmd["msfs_in_cloud_sync"])
            self.logger.info("msfs_in_cloud_sync → %s", self._msfs_in_cloud_sync)
            settings_changed = True
        if "msfs_qnh_check" in cmd:
            val = str(cmd["msfs_qnh_check"])
            if val in ("CHECK", "SYNC"):
                self._msfs_qnh_check = val
                self.logger.info("msfs_qnh_check → %s", val)
                settings_changed = True
        if "timesync_on_startup" in cmd:
            self._timesync_on_startup = bool(cmd["timesync_on_startup"])
            self.logger.info("timesync_on_startup → %s", self._timesync_on_startup)
            settings_changed = True
        if "timesync_on_situ_load" in cmd:
            self._timesync_on_situ_load = bool(cmd["timesync_on_situ_load"])
            self.logger.info("timesync_on_situ_load → %s", self._timesync_on_situ_load)
            settings_changed = True
        if "timesync_periodic" in cmd:
            self._timesync_periodic = bool(cmd["timesync_periodic"])
            self.logger.info("timesync_periodic → %s", self._timesync_periodic)
            settings_changed = True
        if "use_sigmets" in cmd:
            self._use_sigmets = bool(cmd["use_sigmets"])
            self.logger.info("use_sigmets → %s", self._use_sigmets)
            settings_changed = True
        if "sigmet_own" in cmd:
            self._sigmet_own = bool(cmd["sigmet_own"])
            self.logger.info("sigmet_own → %s", self._sigmet_own)
            # Applied by own_sigmet_coro()/_sync_own_sigmets()
            self._own_sigmet_refresh_event.set()
            if self._web_sigmetstate is not None:
                self._update_web_sigmetstate()  # the preset is shown on the page
            settings_changed = True
        if "disable_psx_sigmets" in cmd:
            self._disable_psx_sigmets = bool(cmd["disable_psx_sigmets"])
            self.logger.info("disable_psx_sigmets → %s", self._disable_psx_sigmets)
            if self._sigmet_active():
                # Applied by own_sigmet_coro()/_sync_own_sigmets(): PSX
                # gets no SIGMETs at all.
                self._own_sigmet_refresh_event.set()
            else:
                # Without --sigmet: PSX's own "embedded in the planet
                # weather model" setting.
                self._write_sigmet_on(embed=not self._disable_psx_sigmets, trigger_download=False)
            if self._web_sigmetstate is not None:
                self._update_web_sigmetstate()  # the preset is shown on the page
            settings_changed = True
        if isinstance(cmd.get("sigmet_handle"), dict):
            for hazard, val in cmd["sigmet_handle"].items():
                if hazard in self._sigmet_handle:
                    self._sigmet_handle[hazard] = bool(val)
            self.logger.info("sigmet_handle → %s", ", ".join(
                h for h, on in self._sigmet_handle.items() if on) or "none")
            if self._own_sigmets_applied:
                self._use_own_sigmet_data()
            if self._web_sigmetstate is not None:
                self._update_web_sigmetstate()  # "handled" is shown on the page
            self._own_sigmet_refresh_event.set()
            settings_changed = True
        if cmd.get("sigmet_refresh"):
            self._own_sigmet_download_now = True
            self.logger.info("Own SIGMETs: download requested from the web UI")
            self._own_sigmet_refresh_event.set()
        # cb_airport_mode; avoid_cb_near_airport (true = "move") from routers
        # before 1.26.0
        mode = cmd.get("cb_airport_mode")
        if mode is None and "avoid_cb_near_airport" in cmd:
            mode = "move" if cmd["avoid_cb_near_airport"] else "off"
        if mode in _CB_AIRPORT_MODES and mode != self._cb_airport_mode:
            self._cb_airport_mode = mode
            self.logger.info("cb_airport_mode → %s", mode)
            # Takes effect immediately: move a dep/dst zone to or from its
            # offset position, and rewrite the zones out of cycle
            self._reposition_arpt_zones_for_cb_avoidance()
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()
            settings_changed = True
        if "enroute_wind_enabled" in cmd:
            self._enroute_wind_enabled = bool(cmd["enroute_wind_enabled"])
            self.logger.info("enroute_wind_enabled → %s", self._enroute_wind_enabled)
            if self._enroute_wind_enabled:
                self._enable_enroute_wind()
                self._enroute_wind_changed_event.set()
            else:
                self._restore_corridor_snapshot()
            settings_changed = True
        if "enroute_wind_deviation" in cmd:
            try:
                val = int(cmd["enroute_wind_deviation"])
            except (TypeError, ValueError):
                val = None
            if val in (0, 10, 20, 30, 40, 50, 60, 70, 80):
                self._enroute_wind_deviation = val
                self.logger.info("enroute_wind_deviation → %s", val)
                if self._enroute_wind_enabled:
                    self._apply_enroute_wind_qs497()
                settings_changed = True
            else:
                self.logger.warning(
                    "enroute_wind_deviation: invalid value %r ignored",
                    cmd["enroute_wind_deviation"])
        new_mode = cmd.get("mode")
        if new_mode is None:
            if settings_changed:
                self._state_changed_event.set()
            return
        if new_mode not in ("enabled", "paused", "disabled", "manual"):
            self.logger.warning("FRANKENWEATHER COMMAND: unknown mode %r", new_mode)
            return
        old_mode = self._fw_mode
        if new_mode == old_mode:
            if settings_changed:
                self._state_changed_event.set()
            return
        self._fw_mode = new_mode
        self.logger.info("FRANKENWEATHER mode: %s → %s (via COMMAND)", old_mode, new_mode)
        if self.psx_connected:
            if new_mode == "disabled":
                self._inject_wx_slow_transit()
                self.psx_send_and_set("WxAutoSet", "1")
            elif old_mode == "disabled":
                self._inject_wx_slow_transit()
                self.psx_send_and_set("WxAutoSet", "0")
        if new_mode == "manual" or old_mode == "manual":
            # Force an immediate zone update on either side of a manual-mode
            # transition: entering needs the manual string written right away,
            # and leaving needs the manual weather replaced with real weather
            # right away too — otherwise the old manual data can linger in
            # every zone for up to _REFRESH_MAX_S (5 minutes) since nothing
            # else considers the zones stale immediately after the switch.
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()
        self._state_changed_event.set()

    def _handle_manual_wx_command(self, json_str: str) -> None:
        """Apply a FRANKENWEATHER:MANUALWXCOMMAND message."""
        try:
            cmd = json.loads(json_str)
        except ValueError:
            self.logger.warning("Malformed MANUALWXCOMMAND: %s", json_str[:80])
            return
        int_fields = (
            "hi_oktas", "hi_top", "hi_base",
            "lo_oktas", "lo_top", "lo_base",
            "cb_oktas", "cb_top", "cb_base",
            "turb_severity", "turb_top", "turb_base",
            "mb_mode", "mb_chance", "mb_outflow",
            "inv_top", "inv_tmp",
            "wind_dir", "wind_spd", "wind_gust", "wind_var",
            "precip", "vis_m", "surf_temp",
        )
        for field in int_fields:
            if field in cmd:
                self._manual_wx_params[field] = int(cmd[field])
        if "qnh_hpa" in cmd:
            self._manual_wx_params["qnh_hpa"] = float(cmd["qnh_hpa"])
        if "inv_on" in cmd:
            self._manual_wx_params["inv_on"] = bool(cmd["inv_on"])
        self.logger.info("Manual wx params updated")
        if self._fw_mode == "manual":
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()
        self._state_changed_event.set()

    def _build_manual_wx_string(self) -> str:
        """Build a 24-field PSX Wx string from _manual_wx_params."""
        p = self._manual_wx_params
        wind_var = int(p.get("wind_var", 0))
        wind_dir = int(p.get("wind_dir", 0))
        wind_spd = int(p.get("wind_spd", 0))
        wind_enc = f"{wind_var:03d}{wind_dir:03d}{wind_spd:02d}"
        qnh_psx = int(round(float(p.get("qnh_hpa", 1013.25)) * 2.953))
        inv_on = 1 if p.get("inv_on") else 0
        inv_tmp = int(round(float(p.get("inv_tmp", 5)) * 10))
        return ";".join([
            str(int(p.get("hi_oktas", 0))),      # [0]  hiCloudCov
            str(int(p.get("hi_top", 44950))),     # [1]  hiCloudTop
            str(int(p.get("hi_base", 44950))),    # [2]  hiCloudBase
            str(int(p.get("lo_oktas", 0))),       # [3]  loCloudCov
            str(int(p.get("lo_top", 44900))),     # [4]  loCloudTop
            str(int(p.get("lo_base", 44900))),    # [5]  loCloudBase
            str(int(p.get("turb_severity", 0))),  # [6]  turbIntensity
            str(int(p.get("turb_top", 5000))),    # [7]  turbTop
            str(int(p.get("turb_base", 0))),      # [8]  turbBase
            str(int(p.get("cb_oktas", 0))),       # [9]  cbCloudCov
            str(int(p.get("cb_top", 35000))),     # [10] cbCloudTop
            str(int(p.get("cb_base", 3000))),     # [11] cbCloudBase
            str(int(p.get("mb_mode", 0))),        # [12] microburstMode
            str(int(p.get("mb_chance", 0))),      # [13] microburstRandom (% chance)
            str(int(p.get("mb_outflow", 400))),   # [14] microburstOutflow
            str(inv_on),                           # [15] inversionOn
            str(int(p.get("inv_top", 2320))),     # [16] inversionTop
            str(inv_tmp),                          # [17] inversionTmp (tenths °C)
            wind_enc,                              # [18] arptWindVarDirSpd
            str(int(p.get("wind_gust", 0))),      # [19] arptWindGust
            str(int(p.get("vis_m", 9999))),       # [20] visibMtrs
            str(int(p.get("precip", 0))),         # [21] precipLevel
            str(int(p.get("surf_temp", 15))),     # [22] surfaceTmp
            str(qnh_psx),                          # [23] QNH (inHg×100)
        ])

    async def _update_zones_manual(self) -> None:
        """Write the same manual Wx string to all 7 PSX weather zones."""
        wx_str = self._build_manual_wx_string()
        now = datetime.now(timezone.utc)
        self._inject_wx_slow_transit()
        self.psx_send_and_set("WxAutoSet", "0")
        for zone_num, (lat, lon, icao) in self.zone_positions.items():
            wxmode = build_wxmode_string(lat, lon, _airport_elev_m(icao, 0.0), now.month, icao)
            self.zone_mode[zone_num] = wxmode
            self.zone_is_metar[zone_num] = False
            self.zone_reason[zone_num] = "Manual"
            self.psx_send_and_set(f"WxMode{zone_num}", wxmode)
        await asyncio.sleep(1.0)
        self.last_write_time = time.time()
        self._write_wxbasic(wx_str)
        for zone_num in range(1, 8):
            self.zone_wx[zone_num] = wx_str
            self.psx_send_and_set(f"Wx{zone_num}", wx_str)
        self._state_changed_event.set()
        self.logger.info("Manual mode zone update complete — wx=%s", wx_str[:60])

    # ------------------------------------------------------------------
    # Turbulence subsystem
    # ------------------------------------------------------------------

    def _maybe_inject_burst(self, state: TurbulenceState, intensity: float,
                            tas_kt: float, limit_updown: bool = False) -> None:
        """Inject one WxBurst with probability intensity**1.5 (scaled by the rate setting).

        On the ground only a weaker yaw/roll burst (_GROUND_TURB_CAP).
        limit_updown: up/down bursts one way add up to at most the largest
        single one within _UPDOWN_WINDOW_S (_updown_budget()).
        """
        on_ground = self._on_ground()
        if on_ground:
            intensity = min(intensity, _GROUND_TURB_CAP)
        if intensity < 0.01:
            return
        if random.random() >= (intensity ** 1.5) * (self._turb_rate / 100.0):
            return
        if on_ground:
            base, direction, label = _pick_ground_burst()
        elif self._turb_testing() and state.kind in ('wave', 'mtw'):
            picked = self._pick_wave_burst_testing(state, intensity)
            if picked is None:
                return
            base, direction, label = picked
        else:
            base, direction, label = _pick_burst(state, intensity)
        magnitude = min(99, random.randint(1, max(1, int(intensity * 99))))
        if base == _BURST_SINK and not on_ground and limit_updown:
            magnitude = self._updown_budget(direction, magnitude, intensity)
            if magnitude < 1:
                return
        psx_value = direction * (base + magnitude)
        self.psx_send_and_set("WxBurst", str(psx_value), force=True)
        self._burst_summary.append((base, direction, magnitude, state.kind, intensity))
        self.logger.debug("Injected WxBurst=%d (%s%s%02d)%s", psx_value, label,
                          '+' if direction > 0 else '-', magnitude,
                          f" on the ground at {tas_kt:.0f} kt" if on_ground else "")

    def handle_other_wxburst(self, _key: str, value: str) -> None:
        """Count a WxBurst we didn't send ourselves, for the burst summary.

        Normally PSX's own: it makes its own turbulence (CBs, gusts, wind
        shear, CAT) on the same channels, and lightning flashes as codes
        above 900. Whoever sends it, it adds to ours. Neither PSX nor the
        router sends a client's own writes back to it, so only psx.py's
        local echo of ours (psx_send_and_set()) needs skipping.
        """
        if self._own_write == "WxBurst":
            return
        try:
            burst = int(value)
        except ValueError:
            return
        code = abs(burst)
        if code > 900:
            self._other_lightning += 1
        elif code > 0:
            base = min(_BURST_GUST, (code - 1) // 100 * 100)
            self._other_burst_summary.append((base, _sign(burst), code % 100))

    def _log_burst_summary(self) -> None:
        """Log one line about the bursts of the last _BURST_SUMMARY_S, if there were any.

        Without --debug the individual bursts aren't logged, so this is what
        tells afterwards how hard and in which direction the aircraft was
        being pushed (e.g. "SPD 8 +3/-5": eight speed bursts, three of them
        faster), and by what.
        """
        now = time.monotonic()
        if now - self._burst_summary_start < _BURST_SUMMARY_S:
            return
        bursts, self._burst_summary = self._burst_summary, []
        other, self._other_burst_summary = self._other_burst_summary, []
        lightning, self._other_lightning = self._other_lightning, 0
        self._burst_summary_start = now
        if other or lightning:
            self.logger.info("Other bursts (PSX's own) in the last %.0f s: %d (%s)%s%s",
                             _BURST_SUMMARY_S, len(other),
                             _burst_channels_text(other) or "none",
                             f", size avg {sum(m for _, _, m in other) / len(other):.0f} "
                             f"max {max(m for _, _, m in other)}" if other else "",
                             f", lightning {lightning}" if lightning else "")
        if not bursts:
            return
        channels = _burst_channels_text([(b, d, m) for b, d, m, _, _ in bursts])
        sizes = [m for _, _, m, _, _ in bursts]
        kinds = []
        for kind in dict.fromkeys(k for _, _, _, k, _ in bursts):
            levels = [i for _, _, _, k, i in bursts if k == kind]
            kinds.append(f"{kind} {min(levels) * 100:.0f}-{max(levels) * 100:.0f}%")
        self.logger.info("Bursts in the last %.0f s: %d (%s), size avg %.0f max %d, %s",
                         _BURST_SUMMARY_S, len(bursts), channels,
                         sum(sizes) / len(sizes), max(sizes), ", ".join(kinds))

    def _smooth_terrain(self, state: TurbulenceState, lat: float, lon: float) -> TurbulenceState:
        """Return the terrain model's state with its intensity smoothed (_TERRAIN_SMOOTH_S).

        The wind data comes per 1-degree square and forecast hour, so
        wind, shear, mechanical and wave turbulence used to step where the
        aircraft crossed into another square (~every 7 min at cruise) or
        hour. Fading out, it keeps the kind it had. It smooths the
        intensity as capped (max_intensity, low-level wind shear): the
        uncapped value used to carry over into the next kind, e.g. 0.53
        shear below its 0.30 cap became 0.71 mechanical turbulence (with
        its gust) at 900 ft near EINN, fading over a minute (flight test
        2026-10-25). A position jump (_TERRAIN_SMOOTH_JUMP_NM: a reposition
        or situation load) starts afresh: the previous place's turbulence
        used to fade out over about a minute at the new one.
        """
        now = time.monotonic()
        prev = self._terrain_smooth
        if prev is not None and self._dist_nm(prev[3], prev[4], lat, lon) > _TERRAIN_SMOOTH_JUMP_NM:
            prev = None
        value = min(state.intensity, state.max_intensity)
        if prev is not None and now - prev[1] < 5.0:
            value = prev[0] + (value - prev[0]) * min(1.0, (now - prev[1]) / _TERRAIN_SMOOTH_S)
        if state.kind == "none" and value >= 0.01 and prev is not None:
            state = prev[2]
        self._terrain_smooth = (value, now, state, lat, lon)
        return dataclasses.replace(state, intensity=value)

    def _shown_intensity(self, intensity: float) -> float:
        """Return the effective intensity the UIs show: on the ground, as capped there."""
        return min(intensity, _GROUND_TURB_CAP) if self._on_ground() else intensity

    def _on_ground(self) -> bool:
        """Return True if PSX says the aircraft is on the ground (OnGround)."""
        return (self.psx.get("OnGround") if self.psx else None) == "1"

    def _turb_type_effective_bias(self, kind: str) -> int:
        """Return combined bias for a turbulence type: 0 when disabled."""
        kind = _turb_type(kind)
        if not self._turb_type_enabled.get(kind, True):
            return 0
        return self._turb_type_biases.get(kind, 100)

    def _compute_msfs_turb_factor(self) -> float:
        """Compute turbulence multiplier from MSFS bridge data.

        1.0 out of cloud, or if bridge data is absent or magnitude is zero.
        In MSFS cloud a little more, at most _MSFS_TURB_FACTOR_MAX at 100%
        magnitude: it used to reach ~2.8 in any cloud (stratus and cirrus
        too), on top of everything else, and to cut clear-air turbulence
        to 0.8 (2026-10-24 review).
        """
        if self._turb_msfs_magnitude == 0:
            return 1.0
        if (self._msfs_bridge_last_seen is None or
                time.monotonic() - self._msfs_bridge_last_seen > _MSFS_BRIDGE_TIMEOUT_S):
            return 1.0
        if not self.msfs_in_cloud:
            return 1.0
        raw = 1.1
        # Cloud density: MSFS rarely reports more than ~0.2 even in dense cloud
        if self.msfs_cloud_density is not None:
            raw += 0.1 * max(0.0, min(1.0, self.msfs_cloud_density / 0.2))
        # Vertical wind: up to +0.1 at 10 kt; ignore noise below 3 kt
        if self.msfs_wind_vert is not None:
            raw += 0.1 * max(0.0, min(1.0, (abs(self.msfs_wind_vert) - 3.0) / 7.0))
        raw = min(_MSFS_TURB_FACTOR_MAX, raw)
        # Scale by magnitude: 0 % = no influence, 100 % = full, 200 % = amplified
        return 1.0 + (raw - 1.0) * self._turb_msfs_magnitude / 100.0

    # ------------------------------------------------------------------
    # --config-file (TOML): load/save every setting the web GUI can change
    # ------------------------------------------------------------------

    def _build_config_dict(self) -> dict:
        """Build the config-file dict mirroring every setting the web GUI can change."""
        config = {
            "general": {"mode": self._fw_mode},
            "msfs": {key: getattr(self, attr) for key, attr, _cast in _CONFIG_MSFS_FIELDS},
            "enroute_wind": {
                key: getattr(self, attr) for key, attr, _cast in _CONFIG_ENROUTE_WIND_FIELDS},
            "timesync": {
                key: getattr(self, attr) for key, attr, _cast in _CONFIG_TIMESYNC_FIELDS},
            "sigmet": {
                **{key: getattr(self, attr) for key, attr, _cast in _CONFIG_SIGMET_FIELDS},
                "handle": dict(self._sigmet_handle)},
            "cb_avoidance": {
                key: getattr(self, attr) for key, attr, _cast in _CONFIG_CB_AVOIDANCE_FIELDS},
            "manual_weather": {
                key: self._manual_wx_params[key] for key in _CONFIG_MANUAL_WX_FIELDS},
            "turbulence": {key: getattr(self, attr) for key, attr, _cast in _CONFIG_TURB_FIELDS},
        }
        config["turbulence"]["type_enabled"] = dict(self._turb_type_enabled)
        config["turbulence"]["type_biases"] = dict(self._turb_type_biases)
        config["turbulence"]["event_mult"] = dict(self._turb_event_mult)
        return config

    def _apply_config_dict(self, config: dict) -> None:  # pylint: disable=too-many-branches,too-many-locals
        """Apply a loaded config-file dict onto runtime state; reconciles side effects after."""
        old_mode = self._fw_mode
        general = config.get("general", {})
        if general.get("mode") in ("enabled", "paused", "disabled", "manual"):
            self._fw_mode = general["mode"]

        msfs = config.get("msfs", {})
        for key, attr, cast in _CONFIG_MSFS_FIELDS:
            if key in msfs:
                setattr(self, attr, cast(msfs[key]))
        if msfs.get("wind_sync"):
            self.logger.info("Config file: [msfs] wind_sync is no longer supported (MSFS wind "
                             "sync was removed); the enroute wind importer gives forecast winds")
        if self._msfs_qnh_check not in ("CHECK", "SYNC"):
            self._msfs_qnh_check = "CHECK"

        enroute = config.get("enroute_wind", {})
        for key, attr, cast in _CONFIG_ENROUTE_WIND_FIELDS:
            if key in enroute:
                setattr(self, attr, cast(enroute[key]))
        if self._enroute_wind_deviation not in (0, 10, 20, 30, 40, 50, 60, 70, 80):
            self._enroute_wind_deviation = 30

        timesync = config.get("timesync", {})
        for key, attr, cast in _CONFIG_TIMESYNC_FIELDS:
            if key in timesync:
                setattr(self, attr, cast(timesync[key]))

        sigmet = config.get("sigmet", {})
        for key, attr, cast in _CONFIG_SIGMET_FIELDS:
            if key in sigmet:
                setattr(self, attr, cast(sigmet[key]))
        for hazard, val in (sigmet.get("handle") or {}).items():
            if hazard in self._sigmet_handle:
                self._sigmet_handle[hazard] = bool(val)

        cb_avoidance = config.get("cb_avoidance", {})
        for key, attr, cast in _CONFIG_CB_AVOIDANCE_FIELDS:
            if key in cb_avoidance:
                setattr(self, attr, cast(cb_avoidance[key]))
        if "mode" not in cb_avoidance and "avoid_cb_near_airport" in cb_avoidance:
            # Config files before 1.14.0: on meant moving the airport zone
            self._cb_airport_mode = "move" if cb_avoidance["avoid_cb_near_airport"] else "off"
        if self._cb_airport_mode not in _CB_AIRPORT_MODES:
            self._cb_airport_mode = "off"

        manual_wx = config.get("manual_weather", {})
        for key in _CONFIG_MANUAL_WX_FIELDS:
            if key in manual_wx:
                self._manual_wx_params[key] = manual_wx[key]

        turb = config.get("turbulence", {})
        for key, attr, cast in _CONFIG_TURB_FIELDS:
            if key in turb:
                setattr(self, attr, cast(turb[key]))
        for kind in _TURB_TYPES:
            if kind in turb.get("type_enabled", {}):
                self._turb_type_enabled[kind] = bool(turb["type_enabled"][kind])
            if kind in turb.get("type_biases", {}):
                self._turb_type_biases[kind] = int(turb["type_biases"][kind])
        for kind, val in (turb.get("event_mult") or {}).items():
            if kind in self._turb_event_mult:
                self._turb_event_mult[kind] = min(_TURB_EVENT_MULT_MAX[kind],
                                                  max(0.0, float(val)))

        self._reconcile_after_config_load(old_mode)

    def _reconcile_after_config_load(self, old_mode: str) -> None:
        """Re-apply the side effects _handle_fw_command/_handle_turb_command normally do.

        Needed because a config-file load can change many settings at once
        outside those per-field command handlers (at startup, or via the
        "Load from file" web button at runtime).
        """
        if self._turb_engine is not None:
            if self._turb_wind_mode == "manual":
                self._turb_engine.set_fixed_wind(
                    float(self._turb_manual_wind_dir), float(self._turb_manual_wind_spd))
            else:
                self._turb_engine.clear_fixed_wind()
                if self._turb_wind_mode == "psx" and self.psx_connected:
                    self._turb_update_psx_wind()
        if self.psx_connected and not self._own_sigmets_applied:
            self._write_sigmet_on(embed=self._sigmet_active() or not self._disable_psx_sigmets,
                                  trigger_download=False)
        if self._web_sigmetstate is not None:
            self._update_web_sigmetstate()  # SIGMET settings shown on the SIGMETs page
        if self._enroute_wind_enabled:
            self._enable_enroute_wind()
        else:
            self._restore_corridor_snapshot()
        # cb_airport_mode may have moved a dep/dst zone's place (no-op at startup)
        self._reposition_arpt_zones_for_cb_avoidance()
        if self.psx_connected and self._fw_mode != old_mode:
            if self._fw_mode == "disabled":
                self._inject_wx_slow_transit()
                self.psx_send_and_set("WxAutoSet", "1")
            elif old_mode == "disabled":
                self._inject_wx_slow_transit()
                self.psx_send_and_set("WxAutoSet", "0")
        if self._fw_mode == "manual" or old_mode == "manual":
            # See the matching comment in _handle_fw_command: force an
            # immediate zone update on either side of a manual-mode
            # transition, or stale manual weather (or stale real weather,
            # entering manual) can linger for up to _REFRESH_MAX_S.
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()
        self._apply_turb_profile_option()
        self._apply_turb_profile_bands()
        self._turb_state_changed_event.set()
        self._state_changed_event.set()
        self._enroute_wind_changed_event.set()

    def _apply_turb_profile_option(self) -> None:
        """Let --turb-profile override the profile from the config file (or the default).

        Applied at startup and whenever settings are (re)loaded or reset; a
        switch on the web UI or /efb still changes it for the session. The
        config file only changes if the settings are saved.
        """
        wanted = self.args.turb_profile if self.args else None
        if wanted and self._turb_profile != wanted:
            self.logger.info("Turbulence profile %s from --turb-profile (the settings say %s)",
                             wanted, self._turb_profile)
            self._turb_profile = wanted

    def _load_config_file(self) -> None:
        """Load settings from --config-file, if given and it exists; else keep defaults."""
        path = self.args.config_file
        if not path:
            return
        if not os.path.exists(path):
            self.logger.info(
                "Config file %s does not exist yet; using default settings "
                "(save from the web UI to create it)", path)
            return
        try:
            with open(path, "rb") as fh:
                config = tomllib.load(fh)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            self.logger.warning("Failed to load config file %s: %s", path, exc)
            return
        self._apply_config_dict(config)
        self.logger.info("Loaded settings from config file %s", path)

    def _save_config_file(self) -> bool:
        """Save current settings to --config-file. Returns True on success."""
        path = self.args.config_file
        if not path:
            self.logger.warning("Cannot save settings: no --config-file given at startup")
            return False
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(_dict_to_toml(self._build_config_dict()))
        except OSError as exc:
            self.logger.warning("Failed to save config file %s: %s", path, exc)
            return False
        self.logger.info("Saved settings to config file %s", path)
        return True

    def _turb_update_psx_wind(self) -> None:
        """Read the focused PSX weather zone and update the fixed wind profile."""
        if self._turb_engine is None:
            return
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
            return
        result = _parse_psx_wind(wx_str)
        if result is None:
            self.logger.warning("PSX wind: could not parse %s=%r", wx_var, wx_str)
            return
        dir_deg, speed_kt = result
        self.logger.info("Turb PSX wind: zone=%d %s → %03d°/%dkt",
                         zone, wx_var, dir_deg, speed_kt)
        self._turb_psx_wind = (dir_deg, speed_kt)
        self._turb_engine.set_fixed_wind(float(dir_deg), float(speed_kt))

    def _handle_turb_command(self, json_str: str) -> None:  # pylint: disable=too-many-branches,too-many-statements
        """Apply a FRANKENWEATHER:TURBCOMMAND message."""
        try:
            cmd = json.loads(json_str)
        except ValueError:
            self.logger.warning("Malformed TURBCOMMAND: %s", json_str[:80])
            return
        changed = False
        if "enabled" in cmd:
            self._turb_enabled = bool(cmd["enabled"])
            changed = True
        if "manual_turb_enabled" in cmd:
            self._turb_manual_turb_enabled = bool(cmd["manual_turb_enabled"])
            changed = True
        if "manual_turb_kind" in cmd:
            kind = str(cmd["manual_turb_kind"])
            if kind in _TURB_TYPES:
                self._turb_manual_turb_kind = kind
                changed = True
        if "manual_turb_intensity" in cmd:
            v = float(cmd["manual_turb_intensity"])
            if 0.0 <= v <= 1.0:
                self._turb_manual_turb_intensity = v
                changed = True
        if "intensity_bias" in cmd:
            v = int(cmd["intensity_bias"])
            if 0 <= v <= 999:
                self._turb_intensity_bias = v
                changed = True
        if "lateral_size_bias" in cmd:
            v = int(cmd["lateral_size_bias"])
            if 0 <= v <= 999:
                self._turb_lateral_size_bias = v
                changed = True
        if "wind_mode" in cmd:
            mode = str(cmd["wind_mode"])
            if mode in ("live", "psx", "manual"):
                self._turb_wind_mode = mode
                if self._turb_engine is not None:
                    if mode == "live":
                        self._turb_engine.clear_fixed_wind()
                    elif mode == "psx":
                        self._turb_engine.clear_fixed_wind()
                        if self.psx_connected:
                            self._turb_update_psx_wind()
                    elif mode == "manual":
                        self._turb_engine.set_fixed_wind(
                            float(self._turb_manual_wind_dir),
                            float(self._turb_manual_wind_spd))
                changed = True
        if "manual_wind_dir" in cmd:
            self._turb_manual_wind_dir = int(cmd["manual_wind_dir"]) % 360
            if self._turb_wind_mode == "manual" and self._turb_engine is not None:
                self._turb_engine.set_fixed_wind(
                    float(self._turb_manual_wind_dir),
                    float(self._turb_manual_wind_spd))
            changed = True
        if "manual_wind_spd" in cmd:
            v = int(cmd["manual_wind_spd"])
            if 0 <= v <= 300:
                self._turb_manual_wind_spd = v
                if self._turb_wind_mode == "manual" and self._turb_engine is not None:
                    self._turb_engine.set_fixed_wind(
                        float(self._turb_manual_wind_dir),
                        float(self._turb_manual_wind_spd))
                changed = True
        if "type_enabled" in cmd:
            for kind, val in cmd["type_enabled"].items():
                if kind in _TURB_TYPES:
                    self._turb_type_enabled[kind] = bool(val)
            changed = True
        if "type_bias" in cmd:
            kind = cmd["type_bias"].get("kind")
            val = cmd["type_bias"].get("value")
            if kind in _TURB_TYPES and val is not None:
                v = int(val)
                if 0 <= v <= 999:
                    self._turb_type_biases[kind] = v
                    changed = True
        if "msfs_turb_magnitude" in cmd:
            v = int(cmd["msfs_turb_magnitude"])
            if 0 <= v <= 200:
                self._turb_msfs_magnitude = v
                changed = True
        if cmd.get("profile") in _TURB_PROFILES:
            self._turb_profile = cmd["profile"]
            self.logger.info("Turbulence profile → %s", self._turb_profile)
            self._apply_turb_profile_bands()
            changed = True
        if isinstance(cmd.get("event_mult"), dict):
            for kind, val in cmd["event_mult"].items():
                if kind in self._turb_event_mult:
                    self._turb_event_mult[kind] = min(_TURB_EVENT_MULT_MAX[kind],
                                                      max(0.0, float(val)))
            changed = True
        if cmd.get("trigger") in _turb_events.KINDS:
            if self._turb_testing():
                event = self._turb_events.trigger(cmd["trigger"], self._turb_area[0])
                self._log_turb_event_start(event)
                changed = True
            else:
                self.logger.warning("Turbulence event trigger ignored: events need the "
                                    "testing turbulence profile")
        if changed:
            # Every change is logged: a pilot's report is checked against
            # the settings at the time
            self.logger.info("Turbulence settings changed: %s", json.dumps(cmd))
            self._turb_state_changed_event.set()

    def _get_nearest_cb(self, lat: float, lon: float):
        """Collect CB data from PSX cache and return the nearest active storm cell."""
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
                               lat_scale=self._turb_lateral_size_bias / 100.0)

    async def psx_wind_coro(self) -> None:
        """Refresh PSX weather-zone wind every 30 s when PSX wind mode is active."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(30.0)
                if self._turb_wind_mode == "psx" and self.psx_connected:
                    self._turb_update_psx_wind()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def turb_state_broadcast_coro(self) -> None:  # pylint: disable=too-many-locals
        """Broadcast TURBSTATE to the PSX network when turbulence state changes."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    await asyncio.wait_for(self._turb_state_changed_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
                self._turb_state_changed_event.clear()
                if self._conflict_paused():
                    continue
                state = self._turb_state
                sources = self._turb_sources
                if not math.isnan(getattr(state, 'source_lat', float('nan'))):
                    src_lat = state.source_lat
                    src_lon = state.source_lon
                else:
                    src_lat = None
                    src_lon = None
                payload = {
                    "enabled": self._turb_enabled,
                    "low_speed": self._turb_low_speed,
                    "manual_turb_enabled": self._turb_manual_turb_enabled,
                    "manual_turb_kind": self._turb_manual_turb_kind,
                    "manual_turb_intensity": self._turb_manual_turb_intensity,
                    "intensity_bias": self._turb_intensity_bias,
                    "profile": self._turb_profile,
                    "event_mult": dict(self._turb_event_mult),
                    "event_area": self._turb_area[0],
                    "event_area_reason": self._turb_area[1],
                    "event_active": (self._turb_events.current().kind
                                     if self._turb_events.current() else None),
                    "events": list(self._turb_event_log),
                    "lateral_size_bias": self._turb_lateral_size_bias,
                    "wind_mode": self._turb_wind_mode,
                    "manual_wind_dir": self._turb_manual_wind_dir,
                    "manual_wind_spd": self._turb_manual_wind_spd,
                    "type_enabled": dict(self._turb_type_enabled),
                    "type_biases": dict(self._turb_type_biases),
                    "active_kind": state.kind if state else "none",
                    "active_intensity": round(state.intensity, 3) if state else 0.0,
                    "effective_intensity": round(self._turb_effective_intensity, 3),
                    "active_reason": state.reason if state else "",
                    "intensity_ramp": self._turb_ramp_status(),
                    "pireps": self._pirep_levels,
                    "source_lat": src_lat,
                    "source_lon": src_lon,
                    "sources": [
                        {"kind": s.kind, "intensity": round(e, 3), "reason": s.reason}
                        for e, s in sources
                    ],
                    "msfs_active": (
                        self._msfs_bridge_last_seen is not None and
                        time.monotonic() - self._msfs_bridge_last_seen <= _MSFS_BRIDGE_TIMEOUT_S
                    ),
                    "msfs_last_seen_epoch": self._msfs_bridge_last_seen_epoch,
                    "msfs_in_cloud": self.msfs_in_cloud,
                    "msfs_cloud_density": self.msfs_cloud_density,
                    "msfs_wind_vert": self.msfs_wind_vert,
                    "msfs_precip_state": self.msfs_precip_state,
                    "msfs_turb_factor": round(self._turb_msfs_factor, 3),
                    "msfs_turb_magnitude": self._turb_msfs_magnitude,
                    "msfs_qnh_hpa": (round(self.msfs_qnh_hpa, 1)
                                     if self.msfs_qnh_hpa is not None else None),
                }
                self._web_turbstate = payload
                self._web_turbstate_received_at = time.time()
                msg = (f"addon=FRANKENWEATHER:TURBSTATE:{self._instance_uuid}:"
                       f"{json.dumps(payload)}")
                if self.psx_connected:
                    self._psx_send("addon", msg[len("addon="):])
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s", exc, myname)
            self.logger.critical(traceback.format_exc())

    def _turb_locked(self, name: str, func, *args):
        """Call a turbulence engine/fetcher function under its lock (see _turb_locks)."""
        with self._turb_locks[name]:
            return func(*args)

    def _pirep_physics(self, lat: float, lon: float, levels_ft: list) -> tuple:
        """Compute the physical turbulence sources at each level (PIREPs, executor thread).

        Returns (cape_sample, [(terrain_state, pirep_record, gairmet_region)]
        per level). The gustiness and SIGMET layers are applied by
        _pirep_rate() in the event loop thread.
        """
        cape_sample = self._turb_locked("cape", self._turb_cape_fetcher.get, lat, lon)
        per_level = [
            (self._turb_locked("engine", self._turb_engine.compute, lat, lon, level_ft),
             self._turb_locked("pirep", self._turb_pirep_fetcher.find_relevant,
                               lat, lon, level_ft),
             self._turb_locked("gairmet", self._turb_gairmet_fetcher.get_active,
                               lat, lon, level_ft))
            for level_ft in levels_ft]
        return cape_sample, per_level

    def _pirep_minimum(self, kind: str) -> Optional[float]:
        """Return the raw intensity of a kind that is _PIREP_MODERATE effective, or None.

        Effective means after the intensity biases from the turbulence
        page; None when the kind is switched off. In the testing profile
        _PIREP_LIGHT_CHOP instead (_PIREP_RIDES_TESTING).
        """
        bias = self._turb_intensity_bias * self._turb_type_effective_bias(kind) / 10000.0
        level = _PIREP_LIGHT_CHOP if self._turb_testing() else _PIREP_MODERATE
        return level / bias if bias > 0 else None

    def _pirep_share(self, state: Optional[TurbulenceState], level_ft: float) -> float:
        """Return the long-run share of time a source is moderate or worse at level_ft (PIREPs).

        A kind with gustiness by its family's multiplier at the layer's
        current roughness (not the moment's value); a kind without it is
        either always (1) or never (0) moderate or worse.
        """
        if state is None or state.intensity <= 0.0:
            return 0.0
        minimum = self._pirep_minimum(state.kind)
        if minimum is None:
            return 0.0
        family = self._gust_family(state.kind)
        if family is None:
            return 1.0 if state.intensity >= minimum else 0.0
        if minimum > state.max_intensity:   # capped below it (low-level wind shear)
            return 0.0
        return self._gust_profiles[family].share_at_least(level_ft, minimum / state.intensity)

    def _pirep_rate(self, lat: float, lon: float, fls: list, here: int,  # pylint: disable=too-many-locals
                    physics: tuple) -> list:
        """Return the PIREP ride report per flight level (pirep_levels_coro()).

        fls are flight levels in thousands of feet, here the current one.
        Each level's ride is rated by _PIREP_RIDES from the largest long-run
        share of time any source there is moderate or worse
        (_pirep_share(), _gairmet_share() and _sigmet_share_at()). In the
        testing profile by _PIREP_RIDES_TESTING from the share at light chop
        or more; moderate near a CB, rough inside its cloud.
        """
        testing = self._turb_testing()
        rides = _PIREP_RIDES_TESTING if testing else _PIREP_RIDES
        cape_sample, per_level = physics
        cb = self._get_nearest_cb(lat, lon)
        top_wind_dir = (self._turb_engine.wind_dir_at(cb.cloud_top_ft_msl)
                        if cb is not None and self._turb_engine is not None else None)
        reports = []
        for fl_k, (terrain, pirep_rec, gairmet) in zip(fls, per_level):
            level_ft = fl_k * 1000.0
            sources = [
                terrain,
                (compute_cb_turbulence(level_ft, cb, top_wind_dir)
                 if cb is not None else None),
                compute_cape_turbulence(level_ft, cape_sample) if cape_sample is not None else None,
            ]
            share = max(max(self._pirep_share(src, level_ft) for src in sources),
                        self._pirep_rec_share(pirep_rec, level_ft),
                        self._gairmet_share(gairmet, level_ft),
                        self._sigmet_share_at(level_ft))
            ride = next(label for limit, label in rides if share < limit)
            cb_here = sources[1]
            if testing and cb_here is not None:
                if (cb_here.reason or "").startswith("CB interior"):
                    ride = "rough"
                elif cb_here.intensity >= 0.2:
                    ride = "moderate"
            reports.append({"fl": fl_k * 10, "ride": ride, "share": round(share, 3),
                            "current": fl_k == here})
        return reports

    def _gairmet_share(self, region, level_ft: float) -> float:
        """Return the long-run share of time a G-AIRMET area is moderate or worse at level_ft.

        Like a TURB SIGMET (_gairmet_vary()): the layer at its current
        roughness, scaled by the area's intensity relative to SEV.
        """
        minimum = self._pirep_minimum("gairmet")
        if region is None or minimum is None:
            return 0.0
        scale = compute_gairmet_turbulence(level_ft, region).intensity / _GAIRMET_SEV_INTENSITY
        if scale <= 0.0:
            return 0.0
        return self._gairmet_turb_profile.share_at_least(level_ft, minimum / scale)

    async def pirep_levels_coro(self) -> None:
        """Keep the PIREP ride reports near the aircraft fresh (router /efb PIREP button).

        Every _PIREP_INTERVAL_S, and right away when the aircraft's flight
        level or the driving source changes (turbulence_coro() sets
        _pirep_refresh_event), or SIGMETs are taken over or handed back: whole
        flight levels up to _PIREP_RANGE_FT above and below the current
        one, FL010-FL470. The physical sources run in an executor thread
        (_pirep_physics()), the rating in the event loop (_pirep_rate()).
        A new set is broadcast with the next TURBSTATE.
        """
        myname = inspect.currentframe().f_code.co_name
        loop = asyncio.get_running_loop()
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    await asyncio.wait_for(self._pirep_refresh_event.wait(),
                                           timeout=_PIREP_INTERVAL_S)
                except asyncio.TimeoutError:
                    pass
                self._pirep_refresh_event.clear()
                if (self._turb_engine is None or self.ac_lat is None or
                        self.ac_lon is None or self.ac_alt_ft is None):
                    continue
                lat, lon, alt_ft = self.ac_lat, self.ac_lon, self.ac_alt_ft
                here = int(round(alt_ft / 1000.0))
                span = _PIREP_RANGE_FT // 1000
                fls = [k for k in range(here - span, here + span + 1) if 1 <= k <= 47]
                physics = await loop.run_in_executor(
                    None, self._pirep_physics, lat, lon, [k * 1000.0 for k in fls])
                self._pirep_levels = self._pirep_rate(lat, lon, fls, here, physics)
                self._turb_state_changed_event.set()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def turbulence_coro(self) -> None:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        """Compute turbulence, inject WxBurst into PSX, broadcast state changes."""
        myname = inspect.currentframe().f_code.co_name
        last_print = 0.0
        last_quiet_print = 0.0
        last_ramp_key = None
        last_pirep_fl = None
        last_here_fl = None
        last_driver_name = None
        # kind/intensity last announced, see state_changed below
        announced_kind, announced_intensity = None, 0.0
        try:
            self.logger.debug("Starting %s", myname)
            loop = asyncio.get_running_loop()
            while True:
                await asyncio.sleep(0.2)

                if not self.psx_connected or self.psx_paused:
                    continue
                if self._conflict_paused():
                    continue

                raw = self.psx.get("PiBaHeAlTas")
                if not raw:
                    continue
                try:
                    pba = parse_pibahealtas(raw)
                except ValueError as exc:
                    self.logger.warning("Bad PiBaHeAlTas: %s", exc)
                    continue
                _, _, _, alt_ft, tas_kt, lat, lon = pba

                low_speed = tas_kt < _TURB_MIN_TAS_KT
                if low_speed != self._turb_low_speed:
                    self._turb_low_speed = low_speed
                    self._turb_state_changed_event.set()

                if self._turb_engine is None:
                    continue

                if self._turb_manual_turb_enabled:
                    manual_state = TurbulenceState(
                        kind=self._turb_manual_turb_kind,
                        intensity=self._turb_manual_turb_intensity,
                        reason="Manual",
                    )
                    eff = self._turb_manual_turb_intensity
                    self._turb_effective_intensity = self._shown_intensity(eff)
                    # The normal path announces once manual is off again
                    announced_kind = None
                    # The turbulence on/off switch applies here too
                    if self._turb_enabled and not low_speed:
                        self._maybe_inject_burst(manual_state, eff, tas_kt)
                    state_changed = (
                        self._turb_state is None or
                        self._turb_state.kind != manual_state.kind or
                        abs((self._turb_state.intensity or 0.0) - manual_state.intensity) > 0.05
                    )
                    self._turb_state = manual_state
                    self._turb_sources = [(eff, manual_state)] if eff >= 0.01 else []
                    if state_changed:
                        self._turb_state_changed_event.set()
                    continue

                state, pirep_rec, cape_sample, gairmet_region = await asyncio.gather(
                    loop.run_in_executor(None, self._turb_locked, "engine",
                                         self._turb_engine.compute, lat, lon, alt_ft),
                    loop.run_in_executor(None, self._turb_locked, "pirep",
                                         self._turb_pirep_fetcher.find_relevant, lat, lon, alt_ft),
                    loop.run_in_executor(None, self._turb_locked, "cape",
                                         self._turb_cape_fetcher.get, lat, lon),
                    loop.run_in_executor(None, self._turb_locked, "gairmet",
                                         self._turb_gairmet_fetcher.get_active, lat, lon, alt_ft),
                )

                state = self._smooth_terrain(state, lat, lon)
                # Gustiness: layered multipliers on the physically computed
                # kinds (_apply_gust()); drivers maps each state to what
                # drives its intensity over time (_turb_ramp_status())
                state, terrain_driver = self._apply_gust(state, alt_ft)
                terrain_state = state
                drivers = {id(terrain_state): terrain_driver}

                def _eff(s):
                    return s.intensity * self._turb_type_effective_bias(s.kind)

                cb = self._get_nearest_cb(lat, lon)
                cb_state = None
                if cb is not None:
                    cb_state = compute_cb_turbulence(
                        alt_ft, cb, self._turb_engine.wind_dir_at(cb.cloud_top_ft_msl))
                    if _eff(cb_state) > _eff(state):
                        state = cb_state

                pirep_state = None
                if pirep_rec is not None:
                    pirep_state, drivers_pirep = self._pirep_vary(
                        compute_pirep_turbulence(alt_ft, pirep_rec), alt_ft)
                    drivers[id(pirep_state)] = drivers_pirep
                    if _eff(pirep_state) > _eff(state):
                        state = pirep_state

                cape_state = None
                if cape_sample is not None:
                    cape_state, drivers_cape = self._apply_gust(
                        compute_cape_turbulence(alt_ft, cape_sample), alt_ft)
                    drivers[id(cape_state)] = drivers_cape
                    if _eff(cape_state) > _eff(state):
                        state = cape_state

                # Testing profile: the light bumps of an ordinary breezy or
                # sunny day (frankenturb/boundary_layer.py)
                bl_state = None
                if self._turb_testing() and cape_sample is not None:
                    terrain_m = self._turb_engine.grid.elevation_at(lat, lon) or 0.0
                    bl_state, drivers_bl = self._apply_gust(compute_boundary_layer_turbulence(
                        max(0.0, alt_ft - terrain_m / 0.3048), cape_sample), alt_ft)
                    drivers[id(bl_state)] = drivers_bl
                    if _eff(bl_state) > _eff(state):
                        state = bl_state

                gairmet_state = None
                if gairmet_region is not None:
                    gairmet_state, drivers_gairmet = self._gairmet_vary(
                        compute_gairmet_turbulence(alt_ft, gairmet_region), alt_ft)
                    drivers[id(gairmet_state)] = drivers_gairmet
                    if _eff(gairmet_state) > _eff(state):
                        state = gairmet_state

                sigmet_state, mtw_state = self._sigmet_turbulence(
                    lat, lon, alt_ft, terrain_state)
                for sig_s in (mtw_state, sigmet_state):
                    if sig_s is not None and _eff(sig_s) > _eff(state):
                        state = sig_s
                conv_state = None
                conv = self._sigmet_convective_turbulence(lat, lon, alt_ft)
                if conv is not None:
                    conv_state = conv[0]
                    drivers[id(conv_state)] = ("TS SIGMET", self._sigmet_conv_profile,
                                               conv[1], conv[2])
                    if _eff(conv_state) > _eff(state):
                        state = conv_state
                # What drives the chosen state's intensity over time (an
                # MTW state only exists when it outweighs the terrain
                # model's own wave, see _sigmet_turbulence())
                for sig_s, hazard in ((sigmet_state, "TURB"), (mtw_state, "MTW")):
                    if sig_s is not None:
                        drivers[id(sig_s)] = (f"{hazard} SIGMET",
                                              self._sigmet_turb_profiles[hazard],
                                              self._sigmet_turb_scale.get(hazard, 1.0))
                self._turb_driver = drivers.get(id(state))
                self._turb_driver_alt_ft = alt_ft
                # Announced when a new ramp starts, when it ends and on a
                # change of altitude layer
                ramp_key = None
                if self._turb_driver is not None:
                    ramp_st = self._turb_driver[1].status(alt_ft)
                    ramp_key = (self._turb_driver[0], ramp_st["layer"],
                                ramp_st["segment"], ramp_st["ramp_left_s"] > 0)
                # PIREPs (/efb) are for the current flight level: a fresh
                # set on a change (pirep_levels_coro()), announced when ready
                here_fl = int(round(alt_ft / 1000.0))
                driver_name = self._turb_driver[0] if self._turb_driver else None
                if here_fl != last_here_fl or driver_name != last_driver_name:
                    # Also when the driving source changes, e.g. entering or
                    # leaving a turbulence area
                    last_here_fl, last_driver_name = here_fl, driver_name
                    self._pirep_refresh_event.set()
                pirep_fl = next((r["fl"] for r in self._pirep_levels or ()
                                 if r["current"]), None)

                msfs_factor = self._compute_msfs_turb_factor()
                self._turb_msfs_factor = msfs_factor
                effective_intensity = min(
                    1.0,
                    state.intensity * self._turb_intensity_bias *
                    self._turb_type_effective_bias(state.kind) / 10000.0 * msfs_factor,
                )
                if self._turb_testing():
                    self._turb_area = self._turb_area_class({
                        "terrain": terrain_state, "cb": cb_state, "pirep": pirep_state,
                        "gairmet": gairmet_state, "sigmet": sigmet_state, "mtw": mtw_state,
                        "conv": conv_state})
                    state, effective_intensity = self._turb_event_step(
                        state, effective_intensity,
                        self._turb_enabled and not low_speed and not self._on_ground())
                self._turb_effective_intensity = self._shown_intensity(effective_intensity)
                if self._turb_enabled and not low_speed and not self._turb_event_burst_sent:
                    # like events, a CB core isn't up/down limited (testing profile)
                    limit = self._turb_testing() and state.kind != "event"
                    self._maybe_inject_burst(
                        state, effective_intensity, tas_kt,
                        limit_updown=limit and self._turb_area[0] != "cb_core")
                self._log_burst_summary()
                self._log_flight_status(pba, effective_intensity, state)

                all_sources = []
                for src_s in [terrain_state, cb_state, pirep_state, cape_state, bl_state,
                              gairmet_state, sigmet_state, mtw_state, conv_state]:
                    if src_s is None or src_s.intensity < 0.01:
                        continue
                    src_eff = min(1.0, src_s.intensity * self._turb_intensity_bias *
                                  self._turb_type_effective_bias(src_s.kind) / 10000.0 *
                                  msfs_factor)
                    if src_eff < 0.01:
                        continue
                    all_sources.append((src_eff, src_s))
                all_sources.sort(key=lambda t: t[0], reverse=True)

                # Compared with what was last announced, not the previous
                # tick: a gradual change (e.g. a SIGMET intensity ramp) is
                # far less than 0.05 per tick but must still be announced.
                # The UIs show the effective intensity (biases, MSFS factor,
                # ground cap), which can change without the computed one
                state_changed = (
                    ramp_key != last_ramp_key or  # /efb shows the SIGMET ramp
                    pirep_fl != last_pirep_fl or
                    announced_kind != state.kind or
                    abs(announced_intensity - self._turb_effective_intensity) > 0.05
                )
                self._turb_state = state
                self._turb_sources = all_sources
                # Each new ramp or altitude layer is logged right away (debugging)
                if ramp_key is not None and ramp_key[:3] != (last_ramp_key or ())[:3]:
                    self.logger.info("Turbulence intensity (%s): %s", ramp_key[0],
                                     _fw_sigmet.ramp_text(self._turb_ramp_status()))
                last_ramp_key = ramp_key
                last_pirep_fl = pirep_fl
                if state_changed:
                    announced_kind, announced_intensity = state.kind, self._turb_effective_intensity
                    self._turb_state_changed_event.set()

                now = time.monotonic()
                if effective_intensity < 0.01:
                    last_print = 0.0
                    if now - last_quiet_print >= 60.0:
                        last_quiet_print = now
                        self.logger.info(
                            "Turbulence [%s] lat=%.3f lon=%.3f alt=%.0fft | none",
                            "ON " if self._turb_enabled else "OFF", lat, lon, alt_ft,
                        )
                    continue
                if now - last_print < 10.0:
                    continue
                last_print = now

                intensity_label = _intensity_label(effective_intensity, self._turb_testing())
                enabled_str = "ON " if self._turb_enabled else "OFF"
                kind_str = state.kind
                vert_str = f"{state.vertical:+.2f}" if not _isnan(state.vertical) else "rand"
                roll_str = f"{state.roll:+.2f}" if not _isnan(state.roll) else "rand"
                gust_str = f"{state.gust:+.2f}" if not _isnan(state.gust) else "rand"

                if self._turb_print_count % 20 == 0:
                    self.logger.info("--- Turbulence %s", "-" * 73)
                    self.logger.info(
                        "     [   ] lat(°)   lon(°)   alt(ft)  kind        "
                        "label      (0-1)  vert  roll  gust")
                    self.logger.info("--- Turbulence %s", "-" * 73)
                self._turb_print_count += 1

                self.logger.info(
                    "Turbulence [%s] lat=%.3f lon=%.3f alt=%.0fft | "
                    "%-10s | %-8s (%.2f) | vert=%s roll=%s gust=%s",
                    enabled_str, lat, lon, alt_ft,
                    kind_str, intensity_label, effective_intensity,
                    vert_str, roll_str, gust_str,
                )
                for src_eff, src in all_sources:
                    marker = ">" if (src is state) else " "
                    self.logger.info("           [%s%-10s] %.2f %s",
                                     marker, src.kind, src_eff, src.reason or "")
                    if src.kind == 'cb' and cb is not None:
                        self.logger.info(
                            "           [  cb-geo   ] %s brg=%03.0f° rng=%.0fnm "
                            "base=%.0fft top=%.0fft cov=%d",
                            cb.source, cb.bearing_deg, cb.range_center_nm,
                            cb.cloud_base_ft_msl, cb.cloud_top_ft_msl, cb.coverage)
                    if src is state and self._turb_driver is not None:
                        self.logger.info("           [  ramp     ] %s: %s", self._turb_driver[0],
                                         _fw_sigmet.ramp_text(self._turb_ramp_status()))

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s", exc, myname)
            self.logger.critical(traceback.format_exc())

    def _handle_corridor(self, _key: str, value: str) -> None:
        """Cache the current PSX wind corridor text, and capture flight-plan snapshots.

        Snapshot capture always runs — independent of whether the enroute
        wind importer is actively enabled — so the flight-plan-vs-Open-Meteo
        comparison is available as soon as a route is loaded, even before the
        user opts in to letting FrankenWeather write the corridor. Only the
        active Open-Meteo fetch/write (enroute_wind_coro) requires opt-in.

        A change is "ours" iff its value exactly matches the last corridor we
        sent (from the enroute wind importer) — compared by
        value, not by a time window, since PSX can re-broadcast an unchanged
        variable well after we sent it (e.g. a periodic full resync), and a
        short cooldown would then misread our own old write as a fresh
        flight-plan load. Anything else is treated as external (a situ load
        or flight-plan import) and captured as the flight-plan snapshot.
        """
        if value == self._corridor_last_own_value:
            return
        self.logger.info(
            "Enroute wind: WxCorridor changed externally — capturing flight-plan snapshot")
        self._corridor_snapshot_txt = value
        self._corridor_snapshot_waypoints = wind_corridor.extract_waypoint_winds(value)
        self._corridor_snapshot_coords = wind_corridor.extract_waypoint_coords(value)
        self._waypoint_passed = set()
        self._waypoint_om_wind = {}
        self._enroute_next_fetch_time = 0.0
        # PSX no longer has our corridor: send the next one even if it's
        # the same as our last (it used to stay the situ's until the wind
        # data changed)
        self._enroute_last_corridor_txt = None
        self._enroute_wind_changed_event.set()

    def _restore_corridor_snapshot(self) -> None:
        """Send the captured flight-plan wind corridor back to PSX.

        Called whenever the enroute wind importer turns off, so PSX reverts to the
        original flight-plan data instead of being left with our last
        generated corridor. The restored value is remembered as our own
        write so the echoed change isn't mistaken for a fresh external load.
        """
        if not self._corridor_snapshot_txt:
            return
        self._corridor_last_own_value = self._corridor_snapshot_txt
        self._inject_wx_slow_transit()
        self.psx_send_and_set("WxCorridorTxt", self._corridor_snapshot_txt)
        self.logger.info("Enroute wind: restored original flight-plan wind corridor")

    def _enable_enroute_wind(self) -> None:
        """Apply the enable/re-enable side effects for the enroute wind importer.

        Forces a fresh Open-Meteo fetch on the next cycle, and — by clearing
        _enroute_last_corridor_txt — forces _apply_enroute_wind_injection to
        actually resend the corridor even if the freshly built one happens to
        match a value cached from before enabling. That cache can be stale
        proof of nothing: while disabled, PSX was showing the restored
        flight-plan snapshot instead (or, on startup/config load, nothing has
        been sent at all yet), so a dedup match against it must never be
        allowed to suppress the resend.
        """
        self._enroute_next_fetch_time = 0.0
        self._enroute_last_corridor_txt = None
        self._apply_enroute_wind_qs497()

    def _apply_enroute_wind_qs497(self) -> None:
        """Ensure Qs497 tells PSX to use the wind corridor, with our chosen deviation level.

        Qs497 (WxCorridorSel) holds the Corridor page's selections, one digit
        each (Aerowinx): the first is 2 for "embedded" (PSX uses the corridor
        data; 1 = not embedded), the second the Random slider position (the
        random wind/OAT deviation PSX applies on top, simulating forecast
        inaccuracy; 0 = none), the third the Format menu selection (irrelevant
        for an injected corridor, sent as 0). The deviation setting is kept as
        0-80 in steps of 10, so its tens digit is the slider position. Checked
        on every corridor refresh, but only sent when PSX's value differs:
        WxCorridorSel is subscribed, so psx.get() follows our own writes, the
        Corridor page and situ loads alike. It used to go out on every
        refresh (psx.get() of an unsubscribed key is always ""), and a
        rewrite may make PSX pick new random deviations.
        """
        if not self.psx_connected:
            return
        value = f"2{self._enroute_wind_deviation:02d}"
        if self._psx_has("WxCorridorSel", self.psx.get("WxCorridorSel"), value):
            return
        self._inject_wx_slow_transit()
        self.psx_send_and_set("WxCorridorSel", value)

    def _apply_enroute_wind_injection(self) -> None:
        """Build a fresh Format-A corridor from route_waypoints + fetched OM wind.

        route_waypoints (PSX's live, front-trimmed list) is always a tail of
        _enroute_waypoints (our persistent superset), so live index i maps to
        persistent index offset+i — that's how _waypoint_om_wind (keyed by
        persistent index) is looked up here.

        Only actually sent to PSX when the built corridor differs from the
        last one we sent: PSX recalculates on every WxCorridorTxt receipt, so
        resending identical wind data (e.g. an hourly refetch that happens to
        return the same forecast) would just cost PSX work for no benefit.
        """
        if not self.route_waypoints:
            return
        offset = len(self._enroute_waypoints) - len(self.route_waypoints)
        wind_by_live_index = {
            i: self._waypoint_om_wind.get(offset + i, {})
            for i in range(len(self.route_waypoints))
        }
        new_corridor = wind_corridor.build_corridor_a(
            self.route_waypoints, self._current_fl_list_ft(), wind_by_live_index)
        if new_corridor != self._enroute_last_corridor_txt:
            self._corridor_last_own_value = new_corridor
            self._inject_wx_slow_transit()
            self.psx_send_and_set("WxCorridorTxt", new_corridor)
            self._enroute_last_corridor_txt = new_corridor
            self.logger.info(
                "Enroute wind: refreshed WxCorridor for %d waypoint(s) (%d passed)",
                len(self.route_waypoints), len(self._waypoint_passed))
            # Notify other addons that we actually wrote a new corridor to PSX --
            # not on every hourly poll, only when the dedup check above let a
            # genuinely different corridor through (new wind data, or a reroute).
            self._psx_send("addon", f"FRANKENWEATHER:CORRIDOR_CHANGED:{self._instance_uuid}")
        else:
            self.logger.debug("Enroute wind: corridor unchanged, not resending to PSX")
        self._apply_enroute_wind_qs497()
        self._save_enroute_wind_log()

    def _sigmet_cb_override(self, wx_str: str, pos: tuple,
                            om: dict, zone_label: str) -> str:
        """Restore CAPE-suppressed CBs if the zone lies inside a TS SIGMET polygon.

        TS SIGMETs are observational — controllers report actual thunderstorms.
        Trust the SIGMET even when CAPE is zero; minimum 4 oktas when CAPE
        doesn't contribute more.
        """
        if not self.ts_sigmets:
            return wx_str
        fields = wx_str.split(';')
        if fields[9] != '0':
            return wx_str  # CBs already present — no override needed
        for sig in self.ts_sigmets:
            if not _point_in_polygon(pos[0], pos[1], sig['polygon']):
                continue
            hourly = om.get("hourly", {})
            hi = datetime.now(timezone.utc).hour
            cape = _om_hval(hourly, "cape", hi, 0)
            cin = _om_hval(hourly, "convective_inhibition", hi, 0)
            h_temp = _om_hval(hourly, "temperature_2m", hi, 15.0)
            h_dp = _om_hval(hourly, "dewpoint_2m", hi, 10.0)
            cb_oktas = max(_cape_to_cb_oktas(cape, cin), 4)
            cb_tops = min(max(_cb_tops_ft(cape, pos[0], datetime.now(timezone.utc).month),
                              sig['top_ft']), _CB_TOP_MAX_FT)
            fields[9] = str(cb_oktas)
            fields[10] = str(cb_tops)
            fields[11] = str(_cb_base_ft(h_temp, h_dp))
            self.logger.info("%s: CB suppression lifted by TS SIGMET (%d oktas top=%dft)",
                             zone_label, cb_oktas, cb_tops)
            return ';'.join(fields)
        return wx_str

    def _own_sigmet_cb(self, wx_str: str, pos: tuple, om: dict, zone_label: str) -> str:
        """Return wx_str with the CB a zone inside a TS/TC SIGMET area gets (--sigmet).

        Coverage from the SIGMET's qualifier (see _sigmet_cb_at()), top
        from the SIGMET, base from the zone's own surface temperature/dew
        point. A CB the zone already has from Open-Meteo/METAR (CAPE) can
        only make it bigger. Zones outside every TS/TC area are returned
        unchanged.
        """
        best = self._sigmet_cb_at(pos)
        if best is None:
            return wx_str
        oktas, sig = best
        fields = wx_str.split(';')
        have_oktas = int(fields[9]) if fields[9].isdigit() else 0
        hourly = om.get("hourly", {})
        hi = datetime.now(timezone.utc).hour
        if have_oktas > 0:
            base = int(fields[11])
            top = max(int(fields[10]), sig['top_ft'])
        else:
            base = _cb_base_ft(_om_hval(hourly, "temperature_2m", hi, 15.0),
                               _om_hval(hourly, "dewpoint_2m", hi, 10.0))
            top = max(_cb_tops_ft(_om_hval(hourly, "cape", hi, 0), pos[0],
                                  datetime.now(timezone.utc).month), sig['top_ft'])
        fields[9] = str(max(oktas, have_oktas))
        fields[10] = str(min(top, _CB_TOP_MAX_FT))
        fields[11] = str(base)
        new_wx = ';'.join(fields)
        if new_wx != wx_str:
            self.logger.info("%s: CB from %s SIGMET %s (%s oktas, %s-%sft)", zone_label,
                             sig['hazard'], sig.get('qualifier') or '-', fields[9],
                             fields[11], fields[10])
        return new_wx

    def _sigmet_turbulence(self, lat: float, lon: float, alt_ft: float,
                           terrain_state: TurbulenceState) -> tuple:
        """Return (turb_state, mtw_state) for the TURB/MTW SIGMETs at the aircraft (--sigmet).

        Either is None when not applicable. Only while we own the SIGMETs:
        PSX then doesn't get TURB/MTW, so frankenweather generates their
        turbulence itself. The intensity varies over time and altitude (one
        LayeredIntensityProfile per type), scaled per
        _SIGMET_TURB_QUALIFIER_SCALE,
        faded in near the area's edges and its base/top; the strongest
        SIGMET of each kind wins. A TURB SIGMET gives "sigmet" (random) turbulence.
        An MTW SIGMET strengthens the terrain model's own mountain wave or
        rotor to at least its intensity, keeping the terrain model's
        up/down pattern; where the terrain model finds none, it gives wave
        turbulence of its own.
        """
        if not self._own_sigmets_applied:
            self._sigmet_turb_areas = []
            return None, None
        best = {}
        areas = []  # (hazard, lateral factor, entry) of the areas at lat/lon, for PIREPs
        self._sigmet_turb_alt_ft = alt_ft
        levels = {h: p.value(alt_ft) for h, p in self._sigmet_turb_profiles.items()}
        self._sigmet_turb_scale = {}
        for entry in self._own_sigmet_entries:
            if not entry["ok"] or entry["hazard"] not in _fw_sigmet.TURB_HAZARDS:
                continue
            if not self._sigmet_handle.get(entry["hazard"]):
                continue  # PSX gets this type (SIGMETs page)
            lateral = self._sigmet_turb_lateral(lat, lon, entry)
            if lateral <= 0.0:
                continue
            areas.append((entry["hazard"], lateral, entry))
            factor = lateral * self._sigmet_turb_vertical(alt_ft, entry)
            if factor <= 0.0:
                continue
            intensity = (factor * levels[entry["hazard"]] *
                         _SIGMET_TURB_QUALIFIER_SCALE.get(entry["qualifier"], 1.0))
            if intensity > best.get(entry["hazard"], (0.0, None))[0]:
                best[entry["hazard"]] = (intensity, entry)
                self._sigmet_turb_scale[entry["hazard"]] = (
                    intensity / levels[entry["hazard"]] if levels[entry["hazard"]] else 0.0)
        self._sigmet_turb_areas = areas
        turb_state = mtw_state = None
        if "TURB" in best:
            intensity, entry = best["TURB"]
            turb_state = TurbulenceState(
                intensity=intensity, kind="sigmet",
                reason=f"{entry['qualifier']} TURB SIGMET {entry['fir']} {entry['series']}")
        if "MTW" in best:
            intensity, entry = best["MTW"]
            mtw_state = self._mtw_state(
                terrain_state, intensity,
                f"{entry['qualifier']} MTW SIGMET {entry['fir']} {entry['series']}")
        return turb_state, mtw_state

    def _pick_wave_burst_testing(self, state: TurbulenceState,
                                 intensity: float) -> Optional[tuple]:
        """Return a wave/MTW burst like _pick_burst(), or None to skip it (testing profile).

        _pick_burst() gives sink bursts in the direction of the wave's
        phase and speed/gust bursts in the direction of its gust, so they
        push the same way for a minute at a time: in a SEV MTW SIGMET's
        61 % that drove FLCH into a ~40 s pitch oscillation with descents
        to 5,000+ ft/min (EGPF, 2026-10-25). Here sink bursts follow the
        change of the phase (the draft getting stronger or weaker), and
        only as often as it changes (_WAVE_PHASE_RATE_FULL), so the push is
        bounded. Gust bursts become speed bursts (GUST yaws the aircraft)
        and bank/yaw bursts are dropped; speed bursts go either way, and
        only _WAVE_SPD_KEEP of them are kept -- below _WAVE_LIGHT_BELOW,
        where a wave gets nothing else, the others become up/down bursts.
        """
        now = time.monotonic()
        prev, self._wave_vert_prev = self._wave_vert_prev, (now, state.vertical)
        base, _, label = _pick_burst(state, intensity)
        direction = random.choice([-1, 1])
        if base == _BURST_GUST:
            base, label = _BURST_SPD, "spd"
        elif base in (_BURST_BANK, _BURST_YAW):
            return None
        if base == _BURST_SPD and random.random() >= _WAVE_SPD_KEEP:
            if intensity >= _WAVE_LIGHT_BELOW:
                return None
            base, label = _BURST_SINK, "sink"
        if base != _BURST_SINK or _isnan(state.vertical):
            return base, direction, label
        if prev is None or _isnan(prev[1]) or now - prev[0] <= 0.0 or now - prev[0] > 5.0:
            return None
        rate = (state.vertical - prev[1]) / (now - prev[0])
        if rate == 0.0 or random.random() >= min(1.0, abs(rate) / _WAVE_PHASE_RATE_FULL):
            return None
        return base, _sign(rate), label

    def _updown_budget(self, direction: int, magnitude: int, intensity: float) -> int:
        """Return how much of an up/down burst to send (testing profile; 0: none).

        Bursts one way within _UPDOWN_WINDOW_S add up to at most the
        largest single burst at this intensity, so a run of them can't
        build up the autopilot's pitch oscillation.
        """
        now = time.monotonic()
        while self._updown_bursts and now - self._updown_bursts[0][0] > _UPDOWN_WINDOW_S:
            self._updown_bursts.popleft()
        used = sum(size for _, d, size in self._updown_bursts if d == direction)
        magnitude = min(magnitude, max(1, int(intensity * 99)) - used)
        if magnitude >= 1:
            self._updown_bursts.append((now, direction, magnitude))
        return magnitude

    def _in_cb_sigmet_area(self, lat: float, lon: float) -> bool:
        """Return True if lat/lon is in a TS/TC SIGMET area frankenweather handles (--sigmet)."""
        if lat is None or lon is None or not self._own_sigmets_applied:
            return False
        for entry in self._own_sigmet_entries:
            if not (entry["ok"] and entry["hazard"] in _fw_sigmet.CB_HAZARDS and
                    self._sigmet_handle.get(entry["hazard"])):
                continue
            if any(_fw_sigmet.inside_depth(lat, lon, poly) is not None
                   for poly in entry["polygons"]):
                return True
        return False

    def _sigmet_convective_turbulence(self, lat: float, lon: float,
                                      alt_ft: float) -> Optional[tuple]:
        """Return (state, scale, floor) for the TS/TC SIGMET area here, or None (testing).

        Only in the testing profile, while frankenweather handles the TS/TC
        SIGMETs (--sigmet): their CBs are in at most a few zones, whose CB
        turbulence reaches only half way to the next zone, so flying
        through a big FRQ TS area was mostly smooth. Inside the area,
        below its top, faded in near its edges like a TURB SIGMET, a
        floor plus a varying level (_sigmet_conv_profile) scaled, both by
        the SIGMET's wording (_SIGMET_CONV, TC: _SIGMET_CONV_TC); the
        strongest area wins; scale and floor are its edge-faded ones (for
        _turb_ramp_status()). Kind
        "sigmet": the SIGMET turbulence settings apply. The zones' own CB
        turbulence still applies where it's stronger.
        """
        if not (self._turb_testing() and self._own_sigmets_applied):
            return None
        level = self._sigmet_conv_profile.value(alt_ft)
        best, entry, scale, best_floor = 0.0, None, 0.0, 0.0
        for candidate in self._own_sigmet_entries:
            if (not candidate["ok"] or candidate["hazard"] not in _fw_sigmet.CB_HAZARDS or
                    not self._sigmet_handle.get(candidate["hazard"])):
                continue
            factor = (self._sigmet_turb_lateral(lat, lon, candidate) *
                      self._sigmet_turb_vertical(alt_ft, candidate))
            floor, cand_scale = (_SIGMET_CONV_TC if candidate["hazard"] == "TC" else
                                 _SIGMET_CONV.get(candidate["qualifier"], _SIGMET_CONV_DEFAULT))
            intensity = factor * (floor + cand_scale * level)
            if intensity > best:
                best, entry, scale, best_floor = (intensity, candidate, factor * cand_scale,
                                                  factor * floor)
        if entry is None:
            return None
        label = f"{entry['qualifier']} {entry['hazard']}".strip()
        return (TurbulenceState(
            intensity=best, kind="sigmet",
            reason=f"{label} SIGMET {entry['fir']} {entry['series']} (convective, testing)"),
            scale, best_floor)

    def _mtw_state(self, terrain_state: TurbulenceState, intensity: float,
                   label: str) -> Optional[TurbulenceState]:
        """Return an MTW SIGMET's turbulence, or None if the terrain's own outweighs it.

        Kind "mtw": wave-like bursts, but the SIGMET settings apply. Over the
        terrain model's own wave/rotor the stronger of the two (after their
        settings) wins and keeps its own kind and settings: taking the
        terrain's turbulence over as "mtw" made the SIGMET settings control
        it, and gave a rotor wave bursts.
        """
        if terrain_state.kind not in ("wave", "rotor"):
            return TurbulenceState(intensity=intensity, kind="mtw",
                                   reason=f"{label} (no terrain wave found)")
        if self._turb_testing():
            # A SEV MTW SIGMET lifted a 2-4 % terrain wave to 61 % (EGPF, 2026-10-25)
            intensity = min(intensity,
                            terrain_state.intensity * _MTW_LIFT_FACTOR + _MTW_LIFT_ADD)
        if (terrain_state.intensity * self._turb_type_effective_bias(terrain_state.kind) >=
                intensity * self._turb_type_effective_bias("mtw")):
            return None
        return dataclasses.replace(terrain_state, intensity=intensity, kind="mtw",
                                   reason=f"{label} (over {terrain_state.reason})")

    def _turb_ramp_status(self) -> Optional[dict]:
        """Return the current ramp of what drives the turbulence's intensity, or None.

        For TURBSTATE "intensity_ramp" and the console (fw_sigmet.ramp_text()).
        Levels are scaled like the injected intensity (a SIGMET's edge fade
        and qualifier, or the physical intensity a gustiness multiplies),
        so they match active_intensity; a driver's optional fourth element
        is a floor added to them (a TS/TC SIGMET area's), so the levels
        shown are the whole intensity, not just its varying part.
        ramp_end/hold_end are epoch times,
        so the router can count down between TURBSTATE messages.
        """
        if self._turb_driver is None:
            return None
        name, profile, scale = self._turb_driver[:3]
        floor = self._turb_driver[3] if len(self._turb_driver) > 3 else 0.0
        st = profile.status(self._turb_driver_alt_ft)
        now = time.time()
        return {
            "source": name,
            "layer_base_ft": st["layer_base_ft"],
            "layer_top_ft": st["layer_top_ft"],
            "from": round(min(1.0, floor + st["from"] * scale), 3),
            "target": round(min(1.0, floor + st["target"] * scale), 3),
            "ramp_s": round(st["ramp_s"]),
            "hold_s": round(st["hold_s"]),
            "abrupt": st["abrupt"],
            # ramping: whether the ramp is still going at the time of this
            # status; the end times are not rounded up past the real ones
            "ramping": st["ramp_left_s"] > 0,
            "ramp_end": math.floor((now + st["ramp_left_s"]) * 10) / 10,
            "hold_end": math.floor((now + st["ramp_left_s"] + st["hold_left_s"]) * 10) / 10,
        }

    def _turb_testing(self) -> bool:
        """Return True if the "testing" turbulence profile is on (see _TURB_PROFILES)."""
        return self._turb_profile == "testing"

    def _apply_turb_profile_bands(self) -> None:
        """Give the varying SIGMET/G-AIRMET/PIREP levels the profile's bands.

        Testing: smooth and light only (REALISTIC_BANDS; TURB SIGMETs mostly
        light chop, REALISTIC_SEV_BANDS); moderate and stronger come as
        events (_turb_event_step()).
        """
        testing = self._turb_testing()
        for hazard, profile in self._sigmet_turb_profiles.items():
            profile.set_bands(DEFAULT_BANDS if not testing else
                              REALISTIC_SEV_BANDS if hazard == "TURB" else REALISTIC_BANDS)
        for profile in (self._sigmet_conv_profile, self._gairmet_turb_profile,
                        self._pirep_turb_profile):
            profile.set_bands(REALISTIC_BANDS if testing else DEFAULT_BANDS)

    def _turb_area_class(self, sources: dict) -> tuple:
        """Return (area, reason): the kind of area (_turb_events.AREA_RATES) the aircraft is in.

        sources maps a source name to its TurbulenceState or None; of the
        sources present (switched on, at least 5 %) the one with the
        highest severe-event rate wins.
        """
        best = ("clean", "", _turb_events.AREA_RATES["clean"][1])

        def consider(area, src):
            nonlocal best
            if (src is None or src.intensity * self._turb_type_effective_bias(src.kind) < 5.0):
                return
            if _turb_events.AREA_RATES[area][1] > best[2]:
                best = (area, src.reason or src.kind, _turb_events.AREA_RATES[area][1])

        # Inside the CB's cloud is "in a CB", whatever its level: CB interior
        # turbulence is 0.38-0.66, and a 0.6 threshold left the aircraft
        # "near a CB" most of the time it was inside one (thunderstorm
        # transits 2026-10-26, PAIB 17-14)
        cb = sources.get("cb")
        if cb is not None:
            consider("cb_core" if (cb.reason or "").startswith("CB interior")
                     else "cb_near" if cb.intensity >= 0.2 else "clean", cb)
        consider("cb_near", sources.get("conv"))
        sigmet = sources.get("sigmet")
        if sigmet is not None:
            consider("sev_sigmet" if sigmet.reason.startswith("SEV") else "advisory", sigmet)
        consider("mtw", sources.get("mtw"))
        pirep = sources.get("pirep")
        if pirep is not None:
            consider("sev_sigmet" if "SEV" in pirep.reason else "advisory", pirep)
        consider("advisory", sources.get("gairmet"))
        terrain = sources.get("terrain")
        if terrain is not None and (terrain.kind == "rotor" or
                                    (terrain.kind == "mechanical" and terrain.intensity >= 0.3)):
            consider("bl_strong", terrain)
        return best[0], best[1]

    def _turb_event_step(self, state: TurbulenceState, intensity: float,
                         airborne: bool) -> tuple:
        """Return (state, intensity) to inject after the events (testing profile).

        The continuous turbulence is capped at _TURB_BG_MAX, inside a CB's
        cloud held within _TURB_CB_CORE instead (and its bursts aren't
        up/down limited, see turbulence_coro()). Maybe starts an
        event for this tick's area (_turb_area), sends a running event's
        scripted bursts and raises the level to the event's (with random
        "event" bursts) while it lasts. Severe and extreme bursts are sent
        as they are: their effect on the aircraft is meant to be real.
        """
        if self._turb_area[0] == "cb_core":
            # scaled like the CB turbulence itself by the turbulence page's sliders
            scale = self._turb_intensity_bias * self._turb_type_effective_bias("cb") / 10000.0
            intensity = min(max(intensity, _TURB_CB_CORE[0] * scale), _TURB_CB_CORE[1] * scale)
        else:
            intensity = min(intensity, _TURB_BG_MAX)
        self._turb_event_burst_sent = False
        if not airborne:
            return state, intensity
        area = self._turb_area[0]
        started = self._turb_events.step(0.2, area, 1.0, self._turb_event_mult)
        if started is not None:
            self._log_turb_event_start(started)
        event = self._turb_events.current()
        if event is None:
            if self._turb_event_shown is not None:
                self._log_turb_event_end()
            return state, intensity
        # One per tick, and no other burst in that tick: WxBurst is a delta
        # variable, and PSX keeps only the last of several sent at once (a
        # background burst right after a -99 left 0.45 g instead of 1.3 g)
        for value in event.due_bursts(time.monotonic(), limit=1):
            self._turb_event_burst_sent = True
            self.psx_send_and_set("WxBurst", str(value), force=True)
            self._burst_summary.append((_BURST_SINK, _sign(value), abs(value), event.kind,
                                        event.level))
            self.logger.info("Turbulence event: %s burst %+d (about %.2f g)", event.kind, value,
                             1.25 * (abs(value) / 99.0) ** 2)
        if event.level > intensity:
            return TurbulenceState(intensity=event.level, kind="event",
                                   reason=f"{event.kind} event ({area})"), event.level
        return state, intensity

    def _log_turb_event_start(self, event) -> None:
        """Log a turbulence event starting and remember it for the web UI and its end."""
        mult = self._turb_event_mult.get(event.kind, 1.0)
        how = "triggered" if event.triggered else f"area {event.area or 'clean'}"
        self.logger.info(
            "Turbulence event: %s started (%s: %s) at %s, %.0f s, level %.0f%%%s%s%s",
            event.kind.upper(), how, self._turb_area[1] or "-", self._flight_position_text(),
            event.duration_s, event.level * 100,
            f", {len(event.bursts)} big bursts, planned peak {event.peak_g:.1f} g"
            if event.bursts else "",
            f", likelihood x{mult:g}" if mult != 1.0 and not event.triggered else "",
            "" if self._turb_enabled else " (turbulence off)")
        self._turb_event_shown = event
        self._turb_event_log.appendleft({
            "kind": event.kind, "utc": datetime.now(timezone.utc).strftime("%H:%M:%SZ"),
            "area": event.area, "reason": self._turb_area[1], "triggered": event.triggered,
            "duration_s": round(event.duration_s), "peak_g": round(event.peak_g, 1),
            "position": self._flight_position_text()})

    def _log_turb_event_end(self, why: str = "") -> None:
        """Log the end of the event last started."""
        if self._turb_event_shown is not None:
            self.logger.info("Turbulence event: %s over%s", self._turb_event_shown.kind, why)
        self._turb_event_shown = None

    def _flight_position_text(self) -> str:
        """Return the aircraft's position and altitude, e.g. "N57 00.1 E003 00.0 35,010 ft"."""
        raw = self.psx.get("PiBaHeAlTas") if self.psx else None
        try:
            _, _, _, alt_ft, _, lat, lon = parse_pibahealtas(raw)
        except (TypeError, ValueError):
            return "unknown position"

        def dm(value, pos, neg, width):
            deg, minutes = divmod(abs(value) * 60.0, 60.0)
            return f"{pos if value >= 0 else neg}{int(deg):0{width}d} {minutes:04.1f}"
        return f"{dm(lat, 'N', 'S', 2)} {dm(lon, 'E', 'W', 3)} {alt_ft:,.0f} ft"

    def _log_flight_status(self, pba: tuple, effective_intensity: float,  # pylint: disable=too-many-locals
                           state: TurbulenceState) -> None:
        """Every _FLIGHT_LOG_S, log the flight, weather and turbulence (also when smooth).

        So that a pilot's report of unrealistic (or good) weather or
        turbulence can be checked from the normal log, without --debug.
        """
        now = time.monotonic()
        if now - self._flight_log_last < _FLIGHT_LOG_S:
            return
        self._flight_log_last = now
        pitch, bank, hdg = (math.degrees(v) for v in pba[:3])
        alt_ft, tas_kt = pba[3], pba[4]
        wind = "-"
        profile = self._turb_engine.wind_profile() if self._turb_engine else None
        if profile is not None:
            spd, wdir = profile.wind_at(alt_ft * 0.3048)
            wind = f"{wdir:03.0f}/{spd:.0f} kt"
        psx_utc = "-"
        try:
            psx_utc = datetime.fromtimestamp(int(self.psx.get("TimeEarth")) / 1000.0,
                                             timezone.utc).strftime("%Y-%m-%d %H:%MZ")
        except (TypeError, ValueError, OverflowError, OSError):
            pass
        event = self._turb_events.current() if self._turb_testing() else None
        self.logger.info(
            "Flight: %s hdg %03.0f TAS %.0f kt pitch %.1f bank %.1f%s | UTC %s, PSX %s | "
            "wind %s%s | turbulence %.0f%% %s (%s: %s)%s | profile %s%s",
            self._flight_position_text(), hdg % 360, tas_kt, pitch, bank,
            " on ground" if self._on_ground() else "",
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"), psx_utc, wind,
            " in MSFS cloud" if self.msfs_in_cloud else "",
            effective_intensity * 100, _intensity_label(effective_intensity, self._turb_testing()),
            state.kind, state.reason or "-",
            "" if self._turb_enabled else " (OFF)",
            self._turb_profile,
            (f", area {self._turb_area[0]}, event {event.kind if event else 'none'}, "
             f"likelihood {'/'.join(f'{m:g}' for m in self._turb_event_mult.values())}")
            if self._turb_testing() else "")

    @staticmethod
    def _gust_family(kind: str) -> Optional[str]:
        """Return the gustiness family (_TURB_GUST) of a turbulence kind, or None.

        "X+shear" is wind shear that outweighs X (frankenturb/turbulence.py).
        """
        if kind == "shear" or kind.endswith("+shear"):
            return "shear"
        if kind == "wave":
            return "wave"
        if kind in ("rotor", "mechanical"):
            return "lowlevel"
        if kind == "cape":
            return kind
        return None

    def _apply_gust(self, state: TurbulenceState, alt_ft: float) -> tuple:
        """Return (state, driver): state with its family's gustiness applied at alt_ft.

        driver is (name, GustinessProfile, physical intensity) for
        _turb_ramp_status(), or None for a kind without gustiness (state
        unchanged).
        """
        family = self._gust_family(state.kind)
        if family is None or state.intensity <= 0.0:
            return state, None
        gust = self._gust_profiles[family]
        mult = gust.value(alt_ft)
        intensity = min(1.0, state.max_intensity, state.intensity * mult)
        return (dataclasses.replace(state, intensity=intensity,
                                    reason=f"{state.reason} (gust x{mult:.2f})"),
                (f"{family} gust", gust, state.intensity))

    def _pirep_vary(self, state: TurbulenceState, alt_ft: float) -> tuple:
        """Return (state, driver): PIREP turbulence with a varying level at alt_ft.

        Like G-AIRMET turbulence (_gairmet_vary()): the layered level
        (_pirep_turb_profile) times the PIREP's own intensity relative to
        SEV (distance, altitude and age fade, frankenturb/pirep.py). It used
        to be steady. driver is for _turb_ramp_status().
        """
        if state.intensity <= 0.0:
            return state, None
        scale = state.intensity / _PIREP_SEV_INTENSITY
        level = self._pirep_turb_profile.value(alt_ft)
        return (dataclasses.replace(state, intensity=min(1.0, scale * level),
                                    reason=f"{state.reason} (level {level:.0%})"),
                ("PIREP", self._pirep_turb_profile, scale))

    def _pirep_rec_share(self, rec, level_ft: float) -> float:
        """Return the long-run share of time a PIREP's turbulence is moderate+ at level_ft."""
        minimum = self._pirep_minimum("pirep")
        if rec is None or minimum is None:
            return 0.0
        scale = compute_pirep_turbulence(level_ft, rec).intensity / _PIREP_SEV_INTENSITY
        if scale <= 0.0:
            return 0.0
        return self._pirep_turb_profile.share_at_least(level_ft, minimum / scale)

    def _gairmet_vary(self, state: TurbulenceState, alt_ft: float) -> tuple:
        """Return (state, driver): G-AIRMET turbulence with its varying level at alt_ft.

        Like a TURB SIGMET: the layered level (_gairmet_turb_profile) times
        the area's own intensity relative to SEV (_GAIRMET_SEV_INTENSITY;
        severity and altitude fade, frankenturb/gairmet.py). driver is for
        _turb_ramp_status().
        """
        if state.intensity <= 0.0:
            return state, None
        scale = state.intensity / _GAIRMET_SEV_INTENSITY
        level = self._gairmet_turb_profile.value(alt_ft)
        return (dataclasses.replace(state, intensity=min(1.0, scale * level),
                                    reason=f"{state.reason} (level {level:.0%})"),
                ("G-AIRMET", self._gairmet_turb_profile, scale))

    def _sigmet_share_at(self, level_ft: float) -> float:
        """Return the long-run share of time the TURB/MTW SIGMETs are moderate or worse at level_ft.

        For PIREPs, at the aircraft's position (_sigmet_turb_areas): each
        layer at its current roughness (not the moment's level, like
        reports from aircraft that flew there), scaled like the real
        intensity (edge and base/top fade, qualifier). 0 outside any area.
        """
        minimum = self._pirep_minimum("sigmet")
        if minimum is None:
            return 0.0
        best = 0.0
        for hazard, lateral, entry in self._sigmet_turb_areas:
            scale = (lateral * self._sigmet_turb_vertical(level_ft, entry) *
                     _SIGMET_TURB_QUALIFIER_SCALE.get(entry["qualifier"], 1.0))
            if scale > 0.0:
                best = max(best, self._sigmet_turb_profiles[hazard].share_at_least(
                    level_ft, minimum / scale))
        return best

    @staticmethod
    def _sigmet_turb_lateral(lat: float, lon: float, entry: dict) -> float:
        """Return 0-1: how much of a TURB/MTW SIGMET's turbulence applies at lat/lon (edge fade)."""
        depth = max((d for d in (_fw_sigmet.inside_depth(lat, lon, p) for p in entry["polygons"])
                     if d is not None), default=None)
        return 0.0 if depth is None else min(1.0, depth / _SIGMET_TURB_FADE_NM)

    @staticmethod
    def _sigmet_turb_vertical(alt_ft: float, entry: dict) -> float:
        """Return 0-1: how much of a TURB/MTW SIGMET's turbulence applies at alt_ft (base/top)."""
        base = entry["base_ft"] or 0
        top = entry["top_ft"] if entry["top_ft"] is not None else 60000
        if not base <= alt_ft <= top:
            return 0.0
        vert = min(top - alt_ft, (alt_ft - base) if base > 0 else _SIGMET_TURB_FADE_FT)
        return min(1.0, vert / _SIGMET_TURB_FADE_FT)

    def _sigmet_cb_at(self, pos: tuple) -> Optional[tuple]:
        """Return (oktas, sigmet) for the TS/TC SIGMET area at pos, or None.

        Coverage from the qualifier (fw_sigmet.TS_QUALIFIER_OKTAS), a TC
        within fw_sigmet.TC_OKTAS. With several overlapping areas, the one
        giving the most coverage wins.
        """
        best: Optional[tuple] = None
        best_oktas = -1
        for sig in self.ts_sigmets:
            if not _point_in_polygon(pos[0], pos[1], sig['polygon']):
                continue
            if sig['hazard'] == 'TC':
                low, high = _fw_sigmet.TC_OKTAS
                # Stable per position, so it doesn't flicker between updates
                oktas = low + int(abs(pos[0] * 1000 + pos[1] * 1000)) % (high - low + 1)
            else:
                oktas = _fw_sigmet.TS_QUALIFIER_OKTAS.get(
                    sig.get('qualifier', ''), _fw_sigmet.TS_DEFAULT_OKTAS)
            if oktas > best_oktas:
                best, best_oktas = (oktas, sig), oktas
        return best

    @staticmethod
    def _cb_growth_start_pct(dist_nm: float) -> float:
        """Return a random starting fraction of forecast CB size for a CB this far out.

        Picks uniformly within whichever _CB_GROWTH_START_PCT_RANGES bracket
        dist_nm falls into (see --cb-growth / _ramp_cb_wx()) -- the same
        CB created at the same distance won't always start at the same
        size, only within the same range.
        """
        for threshold, low, high in _CB_GROWTH_START_PCT_RANGES:
            if dist_nm > threshold:
                return random.uniform(low, high)
        return random.uniform(*_CB_GROWTH_START_PCT_RANGE_BELOW)

    @staticmethod
    def _cb_growth_max_top_ft_per_min(cape_jkg: float) -> float:
        """Return the top-growth-rate cap for this CAPE, bounded at both ends.

        Real-world updrafts (and so convective growth) are stronger at
        higher CAPE -- scaled from parcel theory's sqrt(2*CAPE) max vertical
        velocity via _CB_GROWTH_CAPE_UPDRAFT_FRACTION (see that constant's
        comment). _CB_GROWTH_MAX_TOP_FT_PER_MIN is the floor for weak/zero
        CAPE (e.g. a CB forced by a TS SIGMET/METAR override);
        _CB_GROWTH_MAX_TOP_FT_PER_MIN_CEILING caps the other end, since
        sqrt(CAPE) alone has no ceiling of its own.
        """
        cape = max(cape_jkg, 0.0)
        updraft_mps = math.sqrt(2.0 * cape)
        scaled = updraft_mps * _CB_GROWTH_CAPE_UPDRAFT_FRACTION * _MPS_TO_FT_PER_MIN
        return min(_CB_GROWTH_MAX_TOP_FT_PER_MIN_CEILING,
                   max(_CB_GROWTH_MAX_TOP_FT_PER_MIN, scaled))

    def _limit_close_cb(self, zone_num: int, wx: str, pos: tuple) -> str:
        """Return wx with the CB of a zone near the aircraft limited (_CB_CLOSE_*).

        The first time the zone (at pos) is within _CB_SQUEEZE_MIN_FWD_NM,
        its CB from then on may be no bigger than the one it had then, or
        than _CB_CLOSE_MAX_OKTAS/_CB_CLOSE_MAX_TOP_FT (at least
        _CB_MIN_DEPTH_FT deep) if it had none, until the zone is moved:
        new zones are never placed this close, but a zone the aircraft
        flies towards can still get a CB (a new or moved TS SIGMET, CAPE,
        a METAR), which used to appear at full size, e.g. 45,000 ft
        within 10 nm. Not for the dep/dst airport zones (their METAR), and
        no new limit in the first _CB_CLOSE_START_GRACE_S after a start.
        Inside a TS/TC SIGMET area only within _CB_CLOSE_AREA_NM.
        """
        cap = self.zone_cb_close_cap.get(zone_num)
        if cap is not None and cap[0] != pos:
            del self.zone_cb_close_cap[zone_num]   # moved: starts afresh
            cap = None
        if self.ac_lat is None or pos[2] in {self.fmc_dep_icao, self.fmc_dst_icao}:
            return wx
        if cap is None:
            reach = (_CB_CLOSE_AREA_NM if self._in_cb_sigmet_area(self.ac_lat, self.ac_lon)
                     else _CB_SQUEEZE_MIN_FWD_NM)
            if self._dist_nm(self.ac_lat, self.ac_lon, pos[0], pos[1]) > reach:
                return wx
            if time.monotonic() < self._cb_close_grace_until:
                return wx
            prev = (self.zone_wx.get(zone_num) or "").split(';')
            same_zone = self._zone_wx_pos.get(zone_num) == pos and len(prev) > 11
            had = int(prev[9]) if same_zone and prev[9].isdigit() else 0
            cap = ((pos, had, float(prev[10])) if had > 0 else
                   (pos, _CB_CLOSE_MAX_OKTAS, _CB_CLOSE_MAX_TOP_FT))
            self.zone_cb_close_cap[zone_num] = cap
            self.logger.info("Zone %d within %.0fnm: CB at most %d oktas, %.0fft from now on",
                             zone_num, reach, cap[1], cap[2])
        parts = wx.split(';')
        oktas = int(parts[9]) if parts[9].isdigit() else 0
        if oktas <= 0:
            return wx
        base = float(parts[11])
        new_oktas = min(oktas, cap[1])
        new_top = min(float(parts[10]), max(cap[2], base + _CB_MIN_DEPTH_FT))
        if new_oktas == oktas and new_top == float(parts[10]):
            return wx
        self.logger.debug("Zone %d: CB %d oktas %sft limited to %d oktas %.0fft"
                          " (near the aircraft)", zone_num, oktas, parts[10], new_oktas, new_top)
        parts[9], parts[10] = str(new_oktas), str(round(new_top))
        return ';'.join(parts)

    def _fade_cb_wx(self, zone_num: int, target_wx: str) -> str:
        """Let zone_num's CB fade instead of vanishing (without --cb-growth).

        A CB appears or grows at once, as forecast, but a CB whose
        forecast shrinks or disappears (a TEMPO switching off, a new
        METAR, SIGMET expiry...) shrinks at _CB_FADE_*_PER_MIN: at most
        ~15 min from 8 oktas. It used to vanish in one zone update,
        radar echo and turbulence with it. Zone moves reset it (the zone
        starts fresh), so it never holds a zone back.
        """
        parts = target_wx.split(';')
        target_oktas = int(parts[9])
        target_top = float(parts[10])
        now = time.time()
        ramp = self.zone_cb_ramp.get(zone_num)
        if ramp is None or target_oktas >= ramp['current_oktas']:
            if target_oktas > 0:
                self.zone_cb_ramp[zone_num] = {
                    'current_oktas': float(target_oktas), 'current_top_ft': target_top,
                    'base_ft': float(parts[11]), 'last_update_ts': now,
                    'last_oktas_rate_per_min': 0.0, 'last_top_rate_per_min': 0.0}
            else:
                self.zone_cb_ramp.pop(zone_num, None)
            return target_wx
        elapsed_min = max(0.0, (now - ramp['last_update_ts']) / 60.0)
        new_oktas = max(float(target_oktas),
                        ramp['current_oktas'] - _CB_FADE_OKTAS_PER_MIN * elapsed_min)
        if new_oktas < 0.5:
            self.zone_cb_ramp.pop(zone_num, None)
            return target_wx
        base = float(parts[11]) if target_oktas > 0 else ramp.get('base_ft', float(parts[11]))
        new_top = max(target_top if target_oktas > 0 else base + _CB_MIN_DEPTH_FT,
                      ramp['current_top_ft'] - _CB_FADE_TOP_FT_PER_MIN * elapsed_min)
        ramp.update(current_oktas=new_oktas, current_top_ft=new_top, base_ft=base,
                    last_update_ts=now, last_oktas_rate_per_min=-_CB_FADE_OKTAS_PER_MIN,
                    last_top_rate_per_min=-_CB_FADE_TOP_FT_PER_MIN)
        parts[9] = str(max(1, round(new_oktas)))
        parts[10] = str(round(new_top))
        parts[11] = str(round(base))
        return ';'.join(parts)

    def _ramp_cb_wx(  # pylint: disable=too-many-locals,too-many-statements
            self, zone_num: int, target_wx: str, cape_jkg: float = 0.0) -> str:
        """Ramp zone_num's CB oktas/top toward target_wx's values (--cb-growth).

        Growth is sized at CB creation (new zone placement, or a CB first
        appearing in an already-settled zone) from the distance to the
        aircraft at that moment, so it both avoids an abrupt "pop" at a
        distance where it'd be noticeable and still finishes growing before
        the aircraft arrives (see _cb_growth_start_pct()/docs). The top
        growth rate's cap also scales with cape_jkg (this zone's forecast
        CAPE, if known) via _cb_growth_max_top_ft_per_min() -- stronger
        storms are allowed to grow faster, same as in reality. Once sized,
        the growth rate stays fixed; shrinking/removal always uses the
        fixed _CB_DECAY_*_PER_MIN rates instead, regardless of distance.
        Base (field 11) is never touched -- it's always the fresh forecast.
        """
        parts = target_wx.split(';')
        target_oktas = int(parts[9])
        target_top = float(parts[10])
        target_base = float(parts[11])
        now = time.time()
        ramp = self.zone_cb_ramp.get(zone_num)

        if target_oktas <= 0:
            if ramp is None or ramp['current_oktas'] <= 0:
                self.zone_cb_ramp.pop(zone_num, None)
                return target_wx
            elapsed_min = max(0.0, (now - ramp['last_update_ts']) / 60.0)
            new_oktas = max(0.0, ramp['current_oktas'] - _CB_DECAY_OKTAS_PER_MIN * elapsed_min)
            new_top = max(target_base,
                          ramp['current_top_ft'] - _CB_DECAY_TOP_FT_PER_MIN * elapsed_min)
            if new_oktas <= 0:
                self.zone_cb_ramp.pop(zone_num, None)
                return target_wx
            ramp.update(current_oktas=new_oktas, current_top_ft=new_top, last_update_ts=now,
                        last_oktas_rate_per_min=-_CB_DECAY_OKTAS_PER_MIN,
                        last_top_rate_per_min=-_CB_DECAY_TOP_FT_PER_MIN)
            parts[9] = str(max(1, round(new_oktas)))
            parts[10] = str(round(new_top))
            return ';'.join(parts)

        if ramp is None or ramp.get('current_oktas', 0) <= 0:
            dist_nm = self.zone_cb_create_dist_nm.pop(zone_num, None)
            if dist_nm is None:
                lat, lon, _ = self.zone_positions[zone_num]
                dist_nm = self._dist_nm(self.ac_lat, self.ac_lon, lat, lon)
            start_pct = self._cb_growth_start_pct(dist_nm)
            current_oktas = min(
                float(target_oktas),
                max(float(_CB_GROWTH_MIN_OKTAS), round(target_oktas * start_pct)))
            current_top = min(
                target_top,
                target_base + max(_CB_GROWTH_MIN_TOP_ABOVE_BASE_FT,
                                  start_pct * (target_top - target_base)))
            gs_kt = self.ac_tas_kt or _CB_GROWTH_ASSUMED_GS_KT
            time_to_arrival_min = max(1.0, dist_nm / gs_kt * 60.0)
            # Capped, not just sized to finish by arrival: avoiding a "pop"
            # right next to the aircraft matters more than a close-in CB
            # reaching full size in time -- see _CB_GROWTH_MAX_*_PER_MIN /
            # _cb_growth_max_top_ft_per_min() (CAPE raises the top cap).
            rate_oktas = min((target_oktas - current_oktas) / time_to_arrival_min,
                             _CB_GROWTH_MAX_OKTAS_PER_MIN)
            max_top_rate = self._cb_growth_max_top_ft_per_min(cape_jkg)
            rate_top = min((target_top - current_top) / time_to_arrival_min, max_top_rate)
            ramp = {
                'current_oktas': current_oktas,
                'current_top_ft': current_top,
                'growth_rate_oktas_per_min': rate_oktas,
                'growth_rate_top_per_min': rate_top,
                'last_oktas_rate_per_min': rate_oktas,
                'last_top_rate_per_min': rate_top,
                'last_update_ts': now,
            }
            self.zone_cb_ramp[zone_num] = ramp
            top_fill_min = (target_top - current_top) / rate_top if rate_top else 0.0
            self.logger.info(
                "Zone %d: CB growth starting at %.0fnm (%.0f%%, CAPE=%.0fJ/kg):"
                " %.1f/%d oktas, %.0f/%.0fft top (rate %.2fokt/min, %.0fft/min"
                " [cap %.0f], ac ETA %.0fmin, full growth in %.0fmin%s)",
                zone_num, dist_nm, start_pct * 100, cape_jkg, current_oktas,
                target_oktas, current_top, target_top, rate_oktas, rate_top,
                max_top_rate, time_to_arrival_min, top_fill_min,
                " -- capped, won't finish in time" if
                top_fill_min > time_to_arrival_min else "")
        else:
            old_oktas, old_top = ramp['current_oktas'], ramp['current_top_ft']
            elapsed_min = max(0.0, (now - ramp['last_update_ts']) / 60.0)
            if target_oktas >= old_oktas:
                new_oktas = min(
                    float(target_oktas),
                    old_oktas + ramp['growth_rate_oktas_per_min'] * elapsed_min)
            else:
                new_oktas = max(
                    float(target_oktas), old_oktas - _CB_DECAY_OKTAS_PER_MIN * elapsed_min)
            if target_top >= old_top:
                new_top = min(
                    target_top, old_top + ramp['growth_rate_top_per_min'] * elapsed_min)
            else:
                new_top = max(target_top, old_top - _CB_DECAY_TOP_FT_PER_MIN * elapsed_min)
            new_top = max(new_top, target_base)
            if elapsed_min > 0:
                oktas_rate = (new_oktas - old_oktas) / elapsed_min
                top_rate = (new_top - old_top) / elapsed_min
            else:
                oktas_rate = (ramp['growth_rate_oktas_per_min'] if target_oktas >= old_oktas
                              else -_CB_DECAY_OKTAS_PER_MIN)
                top_rate = (ramp['growth_rate_top_per_min'] if target_top >= old_top
                            else -_CB_DECAY_TOP_FT_PER_MIN)
            ramp.update(current_oktas=new_oktas, current_top_ft=new_top, last_update_ts=now,
                        last_oktas_rate_per_min=oktas_rate, last_top_rate_per_min=top_rate)

        parts[9] = str(max(_CB_GROWTH_MIN_OKTAS, round(ramp['current_oktas'])))
        parts[10] = str(round(ramp['current_top_ft']))
        return ';'.join(parts)

    # ------------------------------------------------------------------
    # MSFS sync (in-cloud and QNH)
    # ------------------------------------------------------------------

    def _msfs_time_in_cloud_fraction(self, window_s: float, alt_band_ft: float) -> float:
        """Return the fraction of matching recent samples MSFS reported being in cloud.

        Only samples within window_s seconds AND within alt_band_ft of the
        current altitude count — so history from before entering (or after
        leaving) the current altitude band, e.g. clear air above a layer
        you're now descending through, doesn't dilute the estimate. Falls
        back to the current in_cloud state if there isn't enough matching
        sample history yet (e.g. just after startup, or just after a climb
        or descent has aged out all the old-altitude samples).
        """
        if self.ac_alt_ft is None:
            return 1.0 if self.msfs_in_cloud else 0.0
        now = time.monotonic()
        cutoff = now - window_s
        history = [
            (t, v) for t, v, alt in self._msfs_in_cloud_history
            if t >= cutoff and abs(alt - self.ac_alt_ft) <= alt_band_ft
        ]
        if len(history) < 2:
            return 1.0 if self.msfs_in_cloud else 0.0
        in_cloud_time = sum(
            t1 - t0 for (t0, v0), (t1, _v1) in zip(history, history[1:]) if v0
        )
        if history[-1][1]:
            in_cloud_time += now - history[-1][0]
        total = now - history[0][0]
        return in_cloud_time / total if total > 0 else (1.0 if self.msfs_in_cloud else 0.0)

    def _msfs_cloud_oktas(self) -> int:
        """Return oktas (1-8) for an MSFS-synced cloud layer from density and time-in-cloud.

        Live logs show MSFS's ENV CLOUD DENSITY rarely exceeds ~0.2 even in
        genuinely dense-looking cloud (confirmed both from traffic-log samples
        and a live FL370 test with manual weather set to its densest clouds),
        so treating >0.2 as "this is real, solid cloud" and mapping it
        straight to 8/8 overcast avoids the coverage estimate being
        permanently starved by a ceiling MSFS itself doesn't seem to report
        above. Below that threshold (or when density is unknown), coverage
        instead comes purely from how much of recent flight time was spent in
        cloud (see _msfs_time_in_cloud_fraction) — a brief, patchy encounter
        pulls coverage down towards FEW/SCT rather than straight to OVC.
        """
        if (self.msfs_in_cloud and self.msfs_cloud_density is not None and
                self.msfs_cloud_density > 0.2):
            return 8
        time_frac = self._msfs_time_in_cloud_fraction(
            _MSFS_CLOUD_TIME_WINDOW_S, _MSFS_CLOUD_ALT_BAND_FT)
        return max(1, round(8 * time_frac))

    def _apply_msfs_sync(self) -> None:  # pylint: disable=too-many-branches,too-many-locals,too-many-statements
        """Apply MSFS→PSX sync for the focused zone: clouds and/or QNH.

        Only one of our 7 zones, never the planet zone (WxBasic).
        """
        need_cloud = self._msfs_in_cloud_sync and self.msfs_in_cloud is not None
        need_qnh = True
        if not need_cloud and not need_qnh:
            return
        if self.ac_alt_ft is None:
            return
        if self._msfs_bridge_last_seen is not None:
            bridge_age = time.monotonic() - self._msfs_bridge_last_seen
            if bridge_age > _MSFS_BRIDGE_TIMEOUT_S:
                self.logger.debug("MSFS bridge data stale (%.0fs), skipping sync", bridge_age)
                return
        if self.focused_zone == 0:
            # Only our 7 zones: the planet zone (WxBasic) is outside them and
            # gets nothing from MSFS (see _without_cb() for why it stays quiet)
            self.logger.debug("MSFS sync skipped: the aircraft is in the planet zone")
            return
        zone_key = f"Wx{self.focused_zone}"
        metar_zone_num = self.focused_zone
        wx = self.psx.get(zone_key) if self.psx else None
        if not wx:
            return
        data = wx.split(';')
        if len(data) < 24:
            return

        changed = False

        if need_qnh and self.zone_is_metar.get(metar_zone_num, False):
            self.logger.debug("QNH sync skipped [%s]: zone uses real METAR", zone_key)
            need_qnh = False

        if need_qnh and self.msfs_qnh_hpa is not None:
            psx_qnh_hpa = int(data[23]) / 2.953
            diff = self.msfs_qnh_hpa - psx_qnh_hpa
            if abs(diff) > 1.0:
                if self._msfs_qnh_check == "SYNC":
                    self._inject_wx_slow_transit()
                    data[23] = str(_hpa_to_psx_qnh(self.msfs_qnh_hpa))
                    self.logger.info(
                        "QNH sync [%s]: %.1f → %.1f hPa",
                        zone_key, psx_qnh_hpa, self.msfs_qnh_hpa)
                    changed = True
                    old_metar = self.psx.get(f"Metar{metar_zone_num}")
                    if old_metar:
                        new_metar = _update_metar_qnh(old_metar, self.msfs_qnh_hpa)
                        if new_metar != old_metar:
                            new_metar = _metar_with_note(new_metar, METAR_NOTE_QNH_SYNC)
                            self.psx_send_and_set(f"Metar{metar_zone_num}", new_metar)
                else:
                    # once per zone, again only when the difference changes or after
                    # 10 minutes: it came on every cloud sync, 179 times in 40 minutes
                    # (frankensim 2026-10-29)
                    last = self._qnh_mismatch_warned.get(zone_key)
                    now = time.monotonic()
                    if last is None or abs(diff - last[1]) >= 0.5 or now - last[0] >= 600.0:
                        self._qnh_mismatch_warned[zone_key] = (now, diff)
                        self.logger.warning(
                            "*** QNH mismatch [%s]: MSFS %.1f hPa  PSX %.1f hPa  Δ%.1f ***",
                            zone_key, self.msfs_qnh_hpa, psx_qnh_hpa, diff)

        if need_cloud:
            alt = self.ac_alt_ft
            margin = 1000
            hi_cov, hi_top, hi_base = int(data[0]), int(data[1]), int(data[2])
            lo_cov, lo_top, lo_base = int(data[3]), int(data[4]), int(data[5])
            cb_cov, cb_top, cb_base = int(data[9]), int(data[10]), int(data[11])
            in_hi = hi_cov > 0 and hi_base + margin <= alt <= hi_top - margin
            in_lo = lo_cov > 0 and lo_base + margin <= alt <= lo_top - margin
            in_cb = cb_cov > 0 and cb_base + margin <= alt <= cb_top - margin
            if self.msfs_in_cloud:
                if in_hi or in_lo or in_cb:
                    self.logger.debug("Cloud sync [%s]: already in cloud", zone_key)
                elif hi_cov == 0:
                    oktas = self._msfs_cloud_oktas()
                    data[0] = str(oktas)
                    data[1] = str(int(alt + margin))
                    data[2] = str(int(max(_MSFS_CLOUD_BASE_FLOOR_FT, alt - margin)))
                    self.logger.info("Cloud sync [%s]: created hi layer at %dft (%d oktas)",
                                     zone_key, int(alt), oktas)
                    changed = True
                elif alt > hi_top - margin:
                    oktas = self._msfs_cloud_oktas()
                    data[0] = str(oktas)
                    data[1] = str(int(alt + margin))
                    self.logger.info("Cloud sync [%s]: raised hi layer top to %dft (%d oktas)",
                                     zone_key, int(alt + margin), oktas)
                    changed = True
                else:
                    oktas = self._msfs_cloud_oktas()
                    data[0] = str(oktas)
                    new_base = max(_MSFS_CLOUD_BASE_FLOOR_FT, alt - margin)
                    data[2] = str(int(new_base))
                    self.logger.info("Cloud sync [%s]: lowered hi layer base to %dft (%d oktas)",
                                     zone_key, int(new_base), oktas)
                    changed = True
            else:
                if in_hi:
                    data[0] = "0"
                    self.logger.info("Cloud sync [%s]: MSFS clear — zeroed hi layer", zone_key)
                    changed = True
                elif in_lo:
                    data[3] = "0"
                    self.logger.info("Cloud sync [%s]: MSFS clear — zeroed lo layer", zone_key)
                    changed = True
                else:
                    self.logger.debug("Cloud sync [%s]: MSFS clear, PSX not in cloud", zone_key)

        if changed:
            self._inject_wx_slow_transit()
            self.cloud_sync_last_alt_ft = self.ac_alt_ft
            self.last_write_time = time.time()
            new_wx = ";".join(data)
            self.psx_send_and_set(zone_key, new_wx)
            self.zone_wx[self.focused_zone] = new_wx

    # ------------------------------------------------------------------
    # Maneuvering mode detection
    # ------------------------------------------------------------------

    def _update_maneuvering_mode(self) -> bool:  # pylint: disable=too-many-return-statements
        """Update self._maneuvering from speed and heading history. Returns True if changed."""
        if self.ac_hdg is None:
            return False

        # Below 200 kt (taxi, takeoff roll, approach, landing) always maneuver.
        # Clear heading history so ground turns don't re-trigger after takeoff.
        if self.ac_tas_kt is not None and self.ac_tas_kt < 200.0:
            self._hdg_history.clear()
            if not self._maneuvering:
                self._maneuvering = True
                self.logger.info(
                    "MANEUVERING mode: speed %.0f kt — zones redistributed", self.ac_tas_kt)
                return True
            return False

        now = time.monotonic()
        self._hdg_history.append((now, self.ac_hdg))
        cutoff = now - _HDG_WINDOW_S
        self._hdg_history = [(t, h) for t, h in self._hdg_history if t >= cutoff]
        if len(self._hdg_history) < 2:
            return False
        total_change = sum(
            abs(((b - a + 180.0) % 360.0) - 180.0)
            for (_, a), (_, b) in zip(self._hdg_history, self._hdg_history[1:])
        )
        if not self._maneuvering and total_change > _MANEUVER_ENTER_DEG:
            self._maneuvering = True
            self.logger.info(
                "MANEUVERING mode: heading changed %.0f° in %.0fs — zones redistributed",
                total_change, now - self._hdg_history[0][0])
            return True
        if self._maneuvering and total_change < _MANEUVER_EXIT_DEG:
            self._maneuvering = False
            self.logger.info(
                "CRUISE mode: heading change %.0f° in %.0fs — resuming forward placement",
                total_change, now - self._hdg_history[0][0])
            return True
        return False

    # ------------------------------------------------------------------
    # Zone geometry
    # ------------------------------------------------------------------

    def _dist_nm(self, lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Return geodesic distance between two points in nautical miles."""
        _, _, dist_m = self.geod.inv(lon1, lat1, lon2, lat2)
        return dist_m / _NM_TO_M

    def _airports_within_nm(self, lat: float, lon: float, max_nm: float) -> list:
        """Return list of (icao, alat, alon, dist_nm) within max_nm, sorted by distance."""
        results = []
        for icao, (alat, alon) in self.airports.items():
            _, _, dist_m = self.geod.inv(lon, lat, alon, alat)
            dist_nm = dist_m / _NM_TO_M
            if dist_nm <= max_nm:
                results.append((icao, alat, alon, dist_nm))
        results.sort(key=lambda x: x[3])
        return results

    def _rv_local_sample_points(self) -> list:
        """Return a grid of (lat, lon) points within _RV_DENSITY_SCAN_NM of the aircraft.

        Used to pre-fetch RainViewer tiles covering the local area and to compute
        the local CB echo density for the CB squeeze.
        """
        if self.ac_lat is None or self.ac_lon is None:
            return []
        n = _RV_DENSITY_GRID
        radius = _RV_DENSITY_SCAN_NM
        lat_per_nm = 1.0 / 60.0
        lon_per_nm = lat_per_nm / max(0.01, math.cos(math.radians(self.ac_lat)))
        pts = []
        for i in range(n):
            for j in range(n):
                dlat = (i / (n - 1) * 2 - 1) * radius * lat_per_nm
                dlon = (j / (n - 1) * 2 - 1) * radius * lon_per_nm
                lat_c = self.ac_lat + dlat
                lon_c = ((self.ac_lon + dlon + 180.0) % 360.0) - 180.0
                pts.append((lat_c, lon_c))
        return pts

    def _rv_local_density(self) -> float:
        """Fraction of local sample grid points (within _RV_DENSITY_SCAN_NM) with echo >= 2."""
        pts = self._rv_local_sample_points()
        if not pts:
            return 0.0
        hits = sum(1 for lat, lon in pts if self._rv_echo_at(lat, lon) >= 2)
        return hits / len(pts)

    def _effective_fwd_max(self) -> float:
        """Return fwd_max, squeezed toward the cape-squeeze minimum.

        Two independent signals can trigger the squeeze and are combined via max():
        - CAPE signal: average CB oktas across active zones mapped to approximate CAPE
        - Radar signal: fraction of local grid points (within _RV_DENSITY_SCAN_NM)
          with echo >= 2; full squeeze at _RV_DENSITY_THRESHOLD
        Both are linear [0..1]; the larger factor is used.
        """
        _, fwd_max = self.args.new_zone_infront_range
        if not self.args.cape_squeeze:
            return fwd_max
        cape_threshold, min_fwd = self.args.cape_squeeze

        cape_factor = 0.0
        if self.zone_wx:
            estimates = []
            for zone_num in range(1, 8):
                wx = self.zone_wx.get(zone_num)
                if wx:
                    parts = wx.split(';')
                    if len(parts) > 9:
                        estimates.append(_OKTAS_TO_CAPE.get(int(parts[9]), 0.0))
            if estimates:
                avg_cape = sum(estimates) / len(estimates)
                cape_factor = max(0.0, min(1.0, avg_cape / cape_threshold))

        density = self._rv_local_density()
        radar_factor = max(0.0, min(1.0, density / _RV_DENSITY_THRESHOLD))

        factor = max(cape_factor, radar_factor)
        if factor == 0.0:
            return fwd_max
        return fwd_max - factor * (fwd_max - min_fwd)

    def _placement_desc(self, lat: float, lon: float, icao: str,
                        is_arpt: bool = False) -> str:
        """Return a human-readable placement description for a zone at (lat, lon)."""
        if self.ac_lat is None or self.ac_hdg is None:
            return ""
        az, _, dist_m = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
        dist_nm = dist_m / _NM_TO_M

        if is_arpt:
            label = "Dep" if icao == self.fmc_dep_icao else "Dst"
            return f"{label} airport {icao}, {dist_nm:.0f}nm from aircraft"

        rel_rad = math.radians((az % 360 - self.ac_hdg + 360) % 360)
        fwd_nm = dist_nm * math.cos(rel_rad)
        right_nm = dist_nm * math.sin(rel_rad)

        if abs(fwd_nm) < 5:
            pos = [f"{dist_nm:.0f}nm abeam"]
        elif fwd_nm >= 0:
            pos = [f"{fwd_nm:.0f}nm ahead"]
        else:
            pos = [f"{abs(fwd_nm):.0f}nm behind"]

        if abs(right_nm) >= 5:
            pos.append(f"{abs(right_nm):.0f}nm {'right' if right_nm > 0 else 'left'} of track")

        is_fake = len(icao) == 4 and icao[0] == 'X' and icao[1:].isdigit()
        if not is_fake:
            pos.append(f"at {icao}")

        return ", ".join(pos)

    def _pick_position(  # pylint: disable=too-many-locals
            self, exclude_zone: int = None, initial: bool = False) -> tuple:
        """Pick a zone placement and return (lat, lon, icao).

        When initial=True: random bearing, distance up to new_zone_infront_range
        MAX, distributing zones evenly around the aircraft at startup.
        When initial=False (relocation): forward along track, distance within
        new_zone_infront_range, lateral offset within new_zone_leftright_range.
        Both modes retry up to 10 times to maintain new_zone_notnear clearance
        from other zones, and snap to a nearby airport if within _AIRPORT_SNAP_NM.
        Inside a TS/TC SIGMET area (_in_cb_sigmet_area()) the clearance is at
        least _CB_AREA_ZONE_SEP_NM and the all-around placement keeps
        _CB_AREA_MIN_DIST_NM from the aircraft.
        """
        configured_fwd_min, configured_fwd_max = self.args.new_zone_infront_range
        fwd_max = self._effective_fwd_max()
        fwd_min = (configured_fwd_min * fwd_max / configured_fwd_max
                   if configured_fwd_max > 0 else 0.0)
        fwd_min = max(fwd_min, min(configured_fwd_min, _CB_SQUEEZE_MIN_FWD_NM))
        fwd_max = max(fwd_max, fwd_min)
        if not initial and self.args.cape_squeeze and fwd_max < configured_fwd_max:
            self.logger.info("CB squeeze active: %.0f-%.0fnm ahead → %.0f-%.0fnm",
                             configured_fwd_min, configured_fwd_max, fwd_min, fwd_max)
        lat_min, lat_max = self.args.new_zone_leftright_range
        min_sep = self.args.new_zone_notnear
        cb_area = self._in_cb_sigmet_area(self.ac_lat, self.ac_lon)
        if cb_area:
            min_sep = max(min_sep, _CB_AREA_ZONE_SEP_NM)
        others = [(lat, lon) for zn, (lat, lon, _) in self.zone_positions.items()
                  if zn != exclude_zone]
        other_icaos = {icao for zn, (_, _, icao) in self.zone_positions.items()
                       if zn != exclude_zone}
        search_nm = fwd_max + (0.0 if initial else lat_max) + _AIRPORT_SNAP_NM
        airports_nearby = self._airports_within_nm(self.ac_lat, self.ac_lon, search_nm)

        # Generate a pool of candidates; score by radar echo to bias toward active cells.
        # When radar is unavailable all echoes are 0 → reduces to clearance-only selection.
        candidates: list = []
        for _ in range(10):
            if initial or self._maneuvering:
                bearing = random.uniform(0.0, 360.0)
                nearest = min(_CB_AREA_MIN_DIST_NM, fwd_max) if cb_area else 0.0
                dist_m = random.uniform(nearest, fwd_max) * _NM_TO_M
            else:
                fwd_nm = random.uniform(fwd_min, fwd_max)
                lat_nm = random.uniform(lat_min, lat_max) * random.choice([-1, 1])
                bearing = (self.ac_hdg + math.degrees(math.atan2(lat_nm, fwd_nm))) % 360.0
                dist_m = math.sqrt(fwd_nm ** 2 + lat_nm ** 2) * _NM_TO_M
            lon_c, lat_c, _ = self.geod.fwd(
                lons=self.ac_lon, lats=self.ac_lat, az=bearing, dist=dist_m)
            lon_c = ((lon_c + 180.0) % 360.0) - 180.0
            best_d, best_ap = float('inf'), None
            for ap_icao, ap_lat, ap_lon, _ in airports_nearby:
                d = self._dist_nm(lat_c, lon_c, ap_lat, ap_lon)
                if d < best_d:
                    best_d, best_ap = d, (ap_icao, ap_lat, ap_lon)
            if (best_d <= _AIRPORT_SNAP_NM and best_ap is not None and
                    best_ap[0] not in other_icaos):
                icao, lat_c, lon_c = best_ap
            else:
                icao = f"X{random.randint(100, 999):03d}"
            too_close = bool(others and
                             any(self._dist_nm(lat_c, lon_c, o[0], o[1]) < min_sep
                                 for o in others))
            echo = self._rv_echo_at(lat_c, lon_c)
            candidates.append((echo, not too_close, lat_c, lon_c, icao))

        # Sort: clearance-passing first (-True < -False), then highest echo within each group.
        candidates.sort(key=lambda c: (-c[1], -c[0]))
        _, _, lat_c, lon_c, icao = candidates[0]
        return lat_c, lon_c, icao

    def _cb_turb_in_reach(self, zone_num: int) -> Optional[str]:
        """Return why zone_num's CB turbulence can reach the aircraft, or None if it can't.

        A zone whose CB the aircraft is in, or still near enough to for its
        turbulence (core plus flank zone, or plus the anvil zone above the
        cloud top), is never moved: the turbulence would stop at once
        (live, 2026-10-23). Measured from the zone centre, generously: the
        CB itself is _CB_TO_ZONE_OFFSET_NM from it (see frankenturb/cb.py).
        """
        raw = self.psx.get(f"Wx{zone_num}") if self.psx else None
        cb = parse_wx_zone_basic(raw) if raw else None
        if (cb is None or cb[0] == 0 or self.ac_lat is None or
                zone_num not in self.zone_positions):
            return None
        coverage, top_ft, base_ft = cb
        positions = {zn: (lat, lon, 0.0) for zn, (lat, lon, _) in self.zone_positions.items()}
        reach_nm = (zone_cb_radius_nm(zone_num, positions, self._turb_lateral_size_bias / 100.0) +
                    cb_flank_width_nm(coverage, top_ft, base_ft,
                                      anvil=(self.ac_alt_ft or 0.0) > top_ft) +
                    _CB_TO_ZONE_OFFSET_NM)
        dist_nm = self._dist_nm(self.ac_lat, self.ac_lon, *self.zone_positions[zone_num][:2])
        if dist_nm > reach_nm:
            return None
        return f"CB turbulence reaches {reach_nm:.0f}nm, aircraft {dist_nm:.0f}nm away"

    def _replace_zones_for_cb_area(self) -> bool:
        """Re-place the zones placed before the SIGMETs were applied, if in a TS/TC area.

        At startup the zones are placed before the SIGMETs are applied, so
        without the TS/TC area's spacing (_CB_AREA_*): the first placement
        put a zone 22 nm from the aircraft (frankensim 2026-10-29). Once the
        SIGMETs are in, within _CB_CLOSE_START_GRACE_S of the start, the
        zones are placed once more if the aircraft is in such an area.
        Returns True if they were.
        """
        self._zones_placed_before_sigmets = False
        if (time.monotonic() >= self._cb_close_grace_until or
                not self._in_cb_sigmet_area(self.ac_lat, self.ac_lon)):
            return False
        self.logger.info("Inside a TS/TC SIGMET area: re-placing the zones with the area's spacing")
        self._place_all_zones()
        return True

    def _place_all_zones(self, keep_cb_turb: bool = False) -> None:
        """Place all 7 zones randomly around the aircraft (startup, reposition, maneuvering).

        With keep_cb_turb, zones whose CB turbulence can reach the aircraft
        stay where they are (see _cb_turb_in_reach()).
        """
        self._zones_placed_before_sigmets = not self._own_sigmets_applied
        kept = {}
        if keep_cb_turb:
            for zone_num, pos in self.zone_positions.items():
                why = self._cb_turb_in_reach(zone_num)
                if why is not None:
                    kept[zone_num] = pos
                    self.logger.info("Zone %d: %s left in place (%s)", zone_num, pos[2], why)
        self.zone_positions.clear()
        self.zone_positions.update(kept)
        self._anchor_zone = None
        self._cb_free_zones.clear()
        for per_zone in (self.zone_relocated_time, self.zone_cb_ramp, self.zone_cb_create_dist_nm):
            for zone_num in [zn for zn in per_zone if zn not in kept]:
                del per_zone[zone_num]
        free = [zn for zn in range(1, 8) if zn not in kept]
        kept_icaos = {icao for _, _, icao in kept.values()}
        for icao, lat, lon in self._arpt_coverage_needed():
            if not free:
                break
            if icao in kept_icaos:
                continue
            next_zone = free.pop(0)
            az, _, dist_m = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            self.zone_positions[next_zone] = (lat, lon, icao)
            self.zone_placement_reason[next_zone] = self._placement_desc(
                lat, lon, icao, is_arpt=True)
            self.zone_cb_create_dist_nm[next_zone] = dist_m / _NM_TO_M
            self.logger.info(
                "Zone %d: initial placement at %s @ %.3f/%.3f  %.0f°/%.0fnm (dep/dst arpt)",
                next_zone, icao, lat, lon, az % 360, dist_m / _NM_TO_M)
        for zone_num in free:
            lat, lon, icao = self._pick_position(initial=True)
            self.zone_positions[zone_num] = (lat, lon, icao)
            self.zone_placement_reason[zone_num] = self._placement_desc(lat, lon, icao)
            az, _, dist_m = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            self.zone_cb_create_dist_nm[zone_num] = dist_m / _NM_TO_M
            self.logger.info(
                "Zone %d: initial placement at %s @ %.3f/%.3f  %.0f°/%.0fnm",
                zone_num, icao, lat, lon, az % 360, dist_m / _NM_TO_M)

    def _zone_relocate_reason(
            self, zone_num: int, in_cruise: bool, arpt_icaos: set) -> Optional[str]:
        """Return why zone_num is due for relocation, or None if it's still useful.

        A zone ahead of (or abeam) the aircraft is never due, in cruise or
        maneuvering alike: it may hold a CB, and real CBs only ever drift
        slowly with the wind, never teleport -- so nothing currently in
        front of the aircraft (i.e. potentially visible on radar) gets
        relocated out from under the pilot. Only once a zone has actually
        passed behind does distance matter: cruise_behind_dist in cruise,
        low_alt_dist below cruise altitude or while maneuvering.
        Does not apply _check_and_relocate()'s extra "don't relocate more
        than once per refresh interval" throttle, since that's specific to
        actually moving a zone, not to asking whether it geographically
        still matters.
        """
        lat, lon, icao = self.zone_positions[zone_num]
        if icao in arpt_icaos:
            return None  # dep/dst airport — never relocate, always relevant
        az_to_zone, _, dist_nm = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
        dist_nm /= _NM_TO_M
        is_behind = abs((az_to_zone - self.ac_hdg + 180) % 360 - 180) > 90.0
        if not is_behind:
            return None
        limit = (self.args.cruise_behind_dist if in_cruise and not self._maneuvering
                 else self.args.low_alt_dist)
        if dist_nm <= limit:
            return None
        return f"{dist_nm:.0f}nm behind (limit {limit:.0f}nm)"

    def _zones_all_stale(self) -> bool:
        """Return True if the aircraft is too far from every zone for PSX to use them.

        Used by _update_zones() to decide whether a persistent Open-Meteo
        outage has made the existing (frozen) zone weather irrelevant enough
        to reposition onto the METAR-fallback stations instead (see
        _apply_metar_fallback()). Deliberately a *separate*, more generous
        distance test than _zone_relocate_reason()'s cruise_behind_dist/
        low_alt_dist (50nm/200nm) -- those exist to keep frankenweather's own
        zones geographically *useful* for weather lookahead, not to track how
        far PSX itself will still credit a zone's weather at the aircraft's
        position. PSX keeps using the nearest zone's weather out to
        wxautoset_handoff_dist nm regardless of how "stale" frankenweather
        considers it for its own placement purposes -- repositioning any
        sooner than that just causes needless zone churn for no benefit.
        """
        if self._diag_force_zones_stale:
            return True
        nearest_nm = self._nearest_zone_dist_nm()
        if nearest_nm is None:
            return False
        return nearest_nm > self.args.wxautoset_handoff_dist

    def _nearest_zone_num(self) -> Optional[int]:
        """Return the zone number of the zone nearest the aircraft, or None."""
        if not self.zone_positions or self.ac_lat is None:
            return None
        return min(
            self.zone_positions,
            key=lambda zn: self._dist_nm(
                self.ac_lat, self.ac_lon, *self.zone_positions[zn][:2])
        )

    def _nearest_zone_dist_nm(self) -> Optional[float]:
        """Return the distance in nm from the aircraft to its nearest zone, or None."""
        zone_num = self._nearest_zone_num()
        if zone_num is None:
            return None
        lat, lon, _ = self.zone_positions[zone_num]
        return self._dist_nm(self.ac_lat, self.ac_lon, lat, lon)

    def _check_and_relocate(self) -> bool:  # pylint: disable=too-many-locals
        """Relocate zones that are no longer useful given aircraft position and altitude.

        A zone must have passed behind the aircraft (aft hemisphere) before
        distance is even considered -- see _zone_relocate_reason() for why.
        In cruise (>= cruise_alt ft): relocate once more than
        cruise_behind_dist nm behind. Below cruise alt, or while maneuvering:
        relocate once more than low_alt_dist nm behind.
        The nearest zone and PSX's own reported FocussedWxZone (self.focused_zone
        -- almost always the same zone, since PSX picks its active zone by
        proximity, but not guaranteed to flip the instant the nearest one
        changes) are always left in place, regardless of what the relocation
        test above would otherwise say. Either is normally the zone PSX is
        actually using for the aircraft's local weather, and protecting them
        is the only thing stopping every zone from relocating in the same
        pass (e.g. after a long descent ages several zones behind the
        aircraft at once) -- which would otherwise strip away every nearby
        zone simultaneously and force an abrupt handoff to a brand-new,
        unblended zone, defeating the "new zones appear far ahead and don't
        affect the aircraft until it catches up to them" design.
        Returns True if any zone was moved.
        """
        in_cruise = (self.ac_alt_ft is not None and
                     self.ac_alt_ft >= 18000.0)
        arpt_icaos = {icao for icao, _, _ in self._arpt_coverage_needed()}
        protected_zones = {self._nearest_zone_num()}
        if self.focused_zone:
            protected_zones.add(self.focused_zone)
        if self._anchor_zone is not None:
            protected_zones.add(self._anchor_zone)  # moved only by _maintain_anchor_zone()
        any_moved = False
        now = time.time()
        for zone_num in range(1, 8):
            if zone_num not in self.zone_positions:
                continue
            lat, lon, icao = self.zone_positions[zone_num]
            reason = self._zone_relocate_reason(zone_num, in_cruise, arpt_icaos)
            if reason is None:
                continue
            why_kept = ("nearest and/or PSX's FocussedWxZone" if zone_num in protected_zones
                        else self._cb_turb_in_reach(zone_num))
            if why_kept is not None:
                last_logged = self._zone_protected_last_logged.get(zone_num, 0)
                if now - last_logged >= _ZONE_PROTECTED_LOG_INTERVAL_S:
                    self._zone_protected_last_logged[zone_num] = now
                    self.logger.info(
                        "Zone %d: relocation due (%s) but zone is protected"
                        " (%s) — left in place",
                        zone_num, reason, why_kept)
                continue
            if not in_cruise or self._maneuvering:
                if now - self.zone_relocated_time.get(zone_num, 0) < _REFRESH_MAX_S:
                    continue
            az_to_zone, _, dist_nm = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            dist_nm /= _NM_TO_M
            new_lat, new_lon, new_icao = self._pick_position(exclude_zone=zone_num)
            self.zone_positions[zone_num] = (new_lat, new_lon, new_icao)
            self.zone_placement_reason[zone_num] = self._placement_desc(
                new_lat, new_lon, new_icao)
            self.zone_relocated_time[zone_num] = now
            self.zone_cb_ramp.pop(zone_num, None)
            new_az, _, new_dist_m = self.geod.inv(
                self.ac_lon, self.ac_lat, new_lon, new_lat)
            self.zone_cb_create_dist_nm[zone_num] = new_dist_m / _NM_TO_M
            self.logger.info(
                "Zone %d: relocated from %s @ %.3f/%.3f [%.0f°/%.0fnm] (%s)"
                " → %s @ %.3f/%.3f [%.0f°/%.0fnm]",
                zone_num, icao, lat, lon, az_to_zone % 360, dist_nm, reason,
                new_icao, new_lat, new_lon, new_az % 360, new_dist_m / _NM_TO_M)
            any_moved = True
        return any_moved

    def _arpt_coverage_needed(self) -> list:
        """Return (icao, lat, lon) for dep/dst airports within arpt_zone_dist of aircraft."""
        _ARPT_ZONE_DIST_NM = 200.0
        if self.ac_lat is None:
            return []
        result = []
        seen: set = set()
        for icao in (self.fmc_dep_icao, self.fmc_dst_icao):
            if not icao or icao in seen:
                continue
            seen.add(icao)
            if icao not in self.airports:
                self.logger.warning("FMC arpt %s not in airport database — no zone assigned",
                                    icao)
                continue
            lat, lon = self.airports[icao]
            dist = self._dist_nm(self.ac_lat, self.ac_lon, lat, lon)
            if dist <= _ARPT_ZONE_DIST_NM:
                if self._cb_airport_mode == "move":
                    offset_nm = _CB_AIRPORT_CLEARANCE_NM + _CB_TO_ZONE_OFFSET_NM + 1.0
                    offset_lon, offset_lat, _ = self.geod.fwd(
                        lons=lon, lats=lat, az=_CB_AVOIDANCE_BEARING_DEG,
                        dist=offset_nm * _NM_TO_M)
                    result.append((icao, offset_lat, offset_lon))
                else:
                    result.append((icao, lat, lon))
            else:
                self.logger.debug("FMC arpt %s too far (%.0fnm > %.0fnm limit)",
                                  icao, dist, _ARPT_ZONE_DIST_NM)
        return result

    def _most_expendable_zone(self, exclude_icaos: set) -> Optional[int]:
        """Return the zone number farthest from aircraft not serving an excluded airport."""
        best_zn, best_dist = None, -1.0
        for zn, (zlat, zlon, zicao) in self.zone_positions.items():
            if zicao in exclude_icaos or self._cb_turb_in_reach(zn) is not None:
                continue
            d = self._dist_nm(self.ac_lat, self.ac_lon, zlat, zlon)
            if d > best_dist:
                best_dist, best_zn = d, zn
        return best_zn

    def _reposition_arpt_zones_for_cb_avoidance(self) -> None:
        """Immediately re-apply the current cb_airport_mode zone position in place.

        _ensure_arpt_zones()/_place_all_zones() only ever assign a *new*
        zone to an airport that doesn't already have one -- an
        already-established dep/dst zone is never revisited, by design
        (repositioning a zone the aircraft currently depends on risks the
        same instant-PFD-jump pattern this whole avoidance feature exists
        to sidestep). But per explicit user request, toggling
        cb_airport_mode in flight should take effect immediately even at
        that cost, rather than only applying to the next fresh assignment --
        so this directly moves any already-assigned dep/dst zone to match
        what _arpt_coverage_needed() would now place it at (offset for
        "move", the airport itself otherwise), and forces an out-of-cycle
        _update_zones() write to apply it.
        """
        needed = {icao: (lat, lon) for icao, lat, lon in self._arpt_coverage_needed()}
        moved = False
        for zone_num, (lat, lon, icao) in list(self.zone_positions.items()):
            if icao not in needed:
                continue
            new_lat, new_lon = needed[icao]
            if (new_lat, new_lon) == (lat, lon):
                continue
            self.zone_positions[zone_num] = (new_lat, new_lon, icao)
            self.zone_placement_reason[zone_num] = self._placement_desc(
                new_lat, new_lon, icao, is_arpt=True)
            self.zone_relocated_time[zone_num] = time.time()
            self.zone_cb_ramp.pop(zone_num, None)
            _, _, new_dist_m = self.geod.inv(self.ac_lon, self.ac_lat, new_lon, new_lat)
            self.zone_cb_create_dist_nm[zone_num] = new_dist_m / _NM_TO_M
            self.logger.info(
                "Zone %d: repositioned %s for cb_airport_mode %s "
                "(%.3f/%.3f → %.3f/%.3f)",
                zone_num, icao, self._cb_airport_mode, lat, lon, new_lat, new_lon)
            moved = True
        if moved:
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()

    def _fake_cb_zone(self, zone_num: int, snap_icao: str) -> bool:
        """Return True if --fake-cb applies to zone_num (see --fake-cb-only)."""
        if not self.args.fake_cb_only:
            return True
        wanted = {{"DEP": self.fmc_dep_icao, "DST": self.fmc_dst_icao}.get(name, name)
                  for name in self.args.fake_cb_only}
        stored_icao = self.zone_positions.get(zone_num, (None, None, None))[2]
        return bool({stored_icao, snap_icao} & (wanted - {None}))

    def _cb_removed_near_airport(self, lat: float, lon: float) -> Optional[str]:
        """Return the dep/dst airport whose CB avoidance removes a CB at (lat, lon), else None.

        With cb_airport_mode "remove", a zone centred within
        _CB_AIRPORT_CLEARANCE_NM + _CB_TO_ZONE_OFFSET_NM of the FMC
        departure or destination airport gets no CB, and the METAR PSX gets
        for it no TS/CB (_strip_convective_metar()), so no CB can come within
        _CB_AIRPORT_CLEARANCE_NM of the airport. The zone stays where it is,
        so the airport keeps its real weather.
        """
        if self._cb_airport_mode != "remove":
            return None
        for icao in (self.fmc_dep_icao, self.fmc_dst_icao):
            if icao and icao in self.airports:
                alat, alon = self.airports[icao]
                if (self._dist_nm(lat, lon, alat, alon) <=
                        _CB_AIRPORT_CLEARANCE_NM + _CB_TO_ZONE_OFFSET_NM):
                    return icao
        return None

    def _handles_cb_sigmets(self) -> bool:
        """Return True if frankenweather handles TS and/or TC SIGMETs itself (SIGMETs page)."""
        return any(self._sigmet_handle.get(h) for h in _fw_sigmet.CB_HAZARDS)

    def _is_cb_free_zone(self, zone_num: int) -> bool:
        """Return True if zone_num must not get a CB (see _maintain_anchor_zone())."""
        pos = self._cb_free_zones.get(zone_num)
        cur = self.zone_positions.get(zone_num)
        if pos is None or cur is None:
            return False
        if tuple(cur[:2]) != pos:
            del self._cb_free_zones[zone_num]  # moved elsewhere since: an ordinary zone again
            return False
        return True

    def _maintain_anchor_zone(self) -> bool:
        """Keep a CB-free "anchor" zone behind the aircraft when needed; True if a zone moved.

        PSX takes the local weather (QNH, wind) from the nearest zone. When
        every zone is more than _ANCHOR_TRIGGER_NM away (e.g. zones moved far
        ahead or into TS/TC SIGMET areas), one becomes the anchor,
        _ANCHOR_BEHIND_NM behind the aircraft: CB-free, out of sight of the
        radar, and too far from the zones ahead to shrink their CBs. It
        moves back there once it's _ANCHOR_RELOCATE_NM behind, and is an
        ordinary zone again as soon as another zone is within
        _ANCHOR_TRIGGER_NM (e.g. the departure or destination airport's), or
        in maneuvering mode, where the zones are all around the aircraft.
        It used to be kept 40-50 nm ahead on track (with --sigmet only),
        which kept the track ahead CB-free even inside a TS/TC area
        (flight test 2026-10-25, TC VANCE).
        """
        anchor = self._anchor_zone
        if anchor is not None and not self._is_cb_free_zone(anchor):
            anchor = self._anchor_zone = None  # moved by something else
        if self.ac_lat is None or self.ac_hdg is None or not self.zone_positions:
            return False
        others = [self._dist_nm(self.ac_lat, self.ac_lon, lat, lon)
                  for zn, (lat, lon, _) in self.zone_positions.items() if zn != anchor]
        if self._maneuvering or (others and min(others) <= _ANCHOR_TRIGGER_NM):
            if anchor is not None:
                self._release_anchor("maneuvering" if self._maneuvering else
                                     f"another zone within {_ANCHOR_TRIGGER_NM:.0f}nm")
            return False
        if anchor is not None:
            lat, lon, _ = self.zone_positions[anchor]
            if self._dist_nm(self.ac_lat, self.ac_lon, lat, lon) <= _ANCHOR_RELOCATE_NM:
                return False
            zone_num = anchor
        else:
            zone_num = self._expendable_for_anchor()
            if zone_num is None:
                return False
        self._move_anchor(zone_num)
        return True

    def _move_anchor(self, zone_num: int) -> None:
        """Move zone_num _ANCHOR_BEHIND_NM behind the aircraft and make it the anchor."""
        dist_m = _ANCHOR_BEHIND_NM * _NM_TO_M
        lon, lat, _ = self.geod.fwd(lons=self.ac_lon, lats=self.ac_lat,
                                    az=(self.ac_hdg + 180.0) % 360.0, dist=dist_m)
        lon = ((lon + 180.0) % 360.0) - 180.0
        old = self.zone_positions[zone_num]
        other_icaos = {icao for zn, (_, _, icao) in self.zone_positions.items() if zn != zone_num}
        icao = next(name for name in (f"X{random.randint(100, 999):03d}" for _ in iter(int, 1))
                    if name not in other_icaos)
        self.zone_positions[zone_num] = (lat, lon, icao)
        self.zone_placement_reason[zone_num] = self._placement_desc(lat, lon, icao)
        self.zone_relocated_time[zone_num] = time.time()
        self.zone_cb_ramp.pop(zone_num, None)
        self.zone_cb_create_dist_nm[zone_num] = _ANCHOR_BEHIND_NM
        self.logger.info("Zone %d: relocated from %s @ %.3f/%.3f → %s @ %.3f/%.3f"
                         " [%.0fnm behind] (anchor zone)",
                         zone_num, old[2], old[0], old[1], icao, lat, lon, _ANCHOR_BEHIND_NM)
        self._anchor_zone = zone_num
        self._cb_free_zones = {zone_num: (lat, lon)}
        self.logger.info("Anchor zone: Zone %d, %.0fnm behind, CB-free (no zone within %.0fnm)",
                         zone_num, _ANCHOR_BEHIND_NM, _ANCHOR_TRIGGER_NM)

    def _release_anchor(self, why: str) -> None:
        """Make the anchor zone an ordinary zone again (it may get a CB)."""
        self.logger.info("Anchor zone: Zone %d is an ordinary zone again (%s)",
                         self._anchor_zone, why)
        self._anchor_zone = None
        self._cb_free_zones.clear()

    def _expendable_for_anchor(self) -> Optional[int]:
        """Return the zone to turn into a new anchor zone, or None.

        Never the nearest zone, PSX's focused zone, the current anchor or a
        dep/dst airport zone. Preferred: a zone behind the aircraft (the
        farthest), then one ahead without a CB (the farthest) -- a CB that
        may be on the radar is only taken away if nothing else is left.
        """
        arpt_icaos = {icao for icao, _, _ in self._arpt_coverage_needed()}
        protected = {self._nearest_zone_num(), self.focused_zone, self._anchor_zone}
        best, best_key = None, None
        for zone_num, (lat, lon, icao) in self.zone_positions.items():
            if (zone_num in protected or icao in arpt_icaos or
                    self._cb_turb_in_reach(zone_num) is not None):
                continue
            az, _, dist_m = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            behind = abs((az - self.ac_hdg + 180) % 360 - 180) > 90.0
            wx = (self.zone_wx.get(zone_num) or "").split(";")
            no_cb = len(wx) < 10 or wx[9] == "0"
            key = (behind, no_cb, dist_m)
            if best_key is None or key > best_key:
                best, best_key = zone_num, key
        return best

    def _place_sigmet_zones(self) -> bool:
        """--sigmet: move a zone into a nearby TS/TC SIGMET area; True if one moved.

        TS/TC areas whose nearest point is within _SIGMET_ZONE_RANGE_NM and
        not behind the aircraft, nearest first, each get up to
        _SIGMET_ZONES_PER_TS (TC: _SIGMET_ZONES_PER_TC, clustered around the
        cyclone's centre) zones. New positions are inside the area, at least
        _CB_SQUEEZE_MIN_FWD_NM from the aircraft (so a CB never appears
        close by), preferably ahead and as close as that allows. Only zones
        that can go without anyone noticing are used: behind the aircraft,
        or ahead without a CB -- never the anchor, the nearest or focused
        zone, a dep/dst airport zone, or a zone already in a TS/TC area.
        Moves at most one zone per _SIGMET_PLACE_INTERVAL_S, except right
        after taking over the SIGMETs (_sigmet_place_burst), when every
        zone wanted is placed at once.
        """
        if (not self._own_sigmets_applied or not self._handles_cb_sigmets() or
                self.ac_lat is None or self.ac_hdg is None):
            return False
        if self._sigmet_place_burst:
            self._sigmet_place_burst = False
            moved = False
            while self._place_one_sigmet_zone():
                moved = True
            return moved
        if time.time() - self._sigmet_place_last < _SIGMET_PLACE_INTERVAL_S:
            return False
        return self._place_one_sigmet_zone()

    def _place_one_sigmet_zone(self) -> bool:  # pylint: disable=too-many-locals
        """Move one zone into a TS/TC SIGMET area that wants one; True if moved."""
        now = time.time()
        areas = []
        for entry in self._own_sigmet_entries:
            if not entry["ok"] or entry["hazard"] not in _fw_sigmet.CB_HAZARDS:
                continue
            if not self._sigmet_handle.get(entry["hazard"]):
                continue  # PSX gets this type (SIGMETs page)
            dist, brg = _fw_sigmet.entry_distance(self.ac_lat, self.ac_lon, entry)
            if dist > _SIGMET_ZONE_RANGE_NM:
                continue
            if brg is not None and abs((brg - self.ac_hdg + 180) % 360 - 180) > 90.0:
                continue  # behind the aircraft
            areas.append((dist, entry))
        if not areas:
            return False
        areas.sort(key=lambda a: a[0])
        zone_num = self._expendable_for_sigmet()
        if zone_num is None:
            return False
        for _, entry in areas:
            inside = [zn for zn, (lat, lon, _) in self.zone_positions.items()
                      if _fw_sigmet.entry_distance(lat, lon, entry)[0] == 0.0]
            want = (_SIGMET_ZONES_PER_TC if entry["hazard"] == "TC"
                    else _SIGMET_ZONES_PER_TS)
            if len(inside) >= want:
                continue
            pos = self._sigmet_zone_position(entry, exclude_zone=zone_num)
            if pos is None:
                continue
            lat, lon, dist_nm, brg = pos
            old = self.zone_positions[zone_num]
            icao = f"X{random.randint(100, 999):03d}"
            label = f"{entry['fir']} {entry['series']} {entry['qualifier']}".strip()
            self.zone_positions[zone_num] = (lat, lon, icao)
            self.zone_placement_reason[zone_num] = (
                f"{self._placement_desc(lat, lon, icao)}, inside {entry['hazard']} SIGMET {label}")
            self.zone_relocated_time[zone_num] = now
            self.zone_cb_ramp.pop(zone_num, None)
            self.zone_cb_create_dist_nm[zone_num] = dist_nm
            self._sigmet_place_last = now
            self.logger.info("Zone %d: relocated from %s @ %.3f/%.3f → %s @ %.3f/%.3f"
                             " [%.0f°/%.0fnm] (inside %s SIGMET %s)",
                             zone_num, old[2], old[0], old[1], icao, lat, lon, brg,
                             dist_nm, entry["hazard"], label)
            return True
        return False

    def _expendable_for_sigmet(self) -> Optional[int]:
        """Return the zone _place_sigmet_zones() may move, or None (see there)."""
        arpt_icaos = {icao for icao, _, _ in self._arpt_coverage_needed()}
        protected = {self._nearest_zone_num(), self.focused_zone, self._anchor_zone}
        best, best_key = None, None
        for zone_num, (lat, lon, icao) in self.zone_positions.items():
            if (zone_num in protected or icao in arpt_icaos or
                    self._is_cb_free_zone(zone_num) or
                    self._cb_turb_in_reach(zone_num) is not None):
                continue
            if any(_point_in_polygon(lat, lon, sig['polygon']) for sig in self.ts_sigmets):
                continue
            az, _, dist_m = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            behind = abs((az - self.ac_hdg + 180) % 360 - 180) > 90.0
            wx = (self.zone_wx.get(zone_num) or "").split(";")
            has_cb = len(wx) > 9 and wx[9] not in ("", "0")
            if not behind and has_cb:
                continue  # a CB that may be on the radar never just vanishes
            key = (behind, dist_m)
            if best_key is None or key > best_key:
                best, best_key = zone_num, key
        return best

    def _sigmet_zone_position(self, entry: dict, exclude_zone: int) -> Optional[tuple]:
        """Return (lat, lon, dist_nm, bearing) for a new zone inside a SIGMET area, or None.

        See _place_sigmet_zones(). For a TC the points nearest the
        cyclone's centre (if the SIGMET gives one) come first, with a
        tighter zone separation, so its zones form a cluster.
        """
        points = []
        for poly in entry["polygons"]:
            points += _fw_sigmet.points_in_polygon(
                self.ac_lat, self.ac_lon, poly, _SIGMET_ZONE_RANGE_NM)
        # never behind the aircraft: the relocation moved such a zone away 3 s later,
        # and the next placement put one back, every minute for 7 minutes alongside a
        # TS area the aircraft had nearly passed (test sim 2026-10-29, OOMM A2)
        points = [p for p in points if p[2] >= _CB_SQUEEZE_MIN_FWD_NM and
                  abs((p[3] - self.ac_hdg + 180) % 360 - 180) <= 90.0]
        if not points:
            return None
        others = [(lat, lon) for zn, (lat, lon, _) in self.zone_positions.items()
                  if zn != exclude_zone]
        is_tc = entry["hazard"] == "TC"
        min_sep = _SIGMET_TC_CLUSTER_SEP_NM if is_tc else self.args.new_zone_notnear
        center = _fw_sigmet.tc_center(entry) if is_tc else None

        def rank(p):
            rel = abs((p[3] - self.ac_hdg + 180) % 360 - 180)
            ahead = 0 if rel <= 60.0 else 1 if rel <= 90.0 else 2
            if center is not None:
                return (self._dist_nm(p[0], p[1], *center), p[2])
            return (ahead, p[2])

        for p in sorted(points, key=rank):
            if all(self._dist_nm(p[0], p[1], o[0], o[1]) >= min_sep for o in others):
                return p
        return None

    def _ensure_arpt_zones(self) -> bool:
        """Ensure dep/dst airports within arpt_zone_dist have a dedicated zone.

        Returns True if any zone was relocated to an airport.
        """
        needed = self._arpt_coverage_needed()
        if not needed:
            return False
        covered = {zicao for _, _, zicao in self.zone_positions.values()}
        protected = covered & {icao for icao, _, _ in needed}
        moved = False
        for icao, lat, lon in needed:
            if icao in covered:
                continue
            best_zn = self._most_expendable_zone(protected)
            if best_zn is None:
                self.logger.warning("No expendable zone available for arpt %s", icao)
                continue
            old_pos = self.zone_positions[best_zn]
            self.zone_positions[best_zn] = (lat, lon, icao)
            self.zone_placement_reason[best_zn] = self._placement_desc(
                lat, lon, icao, is_arpt=True)
            self.zone_relocated_time[best_zn] = time.time()
            self.zone_cb_ramp.pop(best_zn, None)
            old_az, _, old_dm = self.geod.inv(
                self.ac_lon, self.ac_lat, old_pos[1], old_pos[0])
            new_az, _, new_dm = self.geod.inv(self.ac_lon, self.ac_lat, lon, lat)
            self.zone_cb_create_dist_nm[best_zn] = new_dm / _NM_TO_M
            self.logger.info(
                "Zone %d: relocated from %s @ %.3f/%.3f [%.0f°/%.0fnm]"
                " → %s @ %.3f/%.3f [%.0f°/%.0fnm] (dep/dst arpt)",
                best_zn, old_pos[2], old_pos[0], old_pos[1],
                old_az % 360, old_dm / _NM_TO_M,
                icao, lat, lon, new_az % 360, new_dm / _NM_TO_M)
            covered.add(icao)
            protected.add(icao)
            moved = True
        return moved

    # ------------------------------------------------------------------
    # METAR TEMPO/PROBnn trend-group CB modulation
    # ------------------------------------------------------------------

    @staticmethod
    def _tempo_oktas(seg_ts_oktas: int) -> int:
        """Pick a randomized 'on' CB oktas value for an active TEMPO segment.

        A TEMPO fluctuation doesn't always show the segment's full oktas
        value -- pick uniformly between _TEMPO_MIN_OKTAS and the segment's
        own oktas, representing that TEMPO coverage may be less than
        otherwise stated. Segments carrying only 'showers' (e.g. "TEMPO
        SHRA" with no CB sky group) have seg_ts_oktas == 0; there is
        nothing to reduce in that case.
        """
        if seg_ts_oktas <= 0:
            return 0
        lo = min(_TEMPO_MIN_OKTAS, seg_ts_oktas)
        return random.randint(lo, seg_ts_oktas)

    def _trend_cb_contribution(self, icao: str, raw_metar: str, trends: list) -> tuple:
        """Return (ts_oktas, showers) currently contributed by this station's TEMPO/PROBnn groups.

        Manages the persistent roll/flicker state in self._metar_trend_state,
        keyed by ICAO. Semantics (see the design discussion this implements):

        - A bare PROBnn segment (no TEMPO/BECMG, e.g. "PROB40 BKN030CB") is
          a sustained yes/no: rolled once per distinct METAR observation
          and, if the roll hits, applied at full value for that
          observation's entire validity (until the raw text next changes).
        - A TEMPO segment (bare, or PROBnn-gated, e.g. "PROB40 TEMPO ...")
          fluctuates: while armed (a bare TEMPO is always armed; a
          PROBnn-gated TEMPO is armed only if its own roll hits), it
          randomly toggles on/off on an irregular timer
          (_TEMPO_HOLD_MIN_S/_TEMPO_HOLD_MAX_S) rather than every ~5-minute
          refresh cycle, so the transitions don't visibly flicker in sync
          with our own refresh cadence. While "on" it contributes a
          reduced oktas value (see _tempo_oktas), not necessarily the
          segment's full stated oktas.
        - BECMG segments are excluded from the main-body observation (see
          _parse_metar()) but are not otherwise modeled here.
        """
        now = time.monotonic()
        state = self._metar_trend_state.get(icao)
        if state is None or state['raw'] != raw_metar:
            # New station, or a new observation for this station: (re-)roll
            # every PROBnn segment fresh and (re-)initialize every TEMPO
            # segment's flicker timer. A previous roll/flicker state must
            # never carry over to a different observation.
            segs = []
            for seg in trends:
                if seg['type'] == 'BECMG':
                    segs.append(None)
                    continue
                armed = (seg['prob'] is None) or (random.random() * 100 < seg['prob'])
                on = armed and seg['type'] == 'TEMPO' and random.random() < 0.5
                segs.append({
                    'armed': armed,
                    'on': on,
                    'next_toggle': now + random.uniform(_TEMPO_HOLD_MIN_S, _TEMPO_HOLD_MAX_S),
                    'oktas': self._tempo_oktas(seg['ts_oktas']) if on else 0,
                })
            state = {'raw': raw_metar, 'segs': segs}
            self._metar_trend_state[icao] = state

        ts_oktas = 0
        showers = False
        for seg, sstate in zip(trends, state['segs']):
            if sstate is None or not sstate['armed']:
                continue
            if seg['type'] == 'TEMPO':
                if now >= sstate['next_toggle']:
                    sstate['on'] = not sstate['on']
                    sstate['next_toggle'] = now + random.uniform(
                        _TEMPO_HOLD_MIN_S, _TEMPO_HOLD_MAX_S)
                    if sstate['on']:
                        sstate['oktas'] = self._tempo_oktas(seg['ts_oktas'])
                if not sstate['on']:
                    continue
                ts_oktas = max(ts_oktas, sstate['oktas'])
            else:
                # Bare PROBnn (sustained): full value for the whole
                # observation once armed, no flicker.
                ts_oktas = max(ts_oktas, seg['ts_oktas'])
            showers = showers or seg['showers']
        return ts_oktas, showers

    # ------------------------------------------------------------------
    # VATSIM METAR cache
    # ------------------------------------------------------------------

    async def _refresh_vatsim_cache(self, session: aiohttp.ClientSession) -> bool:
        """Fetch all VATSIM METARs and update the local cache. Returns success."""
        try:
            async with session.get(_VATSIM_ALL_URL,
                                   timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status != 200:
                    self.logger.warning("VATSIM METAR /all: HTTP %d", r.status)
                    return False
                text = await r.text()
                metars = _parse_vatsim_metar_lines(text)
                self.vatsim_cache = metars
                self.vatsim_cache_time = time.time()
                self.logger.info("VATSIM METAR cache refreshed: %d airports", len(metars))
                return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.warning("VATSIM METAR cache refresh failed: %s", exc)
            return False

    async def _fetch_vatsim_live(
            self, session: aiohttp.ClientSession, icaos: set) -> dict:
        """EXPERIMENTAL (--metar-live-refresh): fetch just these ICAOs, fresh.

        One batch request (metar.vatsim.net accepts a comma-separated list
        at the same path as a single station) with a short
        _VATSIM_LIVE_TIMEOUT_S timeout, so a slow/unresponsive VATSIM
        doesn't hold up a zone update cycle. Returns {} on any failure or
        timeout -- callers fall back to whatever cached METAR they already
        had, which is the whole point: this only ever refines an
        already-selected zone/airport, never decides placement.
        """
        if not icaos:
            return {}
        url = _VATSIM_LIVE_URL.format(icaos=",".join(sorted(icaos)))
        try:
            async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=_VATSIM_LIVE_TIMEOUT_S)) as r:
                if r.status != 200:
                    self.logger.warning("VATSIM METAR live fetch: HTTP %d", r.status)
                    return {}
                text = await r.text()
                metars = _parse_vatsim_metar_lines(text)
                self.logger.debug(
                    "VATSIM METAR live fetch: %d/%d requested stations returned",
                    len(metars), len(icaos))
                return metars
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.warning("VATSIM METAR live fetch failed: %s", exc)
            return {}

    async def _refresh_noaa_metar_cache(self, session: aiohttp.ClientSession) -> bool:
        """Fetch NOAA's current-hour METAR cycle file and update the local cache.

        Used both as the secondary source's periodic refresh (see
        --primary-metar-source) and by _apply_metar_fallback() when the
        primary cache doesn't cover enough nearby stations -- NOAA's network
        is far broader (every station reporting anywhere, not just VATSIM
        ATC/pilot-fed ones). Each file is two lines per station: an
        observation timestamp, then the raw METAR text itself. Returns
        success.

        Reads the previous hour's file as well as the current one: early in
        the hour the current file holds only a handful of stations. Each
        file repeats stations many times and is NOT in observation-time
        order (see _noaa_metar_better()), so the last line seen for a
        station is not necessarily its current report.
        """
        now = datetime.now(timezone.utc)
        texts = []
        for hours_ago in (1, 0):
            hour = (now - timedelta(hours=hours_ago)).hour
            url = _NOAA_METAR_CYCLE_URL.format(hour=hour)
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=30)) as r:
                    if r.status != 200:
                        self.logger.warning(
                            "NOAA METAR cycle fetch (%02dZ): HTTP %d", hour, r.status)
                        return False
                    # NOAA's cycle files are a raw relay of every station's
                    # report and occasionally contain non-UTF-8 bytes (seen
                    # live: 0xed mid-file), which made r.text() raise and
                    # discard the whole file. One garbled report must not cost
                    # every other station -- replace undecodable bytes instead.
                    texts.append((await r.read()).decode("utf-8", errors="replace"))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.logger.warning("NOAA METAR cycle fetch (%02dZ) failed: %s", hour, exc)
                return False
        metars: dict = {}
        for text in texts:
            for line in text.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if parts:
                    icao = parts[0]
                    if len(icao) == 4 and icao.upper() == icao and icao.isalpha() and (
                            _noaa_metar_better(line, metars.get(icao), now)):
                        metars[icao] = line
        self.noaa_metar_cache = metars
        self.noaa_metar_cache_time = time.time()
        self.logger.info("NOAA METAR cache refreshed: %d stations", len(metars))
        return True

    def _metar_lookup_cache(self, *, apply_vatsim_failure_diag: bool = False) -> dict:
        """Return the VATSIM+NOAA METAR caches merged, icao -> raw METAR line.

        Precedence on overlap follows --primary-metar-source (experimental,
        default 'vatsim'). Outside the full Open-Meteo-outage fallback path
        (_apply_metar_fallback(), which always refreshes and consults both
        caches directly), only the primary source is refreshed on the normal
        30-minute schedule -- see _refresh_primary_metar_cache() for how the
        secondary source gets fetched as a stand-in only when the primary's
        own refresh fails, and stops being needed again as soon as the
        primary succeeds (its fresher data then simply wins the precedence
        merge below). At the default setting with a healthy VATSIM, this is
        exactly equivalent to self.vatsim_cache alone.

        apply_vatsim_failure_diag: honor _diag_simulate_vatsim_failure. Only
        _nearest_metar_stations() (the METAR-fallback path) passes True --
        the normal per-zone METAR matching and the D-ATIS override
        deliberately keep using the real cache regardless of that diagnostic.
        """
        vatsim = ({} if apply_vatsim_failure_diag and self._diag_simulate_vatsim_failure
                  else self.vatsim_cache)
        if self.args.primary_metar_source == 'noaa':
            combined = dict(vatsim)
            combined.update(self.noaa_metar_cache)
        else:
            combined = dict(self.noaa_metar_cache)
            combined.update(vatsim)
        return combined

    def _metar_source_label(self, icao: str, raw: str) -> str:
        """Return "VATSIM" or "NOAA", whichever cache actually holds raw for icao."""
        if self.noaa_metar_cache.get(icao) == raw and self.vatsim_cache.get(icao) != raw:
            return "NOAA"
        return "VATSIM"

    async def _refresh_primary_metar_cache(self, session: aiohttp.ClientSession) -> None:
        """Refresh --primary-metar-source's cache every 30 min; fail over symmetrically.

        Only the configured primary source is fetched on the normal
        schedule -- not both unconditionally. If that fetch fails (HTTP
        error or exception, not just "cache still fresh enough to skip"),
        immediately fetch the secondary source too, as a stand-in until the
        primary recovers. No extra state needs to be tracked for the
        recovery side: once the primary's own refresh succeeds again, its
        (now fresher) data simply wins the precedence merge in
        _metar_lookup_cache() over whatever the secondary was last asked to
        hold, so things fall back to the primary automatically.
        """
        if self.args.primary_metar_source == 'noaa':
            primary, secondary = self._refresh_noaa_metar_cache, self._refresh_vatsim_cache
            primary_cache_time, primary_max_age_s = (
                self.noaa_metar_cache_time, _NOAA_METAR_CACHE_MAX_S)
        else:
            primary, secondary = self._refresh_vatsim_cache, self._refresh_noaa_metar_cache
            primary_cache_time, primary_max_age_s = (
                self.vatsim_cache_time, _VATSIM_CACHE_MAX_S)
        if (time.time() - primary_cache_time > primary_max_age_s and
                not await primary(session)):
            self.logger.warning(
                "Primary METAR source (%s) refresh failed -- falling back to "
                "secondary source as a stand-in", self.args.primary_metar_source)
            await secondary(session)

    def _nearest_metar_stations(self, lat: float, lon: float, n: int) -> list:
        """Return up to n (icao, lat, lon, dist_nm, raw_metar), nearest first.

        Draws from the VATSIM and NOAA caches combined (see
        _metar_lookup_cache() for precedence), cross-referenced against the
        known airport-position database. Used only by the METAR-fallback
        path, so distance is unbounded -- the whole point is to find the
        best available data, however far away. See
        _diag_simulate_vatsim_failure for how to exercise the NOAA-only /
        no-data-at-all branches on demand.
        """
        combined = self._metar_lookup_cache(apply_vatsim_failure_diag=True)
        candidates = []
        for icao, raw in combined.items():
            pos = self.airports.get(icao)
            if pos is None:
                continue
            alat, alon = pos
            dist = self._dist_nm(lat, lon, alat, alon)
            candidates.append((dist, icao, alat, alon, raw))
        candidates.sort(key=lambda c: c[0])
        return [(icao, alat, alon, dist, raw) for dist, icao, alat, alon, raw in candidates[:n]]

    def _nearest_unused_metar_station(self, lat: float, lon: float,
                                      used_icaos: set) -> Optional[tuple]:
        """Return the nearest (icao, lat, lon, dist_nm, raw_metar) not in used_icaos.

        Only stations with a fresh METAR count.
        """
        for station in self._nearest_metar_stations(lat, lon, len(used_icaos) + 50):
            if station[0] not in used_icaos and _metar_is_fresh(station[4]):
                return station
        return None

    def _apply_metar_to_wxbasic(self, icao: str, raw: str, dist_nm: float) -> None:
        """Ground WxBasic's QNH/temp in the single globally-nearest METAR.

        WxBasic is the "no zone nearby" background/planet weather -- it
        should never be left at whatever it happened to be (or at PSX's own
        synthetic default) when real station data exists anywhere at all,
        however far away. Only QNH and temperature are overridden; every
        other field is left as PSX last had it, since we have no real basis
        for guessing wind/cloud/visibility that far from the actual station.
        """
        parsed = _parse_metar(raw)
        current = self.psx.get("WxBasic") if self.psx else None
        current_parts = current.split(';') if current else []
        base = list(current_parts) if len(current_parts) >= 24 else list(_WX_DEFAULTS)
        base[22] = str(int(round(parsed['temp_c'])))
        base[23] = str(_hpa_to_psx_qnh(parsed['qnh_hpa']))
        wx_str = ";".join(base)
        if not self._wxbasic_writable():
            return
        self._write_wxbasic(wx_str)
        self.logger.info(
            "METAR fallback: WxBasic QNH/temp set from %s (%.0fnm away)", icao, dist_nm)

    async def _apply_metar_fallback(  # pylint: disable=too-many-locals
            self, session: aiohttp.ClientSession) -> None:
        """Take over all 7 zones + WxBasic from cached METAR text directly.

        Entered from _update_zones() once _zones_all_stale() says the
        existing (frozen) zone weather is no longer geographically relevant
        during a persistent Open-Meteo outage. Positions each zone at one of
        the nearest cached VATSIM/NOAA METAR stations (however far away) and
        pushes the raw METAR text straight into PSX's native Metar<N> field
        (Qs343-Qs349) -- confirmed live 2026-09-10 that PSX's own parser
        correctly derives wind/cloud/CB/QNH/temp from it, for both ICAO/hPa
        and US/inHg METAR formats, with no Open-Meteo/CAPE data needed at
        all. Deliberately never hands control to PSX's own WxAutoSet -- see
        project_frankenweather_altitude_jump memory for why PSX's own
        fallback beyond real METAR coverage is essentially random.
        """
        stations = self._nearest_metar_stations(
            self.ac_lat, self.ac_lon, _METAR_FALLBACK_ZONE_COUNT)
        if (len(stations) < _METAR_FALLBACK_ZONE_COUNT and
                time.time() - self.noaa_metar_cache_time > _NOAA_METAR_CACHE_MAX_S):
            await self._refresh_noaa_metar_cache(session)
            stations = self._nearest_metar_stations(
                self.ac_lat, self.ac_lon, _METAR_FALLBACK_ZONE_COUNT)
        if not stations:
            self.logger.warning(
                "METAR fallback: no METAR station with known coordinates in "
                "the VATSIM or NOAA cache — keeping existing (stale) zone weather")
            return
        self._inject_wx_slow_transit()
        self.zone_positions = {}
        self.zone_placement_reason = {}
        now = time.time()
        month = datetime.now(timezone.utc).month
        for i, (icao, lat, lon, dist_nm, raw) in enumerate(stations):
            zone_num = i + 1
            self.zone_positions[zone_num] = (lat, lon, icao)
            self.zone_placement_reason[zone_num] = f"METAR fallback: {icao} ({dist_nm:.0f}nm)"
            self.zone_relocated_time[zone_num] = now
            wxmode = build_wxmode_string(lat, lon, _airport_elev_m(icao, 0.0), month, icao)
            self.zone_mode[zone_num] = wxmode
            self.psx_send_and_set(f"WxMode{zone_num}", wxmode)
            self.psx_send_and_set(f"Metar{zone_num}", raw)
            self.zone_is_metar[zone_num] = True
            self.zone_metar_source[zone_num] = self._metar_source_label(icao, raw)
            self.zone_reason[zone_num] = f"VATSIM/NOAA {icao} (METAR fallback, {dist_nm:.0f}nm)"
        nearest_icao, _, _, nearest_dist, nearest_raw = stations[0]
        self._apply_metar_to_wxbasic(nearest_icao, nearest_raw, nearest_dist)
        if not self._metar_fallback_active:
            self.logger.warning(
                "METAR fallback ACTIVE: Open-Meteo unavailable, positioned %d zone(s) "
                "on nearest cached METAR stations (nearest %s at %.0fnm)",
                len(stations), nearest_icao, nearest_dist)
        self._metar_fallback_active = True
        self._state_changed_event.set()

    # ------------------------------------------------------------------
    # D-ATIS (MetarsUp / Qs464) override
    # ------------------------------------------------------------------
    #
    # PSX's ACARS "ATIS REQUEST" page (6R = SEND) composes and broadcasts
    # MetarsUp itself, ~5s after being sent, from its own local weather
    # zones or its last two downloaded METAR world files -- neither of
    # which reflects live VATSIM data, and airports outside those sources
    # come back as "<ICAO> UNAVAIL". Since we already hold the full live
    # VATSIM METAR feed (self.vatsim_cache), we replace PSX's answer with
    # our own whenever it contains an UNAVAIL, using VATSIM data for every
    # requested airport (not just the ones PSX itself couldn't find) so
    # the reply is internally consistent -- see the PAIB thread this was
    # designed against for the exact wire format PSX uses.

    @staticmethod
    def _datis_requested_icaos(value: str) -> list:
        """Extract the list of requested ICAOs from a MetarsUp value.

        Works whether each per-airport block found a report ("<ICAO> M
        <date>^...") or not ("<ICAO> UNAVAIL"), since in both cases the
        block starts with the bare ICAO as its first whitespace-delimited
        token.
        """
        icaos = []
        for block in value.split(_DATIS_BLOCK_SEP):
            block = block.strip()
            if not block:
                continue
            icao = block.split(None, 1)[0]
            if len(icao) == 4 and icao.isalpha() and icao.isupper():
                icaos.append(icao)
        return icaos

    def _compose_datis_block(self, icao: str) -> str:
        """Build one airport's MetarsUp block.

        The VATSIM METAR if we have one, wrapped to PSX's 24-char line
        limit, else the same "UNAVAIL" text PSX itself would show.
        """
        raw = self.vatsim_cache.get(icao)
        if raw is None:
            lines = [f"{icao} UNAVAIL"]
        else:
            lines = textwrap.wrap(raw, width=_DATIS_LINE_MAX_CHARS)
        return "^".join(lines) + _DATIS_BLOCK_SEP

    def _compose_datis_reply(self, value: str) -> str:
        """Return a MetarsUp value using VATSIM data for every requested airport.

        Returns `value` unchanged if none of the requested airports could
        be parsed out of it (defensive -- should not happen given PSX's
        own format).
        """
        icaos = self._datis_requested_icaos(value)
        if not icaos:
            return value
        return "".join(self._compose_datis_block(icao) for icao in icaos)

    def _handle_metars_up(self, _key: str, value: str) -> None:
        """React to PSX's own MetarsUp (Qs464) D-ATIS reply.

        Overrides it with live VATSIM data for any requested airport PSX
        itself couldn't find -- see the module comment above
        _datis_requested_icaos() for why.
        """
        if self.args.no_datis_override:
            return
        if self._should_skip_wx_update():
            return
        if 'UNAVAIL' not in value:
            return
        if value == self._datis_last_written:
            # Our own previous write, reflected back via psx_send_and_set()'s
            # local cache update (or a network echo) -- not a new request.
            return
        new_value = self._compose_datis_reply(value)
        if new_value == value:
            return
        self._datis_last_written = new_value
        self.logger.info("D-ATIS: overriding PSX MetarsUp reply with VATSIM data: %s",
                         self._datis_requested_icaos(value))
        self.psx_send_and_set("MetarsUp", new_value)

    @staticmethod
    def _print_requested_unavail_icaos(value: str) -> list:
        """Return ICAOs from a Qs119 printout whose block was exactly "<ICAO> UNAVAIL"."""
        icaos = []
        for block in value.split(_DATIS_BLOCK_SEP):
            parts = block.strip().split(None, 1)
            if len(parts) != 2:
                continue
            icao, rest = parts
            if (len(icao) == 4 and icao.isalpha() and icao.isupper() and
                    rest.strip() == "UNAVAIL"):
                icaos.append(icao)
        return icaos

    def _compose_altn_supplement(self, icaos: list) -> str:
        """Build a supplemental printer message with VATSIM weather for icaos."""
        return _ALTN_SUPPLEMENT_HEADER + "".join(
            self._compose_datis_block(icao) for icao in icaos)

    def _handle_printer_text(self, _key: str, value: str) -> None:
        """React to a new PSX printer message (Qs119).

        PSX's own FMC ALTN weather-request feature prints its reply in the
        same "<ICAO> UNAVAIL"/^^-block format as D-ATIS (see
        _datis_requested_icaos()), confirmed by live testing -- but unlike
        D-ATIS it never touches Qs464, so there's no earlier point to
        intercept it (Aerowinx's own guidance: print a second, corrective
        message after it instead). A normal D-ATIS printout never reaches
        here with an UNAVAIL airport we have data for, since
        _handle_metars_up() already fixes Qs464 before PSX prints it -- so
        in practice this only fires for ALTN requests.
        """
        if self.args.no_altn_supplement:
            return
        if self._should_skip_wx_update():
            return
        if not value.startswith(_DATIS_PRINT_HEADER) or 'UNAVAIL' not in value:
            return
        if value == self._altn_supplement_last_written:
            # Our own previous write, reflected back through
            # psx_send_and_set()'s local cache update -- not a new printout.
            return
        icaos = [icao for icao in self._print_requested_unavail_icaos(value)
                 if icao in self.vatsim_cache]
        if not icaos:
            return
        supplement = self._compose_altn_supplement(icaos)
        self._altn_supplement_last_written = supplement
        self.logger.info("ALTN weather: printing VATSIM supplement for %s", icaos)
        self.psx_send_and_set("PrinterText", supplement)

    # ------------------------------------------------------------------
    # Open-Meteo fetch
    # ------------------------------------------------------------------

    async def _fetch_om_batch(self, positions: list) -> tuple:
        """Return Open-Meteo weather for each position: (entries, fetch_failed, oldest_s).

        entries has one dict per position, or None where there's no data at
        all. Positions with a cache entry younger than _OM_ZONE_FRESH_S are
        served from the on-disk cache (_OM_ZONE_CACHE_PATH), the rest are
        fetched in one request. Cache keys are bucketed to a coarse grid
        (_om_zone_cache_key(), matching frankenturb's CAPE/wind fetcher
        granularity) rather than exact position, so a relocated zone -- or a
        different zone landing nearby, common given the ~150-250nm forward
        placement cone -- also counts as a hit. If the fetch fails, any
        older cache entry (up to _OM_ZONE_KEEP_S) is used instead, so the
        zones can still be updated. fetch_failed is True if a fetch was
        needed and failed; oldest_s is the age of the oldest entry used.
        """
        cached = [self._om_zone_cache.get_entry(_om_zone_cache_key(p[0], p[1]))
                  for p in positions]
        entries = [c[0] if c else None for c in cached]
        ages = [c[1] if c else None for c in cached]
        missing = [i for i, age in enumerate(ages) if age is None or age > _OM_ZONE_FRESH_S]
        if not missing:
            return entries, False, max(ages, default=0.0)
        fetched = await self._fetch_om_positions([positions[i] for i in missing])
        if fetched is not None:
            self._om_zone_cache.set_many({
                _om_zone_cache_key(positions[i][0], positions[i][1]): entry
                for i, entry in zip(missing, fetched)})
            for i, entry in zip(missing, fetched):
                entries[i], ages[i] = entry, 0.0
        oldest = max((a for a in ages if a is not None), default=0.0)
        return entries, fetched is None, oldest

    async def _fetch_om_positions(self, positions: list) -> Optional[list]:
        """Fetch Open-Meteo current weather for positions; a list of dicts, or None if it failed."""
        if self._diag_simulate_om_failure:
            self.logger.warning("Open-Meteo fetch simulated as failed (DIAG)")
            return None
        params = {
            "latitude": ','.join(f"{p[0]:.4f}" for p in positions),
            "longitude": ','.join(f"{max(-180.0, min(180.0, p[1])):.4f}" for p in positions),
            "current": _OM_VARS, "wind_speed_unit": "kn", "timezone": "UTC",
            # precipitation: om_cb_fields()'s fallback for WMO thunderstorms
            "hourly": "cape,convective_inhibition,temperature_2m,dewpoint_2m,showers,precipitation",
            "forecast_days": 1,
        }
        body = await asyncio.get_running_loop().run_in_executor(
            None, _OM_CLIENT.get, params, "zones", self.args.om_proxy)
        if body is None:
            return None
        if isinstance(body, dict):
            body = [body]
        if len(body) != len(positions):
            self.logger.warning("Open-Meteo returned %d results for %d positions",
                                len(body), len(positions))
            return None
        return body

    @staticmethod
    def _enroute_hpa_levels(fl_list_ft: list) -> list:
        """Return the sorted unique Open-Meteo pressure levels needed for fl_list_ft."""
        return sorted({_nearest_om_hpa(fl) for fl in fl_list_ft}, reverse=True)

    async def _fetch_enroute_om_batch(  # pylint: disable=too-many-locals,too-many-branches
            self, targets: list) -> None:
        """Fetch Open-Meteo pressure-level wind/OAT for each (index, name, lat, lon) target.

        Updates self._waypoint_om_wind[index] with {fl_ft: (dir_deg, spd_kt, oat_c)}
        for every level in _current_fl_list_ft(), using the nearest Open-Meteo
        pressure level's hourly forecast for the current UTC hour. A model is
        pinned explicitly (gfs_seamless) since pressure-level fields are not
        guaranteed to be populated under the default best-match model blend.

        Each target's 2-day download is kept in the on-disk cache (see
        _OM_ENROUTE_CACHE_PATH), keyed by position and the current
        flight-level set (since that determines which pressure levels were
        requested), and served from it for _OM_ENROUTE_FRESH_S. Only the
        targets without fresh data are fetched; if that fails, older data
        that still covers the current hour is used.
        """
        if not targets:
            return
        fl_list_ft = self._current_fl_list_ft()
        hpa_levels = self._enroute_hpa_levels(fl_list_ft)
        levels_sig = "-".join(str(h) for h in hpa_levels)
        now_ts = time.time()

        def _cache_key(lat: float, lon: float) -> str:
            return f"{_om_pos_key(lat, lon)}|{levels_sig}"

        cached = [self._om_enroute_cache.get_entry(_cache_key(t[2], t[3])) for t in targets]
        cached = [c if c and _om_hour_index(c[0], now_ts) is not None else None for c in cached]
        body = [c[0] if c else None for c in cached]
        missing = [i for i, c in enumerate(cached) if c is None or c[1] > _OM_ENROUTE_FRESH_S]
        if missing:
            params = {
                "latitude": ','.join(f"{targets[i][2]:.4f}" for i in missing),
                "longitude": ','.join(
                    f"{max(-180.0, min(180.0, targets[i][3])):.4f}" for i in missing),
                "hourly": ",".join(f"{var}_{hpa}hPa" for hpa in hpa_levels
                                   for var in ("temperature", "winddirection", "windspeed")),
                "wind_speed_unit": "kn", "timeformat": "unixtime",
                "models": "gfs_seamless", "forecast_days": 2,
            }
            fetched = await asyncio.get_running_loop().run_in_executor(
                None, _OM_CLIENT.get, params, "enroute", self.args.om_proxy)
            if isinstance(fetched, dict):
                fetched = [fetched]
            if fetched and len(fetched) == len(missing):
                self._om_enroute_cache.set_many({
                    _cache_key(targets[i][2], targets[i][3]): loc
                    for i, loc in zip(missing, fetched)})
                for i, loc in zip(missing, fetched):
                    body[i] = loc
            else:
                stale = sum(1 for i in missing if body[i] is not None)
                self.logger.warning(
                    "Enroute wind: Open-Meteo fetch failed — %d of %d waypoint(s) from "
                    "older cached data, %d without data", stale, len(missing),
                    len(missing) - stale)

        fl_to_hpa = {fl: _nearest_om_hpa(fl) for fl in fl_list_ft}
        for (idx, name, _lat, _lon), loc in zip(targets, body):
            hi = _om_hour_index(loc, now_ts) if loc else None
            if hi is None:
                continue
            hourly = loc.get("hourly") or {}
            levels = {}
            for fl, hpa in fl_to_hpa.items():
                try:
                    spd = hourly[f"windspeed_{hpa}hPa"][hi]
                    wdir = hourly[f"winddirection_{hpa}hPa"][hi]
                    temp = hourly[f"temperature_{hpa}hPa"][hi]
                except (KeyError, IndexError, TypeError):
                    continue
                if spd is None or wdir is None or temp is None:
                    continue
                levels[fl] = (float(wdir), float(spd), float(temp))
            if levels:
                self._waypoint_om_wind[idx] = levels
            else:
                self.logger.warning(
                    "Enroute wind: no Open-Meteo pressure-level data for %s", name)

    # ------------------------------------------------------------------
    # Radar (RainViewer) helpers
    # ------------------------------------------------------------------

    async def _fetch_rv_frame(self, session: aiohttp.ClientSession) -> None:
        """Refresh the RainViewer radar frame path; clear tile cache when frame changes."""
        try:
            async with session.get(_RV_API, timeout=_RV_TIMEOUT) as resp:
                if resp.status != 200:
                    return
                data = await resp.json(content_type=None)
            past = (data.get('radar') or {}).get('past') or []
            path = past[-1].get('path') if past else None
            if path and path != self._rv_frame_path:
                self._rv_frame_path = path
                self._rv_tile_cache.clear()
                self.logger.debug("RainViewer: new frame %s", path)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.debug("RainViewer frame fetch failed: %s", exc)

    async def _fetch_rv_tiles(self, session: aiohttp.ClientSession,
                              positions: list) -> None:
        """Fetch and cache RainViewer tiles for the given (lat, lon) positions."""
        if not _HAS_PIL or self._rv_frame_path is None:
            return
        needed = {_rv_tile_xy(lat, lon, _RV_ZOOM) for lat, lon in positions}
        to_fetch = needed - set(self._rv_tile_cache)
        if not to_fetch:
            return

        async def _get_tile(tx: int, ty: int) -> tuple:
            url = (f"{_RV_TILE}{self._rv_frame_path}"
                   f"/{_RV_TILE_PX}/{_RV_ZOOM}/{tx}/{ty}/2/1_1.png")
            try:
                async with session.get(url, timeout=_RV_TIMEOUT) as r:
                    if r.status != 200:
                        return tx, ty, None
                    data = await r.read()
                return tx, ty, _PIL_Image.open(BytesIO(data)).convert('RGBA')
            except Exception:  # pylint: disable=broad-exception-caught
                return tx, ty, None

        results = await asyncio.gather(*[_get_tile(tx, ty) for tx, ty in to_fetch])
        for tx, ty, img in results:
            if img is not None:
                self._rv_tile_cache[(tx, ty)] = img
        self.logger.debug("RainViewer: fetched %d/%d tiles",
                          sum(1 for _, _, img in results if img is not None), len(to_fetch))

    def _rv_echo_at(self, lat: float, lon: float) -> int:
        """Return cached RainViewer echo strength 0-3 at (lat, lon), or 0 if not available."""
        if not _HAS_PIL or self._rv_frame_path is None:
            return 0
        tx, ty = _rv_tile_xy(lat, lon, _RV_ZOOM)
        img = self._rv_tile_cache.get((tx, ty))
        if img is None:
            return 0
        px, py = _rv_pixel_in_tile(lat, lon, _RV_ZOOM)
        return _rv_tile_echo(img, px, py)

    # ------------------------------------------------------------------
    # Lightning (Blitzortung) helpers
    # ------------------------------------------------------------------

    async def _fetch_bz_strikes(self, session: aiohttp.ClientSession) -> None:
        """Refresh Blitzortung strike list from the last _BZ_LOOKBACK_MIN minutes."""
        if time.monotonic() - self._bz_fetch_time < _BZ_CACHE_S:
            return
        now_utc = datetime.now(timezone.utc)
        strikes = []

        async def _get_minute(dt: datetime) -> list:
            url = (f"{_BZ_URL}/{dt.year}/{dt.month:02d}/{dt.day:02d}"
                   f"/{dt.hour:02d}/{dt.minute:02d}.json")
            try:
                async with session.get(url, timeout=_RV_TIMEOUT) as r:
                    if r.status != 200:
                        return []
                    data = await r.json(content_type=None)
                    if not isinstance(data, list):
                        return []
                    return [(float(s['lat']), float(s['lon']))
                            for s in data if 'lat' in s and 'lon' in s]
            except Exception:  # pylint: disable=broad-exception-caught
                return []

        minutes = [
            (now_utc - timedelta(minutes=offset)).replace(second=0, microsecond=0)
            for offset in range(1, _BZ_LOOKBACK_MIN + 1)
        ]
        results = await asyncio.gather(*[_get_minute(dt) for dt in minutes])
        for batch in results:
            strikes.extend(batch)

        old_count = len(self._bz_strikes)
        self._bz_strikes = strikes
        self._bz_fetch_time = time.monotonic()
        if strikes or old_count:
            self.logger.debug("Blitzortung: %d strikes (last %d min)",
                              len(strikes), _BZ_LOOKBACK_MIN)

    def _bz_near(self, lat: float, lon: float) -> bool:
        """Return True if a recent Blitzortung strike lies within _BZ_RADIUS_NM."""
        lat_tol = 2.0   # fast coarse filter before expensive _dist_nm call
        for slat, slon in self._bz_strikes:
            if abs(slat - lat) > lat_tol:
                continue
            if self._dist_nm(lat, lon, slat, slon) <= _BZ_RADIUS_NM:
                return True
        return False

    # ------------------------------------------------------------------
    # Zone update
    # ------------------------------------------------------------------

    async def _update_zones(  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
            self, session: aiohttp.ClientSession) -> None:
        """Recalculate and push all 7 weather zones to PSX."""
        positions = list(self.zone_positions[i] for i in range(1, 8))
        self.logger.info("Updating zone weather — ac=%.2f/%.2f hdg=%.0f° [%s]",
                         self.ac_lat, self.ac_lon, self.ac_hdg,
                         "MANEUVERING" if self._maneuvering else "CRUISE")

        # Refresh --primary-metar-source's cache if stale; fails over to the
        # other source as a stand-in on failure (see _refresh_primary_metar_cache()).
        await self._refresh_primary_metar_cache(session)
        metar_cache = self._metar_lookup_cache()

        # Find all airports within snapping range for each zone
        zone_candidates = [self._airports_within_nm(lat, lon, _AIRPORT_SNAP_NM)
                           for lat, lon, _ in positions]

        # Per-zone: pick the nearest airport that has a VATSIM METAR; otherwise Open-Meteo
        snap_positions: list = []
        snap_icaos: list = []
        raw_metars: list = []   # real METAR string, or None if Open-Meteo needed
        metar_sources: list = []  # "VATSIM"/"NOAA" alongside raw_metars, or None
        om_zone_idx: list = []  # indices of zones that need Open-Meteo
        used_icaos: set = set()
        locked = {i: snap[1] for i, snap in
                  ((i, self._zone_snap.get(i + 1)) for i in range(7))
                  if snap is not None and snap[0] == positions[i]}
        reserved_icaos = {icao for _, _, icao in locked.values()}
        # zone index -> (icao, age_hours) for zones where a real METAR was
        # available but discarded for being too old (see _metar_is_fresh) --
        # surfaced in zone_reason so it's visible in the web UI, not just logs.
        stale_metar_discarded: dict = {}

        for i, (lat, lon, stored_icao) in enumerate(positions):
            if (self._cb_airport_mode == "move" and stored_icao and
                    stored_icao in (self.fmc_dep_icao, self.fmc_dst_icao) and
                    stored_icao not in used_icaos):
                # positions[i] was deliberately offset from the real airport
                # by _arpt_coverage_needed() so PSX's CB (always 7nm from the
                # zone centre) can never land near the runway. Keep using the
                # real airport's own METAR/identity directly -- the normal
                # nearest-airport search below would otherwise snap straight
                # back to the airport's exact coordinates via
                # _AIRPORT_SNAP_NM and undo the offset. Weather content is
                # still fetched at the offset position (lat, lon) for
                # anything not covered by the METAR text itself (CAPE/radar/
                # lightning CB inputs), so the CB decision stays consistent
                # with the zone's actual (offset) broadcast position.
                raw = metar_cache.get(stored_icao)
                stale_age = None
                if raw is not None and not _metar_is_fresh(raw):
                    stale_age = _metar_age_hours(raw)
                    stale_metar_discarded[i] = (stored_icao, stale_age)
                    raw = None
                snap_positions.append((lat, lon))
                snap_icaos.append(stored_icao)
                raw_metars.append(raw)
                used_icaos.add(stored_icao)
                if raw is None:
                    metar_sources.append(None)
                    om_zone_idx.append(i)
                    if stale_age is not None:
                        self.logger.warning(
                            "Zone %d: discarding stale METAR for %s (%.0fh old, CB-avoidance"
                            " offset zone) — using Open-Meteo instead",
                            i + 1, stored_icao, stale_age)
                    else:
                        self.logger.debug(
                            "Zone %d: no METAR for %s (CB-avoidance offset zone) — "
                            "using Open-Meteo", i + 1, stored_icao)
                else:
                    src_label = self._metar_source_label(stored_icao, raw)
                    metar_sources.append(src_label)
                    self.logger.debug(
                        "Zone %d → %s (%s METAR, CB-avoidance offset zone)",
                        i + 1, stored_icao, src_label)
                continue
            if i in locked:
                snap_lat, snap_lon, snap_icao = locked[i]
                raw = metar_cache.get(snap_icao)
                if raw is not None and not _metar_is_fresh(raw):
                    stale_metar_discarded[i] = (snap_icao, _metar_age_hours(raw))
                    raw = None
                snap_positions.append((snap_lat, snap_lon))
                snap_icaos.append(snap_icao)
                raw_metars.append(raw)
                used_icaos.add(snap_icao)
                if raw is None:
                    metar_sources.append(None)
                    om_zone_idx.append(i)
                else:
                    metar_sources.append(self._metar_source_label(snap_icao, raw))
                continue
            skipped_stale: list = []
            for icao, alat, alon, dist_nm in zone_candidates[i]:
                if icao not in metar_cache or icao in used_icaos or icao in reserved_icaos:
                    continue
                candidate_raw = metar_cache[icao]
                if not _metar_is_fresh(candidate_raw):
                    skipped_stale.append((icao, _metar_age_hours(candidate_raw), dist_nm))
                    continue
                snap_positions.append((alat, alon))
                snap_icaos.append(icao)
                raw_metars.append(candidate_raw)
                used_icaos.add(icao)
                src_label = self._metar_source_label(icao, candidate_raw)
                metar_sources.append(src_label)
                self.logger.debug("Zone %d → %s (%s METAR, %.1fnm)",
                                  i + 1, icao, src_label, dist_nm)
                break
            else:
                if skipped_stale:
                    stale_icao, stale_age, stale_dist_nm = skipped_stale[0]
                    stale_metar_discarded[i] = (stale_icao, stale_age)
                    self.logger.warning(
                        "Zone %d: discarding stale METAR for %s (%s, %.0fnm) — "
                        "using Open-Meteo instead", i + 1, stale_icao,
                        _metar_age_text(stale_age), stale_dist_nm)
                # No (fresh) METAR anywhere nearby — snap to nearest available airport
                # anyway, use OM
                best_ap = next(
                    (ap for ap in zone_candidates[i]
                     if ap[0] not in used_icaos and ap[0] not in reserved_icaos), None)
                if best_ap is not None:
                    snap_positions.append((best_ap[1], best_ap[2]))
                    snap_icaos.append(best_ap[0])
                    raw_metars.append(None)
                    metar_sources.append(None)
                    om_zone_idx.append(i)
                    used_icaos.add(best_ap[0])
                    ac_dist = self._dist_nm(self.ac_lat, self.ac_lon, best_ap[1], best_ap[2])
                    self.logger.warning(
                        "Zone %d: no METAR for %s (%.0fnm from ac) — using Open-Meteo",
                        i + 1, best_ap[0], ac_dist)
                else:
                    snap_positions.append((lat, lon))
                    snap_icaos.append(stored_icao)
                    raw_metars.append(None)
                    metar_sources.append(None)
                    om_zone_idx.append(i)
                    used_icaos.add(stored_icao)
                    self.logger.debug(
                        "Zone %d → %s (Open-Meteo; no airports within %.0fnm)",
                        i + 1, stored_icao, _AIRPORT_SNAP_NM)
        for i in range(7):
            if i not in locked:
                self._zone_snap[i + 1] = (positions[i], (*snap_positions[i], snap_icaos[i]))

        # EXPERIMENTAL (--metar-live-refresh): try a fresher live VATSIM reading
        # for every zone that already got a real METAR from VATSIM above. Never
        # touches zone/airport selection -- that's already final by this point --
        # and silently keeps the existing cached text for any ICAO the live fetch
        # doesn't return (not in VATSIM's live data, or the fetch failed/timed out).
        if self.args.metar_live_refresh:
            live_icaos = {
                snap_icaos[i] for i in range(7)
                if raw_metars[i] is not None and metar_sources[i] == "VATSIM"
            }
            if live_icaos:
                live_metars = await self._fetch_vatsim_live(session, live_icaos)
                if live_metars:
                    self.vatsim_cache.update(live_metars)
                for i in range(7):
                    if metar_sources[i] != "VATSIM":
                        continue
                    fresh = live_metars.get(snap_icaos[i])
                    if fresh is not None and fresh != raw_metars[i]:
                        self.logger.debug(
                            "Zone %d → %s: live VATSIM METAR refresh", i + 1, snap_icaos[i])
                        raw_metars[i] = fresh

        # Fetch Open-Meteo for all 7 zones — CB always comes from OM even for METAR zones
        om_batch, fetch_failed, oldest_s = await self._fetch_om_batch(snap_positions)
        self._last_om_fetch_failed = fetch_failed
        self._om_zone_data_age_s = oldest_s
        # A zone with neither a METAR nor Open-Meteo data (Open-Meteo
        # unavailable, nothing cached) goes on the nearest METAR station
        # instead, chosen again on every update until Open-Meteo is back.
        for i in range(7):
            if raw_metars[i] is not None or om_batch[i] is not None:
                continue
            station = self._nearest_unused_metar_station(
                positions[i][0], positions[i][1], used_icaos)
            if station is None:
                continue
            icao, alat, alon, dist_nm, raw = station
            snap_positions[i] = (alat, alon)
            snap_icaos[i] = icao
            raw_metars[i] = raw
            metar_sources[i] = self._metar_source_label(icao, raw)
            used_icaos.add(icao)
            self.logger.info("Zone %d: no Open-Meteo data — using METAR station %s"
                             " (%.0fnm from the zone's position)", i + 1, icao, dist_nm)
        no_om_data = all(entry is None for entry in om_batch)
        self._zones_metar_only = no_om_data and any(raw is not None for raw in raw_metars)
        if no_om_data and all(raw is None for raw in raw_metars):
            if not self._zones_all_stale():
                # A temporary outage: the existing (frozen) zone weather is still
                # geographically relevant, so keep serving it rather than
                # repositioning onto METAR-fallback zones for no reason.
                self.logger.warning(
                    "Open-Meteo unavailable — keeping existing zone weather"
                    " (still within useful range)")
                return
            await self._apply_metar_fallback(session)
            return
        if no_om_data and self._zones_all_stale():
            await self._apply_metar_fallback(session)
            return
        if no_om_data:
            # METAR zones don't need Open-Meteo (it only refines their CBs),
            # so keep updating those; any others (no METAR station found)
            # keep their current weather.
            self.logger.warning(
                "Open-Meteo unavailable, nothing cached — updating zones from METARs only%s",
                "" if all(raw_metars) else
                f"; no data for zone(s) {[i + 1 for i, r in enumerate(raw_metars) if not r]}")
        elif fetch_failed:
            # Still update the zones (new METARs, setting changes) from the
            # cached data we have
            self.logger.warning(
                "Open-Meteo unavailable — updating zones from cached data (oldest %.0f min)%s",
                oldest_s / 60.0, "" if all(om_batch) else
                f"; no data at all for zone(s) {[i + 1 for i, e in enumerate(om_batch) if not e]}")
        elif self._metar_fallback_active:
            self._metar_fallback_active = False
            self.logger.info(
                "Open-Meteo available again — resuming normal zone placement"
                " (repositioning off the METAR-fallback stations happens on"
                " the next relocation cycle)")
            self._state_changed_event.set()
        if self._diag_qnh_offset_hpa:
            # om_batch entries may be the exact objects held in
            # self._om_zone_cache (a cache hit returns them as-is, see
            # _fetch_om_batch) -- mutate a copy, never the cached dict
            # itself, or the offset gets permanently baked into the cache.
            new_batch = []
            for entry in om_batch:
                cur = entry.get("current") if entry else None
                if cur and "pressure_msl" in cur:
                    entry = dict(entry)
                    entry["current"] = dict(cur)
                    entry["current"]["pressure_msl"] = (
                        float(cur["pressure_msl"]) + self._diag_qnh_offset_hpa)
                new_batch.append(entry)
            om_batch = new_batch
        om_by_zone: dict = {i: entry for i, entry in enumerate(om_batch[:7]) if entry}

        # Refresh radar and lightning sources; failures degrade to echo=0 / no lightning
        await self._fetch_rv_frame(session)
        await self._fetch_rv_tiles(session, snap_positions + self._rv_local_sample_points())
        await self._fetch_bz_strikes(session)

        now = datetime.now(timezone.utc)
        month = now.month

        new_modes: list = []
        new_wxs: list = []
        new_metars: list = []
        _per_zone: list = []   # per-zone CB trigger info for weather detail generation
        for i in range(7):
            lat, lon = snap_positions[i]
            icao = snap_icaos[i]
            om = om_by_zone.get(i, {})
            radar_echo = self._rv_echo_at(lat, lon)
            has_lightning = self._bz_near(lat, lon)
            cb_removed_for = self._cb_removed_near_airport(lat, lon)
            ts_oktas = 0
            has_showers_metar = False
            if raw_metars[i] is not None:
                parsed = _parse_metar(raw_metars[i])
                trend_ts_oktas, trend_showers = self._trend_cb_contribution(
                    icao, raw_metars[i], parsed.get('trends', []))
                parsed['ts_oktas'] = max(parsed['ts_oktas'], trend_ts_oktas)
                parsed['showers'] = parsed['showers'] or trend_showers
                new_modes.append(build_wxmode_string(
                    lat, lon, self._stable_zone_elev(i + 1, lat, lon, _airport_elev_m(icao, 0.0)),
                    month, icao))
                wx = metar_to_wx_string(parsed, lat, month)
                ts_oktas = parsed.get('ts_oktas', 0)
                has_showers_metar = parsed.get('showers', False)
                need_cb = ts_oktas > 0 or has_showers_metar or radar_echo >= 2 or has_lightning
                if om and need_cb:
                    refined = _apply_om_cb(
                        wx, om,
                        metar_showers=has_showers_metar,
                        metar_ts=ts_oktas > 0,
                        radar_echo=radar_echo,
                        lightning=has_lightning)
                    if ts_oktas > 0:
                        # The METAR observes a thunderstorm: Open-Meteo (CAPE,
                        # often lagging real convection) may make it bigger,
                        # never smaller. It used to replace it, e.g. WSSS
                        # +TSRA (8 oktas, 44999 ft) became 2 oktas at 24500 ft.
                        wx = _max_cb(wx, refined)
                    elif refined.split(';')[9] != '0':
                        wx = refined
                new_metars.append(_strip_convective_metar(raw_metars[i]) if cb_removed_for
                                  else raw_metars[i])
            else:
                elev = _airport_elev_m(icao, float(om.get("elevation", 0)) if om else 0.0)
                new_modes.append(build_wxmode_string(
                    lat, lon, self._stable_zone_elev(i + 1, lat, lon, elev), month, icao))
                # No data at all (Open-Meteo unreachable, nothing cached):
                # keep the zone's current weather rather than defaults
                if om:
                    wx = om_to_wx_string(om, radar_echo=radar_echo, lightning=has_lightning)
                else:
                    wx = (self.zone_wx.get(i + 1) or
                          (self.psx.get(f"Wx{i + 1}") if self.psx else None) or
                          ";".join(_WX_DEFAULTS))
                new_metars.append(self._station_only_metar(i + 1, icao, now) if om else None)
            zone_num = i + 1
            if self._is_cb_free_zone(zone_num):
                final_wx = wx   # the anchor zone gets no CB at all (stripped below too)
            elif self._own_sigmets_applied:
                final_wx = self._own_sigmet_cb(wx, (lat, lon), om, f"Zone {i + 1} {icao}")
            else:
                final_wx = self._sigmet_cb_override(wx, (lat, lon), om, f"Zone {i + 1} {icao}")
            sigmet_label = None
            if final_wx != wx:  # a SIGMET gave (or enlarged) this zone's CB
                best = self._sigmet_cb_at((lat, lon)) if self._own_sigmets_applied else None
                sigmet_label = best[1].get('label', 'TS SIGMET') if best else 'TS SIGMET'
            final_wx = _cb_top_above_zone(final_wx, int(new_modes[-1].split(';')[3]))
            if self._is_cb_free_zone(zone_num) or cb_removed_for:
                final_wx = _strip_cb(final_wx)
                sigmet_label = None
            final_wx = self._limit_close_cb(zone_num, final_wx, positions[i])
            self.zone_cb_sigmet[zone_num] = sigmet_label
            target_parts = final_wx.split(';')
            target_oktas_val = int(target_parts[9]) if len(target_parts) > 11 else 0
            self.zone_cb_target[zone_num] = (
                {'oktas': target_oktas_val, 'base': int(target_parts[11]),
                 'top': int(target_parts[10])} if target_oktas_val > 0 else None)
            # In every flight phase: it used to be off in maneuvering mode
            # (holds, vectoring), where CBs then popped in and out at once.
            # Without --cb-growth CBs still fade away (_fade_cb_wx()); CB
            # removal near dep/arr and CB-free zones act at once.
            if cb_removed_for or self._is_cb_free_zone(zone_num):
                self.zone_cb_ramp.pop(zone_num, None)
            elif self.args.cb_growth:
                cape_here = _om_hval(om.get("hourly", {}), "cape", now.hour, 0) if om else 0.0
                final_wx = self._ramp_cb_wx(zone_num, final_wx, cape_here)
            else:
                final_wx = self._fade_cb_wx(zone_num, final_wx)
            new_wxs.append(final_wx)
            _per_zone.append({
                'radar_echo': radar_echo,
                'lightning': has_lightning,
                'ts_oktas': ts_oktas,
                'showers_metar': has_showers_metar,
                'sigmet_label': sigmet_label,
                'cb_removed_for': cb_removed_for,
            })

        if self.args.fake_cb:
            oktas, base_ft, top_ft = self.args.fake_cb
            new_wxs = [wx if (self._is_cb_free_zone(i + 1) or _per_zone[i]['cb_removed_for'] or
                              not self._fake_cb_zone(i + 1, snap_icaos[i])) else
                       _apply_fake_cb(wx, oktas, base_ft, top_ft)
                       for i, wx in enumerate(new_wxs)]
            self.logger.debug("fake-cb applied: %d oktas, base %dft, top %dft",
                              oktas, base_ft, top_ft)

        arpt_set = {self.fmc_dep_icao, self.fmc_dst_icao} - {None}
        cb_suffixes = []
        for i in range(7):
            parts = new_wxs[i].split(';')
            cb_oktas = int(parts[9]) if len(parts) > 11 and parts[9] != '0' else 0
            om = om_by_zone.get(i, {})
            hourly = om.get("hourly", {})

            cape = _om_hval(hourly, "cape", now.hour, 0)
            cin = _om_hval(hourly, "convective_inhibition", now.hour, 0)
            showers_mm = _om_hval(hourly, "showers", now.hour, 0)
            wmo_code = int((om.get("current") or {}).get("weather_code", 0))
            if cb_oktas > 0:
                growth_note = ""
                ramp = self.zone_cb_ramp.get(i + 1)
                target = self.zone_cb_target.get(i + 1)
                if ramp and target:
                    okt_rate = ramp.get('last_oktas_rate_per_min', 0.0)
                    top_rate = ramp.get('last_top_rate_per_min', 0.0)
                    bits = []
                    if abs(okt_rate) >= 0.01:
                        bits.append(f"{okt_rate:+.2f}okt/min")
                    if abs(top_rate) >= 1.0:
                        bits.append(f"{top_rate:+.0f}ft/min")
                    if bits:
                        growth_note = (f" [CB ramp: {' '.join(bits)}"
                                       f" -> target {target['oktas']}ok/{target['top']}ft]")
                sigmet_note = (f"{_per_zone[i]['sigmet_label']}; Open-Meteo: "
                               if _per_zone[i]['sigmet_label'] else "")
                cb_part = (f"  CB {cb_oktas} oktas"
                           f" base={parts[11]}ft tops={parts[10]}ft"
                           f" ({sigmet_note}WMO={wmo_code} showers={showers_mm:.2f}mm/h)"
                           f"{growth_note}")
            elif _cape_to_cb_oktas(cape, cin) > 0 and showers_mm < 0.5:
                cb_part = (f"  (CAPE suppressed: WMO={wmo_code}"
                           f" showers={showers_mm:.2f}mm/h)")
            else:
                cb_part = ""
            cb_suffixes.append(f"  CAPE={cape:.0f} J/kg{cb_part}")

            # Build zone reason (short) and weather_detail (long) for API broadcast
            src = metar_sources[i] if raw_metars[i] else "OM"
            stored_icao = self.zone_positions.get(i + 1, (None, None, ""))[2]
            reason = f"{src} {snap_icaos[i]}"
            if i in stale_metar_discarded:
                stale_icao, stale_age = stale_metar_discarded[i]
                reason += f" (stale {stale_icao} METAR {_metar_age_text(stale_age)} discarded)"
            if stored_icao in arpt_set:
                reason += " (dep/dst arpt)"
            if cb_oktas > 0:
                reason += f" CB {cb_oktas}ok {parts[11]}-{parts[10]}ft"
            elif _cape_to_cb_oktas(cape, cin) > 0 and showers_mm < 0.5:
                reason += f" CAPE={cape:.0f}J/kg suppressed"
            self.zone_reason[i + 1] = reason[:100]

            pz = _per_zone[i]
            snap_icao = snap_icaos[i]
            is_metar = raw_metars[i] is not None
            _snap_fake = (len(snap_icao) == 4 and snap_icao[0] == 'X' and
                          snap_icao[1:].isdigit())
            if stored_icao in arpt_set:
                _role = ("departure" if stored_icao == self.fmc_dep_icao
                         else "destination")
                base = f"{_role} airport {snap_icao}"
                base += f"; {metar_sources[i]} METAR" if is_metar else "; OpenMeteo"
            elif is_metar:
                base = f"{metar_sources[i]} METAR {snap_icao}"
                if snap_icao != stored_icao and stored_icao:
                    base += f" (nearest METAR to {stored_icao})"
            else:
                base = "OpenMeteo"
                if snap_icao and not _snap_fake:
                    base += f" at {snap_icao}"
                    if stored_icao and stored_icao != snap_icao and not (
                            len(stored_icao) == 4 and stored_icao[0] == 'X' and
                            stored_icao[1:].isdigit()):
                        base += f" (nearest to {stored_icao})"
            cb_sources = []
            if pz['ts_oktas'] > 0:
                cb_sources.append(f"METAR TS ({pz['ts_oktas']}oktas)")
            if pz['showers_metar']:
                cb_sources.append("METAR showers")
            if cb_oktas > 0 and (cape > 0 or not is_metar):
                if showers_mm >= 0.5:
                    cb_sources.append(f"CAPE {cape:.0f} J/kg + showers {showers_mm:.2f} mm/h")
                elif cape > 0:
                    cb_sources.append(f"CAPE {cape:.0f} J/kg (WMO {wmo_code})")
            if pz['radar_echo'] >= 2:
                cb_sources.append(f"radar echo {pz['radar_echo']}")
            if pz['lightning']:
                cb_sources.append("Blitzortung lightning")
            if pz['sigmet_label']:
                cb_sources.append(pz['sigmet_label'])
            if cb_oktas > 0:
                detail = base + f"; CBs {cb_oktas}oktas {parts[11]}-{parts[10]}ft"
                if cb_sources:
                    detail += " from " + " + ".join(cb_sources)
            elif _cape_to_cb_oktas(cape, cin) > 0 and showers_mm < 0.5:
                detail = base + (f"; no CBs — CAPE {cape:.0f} J/kg suppressed"
                                 f" (showers {showers_mm:.2f} mm/h, WMO {wmo_code})")
                if pz['lightning']:
                    detail += "; lightning present but CAPE suppressed"
            else:
                detail = base
                reasons_no_cb = []
                if cape > 0:
                    reasons_no_cb.append(f"CAPE {cape:.0f} J/kg")
                if wmo_code:
                    reasons_no_cb.append(f"WMO {wmo_code}")
                if reasons_no_cb:
                    detail += "; no CBs (" + ", ".join(reasons_no_cb) + ")"
                elif not is_metar:
                    detail += "; no CBs"
            if pz['cb_removed_for']:
                detail += f"; CBs removed (Remove CB near dep/arr: {pz['cb_removed_for']})"
            self.zone_weather_detail[i + 1] = detail

        self._inject_wx_slow_transit()
        self.psx_send_and_set("WxAutoSet", "0")

        for i, wxmode in enumerate(new_modes):
            zone_num = i + 1
            self.zone_mode[zone_num] = wxmode
            self.zone_is_metar[zone_num] = raw_metars[i] is not None
            self.zone_metar_source[zone_num] = metar_sources[i]
            src = metar_sources[i] if raw_metars[i] else "OM"
            stale_note = ""
            if i in stale_metar_discarded:
                stale_icao, stale_age = stale_metar_discarded[i]
                stale_note = f"  (stale {stale_icao} METAR {_metar_age_text(stale_age)} discarded)"
            self.psx_send_and_set(f"WxMode{zone_num}", wxmode)
            self.logger.info("Zone %d [%s]: %s @ %.3f/%.3f%s%s",
                             zone_num, src, snap_icaos[i],
                             snap_positions[i][0], snap_positions[i][1],
                             cb_suffixes[i], stale_note)

        await asyncio.sleep(1.0)

        self.last_write_time = time.time()
        # WxBasic (planet fallback) mirrors zone 1's weather data -- on the
        # ground, or while the aircraft is actually in WxBasic, only.
        self._write_wxbasic(new_wxs[0])
        for i, wx in enumerate(new_wxs):
            zone_num = i + 1
            old_wx = self.zone_wx.get(zone_num)
            if old_wx:
                try:
                    old_dir, old_spd, old_qnh = _wx_wind_qnh(old_wx)
                    new_dir, new_spd, new_qnh = _wx_wind_qnh(wx)
                    dir_delta = abs((new_dir - old_dir + 180) % 360 - 180)
                    if (abs(new_qnh - old_qnh) >= 1.0 or abs(new_spd - old_spd) >= 3 or
                            dir_delta >= 10):
                        self.logger.info(
                            "Zone %d weather content changed: QNH %.0f→%.0fhPa"
                            " wind %03d°/%dkt→%03d°/%dkt",
                            zone_num, old_qnh, new_qnh, old_dir, old_spd, new_dir, new_spd)
                except (ValueError, IndexError):
                    pass
            self.zone_wx[zone_num] = wx
            self._zone_wx_pos[zone_num] = self.zone_positions.get(zone_num)
            self.psx_send_and_set(f"Wx{zone_num}", wx)

        for i, metar in enumerate(new_metars):
            if metar is not None:
                self.psx_send_and_set(f"Metar{i + 1}", metar)

        self._state_changed_event.set()
        self._apply_msfs_sync()
        if self.args.sync_tropopause:
            self._write_tropopause()
        self.logger.info("Zone update complete")

    # ------------------------------------------------------------------
    # API state broadcast
    # ------------------------------------------------------------------

    def _cb_turb_area(self, zone_num: int, zone_positions: dict) -> Optional[dict]:
        """Return the CB turbulence area of a zone for the web map, or None if no CB.

        Same geometry as the turbulence itself (_get_nearest_cb()): a circle
        around the CB cell -- which the map places itself -- with the core
        out to core_nm and the flank zone out to flank_nm beyond that. A
        "shape" so other shapes can be added later. Above the top, the anvil
        zone reaches anvil_nm beyond the core downwind (anvil_downwind_deg,
        None if the wind isn't known: then all round), a third of that
        upwind (frankenturb/cb_turbulence.py).
        """
        raw = self.psx.get(f"Wx{zone_num}") if self.psx else None
        cb = parse_wx_zone_basic(raw) if raw else None
        if cb is None or cb[0] == 0 or zone_num not in zone_positions:
            return None
        coverage, top_ft, base_ft = cb
        wind_dir = self._turb_engine.wind_dir_at(top_ft) if self._turb_engine else None
        return {
            "shape": "circle",
            "core_nm": round(zone_cb_radius_nm(zone_num, zone_positions,
                                               self._turb_lateral_size_bias / 100.0), 1),
            "flank_nm": round(cb_flank_width_nm(coverage, top_ft, base_ft), 1),
            "anvil_nm": round(cb_flank_width_nm(coverage, top_ft, base_ft, anvil=True), 1),
            "anvil_downwind_deg": (round((wind_dir + 180.0) % 360.0)
                                   if wind_dir is not None else None),
        }

    def _build_state_message(self) -> str:  # pylint: disable=too-many-locals
        """Build the FRANKENWEATHER:<uuid>:<json> addon message payload."""
        cfg = self.args
        config = {
            "cruise_behind_dist": cfg.cruise_behind_dist,
            "low_alt_dist": cfg.low_alt_dist,
            "wxautoset_handoff_dist": cfg.wxautoset_handoff_dist,
            "new_zone_infront_range": list(cfg.new_zone_infront_range),
            "new_zone_leftright_range": list(cfg.new_zone_leftright_range),
            "new_zone_notnear": cfg.new_zone_notnear,
            "cape_squeeze": list(cfg.cape_squeeze) if cfg.cape_squeeze else None,
            "fake_cb": list(cfg.fake_cb) if cfg.fake_cb else None,
            "msfs_in_cloud_sync": self._msfs_in_cloud_sync,
            "msfs_qnh_check": self._msfs_qnh_check,
            "enroute_wind_enabled": self._enroute_wind_enabled,
            "enroute_wind_deviation": self._enroute_wind_deviation,
            "timesync_on_startup": self._timesync_on_startup,
            "timesync_on_situ_load": self._timesync_on_situ_load,
            "timesync_periodic": self._timesync_periodic,
            "use_sigmets": self._use_sigmets,
            "sigmet_enabled": self._sigmet_enabled,
            "sigmet_own": self._sigmet_own,
            "sigmet_handle": dict(self._sigmet_handle),
            "disable_psx_sigmets": self._disable_psx_sigmets,
            "cb_airport_mode": self._cb_airport_mode,
            # Routers before 1.26.0 only know this on/off form
            "avoid_cb_near_airport": self._cb_airport_mode != "off",
            "cb_airport_clearance_nm": _CB_AIRPORT_CLEARANCE_NM,
            "config_file": cfg.config_file,
            "config_file_exists": bool(cfg.config_file and os.path.exists(cfg.config_file)),
        }
        psx_governed = self._fw_mode == "disabled"
        arpt_icaos = {self.fmc_dep_icao, self.fmc_dst_icao} - {None}
        all_positions = {}  # every zone, as _get_nearest_cb() sees them
        for zone_num in range(1, 8):
            raw_mode = self.psx.get(f"WxMode{zone_num}") if self.psx else None
            pos = parse_wx_zone_position(raw_mode) if raw_mode else None
            if pos is not None:
                all_positions[zone_num] = pos
        zones = []
        for zone_num in range(1, 8):
            raw_mode = self.psx.get(f"WxMode{zone_num}") if self.psx else None
            pos = parse_wx_zone_position(raw_mode) if raw_mode else None
            if pos is None:
                continue
            lat, lon = pos[0], pos[1]
            mode_parts = raw_mode.split(';')
            icao_raw = mode_parts[5] if len(mode_parts) >= 6 else ""
            icao = icao_raw[:4] if len(icao_raw) >= 4 else ""
            if psx_governed:
                source = "PSX"
                reason = "Set by PSX"
            elif self._fw_mode == "manual":
                source = "MANUAL"
                reason = "Manual weather"
            else:
                source = (self.zone_metar_source.get(zone_num, "VATSIM")
                          if self.zone_is_metar.get(zone_num) else "OM")
                reason = self.zone_reason.get(zone_num, "")
            zones.append({
                "zone": zone_num,
                "icao": icao,
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "source": source,
                "reason": reason,
                "placement": self._placement_desc(lat, lon, icao,
                                                  is_arpt=icao in arpt_icaos),
                "weather_detail": self.zone_weather_detail.get(zone_num, ""),
                # --cb-growth: the CB's forecast target (None if none forecast)
                # and its current ramp rate (None if not actively ramping) --
                # the zone's *current* oktas/base/top is already available
                # from Wx<N> itself (see fw_webui.py's _parse_wx_brief()).
                "cb_target": self.zone_cb_target.get(zone_num),
                # --sigmet: the SIGMET behind this zone's CB, and whether
                # this is the CB-free anchor zone
                "cb_sigmet": self.zone_cb_sigmet.get(zone_num),
                "anchor": zone_num == self._anchor_zone,
                "cb_turb": self._cb_turb_area(zone_num, all_positions),
                "cb_rate": ({
                    "oktas_per_min": round(
                        self.zone_cb_ramp[zone_num].get('last_oktas_rate_per_min', 0.0), 3),
                    "top_ft_per_min": round(
                        self.zone_cb_ramp[zone_num].get('last_top_rate_per_min', 0.0), 1),
                } if zone_num in self.zone_cb_ramp else None),
            })
        zone_positions = [(z["lat"], z["lon"]) for z in zones]
        # For the web map: SIGMET areas of every type within its largest
        # range ring of the aircraft, or near/around a weather zone.
        _SIGMET_MAX_NM = 100.0
        _SIGMET_MAP_RANGE_NM = 640.0
        sigmets = []
        # Which areas frankenweather currently uses (the map draws the rest
        # dashed): TS/TC it depicts as CBs (exactly self.ts_sigmets), and
        # TURB/MTW while it owns the SIGMETs and handles that type. Those it
        # doesn't use go to PSX, or nowhere with the "Off" SIGMET preset.
        cb_used = {id(s) for s in self.ts_sigmets}
        turb_used = ({h for h in _fw_sigmet.TURB_HAZARDS if self._sigmet_handle.get(h)}
                     if self._own_sigmets_applied else set())
        psx_gets = not self._disable_psx_sigmets
        for s in self.sigmets:
            poly = s["polygon"]
            relevant = (
                self.ac_lat is not None and _fw_sigmet.polygon_distance(
                    self.ac_lat, self.ac_lon, poly)[0] <= _SIGMET_MAP_RANGE_NM
            ) or any(
                self._dist_nm(zlat, zlon, vlat, vlon) <= _SIGMET_MAX_NM
                for zlat, zlon in zone_positions
                for vlat, vlon in poly
            ) or any(
                _point_in_polygon(zlat, zlon, poly)
                for zlat, zlon in zone_positions
            )
            if relevant:
                sigmets.append({
                    "hazard": s["hazard"],
                    "polygon": [[round(la, 4), round(lo, 4)] for la, lo in poly],
                    "base_ft": s["base_ft"],
                    "top_ft": s["top_ft"],
                    "used_by": ("frankenweather"
                                if id(s) in cb_used or s["hazard"] in turb_used
                                else "PSX" if psx_gets else None),
                })
        # G-AIRMET turbulence areas within the map's range (the router's map
        # only, US areas), "used" unless G-AIRMET turbulence is switched off
        gairmets = []
        if self._turb_gairmet_fetcher is not None and self.ac_lat is not None:
            gairmet_used = self._turb_type_effective_bias("gairmet") > 0
            for region in self._turb_gairmet_fetcher.regions():
                if _fw_sigmet.polygon_distance(
                        self.ac_lat, self.ac_lon, region.polygon)[0] > _SIGMET_MAP_RANGE_NM:
                    continue
                gairmets.append({
                    "severity": region.severity,
                    "due_to": region.due_to,
                    "polygon": [[round(la, 4), round(lo, 4)] for la, lo in region.polygon],
                    "base_ft": region.alt_low_ft,
                    "top_ft": region.alt_high_ft,
                    "used": gairmet_used,
                })
        state = {
            "fw_mode": self._fw_mode,
            "metar_fallback_active": self._metar_fallback_active,
            "last_om_fetch_failed": self._last_om_fetch_failed,
            "om_usage": _OM_CLIENT.usage(),
            "om_backoff_s": round(_OM_CLIENT.backoff_remaining()),
            "om_zone_data_age_s": round(self._om_zone_data_age_s),
            "vatsim_cache_time": self.vatsim_cache_time or None,
            "noaa_metar_cache_time": self.noaa_metar_cache_time or None,
            "primary_metar_source": self.args.primary_metar_source,
            "mode": "MANEUVERING" if self._maneuvering else "CRUISE",
            "sigmet_hazards": self._aircraft_sigmet_hazards(),
            "ac_lat": round(self.ac_lat, 4) if self.ac_lat is not None else None,
            "ac_lon": round(self.ac_lon, 4) if self.ac_lon is not None else None,
            "ac_hdg": round(self.ac_hdg, 1) if self.ac_hdg is not None else None,
            "ac_alt_ft": round(self.ac_alt_ft) if self.ac_alt_ft is not None else None,
            "zones": zones,
            "config": config,
            "sigmets": sigmets,
            "gairmets": gairmets,
            "manual_wx_params": dict(self._manual_wx_params),
        }
        self._web_state = state
        self._web_state_received_at = time.time()
        payload = json.dumps(state, separators=(',', ':'))
        return f"FRANKENWEATHER:STATE:{self._instance_uuid}:{payload}"

    async def time_sync_coro(self) -> None:
        """Every 60s, resync PSX's clock if it has drifted >1s from real time.

        Opt-in via _timesync_periodic. Reads the live TimeEarth value PSX
        broadcasts (already subscribed for the turbulence subsystem) rather
        than tracking our own last-sent value, so this also catches drift
        caused by anything else writing the PSX clocks, not just the passage
        of time under acceleration.
        """
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(60.0)
                if not self._timesync_periodic:
                    continue
                if not self.psx_connected or self.psx_paused:
                    continue
                raw = self.psx.get("TimeEarth")
                if not raw:
                    continue
                try:
                    psx_ms = int(raw)
                except ValueError:
                    continue
                drift_ms = psx_ms - int(time.time() * 1000)
                if abs(drift_ms) > 1000:
                    self.logger.info(
                        "Time sync: PSX clock %.1fs %s real time — resyncing",
                        abs(drift_ms) / 1000.0, "ahead of" if drift_ms > 0 else "behind")
                    self._sync_psx_clock()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def wxclust_park_coro(self) -> None:
        """Every _WXCLUST_PARK_S, park the planet zone's CB cells at Null Island if they aren't.

        See _WXCLUST_PARKED. Logs once an hour how often PSX moved them back.
        """
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            parked, since = 0, time.monotonic()
            while True:
                await asyncio.sleep(_WXCLUST_PARK_S)
                if time.monotonic() - since >= 3600.0:
                    if parked:
                        self.logger.info("WxClust: parked the planet CB cells at Null Island %d "
                                         "time(s) in the last hour", parked)
                    parked, since = 0, time.monotonic()
                if not self.psx_connected or self._fw_mode == "disabled":
                    continue
                current = self.psx.get("WxClust")
                if current is None or current == _WXCLUST_PARKED:
                    continue
                if parked == 0:
                    self.logger.info("WxClust: parking the planet CB cells at Null Island")
                self.psx_send_and_set("WxClust", _WXCLUST_PARKED)
                parked += 1
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def log_flush_coro(self) -> None:
        """Every _LOG_FLUSH_INTERVAL_S, flush and fsync every log file.

        logging.FileHandler already flushes Python's buffer after every
        record, but on Windows a file another process still holds open can
        look stale to copy/zip tools (size/contents as of an earlier point)
        until its data is committed to disk -- the fsync forces that commit.
        """
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(_LOG_FLUSH_INTERVAL_S)
                for handler in logging.getLogger().handlers:
                    if not isinstance(handler, logging.FileHandler):
                        continue
                    handler.acquire()
                    try:
                        if handler.stream:
                            handler.stream.flush()
                            os.fsync(handler.stream.fileno())
                    except OSError as exc:
                        self.logger.debug("Log fsync failed for %s: %s", handler.baseFilename, exc)
                    finally:
                        handler.release()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def sigmet_refresh_coro(self) -> None:
        """Every _SIGMET_REFRESH_MAX_S (and once at startup), refresh PSX's own SIGMET data.

        frankenweather disabling PSX's own METAR downloads also silently
        disables its SIGMET downloads (they share a fetch cycle in PSX) --
        left alone, PSX's SIGMET data (and our own ts_sigmets, parsed from
        the same WxSigmet/Qs499 PSX populates -- see handle_sigmet_change)
        goes stale for the whole flight. Always triggers a download (so our
        own ts_sigmets/CB-override logic keeps working), with "embedded in
        planet weather model" on. Not while we download SIGMETs ourselves
        (--sigmet with our SIGMET system on, see own_sigmet_coro()).
        See _write_sigmet_on() for the bitmask
        details. Gated on _use_sigmets and only while frankenweather is
        actively driving the weather zones, matching the D-ATIS/ALTN-
        supplement gating.
        """
        myname = inspect.currentframe().f_code.co_name
        last_refresh = 0.0
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(30.0)
                if not self.psx_connected or self.psx_paused:
                    continue
                if not self._use_sigmets or self._should_skip_wx_update():
                    continue
                if self._own_sigmets_applied:
                    continue  # we download SIGMETs ourselves, see own_sigmet_coro()
                if time.time() - last_refresh < _SIGMET_REFRESH_MAX_S:
                    continue
                last_refresh = time.time()
                self._write_sigmet_on(embed=self._sigmet_active() or not self._disable_psx_sigmets,
                                      trigger_download=True)
                self.logger.info("SIGMET: requested a fresh PSX download")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def om_failure_banner_coro(self) -> None:
        """Every 30s, print a hard-to-miss banner while the last OM fetch failed.

        Also logs the Open-Meteo usage once an hour.

        _last_om_fetch_failed can be True well before (or even without ever
        reaching) the full METAR-fallback handoff -- a fetch can keep failing
        while frankenweather quietly serves frozen zone weather because not
        all zones are stale yet. That's silent otherwise: only a single
        warning line gets logged once, easy to scroll past. This makes an
        ongoing outage impossible to miss on the console.
        """
        myname = inspect.currentframe().f_code.co_name
        last_usage_log = time.monotonic()
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(30.0)
                if time.monotonic() - last_usage_log >= 3600.0:
                    last_usage_log = time.monotonic()
                    usage = _OM_CLIENT.usage()
                    self.logger.info(
                        "Open-Meteo usage: %.0f calls last hour, %.0f last 24 h "
                        "(daily limit 10,000): %s", usage["last_hour"], usage["last_24h"],
                        ", ".join(f"{k} {v:.0f}" for k, v in usage["by_kind_24h"].items()))
                if not self._last_om_fetch_failed:
                    continue
                if self._metar_fallback_active:
                    status = " — VATSIM/NOAA METAR-fallback zones are active"
                else:
                    nearest_nm = self._nearest_zone_dist_nm()
                    if nearest_nm is None:
                        status = ""
                    else:
                        remaining = self.args.wxautoset_handoff_dist - nearest_nm
                        status = (
                            f" — nearest zone is {nearest_nm:.0f}nm away, still "
                            f"{max(remaining, 0.0):.0f}nm short of the "
                            f"{self.args.wxautoset_handoff_dist:.0f}nm METAR-fallback distance"
                        )
                if self._zones_metar_only:
                    status = " — zones updated from METARs only" + status
                elif self._om_zone_data_age_s > 0:
                    status = (f" — zones updated from cached data "
                              f"(oldest {self._om_zone_data_age_s / 60.0:.0f} min){status}")
                else:
                    status = " — weather not updating" + status
                self.logger.warning(
                    "\n"
                    "############################################################\n"
                    "###  OPEN-METEO FETCH FAILING%s\n"
                    "###  Retrying automatically; this banner repeats every 30s\n"
                    "###  while the outage continues.\n"
                    "############################################################",
                    status)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def state_broadcast_coro(self) -> None:
        """Broadcast current state as a PSX addon message on change or every 60 seconds.

        Deliberately NOT gated by _conflict_paused(): this is the minimal
        identification heartbeat conflict detection itself depends on (see
        _note_conflict()) — it carries no actual weather data and causes no
        PSX recalculation, so a paused instance keeps sending it so the
        winning instance keeps seeing (and logging) the ongoing conflict too.
        """
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    await asyncio.wait_for(self._state_changed_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
                self._state_changed_event.clear()
                if not self.psx_connected:
                    continue
                msg = self._build_state_message()
                self.psx.send("addon", msg)
                self.logger.debug("State broadcast sent (%d bytes)", len(msg))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    # ------------------------------------------------------------------
    # Coroutines
    # ------------------------------------------------------------------

    def _should_skip_wx_update(self) -> bool:
        """Return True (and log) when the zone weather update should be suppressed."""
        if self._fw_mode in ("paused", "disabled"):
            self.logger.debug("Weather update skipped: fw_mode=%s", self._fw_mode)
            return True
        return self._conflict_paused()

    async def weather_update_coro(self) -> None:  # pylint: disable=too-many-branches
        """Periodically update PSX weather zones from Open-Meteo."""
        myname = inspect.currentframe().f_code.co_name
        last_update_time = 0.0
        try:
            self.logger.debug("Starting %s", myname)
            async with aiohttp.ClientSession() as session:
                while True:
                    try:
                        await asyncio.wait_for(self.fmc_changed_event.wait(), timeout=10.0)
                    except asyncio.TimeoutError:
                        pass
                    self.fmc_changed_event.clear()
                    if not self.psx_connected or self.psx_paused:
                        continue
                    if self.ac_lat is None:
                        continue
                    # Only entering maneuvering mode re-places the zones (all
                    # around the aircraft); on leaving it the ordinary
                    # relocation below moves zones as they fall behind. It
                    # used to re-place them on leaving too, taking away a CB
                    # the aircraft was in (live, 2026-10-23).
                    entered_maneuvering = (self._update_maneuvering_mode() and
                                           self._maneuvering)
                    # When PSX manages its own weather (WxAutoSet=1, "disabled"
                    # mode only), don't reposition zones — PSX will move them
                    # and we'd overwrite that on recovery. The last known
                    # zone_positions are preserved for STATE broadcasts.
                    # Zones are still placed while Open-Meteo is unavailable:
                    # _update_zones() puts a zone it has no data for on the
                    # nearest METAR station (it used to freeze them, 2026-09-08,
                    # when relocated zones couldn't be given any weather).
                    if self._fw_mode == "disabled":
                        any_relocated = False
                    elif not self.zone_positions:
                        self._place_all_zones()
                        any_relocated = True
                    elif any(self._dist_nm(self.ac_lat, self.ac_lon, lat, lon) >
                             _REPOSITION_DIST_NM
                             for _, (lat, lon, _) in self.zone_positions.items()):
                        self.logger.info("Aircraft repositioned — reinitializing weather zones")
                        self._place_all_zones()
                        any_relocated = True
                    elif entered_maneuvering:
                        self._place_all_zones(keep_cb_turb=True)
                        any_relocated = True
                    elif self._zones_placed_before_sigmets and self._own_sigmets_applied:
                        any_relocated = self._replace_zones_for_cb_area()
                    else:
                        any_relocated = self._check_and_relocate()
                        any_relocated = self._ensure_arpt_zones() or any_relocated
                        any_relocated = self._maintain_anchor_zone() or any_relocated
                        any_relocated = self._place_sigmet_zones() or any_relocated
                    elapsed = time.time() - last_update_time
                    force = self._manual_wx_force_update
                    self._manual_wx_force_update = False
                    if not any_relocated and not force and elapsed < _REFRESH_MAX_S:
                        continue
                    if self._should_skip_wx_update():
                        continue
                    if self._fw_mode == "manual":
                        await self._update_zones_manual()
                    else:
                        await self._update_zones(session)
                    last_update_time = time.time()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    @staticmethod
    def _ang_diff(a_deg: float, b_deg: float) -> float:
        """Return the shortest signed difference a-b in degrees, in (-180, 180]."""
        return ((a_deg - b_deg + 180.0) % 360.0) - 180.0

    def _nearest_snapshot_name(self, lat: float, lon: float) -> Optional[str]:
        """Find the flight-plan snapshot waypoint whose coordinates are closest to (lat, lon).

        Used as a fallback when a live FMC waypoint's name has no exact match
        in the flight-plan snapshot (see _ENROUTE_COORD_MATCH_NM). PSX itself
        only matches Format D/E waypoints by coordinates, not by name, so this
        mirrors what PSX actually does instead of relying on name spelling.
        """
        best_name, best_nm = None, _ENROUTE_COORD_MATCH_NM
        for name, (snap_lat, snap_lon) in self._corridor_snapshot_coords.items():
            dist_nm = self._dist_nm(lat, lon, snap_lat, snap_lon)
            if dist_nm < best_nm:
                best_name, best_nm = name, dist_nm
        return best_name

    def _build_windstate_dict(self) -> dict:  # pylint: disable=too-many-locals
        """Build the enroute-wind status dict: per-waypoint flight-plan vs OM diff, fetch timers."""
        has_snapshot = self._corridor_snapshot_txt is not None
        snapshot_parseable = (not has_snapshot) or bool(self._corridor_snapshot_waypoints)
        waypoints = []
        for i, (name, lat, lon) in enumerate(self._enroute_waypoints):
            om_levels = self._waypoint_om_wind.get(i, {})
            snap_name = name if name in self._corridor_snapshot_waypoints else None
            if snap_name is None and self._corridor_snapshot_coords:
                snap_name = self._nearest_snapshot_name(lat, lon)
            snap_levels = self._corridor_snapshot_waypoints.get(snap_name, {})
            levels = []
            for fl in sorted(set(om_levels) | set(snap_levels)):
                om = om_levels.get(fl)
                snap = snap_levels.get(fl)
                diff = None
                if om is not None and snap is not None:
                    diff = {
                        "dir_deg": round(self._ang_diff(om[0], snap[0]), 1),
                        "spd_kt": round(om[1] - snap[1], 1),
                        "oat_c": round(om[2] - snap[2], 1),
                    }
                levels.append({
                    "fl_ft": fl,
                    "flightplan": ({"dir_deg": snap[0], "spd_kt": snap[1], "oat_c": snap[2]}
                                   if snap else None),
                    "openmeteo": ({"dir_deg": om[0], "spd_kt": om[1], "oat_c": om[2]}
                                  if om else None),
                    "diff": diff,
                })
            waypoints.append({
                "name": name,
                "lat": round(lat, 4),
                "lon": round(lon, 4),
                "passed": i in self._waypoint_passed,
                "matched_name": snap_name if snap_name and snap_name != name else None,
                "levels": levels,
            })
        return {
            "enabled": self._enroute_wind_enabled,
            "deviation": self._enroute_wind_deviation,
            "has_snapshot": has_snapshot,
            "snapshot_parseable": snapshot_parseable,
            "snapshot_raw": (self._corridor_snapshot_txt
                             if has_snapshot and not snapshot_parseable else None),
            "waypoints": waypoints,
            "last_fetch_epoch": self._enroute_last_fetch_time or None,
            "next_fetch_epoch": self._enroute_next_fetch_time or None,
        }

    def _build_windstate_message(self) -> str:
        """Build the FRANKENWEATHER:WINDSTATE:<uuid>:<payload> addon message.

        <payload> is gzip+base64-encoded JSON (see fw_webui.encode_windstate_payload())
        -- a long-haul route's plain JSON can reach ~200 KB, which compresses much
        smaller thanks to its heavily repeated key names.
        """
        state = self._build_windstate_dict()
        self._web_windstate = state
        self._web_windstate_received_at = time.time()
        payload = _fw_webui.encode_windstate_payload(state)
        return f"FRANKENWEATHER:WINDSTATE:{self._instance_uuid}:{payload}"

    async def own_sigmet_coro(self) -> None:
        """--sigmet: download SIGMETs from aviationweather.gov every _OWN_SIGMET_REFRESH_S.

        Fetches both the international (isigmet) and US domestic
        (airsigmet) lists as JSON and normalizes them (fw_sigmet.normalize()).
        A failed download keeps the last good set, minus anything that has
        expired meanwhile. Gated like sigmet_refresh_coro(). The web UI can
        ask for a download right away (_own_sigmet_refresh_event). Every
        pass also takes over, keeps or hands back PSX's SIGMETs
        (_sync_own_sigmets()).
        """
        myname = inspect.currentframe().f_code.co_name
        last_try = 0.0
        try:
            self.logger.debug("Starting %s", myname)
            async with aiohttp.ClientSession(timeout=_OWN_SIGMET_TIMEOUT) as session:
                first = True
                while True:
                    if not first:  # first pass right away: SIGMETs as early as possible
                        try:
                            await asyncio.wait_for(
                                self._own_sigmet_refresh_event.wait(), timeout=10.0)
                        except asyncio.TimeoutError:
                            pass
                    first = False
                    self._own_sigmet_refresh_event.clear()
                    if self._own_sigmet_download_now:
                        self._own_sigmet_download_now = False
                        last_try = 0.0
                    wanted = (self._sigmet_active() and self._use_sigmets and
                              not self._should_skip_wx_update())
                    if wanted and time.time() - last_try >= _OWN_SIGMET_REFRESH_S:
                        last_try = time.time()
                        self._own_sigmet_next_at = last_try + _OWN_SIGMET_REFRESH_S
                        await self._download_own_sigmets(session)
                    self._sync_own_sigmets(wanted)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def _download_own_sigmets(self, session: aiohttp.ClientSession) -> None:
        """Fetch, normalize and store both SIGMET lists (see own_sigmet_coro())."""
        try:
            await self._fetch_own_sigmets(session)
        finally:
            self._update_web_sigmetstate()

    async def _fetch_own_sigmets(self, session: aiohttp.ClientSession) -> None:
        """Do _download_own_sigmets()'s work."""
        try:
            lists = []
            for url in (_fw_sigmet.ISIGMET_URL, _fw_sigmet.AIRSIGMET_URL):
                async with session.get(url) as resp:
                    if resp.status == 204:
                        # "No Content": none right now (seen for the US
                        # list, 2026-10-28), not a failure
                        lists.append([])
                        continue
                    if resp.status != 200:
                        raise RuntimeError(f"HTTP {resp.status} from {url}")
                    lists.append(await resp.json(content_type=None))
            entries = _fw_sigmet.normalize(lists[0], lists[1])
            downloaded = _fw_sigmet.all_entries(lists[0], lists[1])
        except (aiohttp.ClientError, asyncio.TimeoutError, RuntimeError, ValueError) as exc:
            self._own_sigmet_error = f"{type(exc).__name__}: {exc}"
            now = time.time()
            self._own_sigmet_entries = [
                e for e in self._own_sigmet_entries if not e["valid_to"] or e["valid_to"] >= now]
            self.logger.warning("SIGMET download failed (keeping %d earlier SIGMETs): %s",
                                len(self._own_sigmet_entries), self._own_sigmet_error)
            return
        self._own_sigmet_entries = entries
        self._own_sigmet_downloaded = downloaded
        self._own_sigmet_fetched_at = time.time()
        self._own_sigmet_error = None
        if self._own_sigmets_applied:
            self._use_own_sigmet_data()
        bad = [e for e in entries if not e["ok"]]
        counts = collections.Counter(e["hazard"] for e in entries if e["ok"])
        self.logger.info(
            "SIGMET download: %d usable (%s), %d unusable",
            len(entries) - len(bad),
            ", ".join(f"{n} {h}" for h, n in sorted(counts.items())) or "none", len(bad))
        self._save_unparsed_sigmets(bad)

    def _sync_own_sigmets(self, wanted: bool) -> None:
        """Take over, keep or hand back PSX's SIGMETs (--sigmet).

        While wanted (and once our first download has succeeded), PSX keeps
        embedding SIGMETs but stops downloading them itself, and gets our
        own text (fw_sigmet.psx_text()): every usable SIGMET frankenweather
        doesn't handle itself (fw_sigmet.OWN_HAZARDS: TS/TC as CB weather
        zones, TURB/MTW as its own turbulence) -- or,
        with disable_psx_sigmets, no SIGMETs at all ("#"). The
        SigmetOn bits and the text are re-checked on every call and
        rewritten if anything (a situ load, another addon) changed them.
        When no longer wanted, PSX's own download is switched back on and
        triggered, and its text is parsed as before.
        """
        if not self.psx_connected:
            return
        if not wanted:
            if self._own_sigmets_applied:
                self._own_sigmets_applied = False
                self._own_sigmet_written = None
                # Our SIGMET system switched off (SIGMET presets): as without
                # --sigmet, our TS/TC CBs go, and "Off" also means PSX
                # doesn't use the SIGMETs it downloads.
                if not self._sigmet_active():
                    self.ts_sigmets = []
                self._write_sigmet_on(
                    embed=self._sigmet_active() or not self._disable_psx_sigmets,
                    trigger_download=True, auto_download=True)
                self.logger.info("Own SIGMETs: handing SIGMETs back to PSX (download requested)")
                self._pirep_refresh_event.set()  # PIREPs without our SIGMETs now
            return
        if self._own_sigmet_fetched_at is None:
            return  # nothing of our own yet: leave PSX's SIGMETs alone
        if not self._own_sigmets_applied:
            self._own_sigmets_applied = True
            self._use_own_sigmet_data()
            # Zones already placed (e.g. at startup, before this first
            # download) get their TS/TC CBs right away, and the zones
            # wanted in TS/TC areas are all placed at once.
            self._sigmet_place_burst = True
            self._manual_wx_force_update = True
            self.fmc_changed_event.set()
            self.logger.info("Own SIGMETs: taking over SIGMETs from PSX")
            self._pirep_refresh_event.set()  # PIREPs with our SIGMETs now
        bits = self._sigmet_on_bits
        if not bits & _SIGMET_EMBED_BIT or bits & _SIGMET_AUTO_DOWNLOAD_BIT:
            self._write_sigmet_on(embed=True, trigger_download=False, auto_download=False)
        # disable_psx_sigmets: PSX gets no SIGMETs at all
        handled = self._handled_hazards()
        text = ("#" if self._disable_psx_sigmets else
                _fw_sigmet.psx_text(self._own_sigmet_entries, self._own_sigmet_fetched_at,
                                    handled))
        # Compare without the "#DOWNLOADED <time>" header line: a new
        # download with the same SIGMETs isn't worth a PSX write.
        if (self._psx_sigmet_raw or "").partition("^")[2] == text.partition("^")[2]:
            return
        if self._own_sigmet_written == text:
            self.logger.info("Own SIGMETs: PSX's SIGMET text was changed, writing ours again")
        elif self._disable_psx_sigmets:
            self.logger.info("Own SIGMETs: disable_psx_sigmets, emptying PSX's SIGMET data")
        else:
            kept = len(_fw_sigmet.psx_entries(self._own_sigmet_entries, handled))
            self.logger.info(
                "Own SIGMETs: giving PSX %d SIGMETs (%d bytes), left out %d handled by"
                " frankenweather (%s) and %d unusable", kept, len(text),
                sum(1 for e in self._own_sigmet_entries
                    if e["ok"] and e["hazard"] in handled),
                "/".join(sorted(handled)) or "none",
                sum(1 for e in self._own_sigmet_entries if not e["ok"]))
        self._inject_wx_slow_transit()
        self.psx_send_and_set("WxSigmet", text)
        self._psx_sigmet_raw = text
        self._own_sigmet_written = text

    def _handled_hazards(self) -> set:
        """Return the SIGMET types frankenweather handles itself (the rest go to PSX)."""
        return {h for h, on in self._sigmet_handle.items() if on}

    def _use_own_sigmet_data(self) -> None:
        """Point self.sigmets/ts_sigmets at our own downloaded SIGMETs.

        Same dict layout _parse_sigmets() produces (one dict per polygon),
        so the CB override, the map and everything else using them works
        unchanged. ts_sigmets also holds TC: neither reaches PSX any more.
        """
        sigmets = []
        for e in self._own_sigmet_entries:
            if not e["ok"]:
                continue
            top = e["top_ft"] or 35000
            if e["hazard"] in _fw_sigmet.CB_HAZARDS:
                top = max(top, 25000)  # same sanity floor as _parse_sigmets()
            for poly in e["polygons"]:
                sigmets.append({"hazard": e["hazard"], "polygon": [tuple(p) for p in poly],
                                "base_ft": e["base_ft"] or 0, "top_ft": top,
                                "qualifier": e["qualifier"], "key": e["key"],
                                "label": (f"{e['hazard']} SIGMET {e['fir']} {e['series']}"
                                          f" {e['qualifier']}").strip()})
        self.sigmets = sigmets
        # TS/TC areas we depict ourselves; PSX does any type we don't handle
        self.ts_sigmets = [s for s in sigmets if s["hazard"] in _fw_sigmet.CB_HAZARDS and
                           self._sigmet_handle.get(s["hazard"])]

    def _save_unparsed_sigmets(self, entries: list) -> None:
        """Save each SIGMET we couldn't use to _SIGMET_UNPARSED_DIR, once per SIGMET.

        One small text file per SIGMET (reason, then its raw text), named
        after its unique key, so the parser can be improved later.
        Existing files are left alone, so a SIGMET seen on every download
        is only written once.
        """
        if not entries:
            return
        try:
            os.makedirs(_SIGMET_UNPARSED_DIR, exist_ok=True)
            for entry in entries:
                name = re.sub(r"[^A-Za-z0-9_.-]", "_", entry["key"]) + ".txt"
                path = os.path.join(_SIGMET_UNPARSED_DIR, name)
                if os.path.exists(path):
                    continue
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"Reason: {entry['reason']}\n"
                            f"Hazard: {entry['raw_hazard']}\n\n{entry['raw']}\n")
                self.logger.info("SIGMET %s unusable (%s), saved to %s",
                                 entry["key"], entry["reason"], path)
        except OSError as exc:
            self.logger.warning("Could not save unusable SIGMETs to %s: %s",
                                _SIGMET_UNPARSED_DIR, exc)

    def _update_web_sigmetstate(self) -> None:
        """Refresh the SIGMETs page data (self._web_sigmetstate), with or without PSX.

        Done after every download and setting change, so frankenweather's
        own web UI shows the SIGMETs even before the PSX connection is up
        (the first download runs right at startup). Routers get it via
        sigmetstate_broadcast_coro().
        """
        self._web_sigmetstate = {
            "enabled": self._sigmet_enabled,
            "own": self._sigmet_own,
            "handled": sorted(self._handled_hazards()),
            "psx_off": self._disable_psx_sigmets,
            "source": "aviationweather.gov",
            "fetched_at": self._own_sigmet_fetched_at,
            "next_at": self._own_sigmet_next_at,
            "interval_s": _OWN_SIGMET_REFRESH_S,
            "error": self._own_sigmet_error,
            "entries": self._own_sigmet_entries,
        }
        self._web_sigmetstate_received_at = time.time()
        self._sigmetstate_changed_event.set()

    def _build_sigmetstate_message(self) -> str:
        """Build the FRANKENWEATHER:SIGMETSTATE:<uuid>:<payload> addon message.

        <payload> is gzip+base64-encoded JSON (see
        fw_webui.encode_windstate_payload()) of self._web_sigmetstate, for
        the router web UI's SIGMETs page -- the raw text alone runs ~35 KB.
        """
        payload = _fw_webui.encode_windstate_payload(self._web_sigmetstate)
        return f"FRANKENWEATHER:SIGMETSTATE:{self._instance_uuid}:{payload}"

    async def _build_sigmetjson_message(self) -> str:
        """Build the FRANKENWEATHER:SIGMETJSON:<uuid>:<payload> addon message.

        <payload> is the lzma+base64-encoded JSON (see
        fw_webui.encode_lzma_json_payload()) of /api/sigmet/latest/json, so
        every frankenrouter can serve it too (shared cockpit). The router
        only forwards it to frankenrouters. Compressed in an executor (it
        takes ~80 ms).
        """
        payload = await asyncio.get_running_loop().run_in_executor(
            None, _fw_webui.encode_lzma_json_payload, self._sigmet_json())
        return f"FRANKENWEATHER:SIGMETJSON:{self._instance_uuid}:{payload}"

    async def sigmetstate_broadcast_coro(self) -> None:
        """Broadcast SIGMETSTATE when it changes, and every 5 min for routers connecting later.

        A change while PSX isn't connected (e.g. the first download, right
        at startup) is sent as soon as the connection is up. SIGMETJSON
        (/api/sigmet/latest/json, whose "used_by" also changes with the
        settings) goes along with it, once a download has succeeded.
        """
        myname = inspect.currentframe().f_code.co_name
        pending = False
        last_sent = 0.0
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    await asyncio.wait_for(self._sigmetstate_changed_event.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    pass
                if self._sigmetstate_changed_event.is_set():
                    self._sigmetstate_changed_event.clear()
                    pending = True
                if not pending and time.time() - last_sent < 300.0:
                    continue
                if (not self._sigmet_enabled or self._web_sigmetstate is None or
                        not self.psx_connected or self._conflict_paused()):
                    continue
                self._psx_send("addon", self._build_sigmetstate_message())
                if self._own_sigmet_fetched_at is not None:
                    self._psx_send("addon", await self._build_sigmetjson_message())
                pending = False
                last_sent = time.time()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def windstate_broadcast_coro(self) -> None:
        """Broadcast WINDSTATE to the PSX network when enroute wind state changes."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                try:
                    await asyncio.wait_for(self._enroute_wind_changed_event.wait(), timeout=60.0)
                except asyncio.TimeoutError:
                    pass
                self._enroute_wind_changed_event.clear()
                if not self.psx_connected:
                    continue
                if self._conflict_paused():
                    continue
                if not self._enroute_wind_enabled and not self.route_waypoints:
                    continue
                msg = self._build_windstate_message()
                self._psx_send("addon", msg)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    async def enroute_wind_coro(self) -> None:
        """Periodically fetch Open-Meteo enroute wind and refresh PSX's WxCorridor.

        Contacts Open-Meteo every _ENROUTE_FETCH_INTERVAL_S ("hourly" background
        drift), plus immediately whenever the FMC route genuinely changes — a
        reroute, not just the aircraft passing a waypoint — since that's the
        only case where a current, non-excluded waypoint can be missing wind
        data (_set_route_waypoints resets _enroute_next_fetch_time to 0 for
        that case). Passing a waypoint alone triggers neither a new OM fetch
        nor a corridor resend: the wind data for every remaining waypoint is
        unchanged, so PSX's corridor is simply left in place rather than
        making PSX recalculate for nothing (_apply_enroute_wind_injection also
        skips the resend on its own if a refetch happens to return identical
        data). On by default; _enroute_wind_enabled.
        """
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)
            while True:
                await asyncio.sleep(15.0)
                if not self._enroute_wind_enabled or not self.psx_connected:
                    continue
                if self._conflict_paused():
                    continue
                if not self._enroute_waypoints:
                    continue
                if time.time() < self._enroute_next_fetch_time:
                    continue
                targets = [
                    (i, name, lat, lon)
                    for i, (name, lat, lon) in enumerate(self._enroute_waypoints)
                    if i not in self._waypoint_passed
                ]
                await self._fetch_enroute_om_batch(targets)
                self._apply_enroute_wind_injection()
                now = time.time()
                self._enroute_last_fetch_time = now
                self._enroute_next_fetch_time = now + _ENROUTE_FETCH_INTERVAL_S
                self._enroute_wind_changed_event.set()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    def _psx_log(self, msg: str) -> None:
        """Route psx.Client's own log messages: connection status to INFO, rest to DEBUG.

        psx.Client's default logger is a no-op, so without this, retrying
        while PSX is unreachable (e.g. not started yet) produces no console
        output at all -- just silence every ~12s. Surfaces its
        connect/retry/disconnect messages at INFO so that's visible by
        default; everything else it logs (every TX/RX, every subscription)
        is far too noisy for that and goes to DEBUG instead.
        """
        if msg.startswith((
                "Connecting to PSX Main Server",
                "Oops, that failed",
                "Will retry in",
                "Disconnected by PSX Main Server",
        )):
            self.logger.info("PSX: %s", msg)
        else:
            self.logger.debug("PSX: %s", msg)

    async def get_psx_connection_coro(self) -> None:  # pylint: disable=too-many-statements
        """Maintain PSX connection."""
        myname = inspect.currentframe().f_code.co_name
        try:
            self.logger.debug("Starting %s", myname)

            def connected(*_):
                self.logger.info("PSX CONNECTED")
                self.psx_connected = True
                self.psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")
                self.psx.send(
                    "clientName", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")
                self._state_changed_event.set()
                self._turb_state_changed_event.set()
                if self._fw_mode == "disabled":
                    self.psx_send_and_set("WxAutoSet", "1")
                if self._timesync_on_startup:
                    self._sync_psx_clock()

            def disconnected():
                self.logger.info("PSX DISCONNECTED")
                self.psx_connected = False

            def onresume():
                self.logger.info("PSX RESUMED")
                self.psx_connected = True
                if self.psx_paused:
                    self._last_situ_load_mono = time.monotonic()
                    # A situ load is a new place: a turbulence event doesn't
                    # carry over (one ran on 10 minutes into the next test leg)
                    if self._turb_events.cancel() is not None:
                        self._log_turb_event_end(" (situ loaded)")
                self.psx_paused = False
                self._turb_state_changed_event.set()
                # PSX's load1 (pause) / load3 (resume) pair brackets every full
                # situ (re)load, not just a runtime pause/unpause — it's the only
                # wire-protocol signal PSX gives for "a new situ just finished
                # loading", so it doubles as our situ-load detector here.
                if self._timesync_on_situ_load:
                    self._sync_psx_clock()
                # TEMPORARY (PFD-altitude-jump investigation, see the
                # project_frankenweather_altitude_jump memory): LeftPfdAlt
                # (Qs562) is PSX Mode=DEMAND, so it's never sent unprompted.
                # onResume (load3) fires only after the Lexicon has been
                # fully delivered (see router/docs/NOTES.md's welcome-
                # message sequence), unlike onConnect/the "version"
                # callback, which fire before the Lexicon arrives -- too
                # early for psx.py's name->Qcode mapping to resolve
                # "LeftPfdAlt" (see frankenpush.py's identical pattern).
                # A single demand here makes PSX start sending it on every
                # change for the rest of this connection, which the router
                # logs along with Qs121 -- no periodic re-demand or local
                # tracking/analysis needed, that happens from the logs.
                # Remove once that investigation is closed.
                self.psx.send("demand", "LeftPfdAlt")

            self.psx = psx.Client()
            self.psx.onPause = lambda: setattr(self, 'psx_paused', True)
            self.psx.onDisconnect = disconnected
            self.psx.onConnect = lambda: None
            self.psx.onResume = onresume
            self.psx.logger = self._psx_log

            self.psx.subscribe("id")
            self.psx.subscribe("version", connected)
            self.psx.subscribe("PiBaHeAlTas", self.handle_piba)
            self.psx.subscribe("WxBasic", self.handle_owned_change)
            self.psx.subscribe("FocussedWxZone", self.handle_focused_zone)
            self.psx.subscribe("FmcRteViAcMo", self.handle_fmc_change)
            self.psx.subscribe("FmcRte1", self.handle_fmc_change)
            self.psx.subscribe("FmcRte2", self.handle_fmc_change)
            self.psx.subscribe("WxSigmet", self.handle_sigmet_change)
            self.psx.subscribe("SigmetOn", self.handle_sigmet_on)
            self.psx.subscribe("AeroCkBxs", self.handle_aero_ckbxs)
            self.psx.subscribe("WxAloft", self.handle_wxaloft)
            self.psx.subscribe("addon", self._handle_addon)
            self.psx.subscribe("WxCorridorTxt", self._handle_corridor)
            # Qs497: so psx.get() holds PSX's current value and
            # _apply_enroute_wind_qs497() only sends it when it differs
            self.psx.subscribe("WxCorridorSel")
            self.psx.subscribe("MetarsUp", self._handle_metars_up)
            self.psx.subscribe("PrinterText", self._handle_printer_text)
            # The weather variables we control (_OWNED_KEYS): subscribing
            # makes psx.get() a reliable local cache of PSX's actual current
            # value, which psx_send_and_set() relies on to avoid re-sending
            # weather data PSX already has, and handle_owned_change() writes
            # ours back if anyone else (or a situ load) changes them.
            self.psx.subscribe("WxAutoSet", self.handle_owned_change)

            for i in range(1, 8):
                self.psx.subscribe(f"Wx{i}", self.handle_owned_change)
                self.psx.subscribe(f"WxMode{i}", self.handle_owned_change)
                self.psx.subscribe(f"Metar{i}", self.handle_owned_change)

            # Turbulence subsystem subscriptions
            self.psx.subscribe("WxBurst", self.handle_other_wxburst)
            self.psx.subscribe("TimeEarth")
            self.psx.subscribe("WxClust")
            self.psx.subscribe("AcftHeight")
            self.psx.subscribe("OnGround")

            await self.psx.connect(self.args.psx_host, self.args.psx_port)
            self.logger.warning("psx.connect() returned — this should not happen")

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
                    ("WeatherUpdate", self.weather_update_coro),
                    ("EnrouteWind", self.enroute_wind_coro),
                    ("StateBroadcast", self.state_broadcast_coro),
                    ("WindStateBroadcast", self.windstate_broadcast_coro),
                    ("TimeSync", self.time_sync_coro),
                    ("WxClustPark", self.wxclust_park_coro),
                    ("OmFailureBanner", self.om_failure_banner_coro),
                    ("SigmetRefresh", self.sigmet_refresh_coro),
                    ("OwnSigmet", self.own_sigmet_coro),
                    ("SigmetStateBroadcast", self.sigmetstate_broadcast_coro),
                    ("LogFlush", self.log_flush_coro),
                ]
                coros += [
                    ("TurbulenceTask", self.turbulence_coro),
                    ("PSXWind", self.psx_wind_coro),
                    ("TurbBroadcast", self.turb_state_broadcast_coro),
                    ("PirepLevels", self.pirep_levels_coro),
                ]
                for name, coro_fn in coros:
                    if name not in running:
                        self.logger.info("Starting %s...", name)
                        task = self.taskgroup.create_task(coro_fn(), name=name)
                        self.tasks.add(task)

                self.logger.debug("Running tasks: %s", running)
                await asyncio.sleep(5.0)

        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.critical("Unhandled exception %s in %s, shutting down", exc, myname)
            self.logger.critical(traceback.format_exc())

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    def handle_args(self) -> None:
        """Parse command line arguments."""
        parser = argparse.ArgumentParser(
            prog=__MYNAME__,
            description=__MY_DESCRIPTION__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
        parser.add_argument(
            '--psx-host', type=str, default='127.0.0.1',
            help="Hostname or IP of the PSX server.")
        parser.add_argument(
            '--psx-port', type=int, default=10747,
            help="Port of the PSX server.")
        parser.add_argument(
            '--psx-port-override', type=int, default=None, metavar='PORT',
            help="Override --psx-port with this value (a warning is printed). "
                 "Used by start_scripts to force connecting to the correct router port.")
        parser.add_argument(
            '--stations', type=str, default=None,
            help="UCAR stations.txt file (downloads from UCAR if not given).")
        parser.add_argument(
            '--cruise-behind-dist', type=float, default=50.0, metavar='NM',
            help="In cruise: relocate a zone more than this many nm behind the aircraft.")
        parser.add_argument(
            '--low-alt-dist', type=float, default=200.0, metavar='NM',
            help="Below cruise alt: relocate any zone more than this many nm away.")
        parser.add_argument(
            '--wxautoset-handoff-dist', type=float, default=320.0, metavar='NM',
            help="During an Open-Meteo outage, reposition onto the nearest cached "
                 "VATSIM/NOAA METAR stations only once every zone is more than this "
                 "many nm away (matches PSX's own zone-of-influence radius; "
                 "deliberately independent of --cruise-behind-dist/--low-alt-dist).")
        parser.add_argument(
            '--primary-metar-source', choices=('vatsim', 'noaa'), default='vatsim',
            help="EXPERIMENTAL: which METAR source to prefer when both VATSIM and "
                 "NOAA have data for the same station. Only the primary source's "
                 "cache is refreshed outside the full Open-Meteo-outage fallback "
                 "path (where both are always consulted regardless of this "
                 "setting), so leaving this at the default 'vatsim' is identical "
                 "to behavior before this option existed.")
        parser.add_argument(
            '--metar-live-refresh', action='store_true',
            help="EXPERIMENTAL: once a zone has been assigned a real (fresh, "
                 "cached) METAR by the normal selection rule, additionally try a "
                 "short-timeout live fetch of just that airport's current METAR "
                 "straight from VATSIM, to get something fresher than the bulk "
                 "cache (which only refreshes every 30 minutes). Falls back "
                 "silently to the already-selected cached METAR on failure or "
                 "timeout; never changes which airport a zone snaps to, and never "
                 "turns a cache miss/stale METAR into a hit. Off by default.")
        parser.add_argument(
            '--sigmet', action='store_true',
            help="EXPERIMENTAL: frankenweather's SIGMET system. Downloads "
                 "SIGMETs from aviationweather.gov itself (every 10 "
                 "minutes) instead of letting PSX do it, and handles the "
                 "SIGMET types chosen on the web UI's new SIGMETs page "
                 "itself: thunderstorms (TS) and tropical cyclones (TC) as "
                 "CBs in its weather zones, turbulence (TURB) and mountain "
                 "wave (MTW) as its own turbulence. PSX gets the other "
                 "SIGMETs it could parse. Off by default: frankenweather "
                 "then doesn't use SIGMETs at all and only has PSX download "
                 "them regularly.")
        parser.add_argument(
            '--sync-tropopause', action='store_true',
            help="EXPERIMENTAL: keep PSX's Planet Weather page tropopause "
                 "altitude/temperature/wind set to \"Constantly\" with real "
                 "values computed from Open-Meteo's pressure-level data at the "
                 "aircraft's current position (WMO lapse-rate definition). "
                 "Uses the median of the last hour's samples, and leaves the "
                 "tropopause on PSX's auto for the first 15 minutes, until at "
                 "least 3 samples exist, and again after a reposition. On the "
                 "ground it follows every update; in flight it only changes "
                 "PSX's value when "
                 "that differs by at least 3000ft, 5C or 30kt of wind, at most "
                 "every 30 minutes (every in-flight write risks a PFD altitude "
                 "jump). Wind "
                 "variability is always written as 0 (no ensemble-spread data "
                 "available) and gust is always set equal to speed. Off by "
                 "default -- leaves PSX on its own \"variable by latitude and "
                 "season\"/\"variable by jet stream constellation\" settings.")
        parser.add_argument(
            '--new-zone-infront-range', type=_parse_nm_range, default=(150.0, 250.0),
            metavar='MIN,MAX',
            help="Forward range in nm when placing a new zone ahead (e.g. 150,250).")
        parser.add_argument(
            '--new-zone-leftright-range', type=_parse_nm_range, default=(0.0, 100.0),
            metavar='MIN,MAX',
            help="Lateral range in nm left or right of track for a new zone (e.g. 0,100).")
        parser.add_argument(
            '--new-zone-notnear', type=float, default=50.0, metavar='NM',
            help="Minimum separation in nm from existing zones; retry up to 10 times.")
        parser.add_argument(
            '--cape-squeeze', type=_parse_cape_squeeze, default=(500.0, 150.0),
            metavar='CAPE:MIN_FWD',
            help="Squeeze zone spacing when CAPE is high: at avg CAPE >= CAPE J/kg "
                 "shrink fwd_max to MIN_FWD nm (default: 500:150). New zones are "
                 "never squeezed closer than 100nm ahead.")
        parser.add_argument(
            '--fake-cb', type=_parse_cb_arg, default=None, metavar='O:B:T',
            help="Override CB in all zones: O=oktas (0-8), B=base ft, T=top ft "
                 "(e.g. --fake-cb=6:3000:45000).")
        parser.add_argument(
            '--fake-cb-only', type=lambda s: [x.strip().upper() for x in s.split(',') if x.strip()],
            default=None, metavar='ICAO[,ICAO...]',
            help="With --fake-cb: only fake CBs in the zones of these airports; DEP and DST "
                 "mean the FMC departure/destination (e.g. --fake-cb-only=DST or "
                 "--fake-cb-only=EHAM,EHRD).")
        parser.add_argument(
            '--cb-growth', action='store_true',
            help="EXPERIMENTAL: ramp a CB's coverage (oktas) "
                 "and top height gradually toward its forecast target instead "
                 "of snapping instantly, so new CBs don't appear to pop into "
                 "view and don't finish growing before the aircraft reaches "
                 "them. Shrinking/removal always decays at a fixed rate "
                 "regardless of distance. Off by default -- CBs snap to "
                 "forecast instantly, as before.")
        parser.add_argument(
            '--om-proxy', type=str, default=None, metavar='URL',
            help="Proxy URL for all Open-Meteo requests, e.g. socks5h://localhost:1080."
                 " Requires PySocks (pip install requests[socks]).")
        parser.add_argument(
            '--debug', action='store_true',
            help="Log PSX weather changes, every value sent to PSX, and all PSX traffic.")
        parser.add_argument(
            '--log', type=str, default=None, metavar='FILE',
            help="Also write every console log line to this file (e.g. to review later how "
                 "often Open-Meteo fetches were failing). Appends if the file exists.")
        parser.add_argument(
            '--save-logs', type=str, default=None, metavar='DIR',
            help="[DEVELOPMENT] Directory to save enroute-wind flight-plan-vs-Open-Meteo "
                 "diff data to as timestamped JSON, one file per flight, updated on every "
                 "WxCorridor refresh while the enroute wind importer is enabled.")
        parser.add_argument(
            '--turb-profile', choices=_TURB_PROFILES, default=None,
            help="Turbulence profile to use: stable (the released behaviour) or testing "
                 "(also the changes being evaluated). Overrides the profile in the config "
                 "file; the file itself only changes if you save the settings. Without it, "
                 "the config file's profile is used, or stable.")
        parser.add_argument(
            '--config-file', type=str, default=_DEFAULT_CONFIG_FILE, metavar='PATH',
            help="TOML file for every setting the web GUI can change (MSFS sync, enroute "
                 "wind, manual weather, turbulence). Loaded at startup if it exists; "
                 "otherwise defaults are used until you save from the web UI (nothing is "
                 "written until then). See docs/frankenweather.md for the file format.")

        parser.add_argument(
            '--web-port', type=int, default=9747, metavar='PORT',
            help="TCP port for the standalone web UI (on by default; see --no-web-ui).")
        parser.add_argument(
            '--no-web-ui', action='store_true',
            help="Disable the standalone web UI (enabled by default on --web-port).")
        parser.add_argument(
            '--log-web-requests', action='store_true',
            help="Log every request to the standalone web UI (off by default).")
        parser.add_argument(
            '--no-datis-override', action='store_true',
            help="Disable overriding PSX's own D-ATIS/MetarsUp (Qs464) reply with VATSIM "
                 "data (enabled by default whenever frankenweather is actively controlling "
                 "the weather zones). For testing/troubleshooting.")
        parser.add_argument(
            '--no-altn-supplement', action='store_true',
            help="Disable printing a supplemental VATSIM-weather message after PSX's own "
                 "FMC ALTN weather printout when it contains an UNAVAIL airport we have "
                 "data for (enabled by default whenever frankenweather is actively "
                 "controlling the weather zones). For testing/troubleshooting.")

        # Removed options, kept as accepted-but-ignored so old startup scripts still run;
        # handle_args() logs a deprecation warning for each one actually passed. Hidden
        # from --help (argparse.SUPPRESS) rather than deleted, so that backward-compat
        # acceptance keeps working -- only the --help listing is decluttered.
        parser.add_argument(
            '--turb-config-file', type=str, default=None, help=argparse.SUPPRESS)
        parser.add_argument(
            '--cruise-alt', type=float, default=None, help=argparse.SUPPRESS)
        parser.add_argument(
            '--arpt-zone-dist', type=float, default=None, help=argparse.SUPPRESS)
        parser.add_argument(
            '--msfs-in-cloud-sync', action='store_true', help=argparse.SUPPRESS)
        parser.add_argument(
            '--msfs-qnh-check', choices=('CHECK', 'USE'), default=None,
            help=argparse.SUPPRESS)
        parser.add_argument(
            '--msfs-qnh-check-maxdiff', type=float, default=None, help=argparse.SUPPRESS)
        parser.add_argument(
            '--msfs-wind-sync', action='store_true', help=argparse.SUPPRESS)
        parser.add_argument(
            '--disable-psx-weather-updates', action='store_true', help=argparse.SUPPRESS)
        parser.add_argument(
            '--no-turbulence', action='store_true', help=argparse.SUPPRESS)
        parser.add_argument(
            '--turb-rate', type=int, default=None, help=argparse.SUPPRESS)
        parser.add_argument(
            '--turb-intensity-bias', type=int, default=None, help=argparse.SUPPRESS)
        self.args = parser.parse_args()
        if self.args.psx_port_override is not None:
            if self.args.psx_port_override != self.args.psx_port:
                print(f"WARNING: --psx-port-override={self.args.psx_port_override} "
                      f"overrides --psx-port={self.args.psx_port}", file=sys.stderr)
            self.args.psx_port = self.args.psx_port_override
        self._sigmet_enabled = self.args.sigmet

    # (arg_name, replacement_hint or None for the generic "just remove it" message)
    _REMOVED_ARGS = (
        ('cruise_alt', None),
        ('arpt_zone_dist', None),
        ('msfs_in_cloud_sync', None),
        ('msfs_qnh_check', None),
        ('msfs_qnh_check_maxdiff', None),
        ('msfs_wind_sync', None),
        ('disable_psx_weather_updates', None),
        ('no_turbulence', None),
        ('turb_rate', None),
        ('turb_intensity_bias', None),
        ('turb_config_file', 'turbulence settings are now part of --config-file'),
    )

    def _warn_removed_args(self) -> None:
        """Log a deprecation warning for each removed CLI option actually passed."""
        for name, hint in self._REMOVED_ARGS:
            value = getattr(self.args, name)
            if value:
                suffix = f"; {hint}" if hint else "; remove it from your startup script"
                self.logger.warning(
                    "--%s is deprecated and no longer has any effect%s",
                    name.replace('_', '-'), suffix)

    async def run(self) -> None:
        """Entry point."""
        self.handle_args()

        handlers = [logging.StreamHandler(sys.stdout)]
        if self.args.log:
            # Explicit encoding: without it, FileHandler falls back to the
            # platform default -- e.g. cp1252 on Windows -- which silently
            # mangles the °/—/× etc. these log messages use, and produces a
            # file that isn't valid UTF-8 (confirmed live: broke grep/every
            # standard text tool trying to read one of these logs).
            handlers.append(logging.FileHandler(self.args.log, encoding='utf-8'))
        self._event_log_path = _prepare_event_log()
        if self._event_log_path:
            handlers.append(logging.FileHandler(self._event_log_path, encoding='utf-8'))
        logging.basicConfig(
            format="%(asctime)s: %(message)s",
            level=logging.INFO,
            datefmt="%H:%M:%S",
            handlers=handlers)
        self.logger = logging.getLogger(__MYNAME__)
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)
        if self._event_log_path:
            self.logger.info("Logging to %s", self._event_log_path)
        else:
            self.logger.warning(
                "Could not create a per-run event log under %s", _EVENT_LOG_DIR)
        self.logger.info("frankenweather version %s starting", __version__)
        self._warn_removed_args()

        if self.args.stations:
            self.airports = load_airports(self.args.stations)
            self.logger.info("Loaded %d airports from %s",
                             len(self.airports), self.args.stations)
        else:
            self.airports, source = get_airports(_UCAR_STATIONS_URL, _STATIONS_CACHE)
            self.logger.info("Loaded %d airports from %s", len(self.airports), source)

        self._turb_engine = TurbulenceEngine(om_proxy=self.args.om_proxy)
        self._turb_pirep_fetcher = PirepFetcher()
        self._turb_cape_fetcher = CapeFetcher(proxy=self.args.om_proxy)
        self._turb_gairmet_fetcher = GairmetFetcher()
        self.logger.info("Turbulence engine initialized")
        self._load_config_file()
        self._apply_turb_profile_option()      # also without a config file
        self._apply_turb_profile_bands()
        self.logger.info("Turbulence profile: %s", self._turb_profile)
        # Trigger initial TURBSTATE broadcast so the router has config data immediately.
        self._turb_state_changed_event.set()

        async with asyncio.TaskGroup() as self.taskgroup:
            task = self.taskgroup.create_task(self.monitor_coro(), name="Monitor")
            self.tasks.add(task)
            if not self.args.no_web_ui:
                task = self.taskgroup.create_task(
                    self.run_web_ui_coro(), name="WebUI")
                self.tasks.add(task)
            print("All tasks created")
        print("All tasks completed")

    def _sigmet_json(self) -> dict:
        """Return /api/sigmet/latest/json: the last download, parsed, with its status.

        "generated_at" says when this was put together ("expired" and
        "used_by" are as of then): a router serving it from SIGMETJSON
        may be up to 5 minutes behind.

        Every SIGMET of the last download (fw_sigmet.all_entries(): all
        normalized fields, ok/reason), each also with "expired" and
        "used_by": "frankenweather" (a type it handles), "PSX" (given to
        PSX) or None (unusable, expired, or no SIGMETs for PSX).
        """
        now = time.time()
        handled = self._handled_hazards()
        own = self._own_sigmets_applied
        entries = []
        for entry in self._own_sigmet_downloaded:
            expired = bool(entry["valid_to"]) and entry["valid_to"] < now
            used_by = None
            if entry["ok"] and not expired and own:
                if entry["hazard"] in handled:
                    used_by = "frankenweather"
                elif not self._disable_psx_sigmets:
                    used_by = "PSX"
            entries.append({**entry, "expired": expired, "used_by": used_by})

        def _utc(epoch):
            return (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))
                    if epoch else None)
        return {
            "source": "aviationweather.gov",
            "generated_at": now,
            "generated_at_utc": _utc(now),
            "urls": [_fw_sigmet.ISIGMET_URL, _fw_sigmet.AIRSIGMET_URL],
            "fetched_at": self._own_sigmet_fetched_at,
            "fetched_at_utc": _utc(self._own_sigmet_fetched_at),
            "next_download_at": self._own_sigmet_next_at,
            "next_download_at_utc": _utc(self._own_sigmet_next_at),
            "last_error": self._own_sigmet_error,
            "frankenweather_owns_psx_sigmets": own,
            "sigmet_system_on": self._sigmet_active(),
            "psx_sigmets_off": self._disable_psx_sigmets,
            "handled_by_frankenweather": sorted(handled),
            "counts": {
                "total": len(entries),
                "usable": sum(1 for e in entries if e["ok"] and not e["expired"]),
                "unusable": sum(1 for e in entries if not e["ok"]),
                "expired": sum(1 for e in entries if e["expired"]),
                "by_hazard": dict(collections.Counter(e["hazard"] for e in entries)),
            },
            "sigmets": entries,
        }

    def _sigmet_text_response(self, kind: str):
        """Return the /api/sigmet/latest/<kind> response (see _register_sigmet_text_routes())."""
        from aiohttp import web as _web  # pylint: disable=import-outside-toplevel
        if not self._sigmet_enabled:
            return _web.Response(status=404, text=(
                "frankenweather's SIGMET system is off (start it with --sigmet)\n"))
        if kind == "json":
            if self._own_sigmet_fetched_at is None:
                return _web.Response(status=404, text="No SIGMETs downloaded yet\n")
            return _web.json_response(self._sigmet_json(), headers={
                "Content-Disposition": 'inline; filename="sigmets.json"'})
        if kind == "topsx":
            if not self._own_sigmets_applied or self._own_sigmet_written is None:
                return _web.Response(status=404, text=(
                    "frankenweather isn't giving PSX any SIGMETs right now "
                    "(PSX downloads its own, or nothing has been written yet)\n"))
            # PSX separates lines with "^"
            text = self._own_sigmet_written.replace("^", "\n").rstrip("\n") + "\n"
        else:
            if self._own_sigmet_fetched_at is None:
                return _web.Response(status=404, text="No SIGMETs downloaded yet\n")
            entries = (self._own_sigmet_downloaded if kind == "raw" else
                       [e for e in self._own_sigmet_entries if e["ok"]])
            text = _fw_sigmet.sigmet_text(entries, self._own_sigmet_fetched_at)
        return _web.Response(text=text, content_type="text/plain", headers={
            "Content-Disposition": f'attachment; filename="sigmets-{kind}.txt"'})

    def _register_sigmet_text_routes(self, routes) -> None:
        """Register the SIGMET text downloads (--sigmet; frankenweather's own web port only).

        /api/sigmet/latest/raw: every SIGMET of the last download, expired,
        unknown and unparseable ones included. /api/sigmet/latest/sanitized:
        only the usable ones (the rest left out). /api/sigmet/latest/topsx:
        exactly what frankenweather last gave PSX. All three in PSX's own
        SIGMET text layout (fw_sigmet.sigmet_text()), one line per line.
        /api/sigmet/latest/json: the whole download parsed, with its status
        (_sigmet_json()).
        """
        @routes.get('/api/sigmet/latest/{kind:(raw|sanitized|topsx|json)}')
        async def _sigmet_latest(request):
            return self._sigmet_text_response(request.match_info['kind'])

    async def run_web_ui_coro(self) -> None:
        """Run a standalone aiohttp web server exposing the FrankenWeather UI."""
        from aiohttp import web as _web  # pylint: disable=import-outside-toplevel
        port = self.args.web_port
        ctx = StandaloneFWContext(self)
        routes = _web.RouteTableDef()

        @routes.get('/')
        async def _home(_):
            raise _web.HTTPFound('/weather')

        _fw_webui.register_weather_routes(routes, ctx)
        # Standalone-only: the router UI has no need to browse/download
        # frankenweather's own per-run event logs (see the Config and logs page).
        _fw_webui.register_weather_logs_routes(routes, ctx)
        self._register_sigmet_text_routes(routes)

        static_path = pathlib.Path(__file__).parent / 'router' / 'frankenrouter' / 'static'

        @_web.middleware
        async def _cors(request, handler):
            response = await handler(request)
            response.headers['Access-Control-Allow-Origin'] = '*'
            return response

        app = _web.Application(middlewares=[_cors])
        app.add_routes(routes)
        if static_path.is_dir():
            app.router.add_static('/static', static_path)
        # aiohttp's per-request access log lines are long and many (pages
        # poll): only with --log-web-requests.
        runner = (_web.AppRunner(app) if self.args.log_web_requests
                  else _web.AppRunner(app, access_log=None))
        await runner.setup()
        site = _web.TCPSite(runner, '0.0.0.0', port)
        await site.start()
        self.logger.info("Standalone web UI running on http://0.0.0.0:%d/", port)
        try:
            while True:
                await asyncio.sleep(3600.0)
        except asyncio.CancelledError:
            await runner.cleanup()
            raise


if __name__ == '__main__':
    try:
        asyncio.run(Script().run())
    except Exception:  # pylint: disable=broad-exception-caught
        traceback.print_exc()
        input("An error occurred, press Enter to continue...")
    except SystemExit as exc:
        if exc.code not in (None, 0):
            input("An error occurred, press Enter to continue...")
