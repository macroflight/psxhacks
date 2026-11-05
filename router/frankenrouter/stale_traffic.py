"""Stale traffic: dead-reckon briefly frozen vPilot targets, remove the ones that stay frozen.

vPilot sometimes repeats an aircraft's last position in its updates
(every 5 s) while that aircraft's pressure altitude and speed keep
changing: for 20-25 s at a time for some aircraft, for up to 10 minutes
in the worst cases seen (2026-11-05, see router/docs/Changelog.md). The
PSX.NET.vPilot plugin passes those positions on as TfcPos (Qs450). PSX
works out each target's speed (TfcVel, Qs451) from successive TfcPos, so
a frozen target stops on TCAS, often climbs or descends in place, and
then jumps ahead: a ghost.

The plugin only sends aircraft faster than 90 kt, so a target whose
position repeats exactly is stale. This filter, on a router's ingress
from its vPilot plugin client, moves a repeated position along the
target's last real track and ground speed instead, so PSX keeps it moving
smoothly. A target frozen for more than dead_reckon_s is removed until it
moves again or disappears. Every change is logged: once when we start
dead-reckoning a target, once when we stop (it moves again, is removed or
is gone), not for each update.

A temporary workaround, opt-in ([filtering]
stale_traffic_dead_reckon_seconds): the real fix belongs in vPilot.

TfcPos holds 7 slots "lat rad;lon rad;true altitude x10 ft;true track x100",
nearest first; an empty slot is 0;0;0;0. Slots carry no callsigns: a
target is followed from message to message by its exact position (while
frozen) or by where it should be by now.
"""
import math
import unittest

SLOTS = 7
EMPTY = "0;0;0;0"
EARTH_NM_PER_RAD = 3440.065
MAX_KT = 700.0           # faster than this is a bad match, not an aircraft
MATCH_NM = 3.0           # how far a moved target may be from where we expected it
MATCH_ALT_FT = 3000.0


def pos_text(lat_rad, lon_rad):
    """Return 'N41 20.2 W005 06.0' for a position in radians."""
    def part(value, pos, neg, width):
        deg, minutes = divmod(round(abs(math.degrees(value)) * 600), 600)
        return f"{pos if value >= 0 else neg}{deg:0{width}d} {minutes / 10:04.1f}"
    return f"{part(lat_rad, 'N', 'S', 2)} {part(lon_rad, 'E', 'W', 3)}"


def distance_nm(lat1, lon1, lat2, lon2):
    """Return the great-circle distance in nm between two positions in radians."""
    x = (math.sin(lat1) * math.sin(lat2) +
         math.cos(lat1) * math.cos(lat2) * math.cos(lon2 - lon1))
    return EARTH_NM_PER_RAD * math.acos(max(-1.0, min(1.0, x)))


def course_rad(lat1, lon1, lat2, lon2):
    """Return the initial great-circle course from position 1 to 2 (radians)."""
    y = math.sin(lon2 - lon1) * math.cos(lat2)
    x = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(lon2 - lon1)
    return math.atan2(y, x)


def project(lat, lon, course, dist_nm):
    """Return (lat, lon) dist_nm from (lat, lon) on course (all radians)."""
    d = dist_nm / EARTH_NM_PER_RAD
    lat2 = math.asin(math.sin(lat) * math.cos(d) + math.cos(lat) * math.sin(d) * math.cos(course))
    lon2 = lon + math.atan2(math.sin(course) * math.sin(d) * math.cos(lat),
                            math.cos(d) - math.sin(lat) * math.sin(lat2))
    return lat2, (lon2 + math.pi) % (2 * math.pi) - math.pi


