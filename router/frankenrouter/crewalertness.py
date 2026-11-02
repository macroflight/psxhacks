"""Crew alertness monitor: PILOT RESPONSE on the EICAS when nobody has touched anything.

The master router sees every human input in the shared cockpit: PSX's Qh
variables ("h" for human: switches, knobs, levers, keys). When none of them
has come from a client for a while, the monitor raises an alert that
escalates advisory -> caution -> warning, like CMC-PSX 1.13's (default 15,
20 and 25 minutes in cruise, 5, 6 and 7 in descent; frankenrouter.py writes
the free EICAS messages, and optionally a PSX Sounds addon message); the
next input clears it. Some Qh variables
aren't crew input in this sense -- hardware axes that send noise all the
time -- and are ignored (IGNORE_BUILTIN plus the config's list).

It only runs at or above min_altitude (FL200). No alert is raised or
escalated in a climb (the real system's rule; the timer keeps running, so
it can come at level-off); in a descent the shorter descent times apply.
Climb and descent are told from the altitude over the last minute.

Replaces CMC-PSX's crew alertness monitor, which shows its alert only on the
PC it runs on (WorldFlight 2026: two CMC-PSX on one network also loop MCP
values). Pure bookkeeping; frankenrouter.py does the PSX side.
"""
import re
import unittest

# Not crew input (WorldFlight 2026-10-31..11-02 traffic, airborne): Tiller is
# hardware axis noise (~34,000 writes from 7 sims, every 0.1 s); ParkBrkLev
# flipped 0/1 every ~40 s for hours from two sims' hardware.
IGNORE_BUILTIN = ('Tiller', 'ParkBrkLev')

_QCODE = re.compile(r'^Q[hsi]\d+$')

# Vertical speed (ft/min, averaged over VS_WINDOW_S) within which we're level
LEVEL_FPM = 500
VS_WINDOW_S = 60.0
# Faster than this between two samples is a jump (situ load, reposition), not
# flying: the vertical speed average starts again
JUMP_FPM = 15000

# PSX's free EICAS messages by level (FreeMsgW, FreeMsgC, FreeMsgA, FreeMsgM, FreeMsgS)
FREE_MSG_KEYWORDS = {'W': 'Qs418', 'C': 'Qs419', 'A': 'Qs420', 'M': 'Qs421', 'S': 'Qs422'}


def resolve_ignore(entries, keyword_for_name):
    """Return (set of Q-codes, list of unknown entries) for variable names or Q-codes."""
    codes, unknown = set(), []
    for entry in entries:
        code = entry if _QCODE.match(entry) else keyword_for_name(entry)
        if code:
            codes.add(code)
        else:
            unknown.append(entry)
    return codes, unknown


LEVELS = ('A', 'C', 'W')     # advisory, caution, warning


