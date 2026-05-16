# The scripts in start_scripts
A collection of mostly PowerShell scripts to start and stop a PSX simulator. It assumes you use a frankenrouter as a permanent part of your sim (which then either connects to a PSX main server in your sim or a shared cockpit master sim).

The scripts can be used to greatly simplify startup of your sim and provide a lot of granularity and modularity. The general idea of this directory is that any customizations are done outside of the **psxhacks** Git repository, so you can keep updating your local installation as updates are published in the Github repo while preserving your local overrides. This will be described further down below. 

## Quick start
* Have a single copy of the **psxhacks** repo that you update using e.g GitHub Desktop installed as e.g `C:\fs\psxhacks`.
* Install Python and a virtual environment with all the modules you need (**start_scripts** will only work with the Python versions of **psxhacks**, not the EXE files); see the main README on how to install python or create a Python virtual environment.
* Create your own override file outside the Git repo, e.g `C:\fs\psxhacks-start-override.ps1`. If the file is named like that and on the same directory level as the Git repo, it will be found automatically.
* Check `start_scripts\common.ps1` and put any setting you need to override for your sim in the override file.
* Start the sim using `startsim_slave.ps1`. This assumes that you already have a master sim or a PSX main server ready to connect to. The script will prompt you to do certain things, e.g switch to the correct upstream after the router has been started.

To better understand the setup of the scripts, it helps to consider the topologies outlined below.

## Topologies
This section describes the different topologies when using Frankenrouters. The first one applies to single pilot operations, the next two apply to shared cockpit operations. They differ based on where the master PSX instance is located, and who connects to it remotely.

### The 'solo' setup
This setup splits the PSX instances between two routers. The use of a core Frankenrouter is not mandatory, but it allows for access control if you want to host a master sim for shared cockpit operations.

```
+----------------------+
| Master PSX instance  |
+----------------------+
           ^
+----------------------+
| Core Frankenrouter   |
+----------------------+
           ^
+----------------------+
| Client Frankenrouter | 
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

All of these components can run on the same PC, but they don't need to. If you *do* run them on the same PC you need to be aware that the top three components open up listening ports on the network. As such, they cannot use the same port numbers when they run on the same PC. Consider using the following port numbers a best practise:

| Component | Port | Where to configure |
|---|---|---|
| Master PSX instance | 20747 | PSX preference file with `Port10747=20747` |
| Core Frankenrouter | 10748 | Frankenrouter config file (.toml) |
| Client Frankenrouter | 10747 | Frankenrouter config file (.toml) |

### Connecting to another master sim
Because of the distributed setup, it's rather easy to connect to another master sim using the webinterface of your client Frankenrouter. If you always want to be able to chose a known other master sim, this requires configuration of the `[[upstream]]` section in your client router configuration file. Alternatively, you can add another master sim ad hoc in the webinterface of your client Frankenrouter.

The topology would then look like this:

```
+----------------------+    +----------+    +--------------------------+
| Client Frankenrouter |  > | internet |  > | Other core Frankenrouter |
+----------------------+    +----------+    +--------------------------+
           ^
+----------------------+
| Slave PSX instance   |
+-+--------------------+-+
  | Slave PSX instance   |
  +-+--------------------+-+
    | Slave PSX instance   |
    +----------------------+
```

In this situation, you would not be using your own master PSX instance (and router, if applicable). Instead, you would rely on the the remote master PSX instance allowing for  shared cockpit operations and flying together over the internet!

### Hosting the master sim
As mentioned, when you're using a core Frankenrouter others can connect to it over the internet for shared cockpit operations. This does require port forwarding on your internet router to the IP address of your core Frankenrouter though.

```
+----------------------+
| Master PSX instance  |
+----------------------+
           ^
+----------------------+    +----------+    +-------------------------------+
| Core Frankenrouter   |  < | internet |  < | Other client Frankenrouter(s) |
+----------------------+    +----------+    +-------------------------------+
           ^
+----------------------+
| Client Frankenrouter | 
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

The core Frankenrouter should also contain an `[[access]]` configuration for each pilot that you want to be able to connect. The use of passwords is highly encouraged, as any random person (or bot) on the internet can connect to your open port.

## The scripts

### common.ps1
This file holds most of the settings used by the other scripts and their defaults values. They're used if you don't override them. Don't edit this file directly! Instead, see `psxhacks-start-override.ps1`.

### psxhacks-start-override.ps1
Any overrides of settings in `common.ps1` go in this file. It doesn't exist by default, you have to create it yourself outside of the **psxhacks** Git repo, in the parent directory. This way, new settings and functions can be pulled from Github and your overrides are preserved.

The next two scripts are `startsim_master.ps1` and `startsim_slave.ps1`.  As their names suggest, these scripts have different scopes and start different programs and scripts.

### startsim_master.ps1
Is used to start a main PSX instance (the 'server' instance if you will) and programs or scripts that need to be running only if you are flying by yourself or hosting the master sim. 

Typically, it would start the following:
* a master PSX instance
* a core Frankenrouter
* any additional programs you only want running alongside a master sim (like BACARS for instance)

### startsim_slave.ps1
This script is used to start all client PSX instances and other programs, independent of the upstream connection. The programs started by it should be the ones that do not conflict when performing shared cockpit setup operations (e.g. BACARS should be running only once in the shared cockput and is therefor only started by `startsim_master.ps1` if it is enabled in the override file).

Typically, the following is started:
* a client Frankenrouter
* one or more client PSX instance(s)
* other software that is needed locally or can run multiple times in a shared cockpit, for example (but not limited to):
	* [vPilot](https://vpilot.rosscarlson.dev/)
	* [PSX.NET.MSFS.Router](https://aerowinx.com/board/index.php?topic=7595.0)
	* [PSX.NET.MSFS.Client](https://aerowinx.com/board/index.php?topic=7595.0)
	* [AcarsPrint](https://aerowinx.com/board/index.php?topic=6272.0)
	* drivers needed for hardware
	* etc.

Both these scripts use the variables in `common.ps1`, but you should not edit this script directly. Instead, the variables should be overridden using a custom script as explained further below.

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
