"""Synthetic CB placement test: fly the planner (fw_planner) through storms, fast.

No PSX, no network, no clock. The storms are made up (clusters, lines, singles and big
fields with a life cycle, moving with a wind) or, with --wxlib, real radar storms recorded by
~/fw-weatherlib/capture.py (every 10-minute frame, tracked and moved as frankenweather does).
A simple aircraft cruises, turns or holds; every REPLAN_S (frankenweather: every minute on
the clock) and when the "behind" rule frees a zone, the planner replans and its moves are
applied; every STEP_S the rules are checked (fw_planner's rules, user 2026-11-19):
  - no CB appears, disappears or moves within fw_planner.hard_nm() of the aircraft
  - a CB the crew may see changes at most once per fw_planner.CHANGE_GAP_S
  - no two CBs placed closer than fw_planner.SPACING_MIN_NM; fixed zones never moved
Counted: changes the crew may see (CBs appearing or going in view, by distance), zone moves,
CBs shown, significant clusters ahead with a CB, and "missed" storms: one in view for 5
minutes without a CB while a weaker storm in view had one (a weaker CB took the zone).
One run takes well under a second for hours of flight; any failure can be repeated from its
seed.

Usage: python3 -m flighttest.plansim [--runs N] [--seed S] [--hours H] [--kind cruise|hold|turn]
       [--fields] [--wxlib [DIR]] [--band NM] [--route] [--nd NM]
       [--planner FILE]
"""
import argparse
import glob
import importlib.util
import json
import math
import os
import random
import sys
from dataclasses import dataclass, fields as dc_fields
from typing import Optional

import fw_planner
import fw_radar

P = fw_planner
STEP_S = 30.0
ND_NM = 0.0                  # --nd: every flight's ND range (0: random)
ROUTE_AHEAD_NM = 800         # --route: how far ahead the route goes
REPLAN_S = 60.0
WINDOW_NM, WINDOW_DEG = 200.0, 120.0
ND_DEG = 90.0
VIEW_AHEAD_DEG = 75.0         # "missed" storms: in view this far off the nose
DEAD_KEEP_S = 1800.0
BEHIND_CRUISE_NM = 50.0       # frankenweather's "behind" rule: a zone this far behind is moved
BEHIND_MANEUVER_NM = 200.0    # ... holding: this far away, once a turn puts it behind
ZONES = 7
BAND_NM = 120.0               # storms only this far past what the crew may see (frankenweather's
#                               radar range, _CB_NEW_BEYOND_NM): new CBs are born in that band
FIXED = (1,)                  # zone 1: the anchor (it follows the aircraft)
BIG_DBZ = 58                  # a storm this strong makes a big CB (fw_planner.HARD_BIG_NM)
MISSED_STEPS = 10             # "missed": this many steps (5 min) in view without a CB
WXLIB = os.path.expanduser("~/fw-weatherlib/lib")


@dataclass
class SimStorm:  # pylint: disable=too-many-instance-attributes
    """A made-up storm: born, grows, lives, decays; moves with the wind."""

    key: str
    lat: float
    lon: float
    peak_dbz: int
    area_km2: float
    born_s: float
    life_s: float
    hdg: float
    kt: float
    ltg: int = 0                  # lightning near it (flash pixels)

    def dbz(self, t: float) -> int:
        """Return the storm's strength at t (0: not alive; radar CB threshold 43)."""
        age = t - self.born_s
        if age < 0 or age > self.life_s:
            return 0
        frac = min(1.0, age / 900.0, (self.life_s - age) / 900.0)   # 15 min up and down
        return int(35 + (self.peak_dbz - 35) * frac)

    def area(self, _t: float) -> float:
        """Return the storm's area at t."""
        return self.area_km2

    def pos(self, t: float) -> tuple:
        """Return where the storm is at t (a dead one: where it ended)."""
        t = min(t, self.born_s + self.life_s)
        d = self.kt * max(0.0, t - self.born_s) / 3600.0
        return move(self.lat, self.lon, self.hdg, d)


