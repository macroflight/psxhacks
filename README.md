# psxhacks
Various small utilities for the PSX ecosystem

## Included scripts

### frankenusb.py

Replacement for the PSX USB subsystem (for my needs at least). Things
it can do that native PSX cannot:

- Handle the reverse levers on the Thrustmaster Boeing Quadrant
- Handle the gear lever on the Thrustmaster Boeing Yoke

### comparator.py

Compares the PSX and MSFS pitch, bank, heading and groundspeed. Useful
to detect if the PSX.NET.MSFS.WASM plane is not doing what PSX wants
it to do. Also checks that the MSFS camera angles are zero/zero (I had
an issue where they would drift slightly, and that really screwed up
my landings...)

### radiosync.py

Not needed anymore (bug in PSX.NET.MSFS.WASM now fixed), but maybe
useful as a simple example of how to inject PSX data into MSFS using
SimConnect.

### show_psx.py

Will display and monitor for changes one PSX variable. Mostly useful
when developing PSX Python scripts.

### show_usb.py

Shows events on all connected USB joystick-type devices. Useful if you
e.g want to find the USB button or axis number that matches a physical
button or axis, or the pygame name of a device.

## What you need to run my Python scripts:

- Python 3.10 or later (might work with earlier versions but not
  tested) from https://www.python.org/
- The Python-SimConnect Python module (https://pypi.org/project/SimConnect/)
- Hoppie's psx.py module from https://www.hoppie.nl/psxpython/

## Installing the needed things, my way

### Install Python

- Download the Python installer from python.org. The latest 3.x should
  be OK, no need to use exactly 3.10.
- Run the installer, install into C:\fs\python\310. No need to use this exact path.

### Create a Python virtual environment

Not strictly needed, you could install Python-SimConnect in the main
Python installation instead, but I prefer to use one virtual
environment per project so I can install just the packages I need.

```
c:\fs\python\310\python.exe -m venv c:\fs\python\psxpython
c:\fs\python\psxpython\Scripts\python.exe -m pip install --upgrade pip
```

### Install Python packages into the virtual environment

```
c:\fs\python\psxpython\Scripts\pip install SimConnect
c:\fs\python\psxpython\Scripts\pip install pygame
```

### Update SimConnect.dll in Python-SimConnect (probably not needed)

For at least one of my scripts (radiosync.py) I had to patch
Python-SimConnect as it was missing some SimConnect variables (e.g
COM_RADIO_SET_HZ).

This is often quite easy, just edit a Python file inside
Python-SimConnect (see radiosync.py for an example). But in the
radiosync.py case it turned out that the SimConnect.dll file shipped
with Python-SimConnect was too old and lacked e.g "COM RADIO SET HZ".

To solve this, I simply copied the latest SimConnect.dll file from the
MSFS SDK ("C:\MSFS SDK\SimConnect SDK\lib\SimConnect.dll") to
c:\fs\python\psxpython\Lib\site-packages\SimConnect\SimConnect.dll.

### Download Hoppie's psx.py

- Create a directory where you will run the scripts from. I'm using c:\fs\psx\python.
- Download https://www.hoppie.nl/psxpython/psx.py into that directory

## Running one of my scripts

- Open a PowerShell window
- Change to the directory where you keep the scripts (for me this is c:\fs\psx\python)
- Run the script using python.exe from the virtual environment.

E.g

```
cd c:\fs\psx\python
c:\fs\python\psxpython\Scripts\python.exe comparator.py --debug
```
