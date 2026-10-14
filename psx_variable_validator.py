"""Check whether a PSX network variable's value is valid.

There are two very different sources of knowledge behind this module, and
they are deliberately treated differently:

- **`Variables.txt`** (path configurable, see `DEFAULT_VARIABLES_FILE`) is
  simple, mechanical, and changes with every PSX version -- so it is
  parsed at runtime, not translated into checked-in Python. Per Aerowinx's
  own Network Documentation: "[Variables.txt] indicates for each Q code...
  the allowed value range of the Qh or Qi integer; respectively, the
  allowed string length of the Qs." That distinction matters a lot here:
  for a Qh/Qi variable (always a plain 32-bit integer, never composite),
  that Min/Max *is* the complete documented validity rule, so a coarse
  range check against it is fully reliable. For a Qs variable, Min/Max is
  only a *string length* bound -- plenty of Qs variables pack several
  semicolon-separated sub-values, or fixed per-character codes, into that
  string, each with their own narrower constraint that a length check
  cannot see at all.
- **`Network Documentation.txt`** (Aerowinx's prose protocol doc, last
  updated 2017) is where that finer structure -- for the subset of
  variables it describes -- comes from. It cannot be parsed mechanically,
  so the relevant knowledge has been read and hand-translated into the
  `_OVERLAYS` table below, once, by a human/LLM reading the document. This
  module does not read that file at all. Being older and unmaintained, it
  is also sometimes stale: e.g. it describes `SwitchesRcpL` as ranging
  0..7, but the live `Variables.txt` (version 10.180) has long since
  extended it to 0..76. Overlays here therefore only ever *add structure*
  (subfields, character classes, sparse-value patterns) on top of
  `Variables.txt`'s own numeric envelope; they never narrow a Qh/Qi range
  below what `Variables.txt` currently says, since `Variables.txt` is the
  one source of the two that's actually kept in sync with the running PSX
  version.

Every keyword known to `Variables.txt` gets at least a coarse verdict from
this module; only a keyword/name absent from `Variables.txt` entirely
comes back UNKNOWN. `Variables.txt` is parsed once per path and cached
(see `_load_variables`), so repeated calls -- the expected use here is
scanning a config file or a whole traffic log, not a live per-packet
check -- are a cheap dict lookup plus a cheap per-value check.

One piece of knowledge here comes from neither source: a DEMAND-mode
variable that nobody has sent `demand=` for comes back as a string of
`#` characters instead of real data (see `_is_undemanded_placeholder`).
That's documented only in PSX Forum posts, not in Network
Documentation.txt itself -- worth getting added there; see this
project's traffic-audit report.

Also from PSX Forum posts, not either file: a handful of keywords (the
GPS jamming/spoofing simulation, `Instructor > Situation > Human >
Spoofing`) that PSX's live network lexicon serves but that are absent
from this `Variables.txt` copy entirely -- see `_EXTRA_VARIABLES`. These
are merged in at load time so they're treated exactly like a normal
Variables.txt entry (full Mode/Min/Max, overlay-eligible), rather than
coming back UNKNOWN just because the static file hasn't caught up to
PSX's live lexicon.
"""
import dataclasses
import enum
import os
import re

DEFAULT_VARIABLES_FILE = os.path.expanduser("~/Variables.txt")

_SECTION_HEADER_RE = re.compile(r'^\[.*\]$')
_VARLINE_RE = re.compile(
    r'^(?P<keyword>Q[a-z]\d+)="(?P<name>[^"]*)";\s*'
    r'Mode=(?P<mode>[A-Za-z0-9]+);\s*'
    r'Min=(?P<min>-?\d+);\s*Max=(?P<max>-?\d+);\s*$'
)


