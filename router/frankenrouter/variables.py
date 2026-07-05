"""Read the PSX Variables.txt definition format."""
import logging
import re
import traceback
import unittest
import urllib.request

NETWORK_MODES = [
    'ECON',
    'DELTA',
    'START',
    'XECON',
    'DEMAND',
    'XDELTA',
    'MCPMOM',
    'BIGMOM',
    'GUAMOM4',
    'GUAMOM2',
    'CDUKEYB',
    'RCP',
    'ACP',
    'MIXED',
]

# Qh variables to NOT log as sim events (noisy rotary encoders and brightness dimmers).
# All other Qh variables are logged when they change from a downstream client.
SIMEVENTS_QH_IGNORE = frozenset({
    # XDELTA-mode continuous IAS bug encoders
    'Qh0', 'Qh1', 'Qh2',
    # Overhead and glareshield brightness dimmers (Max=4713)
    'Qh7', 'Qh8', 'Qh9', 'Qh10', 'Qh11', 'Qh12',
    # LCP dimmer controls (Max=4713)
    'Qh84', 'Qh85', 'Qh86', 'Qh87', 'Qh88', 'Qh89', 'Qh90',
    'Qh95', 'Qh96', 'Qh97', 'Qh98', 'Qh99', 'Qh100', 'Qh101',
    # CDU keyboard key presses (per-keypress events, not useful as sim events)
    'Qh401', 'Qh402', 'Qh403',
    # EICAS and CDU brightness
    'Qh139', 'Qh140', 'Qh141', 'Qh404', 'Qh405', 'Qh406',
    # ECP baro/mins rotary encoders (DELTA mode, continuous)
    'Qh28', 'Qh30', 'Qh49', 'Qh51',
    # Standby baro rotary encoder
    'Qh136',
    # MCP speed/heading/VS/altitude knob increments (DELTA mode, very frequent)
    'Qh77', 'Qh78', 'Qh79', 'Qh80',
    # Rudder trim encoder
    'Qh416',
    # Tiller — continuous axis, not meaningful as a discrete event
    'Qh426',
    # SpdBrkLever — handled with state-based logic (see spdbrk_lever_state)
    'Qh388',
})

# Qs/Qi/Qd variables to INCLUDE for sim event logging (empty whitelist by default;
# add specific keywords here as needed).
SIMEVENTS_QSI_INCLUDE = frozenset({
})

# MCP window value variables tracked with a 5-second stability debounce — an event
# is only generated when the previous value has been unchanged for more than 5 seconds.
SIMEVENTS_MCP_WINDOW_KEYS = frozenset({
    'Qi32',  # McpWdoSpd - IAS/MACH window
    'Qi33',  # McpWdoHdg - heading window
    'Qi34',  # McpWdoVs  - vertical speed window
    'Qi35',  # McpWdoAlt - altitude window
})

# PnfMode (Qi217) bitmask — known bit positions and their meaning.
PNF_MODE_BITS = {
    0: 'right seat',     # mask 1
    1: 'left seat',      # mask 2
    2: 'callouts',       # mask 4
    4: 'silent tasks',   # mask 16
    8: 'S/C alt',        # mask 256
}


def pnf_mode_labels(value):
    """Return sorted list of active PNF mode feature labels for an integer bitmask."""
    return [label for bit, label in sorted(PNF_MODE_BITS.items()) if value & (1 << bit)]


# SpdBrkLever (Qh388) detent positions: 0=stowed, 1-60=armed, ≥61=opened.
# The ARM position is set to 41 by frankenusb; 61 is the start of the flight range.
_SPDBRK_ARMED_MAX = 60
_SPDBRK_FLIGHT_MIN = 61


def spdbrk_lever_state(value):
    """Return 'stowed', 'armed', or 'opened' for an integer SpdBrkLever value."""
    if value <= 0:
        return 'stowed'
    if value <= _SPDBRK_ARMED_MAX:
        return 'armed'
    return 'opened'