class ReplayStorm:
    """A real radar storm (fw-weatherlib): its tracked cell in each frame."""

    def __init__(self, key: str, t0: float):
        """t0: the sim's time zero in unix seconds."""
        self.key, self.t0, self.cells, self.ltg = key, t0, {}, 0

    def _cell(self, t: float):
        """Return the cell of the frame that decides at sim time t, or None."""
        frame = fw_radar.active_frame(self.cells, self.t0 + t)
        if frame is None or frame + 780 < self.t0 + t:   # missing from the newest frame: gone
            return None
        return self.cells.get(frame)

    def dbz(self, t: float) -> int:
        """Return the storm's peak dBZ at t (0: not on the radar)."""
        c = self._cell(t)
        return c.max_dbz if c is not None else 0

    def area(self, t: float) -> float:
        """Return the storm's area at t."""
        c = self._cell(t)
        return c.area_km2 if c is not None else 0.0

    def pos(self, t: float) -> tuple:
        """Return where the storm is at t (gone: where it was last)."""
        c = self._cell(t)
        if c is None:
            frame = max(f for f in self.cells if f <= max(self.t0 + t, min(self.cells)))
            c = self.cells[frame]
        return fw_radar.cell_at(c, self.t0 + t, 1200)


def move(lat: float, lon: float, brg: float, dist: float) -> tuple:
    """Return (lat, lon) dist nm from (lat, lon) on bearing brg (small distances: flat)."""
    return (lat + dist * math.cos(math.radians(brg)) / 60.0,
            lon + dist * math.sin(math.radians(brg)) /
            (60.0 * max(0.2, math.cos(math.radians(lat)))))


def make_storms(rng: random.Random, ac: "Flight", hours: float,  # pylint: disable=too-many-locals
                fields: bool = False) -> list:
    """Return made-up storms along the flight: clusters, lines and singles, born over hours.

    fields: also big fields of many similar cells (live: 170 storms in 9 clusters).
    Areas anywhere from 300 nm behind the start to 400 nm past the end of a straight flight,
    up to 300 nm either side of it (a hold stays near the start: around it).
    """
    storms, n = [], 0
    wind_hdg, wind_kt = rng.uniform(0, 360), rng.uniform(10, 50)
    length = 0.0 if ac.kind == "hold" else ac.kt * hours
    for _ in range(rng.randint(4, 10) + int(length / 300)):   # organised areas
        kind = rng.choice(("cluster", "line", "single") + (("field", "field") if fields else ()))
        alat, alon = move(ac.lat, ac.lon, ac.hdg, rng.uniform(-300, length + 400))
        clat, clon = move(alat, alon, ac.hdg + 90, rng.uniform(-300, 300))
        t0 = rng.uniform(-3600, hours * 3600)
        strength = rng.choice((48, 52, 56, 60, 64))
        count = {"cluster": rng.randint(3, 9), "line": rng.randint(4, 10), "single": 1,
                 "field": rng.randint(20, 60)}[kind]
        line_brg = rng.uniform(0, 180)
        for i in range(count):
            if kind == "line":
                lat, lon = move(clat, clon, line_brg, (i - count / 2) * rng.uniform(12, 30))
            else:
                lat, lon = move(clat, clon, rng.uniform(0, 360),
                                rng.uniform(0, 120 if kind == "field" else 40))
            n += 1
            storms.append(SimStorm(f"S{n:03d}", lat, lon, strength + rng.randint(-6, 3),
                                   rng.uniform(20, 300), t0 + rng.uniform(0, 1800),
                                   rng.uniform(1800, 7200), (wind_hdg + rng.uniform(-20, 20)) % 360,
                                   wind_kt * rng.uniform(0.7, 1.2),
                                   random.Random(n).choice((0, 0, 5, 15, 40))))
    return storms


