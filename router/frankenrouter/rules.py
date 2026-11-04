# pylint: disable=fixme,too-many-lines
"""Message routing rules.

This module will primarily return to the router what action it needs
to take with a message.

However, it will also do minor changes to router state to simplify the
code (e.g update a variable).

The module will not do any actions that requires asyncio, e.g sending
messages.

The goal is for this module to be possible to test separately using
pyunittest.
"""

import enum
import hashlib
import hmac
import json
import logging
import re
import unittest
import time

from .connection import NOACCESS_ACCESS_LEVEL
from .routercache import RouterCacheException, RouterCacheTypeError

# Create a frozen set here rather than a list that gets recreated for
# each message:
# Qs120="FltControls"; Mode=ECON; Min=5; Max=14;
# Qs357="Brakes"; Mode=ECON; Min=3; Max=9;
# Qs436="Tla"; Mode=ECON; Min=7; Max=23;
# Qh388="SpdBrkLever"; Mode=ECON; Min=0; Max=800;
# Qh426="Tiller"; Mode=ECON; Min=-999; Max=999;
FLIGHT_CONTROL_INPUT_KEYWORDS = frozenset({'Qs120', 'Qs357', 'Qs436', 'Qh388', 'Qh426'})

# Exception to the flight-control-input filter above, for Qh388 (SpdBrkLever)
# only: a non-flying sim is allowed to arm the speedbrake to help the pilot
# flying, but never to retract it or deploy it further. "Arm" is this exact
# confirmed PSX lever position. Enforced as increase-only and capped here -
# see the FLIGHT_CONTROL_INPUT_KEYWORDS block in route().
SPDBRK_ARM_VALUE = 44

# Same for teh traffic keywords
TRAFFIC_KEYWORDS = frozenset({'Qs450', 'Qs451'})

# PTT buttons and audio panel switches: sim-local only, filtered from other sims
# Qh82="LcpPttCp"; Qh93="LcpPttFo"; Qh410="SwitchesAudioL";
# Qh411="SwitchesAudioC"; Qh412="SwitchesAudioR"
PTT_KEYWORDS = frozenset({'Qh82', 'Qh93', 'Qh410', 'Qh411', 'Qh412'})

# MSROUTER, an MCP bridge in slave sims, shows "CHECK SWITCHES" in the free
# caution message (Qs419, FreeMsgC) when its physical MCP switches don't match
# PSX's, and clears it once they do. The slave router delivers it to its own
# sim; the master router drops it, so it doesn't reach PSX or the other sims
# (each of which may have an identical MCP). The clear (Qs419=) is not filtered.
# See [filtering] master_drop_check_switches in the config.
CHECK_SWITCHES_KEYWORD = 'Qs419'
CHECK_SWITCHES_TEXT = 'CHECK SWITCHES'

# PSX.NET.Orchestration relies on native PTT presses to detect a "doors to
# manual" PA announcement, but a PTT press from another sim never reaches it
# there since it's dropped by the PTT_KEYWORDS filter above (by design, to
# stop cross-sim radio bleed) before Orchestration ever sees it. When that
# drop fires, route() also synthesizes an addon=GROUND.HANDLING PTT event
# (see the PTT_KEYWORDS block below) from this table, so every
# PSX.NET.Orchestration instance in every sim still gets a press/release
# signal, just via addon= instead of the native PSX variable.
#
# key -> (panel, {value: edge}). Deliberately a direct, stateless per-value
# mapping, not the press-combining state machine described in the
# PSX.NET.Orchestration integration spec (tracking whether ACP and glarewing
# are held together, only emitting on the combined edge) -- overlapping ACP
# + glarewing PTT on the same panel is rare enough that a spurious extra or
# early edge is an acceptable outcome (worst case: the ~2s simulated PA call
# needs to be repeated), and skipping it avoids needing any state that could
# go stale/wrong across a router reconnect.
PTT_TO_GROUND_HANDLING = {
    'Qh410': ('LEFT', {'26': 'DOWN', '-1': 'UP'}),
    'Qh411': ('CENTRE', {'26': 'DOWN', '-1': 'UP'}),
    'Qh412': ('RIGHT', {'26': 'DOWN', '-1': 'UP'}),
    'Qh82': ('LEFT', {'1': 'DOWN', '0': 'UP'}),
    'Qh93': ('RIGHT', {'1': 'DOWN', '0': 'UP'}),
}

# Post-connect quarantine: for a while after a downstream connection is
# made, the router drops writes of these variables from it. PSX instances
# and addons that have just connected (or whose slave router has just
# joined the master) send their own, stale state for a while, e.g.:
# - Mf* (malfunctions, e.g. MfAcBus1-4): a PSX instance starting from a situ
#   with a stale malfunction failed all AC buses in the shared cockpit
#   (2026-10-22).
# - TkofRefNg (Qs546, TAKEOFF REF CG/TRIM & RWY/POS no-go string): a PSX
#   client with another situ resets valid entries elsewhere.
# - MaweaBits (Qi190): a slave sim joining the WorldFlight master set the
#   A/P disconnect wailer bit in the shared cockpit (2026-11-02).
# - Weather (zones, METARs, SIGMETs, corridor): slave sims sent their old
#   weather up to ~110 s after joining, overwriting frankenweather's.
# Deliberate writes after the quarantine (a malfunction set from a slave
# sim, manual weather) pass. Never applied to upstream.
#
# Variable name (a trailing "*" matches a prefix) -> (seconds, or None for
# QUARANTINE_DEFAULT_S; True if frankenweather is exempt). frankenweather
# (client id _FRANKENWEATHER_CLIENT_ID) sends all the weather right after
# it connects, and its name always comes first on a new connection. The
# [quarantine] config section can change the times and add variables.
# PrinterPush: the printer's PPR ADV button (0 pressed, -1 released). A
# slave router drops it from upstream ([filtering] slave_drop_printer_push):
# frankenprint feeds the real paper on a press, and other sims' crews don't
# want theirs fed (2026-11-03).
PRINTER_PUSH_KEYWORD = 'Qh415'

# The weather-zone variables (see Rules.route()'s orchestration egress)
_WEATHER_ZONE_VAR_RE = re.compile(r'^(Wx|WxMode|Metar)([1-7])$')
# frankenweather's WXMODES feed: at most this delay (s), zones 1-7
ORCHESTRATION_MAX_DELAY_S = 60.0


def parse_orchestration_wxmodes(text):
    """Parse a FRANKENWEATHER:WXMODES payload; return {'delay_s', 'zones'} or None.

    The payload is JSON: {"delay_s": <s>, "zones": {"<1-7>": "<WxMode string>"}}.
    """
    try:
        data = json.loads(text)
        delay = float(data.get('delay_s', 0.0))
        zones = {int(z): str(m) for z, m in (data.get('zones') or {}).items()}
    except (ValueError, TypeError, AttributeError):
        return None
    if not 0.0 <= delay <= ORCHESTRATION_MAX_DELAY_S:
        return None
    if any(not 1 <= z <= 7 or m.count(';') < 5 or '\n' in m for z, m in zones.items()):
        return None
    return {'delay_s': delay, 'zones': zones}


QUARANTINE_DEFAULT_S = 60.0
# How often per variable a quarantined client is given the network's value
# back (see Rules.quarantine_restore())
QUARANTINE_MAX_RESTORES = 3
# A client writing MfEvent this soon after its first welcome is a PSX main
# client (see Rules.detect_psx_main_client())
PSX_MAIN_CLIENT_DETECT_S = 1.0
QUARANTINE_WEATHER_S = 120.0
QUARANTINE_WEATHER_VARIABLES = (
    ['WxAloft', 'WxBasic', 'WxClust', 'MetarsUp', 'Windshear', 'WxCorridorSel',
     'WxCorridorTxt', 'WxSigmet', 'SigmetOn', 'WxAutoSet', 'WxBurst', 'WxSlowTransit',
     'WxBits'] +
    [f'Wx{i}' for i in range(1, 8)] +
    [f'WxMode{i}' for i in range(1, 8)] +
    [f'Metar{i}' for i in range(1, 8)])
QUARANTINE_VARIABLES = {
    'Mf*': (30.0, False),
    'TkofRefNg': (5.0, False),
    'MaweaBits': (None, False),
    # Centre tank pump switches (with their PRESS light): a starting PSX main
    # client writes 1/257 for ~4.5 s after its welcome; five of them
    # ping-ponged it, and every write reached the master (2026-11-03)
    'FuelCtrL': (10.0, False),
    'FuelCtrR': (10.0, False),
    # Aircraft model pin programming (Instructor > Model > Programming): a
    # joining sim's PSX main client wrote its own 88 over the master's 8,
    # at Addis Ababa just before takeoff (2026-11-04)
    'CfgPin3Prog': (None, False),
    **{name: (QUARANTINE_WEATHER_S, True) for name in QUARANTINE_WEATHER_VARIABLES},
}

# Addon names that are expected to produce regular addon= traffic; their
# forwarded messages are logged at debug rather than info. FRANKENWEATHER is
# not listed here since it never reaches this classification -- it is
# special-cased and forwarded via its own filter in handle_addon() below.
_KNOWN_ADDONS = frozenset(('FRANKENCDUPROXY', 'FRANKENMSFSBRIDGE', 'FRANKENCONTROL',
                           'PSXNETVATSIM'))

# Same idea, but for addons whose messages don't use the normal NAME:payload
# colon convention (so the "addon" token above ends up being the whole
# rest-of-line, e.g. "BACARS.REMOTE;;;;V1|<base64>"), matched by prefix
# instead of exact membership. GROUND.HANDLING isn't listed here even though
# it uses the same convention -- it's special-cased earlier in handle_addon()
# and never reaches this classification.
_KNOWN_ADDON_PREFIXES = ('BACARS.REMOTE', 'GROUND.COMPANIES', 'GEOVR.PSX.', 'MSFS.CLIENT',
                         'MSFS.WEATHER', 'PSX.NET.VPILOT.')

# client_provided_id FrankenWeather identifies itself with on the network
# (see frankenweather.py's __MY_CLIENT_ID__). Used so that other
# FrankenWeather instances keep receiving each other's addon=FRANKENWEATHER
# messages even though those are otherwise restricted to frankenrouters --
# see handle_addon()'s FRANKENWEATHER case and frankenweather.py's
# _note_conflict() for why that visibility matters.
_FRANKENWEATHER_CLIENT_ID = 'WEATHER'

# How long a pure-START-non-ECON value from upstream is treated as the
# private response to a "start" command, rather than an unsolicited
# broadcast-worthy update. Used both for router.start_sent_at (see
# route()) and for connection.last_start_relayed_at (see
# client_broadcast() in frankenrouter.py), which must use the same
# window so a chained frankenrouter's private welcome response isn't
# broadcast by an intermediate hop before the window has even elapsed.
START_PRIVATE_WINDOW_S = 5.0

# How long after we ask our own upstream for a "bang" - because we
# noticed it had recomputed one ECON variable as a side effect of
# another one changing, without broadcasting the result (see the Qi25
# handling in route()) - an unchanged echo from that private reply is
# assumed to just be upstream re-confirming state we already have,
# rather than a coincidental unrelated update. Kept short since a bang
# round trip to a directly-connected upstream is normally near-instant;
# anything that actually differs from our cache is forwarded normally
# regardless of this window, so a genuine change is never lost.
UPSTREAM_RESYNC_BANG_WINDOW_S = 2.0


class RulesAction(enum.Enum):
    """The action the router needs to take for a message.

    DROP: do not forward the message

    DISCONNECT: do not forward, and disconnect client

    UPSTREAM_ONLY: sent message to upstream only

    NORMAL: send message to all endpoints (upstream and clients)
    except the sender

    FILTER: apply a custom filter to determine which endpoints to send
    to. Which filter to use is included in extra_data.

    - 'endpoint_name_regexp': regexp

    If the regexp matches the endpoint name, the message is not sent
    to that endpoint.

    - 'start': True

    Send to other frankenrouters AND clients that are waiting for
    START variables.

    - 'nolong': True

    Send only to clients with nolong=False, i.e ones that want all
    variables.

    - 'reply': message

    Send the message to the sender (used for e.g FRDP PONG)

    - 'frankenweather_forward': True

    Send only to frankenrouters (upstream and downstream), plus any
    downstream client identifying itself as FrankenWeather (see
    _FRANKENWEATHER_CLIENT_ID) so cross-instance conflict detection keeps
    working. Upstream is only sent to if it is itself a frankenrouter.

    - 'frankenrouters_only': True

    Send only to frankenrouters (upstream and downstream). Upstream is
    only sent to if it is itself a frankenrouter. Used for
    FRANKENWEATHER:SIGMETJSON, which no other client needs.

    - 'ground_handling_forward': list of display_name values

    Send only to frankenrouters (upstream and downstream), plus any
    downstream client whose display_name is in the given list (see
    [filtering] ground_handling_forward_names in the config file).
    Upstream is only sent to if it is itself a frankenrouter.

    Other things we can include in extra_data:

    - 'frdp_rtt': the FRDP RTT time in seconds (float)

    """

    DROP = enum.auto()
    DISCONNECT = enum.auto()
    UPSTREAM_ONLY = enum.auto()
    NORMAL = enum.auto()
    FILTER = enum.auto()


class RulesCode(enum.Enum):
    """The code for a routing decision.

    Some of these are just informational, some require that the router
    take some action, e.g filter messages.
    """

    MESSAGE_INVALID = enum.auto()
    FRDP_PING = enum.auto()
    FRDP_PONG = enum.auto()
    FRDP_IDENT = enum.auto()
    FRDP_MY_CONTROLS = enum.auto()
    FRDP_ALL_CONTROL_LOCKS = enum.auto()
    FRDP_NO_CONTROL_LOCKS = enum.auto()
    FRDP_FLIGHTCONTROLS = enum.auto()
    FRDP_ELEVATION_SOURCE = enum.auto()
    FRDP_TRAFFIC_SOURCE = enum.auto()
    FRDP_MASTER_BANG = enum.auto()
    FRDP_CREW_ALERTNESS = enum.auto()
    FRDP_JOIN = enum.auto()
    FRDP_SUBSCRIBE = enum.auto()
    FRDP_CLIENTINFO = enum.auto()
    FRDP_ROUTERINFO = enum.auto()
    FRDP_SHAREDINFO = enum.auto()
    FRDP_FLIGHTINFO = enum.auto()
    FRDP_AUTH_CHALLENGE = enum.auto()
    FRDP_AUTH_FAIL = enum.auto()
    FRDP_AUTH_OK = enum.auto()
    FRDP_AUTH_ALREADY_HAS_ACCESS = enum.auto()
    FRDP_ACCESS_LEVEL = enum.auto()
    NAME_FROM_FRANKENROUTER = enum.auto()
    NAME_LEARNED = enum.auto()
    NAME_NOCHANGE = enum.auto()
    NAME_REJECTED = enum.auto()
    NOLONG = enum.auto()
    NOTIFY = enum.auto()
    NONPSX = enum.auto()
    NOWRITE = enum.auto()
    DEMAND = enum.auto()
    ADDON_FORWARDED = enum.auto()
    ADDON_FORWARDED_KNOWN = enum.auto()
    FRANKENWEATHER_FILTERED = enum.auto()
    GROUND_HANDLING_FILTERED = enum.auto()
    AGAIN = enum.auto()
    START = enum.auto()
    LOAD1 = enum.auto()
    LOAD2 = enum.auto()
    LOAD3 = enum.auto()
    BANG = enum.auto()
    BANG_REJECTED = enum.auto()
    EXIT = enum.auto()
    PBSKAQ = enum.auto()
    LAYOUT = enum.auto()
    PTT = enum.auto()
    POST_CONNECT_QUARANTINE = enum.auto()
    PSXNETVATSIM = enum.auto()
    CDUPROXY = enum.auto()
    KEYVALUE_FILTERED_INGRESS_SIM_LOCAL = enum.auto()
    KEYVALUE_FILTERED_EGRESS_SIM_LOCAL = enum.auto()
    CHECK_SWITCHES_DROPPED = enum.auto()
    KEYVALUE_FILTERED_INGRESS = enum.auto()
    KEYVALUE_FILTERED_INGRESS_SILENT = enum.auto()
    KEYVALUE_FILTER_EGRESS = enum.auto()
    KEYVALUE_NORMAL = enum.auto()
    PARKING_BRAKE_FORCE_RELEASE = enum.auto()
    OBSERVER_MODE = enum.auto()
    FRDP_SIMEVENTS = enum.auto()
    JETTISON_MLW_CHANGED = enum.auto()
    FRDP_DISCONNECT_SIM = enum.auto()