class Validity(enum.Enum):
    """How confidently a value's validity could be determined."""

    #: Checked against a documented rule we trust fully, and it held.
    VALID = "valid"
    #: Checked against a documented rule we trust fully, and it failed.
    INVALID = "invalid"
    #: The variable is known, but the only check we have for it is
    #: known-incomplete (e.g. a Qs length check, with no visibility into
    #: its substructure), so we cannot decide valid or invalid.
    UNDETERMINED = "undetermined"
    #: The keyword/name isn't in Variables.txt at all -- not known to
    #: this module at all.
    UNKNOWN = "unknown"


@dataclasses.dataclass
class VariableInfo:
    """One Variables.txt entry."""

    keyword: str
    name: str
    mode: str
    minimum: int
    maximum: int

    @property
    def is_string(self):
        """Return True for a Qs (string) variable."""
        return self.keyword.startswith('Qs')


@dataclasses.dataclass
class ValidationResult:
    """The outcome of validating one value against one PSX variable."""

    validity: Validity
    reason: str
    keyword: str = None
    name: str = None

    @property
    def valid(self):
        """Return True only for a confident VALID verdict."""
        return self.validity is Validity.VALID


class VariableValidatorException(Exception):
    """Raised for malformed Variables.txt data; never for bad PSX values."""


_variables_cache = {}


def _parse_variables_file(path):
    """Parse a Variables.txt file into {keyword: VariableInfo}."""
    variables = {}
    with open(path, encoding='utf-8') as varfile:
        for lineno, raw_line in enumerate(varfile, start=1):
            line = raw_line.strip()
            if not line or _SECTION_HEADER_RE.match(line):
                continue
            match = _VARLINE_RE.match(line)
            if not match:
                raise VariableValidatorException(
                    f"{path}:{lineno}: unrecognized line: {raw_line!r}")
            keyword = match['keyword']
            if keyword in variables:
                raise VariableValidatorException(
                    f"{path}:{lineno}: duplicate keyword {keyword}")
            variables[keyword] = VariableInfo(
                keyword=keyword,
                name=match['name'],
                mode=match['mode'],
                minimum=int(match['min']),
                maximum=int(match['max']),
            )
    return variables


# Keywords PSX's live network lexicon serves that are absent from this
# Variables.txt copy entirely (confirmed absent as of version 10.180).
# Per multiple PSX Forum posts (quoting PSX's own live Lexicon dump, e.g.
# "Li275(E)=TowTurnRadius" -- the letter in parens is the Mode), and
# confirmed live against a running sim where cross-checked -- see the
# project_gps_spoofing_api memory for the GPS jamming/spoofing group.
# (keyword -> (name, mode, minimum, maximum), same fields as a parsed
# Variables.txt line.)
#
# Qs570 ("NdMsg") and Qs571 ("LocalRwys") are a step down in confidence
# from the rest of this table: the forum post that named them (quoting
# the SRSL-PSX addon author) gave their Mode but not a documented
# Min/Max or internal structure -- unlike every other entry here, where
# the source quotes PSX's actual Min/Max. Their _OVERLAYS entries
# (_nd_msg_check, _local_rwys_check) were therefore reverse-engineered
# from real traffic instead: every distinct value seen across all
# available logs was collected and checked to converge on one structural
# rule each, on the assumption (per user instruction) that real SRSL-PSX
# traffic is itself correct. Min/Max below are set generously around what
# was actually observed (NdMsg: 3..15 chars; LocalRwys: 0..693 chars, the
# high end driven by how many runway ends the current airport has) since
# neither bound is documented.
_EXTRA_VARIABLES = {
    'Qi275': ('TowTurnRadius', 'ECON', 20, 99999),
    'Qs570': ('NdMsg', 'ECON', 0, 100),
    'Qs571': ('LocalRwys', 'ECON', 0, 2000),
    'Qs572': ('SpoofingPage', 'ECON', 20, 300),
    'Qs573': ('GpsDrift', 'ECON', 6, 99),
    'Qs574': ('IlsL', 'DEMAND', 3, 7),
    'Qi276': ('SpoofRadius', 'ECON', -6000, 6000),
    'Qi277': ('SpoofStatus', 'ECON', 0, 3),
}