class _Target:  # pylint: disable=too-few-public-methods,too-many-instance-attributes
    """One traffic target followed from message to message."""

    def __init__(self, raw, lat, lon, alt_ft, now):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Start following a target vPilot sent at (lat, lon) at time now."""
        self.raw = raw              # (lat text, lon text) as last sent by vPilot
        self.lat, self.lon = lat, lon
        self.alt_ft = alt_ft
        self.real_t = now           # when vPilot last sent a new position
        self.speed_kt = None        # last real ground speed and course, when known
        self.course = None
        self.logged = False         # a WARNING said we are dead-reckoning or removing it
        self.removed = False

    def frozen_s(self, now):
        """Return how long vPilot has repeated the same position."""
        return now - self.real_t

    def expected(self, now):
        """Return where the target should be by now (radians)."""
        if self.speed_kt is None:
            return self.lat, self.lon
        return project(self.lat, self.lon, self.course, self.speed_kt * self.frozen_s(now) / 3600.0)


class StaleTrafficFilter:  # pylint: disable=too-few-public-methods
    """Dead-reckon or remove frozen targets in one client's TfcPos (Qs450) values."""

    def __init__(self, dead_reckon_s=60.0):
        """Dead-reckon a frozen target for up to dead_reckon_s, then remove it."""
        self.dead_reckon_s = dead_reckon_s
        self.targets = []

    def process(self, value, now):  # pylint: disable=too-many-locals
        """Return (new value, [log lines]) for a TfcPos value received at now (seconds).

        The value is returned unchanged (the same string) when no target
        is frozen.
        """
        slots = self._parse(value)
        if slots is None:
            return value, []
        logs = []
        old = list(self.targets)
        current = []
        out_slots = []
        changed = False
        for raw, lat, lon, alt_ft, rest in slots:
            target = self._match(old, raw, lat, lon, alt_ft, now)
            if target is None:
                target = _Target(raw, lat, lon, alt_ft, now)
                current.append(target)
                out_slots.append(f"{raw[0]};{raw[1]};{rest}")
                continue
            old.remove(target)
            current.append(target)
            if target.raw != raw:
                logs += self._moved(target, raw, lat, lon, now)
                target.alt_ft = alt_ft
                out_slots.append(f"{raw[0]};{raw[1]};{rest}")
                continue
            target.alt_ft = alt_ft
            slot, slot_logs = self._frozen(target, rest, now)
            logs += slot_logs
            if slot != f"{raw[0]};{raw[1]};{rest}":
                changed = True
            if slot is not None:
                out_slots.append(slot)
        for target in old:
            if target.logged:
                logs.append(f"Stale traffic target {pos_text(target.lat, target.lon)} "
                            f"{target.alt_ft:,.0f} ft: gone from the traffic data after "
                            f"{target.frozen_s(now):.0f} s frozen")
        self.targets = current
        if not changed:
            return value, logs
        out_slots += [EMPTY] * (max(SLOTS, len(slots)) - len(out_slots))
        return ";".join(out_slots) + ";", logs

    @staticmethod
    def _parse(value):
        """Return [((lat text, lon text), lat, lon, alt ft, 'alt;track'), ...] or None."""
        fields = value.strip().rstrip(";").split(";")
        if len(fields) % 4:
            return None
        slots = []
        for i in range(0, len(fields), 4):
            lat_t, lon_t, alt_t, trk_t = fields[i:i + 4]
            try:
                lat, lon, alt = float(lat_t), float(lon_t), float(alt_t) / 10.0
            except ValueError:
                return None
            if lat == 0.0 and lon == 0.0:
                continue
            slots.append(((lat_t, lon_t), lat, lon, alt, f"{alt_t};{trk_t}"))
        return slots

    @staticmethod
    def _match(old, raw, lat, lon, alt_ft, now):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Return the followed target this slot belongs to, or None (a new one)."""
        for target in old:
            if target.raw == raw:
                return target
        best, best_d = None, None
        for target in old:
            if abs(target.alt_ft - alt_ft) > MATCH_ALT_FT:
                continue
            exp = target.expected(now)
            d = min(distance_nm(lat, lon, *exp), distance_nm(lat, lon, target.lat, target.lon))
            # after a long freeze the aircraft can be far from where it froze
            reach = max(MATCH_NM, (target.speed_kt or 500.0) * target.frozen_s(now) / 3600.0 * 1.2)
            if d <= reach and (best_d is None or d < best_d):
                best, best_d = target, d
        return best

    def _moved(self, target, raw, lat, lon, now):
        """Update a target vPilot moved; return log lines."""
        logs = []
        dt = now - target.real_t
        if target.logged:
            # how far off our guess was: where dead-reckoning had it by now
            # (a removed target: where it froze)
            where = (target.lat, target.lon) if target.removed else target.expected(now)
            off_nm = distance_nm(lat, lon, *where)
            logs.append(f"Stale traffic target {pos_text(lat, lon)} {target.alt_ft:,.0f} ft: "
                        f"moving again after {dt:.0f} s frozen ({off_nm:.1f} nm from where "
                        f"{'it was removed' if target.removed else 'we dead-reckoned it'})")
        if dt > 0.5:
            dist = distance_nm(target.lat, target.lon, lat, lon)
            speed = dist / dt * 3600.0
            if speed <= MAX_KT:
                target.speed_kt = speed
                target.course = course_rad(target.lat, target.lon, lat, lon)
            else:
                target.speed_kt = target.course = None
        target.raw, target.lat, target.lon, target.real_t = raw, lat, lon, now
        target.logged = target.removed = False
        return logs

    def _frozen(self, target, rest, now):
        """Return (slot text or None to remove it, log lines) for a repeated position."""
        frozen = target.frozen_s(now)
        where = f"{pos_text(target.lat, target.lon)} {target.alt_ft:,.0f} ft"
        if frozen > self.dead_reckon_s:
            logs = []
            if not target.removed:
                logs.append(f"Stale traffic target {where}: position unchanged for {frozen:.0f} s, "
                            f"removing it until it moves again")
                target.removed = target.logged = True
            return None, logs
        if target.speed_kt is None:
            return f"{target.raw[0]};{target.raw[1]};{rest}", []
        lat, lon = target.expected(now)
        logs = []
        if not target.logged:
            logs.append(f"Stale traffic target {where}: position unchanged for {frozen:.0f} s, "
                        f"dead-reckoning it at {target.speed_kt:.0f} kt "
                        f"track {math.degrees(target.course) % 360:03.0f}")
            target.logged = True
        return f"{lat:.5f};{lon:.5f};{rest}", logs


def _tfcpos(*targets):
    """Return a TfcPos value for (lat deg, lon deg, alt ft, track deg) tuples, padded to 7 slots."""
    slots = [f"{math.radians(a):.5f};{math.radians(b):.5f};{c * 10:.0f};{d * 100:.0f}"
             for a, b, c, d in targets]
    return ";".join(slots + [EMPTY] * (SLOTS - len(slots))) + ";"


def _slots(value):
    """Return [(lat deg, lon deg)] of the non-empty slots of a TfcPos value."""
    f = value.strip(";").split(";")
    return [(math.degrees(float(f[i])), math.degrees(float(f[i + 1])))
            for i in range(0, len(f), 4) if f[i] != "0"]


class TestStaleTraffic(unittest.TestCase):
    """Frozen targets are dead-reckoned, then removed; moving ones pass untouched."""

    # vPilot's real updates for CHC26 on 2026-11-05 (lat, lon, pressure altitude):
    # 08:11:34 moving, 08:11:39-08:11:59 the same position 5 times, 08:12:04 moving again
    CHC26 = [(0, 41.33331, -5.08953, 29563), (5, 41.33733, -5.10066, 29691),
             (10, 41.33733, -5.10066, 29820), (15, 41.33733, -5.10066, 29941),
             (20, 41.33733, -5.10066, 30055), (25, 41.33733, -5.10066, 30171),
             (30, 41.35352, -5.14549, 30278)]
    OTHER = (41.5, -5.5, 35000, 293.0)

    def test_moving_traffic_untouched(self):
        """Values with only moving targets come back as the very same string."""
        f = StaleTrafficFilter()
        for k in range(5):
            v = _tfcpos((41.3 + k * 0.01, -5.1 - k * 0.03, 30000, 293.0), self.OTHER)
            # OTHER repeats: but it has no speed yet, so it's passed through as it is
            out, logs = f.process(v, 100.0 + 5 * k)
            if k < 2:
                self.assertIs(out, v)
            self.assertEqual(logs, [])

    def test_chc26_dead_reckoned(self):
        """CHC26's 20 s freeze keeps moving along its track and meets the real position again."""
        f = StaleTrafficFilter()
        outs, logs = [], []
        for t, lat, lon, alt in self.CHC26:
            out, lg = f.process(_tfcpos((lat, lon, alt, 293.2)), 1000.0 + t)
            outs.append(_slots(out)[0])
            logs += lg
        # every frozen update moved on, at about the same pace as before
        for a, b in zip(outs[1:6], outs[2:6]):
            step = distance_nm(*map(math.radians, a), *map(math.radians, b))
            self.assertGreater(step, 0.3)
            self.assertLess(step, 0.9)
        # where we had it at 08:11:59 is close to where the real position came back 5 s later
        last_dr = tuple(map(math.radians, outs[5]))
        real = tuple(map(math.radians, outs[6]))
        self.assertLess(distance_nm(*last_dr, *real), 1.0)
        # dead-reckoning (from the first repeat), then moving again
        self.assertEqual(len(logs), 2, logs)
        self.assertIn("dead-reckoning", logs[0])
        self.assertIn("moving again after 25 s", logs[1])

    def test_short_skip_logged_once(self):
        """A single repeated update (BAN32N's 5 s skips) is smoothed: a start and a stop line."""
        f = StaleTrafficFilter()
        logs = []
        track = ((0, 44.0, -9.0), (5, 44.01, -9.01), (10, 44.01, -9.01), (15, 44.02, -9.02))
        for t, lat, lon in track:
            logs += f.process(_tfcpos((lat, lon, 35000, 315.0)), t)[1]
        self.assertEqual(len(logs), 2, logs)
        self.assertIn("dead-reckoning", logs[0])
        self.assertIn("moving again after 10 s", logs[1])

    def test_long_freeze_removed_then_back(self):
        """Frozen over 60 s: removed (other targets move up), then logged when it moves again."""
        f = StaleTrafficFilter(dead_reckon_s=60)
        stuck = [(34.0417, -4.6250 + (0.0126 if t == 0 else 0.0), 40128 - t * 20, 252.0)
                 for t in range(0, 101, 5)]
        logs, outs = [], []
        for k, s in enumerate(stuck):
            other = (34.5 + k * 0.01, -4.0, 30000, 90.0)
            out, lg = f.process(_tfcpos(s, other), 2000.0 + 5 * k)
            outs.append(out)
            logs += lg
        self.assertEqual(len(_slots(outs[-1])), 1)            # only the other target is left
        self.assertTrue(outs[-1].endswith(";0;0;0;0;"))
        self.assertTrue(any("removing it" in x for x in logs), logs)
        back = _tfcpos((34.10, -4.80, 37000, 252.0), (34.5 + 21 * 0.01, -4.0, 30000, 90.0))
        out, lg = f.process(back, 2105.0)
        self.assertEqual(len(_slots(out)), 2)
        self.assertTrue(any("moving again" in x and "removed" in x for x in lg), lg)

    def test_gone(self):
        """A logged frozen target that disappears is logged as gone."""
        f = StaleTrafficFilter()
        for t in range(0, 30, 5):
            lat = 41.30 if t else 41.29
            f.process(_tfcpos((lat, -5.10, 30000, 293.0)), t)
        _, logs = f.process(_tfcpos(), 30.0)
        self.assertEqual(len(logs), 1)
        self.assertIn("gone from the traffic data", logs[0])

    def test_bad_value_untouched(self):
        """Anything that isn't a TfcPos value of 4-field slots passes through unchanged."""
        f = StaleTrafficFilter()
        self.assertEqual(f.process("1;2;3", 0.0), ("1;2;3", []))
        self.assertEqual(f.process("a;b;c;d", 0.0), ("a;b;c;d", []))
