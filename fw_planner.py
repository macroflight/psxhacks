"""CB placement planner for frankenweather (--cb-planner; rules agreed with the user 2026-11-19).

Decides which storms the movable weather zones show. Pure functions only: no clock (the caller
passes the time), no downloads, no PSX. frankenweather and the synthetic test runner
(flighttest/plansim.py) give it the storms, the zones and the aircraft, and apply what it
returns. docs/developers/cb-planner.md describes the rules.

Rules, in order (user, 2026-11-19):
1. Don't startle the pilot, as a cost that grows the closer a CB is: within HARD_NM
   (HARD_BIG_NM for a big CB) no CB appears, disappears or moves; from there out to what the
   crew may see a change must win by a margin (change_cost()); a CB the crew may see changes
   at most once per CHANGE_GAP_S; beyond what the crew may see any gain will do (HIDDEN_KEEP
   against flapping).
2. CBs only on real storms (the storms given).
3. (Sameness between sims: dropped 2026-11-19; the plan is still deterministic.)
4. Each area of storms first gets one CB (a cluster's n-th further CB scores DIVERSITY ** n),
   but only areas at least WEAK_CLUSTER_FRAC as intense as the most intense one; weaker ones
   only get zones left over.
5. More CBs where there is more activity (stronger storms score more).

Never moved: fixed zones (the anchor, airports, the focused zone). CBs closer than
SPACING_MIN_NM are not placed; closer than SPACING_SOFT_NM score less (PSX shrinks a CB's
echo near another zone and only the aircraft's zone's CB turbulence counts, measured
2026-11-15/16).
"""
import math
from dataclasses import dataclass, field
from typing import Optional

EARTH_NM = 3440.065
CLUSTER_LINK_NM = 25.0     # storms this close (chained) are one cluster
DIVERSITY = 0.25           # a cluster's n-th further CB scores DIVERSITY ** n (priority 4 before 5)
WEAK_CLUSTER_FRAC = 0.25   # a cluster less intense than this x the most intense one (the sum of
#                            its storms' scores) gets no CB of its own first (user 2026-11-19:
#                            a very large intense cluster and a small weak one: all CBs to the
#                            intense one) ...
WEAK_CLUSTER_SKIP = 6      # ... its first CB scores as a cluster's 7th (zones left over only)
SPACING_MIN_NM = 25.0      # no CB this close to another
SPACING_SOFT_NM = 45.0     # ... and less score up to this (PSX draws a CB full size from ~45 nm)
HARD_NM = 40.0             # no CB appears, disappears or moves this close to the aircraft ...
HARD_BIG_NM = 60.0         # ... or this close for a big CB (6/8 or more, or tops above 40,000 ft)
#                            (user 2026-11-06/19: CBs popping 10-30 nm away on approach)
CHANGE_COST = ((60.0, 3.0), (100.0, 1.5))   # a change the crew may see must win by this factor
#                            at this distance (between: linear; nearer: the first; out to what
#                            the crew may see: down to EDGE_COST)
EDGE_COST = 1.2            # ... at the edge of what the crew may see
HIDDEN_KEEP = 1.1          # a CB the crew can't see changes for a storm scoring this much more
CHANGE_GAP_S = 600.0       # a CB the crew may see changes at most once in this long
DEAD_SCORE = 1.0           # what a CB without a storm (a dead storm's, a model CB) is worth
APPEAR_BASE = 2.5          # a new CB the crew would see appear must be worth this x its change
#                            cost (a free zone's alternative is no CB: without a bar any storm
#                            at 45 nm in view would do; 2.5: about a 45-50 dBZ storm)
AHEAD_DEG = 75.0           # relevance: ahead within this (frankenweather's storm view)
ABEAM_DEG = 110.0          # ... abeam up to this
ROUTE_NM = 60.0            # with a route: storms within this of the route ahead count as ahead
RELEVANCE = {"ahead": 1.0, "abeam": 0.5, "behind": 0.05}
MIN_VALUE = 0.25           # a zone's CB makes way only for a new CB worth at least this (a
#                            zone without a CB takes any): live 2026-11-19, zones moved every
#                            minute between leftover storms scoring 0-0.4


@dataclass
class Storm:  # pylint: disable=too-many-instance-attributes
    """A storm a CB may go on (a radar cell, a made-up storm), as frankenweather sees it now."""

    key: str
    lat: float
    lon: float
    dbz: int = 45
    area_km2: float = 50.0
    lightning: int = 0          # flashes near it (MTG), if known
    in_view: bool = False       # a new CB here would be seen by the crew (frankenweather decides)
    route_off_nm: Optional[float] = None   # distance from the route ahead, if following one
    behind_nm: float = 0.0      # how far behind the aircraft it is (0: ahead or abeam), as the
    #                             caller's "behind" rule measures it (along the route if any)
    big: bool = False           # its CB would be big (HARD_BIG_NM)


