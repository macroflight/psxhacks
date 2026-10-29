"""Fly a plan of test legs (a TOML file) one after another: reposition, settle, record.

See docs/flighttest.md for the plan format. Each leg is flown from
before_nm before its reference point (e.g. an airport) along its true
track; if the terrain doesn't clear the leg's altitude by
min_clearance_ft within check_nm, the reverse track is tried (with
reverse_if_terrain), else the leg is skipped. A leg that doesn't fly
stably within the timeout freezes the sim and stops the plan.
"""
import os
import threading
import time
import tomllib
from typing import Callable, Optional

from . import accel, geo
from .sim import Sim, Target, start_position

# Defaults for every leg, overridden at the top of the plan or per leg
DEFAULTS = {
    "situ": "~/autotest-cruise.situ",
    "registration": "G-PING",
    "callsign": "TEST{n}",
    "route_airport": "EGLF",
    "record_s": 150.0,
    "first_number": 1,
    "correct_track": True,
    "replay_file": None,
    "output_dir": ".",
    "tas_kt": 270.0,
    "magvar": 0.0,
    "before_nm": 0.0,
    "min_clearance_ft": 1000.0,
    "check_nm": 40.0,
    "reverse_if_terrain": False,
    "stable_timeout_s": 150.0,
    "accel_host": "127.0.0.1",
    "accel_port": None,
    "boost_port": None,
    "mcp_speed": None,
    "stable_bank_deg": 5.0,
    "stable_vs_fpm": 300.0,
    "stable_hold_s": 20.0,
}


def empty_route(airport: str) -> str:
    """Return an FmcRte1 value: an empty route from airport to airport."""
    return f"{airport};{airport};;;;;;;;0;49#" + "AbEN00*___'''9.0/9.0'0'0'0';" * 3


def load_plan(path: str) -> list:
    """Return the plan's legs, each a dict with every setting filled in."""
    with open(path, "rb") as plan_file:
        plan = tomllib.load(plan_file)
    top = {**DEFAULTS, **{k: v for k, v in plan.items() if k != "leg"}}
    legs = []
    for number, leg in enumerate(plan.get("leg", []), start=top["first_number"]):
        legs.append({**top, **leg, "number": number})
    return legs


def choose_track(leg: dict) -> tuple:
    """Return (track, start lat, start lon, highest terrain ft), or track None if blocked."""
    tracks = [leg["track"]]
    if leg["reverse_if_terrain"]:
        tracks.append((leg["track"] + 180.0) % 360.0)
    top = 0.0
    for track in tracks:
        lat, lon = geo.dest_point(leg["lat"], leg["lon"], track, -leg["before_nm"])
        top = geo.highest_terrain_ft(lat, lon, track, leg["check_nm"])
        if top <= leg["alt_ft"] - leg["min_clearance_ft"]:
            return track, lat, lon, top
    return None, None, None, top


def _set_replay_time(leg: dict) -> None:
    if leg["replay_file"] and leg.get("replay_time"):
        with open(os.path.expanduser(leg["replay_file"]), "w", encoding="utf-8") as replay:
            replay.write(f"{leg['replay_time']} {time.time()}\n")


def _target(leg: dict, lat: float, lon: float, track: float) -> Target:
    """Return the leg's stability target (rough legs may allow more bank and V/S)."""
    return Target(lat, lon, track, leg["alt_ft"], max_bank_deg=leg["stable_bank_deg"],
                  max_vs_fpm=leg["stable_vs_fpm"])


def _correct_track(sim: Sim, leg: dict, where: tuple, log: Callable[[str], None]) -> None:
    """Correct the heading for the wind and let it settle; where is (name, lat, lon, track)."""
    name, lat, lon, track = where
    change = sim.correct_track(track)
    log(f"TRACK {name}: heading window {change:+d} deg")
    if change:
        # Not the position: the aircraft has flown on from the start
        target = _target(leg, lat, lon, (track + change) % 360.0)
        target.pos_nm = 1e6
        ok, why = sim.wait_stable(target, timeout=90.0, hold=10.0)
        log(f"{'STABLE' if ok else 'UNSETTLED'} {name} after correction: {why}")


def _record(sim: Sim, leg: dict, path: str) -> None:
    """Record the leg to path, with accelerations alongside if their ports are set.

    accel_port: frankenboostproxy's accelerations (.accel); boost_port:
    the raw PSX boost feed (.boost).
    """
    threads = []
    for port_key, func, ext in (("accel_port", accel.record, ".accel"),
                                ("boost_port", accel.record_raw, ".boost")):
        if leg[port_key]:
            threads.append(threading.Thread(target=func, daemon=True, args=(
                path[:-4] + ext, leg["record_s"], leg["accel_host"], int(leg[port_key]))))
    for thread in threads:
        thread.start()
    sim.record(path, leg["record_s"])
    for thread in threads:
        thread.join(timeout=10.0)


def fly_leg(sim: Sim, leg: dict, log: Callable[[str], None]) -> Optional[str]:
    """Fly one leg and record it; return the recording's path, or None if skipped.

    Raises RuntimeError (after freezing the sim) if it doesn't fly stably.
    """
    name = leg.get("name", f"leg{leg['number']}")
    track, lat, lon, top = choose_track(leg)
    if track is None:
        log(f"SKIP {name}: terrain {top:.0f} ft too high for {leg['alt_ft']} ft")
        return None
    _set_replay_time(leg)
    callsign = leg["callsign"].format(n=leg["number"])
    mag = round((track - leg["magvar"]) % 360.0) or 360
    log(f"LEG {leg['number']} {name} {callsign}: start N{lat:.3f} E{lon:.3f} track "
        f"{track:.0f}T/{mag}M {leg['alt_ft']} ft, highest terrain {top:.0f} ft")
    count = sim.load_situ(leg["situ"], {
        "StartPiBaHeAlVsTasYw": start_position(lat, lon, track, leg["alt_ft"], leg["tas_kt"]),
        "McpWdoHdg": str(mag), "McpWdoAlt": str(int(leg["alt_ft"]) // 100),
        "McpWdoSpd": str(leg["mcp_speed"] or min(330, int(leg["tas_kt"]))),
        "McpPshLnv": "0", "McpPshVnv": "0", "McpPshHdgHld": "0", "McpPshAltHld": "0",
        "McpPshSpd": "8", "CfgRego": leg["registration"],
        "FmcRte1": empty_route(leg["route_airport"]), "FmcFltNo": callsign})
    log(f"LOADED {name}: {count} variables")
    if sim.ensure_alt_hold(leg["alt_ft"]):
        log(f"ALT HOLD {name}: FLCH hadn't handed over, pressed ALT HOLD")
    ok, why = sim.wait_stable(_target(leg, lat, lon, track), timeout=leg["stable_timeout_s"],
                              hold=leg["stable_hold_s"])
    if not ok:
        sim.set("SimPaused", 1)
        log(f"ABORT {name}: not stable ({why}); motion frozen")
        raise RuntimeError(f"{name} not stable")
    log(f"STABLE {name}: {why}")
    if leg["correct_track"]:
        _correct_track(sim, leg, (name, lat, lon, track), log)
    path = os.path.join(os.path.expanduser(leg["output_dir"]), f"{name}.rec")
    log(f"REC {name} -> {path}")
    _record(sim, leg, path)
    log(f"END {name}")
    return path


def fly_plan(sim: Sim, path: str, log: Callable[[str], None], skip: int = 0) -> None:
    """Fly every leg of the plan at path (after the first skip legs)."""
    for leg in load_plan(path)[skip:]:
        fly_leg(sim, leg, log)
    log("ALL LEGS DONE")
