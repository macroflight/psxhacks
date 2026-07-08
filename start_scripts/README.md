# The scripts in start_scripts
A collection of mostly PowerShell scripts to start and stop a PSX simulator. It assumes you use a frankenrouter as a permanent part of your sim (which then either connects to a PSX main server in your sim or a shared cockpit master sim, probably over the internet).

The scripts can be used to greatly simplify startup of your sim and provide a lot of granularity and modularity. The general idea of this directory is that any customizations are done outside of the **psxhacks** Git repository, so you can keep updating your local installation as updates are published in the Github repo while preserving your local overrides. This will be described further down below. 

## Quick start
* Have a single copy of the **psxhacks** repo that you update using e.g GitHub Desktop installed as e.g `C:\fs\psxhacks`.
* Install Python and a virtual environment with all the modules you need (**start_scripts** will only work with the Python versions of **psxhacks**, not the EXE files). Run `start_scripts\setup_python_venv.ps1` to do this; it will print the path to `python.exe` you need for the next step.
* Copy `start_scripts\psxhacks-start-override-EXAMPLE.ps1` to `C:\fs\psxhacks-start-override.ps1` (one directory above the Git repo checkout) and edit it. This file is REQUIRED - `common.ps1` will refuse to run without it, and will tell you where to put it if it's missing.
* Open `start_scripts\common.ps1` in the repository (but don't edit it) to see the full set of settings and their defaults; anything you need to change goes in your override file instead, so your changes survive future `git pull`s.
* Run `start_scripts\configure_flavor.ps1` once to set a few sim-specific values (airline, Hoppie logon code(s), etc.) that get saved to `psxhacks-current-flavor.ps1`, alongside the override file.
* Start the sim using `startsim_slave.ps1`. This assumes that you already have a master sim or a PSX main server ready to connect to. The script will prompt you to do certain things, e.g. switch to the correct upstream after the router has been started.

To better understand the setup of the scripts, it helps to consider the topologies outlined below. This wil also explain the master router concept.

## Topologies
This section describes the different topologies when using Frankenrouters. The first one applies to single pilot operations, the next two apply to shared cockpit operations. They differ based on where the master PSX instance is located, and who connects to it remotely.

### The 'solo' setup
This setup splits the PSX instances between two routers. The use of a master Frankenrouter is not mandatory, but it allows for access control if you want to host a master sim for shared cockpit operations.

```
+----------------------+
| Master PSX instance  |
+----------------------+
           ^
+----------------------+
| Master Frankenrouter |
+----------------------+
           ^
+----------------------+
| Slave Frankenrouter  | 
+----------------------+
           ^
+----------------------+
| Slave PSX instance   |
+-+--------------------+-+
  | Slave PSX instance   |
  +-+--------------------+-+
    | Slave PSX instance   |
    +----------------------+
```

All of these components can run on the same PC, but they don't need to. If you *do* run them on the same PC you need to be aware that the top three components in the diagram open up listening ports on the network. As such, they cannot use the same port numbers when they run on the same PC as a port can only be used by one process. Consider using the following port numbers a best practise:

| Component | Port | Where to configure |
|---|---|---|
| Master PSX instance | 20747 | PSX preference file with `Port10747=20747` |
| Master Frankenrouter | 10748 | Frankenrouter config file (e.g. `C:\fs\frankenrouter\frankensim-master.toml`)|
| Slave Frankenrouter | 10747 | Frankenrouter config file (e.g. `C:\fs\frankenrouter\frankensim-slave.toml`) |

### Connecting to another master sim
Because of the distributed setup, it's rather easy to connect to another master sim using the webinterface of your slave Frankenrouter. If you always want to be able to choose a known other master sim, this requires configuration of the `[[upstream]]` section in your slave router configuration file. Alternatively, you can add another master sim ad hoc in the webinterface of your slave Frankenrouter.

The topology would then look like this:

```
+----------------------+    +----------+    +----------------------------+
| Slave Frankenrouter  |  > | internet |  > | Other master Frankenrouter |
+----------------------+    +----------+    +----------------------------+
           ^
+----------------------+
| Slave PSX instance   |
+-+--------------------+-+
  | Slave PSX instance   |
  +-+--------------------+-+
    | Slave PSX instance   |
    +----------------------+
```

In this situation, you would not be using your own master PSX instance (and router, if applicable). Instead, you would rely on the the remote master PSX instance allowing for shared cockpit operations and flying together over the internet!

Note that the party hosting the master sim would need to configure port forwarding in their internet router, so that you are able to connect. The forwarded port should point to the system running the master router.

### Hosting the master sim
As mentioned, when you're using a master Frankenrouter others can connect to it over the internet for shared cockpit operations. This does require port forwarding on your internet router to the IP address of your master Frankenrouter though.

```
+----------------------+
| Master PSX instance  |
+----------------------+
           ^
+----------------------+    +----------+    +------------------------------+
| Master Frankenrouter |  < | internet |  < | Other slave Frankenrouter(s) |
+----------------------+    +----------+    +------------------------------+
           ^
+----------------------+
| Slave Frankenrouter  | 
+----------------------+
           ^
+----------------------+
| Slave PSX instance   |
+-+--------------------+-+
  | Slave PSX instance   |
  +-+--------------------+-+
    | Slave PSX instance   |
    +----------------------+
```

The master Frankenrouter should also contain an `[[access]]` configuration for each pilot that you want to be able to connect. The use of passwords is highly encouraged, as any random person (or bot) on the internet can connect to your open port.

Note that in this case, you would need to configure port forwarding in your internet router, so that others are able to connect to your master router. Again, the forwarded port should point to the system running the master router.

### Flying solo vs shared cockpit
When flying solo, you don't need to worry about which programs you're running and connecting to the PSX network. BACARS, for instance, will connect to PSX and present a user interface on the center CDU. Through the CDU you can retrieve ATIS, pull a flightplan from the Simfest Planning Portal or interact with the CARD server.

When you're doing shared cockpit operations, you don't want to run two BACARS instances each. Firstly, the two instances will fight eachother for access in the center CDU and the outcome will be unstable and unpredictable. Second, all of the CDU operations are synchronized throughout the PSX network. As such, only one instance is needed and it is recommended that you connect it to your master router or master PSX instance. This way you won't be injecting your BACARS data into the network if you selected another upstream.
Another example is PSX.NET, which is used to inject TCAS objects into PSX. It only needs to be running once in the shared cockpit as well.

There are also programs that you would want to run in every instance/location in the shared cockpit. These include but are not limited to:
* PSX.NET.MSFS.Client
* PSX.NET.MSFS.Router
* AcarsPrint
* FrankenUSB
* vPilot

## The scripts

### common.ps1
This file holds most of the settings used by the other scripts and their default values, plus the startup checks that make sure your override file is set up correctly. You should open this file and examine its contents, but you should NOT edit it directly - every option that differs from the defaults shown goes in your override file instead (see next). `common.ps1` dot-sources `functions.ps1` (shared helper functions used throughout `start_scripts`) and, if present, `psxhacks-current-flavor.ps1` (see `configure_flavor.ps1` below).

### psxhacks-start-override-EXAMPLE.ps1 / psxhacks-start-override.ps1
Any overrides of settings in `common.ps1` go in your own override file. It does not exist by default - copy `start_scripts\psxhacks-start-override-EXAMPLE.ps1` to `psxhacks-start-override.ps1` one directory above the **psxhacks** Git repo checkout (e.g. `C:\fs\psxhacks-start-override.ps1`) and edit your copy. `common.ps1` will refuse to start (with an error explaining what to do) if this file is missing. With this setup, new settings and functions in the repository can be pulled from Github and your overrides will be preserved.

### configure_flavor.ps1
Run this once (and again whenever you want to change these values) to interactively set a handful of sim-specific values - airline code, Hoppie logon code(s), Simfest email(s), vPilot plugin, etc. - that are saved to `psxhacks-current-flavor.ps1`, next to your override file. Some settings (like `$HoppieLogonCodes`/`$SimfestEmails`, both hashtables of named entries) are picked from by name here rather than being set directly in the override file.

### setup_python_venv.ps1
Run this once on a new machine to install Python and create the virtual environment that all the `franken*.py` addons and `frankenrouter.py` run in. It prints the path to use for `$PsxhacksPython` in your override file. If you already have Python 3.13 installed some other way (not installed by this script), you can point the script at it instead of downloading a fresh copy - it will still create the virtual environment for you.

The next two scripts are `startsim_master.ps1` and `startsim_slave.ps1`.  As their names suggest, these scripts have different scopes and start different programs and scripts.

### startsim_master.ps1
Is used to start a main PSX instance (the 'server' instance if you will) and programs or scripts that need to be running only if you are flying by yourself or hosting the master sim. 

Typically, it would start the following:
* a master PSX instance
* a master Frankenrouter
* any additional programs you only want running alongside a master sim (like BACARS for instance)

### startsim_slave.ps1
This script is used to start all client PSX instances and other programs, independent of the upstream connection. The programs started by it should be the ones that do not conflict when performing shared cockpit setup operations (e.g. BACARS should be running only once in the shared cockput and is therefor only started by `startsim_master.ps1` if it is enabled in the override file).

Typically, the following is started:
* a slave Frankenrouter
* one or more client PSX instance(s) with specific PSX preferences
* other software that is needed locally or can run multiple times in a shared cockpit, for example (but not limited to):
	* [vPilot](https://vpilot.rosscarlson.dev/)
	* [PSX.NET.MSFS.Router](https://aerowinx.com/board/index.php?topic=7595.0)
	* [PSX.NET.MSFS.Client](https://aerowinx.com/board/index.php?topic=7595.0)
	* [AcarsPrint](https://aerowinx.com/board/index.php?topic=6272.0)
	* drivers needed for hardware
	* etc.

Both of the `startsim_*` script use the variables in `common.ps1`, but again: you should not edit this file directly and use the override script.

### stopsim_master.ps1
Used to stop a main PSX instance and any programs or scripts that need are running alongside the master PSX instance. This will also stop the master router if you're using one. If you're only flying by yourself, you should first run `stopsim_slave.ps1` to stop all the clients first. Both `stopsim_*` scripts ask for confirmation before stopping anything.

### stopsim_slave.ps1
Used to stop all client PSX instances and any programs or scripts that are running alongside them. It will also stop clients that are running on another PC connected to the same slave router, e.g. if you have multiple PCs driving different monitors through distributed PSX instances, then those instances on other PCs are stopped as well. The stop commands are not propagated to other routers, so you won't be stopping PSX clients in other cockpits.

## Help
### Unable to execute ps1/powershell scripts
The Default Execution Policy is set to restricted on Windows 11 and you might need to change it. The current settings on your system can be displayed with the following command in a Powershell window:

```
Get-ExecutionPolicy -List
```

The result will look similar to the following:

```
        Scope ExecutionPolicy
        ----- ---------------
MachinePolicy       Undefined
   UserPolicy       Undefined
      Process       Undefined
  CurrentUser       Undefined
 LocalMachine       Undefined
```

To change the ExecutionPolicy, use the following command:

```
Set-ExecutionPolicy -ExecutionPolicy Unrestricted
```

When you check the settings again, they should now look like this:

```
        Scope ExecutionPolicy
        ----- ---------------
MachinePolicy       Undefined
   UserPolicy       Undefined
      Process       Undefined
  CurrentUser       Undefined
 LocalMachine    Unrestricted
```
