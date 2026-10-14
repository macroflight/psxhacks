"""Diagnostic tool: run psx_variable_validator.py over real traffic logs.

Scans one or more PSX router traffic logs (plain `*.psxnet.log` files, or
`.zip` archives containing them -- streamed directly from the zip, never
extracted to disk) for every `Qxxx=value` message, validates each one
with `psx_variable_validator.validate()`, and reports a summary: lookup
throughput, the VALID/INVALID/UNDETERMINED/UNKNOWN breakdown with the
distinct variable names involved in each non-VALID category, and --
for every non-VALID occurrence -- which router log recorded it and which
client slot sent it, resolved to that client's own self-announced name
where one was seen. That provenance is usually exactly what's needed to
track down where bad data is actually coming from.

Usage:
    python3 psx_variable_validator_logscan.py LOG_OR_ZIP [LOG_OR_ZIP ...]
    python3 psx_variable_validator_logscan.py --variables-file ~/Variables.txt *.zip
    python3 psx_variable_validator_logscan.py --examples 3 *.zip

Intended for exactly the kind of after-the-fact analysis this module was
built for (see its own docstring): auditing a batch of real flight logs
for out-of-spec values, not a live/real-time check.
"""
import argparse
import re
import sys
import time
import zipfile
from collections import defaultdict

import psx_variable_validator as v

# Anchored to "DATA FROM/TO [...]" immediately followed (mod whitespace) by
# the keyword -- not just searched for anywhere in the line. An unanchored
# search also matches inside an addon's own payload, e.g. a base64-encoded
# "addon=GROUND.HANDLING;...;V1|GSX_SCENE|..." blob whose content happens to
# contain "Qi2094=" or "Qj8=" purely by coincidence of the base64 alphabet
# (confirmed: real example payloads ending "...cLKQi2094=" and
# "...sTJID4Qj8=", the trailing "=" being base64 padding, not a key/value
# separator) -- producing phantom "unknown variable" keywords that were
# never actually sent by any client.
_LINE_RE = re.compile(r'DATA (?:FROM|TO)\s*\[[^\]]*\]\s*(Q[a-z]\d+)=(.*)$')

# A client's own self-identification, e.g. "DATA FROM [4] name=USB:FrankenUSB
# 1.2.1" or "...clientName=...". Sent once near connect time; recording it as
# we go (rather than a separate pass) is enough since it always arrives
# before any data from that same slot that we'd want to attribute to it.
_NAME_RE = re.compile(r'DATA FROM \[\s*([^\]\s]+)\s*\]\s*(?:client)?[Nn]ame=(.+)$')
# Which client slot actually originated a given keyword=value message
# (as opposed to a "DATA TO [...]" relay/forward of the same message).
_DATA_FROM_RE = re.compile(r'DATA FROM \[\s*([^\]\s]+)')
# A slot being (re)assigned to a new connection invalidates any name
# previously learned for it.
_CONNECT_RE = re.compile(r'CONNECT client (\S+)')

# Strips the directory and the per-run epoch timestamp from a *.psxnet.log
# file name, so e.g. "logs/masterrouter-traffic-1788581757.psxnet.log" and
# "masterrouter-traffic-1788701591.psxnet.log" (a later run of the same
# router) both report as "masterrouter-traffic" -- a human-readable "which
# router" label, grouping a router's own log rotations together.
_ROUTER_LABEL_RE = re.compile(r'^(?:.*/)?(.*?)(?:-\d+)?\.psxnet\.log$')


def _router_label(path):
    """Return a short, human-readable label for the router a log file represents."""
    match = _ROUTER_LABEL_RE.match(path)
    return match.group(1) if match else path


def _iter_log_lines(path):
    """Yield (router_label, file_id, line) from a plain log or a zip of logs.

    file_id is unique per actual file scanned (so a rotated log, a fresh
    router process, resets client-slot bookkeeping), while router_label
    groups a router's own rotations together for reporting (see
    _router_label).
    """
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            for member in zf.namelist():
                if not member.endswith('.psxnet.log'):
                    continue
                print(f"Scanning {path}::{member} ...", file=sys.stderr, flush=True)
                label = _router_label(member)
                file_id = f"{path}::{member}"
                with zf.open(member) as raw:
                    for raw_line in raw:
                        yield (
                            label, file_id,
                            raw_line.decode('utf-8', errors='replace').rstrip('\r\n'))
    else:
        print(f"Scanning {path} ...", file=sys.stderr, flush=True)
        label = _router_label(path)
        with open(path, encoding='utf-8', errors='replace') as logfile:
            for line in logfile:
                yield label, path, line.rstrip('\r\n')


