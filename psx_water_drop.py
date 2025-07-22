"""Simulate dropping the water load from a 747 Global Supertanker."""
# pylint: disable=missing-function-docstring,global-statement,invalid-name,consider-using-f-string
import asyncio
import re
import threading
import time
from psx import Client

PSX = None
CONNECTED = False
TRIGGERED = False


def setup():
    """Set up PSX connection."""
    global CONNECTED
    print("Simulation started")
    CONNECTED = True
    PSX.send("name", "WaterDrop:FRANKEN.PY water drop script")


def teardown():
    """Shut down PSX connection."""
    print("Simulation stopped")


def press_trigger(key, value):
    """Handle trigger press."""
    global TRIGGERED
    pressed = False
    triggertype = None
    if key == 'addon':
        if re.match(r"WATERBOMBER", value):
            pressed = True
            triggertype = 'addon'
    elif key == 'LcpPttCp':
        pressed = True
        triggertype = 'LcpPttCp'
    else:
        pressed = True
        triggertype = 'UNKNOWN'

    if pressed and not TRIGGERED:
        print("Trigger pressed (%s)" % triggertype)
        TRIGGERED = True


def psx_thread():
    """Start PSX communication thread."""
    global PSX
    with Client() as PSX:
        # PSX.logger = lambda msg: print(f"   {msg}")
        PSX.subscribe("id")
        PSX.subscribe("version", lambda key, value:
                      print(f"Connected to PSX {value} as client #{PSX.get('id')}"))
        PSX.subscribe("TrueZfw")
        PSX.subscribe("LcpPttCp", press_trigger)
        PSX.subscribe("addon", press_trigger)

        PSX.onResume = setup
        PSX.onPause = teardown
        PSX.onDisconnect = teardown
        try:
            asyncio.run(PSX.connect())
        except KeyboardInterrupt:
            print("\nStopped by keyboard interrupt (Ctrl-C)")


def lb2kg(lb):
    """Convert pounds to kilos."""
    return float(lb) / 2.20462


def kg2lb(kg):
    """Convert kilos to pounds."""
    return float(kg) * 2.20462


def psx_send_and_set(psx_variable, new_psx_value):
    """Send variable to PSX and store in local db."""
    PSX.send(psx_variable, new_psx_value)
    PSX._set(psx_variable, new_psx_value)  # pylint: disable=protected-access


MAX_ZFW = 290000.0
MAX_WATER_LOAD = 74000  # kg
REALISTIC_FILL_TIME_FULL_LOAD = 1800  # seconds

