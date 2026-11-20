"""Unit tests for fw_planner (the CB placement planner): python3 -m unittest -v test_fw_planner."""
import math
import random
import unittest
from unittest import mock

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
        """A strong cluster and a medium one (over WEAK_CLUSTER_FRAC): the medium one gets a CB."""
        right = [P.Storm(f"R{i}", *at(130.0 + 90.0, (i - 1.5) * 20.0, *at(130.0, 150.0)),
                         dbz=55, area_km2=300.0) for i in range(4)]   # chained: one cluster
        storms = strong_line("L", 50.0) + right
        got = [m.storm for m in P.plan(storms, free_zones(3), AC).moves]
        self.assertEqual(len(got), 3)
        self.assertTrue(any(k.startswith("R") for k in got))

    def test_weak_cluster_only_left_over(self):
        """A small weak cluster (under WEAK_CLUSTER_FRAC) only gets zones left over."""
        storms = strong_line("L", 50.0) + [storm("W", 130.0, 150.0, dbz=45)]
        got = [m.storm for m in P.plan(storms, free_zones(3), AC).moves]
        self.assertEqual(len(got), 3)
        self.assertNotIn("W", got)
        got = [m.storm for m in P.plan(storms, free_zones(6), AC).moves]
        self.assertIn("W", got)       # five strong storms, a sixth zone: the weak one gets it

    def test_strongest_first(self):
        """With one zone, the strongest storm gets it (prio 5)."""
        storms = [storm("A", 80.0, 250.0, dbz=45), storm("B", 100.0, 250.0, dbz=58)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(1), AC).moves], ["B"])

    def test_spacing(self):
        """No two CBs closer than SPACING_MIN_NM."""
        storms = [storm("A", 90.0, 250.0, dbz=60), storm("B", 90.0, 265.0, dbz=60)]
        self.assertEqual(len(P.plan(storms, free_zones(2), AC).moves), 1)

    def test_fixed_zones_stay(self):
        """Fixed zones are never moved; their storms count as shown."""
        storms = [storm("A", 90.0, 250.0, dbz=60), storm("B", 60.0, 300.0, dbz=50)]
        zones = [P.Zone(1, *at(90.0, 257.0), storm="A", fixed=True),
                 P.Zone(2, *at(0.0, 10.0), fixed=True), P.Zone(3, *at(270.0, 300.0))]
        self.assertEqual([(m.zone, m.storm) for m in P.plan(storms, zones, AC).moves], [(3, "B")])

    def test_hard_radius(self):
        """Within HARD_NM no CB appears, and a CB there is kept even for a far stronger storm."""
        storms = [storm("N", 90.0, 30.0, dbz=60), storm("F", 90.0, 250.0, dbz=45)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(1), AC).moves], ["F"])
        zones = [P.Zone(1, *at(90.0, 35.0), storm="W")]
        storms = [storm("W", 90.0, 35.0, dbz=43), storm("S", 80.0, 250.0, dbz=65, area=400.0)]
        self.assertEqual(P.plan(storms, zones, AC).moves, [])
        big = [storm("B", 90.0, 50.0, dbz=60, big=True), storm("F", 90.0, 250.0, dbz=45)]
        self.assertEqual([m.storm for m in P.plan(big, free_zones(1), AC).moves], ["F"])

    def test_visible_cb_needs_a_margin(self):
        """A CB the crew may see goes only for a storm scoring clearly more (change_cost)."""
        zones = [P.Zone(1, *at(90.0, 150.0), storm="A", visible_cb=True)]
        cost = P.change_cost(150.0, True, AC.visible_nm)
        self.assertTrue(1.2 < cost < 1.5)
        a = storm("A", 90.0, 150.0, dbz=50, area=100.0)
        a_score = P.strength(a)
        small = storm("B", 60.0, 300.0, dbz=50, area=100.0 * 1.1)          # a little better
        self.assertEqual(P.plan([a, small], zones, AC).moves, [])
        clear = storm("B", 60.0, 300.0, dbz=60, area=300.0)                 # much better
        self.assertGreater(P.strength(clear), a_score * cost)
        self.assertEqual([m.storm for m in P.plan([a, clear], zones, AC).moves], ["B"])

    def test_change_gap(self):
        """A CB the crew may see that changed within CHANGE_GAP_S stays, whatever comes."""
        ac = P.Aircraft(lat=50.0, lon=0.0, hdg=90.0, now_s=1000.0)
        zones = [P.Zone(1, *at(90.0, 150.0), storm="A", visible_cb=True, changed_s=900.0)]
        storms = [storm("A", 90.0, 150.0, dbz=45), storm("B", 60.0, 300.0, dbz=65, area=400.0)]
        self.assertEqual(P.plan(storms, zones, ac).moves, [])
        zones[0].changed_s = 0.0
        self.assertEqual([m.storm for m in P.plan(storms, zones, ac).moves], ["B"])

    def test_new_cb_in_view_pays(self):
        """A new CB where the crew would see it counts less (change_cost): one unseen wins."""
        storms = [storm("V", 90.0, 120.0, dbz=50, in_view=True), storm("H", 80.0, 300.0, dbz=50)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(1), AC).moves], ["H"])
        storms[0].dbz = 65
        storms[0].area_km2 = 400.0     # much stronger: worth appearing in view
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(1), AC).moves], ["V"])

    def test_hidden_cb_no_flapping(self):
        """A CB the crew can't see changes only for a storm scoring HIDDEN_KEEP more."""
        zones = [P.Zone(1, *at(90.0, 300.0), storm="A")]
        storms = [storm("A", 90.0, 300.0, dbz=50, area=100.0),
                  storm("B", 60.0, 300.0, dbz=50, area=105.0)]
        self.assertEqual(P.plan(storms, zones, AC).moves, [])
        storms[1].dbz = 65
        storms[1].area_km2 = 300.0     # clearly stronger: worth the change
        self.assertEqual([m.storm for m in P.plan(storms, zones, AC).moves], ["B"])

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
        storms = [storm("B", 200.0, 45.0, dbz=62, behind_nm=40.0),
                  storm("S", 175.0, 150.0, dbz=60), storm("A", 90.0, 250.0, dbz=45)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(3), ac).moves], ["A"])

    def test_far_abeam_off_route(self):
        """With a route, a storm far from it and far abeam gets no zone (live 2026-11-19)."""
        ac = P.Aircraft(lat=50.0, lon=0.0, hdg=90.0, behind_limit_nm=50.0)
        storms = [storm("S", 0.0, 300.0, dbz=60, route_off_nm=250.0),
                  storm("R", 90.0, 200.0, dbz=45, route_off_nm=30.0)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(2), ac).moves], ["R"])

    def test_no_move_for_almost_nothing(self):
        """A zone's CB makes way only for a new CB worth MIN_VALUE; an empty zone takes any."""
        storms = [storm("W", 180.0, 150.0, dbz=43), storm("X", 170.0, 250.0, dbz=50)]
        zone = P.Zone(1, 0.0, 0.0, storm="W")
        with mock.patch.object(P, "MIN_VALUE", 10.0):
            self.assertEqual(P.plan(storms, [zone], AC).moves, [])
            self.assertEqual(P.plan(storms[1:], free_zones(1), AC).moves[0].storm, "X")
        self.assertEqual(P.plan(storms, [zone], AC).moves[0].storm, "X")

    def test_holding_reach(self):
        """Holding: no storm beyond the maneuvering reach (every turn makes it behind)."""
        ac = P.Aircraft(lat=50.0, lon=0.0, hdg=90.0, maneuvering=True, maneuver_reach_nm=150.0)
        storms = [storm("F", 270.0, 300.0, dbz=62), storm("N", 0.0, 120.0, dbz=48)]
        self.assertEqual([m.storm for m in P.plan(storms, free_zones(2), ac).moves], ["N"])

    def test_deterministic(self):
        """The same storms in any order give the same plan."""
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