def _load_variables(path):
    """Parse path once and cache it.

    Variables.txt is assumed not to change during a run (it changes only
    with a PSX version upgrade, between runs) -- so unlike an earlier
    version of this function, there is deliberately no mtime recheck here:
    that cost a stat() syscall on every single validate() call, for a
    change that in practice never happens mid-run.
    """
    cached = _variables_cache.get(path)
    if cached is not None:
        return cached
    variables = _parse_variables_file(path)
    for keyword, (name, mode, minimum, maximum) in _EXTRA_VARIABLES.items():
        if keyword not in variables:
            variables[keyword] = VariableInfo(
                keyword=keyword, name=name, mode=mode, minimum=minimum, maximum=maximum)
    name_index = {info.name: info.keyword for info in variables.values()}
    _variables_cache[path] = (variables, name_index)
    return variables, name_index


def lookup(keyword_or_name, variables_file=DEFAULT_VARIABLES_FILE):
    """Return the VariableInfo for keyword_or_name, or None if not found."""
    variables, name_index = _load_variables(variables_file)
    if keyword_or_name in variables:
        return variables[keyword_or_name]
    keyword = name_index.get(keyword_or_name)
    if keyword is not None:
        return variables[keyword]
    return None


# ---------------------------------------------------------------------------
# Overlay checks: hand-translated from Network Documentation.txt, keyed by
# the variable's canonical *name* (e.g. "Brakes", not "Qs357") -- that is
# the identifier the documentation itself actually uses. Each entry is a
# callable: (value_str) -> (ok: bool, detail: str, reliable: bool).
# ---------------------------------------------------------------------------

def _semicolon_ints(value, field_ranges):
    """Check value as exactly len(field_ranges) semicolon-separated ints.

    field_ranges is a list of (min, max) pairs, or None for "must be an
    integer, but no documented numeric bound".
    """
    parts = value.split(';')
    if len(parts) != len(field_ranges):
        return False, f"expected {len(field_ranges)} fields, got {len(parts)}", True
    for i, (part, bounds) in enumerate(zip(parts, field_ranges), start=1):
        try:
            ivalue = int(part)
        except ValueError:
            return False, f"field {i} ({part!r}) is not an integer", True
        if bounds is not None:
            lo, hi = bounds
            if not lo <= ivalue <= hi:
                return (
                    False,
                    f"field {i} ({ivalue}) outside documented range {lo}..{hi}",
                    True,
                )
    return True, "ok", True


def _fixed_chars(value, length, char_ok):
    """Check value is exactly length characters, each satisfying char_ok(c, index)."""
    if len(value) != length:
        return False, f"expected length {length}, got {len(value)}", True
    for i, char in enumerate(value):
        if not char_ok(char, i):
            return False, f"character {i + 1} ({char!r}) not allowed here", True
    return True, "ok", True


_CB_CHARS = set('xnNyYbBR')


def _cb_panel_check(value):
    """Circuit breaker panel section: one of xnNyYbBR per CB, any length."""
    for i, char in enumerate(value):
        if char not in _CB_CHARS:
            return False, f"character {i + 1} ({char!r}) is not a valid CB state", True
    return True, "ok", True


_CDU_LINE_CHARS = set(
    'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<>.,:/%+-=*_()#oubdlrt ')


def _cdu_line_check(value):
    """CDU text line: 24 text chars (+ optional 24 '+'/'-' font-size chars)."""
    if not 24 <= len(value) <= 48:
        return False, f"length {len(value)} not in 24..48", True
    text_len = min(len(value), 24)
    for i in range(text_len):
        if value[i] not in _CDU_LINE_CHARS:
            return False, f"character {i + 1} ({value[i]!r}) not a valid CDU glyph", True
    for i in range(24, len(value)):
        if value[i] not in '+-':
            return False, f"font-size character {i + 1} ({value[i]!r}) must be + or -", True
    return True, "ok", True


