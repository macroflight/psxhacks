"""Unit tests for fw_planner (the CB placement planner): python3 -m unittest -v test_fw_planner."""
import math
import random
import unittest

import fw_planner as P

AC = P.Aircraft(lat=50.0, lon=0.0, hdg=90.0)


def at(brg_deg: float, dist: float, lat0: float = 50.0, lon0: float = 0.0) -> tuple:
    """Return (lat, lon) dist nm from (lat0, lon0) on bearing brg_deg (flat earth, fine here)."""
    dlat = dist * math.cos(math.radians(brg_deg)) / 60.0
    dlon = dist * math.sin(math.radians(brg_deg)) / (60.0 * math.cos(math.radians(lat0)))
    return lat0 + dlat, lon0 + dlon


def storm(key: str, brg_deg: float, dist: float, dbz: int = 50, area: float = 100.0,
          **kw) -> P.Storm:
    """Return a storm dist nm from the aircraft on bearing brg_deg."""
    lat, lon = at(brg_deg, dist)
    return P.Storm(key, lat, lon, dbz=dbz, area_km2=area, **kw)


def free_zones(n: int) -> list:
    """Return n movable zones without a CB, far behind the aircraft."""
    return [P.Zone(i + 1, *at(270.0, 300.0 + 30 * i)) for i in range(n)]


def strong_line(prefix: str, brg_deg: float) -> list:
    """Return a very strong cluster: five 60 dBZ storms 24 nm apart across the bearing."""
    out = []
    for i in range(5):
        lat, lon = at(brg_deg, 200.0)
        lat, lon = at(brg_deg + 90.0, (i - 2) * 24.0, lat, lon)
        out.append(P.Storm(f"{prefix}{i}", lat, lon, dbz=60, area_km2=300.0))
    return out


