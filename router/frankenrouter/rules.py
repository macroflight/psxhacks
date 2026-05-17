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

# Same for teh traffic keywords
TRAFFIC_KEYWORDS = frozenset({'Qs450', 'Qs451'})


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
    FALLBACK_RULE = enum.auto()
    FRDP_PING = enum.auto()
    FRDP_PONG = enum.auto()
    FRDP_IDENT = enum.auto()
    FRDP_MY_CONTROLS = enum.auto()
    FRDP_ALL_CONTROL_LOCKS = enum.auto()
    FRDP_NO_CONTROL_LOCKS = enum.auto()
    FRDP_FLIGHTCONTROLS = enum.auto()
    FRDP_ELEVATION_SOURCE = enum.auto()
    FRDP_TRAFFIC_SOURCE = enum.auto()
    FRDP_JOIN = enum.auto()
    FRDP_CLIENTINFO = enum.auto()
    FRDP_ROUTERINFO = enum.auto()
    FRDP_SHAREDINFO = enum.auto()
    FRDP_FLIGHTINFO = enum.auto()
    FRDP_AUTH_FAIL = enum.auto()
    FRDP_AUTH_OK = enum.auto()
    FRDP_AUTH_ALREADY_HAS_ACCESS = enum.auto()
    NAME_FROM_FRANKENROUTER = enum.auto()
    NAME_LEARNED = enum.auto()
    NAME_NOCHANGE = enum.auto()
    NAME_REJECTED = enum.auto()
    NOLONG = enum.auto()
    NONPSX = enum.auto()
    NOWRITE = enum.auto()
    DEMAND = enum.auto()
    ADDON_FORWARDED = enum.auto()
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
    PSXNETVATSIM = enum.auto()
    KEYVALUE_FILTERED_INGRESS_SIM_LOCAL = enum.auto()
    KEYVALUE_FILTERED_EGRESS_SIM_LOCAL = enum.auto()
    KEYVALUE_FILTERED_INGRESS = enum.auto()
    KEYVALUE_FILTERED_INGRESS_SILENT = enum.auto()
    KEYVALUE_FILTER_EGRESS = enum.auto()
    KEYVALUE_NORMAL = enum.auto()
    SPEEDBRAKE_OVERRIDE = enum.auto()
    PARKING_BRAKE_FORCE_RELEASE = enum.auto()


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
                message=f"Unexpected ID {payload}, expected {expected_id}"
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
        self.sender.display_name = routername
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

    def handle_addon_frankenrouter_elevation_source(self, payload):
        """Handle FRDP ELEVATION_SOURCE message.

        Format:
        addon=FRANKENROUTER:<protocol version>:ELEVATION_SOURCE:<sim name>

        When this router is the SHAREDINFO master, record the source sim and
        trigger a SHAREDINFO broadcast so all routers update their filters.
        Otherwise forward upstream toward the master.
        """
        if self.router.config.identity.type == 'master':
            self.router.sharedinfo['elevation_source_simulator'] = payload
            self.logger.info("SET elevation_source_simulator to %s",
                             self.router.sharedinfo['elevation_source_simulator'])
            self.router.frdp_sharedinfo_requested = True
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
        if self.router.config.identity.type == 'master':
            self.router.sharedinfo['traffic_source_simulator'] = payload
            self.router.frdp_sharedinfo_requested = True
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_TRAFFIC_SOURCE)
        return self.myreturn(RulesAction.UPSTREAM_ONLY, RulesCode.FRDP_TRAFFIC_SOURCE)

    def handle_addon_frankenrouter_flightcontrols(self, payload):
        """Handle a FRDP FLIGHTCONTROLS message.

        Format:
        addon=FRANKENROUTER:<protocol version>:FLIGHTCONTROLS:<flying sim name>

        For a visual indicator, we use Qs421="FreeMsgM"; Mode=ECON; Min=0; Max=16;

        e.g
        Qs421=PF: MACRO
        Qs421=PF: ALL
        Qs421=PF: NONE
        """
        # Update sharedinfo
        if payload == 'NO_CONTROL_LOCKS':
            self.router.sharedinfo['pilot_flying_simulator'] = "NO_CONTROL_LOCKS"
            message = "Qs421="
        elif payload == 'ALL_CONTROL_LOCKS':
            self.router.sharedinfo['pilot_flying_simulator'] = "ALL_CONTROL_LOCKS"
            message = "Qs421=PF: NOONE"
        else:
            self.router.sharedinfo['pilot_flying_simulator'] = payload
            # We limit the length of the sim identifier so it fits on EICAS
            ident = self.router.sharedinfo['pilot_flying_simulator'][:11].upper()
            message = f"Qs421=PF: {ident}"
        self.router.frdp_sharedinfo_requested = True

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
        # Forward message to network but only to frankenrouters
        return self.myreturn(
            RulesAction.FILTER,
            RulesCode.KEYVALUE_FILTER_EGRESS,
            extra_data={'exclude_non_frankenrouter': True})

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

        if self.router.config.identity.type == 'master':
            raise SystemExit(
                f"SHAREDINFO message received from {sharedinfo['master_uuid']}, "
                f"but this router is configured as master. This should never happen.")

        # Merge data from sharedinfo package into our own variables
        self.router.sharedinfo['master_uuid'] = sharedinfo['master_uuid']
        for key in [
            'pilot_flying_simulator',
            'elevation_source_simulator',
            'traffic_source_simulator',
        ]:
            if key in sharedinfo:
                self.router.sharedinfo[key] = sharedinfo[key]

        # Update local filter state based on source assignments in SHAREDINFO.
        # Do NOT trigger frdp_sharedinfo_requested to avoid a broadcast loop.
        # The master router never updates its own filters from SHAREDINFO.
        if self.router.config.identity.type != 'master':
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

        # Forward message to network but only to frankenrouters
        return self.myreturn(
            RulesAction.FILTER,
            RulesCode.KEYVALUE_FILTER_EGRESS,
            extra_data={'exclude_non_frankenrouter': True})

    def _speedbrake_override(self, value):
        """Return a SPEEDBRAKE_OVERRIDE result for a filtered Qh388 input."""
        try:
            lever = int(value)
        except ValueError:
            lever = 0
        override = 41 if lever > 40 else 0
        action_word = "arming" if override == 41 else "disarming"
        msg = f"speedbrake input {value} from filtered sim, {action_word} speedbrake"
        return self.myreturn(
            RulesAction.DROP,
            RulesCode.SPEEDBRAKE_OVERRIDE,
            message=msg,
            extra_data={'override_line': f'Qh388={override}'},
        )

    def handle_addon_frankenrouter_flightinfo(self, payload):
        """Handle a FRDP FLIGHTINFO message.

        Format:
        addon=FRANKENROUTER:<protocol version>:FLIGHTINFO:<JSON data>
        """
        try:
            flightinfo = json.loads(payload)
        except json.decoder.JSONDecodeError:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Invalid JSON data in FRDP FLIGHTINFO message: {self.line}"
            )
        self.router.flightinfo = flightinfo
        return self.myreturn(
            RulesAction.FILTER,
            RulesCode.FRDP_FLIGHTINFO,
            extra_data={'exclude_non_frankenrouter': True})

    def handle_addon_frankenrouter_auth(self, payload):
        """Handle FRDP AUTH message.

        Format:
        addon=FRANKENROUTER:<protocol version>:AUTH:<password>
        """
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got FRDP AUTH message from upstream: {self.line}"
            )
        if self.sender.has_access():
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_ALREADY_HAS_ACCESS)
        if payload == "":
            # We don't allow empty passwords
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_FAIL)
        # Try to authenticate
        self.sender.update_access_level(payload)
        if not self.sender.has_access():
            return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_FAIL)
        self.router.connection_state_changed()
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_AUTH_OK)

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
        if message_type == 'FLIGHTCONTROLS':
            return self.handle_addon_frankenrouter_flightcontrols(payload)
        if message_type == 'ELEVATION_SOURCE':
            return self.handle_addon_frankenrouter_elevation_source(payload)
        if message_type == 'TRAFFIC_SOURCE':
            return self.handle_addon_frankenrouter_traffic_source(payload)
        if message_type == 'JOIN':
            return self.handle_addon_frankenrouter_join(payload)
        if message_type == 'ROUTERINFO':
            return self.handle_addon_frankenrouter_routerinfo(payload)
        if message_type == 'SHAREDINFO':
            return self.handle_addon_frankenrouter_sharedinfo(payload)
        if message_type == 'FLIGHTINFO':
            return self.handle_addon_frankenrouter_flightinfo(payload)
        if message_type == 'CLIENTINFO':
            return self.handle_addon_frankenrouter_clientinfo(payload)
        if message_type == 'AUTH':
            return self.handle_addon_frankenrouter_auth(payload)
        # Drop unknown FRDP messages
        return self.myreturn(
            RulesAction.DROP, RulesCode.MESSAGE_INVALID,
            message=f"Unsupported FRDP message type {message_type}: {self.line}"
        )

    def handle_addon(self, rest):  # pylint: disable=too-many-return-statements
        """Handle an addon= message."""
        try:
            (addon, payload) = rest.split(":", 1)
        except ValueError:
            self.logger.debug("Got unsupported addon message: %s", rest)
            addon = rest
            payload = ""
        if addon == 'FRANKENCDUPROXY':
            if (self.sender.is_frankenrouter and
                    self.router.config.identity.simulator != self.sender.simulator_name):
                self.logger.info(
                    "Dropping FRANKENCDUPROXY addon from other-sim frankenrouter %s",
                    self.sender.simulator_name)
                return self.myreturn(RulesAction.DROP, RulesCode.ADDON_FORWARDED)

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
        if addon == 'PSXNETVATSIM':
            if self.sender.is_frankenrouter:
                if self.router.config.identity.simulator != self.sender.simulator_name:
                    self.logger.info(
                        "Dropping addon=PSXNETVATSIM from other sim %s: %s",
                        self.sender.simulator_name, self.line)
                    return self.myreturn(RulesAction.DROP, RulesCode.PSXNETVATSIM)

        # Unhandled addon messages should be forwarded, but only from
        # clients that are allowed to write.
        if not self.allow_write():
            return self.myreturn(RulesAction.DROP, RulesCode.NOWRITE)
        return self.myreturn(RulesAction.NORMAL, RulesCode.ADDON_FORWARDED)

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
        if re.match(r".*:FRANKEN.PY frankenrouter", rest):
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
        """Handle the start keyword."""
        if self.sender.upstream:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Got start message from upstream: {self.line}"
            )
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

    def myreturn(self, action, code, message=None, extra_data=None):
        """Return a routing decision."""
        return (action, code, message, extra_data)

    def allow_write(self):
        """Determine if this client is allowed to write."""
        if self.sender.upstream:
            return True
        if self.sender.can_write():
            return True
        return False

    def is_readonly_client(self):
        """Return True if this client matches the SRSL readonly criteria.

        A client is read-only when its display_name contains 'SRSL' and its
        IP address is listed in psx.readonly_srsl_ips.
        """
        if self.sender.upstream:
            return False
        if 'SRSL' not in (self.sender.display_name or ''):
            return False
        return self.sender.ip in self.router.config.psx.readonly_srsl_ips

    def route(self, line, sender):  # pylint: disable=too-many-return-statements,too-many-branches, too-many-statements
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

        if self.is_readonly_client():
            return self.myreturn(RulesAction.DROP, RulesCode.KEYVALUE_FILTERED_INGRESS)

        if key == 'again':
            return self.handle_again()

        if key == 'start':
            return self.handle_start()

        if key == 'pleaseBeSoKindAndQuit':
            return self.handle_pbskaq()

        if key == 'layout':
            return self.handle_layout()

        if (
                key in self.router.config.psx.filter_from_other_sim and
                self.sender.is_frankenrouter and
                self.router.config.identity.simulator != self.sender.simulator_name
        ):
            self.logger.info(
                "Dropping %s from other-sim frankenrouter %s", key, self.sender.simulator_name)
            return self.myreturn(RulesAction.DROP, RulesCode.KEYVALUE_FILTERED_INGRESS_SIM_LOCAL)

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

        # Update router variable stats database
        self.router.variable_stats_add(key, sender.peername)

        if key == 'nolong':
            return self.handle_nolong()

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

        # Ingress filter: flight controls if this is a slave sim router
        if self.router.get_router_type() == 'slave':
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
                        )
                    )
                else:
                    if flying != self.router.config.identity.simulator:
                        # Someone else is pilot flying - filter flight controls,
                        # with special handling for speedbrake lever.
                        if key == 'Qh388':
                            return self._speedbrake_override(value)
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
                            )
                        )

        # Testing a parking brake fix to make it less likely they get
        # stuck on.
        # Qs357="Brakes"; Mode=ECON; Min=3; Max=9;
        # Qh397="ParkBrkLev"; Mode=ECON; Min=0; Max=1;
        if key == 'Qs357':
            # Parking brake release fix
            if not self.sender.upstream and self.router.get_router_type() == 'slave':
                if (self.router.cache.get_value('Qh397') == 1 and
                        self.router.cache.get_age('Qh397') > 5.0):
                    (left, right) = value.split(';', 1)
                    if int(left) > 990 and int(right) > 990:
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
                    return self.myreturn(
                        RulesAction.UPSTREAM_ONLY,
                        RulesCode.KEYVALUE_FILTERED_INGRESS_SILENT,
                        message="Qi198 unchanged: forwarding upstream only")
            except (RouterCacheException, ValueError, TypeError):
                pass

        if not self.sender.upstream and key in TRAFFIC_KEYWORDS:
            # Filter traffic injection from vPilot if filter is enabled
            # We use SILENT filtering since this will happen often
            if self.router.filter_traffic:
                if "vPilot" in self.sender.display_name:
                    return self.myreturn(
                        RulesAction.DROP,
                        RulesCode.KEYVALUE_FILTERED_INGRESS_SILENT,
                        message=f"filtered {key} as filter_traffic is set")

        # Store key-value in router cache
        try:
            self.router.cache.update(key, value)
        except RouterCacheTypeError as exc:
            return self.myreturn(
                RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                message=f"Wrong datatype in message, dropping it: {exc}")

        # The "nolong" keywords are only sent to clients that have
        # asked for them (using the "nolong" keyword)
        if key in self.router.variables.keywords_with_mode("NOLONG"):
            return self.myreturn(
                RulesAction.FILTER,
                RulesCode.KEYVALUE_FILTER_EGRESS,
                extra_data={'nolong': True})

        # START keywords that are not also ECON (e.g Qs493 and Qi208)
        # get special handling.

        # During normal router operation, we assume a START variable
        # is sent as a response from a "start" command, and then only
        # that router client should get the START variables.

        # However, during situ load START variables are sent, e.g
        # Qs122, and then we must forward them to all clients. So we
        # check the time since the last "load3" and if less than 5s,
        # we forward the START variable.
        if key in self.router.variables.keywords_with_mode('START'):
            if key not in self.router.variables.keywords_with_mode('ECON'):
                time_since_load3 = time.perf_counter() - self.router.last_load3
                self.logger.debug(
                    "START variable %s, time since load3 is %.1fs",
                    key, time_since_load3
                )
                if time_since_load3 > 5.0:
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

        #
        # Send normally
        #
        return self.myreturn(RulesAction.NORMAL, RulesCode.KEYVALUE_NORMAL)