@dataclass
class Zone:  # pylint: disable=too-many-instance-attributes
    """A weather zone: where it is, which storm it shows, whether it may be moved."""

    num: int
    lat: float
    lon: float
    storm: Optional[str] = None   # the key of the storm its CB is on, if any
    fixed: bool = False           # anchor, airport or focused zone: never moved for a CB
    visible_cb: bool = False      # the crew may see its CB
    cb_at: Optional[tuple] = None   # (lat, lon) of a CB it shows without a storm (a dead storm's
    #                                 CB kept in view, a model CB): other CBs keep their distance
    cb_big: bool = False          # its CB is big (HARD_BIG_NM)
    changed_s: float = -math.inf  # when its CB last changed (the caller's clock, seconds)


@dataclass
class Aircraft:  # pylint: disable=too-many-instance-attributes
    """Where the aircraft is and how it flies."""

    lat: float
    lon: float
    hdg: float                    # the heading to plan for (HDG SEL: the selected one)
    maneuvering: bool = False     # holding or vectored: all round
    behind_limit_nm: float = math.inf   # the caller moves zones farther behind than this away
    maneuver_reach_nm: float = math.inf   # holding or vectored: no storm farther than this
    visible_nm: float = 200.0     # how far the crew may see a CB (change_cost()'s edge)
    now_s: float = 0.0            # the caller's clock (Zone.changed_s)


@dataclass
class Move:
    """A planned change: zone num goes onto storm key at (lat, lon); storm None: its CB goes."""

    zone: int
    storm: Optional[str]
    lat: float
    lon: float
    score: float
    was: Optional[str] = None     # for the log: the storm its CB was on (as given), if any
    was_value: float = 0.0        # ... and what keeping it was worth


@dataclass
class Plan:
    """The planner's result: the moves, and every storm's cluster and score (for logs and tests)."""

    moves: list = field(default_factory=list)
    cluster_of: dict = field(default_factory=dict)
    score_of: dict = field(default_factory=dict)


