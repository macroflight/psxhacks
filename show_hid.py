r"""List HID devices and monitor raw input reports (button presses, axis movements).

Usage:
    python show_hid.py             # list all HID devices
    python show_hid.py -j          # list joystick/gamepad/pedal devices only
    python show_hid.py -d N        # monitor device N from the list
    python show_hid.py --vid-pid VID:PID   # monitor by VID:PID (hex, e.g. 044f:b10a)
    python show_hid.py -d N -a     # show every report, not just changes
    python show_hid.py -d N -r     # human-readable mode: "axis X: +0.512", "button 1 ON", ...

Installation
------------
  Windows:
        pip install hidapi
    The hidapi package bundles the native library — no separate DLL needed.
    No admin rights are needed to read HID devices on Windows.

  Linux (Ubuntu/Debian):
        pip install hidapi
        sudo apt install libhidapi-hidraw0
    By default only root can open /dev/hidraw* devices. To allow your user:
        echo 'KERNEL=="hidraw*", SUBSYSTEM=="hidraw", MODE="0660", GROUP="plugdev"' \\
            | sudo tee /etc/udev/rules.d/99-hidraw.rules
        sudo udevadm control --reload-rules && sudo udevadm trigger
    Then log out and back in (your user must be in the plugdev group).

  macOS:
        pip install hidapi
        brew install hidapi
    No extra permissions are needed for HID devices on macOS.

Human-readable mode (-r):
    Requires the hid-parser package:
        pip install hid-parser
    Reads and parses the HID report descriptor to decode axes, buttons and hats.
    Falls back to raw byte mode if the descriptor cannot be read or parsed.
"""
import argparse
import sys
import time
import warnings

try:
    import hid
except ImportError:
    sys.exit("hidapi module not found. Install with: pip install hidapi")

try:
    import hid_parser
    HID_PARSER_AVAILABLE = True
except ImportError:
    HID_PARSER_AVAILABLE = False

# HID Generic Desktop usage values
USAGE_NAMES = {
    0x01: 'Pointer',
    0x02: 'Mouse',
    0x04: 'Joystick',
    0x05: 'Gamepad',
    0x06: 'Keyboard',
    0x07: 'Keypad',
    0x08: 'Multi-axis',
}

USAGE_PAGE_NAMES = {
    0x01: 'Generic Desktop',
    0x02: 'Simulation',
    0x08: 'LED',
    0x09: 'Button',
    0x0C: 'Consumer',
}

# Generic Desktop usages that are joystick-like (pedals, throttles, etc.)
JOYSTICK_USAGES = {0x04, 0x05, 0x08}  # Joystick, Gamepad, Multi-axis (pedals often use these)

# Human-readable names for Generic Desktop axis usages (usage page 1)
AXIS_USAGE_NAMES = {
    0x30: 'X', 0x31: 'Y', 0x32: 'Z',
    0x33: 'Rx', 0x34: 'Ry', 0x35: 'Rz',
    0x36: 'Slider', 0x37: 'Dial', 0x38: 'Wheel',
}

HAT_DIRECTIONS = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']

ANSI_YELLOW = "\033[33m"
ANSI_GREEN = "\033[32m"
ANSI_RESET = "\033[0m"


def enumerate_devices(joystick_only=False):
    """Return list of HID devices, deduplicated by path."""
    seen = set()
    result = []
    for d in hid.enumerate():
        if d['path'] in seen:
            continue
        seen.add(d['path'])
        if joystick_only:
            if d['usage_page'] != 1 or d['usage'] not in JOYSTICK_USAGES:
                continue
        result.append(d)
    return result


def print_device_list(devs):
    """Print a formatted table of HID devices."""
    if not devs:
        print("No devices found.")
        return
    print(f"{'#':<4} {'VID:PID':<12} {'Usage':<28} {'Manufacturer':<22} Product")
    print("-" * 90)
    for i, d in enumerate(devs):
        vid_pid = f"{d['vendor_id']:04x}:{d['product_id']:04x}"
        page_name = USAGE_PAGE_NAMES.get(d['usage_page'], f"Page 0x{d['usage_page']:02x}")
        usage_name = USAGE_NAMES.get(d['usage'], f"0x{d['usage']:02x}")
        usage_str = f"{page_name}/{usage_name}"
        mfr = (d['manufacturer_string'] or '')[:20]
        prod = d['product_string'] or ''
        print(f"{i:<4} {vid_pid:<12} {usage_str:<28} {mfr:<22} {prod}")


