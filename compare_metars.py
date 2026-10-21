#!/usr/bin/env python3
"""Compare the current VATSIM and NOAA METAR feeds for all stations.

Downloads every METAR from metar.vatsim.net and from NOAA's hourly cycle
files (the same two sources frankenweather.py uses), then reports how
they differ: which stations only one source has, how old each source's
reports are, which source has the newer report, and -- field by field --
where the two reports disagree (QNH, wind, visibility, weather, clouds,
temperature/dewpoint).

Default output is a summary with counts; --detail adds a per-station
diff. Standard library only, no PSX connection needed.
"""
import argparse
import re
import statistics
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from psxhacks_version import get_version

__version__ = get_version("psxutils", __file__)

VATSIM_URL = "https://metar.vatsim.net/all"
NOAA_CYCLE_URL = "https://tgftp.nws.noaa.gov/data/observations/metar/cycles/{hour:02d}Z.TXT"
USER_AGENT = f"psxhacks-compare_metars/{__version__}"

INHG_TO_HPA = 33.8639

_ICAO_RE = re.compile(r"^[A-Z][A-Z0-9]{3}$")
_TIME_RE = re.compile(r"^(\d{2})(\d{2})(\d{2})Z$")
_WIND_RE = re.compile(r"^(\d{3}|VRB)(\d{2,3})(?:G(\d{2,3}))?(KT|MPS|KMH)$")
_VIS_M_RE = re.compile(r"^(\d{4})(NDV)?$")
_VIS_SM_RE = re.compile(r"^([PM])?(?:(\d+)|(\d+)/(\d+))SM$")
_CLOUD_RE = re.compile(r"^(FEW|SCT|BKN|OVC|VV)(\d{3}|///)(CB|TCU|///)?$")
_TEMP_RE = re.compile(r"^(M?\d{2})/(M?\d{2})?$")
_QNH_RE = re.compile(r"^([QA])(\d{4})$")
_WX_RE = re.compile(
    r"^(?:[-+]|VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?"
    r"(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS)*$")
_NO_CLOUD = {"SKC", "CLR", "NSC", "NCD"}
# Everything from the first of these is trend/remarks, not the observation.
_BODY_END = {"RMK", "NOSIG", "BECMG", "TEMPO"}

# Fields compared between the two reports, in display order.
FIELDS = ["qnh", "wind", "visibility", "weather", "clouds", "temp_dew"]


@dataclass
class Metar:  # pylint: disable=too-many-instance-attributes
    """One METAR report, normalized and decoded into the compared fields."""

    icao: str
    raw: str
    obs_time: Optional[datetime] = None
    qnh: Optional[str] = None
    wind: Optional[str] = None
    visibility: Optional[str] = None
    weather: Optional[str] = None
    clouds: Optional[str] = None
    temp_dew: Optional[str] = None
    qnh_hpa: Optional[float] = None
    body: str = ""
    problems: list = field(default_factory=list)


def normalize(line: str) -> str:
    """Strip METAR/SPECI prefixes, a trailing '=', and collapse whitespace."""
    tokens = line.replace("=", " ").split()
    while tokens and tokens[0] in ("METAR", "SPECI"):
        tokens.pop(0)
    return " ".join(tokens)


def obs_datetime(ddhhmm: re.Match, now: datetime) -> Optional[datetime]:
    """Turn a DDHHMMZ match into a full UTC datetime no later than ~now.

    A METAR only carries day-of-month, so pick the latest month in which
    that day/time is not in the future (allowing an hour of clock skew).
    """
    day, hour, minute = (int(g) for g in ddhhmm.groups())
    year, month = now.year, now.month
    for _ in range(3):
        try:
            candidate = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            candidate = None
        if candidate is not None and candidate <= now + timedelta(hours=1):
            return candidate
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return None


def _visibility(tokens: list, i: int) -> tuple[Optional[str], int]:
    """Decode a visibility group at tokens[i]; return (value, tokens used)."""
    tok = tokens[i]
    if tok == "CAVOK":
        return "CAVOK", 1
    match = _VIS_M_RE.match(tok)
    if match:
        return f"{int(match.group(1))}m", 1
    # US statute miles, possibly split as "1 1/2SM".
    whole = 0
    used = 0
    if tok.isdigit() and i + 1 < len(tokens) and tokens[i + 1].endswith("SM"):
        whole = int(tok)
        used = 1
        tok = tokens[i + 1]
    match = _VIS_SM_RE.match(tok)
    if not match:
        return None, 0
    prefix, integer, num, den = match.groups()
    miles = whole + (int(integer) if integer else int(num) / int(den))
    return f"{prefix or ''}{miles:g}SM", used + 1


