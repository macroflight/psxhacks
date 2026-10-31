"""PSX navdata version: read psx_navdata.json, compare across routers.

The start scripts (start_scripts/functions.ps1, Write-PsxNavDataFile) write
psx_navdata.json to the router's working directory before PSX starts:

    {"airac": "2610", "directory": "2610-Navigraph"}

"directory" is the navdata folder in Aerowinx/Navigation/-Custom/List
that PSX uses; "airac" is its leading digits. Each router sends the
directory name in FRDP ROUTERINFO ("navdata"); the master router
compares them, since PSX clients with different navdata can fight over
e.g. ILS frequencies, and warns if a newer AIRAC cycle is in effect.
"""
import datetime
import json
import os
import re
import tempfile
import unittest

NAVDATA_FILE = "psx_navdata.json"
UNKNOWN = "unknown"

# AIRAC cycles are exactly 28 days apart; cycle 2001 took effect on
# Thursday 2020-01-02. A cycle is named YYNN: the year it takes effect
# in and its number within that year (01-13, sometimes 14).
AIRAC_ANCHOR = datetime.date(2020, 1, 2)
AIRAC_DAYS = 28


def current_airac(today=None):
    """Return (cycle name, effective date) of the AIRAC cycle in effect on today (UTC)."""
    if today is None:
        today = datetime.datetime.now(datetime.timezone.utc).date()
    effective = AIRAC_ANCHOR + datetime.timedelta(
        days=AIRAC_DAYS * ((today - AIRAC_ANCHOR).days // AIRAC_DAYS))
    # First cycle taking effect in the same year (ceiling division)
    jan1 = datetime.date(effective.year, 1, 1)
    first = AIRAC_ANCHOR + datetime.timedelta(
        days=AIRAC_DAYS * -(-(jan1 - AIRAC_ANCHOR).days // AIRAC_DAYS))
    number = (effective - first).days // AIRAC_DAYS + 1
    return f"{effective.year % 100:02d}{number:02d}", effective


def airac_of(navdata):
    """Return the AIRAC cycle (the leading 4 digits) of a navdata directory name, or None."""
    m = re.match(r'(\d{4})', navdata or "")
    return m.group(1) if m else None


def load_navdata(path=NAVDATA_FILE):
    """Return (navdata, problem) from a psx_navdata.json file.

    navdata is the navdata directory name (e.g. "2610-Navigraph"), or
    UNKNOWN if the file is missing or can't be used; problem is then a
    message to log, else None.
    """
    try:
        with open(path, encoding='utf-8-sig') as f:
            data = json.load(f)
    except FileNotFoundError:
        return UNKNOWN, f"{os.path.abspath(path)} not found, navdata version unknown"
    except (OSError, ValueError) as exc:
        return UNKNOWN, f"Can't read {os.path.abspath(path)}: {exc}"
    directory = data.get('directory') if isinstance(data, dict) else None
    if not isinstance(directory, str) or not directory.strip():
        return UNKNOWN, f"No navdata directory in {os.path.abspath(path)}"
    return directory.strip(), None


def check_navdata(routers, today=None):
    """Compare navdata across routers.

    routers is a list of (router display name, navdata) tuples, where
    navdata is None or UNKNOWN when the router didn't report it (e.g.
    an older version, or a sim not started by the start scripts).
    Returns (errors, warnings, unknown): a mismatch between the known
    versions is an error, one older than the AIRAC cycle in effect
    today a warning; unknown lists the routers whose navdata is
    unknown, which is normal (for the log only).
    """
    errors = []
    warnings = []
    unknown = []
    known = {}
    for name, navdata in routers:
        if navdata in (None, UNKNOWN):
            unknown.append(name)
        else:
            known.setdefault(navdata, []).append(name)
    if len(known) > 1:
        groups = [f"{navdata} ({', '.join(names)})" for navdata, names in sorted(known.items())]
        errors.append("Navdata mismatch: " + "; ".join(groups))
    cycle, effective = current_airac(today)
    for navdata, names in sorted(known.items()):
        airac = airac_of(navdata)
        if airac is not None and airac < cycle:
            warnings.append(
                f"Navdata {navdata} ({', '.join(names)}) is out of date:"
                f" AIRAC {cycle} has been in effect since {effective.isoformat()}")
    return errors, warnings, unknown


class TestNavdata(unittest.TestCase):
    """Test navdata loading and checks."""

    def _write(self, tmpdir, text):
        path = os.path.join(tmpdir, NAVDATA_FILE)
        with open(path, 'w', encoding='utf-8') as f:
            f.write(text)
        return path

    def test_load(self):
        """Valid, missing and broken files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, '{"airac": "2610", "directory": "2610-Navigraph"}')
            self.assertEqual(load_navdata(path), ("2610-Navigraph", None))
            navdata, problem = load_navdata(os.path.join(tmpdir, "missing.json"))
            self.assertEqual(navdata, UNKNOWN)
            self.assertIn("not found", problem)
            for text in ('{"airac": "2610"', '[]', '{"directory": ""}', '{"directory": 2610}'):
                path = self._write(tmpdir, text)
                navdata, problem = load_navdata(path)
                self.assertEqual(navdata, UNKNOWN, text)
                self.assertIsNotNone(problem, text)

    def test_load_bom(self):
        """A UTF-8 BOM (Windows PowerShell 5.1 adds one unless told otherwise) is accepted."""
        with tempfile.TemporaryDirectory() as tmpdir:
            path = self._write(tmpdir, '\ufeff{"airac": "2611", "directory": "2611-Navigraph"}')
            self.assertEqual(load_navdata(path), ("2611-Navigraph", None))

    def test_current_airac(self):
        """Known AIRAC effective dates, including a 14-cycle year."""
        date = datetime.date
        for today, expected in (
                (date(2020, 1, 2), ("2001", date(2020, 1, 2))),
                (date(2020, 12, 31), ("2014", date(2020, 12, 31))),
                (date(2021, 1, 27), ("2014", date(2020, 12, 31))),
                (date(2021, 1, 28), ("2101", date(2021, 1, 28))),
                (date(2026, 1, 22), ("2601", date(2026, 1, 22))),
                (date(2026, 10, 28), ("2610", date(2026, 10, 1))),
                (date(2026, 10, 29), ("2611", date(2026, 10, 29)))):
            self.assertEqual(current_airac(today), expected, today)

    def test_check(self):
        """Mismatch is an error, out of date a warning, unknown neither."""
        today = datetime.date(2026, 10, 15)
        self.assertEqual(check_navdata([("a", "2610-X"), ("b", "2610-X")], today), ([], [], []))
        errors, warnings, _ = check_navdata(
            [("a", "2610-X"), ("b", "2611-X"), ("c", "2610-X")], today)
        self.assertEqual(errors, ["Navdata mismatch: 2610-X (a, c); 2611-X (b)"])
        self.assertEqual(warnings, [])
        errors, warnings, _ = check_navdata([("a", "2609-X"), ("b", "2609-X")], today)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, ["Navdata 2609-X (a, b) is out of date:"
                                    " AIRAC 2610 has been in effect since 2026-10-01"])
        self.assertEqual(
            check_navdata([("a", "2610-X"), ("b", None), ("c", UNKNOWN)], today),
            ([], [], ["b", "c"]))


if __name__ == '__main__':
    unittest.main()
