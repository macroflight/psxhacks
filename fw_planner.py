"""CB placement planner for frankenweather (design agreed with the user 2026-11-18; not used yet).

One scored decision per radar frame about which storms the movable weather zones show, in
place of the separate storm placer, SIGMET placer and route storm view. Pure functions only:
no clock, no downloads, no PSX. frankenweather (later) and the synthetic test runner give it
the storms, the zones and the aircraft, and apply what it returns.

Priorities, in order (user, 2026-11-18):
1. Don't startle the pilot: no new CB where the crew may see it, no visible CB moved.
2. Only place CBs where there are CBs in real life (the storms given).
3. Sims in the same area see similar CBs (deterministic: the same inputs, the same plan).
4. At least one CB in each distinct area of CB activity (a cluster's first CB scores full,
   each further one less: DIVERSITY per CB already in it).
5. More CBs where there is more activity (stronger storms score more).

Never moved: fixed zones (the anchor, airports, the focused zone) and zones whose CB the crew
may see; their storms count as shown. A zone that already shows a storm keeps it unless
another scores clearly better (STABILITY). CBs closer than SPACING_MIN_NM are not placed;
closer than SPACING_SOFT_NM score less (PSX shrinks a CB's echo near another zone and only
the aircraft's zone's CB turbulence counts, measured 2026-11-15/16).
"""
import math
from dataclasses import dataclass, field
from typing import Optional

EARTH_NM = 3440.065
CLUSTER_LINK_NM = 25.0     # storms this close (chained) are one cluster
DIVERSITY = 0.25           # a cluster's n-th further CB scores DIVERSITY ** n (a medium cluster
#                            beats a very strong one's second CB: priority 4 before 5)
SPACING_MIN_NM = 25.0      # no CB this close to another
SPACING_SOFT_NM = 45.0     # ... and less score up to this (PSX draws a CB full size from ~45 nm)
STABILITY = 1.3            # a zone's current storm scores this much more (no swaps for a little)
AHEAD_DEG = 75.0           # relevance: ahead within this (frankenweather's storm view)
ABEAM_DEG = 110.0          # ... abeam up to this
ROUTE_NM = 60.0            # with a route: storms within this of the route ahead count as ahead
RELEVANCE = {"ahead": 1.0, "abeam": 0.5, "behind": 0.05}


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


@dataclass
class Zone:
    """A weather zone: where it is, which storm it shows, whether it may be moved."""

    num: int
    lat: float
    lon: float
    storm: Optional[str] = None   # the key of the storm its CB is on, if any
    fixed: bool = False           # anchor, airport or focused zone: never moved for a CB
    visible_cb: bool = False      # its CB may be seen by the crew: never moved
    cb_at: Optional[tuple] = None   # (lat, lon) of a CB it shows without a storm (a dead storm's
    #                                 CB kept in view, a model CB): other CBs keep their distance


@dataclass
class Aircraft:
    """Where the aircraft is and how it flies."""

    lat: float
    lon: float
    hdg: float
    maneuvering: bool = False     # holding or vectored: all round


@dataclass
class Move:
    """A planned change: zone num goes onto storm key at (lat, lon); storm None: its CB goes."""

    zone: int
    storm: Optional[str]
    lat: float
    lon: float
    score: float


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
    return dbz_class * (1.0 + min(storm.area_km2, 400.0) / 400.0) * \
        (1.0 + min(storm.lightning, 50) / 100.0)


def relevance(storm: Storm, ac: Aircraft) -> float:
    """Return how much the crew will meet a storm: ahead (or near the route), abeam, behind."""
    if ac.maneuvering:
        return RELEVANCE["ahead"]
    if storm.route_off_nm is not None and storm.route_off_nm <= ROUTE_NM:
        return RELEVANCE["ahead"]
    off = abs((bearing_deg(ac.lat, ac.lon, storm.lat, storm.lon) - ac.hdg + 180.0) % 360.0 - 180.0)
    if off <= AHEAD_DEG:
        return RELEVANCE["ahead"]
    return RELEVANCE["abeam"] if off <= ABEAM_DEG else RELEVANCE["behind"]


