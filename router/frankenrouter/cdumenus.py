"""Addon CDU menu prompts: who owns them, conflicts, and stale ones.

Addons put a prompt on a CDU's MENU page with cduL/cduC/cduR=<side><row><text>
(side 1 = left, 2 = right, row 1-6, e.g. "cduC=21GND_SV_>") and take it away
again with the same position and no text ("cduC=21"). PSX keeps a prompt
until something removes it -- it doesn't care which connection registered
it, and it never tells a new client which prompts exist (test sim,
2026-11-02). So a prompt outlives an addon that crashes, or a slave sim that
drops off the network, and two addons can claim the same position: the label
shows the last one, but pressing the key activates both (psx.py's
MCDU._subsystem() goes by position).

CduMenus records the registrations this router has seen from its clients,
per connection. It reports positions claimed by two different programs
(critical error), says which prompts went stale (their owner disconnected
and nobody took the position over within the timeout) and what to send to
clean them up, and lists the live prompts to register again after an
upstream reconnect. Pure bookkeeping; frankenrouter.py does the sending.
"""
import dataclasses
import unittest

CDU_KEYWORDS = ('cduL', 'cduC', 'cduR')


@dataclasses.dataclass
class Claim:
    """One connection's registration of a CDU menu position."""

    owner_id: int       # the connection's client_id
    owner: str          # human-readable, for log and error messages
    program: str        # same program = a restart, not a conflict ('' = unknown)
    text: str


def parse(key, value):
    """Return (cdu, position, text) for a CDU menu message, or None if it isn't one."""
    if key not in CDU_KEYWORDS or len(value) < 2:
        return None
    pos = value[:2]
    if pos[0] not in '12' or pos[1] not in '123456':
        return None
    return key[3], pos, value[2:]


def position_name(cdu, pos):
    """Human-readable position, e.g. 'CDU C R1'."""
    return f"CDU {cdu} {'L' if pos[0] == '1' else 'R'}{pos[1]}"


class CduMenus:
    """Registrations of addon CDU menu prompts seen from this router's clients."""

    def __init__(self):
        """Start with no registrations."""
        # (cdu, pos) -> claims, oldest first; the last one is what PSX shows
        self.claims = {}
        # owner_id -> monotonic time its connection went away
        self.gone = {}

    def update(self, key, value, owner_id, owner, program):
        """Record a cduX= message from a client.

        Returns (conflict, instead): conflict describes the conflict if this
        registration takes a position another live program has registered;
        instead is the line to send upstream in place of this one when the
        addon whose prompt is shown removes it while another live addon has
        also registered that position (its prompt is shown again).
        """
        parsed = parse(key, value)
        if parsed is None:
            return None, None
        cdu, pos, text = parsed
        claims = self.claims.setdefault((cdu, pos), [])
        if not text:
            instead = None
            if any(c.owner_id == owner_id for c in claims):
                shown = claims[-1].owner_id == owner_id
                claims[:] = [c for c in claims if c.owner_id != owner_id]
                live = [c for c in claims if c.owner_id not in self.gone]
                if shown and live:
                    claims[:] = live
                    instead = f"cdu{cdu}={pos}{live[-1].text}"
            else:
                claims.clear()      # a third party removed the prompt itself
            if not claims:
                del self.claims[(cdu, pos)]
            return None, instead
        # A new registration replaces this connection's own and, for a
        # program that restarted, the old connection's
        claims[:] = [c for c in claims
                     if c.owner_id != owner_id and not (program and c.program == program)]
        others = [c for c in claims if c.owner_id not in self.gone]
        claims.append(Claim(owner_id, owner, program, text))
        if others:
            return self._conflict_text(cdu, pos, others + [claims[-1]]), None
        return None, None

    @staticmethod
    def _conflict_text(cdu, pos, claims):
        names = ", ".join(f"{c.text.replace('_', ' ')!r} from {c.owner}" for c in claims)
        return f"{position_name(cdu, pos)} registered by more than one addon: {names}"

    def owner_gone(self, owner_id, now):
        """Note that a connection went away; its prompts go stale."""
        hidden = False
        for claims in self.claims.values():
            if any(c.owner_id == owner_id for c in claims[:-1]):
                # Not on the CDU: just forget it (and any conflict with it)
                claims[:-1] = [c for c in claims[:-1] if c.owner_id != owner_id]
                hidden = True
            if claims[-1].owner_id == owner_id:
                self.gone.setdefault(owner_id, now)
        return hidden

    def forget_owner(self, owner_id):
        """Drop a connection's registrations without removing its prompts; return how many."""
        count = 0
        for key, claims in list(self.claims.items()):
            mine = [c for c in claims if c.owner_id == owner_id]
            if mine:
                count += len(mine)
                claims[:] = [c for c in claims if c.owner_id != owner_id]
                if not claims:
                    del self.claims[key]
        return count

    def due(self, now, timeout):
        """Return the stale prompts to clean up now, removing them from the table.

        Each is (claim, cdu, pos, line, restored): line is what to send
        upstream, either the removal or, when another live addon had also
        registered that position, that addon's registration again (restored
        is then its Claim).
        """
        out = []
        for (cdu, pos), claims in list(self.claims.items()):
            shown = claims[-1]
            went = self.gone.get(shown.owner_id)
            if went is None or now - went < timeout:
                continue
            claims[:] = [c for c in claims if c.owner_id not in self.gone]
            if claims:
                out.append((shown, cdu, pos, f"cdu{cdu}={pos}{claims[-1].text}", claims[-1]))
            else:
                del self.claims[(cdu, pos)]
                out.append((shown, cdu, pos, f"cdu{cdu}={pos}", None))
        owners = {c.owner_id for claims in self.claims.values() for c in claims}
        for owner_id in list(self.gone):
            if owner_id not in owners:
                del self.gone[owner_id]
        return out

    def conflicts(self):
        """Return a description of each position claimed by more than one live addon."""
        out = []
        for (cdu, pos), claims in sorted(self.claims.items()):
            live = [c for c in claims if c.owner_id not in self.gone]
            if len(live) > 1:
                out.append(self._conflict_text(cdu, pos, live))
        return out

    def replay(self):
        """Return the registrations of live prompts, to send after an upstream reconnect."""
        return [f"cdu{cdu}={pos}{claims[-1].text}"
                for (cdu, pos), claims in sorted(self.claims.items())
                if claims[-1].owner_id not in self.gone]