def bytes_diff(prev, curr):
    """Return list of (byte_index, old_val, new_val) for changed bytes."""
    length = max(len(prev), len(curr))
    return [
        (i, prev[i] if i < len(prev) else 0, curr[i] if i < len(curr) else 0)
        for i in range(length)
        if (prev[i] if i < len(prev) else 0) != (curr[i] if i < len(curr) else 0)
    ]


def describe_change(idx, old, new):
    """Describe a byte change — guess if it's axis-like or button-like."""
    bit_diff = old ^ new
    changed_bits = bin(bit_diff).count('1')
    # Single bit flip with one value being 0 → likely a button
    if changed_bits == 1 and (old == 0 or new == 0):
        bit_pos = bit_diff.bit_length() - 1
        state = "ON" if new != 0 else "OFF"
        return f"[byte {idx} bit {bit_pos}: {state}]"
    # Gradual value → likely an axis
    return f"[byte {idx}: {old:#04x}→{new:#04x}]"


def format_report_line(data, changed_indices):
    """Format a HID report as hex bytes, highlighting changed indices in yellow."""
    parts = []
    for i, b in enumerate(data):
        s = f"{b:02x}"
        if i in changed_indices:
            s = ANSI_YELLOW + s + ANSI_RESET
        parts.append(s)
    return " ".join(parts)


def build_readable_context(dev):
    """Read and parse the HID report descriptor; return (ReportDescriptor, range_map) or None."""
    try:
        raw = dev.get_report_descriptor()
    except OSError:
        return None
    if not raw:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            rd = hid_parser.ReportDescriptor(list(raw))
    except Exception:  # pylint: disable=broad-except
        return None
    # Build range_map keyed by plain (page, uid) tuples so lookups are reliable.
    # Devices may have no report IDs (use None) or one or more explicit IDs.
    range_map = {}
    for rid in (rd.input_report_ids or [None]):
        try:
            for item in rd.get_input_items(rid):
                if hasattr(item, 'usage'):
                    try:
                        key = (int(item.usage.page), int(item.usage.usage))
                        range_map[key] = (item.logical_min, item.logical_max)
                    except Exception:  # pylint: disable=broad-except
                        pass
        except Exception:  # pylint: disable=broad-except
            pass
    return rd, range_map