def spacing_factor(storm: Storm, placed: list) -> float:
    """Return the score factor for a CB this near the CBs placed (0: too close)."""
    near = min((dist_nm(storm.lat, storm.lon, la, lo) for la, lo in placed), default=math.inf)
    if near < SPACING_MIN_NM:
        return 0.0
    if near >= SPACING_SOFT_NM:
        return 1.0
    return (near - SPACING_MIN_NM) / (SPACING_SOFT_NM - SPACING_MIN_NM)


def plan(storms: list, zones: list, ac: Aircraft) -> Plan:  # pylint: disable=too-many-locals,too-many-branches
    """Return which movable zones go onto which storms (see the module docstring).

    Kept as they are: fixed zones and zones with a visible CB (their storms count as shown).
    The other zones are filled one by one with the storm that scores most now: strength x
    relevance x DIVERSITY ** (CBs already in its cluster) x spacing, a zone's current storm
    x STABILITY, never a storm the crew would see appear (in_view) unless the zone shows it
    already. A zone whose current storm is chosen stays (no move); the others are moved,
    the ones without a CB first. Deterministic: ties go by storm key and zone number.
    """
    result = Plan()
    if not storms:
        return result
    by_key = {s.key: s for s in storms}
    result.cluster_of = clusters(storms)
    base = {s.key: strength(s) * relevance(s, ac) for s in storms}
    in_cluster: dict = {}
    placed: list = []
    taken: set = set()
    movable = []
    for z in sorted(zones, key=lambda z: z.num):
        if z.cb_at is not None and z.storm not in by_key:
            placed.append(z.cb_at)
        if (z.fixed or z.visible_cb) and z.storm in by_key:
            s = by_key[z.storm]
            taken.add(s.key)
            placed.append((s.lat, s.lon))
            cid = result.cluster_of[s.key]
            in_cluster[cid] = in_cluster.get(cid, 0) + 1
        elif not (z.fixed or z.visible_cb):
            movable.append(z)
    current = {z.storm: z for z in movable if z.storm in by_key}
    chosen = []
    for _ in movable:
        best, best_score = None, 0.0
        for s in sorted(storms, key=lambda s: s.key):
            if s.key in taken or (s.in_view and s.key not in current):
                continue
            score = (base[s.key] * DIVERSITY ** in_cluster.get(result.cluster_of[s.key], 0) *
                     spacing_factor(s, placed) * (STABILITY if s.key in current else 1.0))
            result.score_of[s.key] = score
            if score > best_score:
                best, best_score = s, score
        if best is None:
            break
        taken.add(best.key)
        placed.append((best.lat, best.lon))
        cid = result.cluster_of[best.key]
        in_cluster[cid] = in_cluster.get(cid, 0) + 1
        chosen.append((best, best_score))
    keep = {s.key for s, _ in chosen if s.key in current}
    free = [z for z in movable if z.storm not in keep]
    free.sort(key=lambda z: (z.storm is not None, z.num))   # zones without a CB first
    new = [c for c in chosen if c[0].key not in keep]
    for (s, score), z in zip(new, free):
        result.moves.append(Move(z.num, s.key, s.lat, s.lon, score))
    # a movable zone left with a CB it showed before (not chosen, not reassigned) keeps it if
    # it is far enough from every CB placed, else it goes (the crew can't see it: movable)
    for z in free[len(new):]:
        s = by_key.get(z.storm) if z.storm else None
        if s is not None and spacing_factor(s, placed) == 0.0:
            result.moves.append(Move(z.num, None, z.lat, z.lon, 0.0))
        elif s is not None:
            placed.append((s.lat, s.lon))
    return result