def _cdu_scratchpad_check(value):
    """CDU scratchpad line: 0..24 text chars, no font-size suffix."""
    if not 0 <= len(value) <= 24:
        return False, f"length {len(value)} not in 0..24", True
    for i, char in enumerate(value):
        if char not in _CDU_LINE_CHARS:
            return False, f"character {i + 1} ({char!r}) not a valid CDU glyph", True
    return True, "ok", True


def _xpndr_check(value):
    """Qi204 Xpndr: 1/2/3, then ident 0/1, then a 4-digit squawk code."""
    try:
        ivalue = int(value)
    except ValueError:
        return False, f"{value!r} is not an integer", True
    text = str(ivalue)
    if len(text) < 5 or len(text) > 6:
        return False, f"{ivalue} does not have 5-6 digits", True
    if text[0] not in '123':
        return False, f"first digit ({text[0]}) must be 1, 2, or 3", True
    # Normalize to a fixed 6-digit layout (5-digit values omit a leading
    # transponder-status digit position only when squawk < 1000... in
    # practice Variables.txt's own 10000..317777 range means the shortest
    # valid value is 10000, i.e. always at least 5 digits).
    ident_digit = text[1] if len(text) == 6 else text[0]
    if ident_digit not in '01' and len(text) == 6:
        return False, f"ident digit ({ident_digit}) must be 0 or 1", True
    return True, "ok", True


def _act_dest_eta_check(value):
    """Qi247 ActDestEta: -1 (unavailable), or HHMM with MM in 0..59."""
    try:
        ivalue = int(value)
    except ValueError:
        return False, f"{value!r} is not an integer", True
    if ivalue == -1:
        return True, "ok (unavailable)", True
    if not 0 <= ivalue <= 2359:
        return False, f"{ivalue} outside documented range -1, 0..2359", True
    if ivalue % 100 > 59:
        return False, f"{ivalue} has an invalid minutes component (>59)", True
    return True, "ok", True


def _two_digit_pairs_check(value, count):
    """Check value is a fixed string of count 2-digit (00-99) pairs, no separators."""
    if len(value) != count * 2:
        return False, f"expected length {count * 2}, got {len(value)}", True
    for i in range(count):
        pair = value[i * 2:i * 2 + 2]
        if not pair.isdigit():
            return False, f"pair {i + 1} ({pair!r}) is not two digits", True
    return True, "ok", True


def _voltages_check(value, count):
    """MdtVoltages/LtVoltages: count semicolon fields, each "circa" 0..1000.

    The documentation's own wording ("a value from 0 to circa 1000") is
    explicitly approximate, so a value just above 1000 is flagged
    undetermined rather than invalid -- we don't actually know where the
    real ceiling is.
    """
    parts = value.split(';')
    if len(parts) != count:
        return False, f"expected {count} fields, got {len(parts)}", True
    for i, part in enumerate(parts, start=1):
        try:
            ivalue = int(part)
        except ValueError:
            return False, f"field {i} ({part!r}) is not an integer", True
        if ivalue < 0:
            return False, f"field {i} ({ivalue}) is negative", True
        if ivalue > 1200:
            return False, f"field {i} ({ivalue}) far beyond the documented ~1000 ceiling", True
        if ivalue > 1000:
            return True, f"field {i} ({ivalue}) above the documented ~1000 ceiling", False
    return True, "ok", True


def _touchdown_check(value):  # pylint: disable=too-many-return-statements
    """Qs488 Touchdown: a 4-char status prefix, then 8 ';'-separated ints."""
    if len(value) < 5 or value[4] != ';':
        return False, "expected a 4-character status prefix then ';'", True
    prefix, rest = value[:4], value[5:]
    if prefix[1] not in 'Si':
        return False, f"character 2 ({prefix[1]!r}) must be S or i", True
    if prefix[2] not in 'LR1234w':
        return False, f"character 3 ({prefix[2]!r}) must be one of LR1234w", True
    if prefix[3] not in 'Tu':
        return False, f"character 4 ({prefix[3]!r}) must be T or u", True
    fields = rest.split(';')
    if len(fields) != 9 or fields[-1] != '':
        return False, "expected 8 ';'-terminated integer fields after the prefix", True
    for i, part in enumerate(fields[:-1], start=1):
        try:
            int(part)
        except ValueError:
            return False, f"field {i} ({part!r}) is not an integer", True
    # Field-level sentinel/sign conventions (nose/main sinkrate sign,
    # 9999/99999 "invalid data" markers) are documented but not range
    # bounds, so integer-ness is all we can confidently check -- flagged
    # undetermined rather than claiming full confidence.
    return True, "ok", False