class Rules():  # pylint: disable=too-many-public-methods
    """A routing ruleset."""

    def __init__(self, router):
        """Initialize the instance."""
        self.logger = logging.getLogger(__name__)
        # A reference to the Frankenrouter
        self.router = router
        # The message we are processing
        self.line = None
        # Reference to sender endpoint (if still connected)
        self.sender = None
        # Post-connect quarantine table, built on first use (see
        # QUARANTINE_VARIABLES): {name: (seconds, frankenweather_exempt)},
        # the prefix entries as [(prefix, (seconds, frankenweather_exempt))]
        # and the longest time
        self._quarantine = None
        self._quarantine_prefixes = []
        self._quarantine_max_s = 0.0

    def _build_quarantine(self):
        """Build the quarantine table: QUARANTINE_VARIABLES with [quarantine] applied."""
        conf = getattr(self.router.config, 'quarantine', None)
        default = conf.default_seconds if conf is not None else QUARANTINE_DEFAULT_S
        table = {name: (default if seconds is None else seconds, fw_exempt)
                 for name, (seconds, fw_exempt) in QUARANTINE_VARIABLES.items()}
        if conf is not None:
            for name, seconds in conf.variables.items():
                # A variable added in the config has no exemption
                table[name] = (seconds, table.get(name, (None, False))[1])
        table = {name: entry for name, entry in table.items() if entry[0] > 0}
        self._quarantine = {name: entry for name, entry in table.items()
                            if not name.endswith('*')}
        self._quarantine_prefixes = [(name[:-1], entry) for name, entry in table.items()
                                     if name.endswith('*')]
        self._quarantine_max_s = max((entry[0] for entry in table.values()), default=0.0)

    def post_connect_quarantine(self, key):
        """Drop a quarantined variable from a connection that has only just connected.

        A welcome reset (Frankenrouter.reset_client_welcome()) restarts
        the quarantine: a PSX main client writes the same values after a
        reset welcome as after its first one. Returns the DROP result, or
        None if the write may pass. See QUARANTINE_VARIABLES.
        """
        if self._quarantine is None:
            self._build_quarantine()
        age = time.perf_counter() - max(self.sender.connected_at,
                                        self.sender.quarantine_restarted_at)
        if age >= self._quarantine_max_s:
            return None
        name = self.router.variables.get_variable_name(key)
        entry = self._quarantine.get(name)
        if entry is None:
            entry = next((e for prefix, e in self._quarantine_prefixes
                          if name.startswith(prefix)), None)
        if entry is None:
            return None
        seconds, fw_exempt = entry
        if age >= seconds or (
                fw_exempt and self.sender.client_provided_id == _FRANKENWEATHER_CLIENT_ID):
            return None
        self.logger.info(
            "Quarantine: dropping %s from %s, connected or reset %.1f s ago (< %.0f s): %s",
            name, self.sender.display_name, age, seconds, self.line[:200])
        restore = self.quarantine_restore(key, name)
        return self.myreturn(RulesAction.DROP, RulesCode.POST_CONNECT_QUARANTINE,
                             extra_data={"reply": restore} if restore else None)

    def detect_psx_main_client(self, key):
        """Mark the sender as a PSX main client if it writes MfEvent right after its first welcome.

        On 2026-11-03 every PSX main client wrote MfEvent (Qi130) 0.1-0.3 s
        after the load3 of its first welcome, and no other client (addons,
        vPilot, MSFS Router, EFB, ...) did. Its name doesn't tell: other
        clients use clientName= too. Used for the automatic welcome reset
        (Frankenrouter._housekeeping_auto_reset_clients()).
        """
        if key != 'Qi130' or self.sender.looks_like_psx_main or self.sender.is_frankenrouter:
            return
        first = self.sender.first_welcome_done_at
        if first is None:
            return
        after = time.perf_counter() - first
        if after <= PSX_MAIN_CLIENT_DETECT_S:
            self.sender.looks_like_psx_main = True
            self.logger.info(
                "%s looks like a PSX main client (MfEvent %.1f s after its first welcome)",
                self.sender.display_name, after)

    def quarantine_restore(self, key, name):
        """Return the line that gives a quarantined client the network's value back, or None.

        The client has already applied the value it wrote, and PSX doesn't
        send a client's write back, so nothing else would correct it: on
        2026-11-03 PSX main clients kept their own empty wind corridor
        (WxCorridorTxt=#) after the quarantine dropped it, and showed a
        different wind, temperature and IAS. Only when the router's cache
        has a different value, never for a DELTA (event) variable, and at
        most QUARANTINE_MAX_RESTORES times per variable per client, in case
        a client answers by writing its value again.
        """
        if key in self.router.variables.keywords_with_mode('DELTA'):
            return None
        try:
            cached = self.router.cache.get_value(key)
        except RouterCacheException:
            return None
        _, _, value = self.line.partition('=')
        if str(cached) == value:
            return None
        done = self.sender.quarantine_restores.get(key, 0)
        if done >= QUARANTINE_MAX_RESTORES:
            return None
        self.sender.quarantine_restores[key] = done + 1
        self.logger.info("Quarantine: giving %s the network's %s back: %.100s",
                         self.sender.display_name, name, cached)
        return f"{key}={cached}"

    def handle_addon_frankenrouter_ping(self, payload):
        """Handle an FRDP PING message.

        Format:
        addon=FRANKENROUTER:<protocol version>:PING:<unique request id>
        """
        reply = f"addon=FRANKENROUTER:{self.router.frdp_version}:PONG:{payload}"
        self.sender.is_frankenrouter = True
        # Send PONG but do not forward the PING anywhere
        return self.myreturn(
            RulesAction.DROP, RulesCode.FRDP_PING,
            extra_data={"reply": reply})

    def handle_addon_frankenrouter_pong(self, payload):
        """Handle an FRDP PONG message.

        Format:
        addon=FRANKENROUTER:<protocol version>:PONG:<request id from PING message>
        """
        expected_id = self.sender.frdp_ping_request_id
        if payload != expected_id:
            return self.myreturn(
                RulesAction.DROP,
                RulesCode.MESSAGE_INVALID,
                message=(
                    f"Unexpected PONG ID {payload}, expected {expected_id}"
                    f" from {self.sender.display_name}"
                )
            )
        frdp_rtt = time.perf_counter() - self.sender.frdp_ping_sent
        # FIXME: ignore RTT numbers in a period after upstream or a
        # client has connected (10s?)
        self.sender.frdp_ping_rtts.append(frdp_rtt)
        # Drop message
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_PONG,
                             extra_data={'frdp_rtt': frdp_rtt})

    def handle_addon_frankenrouter_ident(self, payload):
        """Handle a FRDP IDENT message.

        Format:
        addon=FRANKENROUTER:<protocol version>:IDENT:<sim name>:<router name>:<uuid>
        """
        try:
            (simname, routername, uuid) = payload.split(':')
        except ValueError:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Malformed FRDP IDENT payload: {self.line}"
            )
        self.sender.simulator_name = simname
        self.sender.router_name = routername
        self.sender.client_provided_id = simname
        self.sender.display_name = f"{simname}/{routername}"
        self.sender.uuid = uuid
        self.sender.display_name_source = "FRDP IDENT"
        self.router.connection_state_changed()
        # Drop message
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_IDENT)

    def handle_addon_frankenrouter_my_controls(self):
        """Handle a FRDP MY_CONTROLS message.

        Format:
        addon=FRANKENROUTER:<protocol version>:MY_CONTROLS

        When a router receives this, the message itself is dropped but
        a new FLIGHTCONTROLS message is sent upstream. The master
        router will then update the sharedinfo data which controls the
        flight control filtering.
        """
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_MY_CONTROLS)

    def handle_addon_frankenrouter_all_control_locks(self):
        """Handle a FRDP ALL_CONTROL_LOCKS message.

        Format:
        addon=FRANKENROUTER:<protocol version>:ALL_CONTROL_LOCKS

        When a router receives this, the message itself is dropped but
        a new FLIGHTCONTROLS message is sent upstream. The master
        router will then update the sharedinfo data which controls the
        flight control filtering.
        """
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_ALL_CONTROL_LOCKS)

    def handle_addon_frankenrouter_no_control_locks(self):
        """Handle a FRDP NO_CONTROL_LOCKS message.

        Format:
        addon=FRANKENROUTER:<protocol version>:NO_CONTROL_LOCKS

        When a router receives this, the message itself is dropped but
        a new FLIGHTCONTROLS message is sent upstream. The master
        router will then update the sharedinfo data which controls the
        flight control filtering.
        """
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_NO_CONTROL_LOCKS)

    def handle_addon_frankenrouter_subscribe(self):
        """Handle a FRDP SUBSCRIBE message.

        Format:
        addon=FRANKENROUTER:<protocol version>:SUBSCRIBE

        Opts a plain (non-router) addon into receiving FRDP broadcasts
        (ROUTERINFO, SHAREDINFO, SIMEVENTS) without making it
        a full FRDP peer: unlike sending name=...FRANKEN.PY frankenrouter...
        to fake router identification, this does not set is_frankenrouter,
        so the client is not FRDP-pinged and none of the router-to-router-
        only behavior elsewhere in this module (PTT/audio cross-sim
        filtering, MY_CONTROLS/ALL_CONTROL_LOCKS/NO_CONTROL_LOCKS, AUTH,
        START-private-window relaying, etc.) is affected by it.
        """
        self.sender.frdp_subscribed = True
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_SUBSCRIBE)

    def handle_addon_frankenrouter_elevation_source(self, payload):
        """Handle FRDP ELEVATION_SOURCE message.

        Format:
        addon=FRANKENROUTER:<protocol version>:ELEVATION_SOURCE:<sim name>

        When this router is the SHAREDINFO master, record the source sim and
        trigger a SHAREDINFO broadcast so all routers update their filters.
        Otherwise forward upstream toward the master.
        """
        if self.router.is_sharedinfo_authority():
            prev = self.router.sharedinfo['elevation_source_simulator']
            self.router.sharedinfo['elevation_source_simulator'] = payload
            self.logger.info("SET elevation_source_simulator to %s",
                             self.router.sharedinfo['elevation_source_simulator'])
            self.router.frdp_sharedinfo_requested = True
            if payload != prev:
                self.router.record_sim_event(
                    'sharedinfo_change',
                    field='elevation_source_simulator',
                    value=payload, prev=prev)
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_ELEVATION_SOURCE)
        return self.myreturn(RulesAction.UPSTREAM_ONLY, RulesCode.FRDP_ELEVATION_SOURCE)

    def handle_addon_frankenrouter_traffic_source(self, payload):
        """Handle FRDP TRAFFIC_SOURCE message.

        Format:
        addon=FRANKENROUTER:<protocol version>:TRAFFIC_SOURCE:<sim name>

        When this router is the SHAREDINFO master, record the source sim and
        trigger a SHAREDINFO broadcast so all routers update their filters.
        Otherwise forward upstream toward the master.
        """
        if self.router.is_sharedinfo_authority():
            prev = self.router.sharedinfo['traffic_source_simulator']
            self.router.sharedinfo['traffic_source_simulator'] = payload
            self.router.frdp_sharedinfo_requested = True
            if payload != prev:
                self.router.record_sim_event(
                    'sharedinfo_change',
                    field='traffic_source_simulator',
                    value=payload, prev=prev)
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_TRAFFIC_SOURCE)
        return self.myreturn(RulesAction.UPSTREAM_ONLY, RulesCode.FRDP_TRAFFIC_SOURCE)

    def handle_addon_frankenrouter_master_bang(self):
        """Handle a FRDP MASTER_BANG message.

        Format: addon=FRANKENROUTER:<protocol version>:MASTER_BANG

        Sent by a slave router's "Global BANG" util button, which cannot
        bang its own upstream directly (that's the master router, not the
        real PSX Main Server). When this router is the SHAREDINFO
        authority (master or standalone), it is the one actually connected
        to the real Main Server, so it sends "bang" there and stops the
        message here. Otherwise forward upstream toward the master.
        """
        if self.router.is_sharedinfo_authority():
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_MASTER_BANG)
        return self.myreturn(RulesAction.UPSTREAM_ONLY, RulesCode.FRDP_MASTER_BANG)

    def handle_addon_frankenrouter_crew_alertness(self, payload):
        """Handle a FRDP CREW_ALERTNESS message.

        Format: addon=FRANKENROUTER:<protocol version>:CREW_ALERTNESS:on|off|test

        Sent by a slave router's Utils page to turn the master router's
        crew alertness monitor (crewalertness.py) on or off, or to show its
        warning as a test. The master acts on it (extra_data 'command');
        any other router forwards it upstream toward the master. Not from
        clients that may not write.
        """
        if payload not in ('on', 'off', 'test'):
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Invalid CREW_ALERTNESS payload: {self.line}")
        if not self.allow_write():
            return self.myreturn(RulesAction.DROP, RulesCode.NOWRITE)
        if self.router.config.identity.type == 'master':
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_CREW_ALERTNESS,
                                 extra_data={'command': payload})
        if self.router.is_sharedinfo_authority():
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_CREW_ALERTNESS)
        return self.myreturn(RulesAction.UPSTREAM_ONLY, RulesCode.FRDP_CREW_ALERTNESS)

    def handle_addon_frankenrouter_flightcontrols(self, payload):
        """Handle a FRDP FLIGHTCONTROLS message.

        Format:
        addon=FRANKENROUTER:<protocol version>:FLIGHTCONTROLS:<flying sim name>

        For a visual indicator, we use Qs421="FreeMsgM"; Mode=ECON; Min=0; Max=16;

        The message is wrapped in parentheses so it can be told apart from a
        real 747 EICAS message at a glance.

        e.g
        Qs421=(PF: MACRO)
        Qs421=(PF: ALL)
        Qs421=(PF: NONE)
        """
        prev = self.router.sharedinfo['pilot_flying_simulator']
        # Update sharedinfo
        if payload == 'NO_CONTROL_LOCKS':
            self.router.sharedinfo['pilot_flying_simulator'] = "NO_CONTROL_LOCKS"
            message = "Qs421="
        elif payload == 'ALL_CONTROL_LOCKS':
            self.router.sharedinfo['pilot_flying_simulator'] = "ALL_CONTROL_LOCKS"
            message = "Qs421=(PF: NOONE)"
        else:
            self.router.sharedinfo['pilot_flying_simulator'] = payload
            # We limit the length of the sim identifier so the parenthesized
            # message still fits within Qs421's 16-character limit.
            ident = self.router.sharedinfo['pilot_flying_simulator'][:10].upper()
            message = f"Qs421=(PF: {ident})"
        self.router.frdp_sharedinfo_requested = True
        new_val = self.router.sharedinfo['pilot_flying_simulator']
        if self.router.is_sharedinfo_authority() and new_val != prev:
            self.router.record_sim_event(
                'sharedinfo_change',
                field='pilot_flying_simulator',
                value=new_val, prev=prev)

        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_FLIGHTCONTROLS,
                             extra_data={'message': message})

    def handle_addon_frankenrouter_join(self, payload):
        """Handle a FRDP JOIN message.

        Format:
        addon=FRANKENROUTER:<protocol version>:JOIN:<sim name>:<router name>:<uuid>:<upstream uuid>
        """
        # For now, we do nothing with this data
        # (simname, routername, uuid, upstream_uuid) = payload.split(':')
        self.logger.debug("Got FRDP JOIN from %s: %s", self.sender.peername, payload)
        return self.myreturn(RulesAction.NORMAL, RulesCode.FRDP_JOIN)

    def handle_addon_frankenrouter_clientinfo(self, payload):
        """Handle a FRDP CLIENTINFO message.

        Format:
        addon=FRANKENROUTER:<protocol version>:CLIENTINFO:<JSON data>

        JSON data example:

        {
            "laddr": "127.0.0.1",
            "lport": 12345,
            "client_provided_id": "GATEFIND",
            "display_name": "PSX.NET GateFinder"
        }
        """
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got FRDP CLIENTINFO message from upstream: {self.line}"
            )
        try:
            clientinfo = json.loads(payload)
        except json.decoder.JSONDecodeError:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Invalid JSON data in FRDP CLIENTINFO message: {self.line}"
            )
        required = ('laddr', 'lport', 'client_provided_id', 'display_name')
        if not all(k in clientinfo for k in required):
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Missing required fields in FRDP CLIENTINFO message: {self.line}"
            )
        peername = (clientinfo['laddr'], clientinfo['lport'])
        if peername in self.router.clients:
            client = self.router.clients[peername]
            if client.display_name_source == 'name message':
                self.logger.debug(
                    "Ignoring CLIENTINFO for %s: already identified via %s",
                    peername, client.display_name_source)
            else:
                client.client_provided_id = clientinfo['client_provided_id']
                client.display_name = clientinfo['display_name']
                client.client_provided_display_name = clientinfo['display_name']
                client.display_name_source = "FRDP CLIENTINFO"
                self.router.connection_state_changed()
        else:
            self.logger.warning(
                "Got CLIENTINFO data for non-connected client %s", peername)
        # Drop message
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_CLIENTINFO)

    def handle_addon_frankenrouter_routerinfo(self, payload):
        """Handle a FRDP ROUTERINFO message.

        Format:
        addon=FRANKENROUTER:<protocol version>:ROUTERINFO:<JSON data>

        This message should be forwarded to the network, since we want
        it to reach all frankenrouters.
        """
        try:
            routerinfo = json.loads(payload)
        except json.decoder.JSONDecodeError:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Invalid JSON data in FRDP ROUTERINFO message: {self.line}"
            )
        if 'uuid' not in routerinfo:
            self.logger.warning("DISCARDING FRDP ROUTERINFO message without uuid: %s", self.line)
            # Drop message
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_ROUTERINFO)

        self.router.routerinfo[routerinfo['uuid']] = routerinfo
        # Add received timestamp
        self.router.routerinfo[routerinfo['uuid']]['received'] = time.time()
        # This is our own upstream's self-reported ROUTERINFO -- our one
        # chance to notice a clock skew against it before it's corrected.
        # See Frankenrouter.maybe_sync_clock_from_upstream_skew().
        if self.sender is self.router.upstream:
            self.router.maybe_sync_clock_from_upstream_skew(routerinfo['uuid'])
        # Forward message to network but only to frankenrouters
        return self.myreturn(
            RulesAction.FILTER,
            RulesCode.KEYVALUE_FILTER_EGRESS,
            extra_data={'exclude_non_frankenrouter': True})

    def handle_addon_frankenrouter_disconnect_sim(self, payload):
        """Handle a FRDP DISCONNECT_SIM message.

        Format:
        addon=FRANKENROUTER:<protocol version>:DISCONNECT_SIM:<JSON data>
        JSON data: {"from_sim": ..., "target_sim": ..., "reason": ...}

        Flooded to every frankenrouter (like ROUTERINFO/SHAREDINFO) since
        the target sim could be anywhere in the topology, not just
        upstream/downstream of whoever sent it. Only a slave-type router
        whose own simulator_name matches target_sim, AND whose own
        upstream connection is itself to a router in a *different* sim
        (i.e. we are the "edge" router for our sim, not an internal
        router within a multi-router sim) actually acts on it; everyone
        else just forwards it along.
        """
        try:
            data = json.loads(payload)
        except json.decoder.JSONDecodeError:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Invalid JSON data in FRDP DISCONNECT_SIM message: {self.line}"
            )
        if not all(k in data for k in ('from_sim', 'target_sim', 'reason')):
            self.logger.warning(
                "DISCARDING FRDP DISCONNECT_SIM message missing fields: %s", self.line)
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_DISCONNECT_SIM)

        own_sim = self.router.config.identity.simulator
        is_edge_router = (
            self.router.upstream is not None and
            self.router.upstream.simulator_name != own_sim
        )
        extra_data = {'exclude_non_frankenrouter': True}
        if (
                self.router.config.identity.type == 'slave' and
                is_edge_router and
                data['target_sim'] == own_sim
        ):
            extra_data['disconnect_sim'] = data
        # Forward message to network but only to frankenrouters
        return self.myreturn(
            RulesAction.FILTER, RulesCode.FRDP_DISCONNECT_SIM, extra_data=extra_data)

    def handle_addon_frankenrouter_sharedinfo(self, payload):  # pylint: disable=too-many-branches
        """Handle a FRDP SHAREDINFO message.

        Format:
        addon=FRANKENROUTER:<protocol version>:SHAREDINFO:<JSON data>

        This message should be forwarded to the network, since we want
        it to reach all frankenrouters.
        """
        self.logger.debug("Handling SHAREDINFO data: %s", payload)
        try:
            sharedinfo = json.loads(payload)
        except json.decoder.JSONDecodeError:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Invalid JSON data in FRDP SHAREDINFO message: {self.line}"
            )
        if 'master_uuid' not in sharedinfo:
            self.logger.warning(
                "DISCARDING FRDP SHAREDINFO message without master_uuid: %s", self.line)
            # Drop message
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_SHAREDINFO)

        if self.router.is_sharedinfo_authority():
            raise SystemExit(
                f"SHAREDINFO message received from {sharedinfo['master_uuid']}, "
                f"but this router is the SHAREDINFO authority "
                f"({self.router.config.identity.type}). This should never happen.")

        # Merge data from sharedinfo package into our own variables
        self.router.sharedinfo['master_uuid'] = sharedinfo['master_uuid']
        for key in [
            'pilot_flying_simulator',
            'elevation_source_simulator',
            'traffic_source_simulator',
            'errors',
            'warnings',
            'crew_alertness',
        ]:
            if key in sharedinfo:
                self.router.sharedinfo[key] = sharedinfo[key]

        # Update local filter state based on source assignments in SHAREDINFO.
        # Do NOT trigger frdp_sharedinfo_requested to avoid a broadcast loop.
        # The master/standalone router never updates its own filters from SHAREDINFO.
        if not self.router.is_sharedinfo_authority():
            own_sim = self.router.config.identity.simulator
            filter_changed = False
            if 'elevation_source_simulator' in sharedinfo:
                new_val = sharedinfo['elevation_source_simulator'] != own_sim
                if new_val != self.router.filter_elevation:
                    self.router.filter_elevation = new_val
                    filter_changed = True
            if 'traffic_source_simulator' in sharedinfo:
                new_val = sharedinfo['traffic_source_simulator'] != own_sim
                if new_val != self.router.filter_traffic:
                    self.router.filter_traffic = new_val
                    filter_changed = True
            if filter_changed:
                self.router.status_display_requested = True
                self.router.frdp_routerinfo_requested = True

            # Apply the experimental load_marker_pause feature's current
            # network-wide state (see config.performance.load_marker_pause_enabled/
            # _delay and Frankenrouter._pause_for_load_marker()). Only a
            # 'master' router ever includes these fields (see
            # send_frdp_sharedinfo()), so a 'slave' router simply mirrors
            # whatever it was last told; absent fields (e.g. during a
            # rolling upgrade against an older master) leave our current
            # value untouched.
            if 'load_marker_pause_enabled' in sharedinfo:
                self.router.load_marker_pause_enabled = sharedinfo['load_marker_pause_enabled']
            if 'load_marker_pause_delay' in sharedinfo:
                self.router.load_marker_pause_delay = sharedinfo['load_marker_pause_delay']

        # Forward message to network but only to frankenrouters
        return self.myreturn(
            RulesAction.FILTER,
            RulesCode.KEYVALUE_FILTER_EGRESS,
            extra_data={'exclude_non_frankenrouter': True})

    def handle_addon_frankenrouter_simevents(self, payload):
        """Handle a FRDP SIMEVENTS message.

        Format:
        addon=FRANKENROUTER:<protocol version>:SIMEVENTS:<JSON data>

        Stores received events in all_sim_events and forwards to other frankenrouters.
        """
        try:
            data = json.loads(payload)
        except json.decoder.JSONDecodeError:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Invalid JSON in FRDP SIMEVENTS: {self.line}")
        received_at = time.time()
        for event in data.get('events', []):
            event['received_at'] = received_at
            self.router.all_sim_events.append(event)
            if self.router.simevents_logger is not None:
                self.router.simevents_logger.info(
                    self.router._simevents_log_line(event))  # pylint: disable=protected-access
        return self.myreturn(
            RulesAction.FILTER, RulesCode.FRDP_SIMEVENTS,
            extra_data={'exclude_non_frankenrouter': True})

    def handle_addon_frankenrouter_auth_challenge(self, nonce):
        """Handle FRDP AUTH_CHALLENGE message received from upstream.

        Format (received by downstream from upstream):
        addon=FRANKENROUTER:<protocol version>:AUTH_CHALLENGE:<nonce>

        Stores the nonce so frdp_send_task can use it to send a hashed AUTH.
        """
        if not self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got FRDP AUTH_CHALLENGE from non-upstream: {self.line}"
            )
        return self.myreturn(
            RulesAction.DROP, RulesCode.FRDP_AUTH_CHALLENGE,
            extra_data={'nonce': nonce})

    def handle_addon_frankenrouter_auth(self, payload):  # pylint: disable=too-many-return-statements
        """Handle FRDP AUTH message.

        Format: addon=FRANKENROUTER:<ver>:AUTH:hmac-sha256:<hmac_hex>
        where hmac_hex = HMAC-SHA256(key=password, msg=nonce).hexdigest()
        and nonce was sent by this router in an AUTH_CHALLENGE message.
        Cleartext AUTH (the pre-HMAC format) is no longer accepted.
        """
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got FRDP AUTH message from upstream: {self.line}"
            )
        if self.sender.has_access():
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_ALREADY_HAS_ACCESS)
        if payload == "":
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_FAIL,
                                 message="empty password")
        if not payload.startswith("hmac-sha256:"):
            return self.myreturn(
                RulesAction.DROP, RulesCode.FRDP_AUTH_FAIL,
                message="cleartext AUTH is no longer accepted, use hmac-sha256")
        received_hmac = payload[len("hmac-sha256:"):]
        nonce = self.sender.auth_nonce
        matched_password = None
        if nonce:
            candidates = [
                a.match_password
                for a in self.router.config.access
                if a.match_password is not None
            ]
            for session_pwd in (self.router.session_password,
                                self.router.observer_session_password):
                if session_pwd:
                    candidates.append(session_pwd)
            for pwd in candidates:
                expected = hmac.new(
                    pwd.encode(), nonce.encode(), hashlib.sha256
                ).hexdigest()
                if hmac.compare_digest(received_hmac, expected):
                    matched_password = pwd
                    break
        if matched_password is None:
            fail_reason = "no challenge was issued" if not nonce else "invalid password"
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_FAIL,
                                 message=fail_reason)
        self.sender.update_access_level(matched_password)
        if not self.sender.has_access():
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_FAIL,
                                 message="invalid password")
        self.router.connection_state_changed()
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_OK)

    def handle_addon_frankenrouter_access_level(self, payload):
        """Handle FRDP ACCESS_LEVEL message.

        Format:
        addon=FRANKENROUTER:<protocol version>:ACCESS_LEVEL:<level>

        Sent by the upstream router to inform us of the access level we have
        been granted. Valid levels: 'crew', 'observer', 'unknown'.
        """
        if not self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got ACCESS_LEVEL from non-upstream: {self.line}")
        if payload not in ('crew', 'observer', 'unknown'):
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Invalid ACCESS_LEVEL payload: {self.line}")
        if payload != self.sender.access_level:
            self.logger.info(
                "Upstream access level changed: %s -> %s",
                self.sender.access_level, payload)
            self.sender.access_level = payload
            self.router.status_display_requested = True
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_ACCESS_LEVEL)

    def handle_addon_frankenrouter(self, rest):  # pylint: disable=too-many-return-statements,too-many-branches
        """Handle FRANKENROUTER addon message."""
        (message_type, _, payload) = rest.partition(":")
        if message_type == 'PING':
            return self.handle_addon_frankenrouter_ping(payload)
        if message_type == 'PONG':
            return self.handle_addon_frankenrouter_pong(payload)
        if message_type == 'IDENT':
            return self.handle_addon_frankenrouter_ident(payload)
        if message_type == 'MY_CONTROLS':
            return self.handle_addon_frankenrouter_my_controls()
        if message_type == 'ALL_CONTROL_LOCKS':
            return self.handle_addon_frankenrouter_all_control_locks()
        if message_type == 'NO_CONTROL_LOCKS':
            return self.handle_addon_frankenrouter_no_control_locks()
        if message_type == 'SUBSCRIBE':
            return self.handle_addon_frankenrouter_subscribe()
        if message_type == 'FLIGHTCONTROLS':
            return self.handle_addon_frankenrouter_flightcontrols(payload)
        if message_type == 'ELEVATION_SOURCE':
            return self.handle_addon_frankenrouter_elevation_source(payload)
        if message_type == 'TRAFFIC_SOURCE':
            return self.handle_addon_frankenrouter_traffic_source(payload)
        if message_type == 'MASTER_BANG':
            return self.handle_addon_frankenrouter_master_bang()
        if message_type == 'CREW_ALERTNESS':
            return self.handle_addon_frankenrouter_crew_alertness(payload)
        if message_type == 'JOIN':
            return self.handle_addon_frankenrouter_join(payload)
        if message_type == 'ROUTERINFO':
            return self.handle_addon_frankenrouter_routerinfo(payload)
        if message_type == 'DISCONNECT_SIM':
            return self.handle_addon_frankenrouter_disconnect_sim(payload)
        if message_type == 'SHAREDINFO':
            return self.handle_addon_frankenrouter_sharedinfo(payload)
        if message_type == 'FLIGHTINFO':
            # Obsolete (Flight Centre flight info support was removed);
            # silently dropped so older routers on the network don't
            # trigger an "unsupported FRDP message" warning.
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_FLIGHTINFO)
        if message_type == 'SIMEVENTS':
            return self.handle_addon_frankenrouter_simevents(payload)
        if message_type == 'CLIENTINFO':
            return self.handle_addon_frankenrouter_clientinfo(payload)
        if message_type == 'AUTH':
            return self.handle_addon_frankenrouter_auth(payload)
        if message_type == 'AUTH_CHALLENGE':
            return self.handle_addon_frankenrouter_auth_challenge(payload)
        if message_type == 'ACCESS_LEVEL':
            return self.handle_addon_frankenrouter_access_level(payload)
        # Drop unknown FRDP messages
        return self.myreturn(
            RulesAction.DROP, RulesCode.MESSAGE_INVALID,
            message=f"Unsupported FRDP message type {message_type}: {self.line}"
        )

    def handle_addon(self, rest):  # pylint: disable=too-many-return-statements,too-many-branches
        """Handle an addon= message."""
        try:
            (addon, payload) = rest.split(":", 1)
        except ValueError:
            self.logger.debug("Got unsupported addon message: %s", rest)
            addon = rest
            payload = ""
        # Drop addon=FRANKENCDUPROXY from from other sims
        if addon == 'FRANKENCDUPROXY':
            if (self.sender.is_frankenrouter and
                    self.router.config.identity.simulator != self.sender.simulator_name):
                self.logger.info(
                    "Dropping FRANKENCDUPROXY addon from other-sim frankenrouter %s",
                    self.sender.simulator_name)
                return self.myreturn(RulesAction.DROP, RulesCode.CDUPROXY)

        # Drop addon=FRANKENMSFSBRIDGE from non-elevation-master sources.
        # Only filter it coming from downstream (i.e. our own local
        # PSX.NET.MSFS.Client, which sends these) - a copy arriving from
        # upstream is from the authoritative elevation-master sim and
        # should always be accepted.
        if (addon == 'FRANKENMSFSBRIDGE' and not self.sender.upstream and
                self.router.filter_elevation):
            return self.myreturn(
                RulesAction.DROP,
                RulesCode.KEYVALUE_FILTERED_INGRESS_SILENT,
                message="filtered FRANKENMSFSBRIDGE addon as filter_elevation is set")

        # FRANKENWEATHER addon traffic (STATE/TURBSTATE/WINDSTATE/SIGMETSTATE...) can be
        # large -- WINDSTATE in particular runs several KB per message --
        # and is only useful to other frankenrouters (to relay state
        # across the mesh) and to other FrankenWeather instances
        # themselves, which rely on seeing each other's messages to detect
        # a conflicting duplicate instance on the network (see
        # frankenweather.py's _note_conflict()). Restrict it accordingly
        # instead of broadcasting it to every client.
        if addon == 'FRANKENWEATHER':
            if not self.allow_write():
                return self.myreturn(RulesAction.DROP, RulesCode.NOWRITE)
            # WXMODES: frankenweather's 1 s WxMode feed for the clients that
            # draw PSX's CBs elsewhere ([filtering] orchestration_names), with
            # the delay all their weather-zone writes get. Frankenrouters only;
            # each router hands it to its own such clients (see
            # Frankenrouter.orchestration_wxmodes()).
            if payload.startswith('WXMODES:'):
                feed = parse_orchestration_wxmodes(payload[len('WXMODES:'):])
                if feed is None:
                    return self.myreturn(
                        RulesAction.FILTER, RulesCode.FRANKENWEATHER_FILTERED,
                        extra_data={'frankenrouters_only': True},
                        message="malformed FRANKENWEATHER:WXMODES, only forwarded")
                return self.myreturn(
                    RulesAction.FILTER, RulesCode.FRANKENWEATHER_FILTERED,
                    extra_data={'orchestration_wxmodes': feed})
            # SIGMETJSON (the parsed SIGMET download, ~30 KB) is only for
            # the routers' own /api/sigmet/latest/json, so frankenrouters only.
            if payload.startswith('SIGMETJSON:'):
                return self.myreturn(
                    RulesAction.FILTER,
                    RulesCode.FRANKENWEATHER_FILTERED,
                    extra_data={'frankenrouters_only': True})
            return self.myreturn(
                RulesAction.FILTER,
                RulesCode.FRANKENWEATHER_FILTERED,
                extra_data={'frankenweather_forward': _FRANKENWEATHER_CLIENT_ID})

        # GROUND.HANDLING (PSX.NET.Orchestration) doesn't use the normal
        # NAME:payload colon convention -- its messages look like
        # "GROUND.HANDLING;;;;V1|STATUS|runtime|..." with no colon at all,
        # so the split above raised ValueError and `addon` here is the
        # whole rest-of-line; matched by prefix instead of exact equality.
        # Its traffic is frequent (mostly small STATUS heartbeats) and only
        # useful to other frankenrouters and to a configured allowlist of
        # ground-handling-aware clients (see [filtering]
        # ground_handling_forward_names) -- most clients (e.g. constrained
        # hardware addons) have no use for it.
        if addon.startswith('GROUND.HANDLING'):
            if not self.allow_write():
                return self.myreturn(RulesAction.DROP, RulesCode.NOWRITE)
            return self.myreturn(
                RulesAction.FILTER,
                RulesCode.GROUND_HANDLING_FILTERED,
                extra_data={
                    'ground_handling_forward':
                        self.router.config.filtering.ground_handling_forward_names})

        if addon == 'FRANKENROUTER':
            if ':' not in payload:
                return self.myreturn(
                    RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                    message=f"Malformed FRANKENROUTER addon message: {self.line}"
                )
            (version, payload) = payload.split(":", 1)
            try:
                version = int(version)
            except ValueError:
                version = 0  # e.g older versions that did not have the version field
            if version != self.router.frdp_version:
                return self.myreturn(
                    RulesAction.DISCONNECT, RulesCode.MESSAGE_INVALID,
                    message=f"FRDP version mismatch in message: {self.line}"
                )
            return self.handle_addon_frankenrouter(payload)

        # Drop addon=PSXNETVATSIM:SELECT_ACP:* from other sims
        if addon == 'PSXNETVATSIM' and 'SELECT_ACP:' in payload:
            if self.sender.is_frankenrouter:
                if self.router.config.identity.simulator != self.sender.simulator_name:
                    self.logger.info(
                        "Dropping addon=PSXNETVATSIM:SELECT_ACP from other sim %s: %s",
                        self.sender.simulator_name, self.line)
                    return self.myreturn(RulesAction.DROP, RulesCode.PSXNETVATSIM)

        # Drop addon=GEOVR.PSX.* (PSX.NET.VATSIM's voice client: CONTROL
        # discover/state/connect/result/disconnect, STATUS, STATUS.REQUEST)
        # from other sims: it's for each sim's own clients. Like
        # GROUND.HANDLING it has no NAME:payload colon, so it's matched on
        # the rest-of-line. Frequent, so logged at debug only.
        if rest.startswith('GEOVR.PSX.'):
            if self.sender.is_frankenrouter:
                if self.router.config.identity.simulator != self.sender.simulator_name:
                    self.logger.debug(
                        "Dropping addon=GEOVR.PSX from other sim %s: %s",
                        self.sender.simulator_name, self.line)
                    return self.myreturn(RulesAction.DROP, RulesCode.PSXNETVATSIM)

        # Unhandled addon messages should be forwarded, but only from
        # clients that are allowed to write.
        if not self.allow_write():
            return self.myreturn(RulesAction.DROP, RulesCode.NOWRITE)
        code = (RulesCode.ADDON_FORWARDED_KNOWN
                if addon in _KNOWN_ADDONS or addon.startswith(_KNOWN_ADDON_PREFIXES)
                else RulesCode.ADDON_FORWARDED)
        return self.myreturn(RulesAction.NORMAL, code)

    def handle_name(self, rest):
        """Handle a name= message.

        Format: examples:

        name=VPLG:vPilot Plugin
        name=:PSX Sounds
        name=EFB1:PSX.NET EFB For Windows
        name=BACARS:BA ACARS Simulation

        If frankenrouter: set is_frankenrouter and display_name

        else: set short display name based on the name given (we often
        have to clean it up a little)

        Using the prefix "R" for frankenrouters
        """
        if re.match(r".*:FRANKEN\.PY frankenrouter", rest):
            display_name = rest.split(":")[0]
            newly_identified = not self.sender.is_frankenrouter
            self.sender.is_frankenrouter = True
            self.sender.display_name = display_name
            self.sender.display_name_source = "name message"
            if newly_identified:
                # Re-trigger broadcasts now that this client is known to be a
                # frankenrouter; the initial connection_state_changed() at
                # connect time fires before identification, so exclude_non_frankenrouter
                # broadcasts would have skipped this client.
                self.router.connection_state_changed()
            return self.myreturn(RulesAction.DROP, RulesCode.NAME_FROM_FRANKENROUTER)

        if rest == "":
            return self.myreturn(RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                                 message=f"name keyword without value: {self.line}")

        if self.sender.is_frankenrouter:
            return self.myreturn(RulesAction.DROP, RulesCode.NAME_REJECTED,
                                 message=f"ignoring name keyword from frankenrouter: {self.line}")
        # It seems that proper addons send name=<ID>:<display name>
        # where ID is short and unique if there are several such
        # clients in a sim. ID can sometimes be empty, e.g if not
        # providing a custom ID to PSX Sounds. Display name is longer
        # and more human-readable.

        # Safe defaults
        provided_display_name = rest
        provided_id = rest

        if ":" in rest:
            (provided_id, provided_display_name) = rest.split(":", 1)

        name_changed = False

        if provided_display_name != self.sender.display_name:
            name_changed = True
            self.sender.display_name = provided_display_name
            self.sender.display_name_source = "name message"

        if provided_id != self.sender.client_provided_id:
            name_changed = True
            self.sender.client_provided_id = provided_id

        if provided_display_name != self.sender.client_provided_display_name:
            name_changed = True
            self.sender.client_provided_display_name = provided_display_name

        if name_changed:
            self.router.connection_state_changed()
            return self.myreturn(RulesAction.DROP, RulesCode.NAME_LEARNED)

        return self.myreturn(RulesAction.DROP, RulesCode.NAME_NOCHANGE)

    def handle_nolong(self):
        """Handle the nolong keyword."""
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got nolong message from upstream: {self.line}"
            )
        self.sender.nolong = not self.sender.nolong
        return self.myreturn(RulesAction.DROP, RulesCode.NOLONG)

    def handle_notify(self, value):
        """Handle the notify keyword.

        Not a PSX protocol keyword -- a convention some hardware
        clients speak (and that the third-party Simstack Switch router
        honours), e.g. "notify=Qs323;Qs324;", to ask for only a
        specific set of Q-code variables rather than everything. Once
        set, client_broadcast() sends this client only the listed
        keywords, plus the normal protocol control traffic every
        client still needs regardless (load1/load2/load3, start, bang,
        exit, name=, addon=, Lexicon entries, ...) -- see
        ClientConnection.notify_keywords. Always terminated here
        rather than forwarded: this is purely a subscription
        preference for this one local connection, nothing upstream or
        any other client has a reason to see it. An empty list (e.g.
        "notify=" or "notify=;") clears the filter back to "send
        everything", the default.
        """
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got notify message from upstream: {self.line}"
            )
        keywords = frozenset(k for k in value.split(';') if k)
        self.sender.notify_keywords = keywords or None
        self.logger.info(
            "Got notify from %s, restricting to: %s",
            self.sender.display_name,
            sorted(keywords) if keywords else "(cleared, sending everything again)")
        return self.myreturn(RulesAction.DROP, RulesCode.NOTIFY)

    def handle_demand(self, value):
        """Handle the demand keyword."""
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got demand message from upstream: {self.line}"
            )
        self.sender.demands.add(value)
        return self.myreturn(RulesAction.UPSTREAM_ONLY, RulesCode.DEMAND)

    def handle_again(self):
        """Handle the again keyword."""
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got again message from upstream: {self.line}"
            )
        return self.myreturn(RulesAction.UPSTREAM_ONLY, RulesCode.AGAIN)

    def handle_start(self):
        """Handle the start keyword.

        Records when this connection last asked us to relay a "start"
        upstream. For a chained frankenrouter this means it is
        currently welcoming one of its own clients; client_broadcast()
        uses this (rather than blanket-trusting is_frankenrouter) to
        decide whether a private START response from upstream should
        be forwarded to it - see the isonlystart handling for why
        that distinction matters.

        Also records that *we* are now expecting a private START
        response on our own upstream connection, exactly as if we had
        sent "start" ourselves via client_add_to_network(). This
        matters when we're only relaying someone else's request (e.g.
        a client frankenrouter welcoming one of its own clients): route()
        uses router.start_sent_at to decide whether an incoming
        pure-START-non-ECON variable from upstream is that private
        response or an unsolicited broadcast-worthy update, and without
        this, a relayed request would never mark that window as open on
        this hop, so the response would leak to this router's own
        unrelated local clients.
        """
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got start message from upstream: {self.line}"
            )
        self.sender.last_start_relayed_at = time.perf_counter()
        self.router.start_sent_at = self.sender.last_start_relayed_at
        return self.myreturn(RulesAction.UPSTREAM_ONLY, RulesCode.START)

    def handle_pbskaq(self):
        """Handle the pleaseBeSoKindAndQuit keyword.

        If the sender is a frankenrouter and its simulator_name is
        different than ours, drop the message, otherwise forward it.

        This ensures that layout commands from other simulators does
        not affect us.


        """
        if self.sender.is_frankenrouter:
            if self.router.config.identity.simulator != self.sender.simulator_name:
                self.logger.info(
                    "Dropping pleaseBeSoKindAndQuit command from %s",
                    self.sender.simulator_name)
                return self.myreturn(RulesAction.DROP, RulesCode.PBSKAQ)
        return self.myreturn(RulesAction.NORMAL, RulesCode.PBSKAQ)

    def handle_layout(self):
        """Handle the layout keyword.

        If the sender is a frankenrouter and its simulator_name is
        different than ours, drop the message, otherwise forward it.

        This ensures that layout commands from other simulators does
        not affect us.
        """
        if self.sender.is_frankenrouter:
            if self.router.config.identity.simulator != self.sender.simulator_name:
                self.logger.info(
                    "Dropping layout command from %s: %s",
                    self.sender.simulator_name, self.line)
                return self.myreturn(RulesAction.DROP, RulesCode.LAYOUT)
        return self.myreturn(RulesAction.NORMAL, RulesCode.LAYOUT)

    def handle_load1(self):
        """Handle the load1 keyword."""
        self.router.last_load1 = time.perf_counter()
        return self.myreturn(RulesAction.NORMAL, RulesCode.LOAD1)

    def handle_load2(self):
        """Handle the load1 keyword."""
        return self.myreturn(RulesAction.NORMAL, RulesCode.LOAD2)

    def handle_load3(self):
        """Handle the load1 keyword."""
        self.router.last_load3 = time.perf_counter()
        return self.myreturn(RulesAction.NORMAL, RulesCode.LOAD3)

    def handle_bang(self):
        """Handle the bang keyword."""
        # drop any bang from upstream
        if self.sender.upstream:
            self.logger.info("Dropped bang from upstream")
            return self.myreturn(RulesAction.DROP, RulesCode.BANG_REJECTED)
        # this will generate a synthetic bang reply from cached data
        return self.myreturn(RulesAction.DROP, RulesCode.BANG)

    def handle_exit(self):
        """Handle the exit keyword.

        The router should close the connection, but the message should
        not be forwarded.
        """
        return self.myreturn(RulesAction.DROP, RulesCode.EXIT)

    def traffic_drop_reason(self):
        """Return why traffic (TfcPos/TfcVel) from self.sender must be dropped, or None.

        Only the traffic master sim may supply traffic, from any of its
        clients. Checking names is not enough: on 2026-11-03 one sim's
        vPilot ran the PSX.NET plugin twice, and the copy that never sent
        name= fed the master sim while another sim was traffic master.
        - A router that isn't the traffic master's (filter_traffic) drops
          traffic from all its own clients, named or not.
        - The SHAREDINFO authority also drops traffic from a router of
          another sim than the traffic master (covers older slave routers),
          and from its own clients when another sim is the traffic master.
        No traffic master (NOSIM) lets the master accept all, as before.
        """
        if not self.sender.is_frankenrouter and self.router.filter_traffic:
            return "this sim is not the traffic master"
        if not self.router.is_sharedinfo_authority():
            return None
        source = self.router.sharedinfo.get('traffic_source_simulator', 'NOSIM')
        if source in (None, 'NOSIM'):
            return None
        if self.sender.is_frankenrouter:
            sender_sim = self.sender.simulator_name
        else:
            sender_sim = self.router.config.identity.simulator
        if sender_sim != source:
            return f"{sender_sim} is not the traffic master ({source})"
        return None

    def myreturn(self, action, code, message=None, extra_data=None):
        """Return a routing decision."""
        return (action, code, message, extra_data)

    def _ptt_ground_handling_extra_data(self, key, value):
        """Return extra_data for a synthetic addon=GROUND.HANDLING PTT event, or None.

        Called when a PTT/audio-panel variable is about to be dropped for
        crossing a sim boundary (see PTT_KEYWORDS handling in route()) --
        see PTT_TO_GROUND_HANDLING's docstring comment for why this is a
        direct value->edge lookup rather than the press-combining state
        machine PSX.NET.Orchestration's own integration spec describes.
        """
        if not self.router.config.filtering.ptt_ground_handling_translation:
            return None
        mapping = PTT_TO_GROUND_HANDLING.get(key)
        edge = mapping[1].get(value) if mapping else None
        if edge is None:
            return None
        panel = mapping[0]
        return {
            'synthetic_ground_handling_line':
                f"addon=GROUND.HANDLING;;;;V1|EVENT|1|PTT|{panel}|{edge}",
            'exclude_simulator': self.sender.simulator_name,
        }

    def allow_write(self):
        """Determine if this client is allowed to write."""
        if self.sender.upstream:
            return True
        if self.sender.can_write():
            return True
        return False

    def route(self, line, sender):  # pylint: disable=too-many-return-statements,too-many-branches, too-many-statements,too-many-locals
        """Decide on routing, log, etc.

        line is a PSX network message string, e.g "Qi123=456"

        sender is a frankenrouter.ClientConnection or UpstreamConnection object
        sender is None if from upstream

        returns tuple(action, code, message, additional_data)

        additional_data is a dictionary with context-specific data,
        e.g an additional message to send. What to do with it is
        determined by the code.
        """
        self.logger.debug("Starting route planning - sender=%s, line=%s", sender, line)
        self.line = line
        self.sender = sender

        # Drop empty lines (can break some addons, e.g psx.pylint
        if line == '':
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got empty line: {self.line}"
            )

        # Sanity check of message
        if len(line.splitlines()) > 1:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message="multi-line message")

        # Split line into key - value. Note: non-key-value messages
        # (e.g "load1") also exist, they will just end up in key
        key, _, value = line.partition("=")

        if key == 'name':
            return self.handle_name(value)

        # PSX 10.184 added support for naming your PSX main clients,
        # but it uses the keyword "clientName" rather than "name". For
        # now, we will be handling clientName just as name.
        if key == 'clientName':
            return self.handle_name(value)

        if key == 'addon':
            return self.handle_addon(value)

        if key == 'demand':
            return self.handle_demand(value)

        #
        # Only clients allowed to write beyond this point
        #

        # Note to self: addon= is partially allowed (FRDP AUTH) for
        # clients not allowed to write, so needs to be above this
        # line.
        if not self.allow_write():
            return self.myreturn(RulesAction.DROP, RulesCode.NOWRITE)

        if key == 'again':
            return self.handle_again()

        if key == 'start':
            return self.handle_start()

        if key == 'pleaseBeSoKindAndQuit':
            return self.handle_pbskaq()

        if key == 'layout':
            return self.handle_layout()

        if (
                key == PRINTER_PUSH_KEYWORD and
                self.sender.upstream and
                self.router.config.identity.type == 'slave' and
                self.router.config.filtering.slave_drop_printer_push
        ):
            self.logger.debug("Dropping %s from upstream (printer PPR ADV)", self.line)
            return self.myreturn(RulesAction.DROP, RulesCode.KEYVALUE_FILTERED_INGRESS_SIM_LOCAL)

        if not self.sender.upstream:
            self.detect_psx_main_client(key)
            quarantined = self.post_connect_quarantine(key)
            if quarantined is not None:
                return quarantined

        if (
                key in PTT_KEYWORDS and
                self.sender.is_frankenrouter and
                self.router.config.identity.simulator != self.sender.simulator_name
        ):
            audio_panel = key in ('Qh410', 'Qh411', 'Qh412')
            if not audio_panel or value in ('26', '-1'):
                self.logger.info(
                    "Dropping PTT/audio variable from %s: %s",
                    self.sender.simulator_name, self.line)
                return self.myreturn(
                    RulesAction.DROP, RulesCode.PTT,
                    extra_data=self._ptt_ground_handling_extra_data(key, value))

        if (
                key in self.router.config.psx.filter_from_other_sim and
                self.sender.is_frankenrouter and
                self.router.config.identity.simulator != self.sender.simulator_name
        ):
            self.logger.info(
                "Dropping %s from other-sim frankenrouter %s", key, self.sender.simulator_name)
            return self.myreturn(RulesAction.DROP, RulesCode.KEYVALUE_FILTERED_INGRESS_SIM_LOCAL)

        if (
                key == CHECK_SWITCHES_KEYWORD and
                value.strip().upper() == CHECK_SWITCHES_TEXT and
                not self.sender.upstream and
                self.router.config.identity.type == 'master' and
                self.router.config.filtering.master_drop_check_switches
        ):
            self.logger.info("Dropping %s=%s from %s (sim-local MCP caution)",
                             key, value, self.sender.display_name)
            return self.myreturn(RulesAction.DROP, RulesCode.CHECK_SWITCHES_DROPPED)

        if key in self.router.config.psx.filter_to_other_sim:
            return self.myreturn(
                RulesAction.FILTER,
                RulesCode.KEYVALUE_FILTERED_EGRESS_SIM_LOCAL,
                extra_data={'exclude_other_sim_frankenrouters': True})

        if key == 'load1':
            return self.handle_load1()

        if key == 'load2':
            return self.handle_load2()

        if key == 'load3':
            return self.handle_load3()

        if key == 'bang':
            return self.handle_bang()

        if key == 'exit':
            return self.handle_exit()

        # Observer mode: block all key-value writes from local clients
        if not self.sender.upstream and self.router.observer_mode:
            return self.myreturn(RulesAction.DROP, RulesCode.OBSERVER_MODE)

        # Update router variable stats database
        self.router.variable_stats_add(key, sender.peername)

        if key == 'nolong':
            return self.handle_nolong()

        if key == 'notify':
            return self.handle_notify(value)

        # Non-PSX keywords: forward with warning
        # FIXME: make configurable - strict mode?
        if not self.router.variables.is_psx_keyword(key):
            return self.myreturn(RulesAction.NORMAL, RulesCode.NONPSX)

        #
        # Handle normal key=value messages
        #

        #
        # Ingress filtering. Some variables we don't even want in the cache.
        #

        # Ingress filter: flight controls if this is a slave sim router.
        # Master and standalone routers never filter flight controls.
        if (self.router.get_router_type() == 'slave' and
                self.router.config.identity.type not in ('master', 'standalone')):
            if not self.sender.upstream and key in FLIGHT_CONTROL_INPUT_KEYWORDS:
                self.logger.debug("FLIGHT CONTROL INPUT: %s", key)
                flying = self.router.sharedinfo["pilot_flying_simulator"]
                self.logger.debug("pilot_flying_simulator is %s", flying)
                if flying == 'NO_CONTROL_LOCKS':
                    pass
                elif flying == 'ALL_CONTROL_LOCKS':
                    self.logger.debug(
                        "%s update dropped - all control locks in", key
                    )
                    return self.myreturn(
                        RulesAction.DROP,
                        RulesCode.KEYVALUE_FILTERED_INGRESS,
                        message=(
                            f"filtered flight control {key} as all control locks are in"
                        ),
                        extra_data={'resync_key': key},
                    )
                else:
                    if flying != self.router.config.identity.simulator and key == 'Qh388':
                        # Exception: let the non-flying sim arm the speedbrake
                        # (increase-only, capped at SPDBRK_ARM_VALUE) to help
                        # the pilot flying - see SPDBRK_ARM_VALUE's comment.
                        try:
                            current = int(self.router.cache.get_value(key))
                        except (RouterCacheException, ValueError, TypeError):
                            current = 0
                        try:
                            requested = int(value)
                        except ValueError:
                            requested = None
                        if requested is not None and current < requested <= SPDBRK_ARM_VALUE:
                            self.logger.info(
                                "Allowing SpdBrkLever arm from non-flying sim %s "
                                "(pilot flying: %s): %s -> %s",
                                self.router.config.identity.simulator, flying,
                                current, requested)
                        else:
                            self.logger.debug(
                                "SpdBrkLever update dropped - %s is pilot flying "
                                "(requested %s, current %s, not a valid arm move)",
                                flying, value, current)
                            return self.myreturn(
                                RulesAction.DROP,
                                RulesCode.KEYVALUE_FILTERED_INGRESS,
                                message=(
                                    f"filtered flight control {key} as we are not the " +
                                    f"flying sim {flying}"
                                ),
                                extra_data={'resync_key': key},
                            )
                    elif flying != self.router.config.identity.simulator:
                        # Someone else is pilot flying - filter flight controls.
                        self.logger.debug(
                            "%s update dropped - %s is pilot flying",
                            key, flying
                        )
                        return self.myreturn(
                            RulesAction.DROP,
                            RulesCode.KEYVALUE_FILTERED_INGRESS,
                            message=(
                                f"filtered flight control {key} as we are not the " +
                                f"flying sim {flying}"
                            ),
                            extra_data={'resync_key': key},
                        )

        # Qs357="Brakes"; Mode=ECON; Min=3; Max=9;
        # Qh397="ParkBrkLev"; Mode=ECON; Min=0; Max=1;
        if key == 'Qs357':
            # Parking brake release fix (opt-in via psx.parking_brake_fix config)
            if (self.router.config.psx.parking_brake_fix and
                    not self.sender.upstream and
                    self.router.get_router_type() == 'slave'):
                try:
                    qh397_stale = (self.router.cache.get_value('Qh397') == 1 and
                                   self.router.cache.get_age('Qh397') > 5.0)
                    (left, right) = value.split(';', 1)
                    brakes_near_max = int(left) > 990 and int(right) > 990
                except (RouterCacheException, ValueError, TypeError):
                    qh397_stale = False
                    brakes_near_max = False
                if qh397_stale and brakes_near_max:
                    # Brakes pressed to almost 100%, ensure release.
                    # Drop this message but RulesCode ensures we send
                    # Qs357=1000;1000 + Qh397=0
                    return self.myreturn(
                        RulesAction.DROP,
                        RulesCode.PARKING_BRAKE_FORCE_RELEASE,
                        message=(
                            f"Qs357 near max ({value}), forcing parking brake release"
                        )
                    )

        if not self.sender.upstream and key == 'Qs119':
            # Do not accept Qs119 from BACARS shortly after BACARS
            # connects. This prevents BACARS from printing some junk
            # (the partial ATIS) when started.
            if time.perf_counter() - self.sender.connected_at < 30.0:
                if 'BACARS' in self.sender.display_name or 'BA ACARS' in self.sender.display_name:
                    return self.myreturn(
                        RulesAction.DROP,
                        RulesCode.KEYVALUE_FILTERED_INGRESS,
                        message="filtered Qs119 from BACARS shortly after connection")

        if not self.sender.upstream and key == 'Qi198':
            # Filter elevation updates (usually from MSFS.Router) from
            # downstream if the runtime filter flag is set. We use SILENT
            # filtering since this will happen at 2Hz
            if self.router.filter_elevation:
                return self.myreturn(
                    RulesAction.DROP,
                    RulesCode.KEYVALUE_FILTERED_INGRESS_SILENT,
                    message="filtered Qi198 as filter_elevation is set")
            # If the value is unchanged, only send to
            # upstream. Sending unconditionally to upstream prevents
            # the master sim router from switching to PSX's elevation
            # source if it has not seen a Qi198 injection in 60
            # seconds. Not sending to downstream when the value is
            # unchanged means we behave more like a PSX main server.
            try:
                if self.router.cache.get_value('Qi198') == int(value):
                    self.router.cache.update('Qi198', value)
                    return self.myreturn(
                        RulesAction.UPSTREAM_ONLY,
                        RulesCode.KEYVALUE_FILTERED_INGRESS_SILENT,
                        message="Qi198 unchanged: forwarding upstream only")
            except (RouterCacheException, ValueError, TypeError):
                pass

        if not self.sender.upstream and key in TRAFFIC_KEYWORDS:
            # Only the traffic master sim may supply traffic. We use
            # SILENT filtering since vPilot keeps sending while filtered.
            reason = self.traffic_drop_reason()
            if key == 'Qs450':
                self.sender.traffic_times.append(time.perf_counter())
                if reason:
                    self.sender.traffic_dropped_times.append(time.perf_counter())
            if reason:
                return self.myreturn(
                    RulesAction.DROP,
                    RulesCode.KEYVALUE_FILTERED_INGRESS_SILENT,
                    message=f"filtered {key}: {reason}")

        # A real upstream's (or another frankenrouter relaying its own
        # upstream's) welcome message includes a "Lexicon" -- raw entries
        # like "Ls0(E)=CfgRego" -- which is authoritative for that
        # connection's exact PSX version. Reconcile it against our
        # Variables.txt-seeded definitions (adding unknown Q-codes,
        # warning and overwriting on any mismatch) before falling
        # through to the normal cache update below, which still caches
        # and forwards the raw entry exactly as before.
        if self.sender.upstream:
            self.router.variables.reconcile_lexicon_entry(key, value)

        # Snapshot the value we had cached for this key (if any) before
        # we overwrite it below - used both to detect a genuine Qi25
        # change and to recognize echoes of our own upstream "bang"
        # replies, further down.
        try:
            old_cached_value = self.router.cache.get_value(key)
        except RouterCacheException:
            old_cached_value = None

        # Store key-value in router cache
        try:
            self.router.cache.update(key, value)
        except RouterCacheTypeError as exc:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Wrong datatype in message, dropping it: {exc}")

        new_cached_value = self.router.cache.get_value(key)  # just stored above, cannot raise

        # PSX recomputes some ECON variables internally as a side
        # effect of another one changing - e.g Qh274 ("JettSelSystem")
        # when Qi25 ("CfgJettisonMlw") changes, to keep the jettison
        # selector position consistent across the two different switch
        # layouts - but does not always broadcast the recomputed value
        # onto the network (see
        # https://aerowinx.com/board/index.php/topic,7861.0.html).
        # Rather than hardcode PSX's undocumented remap table
        # ourselves (a previous attempt at exactly that for this same
        # jettison case had the wrong values baked in - see git
        # history), ask our own upstream to privately resend its
        # current, authoritative state via "bang" whenever we see Qi25
        # change, and let the dedup filter right below narrow that
        # private reply down to just whatever actually changed. This is
        # somewhat intrusive (see config.psx.jettison_resync_fix), so
        # it can be disabled in the config file.
        if (self.router.config.psx.jettison_resync_fix and
                self.sender.upstream and key == 'Qi25' and
                old_cached_value is not None and old_cached_value != new_cached_value):
            return self.myreturn(RulesAction.NORMAL, RulesCode.JETTISON_MLW_CHANGED)

        # If we recently asked our own upstream for a "bang" for the
        # reason above, this key=value is very likely part of that
        # private reply flooding back to us - and almost all of it
        # will just echo what we already had cached (we already had
        # this whole state; we only asked to double-check one
        # recomputed variable we suspected might be stale). Drop those
        # echoes silently so we don't flood our own clients with an
        # unsolicited full resync. Anything that actually differs -
        # the recomputed value we were after, or an unrelated real
        # change that happens to land in this same short window - is
        # still forwarded normally below, so nothing genuine is ever
        # lost.
        if (self.router.config.psx.jettison_resync_fix and
                self.sender.upstream and old_cached_value is not None):
            time_since_bang = time.perf_counter() - self.router.jettison_resync_sent_at
            if (time_since_bang <= UPSTREAM_RESYNC_BANG_WINDOW_S and
                    old_cached_value == new_cached_value):
                return self.myreturn(
                    RulesAction.DROP,
                    RulesCode.KEYVALUE_FILTERED_INGRESS_SILENT,
                    message=f"dropped unchanged {key} echoed by our own upstream resync bang")

        # The "nolong" keywords are only sent to clients that have
        # asked for them (using the "nolong" keyword)
        if key in self.router.variables.keywords_with_mode("NOLONG"):
            return self.myreturn(
                RulesAction.FILTER,
                RulesCode.KEYVALUE_FILTER_EGRESS,
                extra_data={'nolong': True})

        # START keywords that are not also ECON (e.g Qs493 and Qi208)
        # get special handling.

        # When such a variable arrives from downstream (a client),
        # it's either a legitimate one-off startup value or a live,
        # instructor-issued command (e.g. Qs122 from a repositioning
        # tool) - either way it must always reach upstream and every
        # other client normally, so it is never filtered here.

        # When such a variable arrives from upstream (the PSX Main
        # Server or master router), it's the response to the "start"
        # command we send when welcoming a newly-connected client
        # (see the client welcome handling above, which records that
        # moment in router.start_sent_at). Within 5s of that, this is
        # almost certainly that response and must stay private to the
        # new client - broadcasting it would make e.g. Qs122 instantly
        # reposition every other already-connected client. Outside
        # that window it's an unsolicited update (e.g. a real situ
        # load, or the instructor repositioning the aircraft via
        # Qs122) and must be forwarded to everyone normally.
        #
        # Note: we deliberately do not use router.last_load3 here. It
        # is only updated when a load3 message is itself routed
        # through this function (see handle_load3()), which never
        # happens for the "load3" the welcome flow sends directly to
        # the new client's stream - so it would never reflect an
        # in-progress welcome.
        if self.sender.upstream:
            if key in self.router.variables.keywords_with_mode('START'):
                if key not in self.router.variables.keywords_with_mode('ECON'):
                    time_since_start_sent = time.perf_counter() - self.router.start_sent_at
                    self.logger.debug(
                        "START variable %s from upstream, time since start sent is %.1fs",
                        key, time_since_start_sent
                    )
                    if time_since_start_sent <= START_PRIVATE_WINDOW_S:
                        return self.myreturn(
                            RulesAction.FILTER,
                            RulesCode.KEYVALUE_FILTER_EGRESS,
                            extra_data={'start': True, 'key': key})

        # This is not strictly a filter, but we snoop on the incoming
        # messages and if we see Qh400="ApDisc" from downstream with a
        # value of 1 (someone pushed the A/P disconnect button in this
        # sim), we take the same action as if someone had sent the MY
        # CONTROLS message, but still forward the message normally.
        if not self.sender.upstream and key == 'Qh400' and value == "1":
            if self.router.config.psx.filter_flight_controls_ap_disc:
                # Note to future self: since RulesCode.KEYVALUE_NORMAL
                # does nothing, we can omit using it here and instead
                # use RulesCode.FRDP_MY_CONTROLS. If that was not the
                # case or is changed in the future, we could modify
                # the RulesCode.KEYVALUE_NORMAL handling to accept an
                # optional parameter which contains a message to send
                # onto the network (e.g the MY_CONTROLS message)
                self.logger.info("A/P disconnect pressed, sending the MY_CONTROLS message")
                return self.myreturn(RulesAction.NORMAL, RulesCode.FRDP_MY_CONTROLS)

        # Weather-zone writes reach the clients that draw PSX's CBs elsewhere
        # (PSX.NET.Orchestration) late, as late as PSX's own radar shows them,
        # and a drifting zone's WxMode from frankenweather's feed instead (see
        # Frankenrouter.orchestration_*). Everyone else gets them now.
        zone_var = _WEATHER_ZONE_VAR_RE.match(self.router.variables.get_variable_name(key) or '')
        if zone_var and self.router.orchestration_egress_needed(int(zone_var.group(2))):
            return self.myreturn(
                RulesAction.FILTER, RulesCode.KEYVALUE_FILTER_EGRESS,
                extra_data={'orchestration_egress': {
                    'zone': int(zone_var.group(2)), 'wxmode': zone_var.group(1) == 'WxMode'}})

        #
        # Send normally
        #
        return self.myreturn(RulesAction.NORMAL, RulesCode.KEYVALUE_NORMAL)


