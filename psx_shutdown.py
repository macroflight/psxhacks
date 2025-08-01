"""Shut down PSX cleanly."""
# pylint: disable=missing-function-docstring,global-statement,invalid-name
import asyncio
import logging
import sys
import threading
import time
from psx import Client

PSX = None
CONNECTED = False


def setup():
    """Set up PSX connection."""
    global CONNECTED
    print("Simulation started")
    CONNECTED = True
    PSX.send("name", "Shutdown:FRANKEN.PY shutdown script")


def teardown():
    """Shut down PSX connection."""
    print("Simulation stopped")


def psx_thread(name, x, y):  # pylint: disable=unused-argument
    """Start PSX communication thread."""
    global PSX
    logging.info("Thread %s starting", name)
    with Client() as PSX:
        PSX.logger = lambda msg: print(f"   {msg}")
        PSX.subscribe("id")
        PSX.subscribe("version", lambda key, value:
                      print(f"Connected to PSX {value} as client #{PSX.get('id')}"))
        PSX.onResume = setup
        PSX.onPause = teardown
        PSX.onDisconnect = teardown
        try:
            asyncio.run(PSX.connect())
        except KeyboardInterrupt:
            print("\nStopped by keyboard interrupt (Ctrl-C)")


if __name__ == "__main__":
    LOGFORMAT = "%(asctime)s: %(message)s"
    logging.basicConfig(format=LOGFORMAT, level=logging.INFO,
                        datefmt="%H:%M:%S")
    psx_thread = threading.Thread(target=psx_thread, args=("PSX"), daemon=True)
    psx_thread.start()

    while True:
        print("Waiting for PSX connection...")
        if PSX is not None:
            break
        time.sleep(1.0)
    print("Connected to PSX!")
    retry = 0
    while True:
        retry += 1
        if retry > 10:
            raise SystemExit("Giving up")
        if not CONNECTED:
            print(f"Not yet, CONNECTED is {CONNECTED}...")
            time.sleep(1.0)
            continue
        try:
            print("Sending pleaseBeSoKindAndQuit to PSX")
            PSX.writer.write("pleaseBeSoKindAndQuit\n".encode())
            raise SystemExit("Exiting")
        except KeyboardInterrupt:
            print("\nStopped by keyboard interrupt (Ctrl-C)")
            sys.exit()
