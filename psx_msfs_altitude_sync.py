"""
Altitude fix by setting all weather zones to a pressure that makes PSX altitude match
MSFS pressure altitude (which is reported by vPilot to VATSIM ATC).

Method:
- Stop automatic weather updates
- Thread 1: every N minutes, enable and then disable automatic weather updates for Y seconds.
- Thread 2: every M minutes, update global variable which is the pressure we need to have
- PSX module callbacks for all weather zone events - ensure pressure reset to what we need, but only pressure
- Below K feet, disable pressure updates and keep automatic weather on

"""
import datetime
import time
import asyncio
import sys
from psx import Client
from SimConnect import *
from threading import Thread
import ambiance
import winsound
import re

WEATHER_UPDATES=False

global psx

# PSX altitude and altimeter mode. Updated from callback, used from update thread.
global PSX_PFD_ALTITUDE
global PSX_PFD_ALTIMETER_MODE

# Keeps track of the last set virtual pressure (so we can reset it when the weather updates)
global CURRENT_VIRTUAL_PRESSURE

# Sane start values
psx = None

PSX_PFD_ALTITUDE = 0.0
PSX_PFD_ALTIMETER_MODE = None
CURRENT_VIRTUAL_PRESSURE = None

# How often should we recalculate the sync pressure? MSFS weather changes as we move
# Also, we need to give PSX time to drift back to the selected altitude. Until is has, we cannot calculate a new reliable sync pressure
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
    print("Simulation started")
    # GroundSpeed is a DEMAND variable that needs to be requested from PSX.
    psx.send("demand","LeftPfdAlt")

def psx_teardown():
    print("Simulation stopped")

def update_all_weather_zones_qnh(new_qnh_hpa):
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
    psx._set(varname, wxdata_new)
    psx.send(varname, wxdata_new)

def wxchange(zone, wxdata):
    """Callback used when a zone changes.
    
    Detect the sudden jump in zone QNH that happens when a weather update is made, and ensure we override it quickly.
    """
    global CURRENT_VIRTUAL_PRESSURE
    # print(f"wxchange callback for {zone}: new weather: {wxdata}, CVA is {CURRENT_VIRTUAL_PRESSURE}")
    elems = wxdata.split(';')
    inhg_now = elems[-1:][0]
    hpa_now = (float(inhg_now) / 100) / 0.029529983071445
    # print(f"DEBUG: hpa_now on {zone} is {hpa_now:.2f}")
    if CURRENT_VIRTUAL_PRESSURE is not None:
        diff = abs(CURRENT_VIRTUAL_PRESSURE - hpa_now)
        if diff > STEP_ON_PRESSURE_DIFF_LIMIT:
            winsound.Beep(440, 500)
            print(f"RESET: Pressure diff {diff} hPa is too great, reset {zone} to last virtual pressure {CURRENT_VIRTUAL_PRESSURE:.2f} hPa")
            update_weather_zone_qnh(zone, wxdata, CURRENT_VIRTUAL_PRESSURE)

def psx_ensure_wxautoset(mode=False):
    current = psx.get("WxAutoSet")
    # print(f"DEBUG: current autoset is {current}")
    if current == "1":
        current_mode = True
    elif current == "0":
        current_mode = False
    else:
        sys.exit(f"BAD autoset mode {current}")
    if current_mode is mode:
        # print(f"DEBUG: no mode change, autoset is {current_mode}")
        return
    if mode:
        psx._set("WxAutoSet", "1")
        psx.send("WxAutoSet", "1")
        print("Enabled WxAutoSet")
    else:
        psx._set("WxAutoSet", "0")
        psx.send("WxAutoSet", "0")
        print("Disabled WxAutoSet")

def update_psx_altitude(key, value):
    global PSX_PFD_ALTITUDE
    global PSX_PFD_ALTIMETER_MODE
    # print(f"PSX altitude has changed: {key} == {value}")
    last = PSX_PFD_ALTITUDE
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
    qhn = None
    for elem in metar.split(" "):
        m = re.match(r"^Q([0-9]+$)", elem)
        if m:
            qnh = float(m.group(1))
        m = re.match(r"^A([0-9]+)$", elem)
        if m:
            qnh_inhg = float(m.group(1)) / 100.0
            qnh = 33.863886666667 * qnh_inhg
            
    return qnh

def get_active_qnh(from_metar=False):
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