class TestRules(unittest.TestCase):  # pylint: disable=too-many-public-methods
    """Basic test cases for the module."""

    class DummyVariables():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def is_psx_keyword(self, keyword):
            """Return true if keyword is a normal PSX network keyword."""
            if keyword[0] == 'X':
                return False
            return True

        def keywords_with_mode(self, mode):
            """Return fake lists of keywords."""
            if mode == "NOLONG":
                return ['Qi999']
            if mode == "START":
                return ['Qs997', 'Qs998']
            if mode == "ECON":
                return ['Qs997']
            return []

        def reconcile_lexicon_entry(self, _key, _name):
            """Fake Lexicon reconciliation: no-op, never matches."""
            return False

        def get_variable_name(self, keyword):
            """Return a fake variable name for a few keywords."""
            return {'Qs196': 'MfAcBus1', 'Qi130': 'MfEvent', 'Qs546': 'TkofRefNg',
                    'Qi190': 'MaweaBits', 'Qs499': 'WxSigmet', 'Qs329': 'Wx1',
                    'Qi272': 'CfgPin3Prog', 'Qs336': 'WxMode1'}.get(keyword, keyword)

    class DummyCache():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the cache."""
            self.cache = {}

        def update(self, keyword, value):
            """Fake cache update."""
            self.cache[keyword] = value

        def get_value(self, keyword):
            """Fake cache get_value."""
            if keyword not in self.cache:
                raise RouterCacheException(f"{keyword} not in dummy cache")
            return self.cache[keyword]

    class DummyConfigPsx():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the config."""
            self.filter_from_other_sim = []
            self.filter_to_other_sim = []
            self.jettison_resync_fix = True

    class DummyConfigIdentity():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the identity config."""
            self.simulator = 'MySim'
            self.type = 'slave'

    class DummyConfigFiltering():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the filtering config."""
            self.ground_handling_forward_names = [
                'PSX.NET EFB For Windows', 'PSX.NET.Orchestration', 'BA ACARS Simulation']
            self.ptt_ground_handling_translation = True
            self.master_drop_check_switches = True
            self.slave_drop_printer_push = True

    class DummyConfig():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the config."""
            self.psx = TestRules.DummyConfigPsx()
            self.identity = TestRules.DummyConfigIdentity()
            self.filtering = TestRules.DummyConfigFiltering()
            self.access = []
            self.quarantine = None

    class DummyFrankenrouter():  # pylint: disable=too-few-public-methods,too-many-instance-attributes
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the instance."""
            self.upstream = None
            self.clients = {}
            self.variables = TestRules.DummyVariables()
            self.cache = TestRules.DummyCache()
            self.last_load1 = 0.0
            self.last_load3 = 0.0
            self.start_sent_at = 0.0
            self.jettison_resync_sent_at = 0.0
            self.frdp_version = 1
            self.config = TestRules.DummyConfig()
            self.session_password = None
            self.observer_session_password = None
            self.observer_mode = False
            self.sharedinfo = {"pilot_flying_simulator": "NO_CONTROL_LOCKS"}
            self.filter_traffic = False
            self.load_marker_pause_enabled = False
            self.load_marker_pause_delay = 0.1
            self.orchestration_needed = False   # what orchestration_egress_needed() says

        def is_upstream_connected(self):
            """Return dummy value."""
            return False

        def orchestration_egress_needed(self, _zone):
            """Return whether weather-zone writes go to Orchestration clients late."""
            return self.orchestration_needed

        def is_sharedinfo_authority(self):
            """Mirror the real router's own is_sharedinfo_authority method."""
            return self.config.identity.type in ('master', 'standalone')

        def get_router_type(self):
            """Return dummy value."""
            return "unknown"

        def variable_stats_add(self, *args):
            """Add dummy stats."""

        def connection_state_changed(self, *args):
            """Implement Dummy display."""

    class DummyConnection():  # pylint: disable=too-few-public-methods,too-many-instance-attributes
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the connection."""
            self.frdp_ping_sent = 0.0
            self.frdp_ping_rtts = []
            self.frdp_ping_request_id = None
            self.uuid = None
            self.simulator_name = 'UnknownSim'
            self.router_name = 'UnknownRouter'
            self.display_name = 'UnknownDisplay'
            self.display_name_source = 'UnknownSource'
            self.client_provided_id = None
            self.client_provided_display_name = None
            self.is_frankenrouter = False
            self.quarantine_restarted_at = 0.0
            self.quarantine_restores = {}
            self.first_welcome_done_at = None
            self.looks_like_psx_main = False
            self.traffic_times = []
            self.traffic_dropped_times = []
            self.frdp_subscribed = False
            self.auth_nonce = None
            self.upstream = False
            self.peername = None
            # Default to "long connected" so existing tests are unaffected by
            # the post-connect quarantine unless they opt in.
            self.connected_at = time.perf_counter() - 3600

    class DummyClientConnection(DummyConnection):  # pylint: disable=too-few-public-methods,too-many-instance-attributes
        """Implement small parts of the router for unit testing."""

        def __init__(self, peername):
            """Initialize the connection."""
            super().__init__()
            self.is_upstream = False
            self.access_level = 'full'
            self.nolong = False
            self.notify_keywords = None
            self.demands = set()
            self.peername = peername
            self.last_start_relayed_at = 0.0

        def can_write(self):
            """Check if this client is allowed to write."""
            if self.access_level == 'full':
                return True
            return False

        def has_access(self):
            """Return true if client has access."""
            if self.access_level != NOACCESS_ACCESS_LEVEL:
                return True
            return False

        def update_access_level(self, client_password=None):
            """Test access level change."""
            if client_password == "mypassword":
                self.access_level = 'full'
            else:
                self.access_level = NOACCESS_ACCESS_LEVEL

    class DummyUpstreamConnection(DummyConnection):  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the connection."""
            super().__init__()
            self.upstream = True

    def test_invalid_message(self):
        """Drop invalid messages."""
        router = self.DummyFrankenrouter()
        router.upstream = self.DummyUpstreamConnection()
        rules = Rules(router)

        (action, code, *_) = rules.route("Qi17=42\nQs123=foo", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

    def test_frdp_upstream(self):
        """Test FRDP messages from upstream."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        # PING from upstream
        router.upstream.frdp_ping_sent = time.perf_counter()
        (action, code, _, extra_data) = rules.route(
            "addon=FRANKENROUTER:1:PING:54321", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_PING)
        self.assertTrue('reply' in extra_data)

        # PONG from upstream
        router.upstream.frdp_ping_request_id = "54321"
        (action, code, _, extra_data) = rules.route(
            "addon=FRANKENROUTER:1:PONG:54321", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_PONG)
        self.assertTrue('frdp_rtt' in extra_data)

        # IDENT from upstream
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:IDENT:OtherSim:OtherRouter:fakeuuid", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_IDENT)
        self.assertEqual(router.upstream.simulator_name, 'OtherSim')
        self.assertEqual(router.upstream.router_name, 'OtherRouter')
        self.assertEqual(router.upstream.client_provided_id, 'OtherSim')
        self.assertEqual(router.upstream.display_name, 'OtherSim/OtherRouter')
        self.assertEqual(router.upstream.uuid, 'fakeuuid')
        self.assertEqual(router.upstream.display_name_source, 'FRDP IDENT')

        # CLIENTINFO from upstream (not allowed)
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:CLIENTINFO:{}", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

        # AUTH from upstream (not allowed)
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:AUTH:mypassword", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

    def test_frdp_client(self):  # pylint: disable=too-many-statements
        """Test FRDP messages from client."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]
        testpeer.access_level = 'full'
        testpeer.display_name = "Foobar"

        # PING from client
        testpeer.frdp_ping_sent = time.perf_counter()
        (action, code, _, extra_data) = rules.route(
            "addon=FRANKENROUTER:1:PING:12345", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_PING)
        self.assertTrue('reply' in extra_data)

        # PONG from client
        testpeer.frdp_ping_request_id = "12345"
        (action, code, _, extra_data) = rules.route(
            "addon=FRANKENROUTER:1:PONG:12345", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_PONG)
        self.assertTrue('frdp_rtt' in extra_data)

        # PONG from client with invalid ID
        testpeer.frdp_ping_request_id = "123456789"
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:PONG:12345", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

        # IDENT from client
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:IDENT:SomeSim:SomeRouter:fakeuuid", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_IDENT)
        self.assertEqual(testpeer.simulator_name, 'SomeSim')
        self.assertEqual(testpeer.router_name, 'SomeRouter')
        self.assertEqual(testpeer.client_provided_id, 'SomeSim')
        self.assertEqual(testpeer.display_name, 'SomeSim/SomeRouter')
        self.assertEqual(testpeer.display_name_source, 'FRDP IDENT')

        # CLIENTINFO from client
        json_payload = json.dumps({
            "laddr": "127.0.0.1",
            "lport": 12345,
            "client_provided_id": "PSXSOUNDS",
            "display_name": "PSX Sounds"
        })
        (action, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:CLIENTINFO:{json_payload}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_CLIENTINFO)
        self.assertEqual(testpeer.client_provided_id, 'PSXSOUNDS')
        self.assertEqual(testpeer.display_name, 'PSX Sounds')
        self.assertEqual(testpeer.client_provided_display_name, 'PSX Sounds')
        self.assertEqual(testpeer.display_name_source, 'FRDP CLIENTINFO')

        # CLIENTINFO must not overwrite a name= self-identification
        testpeer.display_name = 'Self Identified'
        testpeer.display_name_source = 'name message'
        testpeer.client_provided_id = 'SELFID'
        json_payload2 = json.dumps({
            "laddr": "127.0.0.1",
            "lport": 12345,
            "client_provided_id": "IDENT",
            "display_name": "Ident Provided Name"
        })
        (action, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:CLIENTINFO:{json_payload2}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_CLIENTINFO)
        self.assertEqual(testpeer.client_provided_id, 'SELFID')
        self.assertEqual(testpeer.display_name, 'Self Identified')
        self.assertEqual(testpeer.display_name_source, 'name message')

        # CLIENTINFO must overwrite an access config label (it is more specific)
        testpeer.display_name = 'Access Config Label'
        testpeer.display_name_source = 'access config'
        testpeer.client_provided_id = None
        json_payload3 = json.dumps({
            "laddr": "127.0.0.1",
            "lport": 12345,
            "client_provided_id": "GATEFIND",
            "display_name": "PSX.NET GateFinder"
        })
        (action, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:CLIENTINFO:{json_payload3}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_CLIENTINFO)
        self.assertEqual(testpeer.client_provided_id, 'GATEFIND')
        self.assertEqual(testpeer.display_name, 'PSX.NET GateFinder')
        self.assertEqual(testpeer.display_name_source, 'FRDP CLIENTINFO')

    def test_frdp_disconnect_sim(self):
        """Test the FRDP DISCONNECT_SIM message, including who it applies to."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]

        # We are a slave router for 'MySim' (DummyConfigIdentity defaults),
        # and are targeted by name -> this must apply to us.
        payload = json.dumps({
            "from_sim": "OtherSim", "target_sim": "MySim", "reason": "Testing"})
        (action, code, _, extra_data) = rules.route(
            f"addon=FRANKENROUTER:1:DISCONNECT_SIM:{payload}", testpeer)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.FRDP_DISCONNECT_SIM)
        self.assertTrue(extra_data['exclude_non_frankenrouter'])
        self.assertEqual(extra_data['disconnect_sim'],
                         {"from_sim": "OtherSim", "target_sim": "MySim", "reason": "Testing"})

        # Targeting a different sim -> still forwarded, but does not apply to us
        payload = json.dumps({
            "from_sim": "OtherSim", "target_sim": "SomeOtherSim", "reason": "Testing"})
        (action, code, _, extra_data) = rules.route(
            f"addon=FRANKENROUTER:1:DISCONNECT_SIM:{payload}", testpeer)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.FRDP_DISCONNECT_SIM)
        self.assertNotIn('disconnect_sim', extra_data)

        # Targeting us, but we are not a slave -> does not apply to us
        router.config.identity.type = 'master'
        payload = json.dumps({
            "from_sim": "OtherSim", "target_sim": "MySim", "reason": "Testing"})
        (action, code, _, extra_data) = rules.route(
            f"addon=FRANKENROUTER:1:DISCONNECT_SIM:{payload}", testpeer)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertNotIn('disconnect_sim', extra_data)
        router.config.identity.type = 'slave'

        # Targeted, slave, but our upstream is in the *same* sim (we are
        # an internal router, not the edge router) -> does not apply to us
        router.upstream.simulator_name = 'MySim'
        payload = json.dumps({
            "from_sim": "OtherSim", "target_sim": "MySim", "reason": "Testing"})
        (action, code, _, extra_data) = rules.route(
            f"addon=FRANKENROUTER:1:DISCONNECT_SIM:{payload}", testpeer)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertNotIn('disconnect_sim', extra_data)
        router.upstream.simulator_name = 'UnknownSim'

        # Targeted, slave, but we have no upstream at all -> does not apply
        router.upstream = None
        payload = json.dumps({
            "from_sim": "OtherSim", "target_sim": "MySim", "reason": "Testing"})
        (action, code, _, extra_data) = rules.route(
            f"addon=FRANKENROUTER:1:DISCONNECT_SIM:{payload}", testpeer)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertNotIn('disconnect_sim', extra_data)
        router.upstream = self.DummyUpstreamConnection()

        # Missing required field -> dropped, not forwarded
        payload = json.dumps({"from_sim": "OtherSim", "target_sim": "MySim"})
        (action, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:DISCONNECT_SIM:{payload}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_DISCONNECT_SIM)

        # Invalid JSON -> dropped
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:DISCONNECT_SIM:not json", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

    def test_frdp_subscribe(self):
        """Test the FRDP SUBSCRIBE opt-in, and that it stays short of full FRDP peer status."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]
        self.assertFalse(testpeer.frdp_subscribed)
        self.assertFalse(testpeer.is_frankenrouter)

        (action, code, *_) = rules.route("addon=FRANKENROUTER:1:SUBSCRIBE", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_SUBSCRIBE)
        self.assertTrue(testpeer.frdp_subscribed)
        # SUBSCRIBE must not also grant full FRDP peer status - that
        # would defeat the point (FRDP-ping traffic, router-to-router-only
        # behaviors like PTT/audio cross-sim filtering, etc.).
        self.assertFalse(testpeer.is_frankenrouter)

    def test_frdp_sharedinfo_load_marker_pause(self):
        """A slave applies load_marker_pause fields from an incoming SHAREDINFO."""
        router = self.DummyFrankenrouter()
        router.config.identity.type = 'slave'
        rules = Rules(router)
        sender = self.DummyUpstreamConnection()
        sender.is_frankenrouter = True

        payload = json.dumps({
            'master_uuid': 'some-uuid',
            'load_marker_pause_enabled': True,
            'load_marker_pause_delay': 0.25,
        })
        (_, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:SHAREDINFO:{payload}", sender)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTER_EGRESS)
        self.assertTrue(router.load_marker_pause_enabled)
        self.assertEqual(router.load_marker_pause_delay, 0.25)

        # A SHAREDINFO message omitting the fields (e.g. from an older
        # master during a rolling upgrade) leaves our current value alone.
        payload = json.dumps({'master_uuid': 'some-uuid'})
        (_, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:SHAREDINFO:{payload}", sender)
        self.assertTrue(router.load_marker_pause_enabled)
        self.assertEqual(router.load_marker_pause_delay, 0.25)

    def test_frdp_master_bang(self):
        """Test the FRDP MASTER_BANG message, who it applies to."""
        for identity_type, expected_action in (
                ('master', RulesAction.DROP),
                ('standalone', RulesAction.DROP),
                ('slave', RulesAction.UPSTREAM_ONLY)):
            router = self.DummyFrankenrouter()
            router.config.identity.type = identity_type
            rules = Rules(router)
            router.clients = {
                ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            }
            sender = router.clients[('127.0.0.1', 12345)]

            (action, code, *_) = rules.route("addon=FRANKENROUTER:1:MASTER_BANG", sender)
            self.assertEqual(action, expected_action, identity_type)
            self.assertEqual(code, RulesCode.FRDP_MASTER_BANG, identity_type)

    def test_frdp_crew_alertness(self):
        """FRDP CREW_ALERTNESS: the master acts on it, a slave forwards it."""
        for identity_type, expected_action in (
                ('master', RulesAction.DROP),
                ('standalone', RulesAction.DROP),
                ('slave', RulesAction.UPSTREAM_ONLY)):
            router = self.DummyFrankenrouter()
            router.config.identity.type = identity_type
            rules = Rules(router)
            router.clients = {
                ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            }
            sender = router.clients[('127.0.0.1', 12345)]
            (action, code, _, extra) = rules.route(
                "addon=FRANKENROUTER:1:CREW_ALERTNESS:off", sender)
            self.assertEqual((action, code), (expected_action, RulesCode.FRDP_CREW_ALERTNESS),
                             identity_type)
            if identity_type == 'master':
                self.assertEqual(extra, {'command': 'off'})
                (action, code, _, extra) = rules.route(
                    "addon=FRANKENROUTER:1:CREW_ALERTNESS:test", sender)
                self.assertEqual(extra, {'command': 'test'})
            (action, code, *_) = rules.route("addon=FRANKENROUTER:1:CREW_ALERTNESS:maybe", sender)
            self.assertEqual(code, RulesCode.MESSAGE_INVALID)

    def test_frdp_client_auth(self):  # pylint: disable=too-many-statements
        """Test FRDP messages from client."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        testpeer.access_level = 'full'
        testpeer.display_name = "Foobar"

        router.session_password = "mypassword"
        testpeer.auth_nonce = "testnonce"
        good_hmac = hmac.new(
            b"mypassword", b"testnonce", hashlib.sha256).hexdigest()
        bad_hmac = hmac.new(
            b"badpassword", b"testnonce", hashlib.sha256).hexdigest()

        # AUTH success from client
        testpeer.access_level = NOACCESS_ACCESS_LEVEL
        (action, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:AUTH:hmac-sha256:{good_hmac}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_AUTH_OK)
        self.assertEqual(testpeer.access_level, 'full')

        # AUTH failure from client (wrong password)
        testpeer.access_level = NOACCESS_ACCESS_LEVEL
        (action, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:AUTH:hmac-sha256:{bad_hmac}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_AUTH_FAIL)
        self.assertEqual(testpeer.access_level, NOACCESS_ACCESS_LEVEL)

        # AUTH failure from client (cleartext no longer accepted)
        testpeer.access_level = NOACCESS_ACCESS_LEVEL
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:AUTH:mypassword", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_AUTH_FAIL)
        self.assertEqual(testpeer.access_level, NOACCESS_ACCESS_LEVEL)

        # AUTH already authenticated
        testpeer.access_level = 'full'
        (action, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:AUTH:hmac-sha256:{good_hmac}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_AUTH_ALREADY_HAS_ACCESS)
        self.assertEqual(testpeer.access_level, 'full')

    def test_name(self):
        """Test name messages."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        # unknown addon sending name=
        (action, code, *_) = rules.route("name=somename:or:other", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NAME_LEARNED)
        self.assertEqual(testpeer.display_name, 'or:other')
        self.assertEqual(testpeer.display_name_source, 'name message')

        # known addon sending name=
        (action, code, *_) = rules.route("name=BACARS:BA ACARS Simulation", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NAME_LEARNED)
        self.assertEqual(testpeer.display_name, 'BA ACARS Simulation')
        self.assertEqual(testpeer.display_name_source, 'name message')

    def test_demand(self):
        """Test demand messages."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        # demand from client
        (action, code, *_) = rules.route("demand=Qs325", testpeer)
        self.assertEqual(action, RulesAction.UPSTREAM_ONLY)
        self.assertEqual(code, RulesCode.DEMAND)
        self.assertTrue('Qs325' in testpeer.demands)

        # demand from upstream
        (action, code, *_) = rules.route("demand=Qs325", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

    def test_again(self):
        """Test again messages."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        # again from client
        (action, code, *_) = rules.route("again", testpeer)
        self.assertEqual(action, RulesAction.UPSTREAM_ONLY)
        self.assertEqual(code, RulesCode.AGAIN)

        # again from upstream
        (action, code, *_) = rules.route("again", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

    def test_start(self):
        """Test start messages."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        # start from client
        self.assertEqual(testpeer.last_start_relayed_at, 0.0)
        self.assertEqual(router.start_sent_at, 0.0)
        (action, code, *_) = rules.route("start", testpeer)
        self.assertEqual(action, RulesAction.UPSTREAM_ONLY)
        self.assertEqual(code, RulesCode.START)
        # Relaying "start" upstream records when we did so, so
        # client_broadcast() can later tell this connection currently
        # has a pending welcome (see START_PRIVATE_WINDOW_S).
        self.assertGreater(testpeer.last_start_relayed_at, 0.0)
        # It also marks this router itself as expecting a private START
        # response on its own upstream connection, exactly as if it had
        # initiated the request itself via client_add_to_network() -
        # this matters when we're only relaying someone else's request.
        self.assertGreater(router.start_sent_at, 0.0)

        # start from upstream
        (action, code, *_) = rules.route("start", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

    def test_pbskaq(self):
        """Test pleaseBeSoKindAndQuit messages."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        # pbskaq from client
        (action, code, *_) = rules.route("pleaseBeSoKindAndQuit", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.PBSKAQ)

        # pbskaq from upstream
        (action, code, *_) = rules.route("pleaseBeSoKindAndQuit", router.upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.PBSKAQ)

    def test_load(self):
        """Test loadX messages."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        # load1 from client
        (action, code, *_) = rules.route("load1", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.LOAD1)
        self.assertTrue(router.last_load1 > 0.0)

        # load2 from client
        (action, code, *_) = rules.route("load2", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.LOAD2)

        # load3 from client
        (action, code, *_) = rules.route("load3", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.LOAD3)

        # load1 from upstream
        (action, code, *_) = rules.route("load1", router.upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.LOAD1)
        self.assertTrue(router.last_load1 > 0.0)

        # load2 from upstream
        (action, code, *_) = rules.route("load2", router.upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.LOAD2)

        # load3 from upstream
        (action, code, *_) = rules.route("load3", router.upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.LOAD3)

    def test_exit(self):
        """Test exit messages."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        # exit from client
        (action, code, *_) = rules.route("exit", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.EXIT)

        # exit from upstream
        (action, code, *_) = rules.route("exit", router.upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.EXIT)

    def test_nolong(self):
        """Test nolong messages."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]

        testpeer.nolong = False
        (action, code, *_) = rules.route("nolong", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NOLONG)
        self.assertTrue(testpeer.nolong)

        (action, code, *_) = rules.route("nolong", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NOLONG)
        self.assertFalse(testpeer.nolong)

    def test_notify(self):
        """Test the notify (Simstack-Switch-style variable subscription) keyword."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]

        self.assertIsNone(testpeer.notify_keywords)
        (action, code, *_) = rules.route("notify=Qs323;Qs324;", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NOTIFY)
        self.assertEqual(testpeer.notify_keywords, frozenset({'Qs323', 'Qs324'}))

        # Not forwarded upstream -- purely a local connection preference.
        (action, code, *_) = rules.route("notify=Qs1;", self.DummyUpstreamConnection())
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.MESSAGE_INVALID)

        # An empty notify clears the filter back to "send everything".
        (action, code, *_) = rules.route("notify=", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NOTIFY)
        self.assertIsNone(testpeer.notify_keywords)

    def test_ingress_filtered(self):
        """Test ingress filter."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]
        testpeer.display_name = "BACARS"

        # Qs119 from BACARS within 15s of BACARS connecting
        testpeer.connected_at = time.perf_counter()
        (action, code, *_) = rules.route("Qs119=junk printout", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTERED_INGRESS)

        # Qs119 from BACARS more than 15s after connecting
        testpeer.connected_at = time.perf_counter() - 31.0
        (action, code, *_) = rules.route("Qs119=junk printout", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

    def test_jettison_resync(self):
        """Test the Qi25/Qh274 upstream resync-via-bang mechanism."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        upstream = router.upstream

        # First sighting of Qi25 (nothing cached yet): just a normal
        # update, no resync triggered.
        (action, code, *_) = rules.route("Qi25=0", upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)
        self.assertEqual(router.jettison_resync_sent_at, 0.0)

        # Qi25 actually changing, from upstream, triggers the resync:
        # forwarded normally (like any other ECON update) but with a
        # distinct code so frankenrouter.py knows to bang upstream.
        (action, code, *_) = rules.route("Qi25=1", upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.JETTISON_MLW_CHANGED)

        # Qi25 arriving from a client must never trigger this - PSX's
        # recompute only happens once the change reaches the real main
        # server, so banging our own upstream immediately would just
        # race ahead of it and get a stale answer.
        testpeer = router.clients[('127.0.0.1', 12345)]
        (action, code, *_) = rules.route("Qi25=0", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

        # Simulate frankenrouter.py having just sent the resync bang.
        router.jettison_resync_sent_at = time.perf_counter()

        # An unrelated keyword, unchanged, arriving from upstream
        # within the resync window: this is almost certainly part of
        # the private bang reply re-confirming state we already have,
        # so it must be dropped rather than flooded out to our clients.
        router.cache.update('Qh123', '42')
        (action, code, *_) = rules.route("Qh123=42", upstream)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTERED_INGRESS_SILENT)

        # The actually-corrected value (Qh274) arriving in that same
        # reply must still get through normally, since it differs from
        # what we had cached.
        router.cache.update('Qh274', '0')
        (action, code, *_) = rules.route("Qh274=2", upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

        # An unrelated but genuinely different value landing in this
        # same short window must also still get through normally - the
        # dedup only ever drops exact echoes, never real changes.
        router.cache.update('Qh456', '1')
        (action, code, *_) = rules.route("Qh456=2", upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

        # Once the resync window has elapsed, an unchanged value from
        # upstream is forwarded normally again - PSX main servers and
        # other frankenrouters do sometimes legitimately re-announce
        # unchanged values (e.g. momentary DELTA-mode switches), and we
        # must not suppress that outside the narrow window.
        router.jettison_resync_sent_at = time.perf_counter() - 10.0
        router.cache.update('Qh789', '5')
        (action, code, *_) = rules.route("Qh789=5", upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

        # With jettison_resync_fix disabled: Qi25 changing must not
        # trigger a bang, and an unchanged echo must never be dropped,
        # even right after what would otherwise be a resync window.
        router.config.psx.jettison_resync_fix = False
        router.jettison_resync_sent_at = time.perf_counter()
        (action, code, *_) = rules.route("Qi25=1", upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)
        router.cache.update('Qh999', '7')
        (action, code, *_) = rules.route("Qh999=7", upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

    def test_egress_filter(self):
        """Test egress filter."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        testpeer = router.clients[('127.0.0.1', 12345)]
        upstream = router.upstream

        # Qs997 is START and ECON, should be handled normally
        # regardless of sender or start_sent_at timing.
        testpeer.display_name = "BACARS"
        (action, code, _, extra_data) = rules.route("Qs997=START and ECON", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)
        self.assertIsNone(extra_data)

        # Qs998 is only START. From a downstream client (e.g. a live
        # instructor reposition command such as Qs122), it must
        # always be forwarded normally, regardless of start_sent_at
        # timing.
        router.start_sent_at = 0.0
        testpeer.display_name = "BACARS"
        (action, code, _, extra_data) = rules.route("Qs998=START only", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

        # From upstream, within 5s of us sending "start" upstream
        # (i.e. this is the response to a newly-connected client's
        # own welcome), it must be filtered so only that client sees
        # it.
        router.start_sent_at = time.perf_counter()
        (action, code, _, extra_data) = rules.route("Qs998=START only", upstream)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTER_EGRESS)
        self.assertTrue('start' in extra_data)
        self.assertTrue('key' in extra_data)

        # From upstream, outside the 5s start_sent_at window (e.g. an
        # unsolicited situ load or a live instructor reposition
        # relayed from another router), it must be forwarded
        # normally to everyone.
        router.start_sent_at = time.perf_counter() - 10.0
        (action, code, _, extra_data) = rules.route("Qs998=START only", upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

        # Qs122 is only START, but is exempt from the custom
        # filtering since it doubles as a live, instructor-issued
        # reposition command that must always reach every client.
        testpeer.display_name = "BACARS"
        (action, code, _, extra_data) = rules.route("Qs122=reposition", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)
        self.assertIsNone(extra_data)

    def test_orchestration_wxmodes(self):
        """FRANKENWEATHER:WXMODES is parsed for the Orchestration feed; bad ones only forwarded."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        router.upstream = self.DummyUpstreamConnection()
        mode = "0.82;-0.43;320;12;11;X90101p"
        (action, code, _, extra_data) = rules.route(
            'addon=FRANKENWEATHER:WXMODES:{"delay_s": 10, "zones": {"3": "%s"}}' % mode,
            router.upstream)
        self.assertEqual((action, code), (RulesAction.FILTER, RulesCode.FRANKENWEATHER_FILTERED))
        self.assertEqual(extra_data,
                         {'orchestration_wxmodes': {'delay_s': 10.0, 'zones': {3: mode}}})
        for bad in ('{"delay_s": 999, "zones": {}}',
                    '{"delay_s": 5, "zones": {"8": "%s"}}' % mode,
                    '{"delay_s": 5, "zones": {"1": "no;fields"}}', 'not json'):
            (_, _, _, extra_data) = rules.route(f'addon=FRANKENWEATHER:WXMODES:{bad}',
                                                router.upstream)
            self.assertEqual(extra_data, {'frankenrouters_only': True}, bad)

    def test_orchestration_egress(self):
        """Weather-zone writes get the Orchestration egress filter only when it's needed."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        router.upstream = self.DummyUpstreamConnection()
        (action, code, _, _) = rules.route("Qs329=0;1;2", router.upstream)
        self.assertEqual((action, code), (RulesAction.NORMAL, RulesCode.KEYVALUE_NORMAL))
        router.orchestration_needed = True
        (action, code, _, extra_data) = rules.route("Qs336=0.8;-0.4;320;0;11;X90101p",
                                                    router.upstream)
        self.assertEqual((action, code), (RulesAction.FILTER, RulesCode.KEYVALUE_FILTER_EGRESS))
        self.assertEqual(extra_data, {'orchestration_egress': {'zone': 1, 'wxmode': True}})
        (_, _, _, extra_data) = rules.route("Qs329=0;1;2", router.upstream)
        self.assertEqual(extra_data, {'orchestration_egress': {'zone': 1, 'wxmode': False}})
        (action, _, _, _) = rules.route("Qh33=4", router.upstream)   # not a weather-zone variable
        self.assertEqual(action, RulesAction.NORMAL)

    def test_addon_frankenweather_filter(self):
        """FRANKENWEATHER addon messages must be filtered to frankenrouters only."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]

        # From a downstream client, with write access.
        (action, code, _, extra_data) = rules.route(
            "addon=FRANKENWEATHER:STATE:someuuid:{}", testpeer)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.FRANKENWEATHER_FILTERED)
        self.assertEqual(extra_data, {'frankenweather_forward': 'WEATHER'})

        # Same filtering applies regardless of sender direction or message
        # subtype (STATE/TURBSTATE/WINDSTATE all use the same addon name).
        (action, code, _, extra_data) = rules.route(
            "addon=FRANKENWEATHER:WINDSTATE:someuuid:payload", router.upstream)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.FRANKENWEATHER_FILTERED)
        self.assertEqual(extra_data, {'frankenweather_forward': 'WEATHER'})

        # SIGMETJSON only goes to frankenrouters, from either direction.
        for sender in (testpeer, router.upstream):
            (action, code, _, extra_data) = rules.route(
                "addon=FRANKENWEATHER:SIGMETJSON:someuuid:payload", sender)
            self.assertEqual(action, RulesAction.FILTER)
            self.assertEqual(code, RulesCode.FRANKENWEATHER_FILTERED)
            self.assertEqual(extra_data, {'frankenrouters_only': True})

        # A client without write access must still be dropped, same as any
        # other addon message.
        testpeer.access_level = NOACCESS_ACCESS_LEVEL
        (action, code, _, extra_data) = rules.route(
            "addon=FRANKENWEATHER:SIGMETJSON:someuuid:payload", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NOWRITE)
        (action, code, _, extra_data) = rules.route(
            "addon=FRANKENWEATHER:STATE:someuuid:{}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NOWRITE)

    def test_addon_ground_handling_filter(self):
        """GROUND.HANDLING addon messages must be filtered per [filtering] config."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]

        # GROUND.HANDLING doesn't use the normal NAME:payload colon
        # convention, so the whole rest-of-line is matched by prefix.
        line = "addon=GROUND.HANDLING;;;;V1|STATUS|runtime|loading|0|heartbeat"
        (action, code, _, extra_data) = rules.route(line, testpeer)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.GROUND_HANDLING_FILTERED)
        self.assertEqual(
            extra_data,
            {'ground_handling_forward': [
                'PSX.NET EFB For Windows', 'PSX.NET.Orchestration', 'BA ACARS Simulation']})

        # Same filtering applies regardless of sender direction.
        (action, code, _, extra_data) = rules.route(line, router.upstream)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.GROUND_HANDLING_FILTERED)

        # The configured allowlist (from [filtering]) is passed through as-is.
        router.config.filtering.ground_handling_forward_names = ['Some Client']
        (action, code, _, extra_data) = rules.route(line, testpeer)
        self.assertEqual(extra_data, {'ground_handling_forward': ['Some Client']})

        # A client without write access must still be dropped, same as any
        # other addon message.
        testpeer.access_level = NOACCESS_ACCESS_LEVEL
        (action, code, _, extra_data) = rules.route(line, testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NOWRITE)

    def test_addon_psxnetvatsim_known(self):
        """PSXNETVATSIM: a known addon, forwarded locally, dropped between sims."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]
        for line in ("addon=PSXNETVATSIM:SELECT_ACP:LEFT", "addon=PSXNETVATSIM:SELECT_ACP:RIGHT"):
            testpeer.is_frankenrouter = False
            (action, code, *_) = rules.route(line, testpeer)
            self.assertEqual(action, RulesAction.NORMAL, msg=line)
            self.assertEqual(code, RulesCode.ADDON_FORWARDED_KNOWN, msg=line)

            testpeer.is_frankenrouter = True
            testpeer.simulator_name = 'OtherSim'
            (action, code, *_) = rules.route(line, testpeer)
            self.assertEqual(action, RulesAction.DROP, msg=line)
            self.assertEqual(code, RulesCode.PSXNETVATSIM, msg=line)

    def test_addon_geovr_filter(self):
        """addon=GEOVR.PSX.* must be dropped from other sims only."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]
        lines = (
            "addon=GEOVR.PSX.CONTROL;;;;1|state|214912d611174bafb483799dbffc7eb2|"
            "5453e4d4967b449e888725b60cf7ff40|89ca91869db74025aba04ef00cddc9bd|"
            "flightdeck|connected|GTI577|",
            "addon=GEOVR.PSX.STATUS;;;;1|daad49988b454407bfeddecdfd99ff01|1|1|Disconnected|"
            "0:0:0,1:0:0,2:0:0,3:0:0,4:0:0",
            "addon=GEOVR.PSX.STATUS.REQUEST;;;;1",
        )
        for line in lines:
            # From a regular (non-frankenrouter) client: forwarded
            testpeer.is_frankenrouter = False
            (action, *_) = rules.route(line, testpeer)
            self.assertEqual(action, RulesAction.NORMAL, msg=line)

            # From a frankenrouter on the same sim: forwarded
            testpeer.is_frankenrouter = True
            testpeer.simulator_name = router.config.identity.simulator
            (action, *_) = rules.route(line, testpeer)
            self.assertEqual(action, RulesAction.NORMAL, msg=line)

            # From a frankenrouter on another sim: dropped
            testpeer.simulator_name = 'OtherSim'
            (action, code, *_) = rules.route(line, testpeer)
            self.assertEqual(action, RulesAction.DROP, msg=line)
            self.assertEqual(code, RulesCode.PSXNETVATSIM, msg=line)

    def test_route(self):
        """Test routing."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
            ('127.0.0.1', 23456): self.DummyClientConnection(('127.0.0.1', 23456)),
        }

        # Basic key-value from client
        testpeer = router.clients[('127.0.0.1', 12345)]
        (action, code, *_) = rules.route("Qi17=42", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

    def test_ptt_filter(self):
        """Test PTT/audio variable filtering between sims."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]

        # PTT from a regular (non-frankenrouter) client: always pass
        testpeer.is_frankenrouter = False
        (action, code, *_) = rules.route("Qh82=1", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

        # PTT from a frankenrouter on the same sim: pass
        testpeer.is_frankenrouter = True
        testpeer.simulator_name = router.config.identity.simulator
        (action, code, *_) = rules.route("Qh93=1", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)

        # PTT from a frankenrouter on a different sim: drop with RulesCode.PTT
        testpeer.simulator_name = 'OtherSim'
        for key in ('Qh82', 'Qh93'):
            (action, code, *_) = rules.route(f"{key}=1", testpeer)
            self.assertEqual(action, RulesAction.DROP, msg=f"{key} should be dropped")
            self.assertEqual(code, RulesCode.PTT, msg=f"{key} should use PTT code")

        # Audio panel switches (Qh410/411/412): only drop for values "26" or "-1"
        for key in ('Qh410', 'Qh411', 'Qh412'):
            for val in ('26', '-1'):
                (action, code, *_) = rules.route(f"{key}={val}", testpeer)
                self.assertEqual(action, RulesAction.DROP, msg=f"{key}={val} should be dropped")
                self.assertEqual(code, RulesCode.PTT, msg=f"{key}={val} should use PTT code")
            (action, code, *_) = rules.route(f"{key}=1", testpeer)
            self.assertEqual(action, RulesAction.NORMAL, msg=f"{key}=1 should pass")

    def test_flight_control_speedbrake_arm_exception(self):
        """Test the SpdBrkLever arm-only exception to the flight-control-input filter."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        router.get_router_type = lambda: 'slave'
        router.config.identity.type = 'slave'
        router.sharedinfo = {"pilot_flying_simulator": "OtherSim"}
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]

        # A different flight-control keyword gets no exception: dropped as usual,
        # with a resync_key so the sender's own local PSX gets corrected back.
        (action, code, _, extra_data) = rules.route("Qs357=123", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTERED_INGRESS)
        self.assertEqual(extra_data, {'resync_key': 'Qs357'})

        # SpdBrkLever increasing, within the armed range: allowed.
        router.cache.update('Qh388', 0)
        (action, code, *_) = rules.route("Qh388=44", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)

        # SpdBrkLever increasing further, but past the armed position: dropped,
        # with a resync_key so the sender's own lever snaps back to 44.
        router.cache.update('Qh388', 44)
        (action, code, _, extra_data) = rules.route("Qh388=100", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTERED_INGRESS)
        self.assertEqual(extra_data, {'resync_key': 'Qh388'})

        # SpdBrkLever decreasing (e.g. the non-flying sim trying to disarm/retract): dropped.
        router.cache.update('Qh388', 44)
        (action, code, *_) = rules.route("Qh388=0", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTERED_INGRESS)

        # SpdBrkLever unchanged: dropped (not an increase).
        router.cache.update('Qh388', 44)
        (action, code, *_) = rules.route("Qh388=44", testpeer)
        self.assertEqual(action, RulesAction.DROP)

        # All control locks in: dropped too, also with a resync_key.
        router.sharedinfo["pilot_flying_simulator"] = "ALL_CONTROL_LOCKS"
        (action, code, _, extra_data) = rules.route("Qh388=44", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTERED_INGRESS)
        self.assertEqual(extra_data, {'resync_key': 'Qh388'})
        router.sharedinfo["pilot_flying_simulator"] = "OtherSim"

        # We are pilot flying ourselves: no filtering applies at all.
        router.sharedinfo["pilot_flying_simulator"] = router.config.identity.simulator
        (action, code, *_) = rules.route("Qh388=500", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)

    def test_check_switches_dropped_on_master(self):
        """A master router drops Qs419=CHECK SWITCHES from clients, but not the clear."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]
        testpeer.is_frankenrouter = True
        testpeer.simulator_name = 'OtherSim'
        router.config.identity.type = 'master'
        (action, code, *_) = rules.route("Qs419=CHECK SWITCHES", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.CHECK_SWITCHES_DROPPED)
        # The clear and other cautions pass
        for line in ("Qs419=", "Qs419=OTHER CAUTION"):
            (action, code, *_) = rules.route(line, testpeer)
            self.assertEqual(action, RulesAction.NORMAL, msg=line)
        # Not on slave or standalone routers
        for router_type in ('slave', 'standalone'):
            router.config.identity.type = router_type
            (action, code, *_) = rules.route("Qs419=CHECK SWITCHES", testpeer)
            self.assertEqual(action, RulesAction.NORMAL, msg=router_type)
        # Opted out
        router.config.identity.type = 'master'
        router.config.filtering.master_drop_check_switches = False
        (action, code, *_) = rules.route("Qs419=CHECK SWITCHES", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)

    def test_slave_drop_printer_push(self):
        """A slave drops PrinterPush (PPR ADV) from upstream only, unless turned off."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        local = router.clients[('127.0.0.1', 12345)]
        dropped = (RulesAction.DROP, RulesCode.KEYVALUE_FILTERED_INGRESS_SIM_LOCAL)
        for value in ('0', '-1'):
            self.assertEqual(rules.route(f"Qh415={value}", router.upstream)[:2], dropped)
        # From this sim's own clients: passes
        self.assertEqual(rules.route("Qh415=0", local)[:2],
                         (RulesAction.NORMAL, RulesCode.KEYVALUE_NORMAL))
        # Not on a master, and not when turned off
        router.config.identity.type = 'master'
        self.assertNotEqual(rules.route("Qh415=0", router.upstream)[:2], dropped)
        router.config.identity.type = 'slave'
        router.config.filtering.slave_drop_printer_push = False
        self.assertNotEqual(rules.route("Qh415=0", router.upstream)[:2], dropped)

    def test_ptt_ground_handling_translation(self):
        """Test that a cross-sim PTT drop synthesizes an addon=GROUND.HANDLING PTT event."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)

        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]
        testpeer.is_frankenrouter = True
        testpeer.simulator_name = 'OtherSim'

        cases = (
            ('Qh410', '26', 'LEFT', 'DOWN'),
            ('Qh410', '-1', 'LEFT', 'UP'),
            ('Qh411', '26', 'CENTRE', 'DOWN'),
            ('Qh411', '-1', 'CENTRE', 'UP'),
            ('Qh412', '26', 'RIGHT', 'DOWN'),
            ('Qh412', '-1', 'RIGHT', 'UP'),
            ('Qh82', '1', 'LEFT', 'DOWN'),
            ('Qh82', '0', 'LEFT', 'UP'),
            ('Qh93', '1', 'RIGHT', 'DOWN'),
            ('Qh93', '0', 'RIGHT', 'UP'),
        )
        for key, val, panel, edge in cases:
            (action, code, _, extra_data) = rules.route(f"{key}={val}", testpeer)
            self.assertEqual(action, RulesAction.DROP)
            self.assertEqual(code, RulesCode.PTT)
            self.assertEqual(
                extra_data,
                {
                    'synthetic_ground_handling_line':
                        f"addon=GROUND.HANDLING;;;;V1|EVENT|1|PTT|{panel}|{edge}",
                    'exclude_simulator': 'OtherSim',
                },
                msg=f"{key}={val}")

        # Audio panel selection values (not press/release) never reach the
        # drop branch at all, so no synthetic line -- already covered by
        # test_ptt_filter's NORMAL-passthrough assertion, re-checked here
        # for the extra_data specifically.
        (action, code, _, extra_data) = rules.route("Qh410=5", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertIsNone(extra_data)

        # Same-sim PTT never drops, so never synthesizes either.
        testpeer.simulator_name = router.config.identity.simulator
        (action, code, _, extra_data) = rules.route("Qh410=26", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertIsNone(extra_data)

    def test_ptt_ground_handling_translation_disabled(self):
        """Test that ptt_ground_handling_translation=False suppresses the synthetic line."""
        router = self.DummyFrankenrouter()
        router.config.filtering.ptt_ground_handling_translation = False
        rules = Rules(router)

        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        testpeer = router.clients[('127.0.0.1', 12345)]
        testpeer.is_frankenrouter = True
        testpeer.simulator_name = 'OtherSim'

        (action, code, _, extra_data) = rules.route("Qh410=26", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.PTT)
        self.assertIsNone(extra_data)

    def _quarantine_peer(self, router):
        """Return a downstream client connection for the quarantine tests."""
        router.upstream = self.DummyUpstreamConnection()
        router.clients = {
            ('127.0.0.1', 12345): self.DummyClientConnection(('127.0.0.1', 12345)),
        }
        return router.clients[('127.0.0.1', 12345)]

    def _quarantine_route(self, rules, line, peer, age):
        """Route line from peer, connected age seconds ago; return the result code."""
        peer.connected_at = time.perf_counter() - age
        (_, code, *_) = rules.route(line, peer)
        return code

    def test_post_connect_quarantine(self):
        """Test the post-connect quarantine's built-in table."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        peer = self._quarantine_peer(router)
        quarantined = RulesCode.POST_CONNECT_QUARANTINE
        for line, seconds in (
                ("Qs546=avsfc-1;;;-1;-99;-1;-1;999;", 5.0),     # TkofRefNg
                ("Qs196=0;6;035427", 30.0),                     # MfAcBus1 (Mf*)
                ("Qi130=1", 30.0),                              # MfEvent (Mf*)
                ("Qi190=257", QUARANTINE_DEFAULT_S),            # MaweaBits
                ("Qi272=88", QUARANTINE_DEFAULT_S),             # CfgPin3Prog
        ):
            # Freshly connected and just inside the time: dropped
            self.assertEqual(self._quarantine_route(rules, line, peer, 0.0), quarantined, line)
            self.assertEqual(
                self._quarantine_route(rules, line, peer, seconds - 1.0), quarantined, line)
            # Past it: a deliberate write passes
            self.assertEqual(self._quarantine_route(rules, line, peer, seconds + 1.0),
                             RulesCode.KEYVALUE_NORMAL, line)
        # Other variables pass at once
        self.assertEqual(self._quarantine_route(rules, "Qs197=0;6;-99", peer, 0.0),
                         RulesCode.KEYVALUE_NORMAL)
        # Never applied to upstream, even right after connecting
        router.upstream.connected_at = time.perf_counter()
        (_, code, *_) = rules.route("Qi190=257", router.upstream)
        self.assertNotEqual(code, quarantined)

    def test_post_connect_quarantine_weather(self):
        """Weather is quarantined for 120 s from everyone but frankenweather."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        peer = self._quarantine_peer(router)
        quarantined = RulesCode.POST_CONNECT_QUARANTINE
        # frankenweather: passes at once
        peer.client_provided_id = _FRANKENWEATHER_CLIENT_ID
        for line in ("Qs499=#DOWNLOADED", "Qs329=0;44950;44950;0;0;0"):
            self.assertEqual(self._quarantine_route(rules, line, peer, 0.0),
                             RulesCode.KEYVALUE_NORMAL, line)
        # ...but its other quarantined variables don't
        self.assertEqual(self._quarantine_route(rules, "Qi190=257", peer, 0.0), quarantined)
        # A PSX instance or a slave router: dropped for QUARANTINE_WEATHER_S
        for peer.client_provided_id, peer.is_frankenrouter in ((None, False), ('camslave', True)):
            for line in ("Qs499=#DOWNLOADED", "Qs329=0;44950;44950;0;0;0"):
                self.assertEqual(self._quarantine_route(rules, line, peer, 0.0), quarantined, line)
                self.assertEqual(self._quarantine_route(
                    rules, line, peer, QUARANTINE_WEATHER_S - 1.0), quarantined, line)
                self.assertEqual(self._quarantine_route(
                    rules, line, peer, QUARANTINE_WEATHER_S + 1.0), RulesCode.KEYVALUE_NORMAL, line)

    def test_post_connect_quarantine_config(self):
        """[quarantine] changes the default and per-variable times and adds variables."""
        class Quarantine:  # pylint: disable=too-few-public-methods
            """A [quarantine] config section."""

            default_seconds = 10
            variables = {'TkofRefNg': 0, 'Mf*': 100, 'MfAcBus1': 2, 'Wx1': 20,
                         'CfgPin2Prog': 15}
        router = self.DummyFrankenrouter()
        router.config.quarantine = Quarantine()
        router.variables.get_variable_name = lambda key: {
            'Qs546': 'TkofRefNg', 'Qi130': 'MfEvent', 'Qs196': 'MfAcBus1', 'Qi190': 'MaweaBits',
            'Qs329': 'Wx1', 'Qi248': 'CfgPin2Prog'}.get(key, key)
        rules = Rules(router)
        peer = self._quarantine_peer(router)
        quarantined = RulesCode.POST_CONNECT_QUARANTINE
        normal = RulesCode.KEYVALUE_NORMAL
        for line, age, expected in (
                ("Qs546=avsfc-1;", 0.0, normal),        # turned off
                ("Qi130=1", 99.0, quarantined),         # Mf* now 100 s
                ("Qs196=0;6;035427", 3.0, normal),      # an exact name beats the prefix
                ("Qi190=257", 9.0, quarantined),        # the default, now 10 s
                ("Qi190=257", 11.0, normal),
                ("Qi248=8", 14.0, quarantined),         # added: all connections
                ("Qs329=0;1", 19.0, quarantined),       # Wx1 now 20 s
        ):
            self.assertEqual(self._quarantine_route(rules, line, peer, age), expected,
                             (line, age))
        self.assertEqual(self._quarantine_route(rules, "Qs329=0;1", peer, 19.0), quarantined)
        self.assertEqual(self._quarantine_route(rules, "Qs329=0;1", peer, 21.0), normal)

    def test_traffic_only_from_traffic_master(self):
        """Only the traffic master sim's clients may send TfcPos/TfcVel."""
        tfc = "Qs450=0.22817;-1.03845;1961;7559"

        def route(rules, peer):
            return rules.route(tfc, peer)[0]

        # Slave, not the traffic master: drop from all clients, named or not
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        router.filter_traffic = True
        named = self.DummyClientConnection(('127.0.0.1', 62205))
        named.display_name = 'vPilot Plugin'
        unnamed = self.DummyClientConnection(('127.0.0.1', 62215))
        for peer in (named, unnamed):
            self.assertEqual(route(rules, peer), RulesAction.DROP)
            self.assertEqual(len(peer.traffic_times), 1)
            self.assertEqual(len(peer.traffic_dropped_times), 1)
        self.assertEqual(rules.route("Qs451=0;1;2", unnamed)[0], RulesAction.DROP)
        self.assertEqual(len(unnamed.traffic_times), 1)  # only Qs450 counts
        # Slave that is the traffic master: forward
        router.filter_traffic = False
        self.assertNotEqual(route(rules, unnamed), RulesAction.DROP)

        # Master
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        router.config.identity.type = 'master'
        slave = self.DummyClientConnection(('10.0.0.2', 50000))
        slave.is_frankenrouter = True
        slave.simulator_name = 'OtherSim'
        local = self.DummyClientConnection(('127.0.0.1', 50001))
        # No traffic master: accept all
        router.sharedinfo['traffic_source_simulator'] = 'NOSIM'
        self.assertNotEqual(route(rules, slave), RulesAction.DROP)
        self.assertNotEqual(route(rules, local), RulesAction.DROP)
        # Another sim is traffic master: drop from this slave and own clients
        router.sharedinfo['traffic_source_simulator'] = 'ThirdSim'
        self.assertEqual(route(rules, slave), RulesAction.DROP)
        self.assertEqual(route(rules, local), RulesAction.DROP)
        self.assertEqual(len(slave.traffic_dropped_times), 1)
        # The slave's sim is traffic master
        router.sharedinfo['traffic_source_simulator'] = 'OtherSim'
        self.assertNotEqual(route(rules, slave), RulesAction.DROP)
        self.assertEqual(route(rules, local), RulesAction.DROP)
        # The master's own sim is traffic master
        router.sharedinfo['traffic_source_simulator'] = 'MySim'
        self.assertEqual(route(rules, slave), RulesAction.DROP)
        self.assertNotEqual(route(rules, local), RulesAction.DROP)

    def test_post_connect_quarantine_restarts_on_reset(self):
        """A welcome reset restarts the quarantine of an old connection."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        peer = self._quarantine_peer(router)
        peer.connected_at = time.perf_counter() - 3600
        (_, code, *_) = rules.route("Qi130=1", peer)
        self.assertNotEqual(code, RulesCode.POST_CONNECT_QUARANTINE)
        peer.quarantine_restarted_at = time.perf_counter() - 1.0
        (_, code, *_) = rules.route("Qi130=1", peer)
        self.assertEqual(code, RulesCode.POST_CONNECT_QUARANTINE)
        peer.quarantine_restarted_at = time.perf_counter() - 31.0
        (_, code, *_) = rules.route("Qi130=1", peer)
        self.assertNotEqual(code, RulesCode.POST_CONNECT_QUARANTINE)

    def test_post_connect_quarantine_restore(self):
        """A quarantined write gets the cached value back, at most QUARANTINE_MAX_RESTORES times."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        peer = self._quarantine_peer(router)
        peer.connected_at = time.perf_counter()
        sigmets = "#DOWNLOADED 22:08:46 - 03 NOV 2026 UTC"
        router.cache.update('Qs499', sigmets)
        for _ in range(QUARANTINE_MAX_RESTORES):
            (_, code, _, extra) = rules.route("Qs499=#", peer)
            self.assertEqual(code, RulesCode.POST_CONNECT_QUARANTINE)
            self.assertEqual(extra, {"reply": f"Qs499={sigmets}"})
        (_, code, _, extra) = rules.route("Qs499=#", peer)
        self.assertEqual(code, RulesCode.POST_CONNECT_QUARANTINE)
        self.assertIsNone(extra)
        # Same value as the cache, or nothing cached: nothing to give back
        (_, code, _, extra) = rules.route("Qs329=0;1", peer)
        self.assertEqual(code, RulesCode.POST_CONNECT_QUARANTINE)
        self.assertIsNone(extra)
        router.cache.update('Qs329', '0;1')
        self.assertIsNone(rules.route("Qs329=0;1", peer)[3])

    def test_detect_psx_main_client(self):
        """A client writing MfEvent soon after its first welcome is marked a PSX main client."""
        router = self.DummyFrankenrouter()
        rules = Rules(router)
        peer = self._quarantine_peer(router)
        rules.route("Qi130=1", peer)            # no welcome yet
        self.assertFalse(peer.looks_like_psx_main)
        peer.first_welcome_done_at = time.perf_counter() - 5.0
        rules.route("Qi130=1", peer)            # too late
        self.assertFalse(peer.looks_like_psx_main)
        peer.first_welcome_done_at = time.perf_counter() - 0.2
        rules.route("Qi190=0", peer)            # another variable
        self.assertFalse(peer.looks_like_psx_main)
        rules.route("Qi130=1", peer)
        self.assertTrue(peer.looks_like_psx_main)
        router.upstream.first_welcome_done_at = time.perf_counter() - 0.2
        rules.route("Qi130=1", router.upstream)  # never upstream
        self.assertFalse(router.upstream.looks_like_psx_main)
