"""frankenstutter's report: where the stutters were, how bad, likely causes, what goes with them.

    python frankenstutter.py --report SESSION_FOLDER [more folders ...]

Prints the report and writes report.md into the (first) folder. Plain Python, no packages.
"""
import collections
import csv
import json
import os
import statistics
import sys

SEVS = ("small", "medium", "major")
# a context "spike": the value in the stutter's last 10 s, the baseline: the same over all seconds
SPIKES = (("msfs_read_mbs", "MSFS reading from disk", 50.0),
          ("net_rx_mbs", "network download", 2.0),
          ("msfs_page_faults_s", "MSFS page faults", 20000.0),
          ("other_cpu_cores", "other programs busy (cores)", 1.0),
          ("cpu_dpc_pct", "DPC time % (drivers)", 3.0),
          ("cpu_max_core", "busiest CPU core %", 95.0))


def num(v):
    """Return v as a float, or None."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load(folders):
    """Return (seconds rows, stutter records, markers, session infos)."""
    rows, stutters, markers, infos = [], [], [], []
    for d in folders:
        p = os.path.join(d, "seconds.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                rows += list(csv.DictReader(fh))
        p = os.path.join(d, "stutters.jsonl")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                stutters += [json.loads(line) for line in fh if line.strip()]
        p = os.path.join(d, "markers.csv")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                markers += list(csv.reader(fh))
        p = os.path.join(d, "session.json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                infos.append(json.load(fh))
    return rows, stutters, markers, infos


def phase(r) -> str:
    """Return the flight phase of a row (dict with on_ground, agl_ft, alt_ft)."""
    if num(r.get("lat")) is None:
        return "unknown (no SimConnect)"
    if num(r.get("on_ground")) == 1:
        return "on the ground"
    agl = num(r.get("agl_ft")) or 0
    return "below 3,000 ft AGL" if agl < 3000 else "below 10,000 ft AGL" if agl < 10000 else \
        "cruise / high"


def place(r) -> str:
    """Return where a row was: the airport.

    The airport when on the ground or below 10,000 ft AGL within 30 nm of it; else enroute.
    """
    ap, d = r.get("airport"), num(r.get("airport_nm"))
    agl = num(r.get("agl_ft"))
    if ap and d is not None and d <= 30 and (num(r.get("on_ground")) == 1 or (agl or 0) < 10000):
        return ap
    return "enroute" if num(r.get("lat")) is not None else "unknown"


def table(head, lines) -> str:
    """Return a markdown table."""
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(x) for x in line) + " |" for line in lines]
    return "\n".join(out)


def group(rows, key):
    """Return {group: [rows]} over the seconds rows (paused seconds left out)."""
    g = collections.defaultdict(list)
    for r in rows:
        if num(r.get("paused")) == 1:
            continue
        g[key(r)].append(r)
    return g


def rates(rs):
    """Return (minutes, small/min, medium/min, major/min, fps median, p99 median) for rows."""
    mins = len(rs) / 60.0
    tot = {s: sum(num(r.get(f"stutter_{s}")) or 0 for r in rs) for s in SEVS}
    fps = [num(r.get("fps")) for r in rs if num(r.get("fps"))]
    p99 = [num(r.get("disp_p99")) for r in rs if num(r.get("disp_p99"))]
    return (round(mins, 1), *(round(tot[s] / mins, 2) if mins else 0 for s in SEVS),
            round(statistics.median(fps), 1) if fps else "-",
            round(statistics.median(p99), 1) if p99 else "-")


def report(folders, dest=None) -> None:  # pylint: disable=too-many-locals,too-many-statements,too-many-branches
    """Load the session folders, print the report and write it (dest, or report.md in the first)."""
    rows, stutters, markers, infos = load(folders)
    if not rows:
        sys.exit("no seconds.csv rows in " + ", ".join(folders))
    out = ["# frankenstutter report", ""]
    for info in infos:
        plan = (info.get("power_plan") or "?").split(":")[-1].strip()
        out.append(f"- Session {info.get('start_utc')}: {info.get('cpu')}, {info.get('ram_gb')} GB "
                   f"RAM, GPU {info.get('gpu', '?')}, HAGS {info.get('hags_hwschmode')} "
                   f"(2 = on), power plan: {plan}")
    src = collections.Counter(r.get("source") or "none" for r in rows)
    sources = ", ".join(f"{k} {v}" for k, v in src.items())
    out.append(f"- {len(rows)} seconds ({len(rows) / 60:.0f} min), frames from: {sources}")
    kinds = ", ".join(f"{sum(s['severity'] == v for s in stutters)} {v}" for v in SEVS)
    out.append(f"- {len(stutters)} stutters: {kinds}; {len(markers)} markers typed")
    out += ["", "## Where", "",
            "Stutters per minute; places with at least 1 minute, worst (major, then medium) "
            "first.", ""]
    lines = []
    for k, rs in group(rows, place).items():
        if len(rs) >= 60:
            lines.append((k, *rates(rs)))
    lines.sort(key=lambda x: (-x[4], -x[3], -x[2]))
    out.append(table(("place", "min", "small/min", "medium/min", "major/min", "fps", "p99 ms"),
                     lines[:25]))
    for title, key in (("Flight phase", phase), ("Camera", lambda r: r.get("camera") or "?")):
        out += ["", f"## {title}", ""]
        lines = [(k, *rates(rs)) for k, rs in group(rows, key).items() if len(rs) >= 30]
        lines.sort(key=lambda x: -x[1])
        out.append(table((title.lower(), "min", "small/min", "medium/min", "major/min", "fps",
                          "p99 ms"), lines))

    out += ["", "## Likely causes", "",
            "From PresentMon's CPU and GPU busy times of the slowest frame before each "
            "stutter.", ""]
    for sev in SEVS:
        c = collections.Counter((s.get("guess") or "?").split(" (")[0]
                                for s in stutters if s["severity"] == sev)
        if c:
            out.append(f"- **{sev}** ({sum(c.values())}): " + ", ".join(
                f"{k} {v} ({100 * v / sum(c.values()):.0f} %)" for k, v in c.most_common()))

    out += ["", "## What goes with stutters", "",
            "How often each thing was high in the 10 s before a stutter, compared with how often "
            "it was high in any 10 s of the session. A lift well above 1 points at a cause.", ""]
    lines = []
    for key, text, limit in SPIKES:
        base_vals = [num(r.get(key)) for r in rows]
        windows = [max([v for v in base_vals[max(0, i - 9):i + 1] if v is not None] or [0])
                   for i in range(len(base_vals))]
        base = sum(w >= limit for w in windows) / max(1, len(windows))
        for sev in ("medium", "major", "small"):
            ss = [s for s in stutters if s["severity"] == sev]
            if not ss:
                continue
            hit = sum((num((s.get("max_last_10s") or {}).get(key)) or 0) >= limit
                      for s in ss) / len(ss)
            if not hit and not base:
                continue
            lift = f"{hit / base:.1f}" if base > 0 else ("high" if hit else "-")
            lines.append((f"{text} ≥ {limit:g}", sev, f"{100 * hit:.0f} %", f"{100 * base:.0f} %",
                          lift))
    vram = [s for s in stutters if num((s.get("row") or {}).get("vram_total_gb"))]
    if vram:
        full = sum((num(s["max_last_10s"].get("vram_used_gb")) or 0) >=
                   0.95 * num(s["row"]["vram_total_gb"]) for s in vram) / len(vram)
        allv = [r for r in rows if num(r.get("vram_total_gb"))]
        base = sum((num(r.get("vram_used_gb")) or 0) >= 0.95 * num(r["vram_total_gb"])
                   for r in allv) / max(1, len(allv))
        lines.append(("VRAM ≥ 95 % full", "all", f"{100 * full:.0f} %", f"{100 * base:.0f} %",
                      f"{full / base:.1f}" if base else ("high" if full else "-")))
    out.append(table(("signal", "stutters", "before stutters", "any time", "lift"), lines)
               if lines else "Nothing was high before stutters or at any other time.")

    busy = collections.Counter()
    for s in stutters:
        for item in ((s.get("row") or {}).get("top_other") or "").split(";"):
            if ":" in item:
                busy[item.split(":")[0]] += 1
    if busy:
        out += ["", "Other programs busiest in the stutter's second: " + ", ".join(
            f"{k} ({v})" for k, v in busy.most_common(10))]

    worst = sorted(stutters, key=lambda s: -(s.get("shown_ms") or 0))[:20]
    out += ["", "## The 20 worst", ""]
    out.append(table(("UTC", "ms", "guess", "app/cpu/gpu ms", "where", "alt ft", "camera",
                      "MSFS read MB/s", "VRAM GB"),
                     [(s["utc"][11:23], s.get("shown_ms"), (s.get("guess") or "?").split(" (")[0],
                       f"{s.get('app_ms')}/{s.get('cpu_busy_ms')}/{s.get('gpu_busy_ms')}",
                       place(s.get("row") or {}), (s.get("row") or {}).get("alt_ft"),
                       (s.get("row") or {}).get("camera"),
                       (s.get("max_last_10s") or {}).get("msfs_read_mbs"),
                       (s.get("row") or {}).get("vram_used_gb")) for s in worst]))
    if markers:
        lines = [(m[0][11:19], m[1], m[2]) for m in markers if len(m) > 2]
        out += ["", "## Markers", "", table(("UTC", "note", "airport"), lines)]
    text = "\n".join(out) + "\n"
    print(text)
    dest = dest or os.path.join(folders[0], "report.md")
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"(written to {dest})")