if __name__ == "__main__":
    print("""
When triggered, this script simulates a water drop by reducing ZFW
by a certain amount over a certain period.

    1: Set your ZFW without water load

    2: Enter the amount of water to load below

    3: Enter the amount of water to drop per attack/trigger press

    4: Enter the drop rate in kg/s

    5: Choose fast or realistic (30 min for a full load) fill time

    6: Take off and fly to the fire

    7: Trigger water drops by pressing teh Captain's PTT on the
    glareshield (LcpPttCp) OR sending an addon=WATERBOMBER message to
    the PSX network

    8: When the last water has been dropped, the system resets and you
    can choose a new load

Hint: it is possible to cheat and refill the water in the air :)

""")

    # Connect to PSX
    psx_thread = threading.Thread(target=psx_thread, daemon=True)
    psx_thread.start()

    while True:
        print("Waiting for PSX connection...")
        if PSX is not None:
            break
        time.sleep(1.0)
    print("Connected to PSX!")

    while PSX.get("TrueZfw") is None:
        time.sleep(1.0)

    # Get our original ZFW
    zfw_kg = lb2kg(PSX.get("TrueZfw"))

    try:
        while True:
            print("Drop system reset!")
            TRIGGERED = False

            while True:  # loop until good data entered
                water_load_kg = float(input('Water load (kg): '))
                if water_load_kg > MAX_WATER_LOAD:
                    print("Maximum water load is %.1f kg, using max load" % MAX_WATER_LOAD)
                    water_load_kg = MAX_WATER_LOAD

                if water_load_kg + zfw_kg > MAX_ZFW:
                    print("Requested water load would exceed MAXZFW (%.1f > %.1f)" % (
                        water_load_kg + zfw_kg, MAX_ZFW
                    ))
                    continue

                water_drop_per_trigger = float(input('Water drop per trigger (kg): '))
                if water_drop_per_trigger > water_load_kg:
                    print("Will drop the entire load on trigger")
                    water_drop_per_trigger = water_load_kg

                water_drop_rate = float(input('Water drop rate (kg/s) [valid: 950-4000]: '))
                # "According to the company, the aircraft was capable of laying
                # down a swath of fire retardant 3 mi (4.8 km) long"
                # 3 miles at 140 kt == 77 seconds
                # 74t in 77s == 959 kg/s

                # "The 747 can drop its entire load of 19,200 gallons (72,700
                # liters) in a line that's from three-quarters of a mile (1.2
                # kilometers) to 2 miles (3.2 kilometers) long and more than
                # 200 feet wide. But it can also make eight separate drops
                # from one load."
                # 0.75 nm at 140 kt == 19s => 3840 kg/s

                # So allow drop rates between 950 - 4000 kg/s

                if water_drop_rate > 4000:
                    print("Maximum water drop rate is 4000 kg/s")
                    continue
                if water_drop_rate < 950:
                    print("Minimum water drop rate is 950 kg/s")
                    continue

                water_fill_rate = str(input('Water fill rate - (R)ealistic or (F)ast: '))
                if water_fill_rate in ['R', 'realistic', 'Realistic']:
                    fast_fill = False
                elif water_fill_rate in ['F', 'fast', 'Fast']:
                    fast_fill = True
                else:
                    print("Please choose (R)ealistic or (I)nstant fill rate")
                    continue
                break

            print("Water loading started (%.1f kg)" % water_load_kg)

            if fast_fill:
                fill_rate = 7400  # 10s to full
            else:
                fill_rate = MAX_WATER_LOAD / REALISTIC_FILL_TIME_FULL_LOAD

            # We start at the current ZFW
            zfw_new_kg = zfw_kg
            step_time = 10.0

            while water_load_kg > 0:
                time.sleep(step_time)
                fill = min(water_load_kg, step_time * fill_rate)
                zfw_new_kg += fill
                water_load_kg -= fill
                psx_send_and_set("TrueZfw", str(int(kg2lb(zfw_new_kg))))
                print("ZFW is now %.1f kg, remaining water to load: %.1f kg" % (
                    zfw_new_kg, water_load_kg))

            # Reset water_load_kg to actual load
            water_load_kg = zfw_new_kg - zfw_kg

            print("WATER LOADING COMPLETE. GO FIGHT A FIRE, CAPTAIN!")

            while water_load_kg > 100.0:
                print("Water remaining: %.1f kg" % water_load_kg)
                print("Press trigger to drop")
                # Wait for trigger
                while True:
                    time.sleep(1.0)
                    if TRIGGERED:
                        print("Dropping water!")
                        dropped = 0.0
                        step_time = 1.0
                        while dropped < water_drop_per_trigger:
                            time.sleep(step_time)
                            drop = step_time * water_drop_rate
                            # however, do not exceed the drop per trigger
                            drop = min(drop, water_drop_per_trigger)
                            # and we cannot drop more water than we got remaining
                            drop = min(drop, water_load_kg)
                            if drop <= 0:
                                break
                            zfw_new_kg -= drop
                            water_load_kg -= drop
                            dropped += drop
                            psx_send_and_set("TrueZfw", str(int(kg2lb(zfw_new_kg))))
                            print("ZFW is now %.1f kg, water onboard is %.1f kg" % (
                                zfw_new_kg, water_load_kg))
                        print("Drop complete!")
                        TRIGGERED = False
                        break
    except KeyboardInterrupt as exc:
        raise SystemExit("Stopped") from exc
