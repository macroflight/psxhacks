"""Watch a single PSX variable."""
# pylint: disable=missing-function-docstring,duplicate-code
import asyncio
import sys
from psx import Client
from psxhacks_version import get_version

__version__ = get_version("psxutils", __file__)
__MY_CLIENT_ID__ = 'SHOWPSX'
__MY_DISPLAY_NAME__ = 'Show contents of PSX keyword'


def psx_setup():
    """Run when connected to PSX."""
    print("Simulation started")


def psx_teardown():
    """Run when disconnected from PSX."""
    print("Simulation stopped")
    psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")
    psx.send("clientName", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__} {__version__}")


def print_change(key, value):
    """Print change to variable."""
    print(f"PSX {key} is now {value}")


print(f"show_psx version {__version__} starting")
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