# AFDS (Qs434) FMA mode number → display name.
# Field 2 (pitch) can be negative when pitchFault is set; abs() is applied before lookup.
# -29 appears in armed fields when VNAV is unavailable.
AFDS_MODE_NAMES = {
    -29: '',
    0: '',
    1: 'ATT',
    2: 'HDG HOLD',
    3: 'HDG SEL',
    4: 'LNAV',
    5: 'LOC',
    6: 'ROLLOUT',
    7: 'TO/GA',
    8: 'TO/GA',
    9: 'ALT',
    10: 'FLARE',
    11: 'FLCH SPD',
    12: 'G/S',
    13: 'V/S',
    14: 'VNAV ALT',
    15: 'VNAV PTH',
    16: 'VNAV SPD',
    17: 'VNAV',
    18: 'IDLE',
    19: 'SPD',
    20: 'THR',
    21: 'THR HOLD',
    22: 'THR REF',
    23: 'NO AUTOLAND',
    24: 'LAND 2',
    25: 'LAND 3',
    26: 'CMD',
    27: 'F/D',
    28: 'TEST',
    29: 'VNAV FAIL',
    30: 'VNAV OFF',
}


def parse_afds_fma(afds_value):
    """Return (thr, roll, pitch, roll_armed, pitch_armed) mode name strings from Qs434.

    Returns None if the value cannot be parsed.
    Negative field 2 (pitchFault) is resolved via abs() before lookup.
    """
    try:
        fields = afds_value.split(';')

        def _name(raw):
            n = int(raw)
            return AFDS_MODE_NAMES.get(n, AFDS_MODE_NAMES.get(abs(n), str(n)))

        return (
            _name(fields[0]),   # throttleMode
            _name(fields[1]),   # rollEngaged
            _name(fields[2]),   # pitchEngaged (may be negative on fault)
            _name(fields[3]),   # rollArmed
            _name(fields[4]),   # pitchArmed
        )
    except (ValueError, IndexError):
        return None


ADDITIONAL_MODES = {
    # https://aerowinx.com/board/index.php/topic,7751.0.html - Qs493 and Qi208
    # also behave as ECON, i.e they are sent to the network when changed.
    'Qs493': ['ECON'],
    'Qi208': ['ECON'],
    # NOLONG is perhaps not stricly a network mode, but let's put it here for now
    "Qs375": ['NOLONG'],
    "Qs376": ['NOLONG'],
    "Qs377": ['NOLONG'],
    "Qs407": ['NOLONG'],
    "Qs408": ['NOLONG'],
    "Qs409": ['NOLONG'],
    "Qs410": ['NOLONG'],
    "Qs411": ['NOLONG'],
    "Qs412": ['NOLONG'],
    # Qs119 is the printer message. We want to inhibit this in the
    # client welcome since we don't want the latest message printed
    # just because we reconnect the printer client
    "Qs119": ['INIT_EMPTY'],
    # Qi262 seems to be sent during client welcome despite being a
    # DELTA variable
    "Qi262": ['ECON'],
}


class VariablesException(Exception):
    """A custom exception."""