def dist_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the great-circle distance in nm."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    c = math.sin(p1) * math.sin(p2) + math.cos(p1) * math.cos(p2) * math.cos(dl)
    return EARTH_NM * math.acos(max(-1.0, min(1.0, c)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return the initial great-circle bearing from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return math.degrees(math.atan2(y, x)) % 360.0


def clusters(storms: list) -> dict:
    """Return {storm key: cluster id}: storms within CLUSTER_LINK_NM, chained, share one.

    The id is the smallest key in the cluster, so it is the same whatever the order.
    """
    parent = {s.key: s.key for s in storms}

    def root(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for i, a in enumerate(storms):
        for b in storms[i + 1:]:
            if dist_nm(a.lat, a.lon, b.lat, b.lon) <= CLUSTER_LINK_NM:
                ra, rb = root(a.key), root(b.key)
                if ra != rb:
                    parent[max(ra, rb)] = min(ra, rb)
    return {s.key: root(s.key) for s in storms}


def strength(storm: Storm) -> float:
    """Return how much a storm deserves a CB (priority 5): its dBZ class, area and lightning."""
    dbz_class = max(0, min(storm.dbz, 65) - 40) // 5 + 1     # 43-44: 1, 45-49: 2, ... 65: 6
    return dbz_class * (1.0 + min(storm.area_km2, 400.0) / 400.0) * ltg_factor(storm.lightning)


def ltg_factor(flashes: int) -> float:
    """Return the score factor for lightning near a storm, in steps."""
    return 1.0 if flashes < 5 else 1.15 if flashes < 20 else 1.3 if flashes < 50 else 1.5


def _off_nose(storm: Storm, ac: Aircraft) -> float:
    """Return how far off the aircraft's nose a storm is (degrees, 0-180)."""
    return abs((bearing_deg(ac.lat, ac.lon, storm.lat, storm.lon) - ac.hdg + 180.0) % 360.0 - 180.0)


def relevance(storm: Storm, ac: Aircraft) -> float:
    """Return how much the crew will meet a storm: ahead (or near the route), abeam, behind."""
    if ac.maneuvering:
        return RELEVANCE["ahead"]
    if _near_route(storm):
        return RELEVANCE["ahead"]
    off = _off_nose(storm, ac)
    if off <= AHEAD_DEG:
        return RELEVANCE["ahead"]
    return RELEVANCE["abeam"] if off <= ABEAM_DEG else RELEVANCE["behind"]


def spacing_factor(lat: float, lon: float, placed: list) -> float:
    """Return the score factor for a CB at (lat, lon) this near the CBs placed (0: too close)."""
    near = min((dist_nm(lat, lon, la, lo) for la, lo in placed), default=math.inf)
    if near < SPACING_MIN_NM:
        return 0.0
    if near >= SPACING_SOFT_NM:
        return 1.0
    return (near - SPACING_MIN_NM) / (SPACING_SOFT_NM - SPACING_MIN_NM)


def hard_nm(big: bool) -> float:
    """Return how close to the aircraft a CB never appears, disappears or moves."""
    return HARD_BIG_NM if big else HARD_NM


def change_cost(dist: float, visible: bool, visible_nm: float, big: bool = False) -> float:
    """Return by what factor a change of a CB at dist nm must win (inf: never).

    Within hard_nm(): inf. Not visible: 1.0. Visible: CHANGE_COST's points, then down to
    EDGE_COST at the edge of what the crew may see (visible_nm).
    """
    if dist <= hard_nm(big):
        return math.inf
    if not visible:
        return 1.0
    pts = list(CHANGE_COST) + [(max(visible_nm, CHANGE_COST[-1][0] + 1.0), EDGE_COST)]
    if dist <= pts[0][0]:
        return pts[0][1]
    for (d0, c0), (d1, c1) in zip(pts, pts[1:]):
        if dist <= d1:
            return c0 + (c1 - c0) * (dist - d0) / (d1 - d0)
    return EDGE_COST


def _usable(storms: list, ac: Aircraft) -> list:
    """Return the storms a zone may go to: not where the caller's "behind" rule moves it away.

    Behind the aircraft, or (holding) farther than the maneuvering reach, where every turn
    makes it "behind"; nor abeam and farther than the behind limit (behind a minute later).
    """
    return [s for s in storms if s.behind_nm <= 0.0 and not (
        ac.maneuvering and dist_nm(ac.lat, ac.lon, s.lat, s.lon) > ac.maneuver_reach_nm) and not (
        not ac.maneuvering and not _near_route(s) and _off_nose(s, ac) > AHEAD_DEG and
        dist_nm(ac.lat, ac.lon, s.lat, s.lon) > ac.behind_limit_nm)]


def _near_route(storm: Storm) -> bool:
    """Return True if the storm is near the route ahead (it counts as ahead)."""
    return storm.route_off_nm is not None and storm.route_off_nm <= ROUTE_NM


class _Planning:  # pylint: disable=too-many-instance-attributes
    """One plan()'s working state."""

    def __init__(self, storms: list, zones: list, ac: Aircraft):
        """Sort the zones into locked and movable; score the storms; find the weak clusters."""
        self.ac = ac
        self.result = Plan()
        self.all_by_key = {s.key: s for s in storms}
        usable = _usable(storms, ac)
        self.by_key = {s.key: s for s in usable}
        self.storms = sorted(usable, key=lambda s: s.key)
        self.result.cluster_of = clusters(storms)
        self.base = {s.key: strength(s) * relevance(s, ac) for s in storms}
        intensity: dict = {}
        for s in self.storms:
            cid = self.result.cluster_of[s.key]
            intensity[cid] = intensity.get(cid, 0.0) + self.base[s.key]
        top = max(intensity.values(), default=0.0)
        # weak clusters: their first CB counts as their (WEAK_CLUSTER_SKIP + 1)-th
        self.in_cluster = {cid: WEAK_CLUSTER_SKIP for cid, v in intensity.items()
                           if v < WEAK_CLUSTER_FRAC * top}
        self.placed: dict = {}        # owner -> (lat, lon): zone num, or ("new", key)
        self.taken: set = set()
        self.movable = []
        for z in sorted(zones, key=lambda z: z.num):
            pos = self.cb_pos(z)
            if pos is None:
                if not z.fixed:
                    self.movable.append(z)
                continue
            d = dist_nm(ac.lat, ac.lon, *pos)
            if (z.fixed or d <= hard_nm(z.cb_big) or
                    (z.visible_cb and ac.now_s - z.changed_s < CHANGE_GAP_S)):
                self.show(z.num, z.storm, pos)        # locked
            else:
                self.movable.append(z)
                if z.visible_cb:      # kept unless something beats it: others keep away
                    self.placed[z.num] = pos

    def cb_pos(self, z: Zone) -> Optional[tuple]:
        """Return where zone z's CB is, or None (no CB)."""
        if z.storm in self.all_by_key:
            s = self.all_by_key[z.storm]
            return (s.lat, s.lon)
        return z.cb_at

    def show(self, owner, key: Optional[str], pos: tuple) -> None:
        """Count a CB shown: on storm key (or none) at pos."""
        self.placed[owner] = pos
        if key in self.all_by_key:
            self.taken.add(key)
            cid = self.result.cluster_of[key]
            self.in_cluster[cid] = self.in_cluster.get(cid, 0) + 1

    def others(self, owner) -> list:
        """Return the CB positions placed, except owner's."""
        return [p for o, p in self.placed.items() if o != owner]

    def value(self, key: Optional[str], pos: tuple, owner) -> float:
        """Return what a CB at pos (on storm key, or none) is worth now, before change costs."""
        if key not in self.all_by_key:
            return DEAD_SCORE
        cid = self.result.cluster_of[key]
        return (self.base[key] * DIVERSITY ** self.in_cluster.get(cid, 0) *
                spacing_factor(*pos, self.others(owner)))

    def keep_value(self, z: Zone) -> float:
        """Return what keeping zone z's CB is worth, its change cost included (0: no CB)."""
        pos = self.cb_pos(z)
        if pos is None:
            return 0.0
        if z.storm not in self.by_key and not z.visible_cb:
            return 0.0                # a storm left out (behind, ...) or none, unseen: free
        d = dist_nm(self.ac.lat, self.ac.lon, *pos)
        cost = change_cost(d, z.visible_cb, self.ac.visible_nm, z.cb_big)
        return self.value(z.storm, pos, z.num) * (cost if z.visible_cb else HIDDEN_KEEP)

    def new_value(self, s: Storm) -> float:
        """Return what a new CB on storm s is worth, its change cost included (0: not allowed)."""
        d = dist_nm(self.ac.lat, self.ac.lon, s.lat, s.lon)
        cost = change_cost(d, s.in_view, self.ac.visible_nm, s.big)
        if math.isinf(cost):
            return 0.0
        v = self.value(s.key, (s.lat, s.lon), ("new", s.key))
        if s.in_view and v < APPEAR_BASE * cost:
            return 0.0
        return v / cost

    def run(self) -> Plan:
        """Choose, best first, between keeping a zone's CB and a new CB on a storm."""
        undecided = list(self.movable)
        current = {z.storm for z in undecided if z.storm}
        skipped: set = set()
        while True:
            options = []   # (value, rank: keep 1 > new 0, order, kind, item)
            for i, s in enumerate(self.storms):
                if s.key in self.taken or s.key in current or s.key in skipped:
                    continue
                v = self.new_value(s)
                self.result.score_of.setdefault(s.key, round(v, 3))
                if v > 0.0:
                    options.append((v, 0, -i, "new", s))
            options += [(v, 1, -z.num, "keep", z) for z in undecided
                        if (v := self.keep_value(z)) > 0.0]
            if not options:
                break
            v, _, _, kind, item = max(options, key=lambda o: o[:3])
            if kind == "keep":
                undecided.remove(item)
                self.show(item.num, item.storm, self.cb_pos(item))
                continue
            zone = self.zone_for(undecided, v)
            if zone is None:
                skipped.add(item.key)
                continue
            was_value = self.keep_value(zone)
            undecided.remove(zone)
            self.placed.pop(zone.num, None)
            current.discard(zone.storm)
            self.show(zone.num, item.key, (item.lat, item.lon))
            self.result.moves.append(Move(zone.num, item.key, item.lat, item.lon, v,
                                          zone.storm, was_value))
        # left over: a CB the crew can't see goes if too close to a CB placed
        for z in undecided:
            pos = self.cb_pos(z)
            if pos is not None and not z.visible_cb and \
                    spacing_factor(*pos, self.others(z.num)) == 0.0:
                self.result.moves.append(Move(z.num, None, z.lat, z.lon, 0.0))
        return self.result

    def zone_for(self, undecided: list, value: float) -> Optional[Zone]:
        """Return the zone a new CB worth value goes to: one without a CB, else the cheapest.

        A zone showing a CB makes way only for a CB worth at least MIN_VALUE.
        """
        empty = [z for z in undecided if self.cb_pos(z) is None]
        if empty:
            return empty[0]
        if value < MIN_VALUE:
            return None
        cands = [(self.keep_value(z), z.num, z) for z in undecided]
        cands = [c for c in cands if c[0] < value]
        return min(cands, key=lambda c: c[:2])[2] if cands else None


def plan(storms: list, zones: list, ac: Aircraft) -> Plan:
    """Return which movable zones go onto which storms (see the module docstring).

    Greedy, best first: each step takes the most valuable of keeping a movable zone's CB
    (its value x its change cost, x HIDDEN_KEEP if the crew can't see it) and a new CB on a
    storm (its value / its change cost if the crew would see it appear), where value =
    strength x relevance x DIVERSITY ** (CBs before it in its cluster) x spacing. A new CB
    goes to a zone without a CB, else to the zone whose CB is worth least, if worth less.
    Locked: fixed zones, CBs within hard_nm(), and CBs the crew may see that changed within
    CHANGE_GAP_S. Deterministic: ties go by storm key and zone number.
    """
    return _Planning(storms, zones, ac).run()
