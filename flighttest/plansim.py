"""Synthetic CB placement test: fly the planner (fw_planner) through made-up storms, fast.

No PSX, no network, no clock: storms with a life cycle (growth, maturity, decay) in clusters and
lines move with a wind; a simple aircraft cruises, turns or holds; every RADAR_S the planner
replans and its moves are applied; every STEP_S the rules are checked:
  - no new CB where the crew may see it (out of the window or on the ND)
  - no CB the crew may see moved or removed, except a dead storm's CB once out of view (or
    after DEAD_KEEP_S)
  - no two CBs closer than fw_planner.SPACING_MIN_NM
  - fixed zones never moved
Counted too: CBs shown, clusters ahead with a CB, zone moves per hour. One run takes well under
a second for hours of flight; any failure can be repeated from its seed.

Usage: python3 -m flighttest.plansim [--runs N] [--seed S] [--hours H] [--kind cruise|hold|turn]
       [--pair NM]   (two sims NM apart in trail: how many CBs both show)
       [--replan S]  (also replan every S seconds, on the clock)
"""
import argparse
import math
import random
import sys
from dataclasses import dataclass
from typing import Optional

import fw_planner as P

STEP_S = 30.0
RADAR_S = 600.0
WINDOW_NM, WINDOW_DEG = 200.0, 120.0
ND_DEG = 90.0
DEAD_KEEP_S = 1800.0
BEHIND_CRUISE_NM = 50.0       # frankenweather's "behind" rule: a zone this far behind is moved
BEHIND_MANEUVER_NM = 200.0    # ... holding: this far away, once a turn puts it behind
ZONES = 7
NEW_BEYOND_NM = 60.0          # storms only this far past what the crew may see (frankenweather's
#                               radar range, _CB_NEW_BEYOND_NM): new CBs are born in that band
FIXED = (1,)                  # zone 1: the anchor (it follows the aircraft)


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
    ltg: int = 0                  # lightning near it (flash pixels), before each sim's own noise

    def dbz(self, t: float) -> int:
        """Return the storm's strength at t (0: not alive; radar CB threshold 43)."""
        age = t - self.born_s
        if age < 0 or age > self.life_s:
            return 0
        frac = min(1.0, age / 900.0, (self.life_s - age) / 900.0)   # 15 min up and down
        return int(35 + (self.peak_dbz - 35) * frac)

    def pos(self, t: float) -> tuple:
        """Return where the storm is at t."""
        d = self.kt * max(0.0, t - self.born_s) / 3600.0
        return move(self.lat, self.lon, self.hdg, d)


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