class Variables():  # pylint: disable=too-few-public-methods
    """A definition of PSX network variables."""

    def __init__(self, config, vfilepath=None, vfiledata=None):
        """Initialize the instance."""
        self.logger = logging.getLogger(__name__)
        self.variables = {}
        self.config = config

        # Since the mode of keywords does not change while the router
        # runs, and the lookup function we used earlier was very
        # expensive, we have switched to a cache created on startup.
        self._mode_cache = {}

        if vfilepath is not None:
            # Read the standard Variables.txt file from the PSX
            # install (Developers/Variables.txt) or the Forum.
            try:
                with open(vfilepath, 'r', encoding='utf-8') as vfile:
                    self._init_from_data(vfile.read())
            except FileNotFoundError:
                self.logger.warning(
                    "%s not found, trying to download from Aerowinx",
                    vfilepath)
                try:
                    urllib.request.urlretrieve(
                        "https://aerowinx.com/assets/networkers/Variables.txt",
                        "Variables.txt")
                except urllib.error.URLError as exc2:
                    raise VariablesException(
                        "Failed to download Variables.txt from Aerowinx") from exc2
                self.logger.info("Downloaded Variables.txt from Aerowinx")
                try:
                    with open("Variables.txt", 'r', encoding='utf-8') as vfile:
                        self._init_from_data(vfile.read())
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    raise SystemExit(
                        "Downloaded Variables.txt from Aerowinx but failed to read it") from exc
            except Exception:  # pylint: disable=broad-exception-caught
                msg = f"Unhandled exception: {traceback.format_exc()}"
                if self.config.identity.stop_minded:
                    raise SystemExit(f"{msg}\nRouter is stop-minded so shutting down now")  # pylint: disable=raise-missing-from
                self.logger.critical("%s\nRouter is go-minded so trying to continue", msg)
        elif vfiledata is not None:
            self._init_from_data(vfiledata)

    def keywords_with_mode(self, mode):
        """Return list of keywords that have this network mode."""
        if mode not in self._mode_cache:
            # build cache entry (only on first call per mode)
            results = set()
            for keyword, props in self.variables.items():
                if props['mode'] == mode or (
                        'additional_modes' in props and mode in props['additional_modes']):
                    results.add(keyword)
            self._mode_cache[mode] = results
        return self._mode_cache[mode]

    def _init_from_data(self, data):  # pylint: disable=too-many-branches
        """Initialize from data."""
        for line in data.splitlines():
            thiskey = None
            line = line.rstrip()
            if line == '' or line.startswith('['):
                continue
            for elem in line.split(';'):
                elem = elem.strip()
                if elem == '':
                    continue
                try:
                    (key, value) = elem.split('=')
                except ValueError as exc:
                    raise VariablesException(f"Invalid line: {line}") from exc
                if key.startswith('Q'):
                    value = value.replace('"', '')
                    if key in self.variables:
                        raise VariablesException(f"Duplicate name {key} in data")
                    self.variables[key] = {}
                    thiskey = key
                    self.variables[key]['name'] = value
                else:
                    assert thiskey in self.variables  # should not happen
                    if key == 'Mode':
                        assert value in NETWORK_MODES, f"unknown variable type {value}"
                        self.variables[thiskey]['mode'] = value
                        if thiskey in ADDITIONAL_MODES:
                            self.variables[thiskey]['additional_modes'] = (
                                ADDITIONAL_MODES[thiskey])
                    if key == 'Min':
                        try:
                            self.variables[thiskey]['min'] = int(value)
                        except ValueError as exc:
                            raise VariablesException(f"Invalid type in {line}") from exc
                    if key == 'Max':
                        try:
                            self.variables[thiskey]['max'] = int(value)
                        except ValueError as exc:
                            raise VariablesException(f"Invalid type in {line}") from exc

        for key, value in self.variables.items():
            assert 'mode' in value, f"invalid data, Mode missing for {key}"
            assert 'min' in value, f"invalid data, Min missing for {key}"
            assert 'max' in value, f"invalid data, Max missing for {key}"

    def is_psx_keyword(self, keyword):
        """Return true of keyword is a normal PSX network keyword.

        Since we call this for every received message, avoiding
        regexps if possible.
        """
        if len(keyword) < 2:
            return False
        if keyword[0] == 'Q':
            if keyword[1] in {'h', 's', 'd', 'i'}:
                return True
        elif keyword[0] == 'L':
            if keyword[1] in {'s', 'i', 'h'}:
                return True
        elif keyword in {
                'exit',
                'cduC',
                'cduL',
                'cduR',
                'bang',
                'name',
                'id',
                'start',
                'lexicon',
                'again',
                'gid',
                'version',
                'layout',
                'metar',
                'demand',
                'load1',
                'load2',
                'load3',
                'keepalive',  # not PSX, but SimStack Switch sends this often
        }:
            return True
        return False

    def get_keyword_for_name(self, name):
        """Return the Q-code keyword for a PSX variable name, or None if not found."""
        for keyword, props in self.variables.items():
            if props.get('name') == name:
                return keyword
        return None

    def keywords_for_simevents(self):
        """Return frozenset of keywords to monitor for sim event logging.

        Includes all Qh variables not in SIMEVENTS_QH_IGNORE, plus any
        Qs/Qi/Qd keywords listed in SIMEVENTS_QSI_INCLUDE.
        """
        result = set()
        for key in self.variables:
            if key.startswith('Qh') and key not in SIMEVENTS_QH_IGNORE:
                result.add(key)
            elif key in SIMEVENTS_QSI_INCLUDE:
                result.add(key)
        return frozenset(result)

    def get_variable_name(self, keyword):
        """Return human-readable name for a keyword, or the keyword itself."""
        return self.variables.get(keyword, {}).get('name', keyword)

    def sort_psx_keywords(self, input_list):
        """Sort PSX keywords numerically in the order PSX outputs them."""
        def alphanum_key(key):
            return [int(s) if s.isdigit() else s.lower() for s in re.split("([0-9]+)", key)]
        return sorted(input_list, key=alphanum_key)