def _engflamen1_check(value):
    """Qs481 EngFlameN1: 4 flame-status chars, then 4 ';'-separated N1 ints."""
    if len(value) < 5:
        return False, "too short for a 4-character prefix", True
    prefix, rest = value[:4], value[4:]
    for i, char in enumerate(prefix, start=1):
        if char not in 'xL':
            return False, f"character {i} ({char!r}) must be x or L", True
    fields = rest.split(';')
    if len(fields) != 5 or fields[-1] != '':
        return False, "expected 4 ';'-terminated N1 integer fields after the prefix", True
    for i, part in enumerate(fields[:-1], start=1):
        try:
            int(part)
        except ValueError:
            return False, f"N1 field {i} ({part!r}) is not an integer", True
    # No documented numeric bound for N1 x10 itself.
    return True, "ok", False


def _tcas_pan_stat_check(value):
    """Qs118 TcasPanStat: 8 digit/underscore chars, then 3 status chars."""
    if len(value) != 11:
        return False, f"expected length 11, got {len(value)}", True
    for i in range(8):
        if value[i] != '_' and not value[i].isdigit():
            return False, f"character {i + 1} ({value[i]!r}) must be a digit or '_'", True
    if value[8] not in 'LN':
        return False, f"character 9 ({value[8]!r}) must be L or N", True
    if value[9] not in 'BPN':
        return False, f"character 10 ({value[9]!r}) must be B, P, or N", True
    if value[10] not in 'FN':
        return False, f"character 11 ({value[10]!r}) must be F or N", True
    return True, "ok", True


def _semicolon_terminated_floats(value, count):
    """Check value as exactly count ';'-terminated numeric fields.

    Unlike _semicolon_ints, these PSX fields (GPS spoofing geometry) mix
    integers and floats and end in a trailing ';' rather than being purely
    delimiter-separated -- splitting on ';' therefore yields count + 1
    parts, the last one empty.
    """
    parts = value.split(';')
    if len(parts) != count + 1 or parts[-1] != '':
        return False, f"expected {count} ';'-terminated numeric fields, got {value!r}", True
    for i, part in enumerate(parts[:-1], start=1):
        try:
            float(part)
        except ValueError:
            return False, f"field {i} ({part!r}) is not a number", True
    return True, "ok", True


def _spoofing_page_check(value):
    """Qs572 SpoofingPage: [P/p][T/t] prefix + 9 ';'-terminated numeric fields.

    Per PSX Forum: zone lat, zone lon, zone min radius, zone radius
    addition, jamming extension, spoofed-to lat, spoofed-to lon,
    spoofed-to alt, spoofed-to time deviation.
    """
    if len(value) < 2:
        return False, f"expected a 2-character [P/p][T/t] prefix, got {value!r}", True
    if value[0] not in 'Pp':
        return False, f"character 1 ({value[0]!r}) must be P or p", True
    if value[1] not in 'Tt':
        return False, f"character 2 ({value[1]!r}) must be T or t", True
    return _semicolon_terminated_floats(value[2:], 9)


def _gps_drift_check(value):
    """Qs573 GpsDrift: 2 ';'-terminated numeric fields (distance nm, alt deviation ft)."""
    return _semicolon_terminated_floats(value, 2)


_ND_MSG_RE = re.compile(r'^(.*);([01]);$')