def _parse_report(rd, data):
    """Parse a HID report into {(page, uid): native bool/int}."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            raw = rd.parse_input_report(data)
            result = {}
            for usage, val in raw.items():
                if val is None:
                    continue
                page, uid = int(usage.page), int(usage.usage)
                result[(page, uid)] = int(val)
        return result
    except Exception:  # pylint: disable=broad-except
        return {}


def _format_one(key, val, range_map):
    """Format a (page, uid)/value pair as a human-readable string, or return None to skip."""
    page, uid = key
    if page == 9:  # Button page
        return f"Button {uid} {'ON' if val else 'OFF'}"
    if page == 1 and uid in AXIS_USAGE_NAMES:
        lo, hi = range_map.get(key, (0, 255))
        norm = (val - lo) / (hi - lo) * 2.0 - 1.0 if hi != lo else 0.0
        return f"{AXIS_USAGE_NAMES[uid]}: {norm:+.3f}"
    if page == 1 and uid == 0x39:  # Hat switch
        return "Hat: centered" if val > 7 else f"Hat: {HAT_DIRECTIONS[val]}"
    return f"page=0x{page:02x} uid=0x{uid:02x}: {val}"


def format_all_values(state, range_map):
    """Return human-readable strings for every value in a state dict."""
    lines = []
    for key, val in state.items():
        line = _format_one(key, val, range_map)
        if line is not None:
            lines.append(line)
    return lines


def format_readable_changes(prev_state, curr_state, range_map):
    """Return human-readable strings for values that changed between two parsed reports."""
    lines = []
    for key, curr_val in curr_state.items():
        prev_val = prev_state.get(key)
        if prev_val is None or curr_val == prev_val:
            continue
        line = _format_one(key, curr_val, range_map)
        if line is not None:
            lines.append(line)
    return lines


def _run_raw_loop(dev, show_all):
    """Read loop for raw byte mode — prints hex rows with changed bytes highlighted."""
    prev = []
    while True:
        data = dev.read(64)
        if data:
            changes = bytes_diff(prev, data)
            if changes or show_all:
                changed_indices = {c[0] for c in changes}
                hex_row = format_report_line(data, changed_indices)
                descs = ("  " + "  ".join(describe_change(i, o, n) for i, o, n in changes)
                         if changes else "")
                print(f"{hex_row}{descs}")
            prev = data
        else:
            time.sleep(0.001)


def _collect_initial_state(dev, rd):
    """Read reports until all report IDs seen (or 500 ms); return merged state dict."""
    report_ids = set(rd.input_report_ids or [None])
    seen_ids = set()
    merged = {}
    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        data = dev.read(64)
        if data:
            rid = data[0] if rd.input_report_ids else None
            parsed = _parse_report(rd, data)
            merged.update(parsed)
            seen_ids.add(rid)
            if seen_ids >= report_ids:
                break
        else:
            time.sleep(0.001)
    return merged


def _run_readable_loop(dev, rd_ctx):
    """Read loop for human-readable mode — decodes axes, buttons and hats."""
    rd, range_map = rd_ctx

    state = _collect_initial_state(dev, rd)
    for line in format_all_values(state, range_map):
        print(line)
    if state:
        print()

    while True:
        data = dev.read(64)
        if data:
            curr = _parse_report(rd, data)
            for line in format_readable_changes(state, curr, range_map):
                print(line)
            state.update(curr)
        else:
            time.sleep(0.001)


def monitor(path, show_all=False, readable=False):
    """Open a HID device and print report changes until Ctrl+C."""
    dev = hid.device()
    try:
        dev.open_path(path)
    except OSError as e:
        print(f"Cannot open device: {e}")
        if sys.platform == 'linux':
            print("On Linux, add a udev rule or run as root for hidraw access.")
        return

    print(f"Opened: {dev.get_manufacturer_string() or '?'} — {dev.get_product_string() or '?'}")

    rd_ctx = None
    if readable:
        if not HID_PARSER_AVAILABLE:
            print("Warning: hid-parser not installed (pip install hid-parser).")
            print("Falling back to raw mode.\n")
        else:
            rd_ctx = build_readable_context(dev)
            if rd_ctx is None:
                print("Warning: could not read report descriptor. Falling back to raw mode.\n")
            else:
                print("Human-readable mode. Ctrl+C to stop.\n")

    if rd_ctx is None:
        print("Monitoring HID reports. Changed bytes shown in yellow. Ctrl+C to stop.\n")

    dev.set_nonblocking(1)
    try:
        if rd_ctx is not None:
            _run_readable_loop(dev, rd_ctx)
        else:
            _run_raw_loop(dev, show_all)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        dev.close()


def main():
    """Parse arguments and either list HID devices or monitor one."""
    parser = argparse.ArgumentParser(
        description="List and monitor USB HID devices (joysticks, pedals, gamepads, etc.)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('-l', '--list', action='store_true',
                        help='List all HID devices (default if no device specified)')
    parser.add_argument('-j', '--joystick', action='store_true',
                        help='Only show joystick/gamepad/pedal devices')
    parser.add_argument('-d', '--device', metavar='N', type=int,
                        help='Monitor device number N from list')
    parser.add_argument('--vid-pid', metavar='VID:PID',
                        help='Monitor device by VID:PID in hex (e.g. 044f:b10a)')
    parser.add_argument('-a', '--all', action='store_true', dest='show_all',
                        help='Print every report, not just changes (raw mode only)')
    parser.add_argument('-r', '--readable', action='store_true',
                        help='Human-readable mode: decode axes, buttons and hats'
                             ' (requires hid-parser)')
    args = parser.parse_args()

    if args.device is None and not args.vid_pid:
        devs = enumerate_devices(joystick_only=args.joystick)
        print_device_list(devs)
        if devs:
            print(f"\nMonitor a device:  {parser.prog} -d N   or   {parser.prog} --vid-pid VID:PID")
        return

    if args.vid_pid:
        try:
            vid_s, pid_s = args.vid_pid.split(':')
            vid, pid = int(vid_s, 16), int(pid_s, 16)
        except ValueError:
            sys.exit("Invalid VID:PID. Use hex like: 044f:b10a")
        for d in hid.enumerate():
            if d['vendor_id'] == vid and d['product_id'] == pid:
                monitor(d['path'], show_all=args.show_all, readable=args.readable)
                return
        sys.exit(f"Device {args.vid_pid} not found.")

    devs = enumerate_devices(joystick_only=args.joystick)
    if args.device >= len(devs):
        sys.exit(f"Device index {args.device} out of range (0–{len(devs) - 1})")
    monitor(devs[args.device]['path'], show_all=args.show_all, readable=args.readable)


if __name__ == '__main__':
    main()