def load_capture(path: str) -> tuple:
    """Return (meta, {key: ReplayStorm-to-be cells}, frame times) of a fw-weatherlib capture.

    The cells of every frame, tracked frame to frame as frankenweather does (fw_radar.track):
    a storm keeps its key while it is tracked.
    """
    with open(os.path.join(path, "meta.json"), encoding="utf-8") as f:
        meta = json.load(f)
    frames = []
    for d in sorted(glob.glob(os.path.join(path, "radar", "[0-9]*"))):
        if not os.path.exists(os.path.join(d, "cells.json")):   # still being captured
            continue
        with open(os.path.join(d, "cells.json"), encoding="utf-8") as f:
            raw = json.load(f)
        names = {fl.name for fl in dc_fields(fw_radar.Cell)}
        cells = [fw_radar.Cell(**{k: v for k, v in c.items() if k in names and
                                  k not in ("key", "motion", "prev", "age_s")})
                 for c in raw["cells"]]
        frames.append((raw["frame"], cells))
    tracks: dict = {}
    previous, prev_time = [], None
    for frame_time, cells in frames:
        if previous and 0 < frame_time - prev_time <= 1500:
            fw_radar.track(previous, cells, frame_time - prev_time)
        for c in cells:
            if c.is_cb:
                tracks.setdefault(c.key, {})[frame_time] = c
        previous, prev_time = cells, frame_time
    return meta, tracks, [t for t, _ in frames]


