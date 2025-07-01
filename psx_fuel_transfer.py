"""Convert ZFW into fuel in centre tank, i.e simulate a fuel tank in the hold."""
import asyncio
import datetime
import sys
import threading
import time
from psx import Client
# pylint: disable=missing-function-docstring,global-statement,invalid-name

PSX = None
CONNECTED = False


def setup():
    """Set up PSX connection."""
    global CONNECTED
    print("Simulation started")
    CONNECTED = True
    PSX.send("name", "FuelXfer:FRANKEN.PY fuel transfer script")


def teardown():
    """Shut down PSX connection."""
    print("Simulation stopped")


def psx_thread():
    """Start PSX communication thread."""
    global PSX
    with Client() as PSX:
        # PSX.logger = lambda msg: print(f"   {msg}")
        PSX.subscribe("id")
        PSX.subscribe("version", lambda key, value:
                      print(f"Connected to PSX {value} as client #{PSX.get('id')}"))
        PSX.subscribe("TrueZfw")
        PSX.subscribe("FuelQty")
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


CENTER_MAX = 52150.0   # CENTER capacity (kg)
INTERVAL = 60.0        # How often we transfer (seconds)
RATE_MAX = 1000.0      # Transfer pump max speed, kg/minute

if __name__ == "__main__":
    print("""
This script simulates additional tanks in the hold. Usage:
- Make a flight plan using a Simbrief profile that has enough fuel capacity
- Set max fuel in PSX (183.4t for 744ER with all tanks fitted)
- Set the flight plan ZFW in PSX
- Subtract PSX fuel from flight plan fuel
- Add the etra fuel to the PSX ZFW
- Run the script, enter the flight plan ZFW. The script will now
  transfer fuel from the simulated additional tanks to teh centre tanks

""")
    zfw_target = float(input('ZFW from flight plan (kg): '))

    print(f"Will try to reduce ZFW to {zfw_target:.0f} kg by moving weight from ZFW to center tank")

    psx_thread = threading.Thread(target=psx_thread, daemon=True)
    psx_thread.start()

    while True:
        print("Waiting for PSX connection...")
        if PSX is not None:
            break
        time.sleep(1.0)
    print("Connected to PSX!")
    zfw_kg = 999999999999  # safe start
    while zfw_kg > zfw_target:
        print(f"{zfw_kg} > {zfw_target}")
        if not CONNECTED:
            print(f"Not yet, CONNECTED is {CONNECTED}...")
            time.sleep(3.0)
            continue
        try:
            print()
            print(f"Status at {datetime.datetime.now()}")
            zfw_kg = lb2kg(PSX.get("TrueZfw"))
            fuelqty = PSX.get("FuelQty")
            # Remove intial "d"
            fuelqty = fuelqty[1:]
            fuelqty_split = fuelqty.split(';')
            fuelqty_total = 0.0
            for elem in range(0, 9):
                fuelqty_total += int(fuelqty_split[elem]) / 10
            fuelqty_total_kg = lb2kg(fuelqty_total)
            fuelqty_total_real = (zfw_kg - zfw_target) + fuelqty_total_kg
            print(f"Sum of fuel in PSX tanks: {fuelqty_total_kg:.0f}")
            print(f"Actual fuel remaining:    {fuelqty_total_real:.0f}")

            # Sample:
            # d300292;830812;830790;300292;88295;88295;1142282;221102;214951;404491;587;
            #  main1   main2  main3  main4

            fuelqty_center_kg = lb2kg(float(fuelqty_split[6]) / 10)
            print(
                f"Current ZFW={zfw_kg:.0f} (target={zfw_target:.0f}), " +
                f"CENTER={fuelqty_center_kg:.0f} kg (max={CENTER_MAX}), " +
                f"to transfer: {(zfw_kg - zfw_target):.0f}"
            )
            center_space_avail = max(0, CENTER_MAX - fuelqty_center_kg)
            # Largest possible transfer is zfw-zfw_target
            max_transfer = zfw_kg - zfw_target
            # The amount we can actually transfer is the smallest of
            # max_transfer, space available and pump capacity
            proposed_transfer = min(max_transfer, center_space_avail, (RATE_MAX / (60 / INTERVAL)))
            if proposed_transfer <= 1.0:
                print("No transfer possible right now")
            else:
                print(f"Starting transfer of {proposed_transfer:.2f} kg from ZFW to CENTER")
                zfw_kg_new = zfw_kg - proposed_transfer
                fuelqty_center_kg_new = fuelqty_center_kg + proposed_transfer

                fuelqty_split[6] = str(int(10 * kg2lb(fuelqty_center_kg_new)))
                fuelqty_new = "d" + ";".join(fuelqty_split)

                psx_send_and_set("TrueZfw", str(int(kg2lb(zfw_kg_new))))
                psx_send_and_set("FuelQty", fuelqty_new)
            print(f"Sleeping {INTERVAL} s")
            time.sleep(INTERVAL)

        except KeyboardInterrupt:
            print("\nStopped by keyboard interrupt (Ctrl-C)")
            sys.exit()
    print("FUEL TRANFER FINISHED")
    time.sleep(36000)
