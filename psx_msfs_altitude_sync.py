"""Workaround for PSX/MSFS altitude mismatch.

Hopefully this will not be needed once we get that updated vPIlot.
"""

# pylint:disable=missing-function-docstring,fixme

import asyncio
import re
import time
import winsound  # pylint:disable=import-error
from threading import Thread

import ambiance  # pylint:disable=import-error
from psx import Client  # pylint:disable=import-error
import SimConnect  # pylint:disable=import-error

# FIXME: is this needed?
global psx  # pylint:disable=invalid-name,global-at-module-level

# PSX altitude and altimeter mode. Updated from callback, used from update thread.
global PSX_PFD_ALTITUDE  # pylint:disable=global-at-module-level
global PSX_PFD_ALTIMETER_MODE  # pylint:disable=global-at-module-level

# Keeps track of the last set virtual pressure (so we can reset it when the weather updates)
global CURRENT_VIRTUAL_PRESSURE  # pylint:disable=global-at-module-level

# Sane start values
psx = None  # pylint:disable=invalid-name

PSX_PFD_ALTITUDE = 0.0
PSX_PFD_ALTIMETER_MODE = None
CURRENT_VIRTUAL_PRESSURE = None

# How often should we recalculate the sync pressure?
# MSFS weather changes as we move, etc.
# Also, we need to give PSX time to drift back to the selected altitude.
# Until is has, we cannot calculate a new reliable sync pressure
INTERVAL_UPDATE_SYNC_PRESSURE = 10.0

# How many hPa may we change the local QNH with per cycle?
SYNC_PRESSURE_MAX_CHANGE_HPA = 10.0

# How often should we toggle autoupdate on and then off to get new weather zones?
INTERVAL_UPDATE_METAR = 30.0

# How much difference is allowed before we change the sync value? (hPa)
SYNC_PRESSURE_ALLOWED_DIFF = 2.0

# How big a jump in pressure (on weather update) will trigger a jump back to the virtual pressure?
STEP_ON_PRESSURE_DIFF_LIMIT = 20.0


ALLZONES = [
    "WxBasic",
    "Wx1",
    "Wx2",
    "Wx3",
    "Wx4",
    "Wx5",
    "Wx6",
    "Wx7",
]


def psx_setup():
    """Code to run when PSX connection initialized."""
    print("Simulation started")
    # GroundSpeed is a DEMAND variable that needs to be requested from PSX.
    psx.send("demand", "LeftPfdAlt")


def psx_teardown():
    """Code to run when PSX connection stopped."""
    print("Simulation stopped")


def update_all_weather_zones_qnh(new_qnh_hpa):
    """Push the virtual QNH to all weather zones."""
    print(f"Updating QNH in all zones to {new_qnh_hpa:.2f}")
    for zone in ALLZONES:
        wxdata = psx.get(zone)
        update_weather_zone_qnh(zone, wxdata, new_qnh_hpa)


def update_weather_zone_qnh(varname, wxdata, new_qnh_hpa):
    """Replace QNH field in wxdata with inhg (float) value, then send to PSX.

    Alternate mode: if reset=True, compare actual QHN in zone to the last set virtual pressure.
    If they differ by too much, force the actual pressure to be the last set virtual pressure.
    This is used to quickly recover from a pressure upset after a weather update.
    """
    # print(f"Updating pressure in {varname}: {wxdata} to {new_qnh_hpa:.2f} hPa")
    elems = wxdata.split(';')
    inhg_new = new_qnh_hpa * 0.029529983071445
    inhg_new_str = str(int(100 * inhg_new))
    elems[-1:] = [inhg_new_str]
    wxdata_new = ";".join(elems)
    # print(f"New {varname}: {wxdata_new}")
    psx._set(varname, wxdata_new)  # pylint:disable=protected-access
    psx.send(varname, wxdata_new)


def wxchange(zone, wxdata):
    """When the weather in a zone changes, restore virtual QNH if needed.

    Detect the sudden jump in zone QNH that happens when a weather
    update is made, and ensure we override it quickly.
    """
    # FIXME: not needed?
    global CURRENT_VIRTUAL_PRESSURE  # pylint:disable=global-variable-not-assigned
    elems = wxdata.split(';')
    inhg_now = elems[-1:][0]
    hpa_now = (float(inhg_now) / 100) / 0.029529983071445
    # print(f"DEBUG: hpa_now on {zone} is {hpa_now:.2f}")
    if CURRENT_VIRTUAL_PRESSURE is not None:
        diff = abs(CURRENT_VIRTUAL_PRESSURE - hpa_now)
        if diff > STEP_ON_PRESSURE_DIFF_LIMIT:
            winsound.Beep(440, 500)
            print(f"RESET: Pressure diff {diff} hPa is too great, reset {zone}" +
                  f"to last virtual pressure {CURRENT_VIRTUAL_PRESSURE:.2f} hPa")
            update_weather_zone_qnh(zone, wxdata, CURRENT_VIRTUAL_PRESSURE)