def parse(line: str, now: datetime) -> Optional[Metar]:  # pylint: disable=too-many-branches
    """Parse one raw METAR line; None if it doesn't start with an ICAO code."""
    text = normalize(line)
    tokens = text.split()
    if not tokens or not _ICAO_RE.match(tokens[0]):
        return None
    metar = Metar(icao=tokens[0], raw=text)
    weather, clouds = [], []
    body = []
    i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok in _BODY_END:
            break
        body.append(tok)
        used = 1
        match = None
        if metar.obs_time is None and (match := _TIME_RE.match(tok)):
            metar.obs_time = obs_datetime(match, now)
        elif metar.wind is None and (match := _WIND_RE.match(tok)):
            metar.wind = tok
        elif metar.visibility is None and metar.wind is not None and (
                vis := _visibility(tokens, i))[0] is not None:
            metar.visibility, used = vis
            body.extend(tokens[i + 1:i + used])
            if metar.visibility == "CAVOK":
                clouds.append("CAVOK")
        elif tok in _NO_CLOUD:
            clouds.append(tok)
        elif _CLOUD_RE.match(tok):
            clouds.append(tok)
        elif metar.temp_dew is None and _TEMP_RE.match(tok):
            metar.temp_dew = tok
        elif metar.qnh is None and (match := _QNH_RE.match(tok)):
            metar.qnh = tok
            value = int(match.group(2))
            metar.qnh_hpa = value if match.group(1) == "Q" else value / 100.0 * INHG_TO_HPA
        elif tok and _WX_RE.match(tok) and tok not in ("AUTO", "COR", "NIL") and (
                metar.wind is not None):
            weather.append(tok)
        i += used
    metar.weather = " ".join(sorted(weather)) or "-"
    metar.clouds = " ".join(clouds) or None
    metar.body = " ".join(body)
    if metar.obs_time is None:
        metar.problems.append("no observation time")
    return metar


def fetch(url: str, timeout: float) -> str:
    """Download url as text, replacing undecodable bytes.

    NOAA's cycle files occasionally contain non-UTF-8 bytes in a single
    report (seen live: 0xed); one garbled report mustn't lose the rest.
    """
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _keep_newest(metars: dict, metar: Metar) -> None:
    """Store metar unless metars already has a better report for its station.

    NOAA's cycle files repeat each station many times and are NOT in
    observation-time order (older reports get re-sent after newer ones,
    and an uncorrected original can be re-sent after its COR), so the
    file's last line is not necessarily the current report. Rules:
    newest observation time wins; at equal time a COR beats a non-COR;
    a copy that is just the existing report with its remarks cut off
    never replaces it; otherwise the later line wins (corrections
    without a COR marker are seen in the wild too).
    """
    old = metars.get(metar.icao)
    if old is not None and old.obs_time is not None:
        if metar.obs_time is None or metar.obs_time < old.obs_time:
            return
        if metar.obs_time == old.obs_time:
            new_cor, old_cor = " COR " in f" {metar.raw} ", " COR " in f" {old.raw} "
            if old_cor and not new_cor:
                return
            if new_cor == old_cor and old.raw.startswith(metar.raw):
                return
    metars[metar.icao] = metar


def load_vatsim(timeout: float, now: datetime) -> tuple[dict, int]:
    """Return ({icao: Metar}, unparseable line count) from VATSIM."""
    metars: dict = {}
    bad = 0
    for line in fetch(VATSIM_URL, timeout).splitlines():
        if not line.strip():
            continue
        metar = parse(line, now)
        if metar is None:
            bad += 1
        else:
            _keep_newest(metars, metar)
    return metars, bad


def load_noaa(timeout: float, now: datetime) -> tuple[dict, int]:
    """Return ({icao: Metar}, unparseable line count) from NOAA.

    Reads both the current and the previous hour's cycle file: early in
    the hour the current file is still mostly empty, and a station that
    only reports hourly may have its latest report in the previous one.
    """
    metars: dict = {}
    bad = 0
    for hours_ago in (1, 0):
        hour = (now - timedelta(hours=hours_ago)).hour
        for line in fetch(NOAA_CYCLE_URL.format(hour=hour), timeout).splitlines():
            # Each report is preceded by a "YYYY/MM/DD HH:MM" line.
            if not line.strip() or re.match(r"^\d{4}/\d{2}/\d{2} ", line):
                continue
            metar = parse(line, now)
            if metar is None:
                bad += 1
            else:
                _keep_newest(metars, metar)
    return metars, bad


def compare(vat: Metar, noaa: Metar) -> dict:
    """Return {field: (vatsim value, noaa value)} for every differing field."""
    diffs = {}
    for name in FIELDS:
        v_val, n_val = getattr(vat, name), getattr(noaa, name)
        if name == "qnh" and vat.qnh_hpa is not None and noaa.qnh_hpa is not None:
            # Q vs A groups for the same pressure aren't a real difference.
            if round(vat.qnh_hpa) == round(noaa.qnh_hpa):
                continue
        if v_val != n_val:
            diffs[name] = (v_val, n_val)
    return diffs