class TestCduMenus(unittest.TestCase):
    """Tests for CduMenus."""

    def test_parse(self):
        """Positions and texts, and what isn't a CDU menu message."""
        self.assertEqual(parse('cduC', '21GND_SV_>'), ('C', '21', 'GND_SV_>'))
        self.assertEqual(parse('cduL', '16'), ('L', '16', ''))
        self.assertIsNone(parse('cduC', '31X'))
        self.assertIsNone(parse('cduC', '27X'))
        self.assertIsNone(parse('cduC', '2'))
        self.assertIsNone(parse('Qs77', '21X'))

    def test_register_remove(self):
        """A registration and its removal leave nothing behind."""
        m = CduMenus()
        self.assertEqual(m.update('cduC', '21GND_SV_>', 1, 'A', 'A'), (None, None))
        self.assertEqual(m.replay(), ['cduC=21GND_SV_>'])
        m.update('cduC', '21', 1, 'A', 'A')
        self.assertEqual(m.claims, {})

    def test_stale_removed_after_timeout(self):
        """A prompt whose owner went away is removed after the timeout."""
        m = CduMenus()
        m.update('cduC', '21GND_SV_>', 1, 'A', 'A')
        m.update('cduC', '22DOORS_>', 1, 'A', 'A')
        m.owner_gone(1, now=100.0)
        self.assertEqual(m.due(110.0, 15), [])
        self.assertEqual(m.replay(), [])
        lines = [d[3] for d in m.due(115.0, 15)]
        self.assertEqual(lines, ['cduC=21', 'cduC=22'])
        self.assertEqual((m.claims, m.gone), ({}, {}))

    def test_restart_within_timeout(self):
        """The same program registering again (new connection) keeps its prompt."""
        m = CduMenus()
        m.update('cduC', '15<SRSL', 1, 'SRSL-PSX', 'SRSL-PSX')
        m.owner_gone(1, now=100.0)
        self.assertEqual(m.update('cduC', '15<SRSL', 2, 'SRSL-PSX', 'SRSL-PSX'), (None, None))
        self.assertEqual(m.due(200.0, 15), [])
        self.assertEqual(m.replay(), ['cduC=15<SRSL'])

    def test_conflict(self):
        """Another program on a busy position is a conflict until one goes."""
        m = CduMenus()
        m.update('cduC', '21GND_SV_>', 1, 'Orchestration', 'Orchestration')
        conflict, _ = m.update('cduC', '21SRSL>', 2, 'SRSL-PSX', 'SRSL-PSX')
        self.assertIn('CDU C R1', conflict)
        self.assertIn("'GND SV >' from Orchestration", conflict)
        self.assertEqual(len(m.conflicts()), 1)
        m.owner_gone(1, now=100.0)      # the hidden one leaves: conflict over
        self.assertEqual(m.conflicts(), [])
        self.assertEqual(m.due(200.0, 15), [])

    def test_unknown_programs_conflict(self):
        """Unnamed clients (program '') never count as the same program."""
        m = CduMenus()
        m.update('cduC', '21A', 1, 'Any local client', '')
        self.assertIsNotNone(m.update('cduC', '21B', 2, 'Any local client', '')[0])

    def test_shown_owner_gone_restores_other(self):
        """When the shown prompt's owner goes, a live earlier claim is registered again."""
        m = CduMenus()
        m.update('cduC', '21GND_SV_>', 1, 'Orchestration', 'Orchestration')
        m.update('cduC', '21SRSL>', 2, 'SRSL-PSX', 'SRSL-PSX')
        m.owner_gone(2, now=100.0)
        self.assertEqual(m.conflicts(), [])
        (shown, _, _, line, restored) = m.due(115.0, 15)[0]
        self.assertEqual((shown.owner, line, restored.owner),
                         ('SRSL-PSX', 'cduC=21GND_SV_>', 'Orchestration'))
        self.assertEqual(m.replay(), ['cduC=21GND_SV_>'])

    def test_forget_owner(self):
        """A forgotten owner's prompts are never removed; others' stay tracked."""
        m = CduMenus()
        m.update('cduC', '21GND_SV_>', 5, 'CRAVE', 'router:CRAVE')
        m.update('cduC', '22DOORS_>', 5, 'CRAVE', 'router:CRAVE')
        m.update('cduC', '15<SRSL', 1, 'SRSL-PSX', 'SRSL-PSX')
        self.assertEqual(m.forget_owner(5), 2)
        self.assertEqual(m.due(1000.0, 15), [])
        self.assertEqual(m.replay(), ['cduC=15<SRSL'])

    def test_third_party_removal(self):
        """A removal from a connection that never registered clears the position."""
        m = CduMenus()
        m.update('cduC', '21GND_SV_>', 1, 'A', 'A')
        m.update('cduC', '21', 9, 'B', 'B')
        self.assertEqual(m.claims, {})

    def test_same_connection_overwrites(self):
        """One connection (e.g. a slave router) registering again is no conflict."""
        m = CduMenus()
        m.update('cduC', '21GND_SV_>', 5, 'CRAVE', 'router:CRAVE')
        self.assertEqual(m.update('cduC', '21SRSL>', 5, 'CRAVE', 'router:CRAVE'), (None, None))
        self.assertEqual(m.replay(), ['cduC=21SRSL>'])

    def test_shown_removes_restores_other(self):
        """The shown addon removing its prompt shows the other live addon's again."""
        m = CduMenus()
        m.update('cduC', '23P_ONE_>', 1, 'sim A', 'router:A')
        m.update('cduC', '23Q_TWO_>', 2, 'sim B', 'router:B')
        self.assertEqual(m.update('cduC', '23', 2, 'sim B', 'router:B'), (None, 'cduC=23P_ONE_>'))
        self.assertEqual(m.replay(), ['cduC=23P_ONE_>'])
        self.assertEqual(m.conflicts(), [])
        # The hidden one removing its own changes nothing on the CDU
        m.update('cduC', '23Q_TWO_>', 2, 'sim B', 'router:B')
        self.assertEqual(m.update('cduC', '23', 1, 'sim A', 'router:A'), (None, None))
        self.assertEqual(m.replay(), ['cduC=23Q_TWO_>'])