class CrewAlertness:  # pylint: disable=too-many-instance-attributes
    """When the crew last did something, and which alert level is up."""

    def __init__(self, enabled, cruise, descent, ignore, min_altitude=20000):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Start enabled or not.

        cruise and descent are the seconds without crew input before the
        advisory, caution and warning; ignore is a set of Q-codes.
        """
        self.enabled = enabled
        self.times = {'cruise': tuple(cruise), 'descent': tuple(descent)}
        self.ignore = set(ignore)
        self.min_altitude = min_altitude
        self.last_activity = None   # monotonic time; None = not running (ground, low)
        self.level = None           # 'A', 'C', 'W' or None
        self.phase = None           # 'climb', 'cruise', 'descent' or None (not running)
        self.altitudes = []         # (time, ft) over the last VS_WINDOW_S

    def vertical_speed(self):
        """Average vertical speed in ft/min over the last VS_WINDOW_S, or None."""
        if len(self.altitudes) < 2 or self.altitudes[-1][0] - self.altitudes[0][0] < 10:
            return None
        (t0, a0), (t1, a1) = self.altitudes[0], self.altitudes[-1]
        return (a1 - a0) / (t1 - t0) * 60

    def activity(self, key, now):
        """Note a Qh write from a client; return True if it counts as crew activity."""
        if not key.startswith('Qh') or key in self.ignore:
            return False
        self.last_activity = now
        return True

    def cleared_by_activity(self):
        """After counted activity: return the level that was up (now cleared), or None."""
        level, self.level = self.level, None
        return level

    def tick(self, now, airborne, altitude=None):
        """Return the new level ('A', 'C', 'W'), 'clear' when it comes down, else None.

        airborne is False on the ground (and paused); altitude in feet.
        """
        if altitude is not None:
            if self.altitudes:
                t_prev, a_prev = self.altitudes[-1]
                if abs(altitude - a_prev) > JUMP_FPM / 60 * max(now - t_prev, 1.0):
                    self.altitudes = []
            self.altitudes.append((now, altitude))
            while self.altitudes and now - self.altitudes[0][0] > VS_WINDOW_S:
                self.altitudes.pop(0)
        high = altitude is not None and altitude >= self.min_altitude
        if not self.enabled or not airborne or not high:
            self.last_activity = None
            self.phase = None
            if self.level:
                self.level = None
                return 'clear'
            return None
        if self.last_activity is None:
            self.last_activity = now        # reached min_altitude, or just enabled
        vs = self.vertical_speed()
        if vs is None:
            return None
        self.phase = 'climb' if vs > LEVEL_FPM else 'descent' if vs < -LEVEL_FPM else 'cruise'
        if self.phase == 'climb':
            return None
        idle = now - self.last_activity
        due = None
        for level, after in zip(LEVELS, self.times[self.phase]):
            if idle >= after:
                due = level
        if due and (self.level is None or LEVELS.index(due) > LEVELS.index(self.level)):
            self.level = due
            return due
        return None

    def idle(self, now):
        """Seconds since the last crew activity while the monitor runs, or None."""
        return None if self.last_activity is None else now - self.last_activity

    def status(self, now, message):
        """Return the state for SHAREDINFO and the Utils pages (see status_text())."""
        idle = self.idle(now)
        return {
            'enabled': self.enabled,
            'level': self.level,
            'idle_s': None if idle is None else round(idle),
            'phase': self.phase,
            'times_min': [t / 60 for t in self.times.get(self.phase, ())],
            'min_altitude': self.min_altitude,
            'message': message,
        }


def status_text(status):
    """Return one line about the monitor's state, from status() (master's or via SHAREDINFO)."""
    names = {'A': 'advisory', 'C': 'caution', 'W': 'warning'}
    idle = status.get('idle_s')
    if status.get('level'):
        return (f"{status.get('message', '')} {names.get(status['level'], '?').upper()} "
                f"(no crew input for {(idle or 0) / 60:.0f} min)")
    if not status.get('enabled'):
        return "off"
    if idle is None:
        return f"inactive: on the ground, paused or below {status.get('min_altitude')} ft"
    times = '/'.join(f"{t:g}" for t in status.get('times_min') or ())
    after = f", advisory/caution/warning after {times} min" if times else ""
    return f"last crew input {idle / 60:.0f} min ago; {status.get('phase') or 'starting'}{after}"


class TestCrewAlertness(unittest.TestCase):
    """Tests for CrewAlertness."""

    CRUISE, DESCENT = (900, 1200, 1500), (300, 360, 420)

    def cam(self):
        """Return a monitor with CMC-PSX's default times, ignoring the tiller."""
        return CrewAlertness(True, self.CRUISE, self.DESCENT, {'Qh426'})

    @staticmethod
    def fly(cam, t0, t1, alt0, fpm=0, airborne=True, step=5):  # pylint: disable=too-many-arguments,too-many-positional-arguments
        """Tick from t0 to t1 (inclusive) at fpm from alt0; return the non-None results."""
        out = []
        t = t0
        while t <= t1:
            r = cam.tick(t, airborne, alt0 + fpm * (t - t0) / 60)
            if r:
                out.append((t, r))
            t += step
        return out

    def test_resolve_ignore(self):
        """Names are looked up, Q-codes kept, unknown names reported."""
        names = {'Tiller': 'Qh426', 'ParkBrkLev': 'Qh397'}
        codes, unknown = resolve_ignore(['Tiller', 'Qh389', 'NoSuchVar'], names.get)
        self.assertEqual((codes, unknown), ({'Qh426', 'Qh389'}, ['NoSuchVar']))

    def test_cruise_escalation(self):
        """Advisory, caution, warning at 15, 20, 25 min in level cruise; Qh input clears."""
        cam = self.cam()
        out = self.fly(cam, 0, 1600, 35000)
        self.assertEqual(out, [(900, 'A'), (1200, 'C'), (1500, 'W')])
        self.assertFalse(cam.activity('Qh426', 1601))    # ignored (tiller)
        self.assertFalse(cam.activity('Qs121', 1601))    # not a Qh
        self.assertTrue(cam.activity('Qh80', 1602))
        self.assertEqual(cam.cleared_by_activity(), 'W')
        self.assertEqual(self.fly(cam, 1605, 2510, 35000)[0], (2505, 'A'))

    def test_activity_resets(self):
        """Input before the advisory starts the timer again."""
        cam = self.cam()
        self.fly(cam, 0, 495, 35000)
        cam.activity('Qh82', 500)
        self.assertIsNone(cam.cleared_by_activity())
        self.assertEqual(self.fly(cam, 500, 1400, 35000), [(1400, 'A')])

    def test_climb_and_low(self):
        """Nothing below FL200 or in a climb; the alert comes at level-off."""
        cam = self.cam()
        self.assertEqual(self.fly(cam, 0, 1800, 5000), [])                 # low
        self.assertEqual(self.fly(cam, 1805, 3600, 20000, fpm=500.5), [])  # climbing
        out = self.fly(cam, 3605, 3800, 35000)                             # level off
        # 31 min idle above FL200: straight to warning...
        self.assertEqual([r for _, r in out], ['W'])
        self.assertLess(out[0][0], 3605 + 70)           # ...once the 60 s average says level

    def test_descent_times(self):
        """In a descent the shorter times apply; below FL200 it clears."""
        cam = self.cam()
        self.fly(cam, 0, 200, 35000)
        out = self.fly(cam, 205, 600, 35000, fpm=-1000)
        self.assertEqual([r for _, r in out], ['A', 'C', 'W'])
        self.assertEqual(out[0][0], 300)
        out = self.fly(cam, 605, 1500, 35000 - 1000 * 395 / 60, fpm=-1000)
        self.assertEqual([r for _, r in out], ['clear'])

    def test_altitude_jump(self):
        """A jump in altitude (situ load) starts the climb/descent average again."""
        cam = self.cam()
        self.fly(cam, 0, 200, 35000)
        # Jumped down to FL250 and climbing: a climb, not a 10,000 ft descent
        self.assertEqual(self.fly(cam, 205, 600, 25000, fpm=1500), [])
        self.assertEqual(cam.phase, 'climb')
        # Jumped up to FL350 and descending: descent times apply right away
        cam.activity('Qh77', 600)
        out = self.fly(cam, 605, 1000, 35000, fpm=-3000)
        self.assertEqual(out[0], (900, 'A'))

    def test_status_text(self):
        """The Utils page line for each state."""
        cam = self.cam()
        self.assertEqual(status_text(cam.status(0, 'PILOT RESPONSE')),
                         'inactive: on the ground, paused or below 20000 ft')
        self.fly(cam, 0, 300, 35000)
        self.assertEqual(status_text(cam.status(300, 'PILOT RESPONSE')),
                         'last crew input 5 min ago; cruise, '
                         'advisory/caution/warning after 15/20/25 min')
        self.fly(cam, 305, 1200, 35000)
        self.assertEqual(status_text(cam.status(1200, 'PILOT RESPONSE')),
                         'PILOT RESPONSE CAUTION (no crew input for 20 min)')
        cam.enabled = False
        cam.tick(1205, True, 35000)
        self.assertEqual(status_text(cam.status(1205, 'PILOT RESPONSE')), 'off')

    def test_inhibited_on_ground(self):
        """Never raised on the ground (or paused); landing clears it."""
        cam = self.cam()
        self.assertEqual(self.fly(cam, 0, 5000, 35000, airborne=False), [])
        self.assertEqual(self.fly(cam, 5005, 5910, 35000), [(5905, 'A')])
        self.assertEqual(cam.tick(5915, False, 35000), 'clear')
        self.assertIsNone(cam.idle(5915))

    def test_disable_clears(self):
        """Turning it off clears a raised alert; turning it on starts the timer then."""
        cam = self.cam()
        self.fly(cam, 0, 900, 35000)
        cam.enabled = False
        self.assertEqual(cam.tick(905, True, 35000), 'clear')
        cam.enabled = True
        self.assertEqual(self.fly(cam, 1000, 1910, 35000)[0], (1900, 'A'))