def update_sync_qnh(weather_update=False):
    """Update the QNH value we should set everywhere when sync is on."""    
    global PSX_PFD_ALTITUDE
    global CURRENT_VIRTUAL_PRESSURE

    # Get MSFS pressure altitude (what vPilot will send to VATSIM and Euroscope will see)
    msfs_pa_m = aq.get("PRESSURE_ALTITUDE")
    if msfs_pa_m is None:
        print("update_sync_qnh: got no altitude data from MSFS, SKIP update")
        return False

    # Get PSX PFD altimeter altitude in m
    psx_alt_m = PSX_PFD_ALTITUDE * 0.3048 # convert to m

    # Get active QNH in currently active zone
    active_qnh = get_active_qnh()
    if active_qnh is None:
        print("Got no active QNH")
        return False    
    print(f"Active QNH is {active_qnh:.2f}")

    if PSX_PFD_ALTIMETER_MODE == "b":
        print(f"PSX altimeter mode {PSX_PFD_ALTIMETER_MODE} - move towards METAR QNH")
        psx_ensure_wxautoset(True)
        # Use QHN in local zone (from METAR) as target
        wanted_qnh = get_active_qnh(from_metar=True)
        if wanted_qnh is None:
            print("Got no active QNH from METAR, no update possible")
            return False
        print(f"update_sync_qnh: QNH from METAR={wanted_qnh:.2f})")
        press_diff_hpa = wanted_qnh - active_qnh 
    elif PSX_PFD_ALTIMETER_MODE == "s":
        print(f"PSX altimeter mode {PSX_PFD_ALTIMETER_MODE} - move towards MSFS altitude")
        # Try with updates always on
        psx_ensure_wxautoset(True)
        
        #if weather_update and WEATHER_UPDATES:
        #    psx_ensure_wxautoset(True)
        #    time.sleep(2.0)
        # psx_ensure_wxautoset(False)
        # Use pressure that will give the MSFS altitude as target   
        # Calculate the difference between PSX and MSFS altitude
        alt_diff_m = msfs_pa_m - psx_alt_m
        # Calculate ISA pressure at sea level and at sea level + altitude difference. This seems to be a good way to figure out how much to change the PSX zone QNH
        p1 = ambiance.Atmosphere(0.0).pressure[0]
        p2 = ambiance.Atmosphere(alt_diff_m).pressure[0]
        press_diff_hpa = (p2 - p1) / 100.0
        wanted_qnh = active_qnh + press_diff_hpa
        print(f"update_sync_qnh: MSFS={msfs_pa_m:.0f} m, PSX={psx_alt_m:.0f} m, QNH={active_qnh:.2f}) WANTED={wanted_qnh:.2f}")
    else:
        print(f"Unknown altimeter mode {PSX_PFD_ALTIMETER_MODE}, no update")
        return False
    
    if press_diff_hpa > SYNC_PRESSURE_ALLOWED_DIFF:
        new_qnh_hpa = active_qnh + min(press_diff_hpa, SYNC_PRESSURE_MAX_CHANGE_HPA)
        print(f"Adjusting current QNH up from {active_qnh:.2f} to {new_qnh_hpa:.2f} [target {wanted_qnh:.2f}]")
        update_all_weather_zones_qnh(new_qnh_hpa)
        CURRENT_VIRTUAL_PRESSURE = new_qnh_hpa
    elif press_diff_hpa < -SYNC_PRESSURE_ALLOWED_DIFF:
        new_qnh_hpa = active_qnh + max(press_diff_hpa, -SYNC_PRESSURE_MAX_CHANGE_HPA)
        print(f"Adjusting current QNH down from {active_qnh:.2f} to {new_qnh_hpa:.2f} [target {wanted_qnh:.2f}]")
        update_all_weather_zones_qnh(new_qnh_hpa)
        CURRENT_VIRTUAL_PRESSURE = new_qnh_hpa
    else:
        print(f"update_sync_qnh: no pressure update needed (diff={press_diff_hpa})")
      
    return True

def update_sync_qnh_thread():
    """Every N seconds, update the QNH value we should set if sync is on."""
    last_weather_update = datetime.datetime.now()
    while True:
        time_since_weather_update = datetime.datetime.now() - last_weather_update
        if time_since_weather_update > datetime.timedelta(seconds=INTERVAL_UPDATE_METAR):
            print("Time to update the weather!")
            weather_update=True
            last_weather_update = datetime.datetime.now()
        else:
            weather_update=False
        res = update_sync_qnh(weather_update)
        if res is True:
            time.sleep(INTERVAL_UPDATE_SYNC_PRESSURE)
        else:
            print("update_sync_qnh returned False, sleeping 1s before retry")
            time.sleep(1.0) # faster retry

def print_status():
    while True:
        msfs_pa_m = aq.get("PRESSURE_ALTITUDE")
        if msfs_pa_m is None:
            msfs_pa = 0.0
        else:
            msfs_pa = 3.28084 * msfs_pa_m
        psx_alt= PSX_PFD_ALTITUDE
        psx_alt_m = PSX_PFD_ALTITUDE / 3.28084
        diff = msfs_pa - psx_alt
        print(f"===> STATUS: MSFS={msfs_pa:.0f} ({msfs_pa_m:.0f} m) PSX={psx_alt:.0f} ({psx_alt_m:.0f} m) [diff={diff:.0f}]")
        
        time.sleep(1.0)

# Create SimConnect link
print("Altitude sync starting, connecting to MSFS...")
sm = SimConnect()
print("SimConnect established connection to MSFS")
# Note the default _time is 2000 to be refreshed every 2 seconds
aq = AircraftRequests(sm, _time=2000)
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
    psx.subscribe("WxAutoSet")
    psx.subscribe("FocussedWxZone")

    psx.subscribe("WxBasic")
    for wx in [ 1, 2, 3, 4, 5, 6, 7 ]:
        psx.subscribe("Wx" + str(wx), wxchange)
        psx.subscribe("Metar" + str(wx))
    
    # Server-related action callbacks. Note that these do more than just the
    # MCDU head setup/teardown. Otherwise they could be direct mcdu.methods.
    psx.onResume     = psx_setup
    psx.onPause      = psx_teardown
    psx.onDisconnect = psx_teardown

    try:
      # Make and maintain a PSX Main Server connection until stopped.
      # Only here something actually happens!
      asyncio.run(psx.connect())
    except KeyboardInterrupt:
      print("\nStopped by keyboard interrupt (Ctrl-C)")
 