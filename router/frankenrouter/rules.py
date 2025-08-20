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

DISPLAY_NAME_MAXLEN = 24


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
    FRDP_BANG = enum.auto()
    FRDP_PING = enum.auto()
    FRDP_PONG = enum.auto()
    FRDP_IDENT = enum.auto()
    FRDP_CLIENTINFO = enum.auto()
    FRDP_ROUTERINFO = enum.auto()
    FRDP_AUTH_FAIL = enum.auto()
    FRDP_AUTH_OK = enum.auto()
    FRDP_AUTH_ALREADY_HAS_ACCESS = enum.auto()
    NAME_FROM_FRANKENROUTER = enum.auto()
    NAME_LEARNED = enum.auto()
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
    EXIT = enum.auto()
    PBSKAQ = enum.auto()
    LAYOUT = enum.auto()
    KEYVALUE_FILTERED_INGRESS = enum.auto()
    KEYVALUE_FILTER_EGRESS = enum.auto()
    KEYVALUE_NORMAL = enum.auto()


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
        frdp_rtt = time.perf_counter() - self.sender.ping_sent
        # FIXME: ignore RTT numbers in a period after upstream or a
        # client has connected (10s?)
        self.sender.frdp_ping_rtts.append(frdp_rtt)
        # Drop message
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_PONG,
                             extra_data={'frdp_rtt': frdp_rtt})

    def handle_addon_frankenrouter_ident(self, payload):
        """Handle a FRDP IDENT message.

        Format:
        addon=FRANKENROUTER:<protocol version>:IDENT:<sim name>:<router name>
        """
        (simname, routername, uuid) = payload.split(':')
        self.sender.simulator_name = simname
        self.sender.router_name = routername
        self.sender.display_name = routername
        self.sender.uuid = uuid
        self.sender.display_name_source = "FRDP IDENT"
        self.router.connection_state_changed()
        # Drop message
        return self.myreturn(RulesAction.DROP, RulesCode.FRDP_IDENT)

    def handle_addon_frankenrouter_clientinfo(self, payload):
        """Handle a FRDP CLIENTINFO message.

        Format:
        addon=FRANKENROUTER:<protocol version>:CLIENTINFO:<JSON data>

        JSON data example:

        {
            "laddr": "127.0.0.1",
            "lport": 12345,
            "name": "PSX Sounds"
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
        peername = (clientinfo['laddr'], clientinfo['lport'])
        if peername in self.router.clients:
            thisname = clientinfo['name']
            if len(thisname) > DISPLAY_NAME_MAXLEN:
                newname = thisname[:DISPLAY_NAME_MAXLEN]
                self.logger.warning(
                    "Client name %s is too long, using %s",
                    thisname, newname)
                thisname = newname
            self.router.clients[peername].display_name = thisname
            self.router.clients[peername].display_name_source = "FRDP CLIENTINFO"
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
        # Forward message to network
        return self.myreturn(RulesAction.NORMAL, RulesCode.FRDP_ROUTERINFO)

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

    def handle_addon_frankenrouter(self, rest):  # pylint: disable=too-many-return-statements
        """Handle FRANKENROUTER addon message."""
        (message_type, _, payload) = rest.partition(":")
        if message_type == 'PING':
            return self.handle_addon_frankenrouter_ping(payload)
        if message_type == 'PONG':
            return self.handle_addon_frankenrouter_pong(payload)
        if message_type == 'IDENT':
            return self.handle_addon_frankenrouter_ident(payload)
        if message_type == 'BANG':
            # Store in router object and forward to network
            self.router.last_bang = time.perf_counter()
            return self.myreturn(RulesAction.NORMAL, RulesCode.FRDP_BANG)
        if message_type == 'ROUTERINFO':
            return self.handle_addon_frankenrouter_routerinfo(payload)
        if message_type == 'CLIENTINFO':
            return self.handle_addon_frankenrouter_clientinfo(payload)
        if message_type == 'AUTH':
            return self.handle_addon_frankenrouter_auth(payload)
        # Drop unknown FRDP messages
        return self.myreturn(
            RulesAction.DROP, RulesCode.MESSAGE_INVALID,
            message=f"Unsupported FRDP message type {message_type}: {self.line}"
        )

    def handle_addon(self, rest):
        """Handle an addon= message."""
        try:
            (addon, payload) = rest.split(":", 1)
        except ValueError:
            self.logger.warning("Got addon message without payload from %s", rest)
            addon = rest
            payload = ""
        if addon == 'FRANKENROUTER':
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

        name=ICING:FRANKEN.PY frankenfreeze MSFS to PSX ice sync
        name=WIND:FRANKEN.PY frankenwind MSFS to PSX wind sync
        name=<simname>:FRANKEN.PY frankenrouter PSX router <routername>

        If frankenrouter: set is_frankenrouter and display_name

        else: set short display name based on the name given (we often
        have to clean it up a little)

        Using the prefix "R" for frankenrouters
        Using the prefix "N" prefix for other names learned from name= messages
        """
        if re.match(r".*:FRANKEN.PY frankenrouter", rest):
            display_name = rest.split(":")[0]
            self.sender.is_frankenrouter = True
            self.sender.display_name = display_name
            self.sender.display_name_source = "name message"
            return self.myreturn(RulesAction.DROP, RulesCode.NAME_FROM_FRANKENROUTER)

        if rest == "":
            return self.myreturn(RulesAction.DROP, RulesCode.MESSAGE_INVALID,
                                 message=f"name keyword without value: {self.line}")

        if self.sender.is_frankenrouter:
            return self.myreturn(RulesAction.DROP, RulesCode.NAME_REJECTED,
                                 message=f"ignoring name keyword from frankenrouter: {self.line}")
        # By default we use the entire name given
        thisname = rest
        # Certain known addons get a shorter or better display name
        if re.match(r".*PSX.NET EFB.*", rest):
            thisname = rest.split(":")[0]
        elif re.match(r":PSX Sounds", rest):
            thisname = "PSX Sounds"
        elif re.match(r"^MSFS Router", rest):
            thisname = "MSFS Router"
        elif re.match(r"^BACARS:", rest):
            thisname = "BACARS"
        elif re.match(r"^VPLG:", rest):
            thisname = "vPilot"
        elif re.match(r".*:FRANKEN\.PY", rest):
            thisname = rest.split(":")[0]
        self.sender.display_name = thisname
        self.sender.display_name_source = "name message"
        self.router.connection_state_changed()
        return self.myreturn(RulesAction.DROP, RulesCode.NAME_LEARNED)

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
        return self.myreturn(RulesAction.NORMAL, RulesCode.LOAD3)

    def handle_bang(self):
        """Handle the bang keyword.

        - update self.router.last_bang

        - send FRDP BANG message (so other frankenrouters learn a bang
          has been sent somewhere in the network

        - forward the bang

        """
        self.router.last_bang = time.perf_counter()
        reply = "addon=FRANKENROUTER:{self.frdp_version}:BANG"
        return self.myreturn(RulesAction.NORMAL, RulesCode.BANG,
                             extra_data={"reply": reply})

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

    def route(self, line, sender):  # pylint: disable=too-many-return-statements,too-many-branches
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

        if not self.sender.upstream and key == 'Qs119':
            # Do not accept Qs119 from BACARS within 15s of
            # connection. This prevents BACARS from printing some junk
            # when it is started.
            if time.perf_counter() - self.sender.connected_at < 15.0:
                if re.match(r".*BACARS.*", self.sender.display_name):
                    return self.myreturn(
                        RulesAction.DROP,
                        RulesCode.KEYVALUE_FILTERED_INGRESS,
                        message="filtered Qs119 from BACARS shortly after connection")

        # Store key-value in router cache
        self.router.cache.update(key, value)

        # The "nolong" keywords are only sent to clients that have
        # asked for them (using the "nolong" keyword)
        if key in self.router.variables.keywords_with_mode("NOLONG"):
            return self.myreturn(
                RulesAction.FILTER,
                RulesCode.KEYVALUE_FILTER_EGRESS,
                extra_data={'nolong': True})

        # START keywords that are not also ECON (e.g Qs493 and Qi208)
        # get special handling
        if key in self.router.variables.keywords_with_mode('START'):
            if key not in self.router.variables.keywords_with_mode('ECON'):
                return self.myreturn(
                    RulesAction.FILTER,
                    RulesCode.KEYVALUE_FILTER_EGRESS,
                    extra_data={'start': True, 'key': key})

        # Do not send Qi191 to PSX Sounds when bang sent recently
        # (this variable causes PSX Sounds to play its gear pin sound)
        # Note: since data in response to a bang can only come from
        # upstream, we only filter when the sender is upstream
        if self.sender.upstream and key == 'Qi191':
            if time.perf_counter() - self.router.last_bang < 2.0:
                return self.myreturn(
                    RulesAction.FILTER,
                    RulesCode.KEYVALUE_FILTER_EGRESS,
                    extra_data={'endpoint_name_regexp': r".*PSX Sound.*"})

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

    class DummyFrankenrouter():  # pylint: disable=too-few-public-methods
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the instance."""
            self.upstream = None
            self.clients = {}
            self.last_bang = 0.0
            self.variables = TestRules.DummyVariables()
            self.cache = TestRules.DummyCache()
            self.last_load1 = 0.0
            self.frdp_version = 1

        def variable_stats_add(self, *args):
            """Add dummy stats."""

        def connection_state_changed(self, *args):
            """Implement Dummy display."""

    class DummyConnection():  # pylint: disable=too-few-public-methods,too-many-instance-attributes
        """Implement small parts of the router for unit testing."""

        def __init__(self):
            """Initialize the connection."""
            self.ping_sent = 0.0
            self.frdp_ping_rtts = []
            self.simulator_name = 'UnknownSim'
            self.router_name = 'UnknownRouter'
            self.display_name = 'UnknownDisplay'
            self.display_name_source = 'UnknownSource'
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
        router.upstream.ping_sent = time.perf_counter()
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

        # BANG
        (action, code, *_) = rules.route("addon=FRANKENROUTER:1:BANG", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.FRDP_BANG)
        self.assertTrue(router.last_bang > 0.0)

        # PING from client
        testpeer.ping_sent = time.perf_counter()
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
            "name": "PSX Sounds"
        })
        (action, code, *_) = rules.route(
            f"addon=FRANKENROUTER:1:CLIENTINFO:{json_payload}", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.FRDP_CLIENTINFO)
        self.assertEqual(testpeer.display_name, 'PSX Sounds')
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
        self.assertEqual(testpeer.display_name, 'somename:or:other')
        self.assertEqual(testpeer.display_name_source, 'name message')

        # known addon sending name=
        (action, code, *_) = rules.route("name=BACARS:BA ACARS Simulation", testpeer)
        self.assertEqual(action, RulesAction.DROP)
        self.assertEqual(code, RulesCode.NAME_LEARNED)
        self.assertEqual(testpeer.display_name, 'BACARS')
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

        # demand from client
        (action, code, *_) = rules.route("demand=Qs325", testpeer)
        self.assertEqual(action, RulesAction.UPSTREAM_ONLY)
        self.assertEqual(code, RulesCode.DEMAND)
        self.assertTrue('Qs325' in testpeer.demands)

        # demand from upstream
        (action, code, *_) = rules.route("demand=Qs325", router.upstream)
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
        testpeer.connected_at = time.perf_counter() - 20.0
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

        # Qi191 to PSX Sounds from upstream near-bang
        router.last_bang = time.perf_counter()
        (action, code, _, extra_data) = rules.route("Qi191=trigger for sound", router.upstream)
        self.assertEqual(action, RulesAction.FILTER)
        self.assertEqual(code, RulesCode.KEYVALUE_FILTER_EGRESS)
        self.assertTrue('endpoint_name_regexp' in extra_data)

        # Qi191 to PSX Sounds from upstream far from bang
        router.last_bang = 0.0
        (action, code, _, extra_data) = rules.route("Qi191=trigger for sound", router.upstream)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)
        self.assertIsNone(extra_data)

        # Qi191 to PSX Sounds from client near-bang
        router.last_bang = time.perf_counter()
        (action, code, _, extra_data) = rules.route("Qi191=trigger for sound", testpeer)
        self.assertEqual(action, RulesAction.NORMAL)
        self.assertEqual(code, RulesCode.KEYVALUE_NORMAL)
        self.assertIsNone(extra_data)

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