def _nd_msg_check(value):
    """Qs570 NdMsg: '<message text>;<0 or 1>;' -- an ND warning and its active flag.

    Format reverse-engineered from real traffic, not documented anywhere
    (see _EXTRA_VARIABLES): every distinct value seen so far is one of
    ';0;' (no message) or '<TEXT>;1;' for TEXT in FLAPS, UNSTABLE,
    RAAS-OK-M, ON TAXIWAY, SHORT RUNWAY, LONG LANDING -- but the message
    text itself is left unconstrained here (SRSL-PSX almost certainly has
    more canned messages than these six), only the '<text>;<flag>;' shape.
    """
    if not _ND_MSG_RE.match(value):
        return False, "expected '<message text>;<0 or 1>;'", True
    return True, "ok", True


# A runway-end designator as used by LocalRwys: 2 digits, then a 3rd
# character that's one of L/C/R/W or a space (no suffix).
_LOCAL_RWYS_ENTRY_RE = re.compile(
    r"^\d{2}[LCRW ]'-?[0-9.]+(?:[Ee][+-]?\d+)?'-?[0-9.]+(?:[Ee][+-]?\d+)?'-?\d+$")


def _local_rwys_check(value):
    """Qs571 LocalRwys: ';'-terminated runway-end entries for the current airport.

    Each entry is <designator>'<lat_rad>'<lon_rad>'<threshold_elev_ft>,
    e.g. "27L'0.8278179'0.3359802'0". Format reverse-engineered from real
    traffic, not documented anywhere (see _EXTRA_VARIABLES): checked
    against every distinct value seen across all available logs (364
    values, lengths 0..693) with zero exceptions. An empty string means
    no runway data for the current position.
    """
    if value == '':
        return True, "ok (no runway data for the current position)", True
    entries = value.split(';')
    if entries[-1] != '':
        return False, "expected a ';'-terminated list of runway entries", True
    for i, entry in enumerate(entries[:-1], start=1):
        if not _LOCAL_RWYS_ENTRY_RE.match(entry):
            return (
                False,
                f"entry {i} ({entry!r}) doesn't match the observed "
                "designator'lat'lon'elevation format",
                True,
            )
    return True, "ok", True


def _ils_l_check(value):
    """Qs574 IlsL: LOC;GS needle positions, each 0..128 or the 999 unpowered sentinel."""
    parts = value.split(';')
    if len(parts) != 2:
        return False, f"expected 2 ';'-separated fields, got {len(parts)}", True
    for i, part in enumerate(parts, start=1):
        try:
            ivalue = int(part)
        except ValueError:
            return False, f"field {i} ({part!r}) is not an integer", True
        if not 0 <= ivalue <= 128 and ivalue != 999:
            return (
                False,
                f"field {i} ({ivalue}) outside 0..128 and isn't the unpowered sentinel 999",
                True,
            )
    return True, "ok", True


def _wrap_fixed(func, *args):
    return lambda value: func(value, *args)


