"""Read the PSX Variables.txt definition format."""
import logging
import re
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
}


class VariablesException(Exception):
    """A custom exception."""


class Variables():  # pylint: disable=too-few-public-methods
    """A definition of PSX network variables."""

    def __init__(self, vfilepath=None, vfiledata=None):
        """Initialize the instance."""
        self.logger = logging.getLogger(__name__)
        self.variables = {}

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
        elif vfiledata is not None:
            self._init_from_data(vfiledata)

    def keywords_with_mode(self, mode):
        """Return list of keywords that have this network mode."""
        results = []
        for keyword, props in self.variables.items():
            if props['mode'] == mode:
                results.append(keyword)
            elif 'additional_modes' in props:
                if mode in props['additional_modes']:
                    results.append(keyword)
        return results

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
            if keyword[1] in ['h', 's', 'd', 'i']:
                return True
        elif keyword[0] == 'L':
            if keyword[1] in ['s', 'i', 'h']:
                return True
        elif keyword in [
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
        ]:
            return True
        return False

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
            Variables(vfiledata=self.bad_data_1)
        with self.assertRaises(AssertionError):
            Variables(vfiledata=self.bad_data_2)

    def test_good_input(self):
        """A few tests with valid input data."""
        me = Variables(vfiledata=self.good_data_1)
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

        me = Variables(vfiledata=self.good_data_2)
        self.assertEqual(len(me.variables.keys()), 10)
        self.assertEqual(me.keywords_with_mode("DELTA"), ['Qs468'])
        self.assertEqual(me.keywords_with_mode("START"), ['Qs493'])
        self.assertEqual(me.keywords_with_mode("NOLONG"), ['Qs411'])
        self.assertEqual(me.keywords_with_mode("ECON"),
                         ['Qs0', 'Qs1', 'Qs2', 'Qs3', 'Qs6', 'Qs7', 'Qs8', 'Qs493', 'Qs411'])

    def test_keyword(self):
        """Test the PSX keyword check."""
        me = Variables()
        self.assertEqual(me.is_psx_keyword("Gurka"), False)
        self.assertEqual(me.is_psx_keyword("demand"), True)
        self.assertEqual(me.is_psx_keyword("Qs123"), True)

    def test_keyword_sort(self):
        """Test the PSX keyword sort."""
        me = Variables()
        self.assertEqual(
            me.sort_psx_keywords(["Qs1", "Qs100", "Qs999", "Qs42"]),
            ["Qs1", "Qs42", "Qs100", "Qs999"])


if __name__ == '__main__':
    unittest.main()
