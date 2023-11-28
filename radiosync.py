"""Workaround for 134.149 MHz bug.

See https://aerowinx.com/board/index.php/topic,7238.0.html

The script will:
- Set the active MSFS COM1 frequency to the PSX VHF L active frequency
- Set the active MSFS COM2 frequency to the PSX VHF R active frequency

Note: the value we get from PSX is a string with 6 characters.

Note: the sync is unidirectional as I had trouble reliably reading a
frequency change done from vPilot uing e.g ".com1 123.000" through
SimConnect. Something cached somewhere?

Note: the script will (inentionally) not overwrite a frequency change
done from vPilot. If you have done a change through vPilot and want to
return to the PSX frequency, just press the PSX frequency swap button
twice.

The SimConnect calls COM_RADIO_SET_HZ and COM2_RADIO_SET_HZ requires a
frequency in Hz given as an integer.

To avoid rounding errors I simply pad the PSX string (e.g "121500")
with "000" and convert to a Python int.

See https://aerowinx.com/assets/networkers/Network%20Documentation.txt
for a description of MemRcpL

See
https://docs.flightsimulator.com/html/Programming_Tools/Event_IDs/Aircraft_Radio_Navigation_Events.htm
for a description of COM_RADIO_SET_HZ and COM2_RADIO_SET_HZ

Requirements:
-------------

- Python

- The Python SimConnect module (e.g "pip install SimConnect")

- Patching the file EventList.py in the SimConnect module (it lacks
  COM_RADIO_SET_HZ and COM2_RADIO_SET_HZ, just copy and edit the
  COM_RADIO_SET and COM2_RADIO_SET lines)

- Update SimConenct.dll in the SimConnect module to a more recent one
  (I installed the latest MSFS SDK and grabbed it from there).

- Hoppie's psx.py (https://www.hoppie.nl/psxpython/psx.py, docs on
  https://www.hoppie.nl/psxpython/). Place psx.py in the same
  directory as this script.

"""

import asyncio
import SimConnect  # pylint: disable=import-error
from psx import Client  # pylint: disable=import-error

# Keep track of the last seen VHF L and VHF R active frequencies
global PSX_RCP_VHF_L_ACTIVE  # pylint: disable=global-at-module-level
global PSX_RCP_VHF_R_ACTIVE  # pylint: disable=global-at-module-level
PSX_RCP_VHF_L_ACTIVE = 0.0
PSX_RCP_VHF_R_ACTIVE = 0.0

# Create SimConnect link to talk to MSFS
sm = SimConnect.SimConnect()  # pylint: disable=undefined-variable
print("SimConnect established connection to MSFS")
aq = SimConnect.AircraftRequests(sm)
ae = SimConnect.AircraftEvents(sm)


def psx_setup():
    """Set up the PSX connection."""
    print("Setting up PSX connection")
    psx.send("demand", "MemRcpL")


def psx_teardown():
    """PSC teardown."""
    print("PSX connection closed")


def psx_rcp_change(_, value):
    """When PSXVHF L or R active frequency changes, update MSFS."""
    global PSX_RCP_VHF_L_ACTIVE  # pylint: disable=global-statement
    global PSX_RCP_VHF_R_ACTIVE  # pylint: disable=global-statement
    fields = value.split(';')
    vhf_l_active = fields[0]
    vhf_r_active = fields[4]
    if vhf_l_active != PSX_RCP_VHF_L_ACTIVE:
        print(f"PSX VHF L active frequency changed, updating MSFS COM 1 to {vhf_l_active}")
        PSX_RCP_VHF_L_ACTIVE = vhf_l_active
        set_msfs_active_frequency(vhf_l_active, "COM")
    if vhf_r_active != PSX_RCP_VHF_R_ACTIVE:
        print(f"PSX VHF R active frequency changed, updating MSFS COM 2 to {vhf_r_active}")
        PSX_RCP_VHF_R_ACTIVE = vhf_r_active
        set_msfs_active_frequency(vhf_r_active, "COM2")


def set_msfs_active_frequency(frequency="121500", radio="COM"):
    """Set the active MSFS COMx frequency using SimConnect."""
    frequency_hz_int = int(frequency + "000")
    setter_str = radio + '_RADIO_SET_HZ'
    setter = ae.find(setter_str)
    if setter is None:
        print(f"ERROR: SimConnect did not find {setter_str}")
    else:
        setter(frequency_hz_int)


with Client() as psx:
    psx.logger = lambda msg: print(f"   {msg}")
    psx.subscribe("id")
    psx.subscribe("version", lambda key, value:
                  print(f"Connected to PSX {value} as client #{psx.get('id')}"))

    print("Subscribing to MemRcpL")
    psx.subscribe("MemRcpL", psx_rcp_change)

    psx.onResume = psx_setup
    psx.onPause = psx_teardown
    psx.onDisconnect = psx_teardown

    try:
        asyncio.run(psx.connect())
    except KeyboardInterrupt:
        print("\nStopped by keyboard interrupt (Ctrl-C)")
