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
stale_traffic_dead_reckon_seconds); the cause of the frozen positions is not known yet.

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
MATCH_NM = 3.0           # how far a moved target may be from where we expected it
MATCH_ALT_FT = 3000.0
# A move within this time is a normal update; longer: a catch-up after a freeze
NORMAL_DT_S = 12.0


def typical_kt(alt_ft):
    """Return a typical ground speed for an aircraft at alt_ft (for a target never seen moving)."""
    if alt_ft < 10000:
        return 250.0
    return 300.0 if alt_ft < 25000 else 450.0


def max_kt(alt_ft):
    """Return the fastest plausible ground speed at alt_ft (faster: a bad match or a catch-up)."""
    if alt_ft < 10000:
        return 350.0
    return 480.0 if alt_ft < 25000 else 650.0


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

    def __init__(self, raw, lat, lon, alt_ft, now, track):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Start following a target vPilot sent at (lat, lon) at now (track: radians or None)."""
        self.raw = raw              # (lat text, lon text) as last sent by vPilot
        self.lat, self.lon = lat, lon
        self.alt_ft = alt_ft
        self.track = track          # the track vPilot sends with it (radians), if any
        self.real_t = now           # when vPilot last sent a new position
        self.speed_kt = None        # measured ground speed and course, once seen moving normally
        self.course = None
        self.moves = []             # the last 3 normal moves: (seconds, nm)
        self.logged = False         # a WARNING said we are dead-reckoning or removing it
        self.removed = False

    def frozen_s(self, now):
        """Return how long vPilot has repeated the same position."""
        return now - self.real_t

    def motion(self):
        """Return (speed kt, course radians, measured?) to dead-reckon with, or None.

        The measured speed and course once the target has moved normally;
        before that (it appeared frozen) a typical speed for its altitude
        along the track vPilot sends.
        """
        if self.speed_kt is not None and self.course is not None:
            return self.speed_kt, self.course, True
        course = self.course if self.course is not None else self.track
        if course is None:
            return None
        return typical_kt(self.alt_ft), course, False

    def expected(self, now):
        """Return where the target should be by now (radians)."""
        m = self.motion()
        if m is None:
            return self.lat, self.lon
        return project(self.lat, self.lon, m[1], m[0] * self.frozen_s(now) / 3600.0)


class StaleTrafficFilter:  # pylint: disable=too-few-public-methods
    """Dead-reckon or remove frozen targets in one client's TfcPos (Qs450) values."""

    def __init__(self, dead_reckon_s=60.0):
        """Dead-reckon a frozen target for up to dead_reckon_s, then remove it."""
        self.dead_reckon_s = dead_reckon_s
        self.targets = []
        # what happened to each slot of the last value: dicts with lat, lon
        # (radians, as vPilot sent it), alt_ft, state (live / repeated /
        # dead-reckoned / removed), frozen_s, out_lat/out_lon (as passed on)
        self.last_slots = []

    def process(self, value, now):  # pylint: disable=too-many-locals,too-many-branches
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
        self.last_slots = []
        for raw, lat, lon, alt_ft, rest in slots:
            info = {'lat': lat, 'lon': lon, 'alt_ft': alt_ft, 'state': 'live', 'frozen_s': 0.0,
                    'out_lat': lat, 'out_lon': lon}
            self.last_slots.append(info)
            target = self._match(old, raw, lat, lon, alt_ft, now)
            track = self._track(rest)
            if target is None:
                target = _Target(raw, lat, lon, alt_ft, now, track)
                current.append(target)
                out_slots.append(f"{raw[0]};{raw[1]};{rest}")
                continue
            old.remove(target)
            current.append(target)
            if track is not None:
                target.track = track
            if target.raw != raw:
                logs += self._moved(target, raw, lat, lon, now)
                target.alt_ft = alt_ft
                out_slots.append(f"{raw[0]};{raw[1]};{rest}")
                continue
            target.alt_ft = alt_ft
            slot, slot_logs = self._frozen(target, rest, now)
            logs += slot_logs
            info['frozen_s'] = target.frozen_s(now)
            if slot is None:
                info['state'] = 'removed'
                info['out_lat'] = info['out_lon'] = None
            elif slot != f"{raw[0]};{raw[1]};{rest}":
                info['state'] = 'dead-reckoned'
                info['out_lat'], info['out_lon'] = (float(x) for x in slot.split(';')[:2])
            else:
                info['state'] = 'repeated'      # no speed known yet: passed on as it is
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

    def counts(self):
        """Return (targets being dead-reckoned, targets removed) right now."""
        return (sum(1 for t in self.targets if t.logged and not t.removed),
                sum(1 for t in self.targets if t.removed))

    @staticmethod
    def _track(rest):
        """Return the track (radians) in a slot's 'alt;track x100' text, or None."""
        try:
            return math.radians(int(rest.split(';')[1]) / 100.0)
        except (ValueError, IndexError):
            return None

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
            m = target.motion()
            speed = m[0] if m else 500.0
            reach = max(MATCH_NM, speed * target.frozen_s(now) / 3600.0 * 1.5)
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
            cap = max_kt(target.alt_ft)
            if dist > 0.05 and speed <= cap * 1.3:
                # a plausible move: its direction is the course from now on
                target.course = course_rad(target.lat, target.lon, lat, lon)
            if dt <= NORMAL_DT_S and speed <= cap * 1.3:
                # a normal update (not the catch-up jump after a freeze, not a mismatch):
                # the speed is the average over the last 3 of these, capped
                target.moves = (target.moves + [(dt, dist)])[-3:]
                total_s = sum(m[0] for m in target.moves)
                target.speed_kt = min(cap, sum(m[1] for m in target.moves) / total_s * 3600.0)
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
        motion = target.motion()
        if motion is None:
            return f"{target.raw[0]};{target.raw[1]};{rest}", []
        lat, lon = target.expected(now)
        logs = []
        if not target.logged:
            how = "" if motion[2] else " (typical for its altitude: not seen moving yet)"
            logs.append(f"Stale traffic target {where}: position unchanged for {frozen:.0f} s, "
                        f"dead-reckoning it at {motion[0]:.0f} kt{how} "
                        f"track {math.degrees(motion[1]) % 360:03.0f}")
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
            v = _tfcpos((41.3 + k * 0.01, -5.1 - k * 0.03, 30000, 293.0),
                        (41.5 + k * 0.005, -5.5, 35000, 0.0))
            out, logs = f.process(v, 100.0 + 5 * k)
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

    def test_frozen_from_first_sight(self):
        """A target that arrives frozen moves along its track at a typical speed (BAW47C)."""
        f = StaleTrafficFilter()
        logs, outs = [], []
        for k in range(4):              # 7 times the same position at FL210, track 073
            out, lg = f.process(_tfcpos((51.185, -0.582, 21000 - 150 * k, 73.0)), 5.0 * k)
            outs.append(_slots(out)[0])
            logs += lg
        step = distance_nm(*map(math.radians, outs[1]), *map(math.radians, outs[3]))
        self.assertAlmostEqual(step, 300.0 * 10 / 3600, delta=0.05)    # 300 kt below FL250
        self.assertIn("typical for its altitude", logs[0])
        a, b = map(math.radians, outs[1]), map(math.radians, outs[3])
        brg = math.degrees(course_rad(*a, *b)) % 360
        self.assertAlmostEqual(brg, 73.0, delta=1.0)

    def test_speed_sanity(self):
        """A catch-up jump after a freeze, or an implausible speed, doesn't become the speed."""
        f = StaleTrafficFilter()
        step = 300.0 * 5 / 3600 / 60 / math.cos(math.radians(51.2))    # 300 kt east, in degrees
        track = [(0, -0.30), (5, -0.30 + step), (10, -0.30 + 2 * step),     # moving at 300 kt
                 (15, -0.30 + 2 * step), (20, -0.30 + 2 * step),            # frozen
                 (25, -0.30 + 8 * step),          # catch-up: 6 steps in 15 s
                 (30, -0.30 + 9 * step),                                    # normal again
                 (35, -0.30 + 9 * step + 0.2)]    # a mismatch: 7 nm in 5 s
        for t, lon in track[:-1]:
            f.process(_tfcpos((51.2, lon, 17000, 90.0)), float(t))
        # the catch-up isn't a speed
        self.assertAlmostEqual(f.targets[0].speed_kt, 300.0, delta=5.0)
        f.process(_tfcpos((51.2, track[-1][1], 17000, 90.0)), 35.0)
        self.assertIsNone(f.targets[0].speed_kt)   # 7 nm away: another aircraft

    def test_counts(self):
        """counts() tells how many targets are dead-reckoned and removed now."""
        f = StaleTrafficFilter(dead_reckon_s=12)
        f.process(_tfcpos((41.29, -5.10, 30000, 293.0)), 0.0)
        f.process(_tfcpos((41.30, -5.10, 30000, 293.0)), 5.0)
        f.process(_tfcpos((41.30, -5.10, 30000, 293.0)), 10.0)
        self.assertEqual(f.counts(), (1, 0))
        self.assertEqual(f.last_slots[0]['state'], 'dead-reckoned')
        self.assertNotEqual(f.last_slots[0]['out_lat'], f.last_slots[0]['lat'])
        f.process(_tfcpos((41.30, -5.10, 30000, 293.0)), 20.0)
        self.assertEqual(f.counts(), (0, 1))
        self.assertEqual(f.last_slots[0]['state'], 'removed')

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