def update_psx_altitude(_, value):
    """Update global PSX altitude variables when altitude changes."""
    # FIXME: global needed?
    global PSX_PFD_ALTITUDE  # pylint:disable=global-statement
    global PSX_PFD_ALTIMETER_MODE  # pylint:disable=global-statement
    PSX_PFD_ALTIMETER_MODE = value[:1]
    (alt_qnh, alt_std, _) = value[1:].split(';')
    if PSX_PFD_ALTIMETER_MODE == "s":
        PSX_PFD_ALTITUDE = float(alt_std)
    elif PSX_PFD_ALTIMETER_MODE == "b":
        PSX_PFD_ALTITUDE = float(alt_qnh)
    else:
        print(f"Unsupported mode {PSX_PFD_ALTIMETER_MODE}")
        return


def metar_to_qnh(metar):
    """Extract the QNH from a METAR string and return."""
    qnh = None
    for elem in metar.split(" "):
        rematch = re.match(r"^Q([0-9]+$)", elem)
        if rematch:
            qnh = float(rematch.group(1))
        rematch = re.match(r"^A([0-9]+)$", elem)
        if rematch:
            qnh_inhg = float(rematch.group(1)) / 100.0
            qnh = 33.863886666667 * qnh_inhg

    return qnh


def get_active_qnh(from_metar=False):
    """Get the PSX QNH from the currently active weather zone."""
    activezone = psx.get("FocussedWxZone")
    if activezone is None:
        print("WARNING: no active zone")
        return None
    if activezone == "0":
        zone = "WxBasic"
        metarzone = None
    else:
        zone = "Wx" + activezone
        metarzone = "Metar" + activezone

    if from_metar:
        metar = psx.get(metarzone)
        metar_qnh = metar_to_qnh(metar)
        # print(f"get_active_qnh: QHN from METAR is {metar_qnh} (data: {metar})")
        return metar_qnh

    wxdata = psx.get(zone)
    if wxdata is None:
        return None
    elems = wxdata.split(';')
    inhg_str = elems[-1]
    inhg = float(inhg_str) / 100.0
    hpa = 33.863886666667 * inhg
    print(f"get_active_qnh: QHN from active zone is {hpa:.2f}")
    return hpa


def update_sync_qnh():
    """Update the QNH value we should set everywhere when sync is on."""
    global PSX_PFD_ALTITUDE  # pylint:disable=global-statement,global-variable-not-assigned
    global CURRENT_VIRTUAL_PRESSURE  # pylint:disable=global-statement

    # Get MSFS pressure altitude (what vPilot will send to VATSIM and Euroscope will see)
    msfs_pa_m = aq.get("PRESSURE_ALTITUDE")
    if msfs_pa_m is None:
        print("update_sync_qnh: got no altitude data from MSFS, SKIP update")
        return False

    # Get PSX PFD altimeter altitude in m
    psx_alt_m = PSX_PFD_ALTITUDE * 0.3048  # convert to m

    # Get active QNH in currently active zone
    active_qnh = get_active_qnh()
    if active_qnh is None:
        print("Got no active QNH")
        return False
    print(f"Active QNH is {active_qnh:.2f}")

    if PSX_PFD_ALTIMETER_MODE == "b":
        print(f"PSX altimeter mode {PSX_PFD_ALTIMETER_MODE} - move towards METAR QNH")
        # Use QHN in local zone (from METAR) as target
        wanted_qnh = get_active_qnh(from_metar=True)
        if wanted_qnh is None:
            print("Got no active QNH from METAR, no update possible")
            return False
        print(f"update_sync_qnh: QNH from METAR={wanted_qnh:.2f})")
        press_diff_hpa = wanted_qnh - active_qnh
    elif PSX_PFD_ALTIMETER_MODE == "s":
        print(f"PSX altimeter mode {PSX_PFD_ALTIMETER_MODE} - move towards MSFS altitude")
        # Use pressure that will give the MSFS altitude as target
        # Calculate the difference between PSX and MSFS altitude
        alt_diff_m = msfs_pa_m - psx_alt_m
        # Calculate ISA pressure at sea level and at sea level +
        # altitude difference. This seems to be a good way to figure
        # out how much to change the PSX zone QNH
        pressure_sl = ambiance.Atmosphere(0.0).pressure[0]
        pressure_sl_plus_diff = ambiance.Atmosphere(alt_diff_m).pressure[0]
        press_diff_hpa = (pressure_sl_plus_diff - pressure_sl) / 100.0
        wanted_qnh = active_qnh + press_diff_hpa
        print(f"update_sync_qnh: MSFS={msfs_pa_m:.0f} m, PSX={psx_alt_m:.0f} m, " +
              f"QNH={active_qnh:.2f}) WANTED={wanted_qnh:.2f}")
    else:
        print(f"Unknown altimeter mode {PSX_PFD_ALTIMETER_MODE}, no update")
        return False

    if press_diff_hpa > SYNC_PRESSURE_ALLOWED_DIFF:
        new_qnh_hpa = active_qnh + min(press_diff_hpa, SYNC_PRESSURE_MAX_CHANGE_HPA)
        print(f"Adjusting current QNH up from {active_qnh:.2f} to {new_qnh_hpa:.2f} " +
              "[target {wanted_qnh:.2f}]")
        update_all_weather_zones_qnh(new_qnh_hpa)
        CURRENT_VIRTUAL_PRESSURE = new_qnh_hpa
    elif press_diff_hpa < -SYNC_PRESSURE_ALLOWED_DIFF:
        new_qnh_hpa = active_qnh + max(press_diff_hpa, -SYNC_PRESSURE_MAX_CHANGE_HPA)
        print(f"Adjusting current QNH down from {active_qnh:.2f} to {new_qnh_hpa:.2f} " +
              f"[target {wanted_qnh:.2f}]")
        update_all_weather_zones_qnh(new_qnh_hpa)
        CURRENT_VIRTUAL_PRESSURE = new_qnh_hpa
    else:
        print(f"update_sync_qnh: no pressure update needed (diff={press_diff_hpa})")

    return True


