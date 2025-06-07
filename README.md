# psxhacks
Various small utilities for the PSX ecosystem

## Included scripts

### frankenusb.py

Replacement for the PSX USB subsystem (for my needs at least). Things
it can do that native PSX cannot:

- Handle the reverse levers on e.g the Thrustmaster Boeing Quadrant,
  i.e you pull the lever to engage reverse idle and then push the
  throttle forward for more reverse.

- Handle the gear lever on the Thrustmaster Boeing Yoke (which looks
  like two buttons UP+DOWN).

- Handle the gear lever on the STECS Standard, which has three
  positions but two buttons (UP, no_button, DOWN).

- Control PSX towing (change target heading, start, stop, toggle
  direction)

- A button to switch between using an axis as tiller and aileron (i.e
  not the automated switch that PSX offers)

- Custom speedbrake axis - since we never really use the range between
  max flight speedbrake and max ground speedbrake, we let most of the
  axis range handle the in-flight band giving better sensitivity.

- Bind a button to certain common procedures that are done during busy
  phases of flight, e.g raise flaps, start APU, stow spoilers,
  transponder to XPDR, lights off, etc. after vacating the runway.

You can find sample config files for this in the config_examples
directory.

### frankenusb.py

A flexible replacement for the PSX USB subsystem. Works with any
hardware that pygame supports (the only thing we found so far that
does not work is one type of rudder pedals). Development is currently
done on VKB Gladiator joystick, VKB STECS throttle, MFG pedals and
Thrustmaster Boeing yoke. Also tested with Bravo throttle,
Thrustmaster Airbuse joystick, etc.

### frankenfreeze.py

This will create cloud in PSX's weather model when MSFS is in
cloud. This helps make PSX icing match MSFS conditions better, i.e you
will see icing start when you enter a cloud in MSFS (if the
temperature is right).

### frankenwind.py

This will replace the PSX wind corridor data with the current MSFS
wind at your altitude when the altimeter is set to STD, and restore
the PSX data when the altimeter is set to QNH. The reasoning behind
this is that the PSX wind data will match MSFS anyway when near an
airport (i.e low, so QNH set).

Use this if you feel that your winds are very different than other
VATSIM users when enroute. Personally, I'm not convinced that this is
needed, my enroute winds (from Simbrief) seems to match MSFS quite
well.

### show_psx.py

Will display and monitor for changes one PSX variable. Mostly useful
when developing PSX Python scripts.

E.g "show_psx.py Tla" will show the thrust lever angles

### show_usb.py

Shows events on all connected USB joystick-type devices. Useful if you
e.g want to find the USB button or axis number that matches a physical
button or axis, or the pygame name of a device.

If your USB device is not detected by show_usb.py, frankenusb.py will
not be able to use it (since both use pygame to access USB devices).

### psx_shutdown.py

Connect to PSX and send the magic message that cause all instances to
shutdown cleanly.

### psx_fuel_transfer.py

Small hack to transfer X tons of ZFW into the centre tank, simulating
a large fuel tank in the hold that can be emptied into the center
tank. Who said you can't fly London to Sydney with a decent payload in
a 744? :)

### comparator.py

Mostly an example script. Compares the PSX and MSFS pitch, bank,
heading and groundspeed. Useful to detect if the PSX.NET.MSFS.WASM
plane is not doing what PSX wants it to do. Also checks that the MSFS
camera angles are zero/zero (I had an issue where they would drift
slightly, and that really screwed up my landings...)

### radiosync.py

Not needed anymore (bug in PSX.NET.MSFS.WASM now fixed), but maybe
useful as a simple example of how to inject PSX data into MSFS using
SimConnect.

## make_gatefinder_database.py
Extracts gate positions from a LittleNavMap database, for use with the
Gatefinder tool.

## What you need to run my Python scripts:

- Python 3.10 or later (might work with earlier versions but not
  tested) from https://www.python.org/
- The Python-SimConnect Python module (https://pypi.org/project/SimConnect/)
- Hoppie's psx.py module from https://www.hoppie.nl/psx/python/

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
- Download https://www.hoppie.nl/psx/python/psx.py into that directory

## Running one of my scripts

- Open a PowerShell window
- Change to the directory where you keep the scripts (for me this is c:\fs\psx\python)
- Run the script using python.exe from the virtual environment.

E.g

```
cd c:\fs\psx\python
c:\fs\python\psxpython\Scripts\python.exe comparator.py --debug
```

## Binary packages

If you're not comfortable with installing Python, a virtual
environment, etc. you can download some of the above scripts as EXE
files. These are packaged using
[Pyinstaller](https://pyinstaller.org/).

Pros: Easier to install

Cons: You cannot edit the Python script inside if you want to change
something. You also cannot verify what the binary does, so you have to
trust the developer...

You can find the binary packages on
https://drive.google.com/drive/folders/1Eu1uJCNUiLkFg9Qq8YwPCiPd9V7D5FbA

All the binaries can be run by double-clicking on them or starting
them in a PowerShell or CMD window.

show_psx.exe must be run in a window since you need to tell it which
PSX variable to monitor, e.g "show_psx.exe Tla".

In order to use frankenusb.exe you probably need to edit
frankenusb-frankensim.conf first.

## Short frankenusb tutorial

- Download the binary package zipfile and unpack it somewhere
- Double-click frankenusb.exe (you will probably see a Windows warning
  about untrusted apps)
- If you see "Connected to PSX, setting up" things are working, but
  you won't be able to do much unless you have the exact same USB
  devices as I do, so stop the script (Control-C in the window or just
  close the window)
- Double-click show_usb.exe
- Press an USB device button you want to use (or move an axis), note
  the name and button/axis number
- Edit the config file to match your devices
- Start frankenusb.exe again and try the change
- Repeat until you have mapped all the buttons and axes you want to
  use

## Help

Ask macroflight in the Aerowinx forum on Discord (username mk3830).