class TestRules(unittest.TestCase):
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

    class DummyCache():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the cache."""
            self.cache = {}

        def update(self, keyword, value):
            """Fake cache update."""
            self.cache[keyword] = value

    class DummyConfigPsx():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the config."""
            self.filter_from_other_sim = []
            self.filter_to_other_sim = []
            self.readonly_srsl_ips = []

    class DummyConfig():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the config."""
            self.psx = TestRules.DummyConfigPsx()

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
            self.frdp_version = 1
            self.config = TestRules.DummyConfig()

        def is_upstream_connected(self):
            """Return dummy value."""
            return False

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
            self.simulator_name = 'UnknownSim'
            self.router_name = 'UnknownRouter'
            self.display_name = 'UnknownDisplay'
            self.display_name_source = 'UnknownSource'
            self.client_provided_id = None
            self.client_provided_display_name = None
            self.is_frankenrouter = False
            self.upstream = False
            self.peername = None

    class DummyClientConnection(DummyConnection):  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self, peername):
            """Initialize the connection."""
            super().__init__()
            self.is_upstream = False
            self.access_level = 'full'
            self.nolong = False
            self.demands = set()
            self.peername = peername

        def can_write(self):
            """Check if this client is allowed to write."""
            if self.access_level == NOACCESS_ACCESS_LEVEL:
                return False
            return True

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
        self.assertEqual(router.upstream.display_name, 'OtherRouter')
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
        self.assertEqual(testpeer.display_name, 'SomeRouter')
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

        # AUTH success from client
        testpeer.access_level = NOACCESS_ACCESS_LEVEL
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:AUTH:mypassword", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_AUTH_OK)
        self.assertEqual(testpeer.access_level, 'full')

        # AUTH failure from client
        testpeer.access_level = NOACCESS_ACCESS_LEVEL
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:AUTH:badpassword", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_AUTH_FAIL)
        self.assertEqual(testpeer.access_level, NOACCESS_ACCESS_LEVEL)

        # AUTH already authenticated
        testpeer.access_level = 'full'
        (action, code, *_) = rules.route(
            "addon=FRANKENROUTER:1:AUTH:mypassword", testpeer)
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
        (action, code, *_) = rules.route("start", testpeer)
        self.assertEqual(action, RulesAction.UPSTREAM_ONLY)
        self.assertEqual(code, RulesCode.START)

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

        # Qs997 is START and ECON, should be handled normally
        testpeer.display_name = "BACARS"
        (action, code, _, extra_data) = rules.route("Qs997=START and ECON", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)
        self.assertIsNone(extra_data)

        # Qs998 is only START, should get custom filtering
        testpeer.display_name = "BACARS"
        (action, code, _, extra_data) = rules.route("Qs998=START only", testpeer)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTER_EGRESS)
        self.assertTrue('start' in extra_data)
        self.assertTrue('key' in extra_data)

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
