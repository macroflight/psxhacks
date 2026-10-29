"""Automated flight testing on a PSX test sim: reposition, fly stably, record (docs/flighttest.md).

Writes to the sim (repositions it, presses MCP buttons): use it only on
a test sim, never on someone's flight.
"""
import argparse
import sys
import threading
import time

from flighttest import accel, analyse, legs
from flighttest.sim import Sim, Target, start_position
from psxhacks_version import get_version

__version__ = get_version("psxutils", __file__)


def log(msg: str) -> None:
    """Print a timestamped progress line."""
    print(f"{time.strftime('%H:%M:%S')} {msg}", flush=True)


def _cmd_load(sim: Sim, args) -> None:
    overrides = dict(o.split("=", 1) for o in args.set)
    if args.pos:
        lat, lon, heading, alt, tas = args.pos
        overrides["StartPiBaHeAlVsTasYw"] = start_position(lat, lon, heading, alt, tas)
    count = sim.load_situ(args.situ, overrides, engage_modes=not args.no_modes)
    log(f"loaded {args.situ}: {count} variables")


def _cmd_stable(sim: Sim, args) -> None:
    ok, why = sim.wait_stable(Target(*args.target), timeout=args.timeout)
    log(f"{'STABLE' if ok else 'NOT STABLE'} {why}")
    sys.exit(0 if ok else 1)


def _cmd_track(sim: Sim, args) -> None:
    if args.correct is None:
        log(f"ground track {sim.measure_track(args.seconds)}")
    else:
        log(f"heading window {sim.correct_track(args.correct, args.seconds):+d} deg")


def _cmd_record(sim: Sim, args) -> None:
    base = args.out[:-4] if args.out.endswith(".rec") else args.out
    workers = [threading.Thread(target=func, daemon=True,
                                args=(base + ext, args.seconds, args.psx_host, port))
               for port, func, ext in ((args.accel_port, accel.record, ".accel"),
                                       (args.boost_port, accel.record_raw, ".boost"))
               if port]
    for worker in workers:
        worker.start()
    log(f"recorded {sim.record(args.out, args.seconds)} lines to {args.out}")
    for worker in workers:
        worker.join(timeout=10.0)


def main() -> None:
    """Parse the command line and run a subcommand."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument('--psx-host', default='127.0.0.1',
                        help="PSX/router host (default 127.0.0.1)")
    parser.add_argument('--psx-port', type=int, default=10747,
                        help="PSX/router port (default 10747)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_load = sub.add_parser(
        "load", help="load a situation (freeze, load1/2, variables, load3, release)")
    p_load.add_argument("situ")
    p_load.add_argument("--pos", type=float, nargs=5,
                        metavar=("LAT", "LON", "HDG_TRUE", "ALT_FT", "TAS_KT"),
                        help="start in level flight here")
    p_load.add_argument("--set", action="append", default=[], metavar="NAME=VALUE",
                        help="override a situation variable (repeatable)")
    p_load.add_argument("--no-modes", action="store_true",
                        help="don't press HDG SEL and FLCH after release")

    p_stable = sub.add_parser("stable", help="wait until flying stably as set up")
    p_stable.add_argument("target", type=float, nargs=4,
                          metavar=("LAT", "LON", "HDG_TRUE", "ALT_FT"))
    p_stable.add_argument("--timeout", type=float, default=150.0)

    p_track = sub.add_parser("track",
                             help="measure the ground track, or correct the heading for it")
    p_track.add_argument("--correct", type=float, metavar="TRACK_TRUE")
    p_track.add_argument("--seconds", type=float, default=15.0)

    p_record = sub.add_parser("record", help="record position, bursts and weather zones")
    p_record.add_argument("out")
    p_record.add_argument("seconds", type=float)
    p_record.add_argument("--accel-port", type=int, metavar="PORT",
                          help="also record frankenboostproxy's --accel-port to OUT.accel")
    p_record.add_argument("--boost-port", type=int, metavar="PORT",
                          help="also record the raw PSX boost feed (e.g. frankenboostproxy's "
                               "relay) to OUT.boost, for vertical g")

    p_send = sub.add_parser("send", help="send raw lines, e.g. Qi129=1")
    p_send.add_argument("lines", nargs="+")

    p_analyse = sub.add_parser("analyse",
                               help="how hard the autopilot works in recordings (turbulence)")
    p_analyse.add_argument("recordings", nargs="+")

    p_legs = sub.add_parser("legs", help="fly a plan of test legs (TOML)")
    p_legs.add_argument("plan")
    p_legs.add_argument("--skip", type=int, default=0, help="skip the first N legs")

    args = parser.parse_args()
    sim = Sim(args.psx_host, args.psx_port)
    if args.command == "load":
        _cmd_load(sim, args)
    elif args.command == "stable":
        _cmd_stable(sim, args)
    elif args.command == "track":
        _cmd_track(sim, args)
    elif args.command == "record":
        _cmd_record(sim, args)
    elif args.command == "send":
        sim.send(args.lines)
    elif args.command == "analyse":
        for path in args.recordings:
            print(analyse.report(path))
    elif args.command == "legs":
        legs.fly_plan(sim, args.plan, log, skip=args.skip)


if __name__ == "__main__":
    main()