_OVERLAYS = {
    # --- Flight controls: PAIB/2026/016's actual trigger. ---
    'Brakes': _wrap_fixed(_semicolon_ints, [(0, 1000), (0, 1000)]),
    'FltControls': _wrap_fixed(_semicolon_ints, [(-999, 999)] * 3),
    'Tla': _wrap_fixed(_semicolon_ints, [(-8925, 5000)] * 4),

    # --- Panels with clearly documented per-subfield ranges. ---
    'TcasPanSel': _wrap_fixed(_semicolon_ints, [(0, 1), (0, 1), (0, 2), (0, 3)]),
    'WxRdrPanelTurn': _wrap_fixed(
        _semicolon_ints, [(0, 4713), (-6, 3), (0, 4713), (-6, 3)]),
    'RotSelAudioL': _wrap_fixed(_semicolon_ints, [(0, 99)] * 13 + [(0, 3), (0, 2), (0, 3)]),
    'RotSelAudioC': _wrap_fixed(_semicolon_ints, [(0, 99)] * 13 + [(0, 3), (0, 2), (0, 3)]),
    'RotSelAudioR': _wrap_fixed(_semicolon_ints, [(0, 99)] * 13 + [(0, 3), (0, 2), (0, 3)]),

    # --- Sparse-validity integers: a contiguous Min..Max range that
    # nonetheless excludes many individual values. ---
    'Xpndr': _xpndr_check,
    'ActDestEta': _act_dest_eta_check,

    # --- Fixed-width two-digit-pair strings. ---
    'SurfSpoilers': _wrap_fixed(_two_digit_pairs_check, 11),
    'SurfAilEleRud': _wrap_fixed(_two_digit_pairs_check, 10),

    # --- "Circa 1000" voltage arrays: deliberately approximate. ---
    'MdtVoltages': _wrap_fixed(_voltages_check, 16),
    'LtVoltages': _wrap_fixed(_voltages_check, 22),

    # --- Multi-field strings with some, but not all, structure known. ---
    'Touchdown': _touchdown_check,
    'EngFlameN1': _engflamen1_check,
    'TcasPanStat': _tcas_pan_stat_check,

    # --- GPS jamming/spoofing simulation; see _EXTRA_VARIABLES above. ---
    'SpoofingPage': _spoofing_page_check,
    'GpsDrift': _gps_drift_check,
    'IlsL': _ils_l_check,
    'NdMsg': _nd_msg_check,
    'LocalRwys': _local_rwys_check,
}

# Circuit breaker panel sections (Qs4..Qs61): one of 'xnNyYbBR' per CB.
for _cb_name in (
        'P71A', 'P71B', 'P71C', 'P71D', 'P71E', 'P71F', 'P71G', 'P71H', 'P71J',
        'P72A', 'P72B', 'P72C', 'P72D', 'P72E', 'P72F', 'P72G', 'P72H', 'P72J',
        'P61C', 'P61D', 'P61E', 'P61F', 'P61G', 'P61H', 'P61J', 'P61K', 'P61L',
        'P62C', 'P62D', 'P62E', 'P62F', 'P62G', 'P62H', 'P62J', 'P62K', 'P62L',
        'P63C', 'P63D', 'P63E', 'P63F', 'P63G', 'P63H', 'P63J', 'P63K', 'P63L',
        'P64C', 'P64D', 'P64E', 'P64F', 'P64G', 'P64H', 'P64J', 'P64K', 'P64L',
        'P65A', 'P65B', 'P66A', 'P66B',
):
    _OVERLAYS[_cb_name] = _cb_panel_check

# CDU screen lines/titles (text + optional font-size suffix) and scratchpads.
for _cdu_seat in ('Lcdu', 'Ccdu', 'Rcdu'):
    _OVERLAYS[f'{_cdu_seat}Title'] = _cdu_line_check
    for _cdu_line in range(1, 7):
        _OVERLAYS[f'{_cdu_seat}Line{_cdu_line}s'] = _cdu_line_check
        _OVERLAYS[f'{_cdu_seat}Line{_cdu_line}b'] = _cdu_line_check
    _OVERLAYS[f'{_cdu_seat}ScrPad'] = _cdu_scratchpad_check


def _is_undemanded_placeholder(value):
    """Check value is PSX's "nobody has demanded this yet" placeholder.

    Per several PSX Forum posts (not Aerowinx's own Network
    Documentation.txt -- flagged in this project's report as worth
    getting added there): a DEMAND-mode variable that nobody has sent
    `demand=<keyword>` for comes back as a string of '#' characters, one
    per expected character position, instead of real data. Confirmed
    against real traffic for SurfSpoilers, SurfAilEleRud, and EngFlameN1.
    """
    return len(value) > 0 and set(value) == {'#'}


