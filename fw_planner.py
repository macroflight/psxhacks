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
CANONICAL = True           # zones show the storms of one list ranked from the storms alone (each
#                            zone's own history made two sims flying together diverge; synthetic
#                            pairs 92 -> 93-94 %, 15 % more moves of zones the crew can't see)
SCORE_STEP = 1.3           # scores within this factor count as equal (the one nearer ahead wins)
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
    behind_nm: float = 0.0      # how far behind the aircraft it is (0: ahead or abeam), as the
    #                             caller's "behind" rule measures it (along the route if any)


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
    behind_limit_nm: float = math.inf   # the caller moves zones farther behind than this away
    maneuver_reach_nm: float = math.inf   # holding or vectored: no storm farther than this


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
    return dbz_class * (1.0 + min(storm.area_km2, 400.0) / 400.0) * ltg_factor(storm.lightning)


def pick(cands: list, ac: Aircraft) -> tuple:
    """Return the (storm, score) to take next from [(storm, score)]: (None, 0.0) if none.

    Of the storms scoring within SCORE_STEP of the best, the one nearest ahead (along the
    heading; then by key). In a big field of similar cells the exact scores differ a little
    in each sim (its own lightning counts, a storm just in view or not): taking the exact
    best, each sim chose other cells (synthetic pairs).
    """
    if not cands:
        return None, 0.0
    top = max(score for _, score in cands)
    return min(((s, score) for s, score in cands if score >= top / SCORE_STEP),
               key=lambda c: (_along(c[0], ac), c[0].key))


def ltg_factor(flashes: int) -> float:
    """Return the score factor for lightning near a storm: in steps, not per flash.

    Each sim downloads the lightning itself, a few flashes apart: counted per flash, that
    picked other storms in each sim (synthetic pairs in big fields 89 % -> 84 %).
    """
    return 1.0 if flashes < 5 else 1.15 if flashes < 20 else 1.3 if flashes < 50 else 1.5


def _along(storm: Storm, ac: Aircraft) -> float:
    """Return how far ahead along the heading a storm is (nm; negative: behind).

    The tie-break among storms of the same score step: their order only changes when the
    aircraft turns (straight-line distance changes as it passes them), and it is the same for
    sims in trail.
    """
    off = math.radians(bearing_deg(ac.lat, ac.lon, storm.lat, storm.lon) - ac.hdg)
    return dist_nm(ac.lat, ac.lon, storm.lat, storm.lon) * math.cos(off)


def _off_nose(storm: Storm, ac: Aircraft) -> float:
    """Return how far off the aircraft's nose a storm is (degrees, 0-180)."""
    return abs((bearing_deg(ac.lat, ac.lon, storm.lat, storm.lon) - ac.hdg + 180.0) % 360.0 - 180.0)


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


def canonical(storms: list, result: Plan, base: dict, ac: Aircraft, n: int) -> list:
    """Return up to n [(storm, score)] in the order every sim would show them (storms only).

    Like plan()'s choice, but without the zones: no CBs shown yet, no STABILITY. Each sim
    then shows as many of them, in this order, as it has zones for.
    """
    out: list = []
    in_cluster: dict = {}
    placed: list = []
    used: set = set()
    while len(out) < n:
        cands = []
        for s in sorted(storms, key=lambda s: s.key):
            if s.key in used:
                continue
            score = (base[s.key] * DIVERSITY ** in_cluster.get(result.cluster_of[s.key], 0) *
                     spacing_factor(s, placed))
            result.score_of.setdefault(s.key, score)
            if score <= 0.0:
                continue
            cands.append((s, score))
        best, best_score = pick(cands, ac)
        if best is None:
            break
        used.add(best.key)
        placed.append((best.lat, best.lon))
        cid = result.cluster_of[best.key]
        in_cluster[cid] = in_cluster.get(cid, 0) + 1
        out.append((best, best_score))
    return out


def plan(storms: list, zones: list, ac: Aircraft) -> Plan:  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
    """Return which movable zones go onto which storms (see the module docstring).

    Kept as they are: fixed zones and zones with a visible CB (their storms count as shown).
    CANONICAL: the movable zones show, in order, the storms of canonical() (ranked from the
    storms alone: strength x relevance x DIVERSITY ** (CBs before it in its cluster) x
    spacing, picked by pick()), skipping storms already shown, too close to a CB shown, or
    that the crew would see appear (in_view) unless a zone shows them already; so a sim with
    fewer zones free shows the start of the same list. (Not CANONICAL: filled one by one
    with the storm that scores most now, a zone's current storm x STABILITY.) A zone whose
    storm is chosen stays (no move); the others are moved, the ones without a CB first.
    Deterministic: ties go by position ahead, storm key and zone number.
    """
    result = Plan()
    all_by_key = {s.key: s for s in storms}   # every storm: existing CBs keep their spacing
    # not a storm the caller's "behind" rule would take the zone away from at once: a CB that
    # comes and goes (frankenweather, headless 2026-11-18): behind the aircraft, or (holding)
    # farther than the maneuvering reach, where every turn makes it "behind"
    # Nor abeam and farther than the limit: it is behind a minute later (frankenweather's
    # storm placer: 75 degrees of the nose since a zone went away and back every 30-60 s)
    # Behind at all: going away, the rule takes it soon (placed 43 nm behind, moved 30 s later
    # at 50 nm: synthetic flights 2026-11-18)
    storms = [s for s in storms if s.behind_nm <= 0.0 and not (
        ac.maneuvering and dist_nm(ac.lat, ac.lon, s.lat, s.lon) > ac.maneuver_reach_nm) and not (
        not ac.maneuvering and s.route_off_nm is None and _off_nose(s, ac) > AHEAD_DEG and
        dist_nm(ac.lat, ac.lon, s.lat, s.lon) > ac.behind_limit_nm)]
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
        if z.cb_at is not None and z.storm not in all_by_key:
            placed.append(z.cb_at)
        elif z.storm in all_by_key and z.storm not in by_key:   # its storm left out above
            placed.append((all_by_key[z.storm].lat, all_by_key[z.storm].lon))
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
    if CANONICAL:
        for s, score in canonical(storms, result, base, ac, 2 * len(zones)):
            if len(chosen) == len(movable):
                break
            if (s.key in taken or (s.in_view and s.key not in current) or
                    spacing_factor(s, placed) == 0.0):
                continue
            taken.add(s.key)
            placed.append((s.lat, s.lon))
            chosen.append((s, score))
    for _ in [] if CANONICAL else movable:
        cands = []
        for s in sorted(storms, key=lambda s: s.key):
            if s.key in taken or (s.in_view and s.key not in current):
                continue
            score = (base[s.key] * DIVERSITY ** in_cluster.get(result.cluster_of[s.key], 0) *
                     spacing_factor(s, placed) * (STABILITY if s.key in current else 1.0))
            result.score_of[s.key] = score
            if score <= 0.0:
                continue
            cands.append((s, score))
        best, best_score = pick(cands, ac)
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
        if s is None and z.storm in all_by_key:   # its storm left out: already in placed
            continue
        if s is not None and spacing_factor(s, placed) == 0.0:
            result.moves.append(Move(z.num, None, z.lat, z.lon, 0.0))
        elif s is not None:
            placed.append((s.lat, s.lon))
    return result