def age_minutes(metar: Metar, now: datetime) -> Optional[float]:
    """Return the report's age in minutes, or None if its time is unknown."""
    if metar.obs_time is None:
        return None
    return (now - metar.obs_time).total_seconds() / 60.0


def fmt_age(minutes: Optional[float]) -> str:
    """Format an age in minutes compactly (e.g. 45m, 3h05m, 2d04h)."""
    if minutes is None:
        return "?"
    minutes = int(round(minutes))
    sign = "-" if minutes < 0 else ""
    minutes = abs(minutes)
    if minutes < 60:
        return f"{sign}{minutes}m"
    if minutes < 24 * 60:
        return f"{sign}{minutes // 60}h{minutes % 60:02d}m"
    return f"{sign}{minutes // 1440}d{minutes % 1440 // 60:02d}h"


def age_stats(name: str, metars: dict, now: datetime) -> str:
    """Return one summary line of report-age statistics for a source."""
    ages = sorted(a for a in (age_minutes(m, now) for m in metars.values()) if a is not None)
    if not ages:
        return f"  {name:<7} no dated reports"
    p90 = ages[int(len(ages) * 0.9)]
    over_2h = sum(1 for a in ages if a > 120)
    over_24h = sum(1 for a in ages if a > 24 * 60)
    return (f"  {name:<7} median {fmt_age(statistics.median(ages)):>6}  "
            f"p90 {fmt_age(p90):>7}  max {fmt_age(ages[-1]):>7}  "
            f">2h: {over_2h:>5}  >24h: {over_24h:>5}")


def pct(count: int, total: int) -> str:
    """Format count with its percentage of total."""
    return f"{count:>6} ({100.0 * count / total:5.1f}%)" if total else f"{count:>6}"


@dataclass
class Station:
    """Comparison result for one station present in both sources."""

    icao: str
    vat: Metar
    noaa: Metar
    diffs: dict
    time_delta: Optional[float]  # minutes, positive = VATSIM newer

    @property
    def identical(self) -> bool:
        """Whether both sources have the exact same (normalized) report."""
        return self.vat.raw == self.noaa.raw


def print_summary(vatsim: dict, noaa: dict, stations: list, now: datetime) -> None:  # pylint: disable=too-many-locals
    """Print the summary report."""
    only_v = len(set(vatsim) - set(noaa))
    only_n = len(set(noaa) - set(vatsim))
    total = len(stations)
    print(f"\nStations: VATSIM {len(vatsim)}, NOAA {len(noaa)}, both {total}, "
          f"VATSIM only {only_v}, NOAA only {only_n}")

    print("\nReport age (all stations in each source):")
    print(age_stats("VATSIM", vatsim, now))
    print(age_stats("NOAA", noaa, now))
    print("Report age (stations in both):")
    print(age_stats("VATSIM", {s.icao: s.vat for s in stations}, now))
    print(age_stats("NOAA", {s.icao: s.noaa for s in stations}, now))

    same_time = [s for s in stations if s.time_delta == 0]
    v_newer = [s for s in stations if s.time_delta is not None and s.time_delta > 0]
    n_newer = [s for s in stations if s.time_delta is not None and s.time_delta < 0]
    unknown = total - len(same_time) - len(v_newer) - len(n_newer)
    print(f"\nObservation time (stations in both, {total}):")
    print(f"  same time         {pct(len(same_time), total)}")
    for label, group in (("VATSIM newer", v_newer), ("NOAA newer", n_newer)):
        line = f"  {label:<17} {pct(len(group), total)}"
        if group:
            gaps = sorted(abs(s.time_delta) for s in group)
            line += f"   by median {fmt_age(statistics.median(gaps))}, max {fmt_age(gaps[-1])}"
            line += f", >1h: {sum(1 for g in gaps if g > 60)}"
        print(line)
    if unknown:
        print(f"  time unknown      {pct(unknown, total)}")

    identical = sum(1 for s in same_time if s.identical)
    body_same = sum(1 for s in same_time if not s.identical and s.vat.body == s.noaa.body)
    print(f"\nContent, same observation time ({len(same_time)}):")
    print(f"  identical text    {pct(identical, len(same_time))}")
    print(f"  remarks/trend only{pct(body_same, len(same_time))}")
    print(f"  observation diff  {pct(len(same_time) - identical - body_same, len(same_time))}")

    print("\nStations with a differing field:")
    print(f"  {'field':<12} {'same time':>17}   {'all in both':>17}")
    for name in FIELDS:
        n_same = sum(1 for s in same_time if name in s.diffs)
        n_all = sum(1 for s in stations if name in s.diffs)
        print(f"  {name:<12} {pct(n_same, len(same_time))}   {pct(n_all, total)}")
    qnh_gaps = [abs(s.vat.qnh_hpa - s.noaa.qnh_hpa) for s in same_time
                if "qnh" in s.diffs and s.vat.qnh_hpa is not None and s.noaa.qnh_hpa is not None]
    if qnh_gaps:
        print(f"  QNH diff at same time: median {statistics.median(qnh_gaps):.1f} hPa, "
              f"max {max(qnh_gaps):.1f} hPa, >=3 hPa: {sum(1 for g in qnh_gaps if g >= 3)}")


