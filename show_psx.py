"""Watch a single PSX variable."""
# pylint: disable=missing-function-docstring,duplicate-code
import asyncio
import sys
from psx import Client


def psx_setup():
    """Run when connected to PSX."""
    print("Simulation started")


def psx_teardown():
    """Run when disconnected from PSX."""
    print("Simulation stopped")
    psx.send("name", "show_psx:FRANKEN.PY script that shows a PSX variable")


def print_change(key, value):
    """Print change to variable."""
    print(f"PSX {key} is now {value}")


psx_variables = sys.argv[1].split(",")
print(f"Watching {psx_variables}")

with Client() as psx:
    psx.logger = lambda msg: print(f"   {msg}")
    psx.subscribe("id")
    psx.subscribe("version", lambda key, value:
                  print(f"Connected to PSX {value} as client #{psx.get('id')}"))
    for psx_variable in psx_variables:
        psx.subscribe(psx_variable, print_change)
    psx.onResume = psx_setup
    psx.onPause = psx_teardown
    psx.onDisconnect = psx_teardown

    try:
        asyncio.run(psx.connect())
    except KeyboardInterrupt as exc:
        raise SystemExit("Stopped by keyboard interrupt (Ctrl-C)") from exc
