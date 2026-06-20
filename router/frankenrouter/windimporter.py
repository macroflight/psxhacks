"""Parse DLH-format OFP wind data into PSX Format D corridor text.

The public API consists of two functions:

  parse_ofp(ofp_text)   → multi-line Format D text (name / lat / lon / wind rows)
  to_psx_corridor(text) → PSX Qs498 wire value built from parse_ofp output

Climb, descent, and transition blocks (T.O.C, T.O.D …) are excluded from the
output.  For each remaining group N_OUTPUT_LEVELS wind levels nearest to the
expected cruise FL are selected.
"""

import os
import re
import unittest

__all__ = ['parse_ofp', 'to_psx_corridor', 'WindImporterException']

COL_WIDTH = 17
N_OUTPUT_LEVELS = 5


class WindImporterException(Exception):
    """Raised when an OFP cannot be parsed."""


class _OFPParser:
    """Internal parser — not part of the public API."""

    _lat_row_re = re.compile(r'^[NS]\d{4}\.')

    def extract_wind_section(self, ofp_text):
        """Extract the waypoint wind table from the full OFP text."""
        lines = ofp_text.splitlines()
        start = end = None
        for i, line in enumerate(lines):
            if 'WAYPOINT-LIST FOR OFP' in line and start is None:
                start = i + 2  # skip header + separator line
            if start is not None and 'Crew Information' in line:
                end = i
                break
        if start is None:
            raise WindImporterException("Wind section (WAYPOINT-LIST FOR OFP) not found")
        return '\n'.join(lines[start:end] if end else lines[start:])

    def parse_leg_table(self, ofp_text):  # pylint: disable=too-many-locals,too-many-branches
        """Return [(name, raw_lvl_or_None), ...] in route order.

        raw_lvl < 1000  — flight level in hundreds of feet (e.g. 310 = FL310).
        raw_lvl >= 1000 — metric altitude in decametres (e.g. 1130 = 11300 m ≈ FL371).
        """
        lines = ofp_text.splitlines()
        start = end = None
        for i, line in enumerate(lines):
            if 'TIME  AWY' in line and start is None:
                start = i
            if start is not None and 'ARRIVAL ATIS' in line:
                end = i
                break
        if start is None:
            return []

        sep_re = re.compile(r'^-{20,}')
        lvl_re = re.compile(r'^\.\.\.\.\s+\.\.\.\.\s+\.\.\.\.\s+(\d{3,4})')

        entries = []
        block = []
        for line in lines[start:end]:  # pylint: disable=too-many-nested-blocks
            if sep_re.match(line):
                if block:
                    wp_name = raw_lvl = None
                    for bline in block:
                        if (len(bline) > 6 and
                                (bline[:4].isdigit() or bline[:4] == '....') and
                                bline[6:7] not in ('.', ' ')):
                            rest = bline[6:]
                            parts = re.split(r'\s{2,}', rest)
                            if parts and parts[0].strip():
                                wp_name = parts[0].strip()
                        m = lvl_re.match(bline)
                        if m:
                            raw_lvl = int(m.group(1))
                    if wp_name:
                        entries.append((wp_name, raw_lvl))
                block = []
            else:
                block.append(line)
        return entries

    def convert_lvl_to_fl(self, raw_lvl):
        """Convert a LVL column value to a flight level (hundreds of feet)."""
        if raw_lvl < 1000:
            return raw_lvl
        return round(raw_lvl * 10 / 0.3048 / 100)

    def annotate_cruise_fl(self, leg_entries, groups):
        """Set block['cruise_fl'] on every block using FL data from the leg table."""
        leg_fl = {}
        current_fl = None
        for name, raw_lvl in leg_entries:
            if raw_lvl is not None:
                current_fl = self.convert_lvl_to_fl(raw_lvl)
            leg_fl[name] = current_fl

        current_fl = None
        for group in groups:
            for block in group:
                name = block['name']
                if name and name in leg_fl and leg_fl[name] is not None:
                    current_fl = leg_fl[name]
                block['cruise_fl'] = current_fl

    def parse_wind(self, s):
        """Parse 'FFF DDD/SSS ±TT' -> (fl, dir, spd, temp) or None."""
        m = re.match(r'(\d{3})\s+(\d{3})/(\d{3})\s+([+-]\d+)', s.strip())
        if not m:
            return None
        return int(m[1]), int(m[2]), int(m[3]), int(m[4])

    def transform_name(self, name):
        """T.O.C -> 'T O C', ABT.O.D -> 'ABTOD'."""
        if '.' not in name:
            return name
        parts = name.split('.')
        if all(len(p) == 1 for p in parts):
            return ' '.join(parts)
        return name.replace('.', '')

    def split_row(self, line):
        """Split a line into 4 columns of COL_WIDTH characters."""
        return [line[i * COL_WIDTH:(i + 1) * COL_WIDTH].rstrip() for i in range(4)]

    def parse_groups(self, text):  # pylint: disable=too-many-locals,too-many-branches
        """Return list of groups; each group is a list of 4 block dicts."""
        raw_groups, cur = [], []
        for line in text.splitlines():
            if line.strip():
                cur.append(line)
            elif cur:
                raw_groups.append(cur)
                cur = []
        if cur:
            raw_groups.append(cur)

        groups = []
        for raw in raw_groups:
            first_cols = [c.strip() for c in self.split_row(raw[0])]
            non_empty = [c for c in first_cols if c]
            if non_empty and all(self._lat_row_re.match(c) for c in non_empty):
                raw = [''] + raw

            names = [c.strip() for c in self.split_row(raw[0])]
            blocks = []
            for col, name in enumerate(names):
                block = {'name': self.transform_name(name), 'winds': {}}
                if name in ('CLIMB', 'DESCENT'):
                    block['type'] = name.lower()
                    for row in raw[1:]:
                        entry = self.parse_wind(self.split_row(row)[col])
                        if entry:
                            fl, d, s, t = entry
                            block['winds'][fl] = (d, s, t)
                else:
                    block['type'] = 'regular'
                    block['lat'] = self.split_row(raw[1])[col].strip() if len(raw) > 1 else ''
                    block['lon'] = self.split_row(raw[2])[col].strip() if len(raw) > 2 else ''
                    for row in raw[3:]:
                        entry = self.parse_wind(self.split_row(row)[col])
                        if entry:
                            fl, d, s, t = entry
                            block['winds'][fl] = (d, s, t)
                blocks.append(block)
            groups.append(blocks)
        return groups

    def _is_transition(self, block):
        """Return True for T.O.C, T.O.D and similar dot-abbreviation waypoints."""
        parts = block['name'].split()
        return len(parts) > 1 and all(len(p) == 1 for p in parts)

    def select_winds(self, block):
        """Return list of (fl, dir, spd, temp) tuples for output, high to low FL."""
        winds = block['winds']
        available = sorted(winds.keys(), reverse=True)

        cruise_fl = block.get('cruise_fl')
        if cruise_fl is not None:
            ranked = sorted(available, key=lambda fl: abs(fl - cruise_fl))
            chosen = sorted(ranked[:N_OUTPUT_LEVELS], reverse=True)
        else:
            chosen = available[:N_OUTPUT_LEVELS]

        return [(fl, *winds[fl]) for fl in chosen]

    def fmt_wind(self, fl, d, s, t):
        """Format a single wind entry."""
        sign = '+' if t >= 0 else '-'
        return f'{fl:03d} {d:03d}/{s:03d} {sign}{abs(t):02d}'

    def render(self, groups):  # pylint: disable=too-many-locals
        """Render groups into PSX Format D corridor text (name / lat / lon / wind rows).

        Climb, descent, and transition-point blocks (T.O.C, T.O.D …) are
        excluded.  Each surviving group is separated by a blank line.
        """
        out = []
        for group in groups:
            blocks = [b for b in group
                      if b['type'] == 'regular' and b.get('lat') and b.get('lon') and
                      not self._is_transition(b)]
            if not blocks:
                continue

            names = [b['name'] for b in blocks]
            lats = [b['lat'] for b in blocks]
            lons = [b['lon'] for b in blocks]
            n = len(blocks)

            out.append(''.join(f'{name:<{COL_WIDTH}}' for name in names[:-1]) + names[-1])
            out.append(' '.join(lats))
            out.append(' '.join(lons))

            cols = [self.select_winds(b) for b in blocks]
            for i in range(max(len(c) for c in cols)):
                parts = []
                for j, col in enumerate(cols):
                    entry = self.fmt_wind(*col[i]) if i < len(col) else ''
                    parts.append(f'{entry:<{COL_WIDTH}}' if j < n - 1 else entry)
                out.append(''.join(parts))

            out.append('')
        return '\n'.join(out)