class TestPlanner(unittest.TestCase):
    """The planner's rules, one by one."""

    def test_each_cluster_gets_one_before_more(self):
        """A very strong cluster left, a medium one right: the medium one gets a CB too (prio 4)."""
        storms = strong_line("L", 50.0) + [storm("R0", 130.0, 150.0, dbz=50)]
        plan = P.plan(storms, free_zones(3), AC)
        got = [m.storm for m in plan.moves]
        self.assertEqual(len(got), 3)
        self.assertIn("R0", got)
        self.assertEqual(sum(1 for k in got if k.startswith("L")), 2)

    def test_strongest_first(self):
        """With one zone, the strongest storm gets it (prio 5)."""
        storms = [storm("A", 80.0, 150.0, dbz=45), storm("B", 100.0, 150.0, dbz=58)]
        plan = P.plan(storms, free_zones(1), AC)
        self.assertEqual([m.storm for m in plan.moves], ["B"])

    def test_spacing(self):
        """No two CBs closer than SPACING_MIN_NM."""
        storms = [storm("A", 90.0, 150.0, dbz=60), storm("B", 90.0, 165.0, dbz=60)]
        plan = P.plan(storms, free_zones(2), AC)
        self.assertEqual(len(plan.moves), 1)

    def test_fixed_and_visible_zones_stay(self):
        """Fixed zones and zones with a visible CB are never moved; their storms count as shown."""
        storms = [storm("A", 90.0, 150.0, dbz=60), storm("B", 60.0, 200.0, dbz=50)]
        zones = [P.Zone(1, *at(90.0, 157.0), storm="A", visible_cb=True),
                 P.Zone(2, *at(0.0, 10.0), fixed=True)] + [P.Zone(3, *at(270.0, 300.0))]
        plan = P.plan(storms, zones, AC)
        self.assertEqual([(m.zone, m.storm) for m in plan.moves], [(3, "B")])

    def test_no_new_cb_in_view(self):
        """A storm the crew may see gets no new CB, but a zone already showing it keeps it."""
        storms = [storm("A", 90.0, 100.0, dbz=60, in_view=True),
                  storm("B", 90.0, 300.0, dbz=45)]
        plan = P.plan(storms, free_zones(1), AC)
        self.assertEqual([m.storm for m in plan.moves], ["B"])
        zones = [P.Zone(1, *at(90.0, 107.0), storm="A")]
        self.assertEqual(P.plan(storms, zones, AC).moves, [])   # keeps A, no move

    def test_stability(self):
        """A zone keeps its storm while the list has it; a small change doesn't swap it."""
        storms = [storm("A", 90.0, 150.0, dbz=50), storm("B", 80.0, 160.0, dbz=52)]
        zones = [P.Zone(1, *at(90.0, 157.0), storm="A")]
        self.assertEqual(P.plan(storms, zones, AC).moves, [])   # same step: A is nearer ahead
        storms[0].area_km2 = 80.0                               # a little weaker: still A
        self.assertEqual(P.plan(storms, zones, AC).moves, [])
        storms[1].dbz = 62                                      # clearly stronger: B
        self.assertEqual([m.storm for m in P.plan(storms, zones, AC).moves], ["B"])

    def test_score_steps(self):
        """Scores within SCORE_STEP count as equal: the storm nearer ahead wins (sims agree)."""
        near = storm("N", 90.0, 150.0, dbz=50, area=100.0)
        far = storm("F", 90.0, 250.0, dbz=50, area=120.0)    # a little stronger, farther
        self.assertEqual([m.storm for m in P.plan([near, far], free_zones(1), AC).moves], ["N"])

    def test_behind_scores_little(self):
        """A storm behind loses to a weaker one ahead (relevance)."""
        storms = [storm("A", 270.0, 100.0, dbz=60), storm("B", 90.0, 200.0, dbz=45)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(1), AC).moves], ["B"])

    def test_route_counts_as_ahead(self):
        """Near the route ahead counts as ahead even if far off the heading."""
        storms = [storm("A", 200.0, 150.0, dbz=50, route_off_nm=20.0),
                  storm("B", 180.0, 150.0, dbz=50)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(1), AC).moves], ["A"])

    def test_not_behind_or_far_abeam(self):
        """Storms behind, or abeam beyond the behind limit, get no zone (it would go at once)."""
        ac = P.Aircraft(lat=50.0, lon=0.0, hdg=90.0, behind_limit_nm=50.0)
        storms = [storm("B", 200.0, 40.0, dbz=62, behind_nm=40.0),
                  storm("S", 175.0, 150.0, dbz=60), storm("A", 90.0, 250.0, dbz=45)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(3), ac).moves], ["A"])

    def test_holding_reach(self):
        """Holding: no storm beyond the maneuvering reach (every turn makes it behind)."""
        ac = P.Aircraft(lat=50.0, lon=0.0, hdg=90.0, maneuvering=True, maneuver_reach_nm=150.0)
        storms = [storm("F", 270.0, 300.0, dbz=62), storm("N", 0.0, 120.0, dbz=48)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(2), ac).moves], ["N"])

    def test_deterministic(self):
        """The same storms in any order give the same plan (prio 3)."""
        storms = strong_line("L", 50.0) + strong_line("M", 130.0) + \
            [storm(f"S{i}", 60.0 + i * 7, 120.0 + i * 13, dbz=45 + i) for i in range(8)]
        ref = [(m.zone, m.storm) for m in P.plan(storms, free_zones(5), AC).moves]
        for seed in range(5):
            shuffled = storms[:]
            random.Random(seed).shuffle(shuffled)
            self.assertEqual([(m.zone, m.storm) for m in P.plan(shuffled, free_zones(5), AC).moves],
                             ref)

    def test_clusters(self):
        """Chained storms within CLUSTER_LINK_NM are one cluster, named by the smallest key."""
        line = strong_line("L", 50.0)
        cl = P.clusters(line + [storm("R", 130.0, 150.0)])
        self.assertEqual({cl[s.key] for s in line}, {"L0"})
        self.assertEqual(cl["R"], "R")


if __name__ == "__main__":
    unittest.main()