def update_sync_qnh_thread():
    """Every N seconds, update the QNH value we should set if sync is on."""
    while True:
        res = update_sync_qnh()
        if res is True:
            time.sleep(INTERVAL_UPDATE_SYNC_PRESSURE)
        else:
            print("update_sync_qnh returned False, sleeping 1s before retry")
            time.sleep(1.0)  # faster retry


def print_status():
    """Loop forever and print altitude status every N seconds."""
    while True:
        msfs_pa_m = aq.get("PRESSURE_ALTITUDE")
        if msfs_pa_m is None:
            msfs_pa = 0.0
        else:
            msfs_pa = 3.28084 * msfs_pa_m
        psx_alt = PSX_PFD_ALTITUDE
        psx_alt_m = PSX_PFD_ALTITUDE / 3.28084  # pylint:disable=unused-variable
        diff = msfs_pa - psx_alt  # pylint:disable=unused-variable
        print(
            f"===> STATUS: MSFS={msfs_pa:.0f} ({msfs_pa_m:.0f} m) PSX={psx_alt:.0f} " +
            "({psx_alt_m:.0f} m) [diff={diff:.0f}]")
        time.sleep(1.0)


if __name__ == '__main__':
    # Create SimConnect link
    print("Altitude sync starting, connecting to MSFS...")
    sm = SimConnect.SimConnect()
    print("SimConnect established connection to MSFS")
    # Note the default _time is 2000 to be refreshed every 2 seconds
    aq = SimConnect.AircraftRequests(sm, _time=2000)
    print("Connected to MSFS.")

    alt = aq.find("PRESSURE_ALTITUDE")
    alt.time = 200

    # Start a thread that will check MSFS altitude
    daemon = Thread(target=update_sync_qnh_thread, daemon=True, name='UPDATE_QNH')
    daemon.start()

    daemon2 = Thread(target=print_status, daemon=True, name='PRINT_STATUS')
    daemon2.start()

    with Client() as psx:
        # psx.logger = lambda msg: print(f"   {msg}")

        # Register some PSX variables we are interested in, and some callbacks.
        # NOTE: These subscriptions are registered in the connector module, but
        # until the PSX connection is activated, nothing will happen.
        psx.subscribe("id")
        psx.subscribe("version", lambda key, value:
                      print(f"Connected to PSX {value} as client #{psx.get('id')}"))

        psx.subscribe("LeftPfdAlt", update_psx_altitude)
        psx.subscribe("FocussedWxZone")

        psx.subscribe("WxBasic")
        for wx in [1, 2, 3, 4, 5, 6, 7]:
            psx.subscribe("Wx" + str(wx), wxchange)
            psx.subscribe("Metar" + str(wx))

        # Server-related action callbacks. Note that these do more than just the
        # MCDU head setup/teardown. Otherwise they could be direct mcdu.methods.
        psx.onResume = psx_setup
        psx.onPause = psx_teardown
        psx.onDisconnect = psx_teardown

        try:
            # Make and maintain a PSX Main Server connection until stopped.
            # Only here something actually happens!
            asyncio.run(psx.connect())
        except KeyboardInterrupt:
            print("\nStopped by keyboard interrupt (Ctrl-C)")