def validate(  # pylint: disable=too-many-return-statements
        keyword_or_name, value, variables_file=DEFAULT_VARIABLES_FILE):
    """Check whether value is a documented-valid value for keyword_or_name.

    keyword_or_name is either a Q-code (e.g. "Qs357") or a friendly name
    (e.g. "Brakes") as used in variables_file. value is the raw string as
    it appears on the wire (the part after "=").
    """
    info = lookup(keyword_or_name, variables_file)
    if info is None:
        return ValidationResult(
            validity=Validity.UNKNOWN,
            reason=f"{keyword_or_name!r} is not a known PSX variable "
                   f"(not found in {variables_file})",
        )

    if info.mode == 'DEMAND' and _is_undemanded_placeholder(value):
        return ValidationResult(
            validity=Validity.VALID,
            reason="all '#': the known placeholder PSX returns for a "
                   "DEMAND-mode variable nobody has sent demand= for",
            keyword=info.keyword, name=info.name,
        )

    overlay = _OVERLAYS.get(info.name)
    if overlay is not None:
        ok, detail, reliable = overlay(value)
        validity = (
            Validity.VALID if ok and reliable else
            Validity.UNDETERMINED if ok else
            Validity.INVALID
        )
        return ValidationResult(
            validity=validity, reason=detail, keyword=info.keyword, name=info.name)

    if info.is_string:
        length = len(value)
        if not info.minimum <= length <= info.maximum:
            return ValidationResult(
                validity=Validity.INVALID,
                reason=f"length {length} outside documented range "
                       f"{info.minimum}..{info.maximum}",
                keyword=info.keyword, name=info.name,
            )
        return ValidationResult(
            validity=Validity.UNDETERMINED,
            reason=f"length {length} is within the documented range "
                   f"{info.minimum}..{info.maximum}, but Variables.txt only "
                   "documents string length here, not content structure",
            keyword=info.keyword, name=info.name,
        )

    try:
        ivalue = int(value)
    except ValueError:
        return ValidationResult(
            validity=Validity.INVALID,
            reason=f"{value!r} is not an integer",
            keyword=info.keyword, name=info.name,
        )
    if not info.minimum <= ivalue <= info.maximum:
        return ValidationResult(
            validity=Validity.INVALID,
            reason=f"{ivalue} outside documented range {info.minimum}..{info.maximum}",
            keyword=info.keyword, name=info.name,
        )
    return ValidationResult(
        validity=Validity.VALID,
        reason=f"{ivalue} is within the documented range {info.minimum}..{info.maximum}",
        keyword=info.keyword, name=info.name,
    )


@dataclasses.dataclass
class CoverageReport:
    """How much of Variables.txt this module can actually confirm valid/invalid for."""

    total: int
    qhqi_total: int
    qs_total: int
    overlay_count: int
    qs_without_overlay: list

    @property
    def well_covered(self):
        """Return the count of variables with a fully reliable validity check.

        Every Qh/Qi variable qualifies (Variables.txt's Min/Max *is* the
        complete documented rule for a plain integer); a Qs variable only
        qualifies if it has a structured overlay.
        """
        return self.total - len(self.qs_without_overlay)

    @property
    def not_well_covered(self):
        """Return the count of variables that can only ever come back UNDETERMINED."""
        return len(self.qs_without_overlay)


def coverage(variables_file=DEFAULT_VARIABLES_FILE):
    """Return a CoverageReport describing this module's own blind spots.

    A Qs variable with no entry in `_OVERLAYS` can only ever be checked by
    string length, so any value within that length always comes back
    UNDETERMINED, never VALID -- this reports exactly which variables that
    applies to, so the overlay table's priorities are visible rather than
    just discovered by running into them in a scan.
    """
    variables, _ = _load_variables(variables_file)
    qs_total = sum(1 for info in variables.values() if info.is_string)
    qs_without_overlay = sorted(
        info.name for info in variables.values()
        if info.is_string and info.name not in _OVERLAYS
    )
    return CoverageReport(
        total=len(variables),
        qhqi_total=len(variables) - qs_total,
        qs_total=qs_total,
        overlay_count=len(_OVERLAYS),
        qs_without_overlay=qs_without_overlay,
    )