def print_detail(vatsim: dict, noaa: dict, stations: list, now: datetime,
                 show_identical: bool) -> None:
    """Print a per-station diff."""
    print("\nPer-station differences (V = VATSIM, N = NOAA):")
    for s in stations:
        if s.identical and not show_identical:
            continue
        if s.time_delta is None:
            when = "time unknown"
        elif s.time_delta == 0:
            when = "same time"
        else:
            newer = "VATSIM" if s.time_delta > 0 else "NOAA"
            when = f"{newer} newer by {fmt_age(abs(s.time_delta))}"
        if s.identical:
            what = "identical"
        elif not s.diffs:
            what = "remarks/trend only" if s.vat.body == s.noaa.body else "other text"
        else:
            what = ", ".join(f"{k} {v!s}→{n!s}" for k, (v, n) in s.diffs.items())
        print(f"\n{s.icao}  V age {fmt_age(age_minutes(s.vat, now))}, "
              f"N age {fmt_age(age_minutes(s.noaa, now))}, {when}: {what}")
        print(f"  V: {s.vat.raw}")
        print(f"  N: {s.noaa.raw}")
    for name, mine, other in (("VATSIM", vatsim, noaa), ("NOAA", noaa, vatsim)):
        only = sorted(set(mine) - set(other))
        if only:
            print(f"\nOnly in {name} ({len(only)}):")
            for icao in only:
                print(f"  {icao}  age {fmt_age(age_minutes(mine[icao], now)):>7}  {mine[icao].raw}")


def main() -> int:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="Compare the current VATSIM and NOAA METAR feeds for all stations.")
    parser.add_argument('--detail', action='store_true',
                        help="Show the differences for each station, plus the "
                        "stations only one source has")
    parser.add_argument('--all', action='store_true',
                        help="With --detail, also list stations whose reports are identical")
    parser.add_argument('--icao', metavar='LIST',
                        help="Only compare these stations (comma-separated, e.g. EGLL,KJFK)")
    parser.add_argument('--prefix', metavar='LIST',
                        help="Only compare stations whose ICAO starts with one of these "
                        "(comma-separated, e.g. K,EG)")
    parser.add_argument('--timeout', type=float, default=60.0,
                        help="Download timeout in seconds (default: %(default)s)")
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    print(f"compare_metars {__version__}, {now:%Y-%m-%d %H:%M}Z")
    try:
        vatsim, v_bad = load_vatsim(args.timeout, now)
        noaa, n_bad = load_noaa(args.timeout, now)
    except OSError as exc:
        print(f"Download failed: {exc}", file=sys.stderr)
        return 1
    print(f"Downloaded VATSIM {len(vatsim)} stations ({v_bad} lines without a station ID), "
          f"NOAA {len(noaa)} stations ({n_bad} lines without a station ID)")

    def wanted(icao: str) -> bool:
        if args.icao and icao not in {x.strip().upper() for x in args.icao.split(",")}:
            return False
        if args.prefix and not icao.startswith(
                tuple(x.strip().upper() for x in args.prefix.split(","))):
            return False
        return True

    vatsim = {k: v for k, v in vatsim.items() if wanted(k)}
    noaa = {k: v for k, v in noaa.items() if wanted(k)}

    stations = []
    for icao in sorted(set(vatsim) & set(noaa)):
        vat, nmet = vatsim[icao], noaa[icao]
        delta = None
        if vat.obs_time is not None and nmet.obs_time is not None:
            delta = (vat.obs_time - nmet.obs_time).total_seconds() / 60.0
        stations.append(Station(icao, vat, nmet, compare(vat, nmet), delta))

    if args.detail:
        print_detail(vatsim, noaa, stations, now, args.all)
    print_summary(vatsim, noaa, stations, now)
    return 0


if __name__ == "__main__":
    EXIT_STATUS = main()
    # The EXE is usually double-clicked, and its console window closes as
    # soon as we exit -- keep the report on screen until the user is done.
    if getattr(sys, 'frozen', False):
        try:
            input("\nPress Enter to exit")
        except EOFError:
            pass
    sys.exit(EXIT_STATUS)
