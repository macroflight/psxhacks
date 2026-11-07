"""Measure how fast a PSX boost server delivers data: rate and longest gap.

Connects to a PSX boost server (or frankenboostproxy's raw relay port) and,
every --interval seconds, prints the number of boost lines per second and
the longest gap between two lines. The boost server nominally delivers about
20 Hz in flight and a little less when there's little to report (e.g.
parked); a sim whose PSX can't keep up shows a lower rate or long gaps. Run
it next to a bare PSX main server on real hardware to get a baseline.

Usage: show_boost_rate.py [--boost-host 127.0.0.1] [--boost-port 10749] [--interval 10]
                          [--log-file FILE]
Every summary also goes to a log file (default show_boost_rate-<UTC date>.log
in the current directory), handy on Windows where stdout is hard to capture.
Read-only: it never sends anything but its own name.
"""
import argparse
import asyncio
import time

from psxhacks_version import get_version

__version__ = get_version("psxutils", __file__)
__MY_CLIENT_ID__ = 'BOOSTRATE'


LOG = None


def say(text):
    """Print a line and append it to the log file."""
    print(text, flush=True)
    if LOG is not None:
        LOG.write(text + "\n")
        LOG.flush()


def report(count, longest_gap, interval, flight):
    """Report one line: rate, longest gap and the last sample's phase (UTC time)."""
    phase = {"F": "flight", "G": "ground"}.get(flight, "?")
    say(f"{time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}  {count / interval:5.1f} Hz  "
        f"longest gap {longest_gap * 1000:6.0f} ms  ({phase})")


async def measure(host, port, interval):
    """Connect (and reconnect) to the boost server and report until interrupted."""
    while True:
        try:
            reader, writer = await asyncio.open_connection(host, port)
        except OSError as exc:
            say(f"Cannot connect to boost server {host}:{port}: {exc} - retrying in 5 s")
            await asyncio.sleep(5)
            continue
        say(f"Connected to boost server {host}:{port}")
        writer.write(f"name={__MY_CLIENT_ID__}:Show boost rate {__version__}\n".encode())
        await writer.drain()
        count, longest, last, flight = 0, 0.0, None, None
        window_end = time.monotonic() + interval
        try:
            while True:
                timeout = max(0.0, window_end - time.monotonic())
                try:
                    line = await asyncio.wait_for(reader.readline(), timeout)
                except asyncio.TimeoutError:
                    line = None
                now = time.monotonic()
                if line == b"":
                    raise ConnectionError("closed")
                if line:
                    if last is not None:
                        longest = max(longest, now - last)
                    last = now
                    count += 1
                    flight = line[:1].decode("ascii", "replace")
                if now >= window_end:
                    if last is not None:
                        longest = max(longest, now - last)   # still waiting counts as a gap
                    report(count, longest, interval, flight)
                    count, longest = 0, 0.0
                    window_end = now + interval
        except (ConnectionError, OSError) as exc:
            say(f"Boost server connection lost ({exc}) - reconnecting")
            writer.close()
            await asyncio.sleep(2)


def main():
    """Parse arguments and measure."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", maxsplit=1)[0])
    parser.add_argument("--boost-host", default="127.0.0.1", help="Boost server host")
    parser.add_argument("--boost-port", type=int, default=10749, help="Boost server port")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds per report")
    parser.add_argument("--log-file", default=None,
                        help="Log file (default show_boost_rate-<UTC date>.log here)")
    args = parser.parse_args()
    global LOG  # pylint: disable=global-statement
    path = args.log_file or time.strftime("show_boost_rate-%Y%m%d.log", time.gmtime())
    LOG = open(path, "a", encoding="utf-8")  # pylint: disable=consider-using-with
    say(f"show_boost_rate version {__version__} starting, logging to {path}")
    try:
        asyncio.run(measure(args.boost_host, args.boost_port, args.interval))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