class TestVariablesParser(unittest.TestCase):
    """Basic test cases for the module."""

    bad_data_1 = """
INVALID FILE
"""

    bad_data_2 = """
Qs36="P62H"; Mode=ECON; Min=9; Max=9;
Qs37="P62J"; Mode=ECON; Min=9;
Qs38="P62K"; Mode=ECON; Min=9; Max=9;
"""

    good_data_1 = """
Qi224="AtcPhase"; Mode=ECON; Min=0; Max=99;
Qi225="CrashInhib"; Mode=ECON; Min=0; Max=2147483647;
"""

    good_data_2 = """
[Aerowinx Precision Simulator - Variables]
[Version 10.180]

[Qs Types (strings)]
Qs0="CfgRego"; Mode=ECON; Min=0; Max=8;
Qs1="CfgSelcal"; Mode=ECON; Min=0; Max=8;
Qs2="CfgCoId"; Mode=ECON; Min=2; Max=2;
Qs3="CfgDragFf"; Mode=ECON; Min=3; Max=7;
Qs6="P71C"; Mode=ECON; Min=13; Max=13;
Qs7="P71D"; Mode=ECON; Min=13; Max=13;
Qs8="P71E"; Mode=ECON; Min=13; Max=13;
Qs468="FansDnResp"; Mode=DELTA; Min=0; Max=500;
Qs493="DestRwy"; Mode=START; Min=0; Max=3;
Qs411="CduRteCa"; Mode=ECON; Min=15; Max=50000;
"""

    def test_bad_input(self):
        """A few tests with invalid input data."""
        with self.assertRaises(VariablesException):
            Variables(None, vfiledata=self.bad_data_1)
        with self.assertRaises(AssertionError):
            Variables(None, vfiledata=self.bad_data_2)

    def test_good_input(self):
        """A few tests with valid input data."""
        me = Variables(None, vfiledata=self.good_data_1)
        self.assertEqual(
            me.variables,
            {
                'Qi224': {
                    'max': 99,
                    'min': 0,
                    'mode': 'ECON',
                    'name': 'AtcPhase'
                },
                'Qi225': {
                    'max': 2147483647,
                    'min': 0,
                    'mode': 'ECON',
                    'name':
                    'CrashInhib'
                }
            }
        )

        me = Variables(None, vfiledata=self.good_data_2)
        self.assertEqual(len(me.variables.keys()), 10)
        self.assertEqual(me.keywords_with_mode("DELTA"), {'Qs468'})
        self.assertEqual(me.keywords_with_mode("START"), {'Qs493'})
        self.assertEqual(me.keywords_with_mode("NOLONG"), {'Qs411'})
        self.assertEqual(me.keywords_with_mode("ECON"),
                         {'Qs0', 'Qs1', 'Qs2', 'Qs3', 'Qs6', 'Qs7', 'Qs8', 'Qs493', 'Qs411'})

    def test_keyword(self):
        """Test the PSX keyword check."""
        me = Variables(None)
        self.assertEqual(me.is_psx_keyword("Gurka"), False)
        self.assertEqual(me.is_psx_keyword("demand"), True)
        self.assertEqual(me.is_psx_keyword("Qs123"), True)

    def test_keyword_sort(self):
        """Test the PSX keyword sort."""
        me = Variables(None)
        self.assertEqual(
            me.sort_psx_keywords(["Qs1", "Qs100", "Qs999", "Qs42"]),
            ["Qs1", "Qs42", "Qs100", "Qs999"])


if __name__ == '__main__':
    unittest.main()
