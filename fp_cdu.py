"""Reading PSX CDU screens for frankenpilot: page titles, the VNAV cruise page, page keys.

PSX sends each CDU's screen as network variables ({L,C,R}cduTitle,
{L,C,R}cduLine1s/1b ... 6s/6b, 24 characters, '_' for a space, then the
characters' font sizes). frankenpilot reads its own side's CDU, e.g. VNAV
CRZ page 2/3 for the next step climb (not on the network otherwise, see
docs/psx-issues.md), and puts the page that was showing back.
"""
import re

# KeybCdu{L,C,R} key numbers (psx.py's MCDU key map); -1 = key released
KEYS = {
    "NEXT": 50, "PREV": 49, "MENU": 47, "NAVRAD": 48, "INITREF": 57, "RTE": 58,
    "DEPARR": 59, "ATC": 60, "VNAV": 61, "FIX": 62, "LEGS": 63, "HOLD": 64,
    "FMCCOMM": 65, "PROG": 66,
}

# Page title -> the key that brings that page back (first match wins). INIT REF
# pages, DEP/ARR, MENU and other subsystems aren't listed: pressing their key
# doesn't reliably give the same page back, so frankenpilot doesn't leave them.
_RESTORE = (
    (r"\bLEGS\b", "LEGS"),
    (r"\bRTE [12]\b|\bROUTE\b", "RTE"),
    (r"\bPROGRESS\b", "PROG"),
    (r"\b(CLB|CRZ|DES)\b", "VNAV"),
    (r"\bHOLD\b", "HOLD"),
    (r"\bFIX INFO\b", "FIX"),
    (r"\bNAV RADIO\b", "NAVRAD"),
)


def text(raw):
    """Return the 24 visible characters of a CDU line/title, '_' as space, stripped."""
    return (raw or "")[:24].replace("_", " ").strip()


def half(raw, side):
    """Return the left (0) or right (1) half of a CDU line (12 characters each), stripped."""
    line = (raw or "")[:24].replace("_", " ")
    return (line[12:] if side else line[:12]).strip()


def page_number(title):
    """Return (page, pages) from a title like "ACT RTE 1 LEGS   2/5", or (1, 1)."""
    m = re.search(r"(\d+)/(\d+)\s*$", title)
    return (int(m.group(1)), int(m.group(2))) if m else (1, 1)


def page_name(title):
    """Return a title without its page number and ACT/MOD prefix (to compare pages)."""
    return re.sub(r"^(ACT|MOD)\s+|\s*\d+/\d+\s*$", "", title).strip()


def restore_key(title):
    """Return the key name that brings this page back, or None if it can't be."""
    for pattern, key in _RESTORE:
        if re.search(pattern, title):
            return key
    return None


def is_vnav_cruise_page2(title):
    """Return True for VNAV CRZ page 2 (e.g. "ACT ECON CRZ   2/3")."""
    return bool(re.search(r"\bCRZ\b", title)) and page_number(title)[0] == 2


def parse_vnav_cruise(lines):
    """Parse VNAV CRZ 2/3 (NG FMC) screen lines {name: raw}; return a dict or None.

    NG FMC layout: line 1 big right "STEP TO" level, line 2 big right "AT"
    ("NOW" or "1010Z/ 265NM"), line 4 small "STEP ... OPT MAX RECMD" and
    big left the step size ("0", "1000", "RVSM"). None when it isn't that page.
    """
    l4s, l4b = text(lines.get("Line4s")), text(lines.get("Line4b"))
    if not l4s.startswith("STEP") or "SIZE" in l4s:     # original FMC: "STEP SIZE"
        return None
    crz = re.match(r"(FL\d{3}|\d{4,5})", half(lines.get("Line1b"), 0))
    step_to = re.fullmatch(r"FL(\d{3})", half(lines.get("Line1b"), 1))
    at = half(lines.get("Line2b"), 1)
    m_time, m_nm = re.search(r"(\d{4})Z", at), re.search(r"(\d+)NM", at)
    return {
        "crz_alt": crz.group(1) if crz else None,
        "step_to_ft": int(step_to.group(1)) * 100 if step_to else None,
        "at": at,
        "now": at == "NOW",
        "at_time": m_time.group(1) if m_time else None,
        "at_nm": int(m_nm.group(1)) if m_nm else None,
        "step_size": l4b.split()[0] if l4b.split() else "",
    }