def scan(paths, variables_file, max_examples=0):  # pylint: disable=too-many-locals
    """Validate every Qxxx=value message found in paths; return a stats dict."""
    names = {validity: set() for validity in v.Validity}
    counts = {validity: 0 for validity in v.Validity}
    examples = defaultdict(list)
    # display_name -> {(router_label, client_slot, client_name_or_None)}
    provenance = defaultdict(set)

    total_lines = 0
    matched_lines = 0
    validate_time = 0.0
    t_start = time.perf_counter()

    current_file_id = None
    current_label = None
    slot_names = {}

    for path in paths:
        for label, file_id, line in _iter_log_lines(path):
            if file_id != current_file_id:
                current_file_id = file_id
                current_label = label
                slot_names = {}
            total_lines += 1

            name_match = _NAME_RE.search(line)
            if name_match:
                slot_names[name_match.group(1)] = name_match.group(2)
                continue
            connect_match = _CONNECT_RE.search(line)
            if connect_match:
                slot_names.pop(connect_match.group(1), None)
                continue

            match = _LINE_RE.search(line)
            if not match:
                continue
            matched_lines += 1
            keyword, value = match.group(1), match.group(2)
            t0 = time.perf_counter()
            result = v.validate(keyword, value, variables_file=variables_file)
            validate_time += time.perf_counter() - t0

            counts[result.validity] += 1
            display_name = result.name or keyword
            names[result.validity].add(display_name)
            if result.validity is not v.Validity.VALID:
                if (max_examples and
                        len(examples[display_name]) < max_examples):
                    examples[display_name].append((keyword, value, result.reason))
                from_match = _DATA_FROM_RE.search(line)
                if from_match:
                    slot = from_match.group(1)
                    provenance[display_name].add(
                        (current_label, slot, slot_names.get(slot)))

    elapsed = time.perf_counter() - t_start
    return {
        'total_lines': total_lines,
        'matched_lines': matched_lines,
        'elapsed': elapsed,
        'validate_time': validate_time,
        'counts': counts,
        'names': names,
        'examples': examples,
        'provenance': provenance,
    }


def report(stats, show_provenance=True):  # pylint: disable=too-many-locals
    """Print the stats dict from scan() as a human-readable summary."""
    print()
    print("=" * 70)
    print(f"Total lines read:        {stats['total_lines']:,}")
    print(f"Lines matching Qx=value: {stats['matched_lines']:,}")
    print(f"Wall clock (scan+validate): {stats['elapsed']:.2f} s")
    print(f"validate()-only time:      {stats['validate_time']:.2f} s")
    if stats['validate_time'] > 0:
        rate = stats['matched_lines'] / stats['validate_time']
        print(f"Lookup rate (validate() only): {rate:,.0f} lookups/s")
    if stats['elapsed'] > 0:
        line_rate = stats['total_lines'] / stats['elapsed']
        print(f"Overall line rate (incl. I/O+regex): {line_rate:,.0f} lines/s")
    print()

    for validity in v.Validity:
        count = stats['counts'][validity]
        distinct = len(stats['names'][validity])
        print(
            f"{validity.value.upper():12s} occurrences: {count:,} "
            f"({distinct} distinct variables)")

    for validity in v.Validity:
        if validity is v.Validity.VALID:
            continue
        names = sorted(stats['names'][validity])
        if not names:
            continue
        print()
        print(f"{validity.value.upper()} variable names:")
        for name in names:
            print(f"  {name}")
            for keyword, value, reason in stats['examples'].get(name, []):
                print(f"      e.g. {keyword}={value!r}  -> {reason}")
            if show_provenance:
                for router, slot, client_name in sorted(
                        stats['provenance'].get(name, []), key=lambda t: (t[0], t[1])):
                    who = client_name if client_name else "(no name seen for this slot)"
                    print(f"      seen on {router}, client slot {slot}: {who}")


def report_coverage(variables_file):
    """Print a summary of the module's own blind spots (see v.coverage())."""
    cov = v.coverage(variables_file)
    print()
    print("=" * 70)
    print(f"Module self-coverage against {variables_file}:")
    print(f"  Total variables:         {cov.total}")
    print(f"  Qh/Qi (always reliable): {cov.qhqi_total}")
    print(f"  Qs (string):             {cov.qs_total}")
    print(f"  Variables with a structured overlay: {cov.overlay_count}")
    print(
        f"  Well covered (Qh/Qi, or Qs with an overlay): {cov.well_covered} "
        f"({100 * cov.well_covered / cov.total:.1f}%)")
    print(
        f"  NOT well covered (bare Qs, length-only -> always UNDETERMINED "
        f"if length is valid): {cov.not_well_covered} "
        f"({100 * cov.not_well_covered / cov.total:.1f}%)")
    print()
    print("Qs variables with no structured overlay:")
    for name in cov.qs_without_overlay:
        print(f"  {name}")


def main():
    """Parse arguments and run the scan."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        'paths', nargs='+',
        help="one or more *.psxnet.log files and/or .zip archives containing them")
    parser.add_argument(
        '--variables-file', default=v.DEFAULT_VARIABLES_FILE,
        help=f"path to Variables.txt (default: {v.DEFAULT_VARIABLES_FILE})")
    parser.add_argument(
        '--examples', type=int, default=0, metavar='N',
        help="print up to N example (keyword, value, reason) lines per "
             "non-VALID variable name (default: 0, off)")
    parser.add_argument(
        '--no-coverage', action='store_true',
        help="skip the module self-coverage summary (see psx_variable_validator.coverage())")
    parser.add_argument(
        '--no-provenance', action='store_true',
        help="skip the per-variable router/client-slot/client-name breakdown")
    args = parser.parse_args()

    stats = scan(args.paths, args.variables_file, max_examples=args.examples)
    report(stats, show_provenance=not args.no_provenance)
    if not args.no_coverage:
        report_coverage(args.variables_file)


if __name__ == '__main__':
    main()