class Flight:
    """A simple aircraft: cruise straight, one 90-degree turn, or a hold (always turning)."""

    def __init__(self, kind: str, rng: random.Random):
        """Start at (0, 0) on a random heading; a random ND range."""
        self.kind, self.lat, self.lon = kind, 0.0, 0.0
        self.hdg = rng.uniform(0, 360)
        self.kt = 250.0 if kind == "hold" else 480.0
        self.nd_nm = rng.choice((40.0, 80.0, 160.0, 320.0))
        self.t_turn = rng.uniform(1200, 3600)

    def step(self, t: float, dt: float) -> None:
        """Fly dt seconds."""
        if self.kind == "hold":
            self.hdg = (self.hdg + 3.0 * dt / 2.0) % 360 if (t // 120) % 2 else self.hdg
        elif self.kind == "turn" and self.t_turn <= t < self.t_turn + 30:
            self.hdg = (self.hdg + 90.0) % 360
        self.lat, self.lon = move(self.lat, self.lon, self.hdg, self.kt * dt / 3600.0)

    @property
    def maneuvering(self) -> bool:
        """Holding: all round."""
        return self.kind == "hold"

    def sees(self, lat: float, lon: float) -> bool:
        """Return True if the crew may see a CB at (lat, lon) (window or ND)."""
        d = P.dist_nm(self.lat, self.lon, lat, lon)
        off = abs((P.bearing_deg(self.lat, self.lon, lat, lon) - self.hdg + 180) % 360 - 180)
        return (d <= WINDOW_NM and off <= WINDOW_DEG) or (d <= self.nd_nm and off <= ND_DEG)

    def may_see(self, lat: float, lon: float) -> bool:
        """Return True if a new CB at (lat, lon) would be seen (holding: anywhere in range)."""
        if self.maneuvering:
            return P.dist_nm(self.lat, self.lon, lat, lon) <= max(WINDOW_NM, self.nd_nm)
        return self.sees(lat, lon)


@dataclass
class SimZone:  # pylint: disable=too-many-instance-attributes
    """A zone in the simulation: the storm its CB is on, and when that storm died (if it did)."""

    num: int
    lat: float
    lon: float
    storm: Optional[str] = None
    dead_since: Optional[float] = None
    placed_t: float = -1e9
    placed_off: float = 0.0       # how far off the nose it was placed (degrees)
    model_at: Optional[tuple] = None   # a startup CB without a storm (model CB) until model_until
    model_until: float = 0.0


class Sim:  # pylint: disable=too-many-instance-attributes
    """One simulated frankenweather: its aircraft, zones and counts (a pair test runs two)."""

    def __init__(self, ac: Flight, tag: str, plan_delay_s: float = 0.0,  # pylint: disable=too-many-arguments,too-many-positional-arguments
                 startup: Optional[random.Random] = None, replan_s: float = 0.0,
                 noise: Optional[random.Random] = None):
        """Zones start far behind; tag names its storms (each sim's keys differ, as live).

        startup: some zones start with a model CB of their own (no storm) somewhere ahead that
        goes within the first 4 minutes (live: the zones' first weather, each sim's own).
        """
        self.ac, self.tag, self.plan_delay_s, self.replan_s = ac, tag, plan_delay_s, replan_s
        self.noise = noise            # each sim's own lightning count (its own MTG downloads)
        self.zones = [SimZone(i + 1, *move(ac.lat, ac.lon, ac.hdg + 180, 300 + 20 * i))
                      for i in range(ZONES)]
        for z in self.zones[1:] if startup else ():
            if startup.random() < 0.6:
                z.lat, z.lon = move(ac.lat, ac.lon, ac.hdg + startup.uniform(-100, 100),
                                    startup.uniform(50, 300))
                z.model_at, z.model_until = (z.lat, z.lon), startup.uniform(20, 240)
        self.out = {"violations": [], "moves": 0, "cb_steps": 0, "steps": 0, "behind": 0,
                    "after_turn": 0, "clusters_ahead": 0, "clusters_covered": 0, "plans": 0}
        self.next_frame, self.last_plan, self.freed = plan_delay_s, -1e9, False

    def key(self, storm_key: str) -> str:
        """Return this sim's key for a storm (live, keys carry the frame a sim first saw it in)."""
        return storm_key if not self.tag else f"{self.tag}{999 - int(storm_key[1:]):03d}"

    def step(self, t: float, storms: list, by_key: dict) -> None:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        """Fly STEP_S seconds, apply the behind rule, replan when frankenweather would, check."""
        ac, zones, out = self.ac, self.zones, self.out
        ac.step(t, STEP_S)
        zones[0].lat, zones[0].lon = ac.lat, ac.lon          # the anchor follows the aircraft
        shown_before = {z.num: (z.storm, ac.sees(*cb_pos(z, by_key, t))) for z in zones if z.storm}
        for z in zones[1:]:                                   # the "behind" rule (frankenweather)
            if not z.storm or ac.sees(*cb_pos(z, by_key, t)):
                continue
            lat, lon = cb_pos(z, by_key, t)
            d = P.dist_nm(ac.lat, ac.lon, lat, lon)
            off = abs((P.bearing_deg(ac.lat, ac.lon, lat, lon) - ac.hdg + 180) % 360 - 180)
            if off > 90 and d > (BEHIND_MANEUVER_NM if ac.maneuvering else BEHIND_CRUISE_NM):
                turned = ac.kind == "turn" and ac.t_turn <= t < ac.t_turn + 900
                if turned:
                    out["after_turn"] += 1   # a 90-degree turn makes zones ahead "behind"
                elif t - z.placed_t < 600 and z.placed_off > P.AHEAD_DEG:   # placed abeam/behind
                    out["violations"].append((t, f"zone {z.num}: placed {t - z.placed_t:.0f} s ago "
                                                 "and moved by the behind rule"))
                z.storm, z.dead_since = None, None
                z.lat, z.lon = move(ac.lat, ac.lon, ac.hdg, 150)
                out["behind"] += 1
                self.freed = True                             # frankenweather replans soon
        for z in zones:                                       # startup model CBs go
            if z.model_at and t >= z.model_until:
                z.model_at = None
                self.freed = True
        for z in zones:                                       # storms die: dead CBs kept in view
            if z.storm and by_key[z.storm].dbz(t) < 43:
                if z.dead_since is None:
                    z.dead_since = t
                if not ac.sees(*cb_pos(z, by_key, t)) or t - z.dead_since > DEAD_KEEP_S:
                    z.storm, z.dead_since = None, None
        # frankenweather replans on every new radar frame (each sim notices it a little later
        # or sooner) and, at most every 30 s, when the behind rule freed zones
        # (replan_s: also every replan_s on the clock, the same moments in every sim)
        tick = self.replan_s and t // self.replan_s > self.last_plan // self.replan_s
        if t >= self.next_frame or tick or (self.freed and t - self.last_plan >= 30.0):
            if t >= self.next_frame:
                self.next_frame += RADAR_S
            self.freed, self.last_plan = False, t
            self.plan(t, storms, by_key)
        # the rules
        for z in zones:
            if not z.storm:
                continue
            seen_now = ac.sees(*cb_pos(z, by_key, t))
            before = shown_before.get(z.num)
            if seen_now and (before is None or before[0] != z.storm) and t >= 120:
                out["violations"].append((t, f"zone {z.num}: new CB {z.storm} in view"))
        for num, (key, was_seen) in shown_before.items():
            z = zones[num - 1]
            died = by_key[key].dbz(t) < 43   # a dead storm's CB (out of view, or kept 30 min)
            if was_seen and z.storm != key and not died:
                out["violations"].append((t, f"zone {num}: visible CB {key} moved/removed"))
        out["cb_steps"] += sum(1 for z in zones if z.storm)
        out["steps"] += 1

    def plan(self, t: float, storms: list, by_key: dict) -> None:  # pylint: disable=too-many-locals
        """Run the planner as frankenweather would and apply its moves."""
        ac, zones, out = self.ac, self.zones, self.out
        out["plans"] += 1
        back = {}
        p_storms = []
        for s in storms:
            if s.dbz(t) < 43:
                continue
            lat, lon = s.pos(t)
            d = P.dist_nm(ac.lat, ac.lon, lat, lon)
            if d > max(WINDOW_NM, ac.nd_nm) + NEW_BEYOND_NM:
                continue
            off = abs((P.bearing_deg(ac.lat, ac.lon, lat, lon) - ac.hdg + 180) % 360 - 180)
            k = self.key(s.key)
            back[k] = s.key
            ltg = max(0, s.ltg + self.noise.randint(-8, 8)) if self.noise else s.ltg
            p_storms.append(P.Storm(k, lat, lon, dbz=s.dbz(t), area_km2=s.area_km2, lightning=ltg,
                                    in_view=ac.may_see(lat, lon),
                                    behind_nm=d if off > 90 and not ac.maneuvering else 0.0))
        p_zones = [P.Zone(z.num, z.lat, z.lon,
                          storm=self.key(z.storm) if z.storm and z.dead_since is None else None,
                          fixed=z.num in FIXED,
                          visible_cb=(bool(z.storm) and ac.sees(*cb_pos(z, by_key, t))) or
                          (bool(z.model_at) and ac.sees(*z.model_at)),
                          cb_at=cb_pos(z, by_key, t) if z.storm and z.dead_since is not None
                          else z.model_at)
                   for z in zones]
        plan = P.plan(p_storms, p_zones,
                      P.Aircraft(ac.lat, ac.lon, ac.hdg, maneuvering=ac.maneuvering,
                                 behind_limit_nm=BEHIND_CRUISE_NM,
                                 maneuver_reach_nm=BEHIND_MANEUVER_NM - 50.0))
        placed_now = set()
        for m in plan.moves:
            z = zones[m.zone - 1]
            if z.num in FIXED:
                out["violations"].append((t, f"fixed zone {z.num} moved"))
            z.storm = back[m.storm] if m.storm else None
            z.dead_since, z.lat, z.lon, z.model_at = None, m.lat, m.lon, None
            z.placed_t = t
            z.placed_off = abs((P.bearing_deg(ac.lat, ac.lon, m.lat, m.lon) - ac.hdg + 180)
                               % 360 - 180) if m.storm else 0.0
            out["moves"] += 1
            if m.storm:
                placed_now.add(z.num)
        # spacing: checked where CBs are placed (storms moving apart or together later
        # is the weather's doing; a visible CB isn't moved for it)
        for z in zones:
            if z.num not in placed_now:
                continue
            for o in zones:
                gap = (P.dist_nm(*cb_pos(z, by_key, t), *cb_pos(o, by_key, t))
                       if o is not z and o.storm else math.inf)
                if gap < P.SPACING_MIN_NM - 0.5:
                    out["violations"].append(
                        (t, f"zone {z.num}: placed {gap:.0f} nm from zone {o.num}'s CB"))
        # clusters within 320 nm ahead and whether one of their storms has a CB
        shown = {self.key(z.storm) for z in zones if z.storm}
        for cid in set(plan.cluster_of.values()):
            members = [s for s in p_storms if plan.cluster_of.get(s.key) == cid]
            if any(P.dist_nm(ac.lat, ac.lon, s.lat, s.lon) <= 320 and
                   P.relevance(s, P.Aircraft(ac.lat, ac.lon, ac.hdg)) == 1.0 for s in members):
                out["clusters_ahead"] += 1
                out["clusters_covered"] += any(s.key in shown for s in members)


def run(seed: int, hours: float, kind: str, pair_nm: Optional[float] = None,  # pylint: disable=too-many-locals,too-many-arguments,too-many-positional-arguments
        replan_s: float = 0.0, fields: bool = False, noise: bool = False) -> dict:
    """Fly one synthetic flight (two in trail with pair_nm); return counts and rule violations.

    A pair: the second aircraft pair_nm behind on the same track, with its own zones, storm
    keys and a frame noticed up to a minute apart; "same" counts, every step, the CBs both
    crews could see (within WINDOW_NM, ahead) and how many of them the other sim shows too.
    """
    rng = random.Random(seed)
    ac = Flight(kind, rng)
    storms = make_storms(rng, ac, hours, fields)
    by_key = {s.key: s for s in storms}
    sims = [Sim(ac, "", replan_s=replan_s)]
    if pair_nm is not None:   # each sim: its own startup CBs, its first frame up to 2 min late
        ac2 = Flight(kind, random.Random(seed))
        ac2.lat, ac2.lon = move(ac.lat, ac.lon, ac.hdg + 180, pair_nm)
        sims = [Sim(ac, "", plan_delay_s=rng.uniform(0, 120), startup=random.Random(seed * 2),
                    replan_s=replan_s, noise=random.Random(seed * 3) if noise else None),
                Sim(ac2, "T", plan_delay_s=rng.uniform(0, 120),
                    startup=random.Random(seed * 2 + 1), replan_s=replan_s,
                    noise=random.Random(seed * 3 + 1) if noise else None)]
    out = sims[0].out
    out.update({"seed": seed, "kind": kind, "both": 0, "same": 0})
    t = 0.0
    while t < hours * 3600:
        for sim in sims:
            sim.step(t, storms, by_key)
        if len(sims) == 2:
            sa = {z.storm for z in sims[0].zones if z.storm}
            sb = {z.storm for z in sims[-1].zones if z.storm}
            for key in sa | sb:
                lat, lon = by_key[key].pos(t)
                if all(sim.ac.sees(lat, lon) for sim in sims):
                    out["both"] += 1
                    out["same"] += key in sa and key in sb
        t += STEP_S
    if len(sims) == 2:
        out["violations"] += [(t, f"(trailing sim) {v}") for t, v in sims[1].out["violations"]]
    return out


def cb_pos(z: SimZone, by_key: dict, t: float) -> tuple:
    """Return where zone z's CB is: on its storm (the storm moves; a dead one where it ended)."""
    return by_key[z.storm].pos(min(t, by_key[z.storm].born_s + by_key[z.storm].life_s))


def main() -> int:
    """Run the synthetic flights; print a summary; exit 1 on any rule violation."""
    ap = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--hours", type=float, default=3.0)
    ap.add_argument("--kind", choices=("cruise", "hold", "turn", "all"), default="all")
    ap.add_argument("--pair", type=float, default=None, metavar="NM",
                    help="fly a second sim NM behind and count how many CBs both show")
    ap.add_argument("--replan", type=float, default=0.0, metavar="S",
                    help="also replan every S seconds on the clock (default: frames only)")
    ap.add_argument("--fields", action="store_true",
                    help="also big fields of many similar cells (as live)")
    ap.add_argument("--noise", action="store_true",
                    help="pairs: each sim's own lightning counts (its own MTG downloads)")
    a = ap.parse_args()
    kinds = ("cruise", "hold", "turn") if a.kind == "all" else (a.kind,)
    bad, totals = [], {"moves": 0, "cb_steps": 0, "steps": 0, "ahead": 0, "covered": 0,
                       "both": 0, "same": 0}
    for i in range(a.runs):
        r = run(a.seed + i, a.hours, kinds[i % len(kinds)], a.pair, a.replan, a.fields, a.noise)
        totals["both"] += r["both"]
        totals["same"] += r["same"]
        totals["moves"] += r["moves"]
        totals["cb_steps"] += r["cb_steps"]
        totals["steps"] += r["steps"]
        totals["ahead"] += r["clusters_ahead"]
        totals["covered"] += r["clusters_covered"]
        if r["violations"]:
            bad.append(r)
    flight_h = a.runs * a.hours
    shown = totals['cb_steps'] / max(1, totals['steps'])
    print(f"{a.runs} flights, {flight_h:.0f} h: CBs shown {shown:.1f}"
          f" on average, zone moves {totals['moves'] / flight_h:.1f}/h, clusters ahead with a CB "
          f"{100 * totals['covered'] / max(1, totals['ahead']):.0f}%")
    if a.pair is not None:
        print(f"pair {a.pair:g} nm apart: of the CBs both crews could see, both sims show "
              f"{100 * totals['same'] / max(1, totals['both']):.0f}%")
    for r in bad[:10]:
        print(f"seed {r['seed']} ({r['kind']}): {len(r['violations'])} violations, first at "
              f"{r['violations'][0][0] / 60:.0f} min: {r['violations'][0][1]}")
    print(f"flights with rule violations: {len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