class Flight:  # pylint: disable=too-many-instance-attributes
    """A simple aircraft: cruise straight, one 90-degree turn, or a hold (always turning)."""

    def __init__(self, kind: str, rng: random.Random, lat: float = 0.0, lon: float = 0.0):
        """Start at (lat, lon) on a random heading; a random ND range."""
        self.kind, self.lat, self.lon = kind, lat, lon
        self.hdg = rng.uniform(0, 360)
        self.kt = 250.0 if kind == "hold" else 480.0
        self.nd_nm = rng.choice((40.0, 80.0, 160.0, 320.0))
        self.t_turn = rng.uniform(1200, 3600)
        self.route = False
        if ND_NM:
            self.nd_nm = ND_NM

    def step(self, t: float, dt: float) -> None:
        """Fly dt seconds."""
        if self.kind == "hold":
            self.hdg = (self.hdg + 3.0 * dt / 2.0) % 360 if (t // 120) % 2 else self.hdg
        elif self.kind == "turn" and self.t_turn <= t < self.t_turn + 30:
            self.hdg = (self.hdg + 90.0) % 360
        self.lat, self.lon = move(self.lat, self.lon, self.hdg, self.kt * dt / 3600.0)

    def route_off(self, t: float, lat: float, lon: float) -> Optional[float]:
        """Return how far (lat, lon) is from the route ahead (--route: the path it will fly)."""
        if not self.route or self.maneuvering:
            return None
        plat, plon, hdg, best = self.lat, self.lon, self.hdg, math.inf
        turned = self.kind != "turn" or t >= self.t_turn
        for i in range(ROUTE_AHEAD_NM // 20 + 1):
            best = min(best, P.dist_nm(plat, plon, lat, lon))
            if not turned and t + i * 20 / self.kt * 3600 >= self.t_turn:
                hdg, turned = (hdg + 90.0) % 360, True
            plat, plon = move(plat, plon, hdg, 20.0)
        return best

    @property
    def maneuvering(self) -> bool:
        """Holding: all round."""
        return self.kind == "hold"

    @property
    def visible_nm(self) -> float:
        """Return how far the crew may see a CB (window or ND)."""
        return max(WINDOW_NM, self.nd_nm)

    def off(self, lat: float, lon: float) -> float:
        """Return how far off the nose (lat, lon) is (degrees)."""
        return abs((P.bearing_deg(self.lat, self.lon, lat, lon) - self.hdg + 180) % 360 - 180)

    def sees(self, lat: float, lon: float) -> bool:
        """Return True if the crew may see a CB at (lat, lon) (window or ND; holding: all round)."""
        d = P.dist_nm(self.lat, self.lon, lat, lon)
        if self.maneuvering:
            return d <= self.visible_nm
        off = self.off(lat, lon)
        return (d <= WINDOW_NM and off <= WINDOW_DEG) or (d <= self.nd_nm and off <= ND_DEG)


@dataclass
class SimZone:  # pylint: disable=too-many-instance-attributes
    """A zone in the simulation: the storm its CB is on, and when that storm died (if it did)."""

    num: int
    lat: float
    lon: float
    storm: Optional[str] = None
    dead_since: Optional[float] = None
    placed_t: float = -1e9
    changed_t: float = -1e9       # when the crew last saw its CB change (CHANGE_GAP_S)


def strength_of(s, t: float) -> float:
    """Return fw_planner.strength() of a sim storm at t."""
    return P.strength(P.Storm(s.key, 0.0, 0.0, dbz=s.dbz(t), area_km2=s.area(t), lightning=s.ltg))


class Sim:  # pylint: disable=too-many-instance-attributes
    """One simulated frankenweather: its aircraft, zones and counts."""

    def __init__(self, ac: Flight, planner, band_nm: float):
        """Zones start far behind."""
        self.ac, self.planner, self.band_nm = ac, planner, band_nm
        self.zones = [SimZone(i + 1, *move(ac.lat, ac.lon, ac.hdg + 180, 300 + 20 * i))
                      for i in range(ZONES)]
        self.out = {"violations": [], "moves": 0, "cb_steps": 0, "steps": 0, "behind": 0,
                    "clusters_ahead": 0, "clusters_covered": 0, "plans": 0,
                    "seen_new": {}, "seen_gone": {}, "missed": 0, "missed_free": 0}
        self.last_plan, self.freed = -1e9, False
        self.miss_steps: dict = {}
        self.missed: set = set()
        storm_fields = {f.name for f in dc_fields(planner.Storm)}
        zone_fields = {f.name for f in dc_fields(planner.Zone)}
        ac_fields = {f.name for f in dc_fields(planner.Aircraft)}
        self.fields = (storm_fields, zone_fields, ac_fields)

    def cb_pos(self, z: SimZone, by_key: dict, t: float) -> tuple:
        """Return where zone z's CB is."""
        return by_key[z.storm].pos(t)

    def step(self, t: float, storms: list, by_key: dict) -> None:  # pylint: disable=too-many-locals,too-many-branches
        """Fly STEP_S seconds, apply the behind rule, replan when frankenweather would, check."""
        ac, zones, out = self.ac, self.zones, self.out
        ac.step(t, STEP_S)
        zones[0].lat, zones[0].lon = ac.lat, ac.lon          # the anchor follows the aircraft
        before = {z.num: (z.storm, self.cb_pos(z, by_key, t)) for z in zones if z.storm}
        for z in zones[1:]:                                   # the "behind" rule (frankenweather)
            if not z.storm or ac.sees(*self.cb_pos(z, by_key, t)):
                continue
            lat, lon = self.cb_pos(z, by_key, t)
            d = P.dist_nm(ac.lat, ac.lon, lat, lon)
            if ac.off(lat, lon) > 90 and d > (BEHIND_MANEUVER_NM if ac.maneuvering
                                              else BEHIND_CRUISE_NM):
                z.storm, z.dead_since = None, None
                z.lat, z.lon = move(ac.lat, ac.lon, ac.hdg, 150)
                out["behind"] += 1
                self.freed = True
        for z in zones:                                       # storms die: dead CBs kept in view
            if z.storm and by_key[z.storm].dbz(t) < 43:
                if z.dead_since is None:
                    z.dead_since = t
                if not ac.sees(*self.cb_pos(z, by_key, t)) or t - z.dead_since > DEAD_KEEP_S:
                    z.storm, z.dead_since = None, None
        tick = t // REPLAN_S > self.last_plan // REPLAN_S
        if tick or (self.freed and t - self.last_plan >= 30.0):
            self.freed, self.last_plan = False, t
            self.plan(t, storms, by_key, before)
        self.count_missed(t, storms, by_key)
        out["cb_steps"] += sum(1 for z in zones if z.storm)
        out["steps"] += 1

    def changed(self, t: float, z: SimZone, old: Optional[tuple], new: Optional[tuple]) -> None:
        """Check and count a planner change of zone z's CB (old/new: (key, pos) or None)."""
        ac, out = self.ac, self.out
        for what, cb in (("gone", old), ("new", new)):
            if cb is None:
                continue
            d = P.dist_nm(ac.lat, ac.lon, *cb[1])
            if d <= P.HARD_NM:
                out["violations"].append((t, f"zone {z.num}: CB {what} {d:.0f} nm away"))
            if ac.sees(*cb[1]):
                band = "<80" if d < 80 else "80-120" if d < 120 else "120-200" if d < 200 \
                    else "200+"
                key = "seen_" + what
                out[key][band] = out[key].get(band, 0) + 1
                if t - z.changed_t < getattr(self.planner, "CHANGE_GAP_S", 0.0) and what == "gone":
                    out["violations"].append(
                        (t, f"zone {z.num}: visible CB changed {t - z.changed_t:.0f} s after "
                            "the last change"))
                z.changed_t = t

    def plan(self, t: float, storms: list, by_key: dict, before: dict) -> None:  # pylint: disable=too-many-locals,too-many-branches
        """Run the planner as frankenweather would and apply its moves."""
        ac, zones, out, pl = self.ac, self.zones, self.out, self.planner
        sf, zf, af = self.fields
        out["plans"] += 1
        p_storms = []
        for s in storms:
            dbz = s.dbz(t)
            if dbz < 43:
                continue
            lat, lon = s.pos(t)
            d = P.dist_nm(ac.lat, ac.lon, lat, lon)
            if d > ac.visible_nm + self.band_nm:
                continue
            kw = {"dbz": dbz, "area_km2": s.area(t), "lightning": s.ltg,
                  "in_view": ac.sees(lat, lon),
                  "behind_nm": d if ac.off(lat, lon) > 90 and not ac.maneuvering else 0.0,
                  "route_off_nm": ac.route_off(t, lat, lon),
                  "big": dbz >= BIG_DBZ}
            p_storms.append(pl.Storm(s.key, lat, lon, **{k: v for k, v in kw.items() if k in sf}))
        p_zones = []
        for z in zones:
            dead = z.storm and z.dead_since is not None
            kw = {"storm": z.storm if z.storm and not dead else None, "fixed": z.num in FIXED,
                  "visible_cb": bool(z.storm) and ac.sees(*self.cb_pos(z, by_key, t)),
                  "cb_at": self.cb_pos(z, by_key, t) if dead else None,
                  "cb_big": bool(z.storm) and by_key[z.storm].dbz(t) >= BIG_DBZ,
                  "changed_s": z.changed_t}
            p_zones.append(pl.Zone(z.num, z.lat, z.lon, **{k: v for k, v in kw.items() if k in zf}))
        akw = {"maneuvering": ac.maneuvering, "behind_limit_nm": BEHIND_CRUISE_NM,
               "maneuver_reach_nm": BEHIND_MANEUVER_NM - 50.0, "visible_nm": ac.visible_nm,
               "now_s": t}
        plan = pl.plan(p_storms, p_zones, pl.Aircraft(ac.lat, ac.lon, ac.hdg,
                                                      **{k: v for k, v in akw.items() if k in af}))
        placed_now = set()
        for m in plan.moves:
            z = zones[m.zone - 1]
            if z.num in FIXED:
                out["violations"].append((t, f"fixed zone {z.num} moved"))
            old = (z.storm, self.cb_pos(z, by_key, t)) if z.storm else None
            z.storm = m.storm
            z.dead_since, z.lat, z.lon = None, m.lat, m.lon
            z.placed_t = t
            self.changed(t, z, old, (m.storm, (m.lat, m.lon)) if m.storm else None)
            out["moves"] += 1
            if m.storm:
                placed_now.add(z.num)
        for z in zones:   # spacing where CBs are placed
            if z.num not in placed_now:
                continue
            for o in zones:
                gap = (P.dist_nm(*self.cb_pos(z, by_key, t), *self.cb_pos(o, by_key, t))
                       if o is not z and o.storm else math.inf)
                if gap < P.SPACING_MIN_NM - 0.5:
                    out["violations"].append(
                        (t, f"zone {z.num}: placed {gap:.0f} nm from zone {o.num}'s CB"))
        del before
        # significant clusters (fw_planner.WEAK_CLUSTER_FRAC of the strongest) within 320 nm
        # ahead, and whether one of their storms has a CB
        shown = {z.storm for z in zones if z.storm}
        ahead = [s for s in p_storms if P.dist_nm(ac.lat, ac.lon, s.lat, s.lon) <= 320 and
                 P.relevance(s, P.Aircraft(ac.lat, ac.lon, ac.hdg)) == 1.0]
        inten: dict = {}
        for s in ahead:
            cid = plan.cluster_of.get(s.key, s.key)
            inten[cid] = inten.get(cid, 0.0) + P.strength(s)
        top = max(inten.values(), default=0.0)
        for cid, v in inten.items():
            if v >= P.WEAK_CLUSTER_FRAC * top:
                out["clusters_ahead"] += 1
                out["clusters_covered"] += any(s.key in shown for s in ahead
                                               if plan.cluster_of.get(s.key, s.key) == cid)

    def count_missed(self, t: float, storms: list, by_key: dict) -> None:
        """Count storms in view without a CB while a weaker storm in view has one (5 min).

        "missed": every zone's CB was in view (a weaker CB took the zone); "missed_free": a
        zone was empty or its CB out of view (the change cost kept the new CB from appearing).
        """
        ac = self.ac
        shown = [(z.storm, self.cb_pos(z, by_key, t)) for z in self.zones if z.storm]
        weakest = min((strength_of(by_key[k], t) for k, pos in shown
                       if ac.sees(*pos) and by_key[k].dbz(t) >= 43), default=None)
        if weakest is None:
            return
        keys = {k for k, _ in shown}
        free = any(not z.storm or not ac.sees(*self.cb_pos(z, by_key, t))   # movable for it
                   for z in self.zones if z.num not in FIXED)
        for s in storms:
            if s.key in keys or s.key in self.missed or s.dbz(t) < 43:
                continue
            lat, lon = s.pos(t)
            if (P.dist_nm(ac.lat, ac.lon, lat, lon) > WINDOW_NM or
                    ac.off(lat, lon) > VIEW_AHEAD_DEG or strength_of(s, t) <= weakest or
                    any(P.dist_nm(lat, lon, *pos) < P.SPACING_MIN_NM for _, pos in shown)):
                continue
            kind = "missed_free" if free else "missed"   # a zone was free: not placed (cost)
            n = self.miss_steps.get((s.key, kind), 0) + 1
            self.miss_steps[(s.key, kind)] = n
            if n >= MISSED_STEPS:
                self.missed.add(s.key)
                self.out[kind] += 1


def run(seed: int, hours: float, kind: str, planner, band_nm: float,  # pylint: disable=too-many-arguments,too-many-positional-arguments,too-many-locals
        fields: bool = False, capture: Optional[tuple] = None, route: bool = False) -> dict:
    """Fly one synthetic flight; return counts and rule violations.

    capture: (meta, tracks, frame times) from load_capture(): real storms, the flight starting
    at a random time within its frames: holding within 300 nm of the area's centre, else
    near its edge, heading across it.
    """
    rng = random.Random(seed)
    if capture is None:
        ac = Flight(kind, rng)
        storms = make_storms(rng, ac, hours, fields)
    else:
        meta, tracks, times = capture
        if kind == "hold":
            lat, lon = move(meta["lat"], meta["lon"], rng.uniform(0, 360), rng.uniform(0, 300))
            ac = Flight(kind, rng, lat, lon)
        else:   # from near the edge across the area (a random start left it within the hour)
            r = meta.get("radius_nm", 500.0) * 0.8
            lat, lon = move(meta["lat"], meta["lon"], rng.uniform(0, 360), r)
            ac = Flight(kind, rng, lat, lon)
            ac.hdg = (P.bearing_deg(lat, lon, meta["lat"], meta["lon"]) +
                      rng.uniform(-30.0, 30.0)) % 360
        span = max(0.0, times[-1] - times[0] - hours * 3600)
        t0 = times[0] + 600 + rng.uniform(0, span)
        storms = []
        for key, cells in tracks.items():
            st = ReplayStorm(key, t0)
            st.cells = cells
            storms.append(st)
    ac.route = route
    by_key = {s.key: s for s in storms}
    sim = Sim(ac, planner, band_nm)
    out = sim.out
    out.update({"seed": seed, "kind": kind})
    t = 0.0
    while t < hours * 3600:
        sim.step(t, storms, by_key)
        t += STEP_S
    return out


def load_planner(path: Optional[str]):
    """Return the planner module: fw_planner, or the one in file path (an older version)."""
    if not path:
        return fw_planner
    spec = importlib.util.spec_from_file_location("planner_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main() -> int:  # pylint: disable=too-many-locals
    """Run the synthetic flights; print a summary; exit 1 on any rule violation."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--kind", choices=("cruise", "hold", "turn", "all"), default="all")
    ap.add_argument("--fields", action="store_true",
                    help="also big fields of many similar cells (as live)")
    ap.add_argument("--wxlib", nargs="?", const=WXLIB, default=None, metavar="DIR",
                    help="real radar storms: every capture in DIR (default %(const)s), or one "
                         "capture directory")
    ap.add_argument("--band", type=float, default=BAND_NM, metavar="NM",
                    help="storms up to NM past what the crew may see (default %(default)g)")
    ap.add_argument("--route", action="store_true",
                    help="cruise and turn flights follow a route (the path they fly): storms "
                         "get their distance from it (frankenweather --route-zones)")
    ap.add_argument("--nd", type=float, default=0.0, metavar="NM",
                    help="every flight's ND range (default: random 40-320 nm)")
    ap.add_argument("--planner", default=None, metavar="FILE",
                    help="test this planner module file instead of fw_planner (an old version)")
    a = ap.parse_args()
    global ND_NM  # pylint: disable=global-statement
    ND_NM = a.nd
    planner = load_planner(a.planner)
    kinds = ("cruise", "hold", "turn") if a.kind == "all" else (a.kind,)
    captures = []
    if a.wxlib:
        dirs = [a.wxlib] if os.path.exists(os.path.join(a.wxlib, "meta.json")) else \
            sorted(d for d in glob.glob(os.path.join(a.wxlib, "*"))
                   if os.path.exists(os.path.join(d, "meta.json")))
        captures = [load_capture(d) for d in dirs]
        captures = [c for c in captures if len(c[2]) >= 6]
    bad, tot = [], {}
    for i in range(a.runs):
        cap = captures[i % len(captures)] if captures else None
        hours = min(a.hours, (cap[2][-1] - cap[2][0] - 900) / 3600) if cap else a.hours
        r = run(a.seed + i, hours, kinds[i % len(kinds)], planner, a.band, a.fields, cap,
                a.route)
        r["hours"] = hours
        for k in ("moves", "cb_steps", "steps", "clusters_ahead", "clusters_covered", "behind",
                  "missed", "missed_free", "hours"):
            tot[k] = tot.get(k, 0) + r[k]
        for k in ("seen_new", "seen_gone"):
            for band, n in r[k].items():
                tot.setdefault(k, {})[band] = tot.get(k, {}).get(band, 0) + n
        if r["violations"]:
            bad.append(r)
    fh = max(1e-9, tot["hours"])
    print(f"{a.runs} flights, {fh:.0f} h{' (real radar storms)' if captures else ''}: CBs shown "
          f"{tot['cb_steps'] / max(1, tot['steps']):.1f} on average, zone moves "
          f"{tot['moves'] / fh:.1f}/h, significant clusters ahead with a CB "
          f"{100 * tot['clusters_covered'] / max(1, tot['clusters_ahead']):.0f}%")
    print(f"strong storms in view without a CB while a weaker one had a CB: every zone in view "
          f"{tot['missed'] / fh:.2f}/h, a zone free (too costly to appear in view) "
          f"{tot['missed_free'] / fh:.2f}/h")
    for k, label in (("seen_new", "CBs appearing in view"), ("seen_gone", "CBs going in view")):
        bands = tot.get(k, {})
        print(f"{label}: {sum(bands.values()) / fh:.2f}/h (" +
              ", ".join(f"{b} nm {bands.get(b, 0) / fh:.2f}"
                        for b in ("<80", "80-120", "120-200", "200+")) + ")")
    for r in bad[:10]:
        print(f"seed {r['seed']} ({r['kind']}): {len(r['violations'])} violations, first at "
              f"{r['violations'][0][0] / 60:.0f} min: {r['violations'][0][1]}")
    print(f"flights with rule violations: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
