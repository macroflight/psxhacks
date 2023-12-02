"""Watch a single PSX variable"""
import asyncio
import sys
import time
from psx import Client

def psx_setup():
    """Run when connected to PSX."""
    print("Simulation started")
    
def psx_teardown():
    """Run when disconnected from PSX."""
    print("Simulation stopped")

def print_change(key, value):
    print(f"PSX {key} is now {value}")
    
psx_variable = sys.argv[1]
print(f"Watching {psx_variable}")

with Client() as psx:
    psx.logger = lambda msg: print(f"   {msg}")
    psx.subscribe("id")
    psx.subscribe("version", lambda key, value:
                  print(f"Connected to PSX {value} as client #{psx.get('id')}"))
    psx.subscribe(psx_variable, print_change)
    psx.onResume     = psx_setup
    psx.onPause      = psx_teardown
    psx.onDisconnect = psx_teardown

    try:
        asyncio.run(psx.connect())
    except KeyboardInterrupt:
        logging.info("\nStopped by keyboard interrupt (Ctrl-C)")