def parse_ofp(ofp_text: str) -> str:
    """Parse a DLH OFP string and return wind data in PSX Format D corridor text.

    Raises WindImporterException if the OFP cannot be parsed or contains no
    usable wind data.
    """
    parser = _OFPParser()
    leg_entries = parser.parse_leg_table(ofp_text)
    wind_text = parser.extract_wind_section(ofp_text)
    if not wind_text.strip():
        raise WindImporterException("Wind section found but contains no data")
    groups = parser.parse_groups(wind_text)
    if not groups:
        raise WindImporterException("No wind groups found in wind section")
    parser.annotate_cruise_fl(leg_entries, groups)
    return parser.render(groups)


def to_psx_corridor(wind_data: str) -> str:
    """Convert parse_ofp output to the PSX Qs498 wire format.

    Each non-blank line of the multi-line wind_data becomes one caret-
    delimited section; the result is prefixed with '#' as PSX expects.
    """
    lines = [ln for ln in wind_data.splitlines() if ln.strip()]
    return '#' + '^'.join(lines) + '^'


_TESTDATA = os.path.join(os.path.dirname(os.path.realpath(__file__)),
                         'testdata', 'windimporter')

_OFP_FIXTURES = [
    'dlh-ofp',
    'dlh-ofp-2',
    'dlh-ofp-3',
    'dlh-ofp-4',
    'dlh-ofp-5',
]


class TestWindImporter(unittest.TestCase):
    """Regression tests for parse_ofp against stored OFP fixtures."""

    def test_ofp_golden(self):
        """parse_ofp output matches the stored expected output for all fixtures."""
        for stem in _OFP_FIXTURES:
            with self.subTest(fixture=stem):
                with open(os.path.join(_TESTDATA, stem + '.txt'), encoding='utf-8') as f:
                    ofp_text = f.read()
                with open(os.path.join(_TESTDATA, stem + '-expected.txt'), encoding='utf-8') as f:
                    expected = f.read()
                self.assertEqual(parse_ofp(ofp_text), expected)
