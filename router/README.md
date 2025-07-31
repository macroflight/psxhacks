# The PSX protocol router "frankenrouter"

This is a router/broker for the Aerowinx PSX network protocol.

[Changelog](docs/Changelog.md)

[Some notes on the PSX network protocol and the router design](docs/NOTES.md)

## Maturity

This is still somewhat of a prototype. The code is being gradually
cleaned up.

The router is stable enough for long flights (16+ hours).

## Why use a PSX router

- It makes it easier to switch your PSX sim from normal mode to shared
  cockpit mode.
- It shows you which PSX main clients and addons are connected so you
  can see if some part of the sim is not started or has crashed. It
  can also optionally warn if some expected client is not connected.
- It provides logging of the PSX network traffic if you need to track
  down any problems, e.g in new addons.
- It holds the client connection alive if the PSX main server (or a
  remote sim) is restarted. Since many addons will not reconnect
  automatically, this can be very useful.
- Addons or whole sims can be connected in read-only mode (can read
  PSX traffic and request DEMAND mode variables).

## Installing

For now we distribute the router as Python scripts. If there is enough
interest we might provide a standalone binary or installer later.

- Clone the [psxhacks Git repository](https://github.com/macroflight/psxhacks)
    - Alternative if you don't have a Git client installed: download a
      ZIP of the repository usign the `Code` button's Download Zip
      option.
- Download the Variables.txt file from [the PSX
  Forum](https://aerowinx.com/assets/networkers/Variables.txt)
- Install Python (see below)

### Python

I recommend using Python 3.13 (or later) as that is what I use for
development.

For Windows you probably want the [Windows installer
(64-bit)](https://www.python.org/ftp/python/3.13.5/python-3.13.5-amd64.exe)

One extra Python module is needed for the router:

- aiohttp (for the REST API)

You can either install these modules in your main Python installation,
or use a [Python virtual
environment](https://docs.python.org/3/library/venv.html).

Example:

``` text
python3 -m venv router1
. router1/bin/activate
pip install aiohttp
```

### Linux and macOS support

The router itself should work fine on Linux (I use it for a lot of the
router development) and macOS.

The script that identifies PSX clients by their window title only
works on Windows for now.

## Configuration

[Configuration file format](docs/Configuration.md)

Edit frankenrouter.toml (or copy it and use the `--config-file` option
to start the router using that config file).

There are some config examples in the config_examples directory.

Some aspects of frankenrouter's configuration can be overridded by
command line options, e.g `--log-traffic` will enable logging of
traffic. Run `frankenrouter.py --help` to see the available command
line options.

## Starting the router

- Open a terminal window
- If using a virtual environment, activate it or use the full path to
  "python" inside the virtual environment to start the router.

Example:

``` text
. router1/bin/activate
python frankenrouter.py --variables-file=C:\PSX\Variables.txt --log-data

```

Example of the router output when running:

``` text
08:03:32: --------------------------------------------------------------------------------------------------------------
08:03:32: Frankenrouter SlaveSim port 10748, 2460 keywords cached, uptime 2990 s, server connects 1, self restarts 0
08:03:32: Ctrl-C to shut down cleanly. Password: None Read-only password: None
08:03:32: Logging traffic to mastersim/frankenrouter-SlaveSim-traffic-1751260422.psxnet.log
08:03:32: SERVER 127.0.0.1:20748 R:MasterSim, RTT mean/max: 0.6/6.2 ms, output delay avg/max 0.0/0.1 ms
08:03:32: 2 clients                             Local                   Lines  Lines  Bytes  Bytes FRDP ms   Delay us
08:03:32: id Identifier         Client IP        Port   Access Clients   sent  recvd   sent  recvd mean  max mean  max
08:03:32:  2 LocalHost          127.0.0.1       44622     full       0  34882    270 3132090  23460    -    -  0.0  0.1
08:03:32:  4 L:SomAddon         127.0.0.1       57366     full       0   2599      1  63509     14    -    -  0.0  0.0
08:03:32: --------------------------------------------------------------------------------------------------------------
08:03:32: pitch=0.3 bank=0.0 heading=14 altitude_true=30934 TAS=501 lat=18.813094 lon=-62.915702
```

The router above ("SlaveSim") is connected to an upstream
frankenrouter "MasterSim" (in a remote shared cockpit "master sim").

The network round trip delay to the upstream router and back is 0.6 ms
(with a maximum of 6.2ms).

Two clients are connected to this router. One unidentified addon, and
one that was identified as "SomAddon" by having sent "name=SomAddon"
on the PSX network.

Both clients have full access (as opposed to read-only access)

The last line shows some basic information about the PSX simulation
that we get from the router's variable cache.

## REST API

The router has a (currently very small) REST API. You can enable it
with `--api-port=<some port number>`.

- GET /clients
    - returns information about connected clients (very basic)
- POST /upstream/set (provide host= and ip=)
    - Force the router to connect to a different upstream PSX main
      server or router.

Example usage (change the upstream connection to 127.0.0.1 port 20747):

``` text
curl -d "host=127.0.0.1&port=20747" -X POST http://127.0.0.1:8080/upstream/set
```

Getting information from the router:

``` text
$ curl -s http://127.0.0.1:8080/clients | jq .
[
  {
    "ip": "127.0.0.1",
    "port": 49680,
    "identifier": "R:SlaveSim"
  }
]
```

## The addon that identifies PSX clients by their window name "frankenrouter_ident"

The router will always identify addons that send the special `name=`
keyword to the network. Unfortunately PSX main clients do not do this,
and many addons don't. So you end up with a router status display that
only shows IP addresses and ports, and that makes it harder to know
which addons are actually connected.

To improve the situation, I wrote a small addon that tries to identify
PSX main clients and addons by their window title and send that
information to the router. PSX main clients use the layout name as
part of their window title, and that allows us to identify all PSX
instances by simply giving the layouts descriptive names. This addon
can also identify those addons that have a window with a useful title.

This addon needs the `pywin32` and `psutil` modules installed, e.g
using a virtual environment:

``` text
text
python3 -m venv router1
. router1/bin/activate
pip install pywin32
pip install psutil
python3 frankenrouter_ident.py
```

To start this addon, start a new terminal window and run `python
frankenrouter_ident.py` in the router directory.

## Planned changes/new features

- A way to reconfigure the router without restarting it (e.g to switch
  the connection from a local PSX main server to a remote shared
  cockpit master)
- A more feature-rich REST API

## If you need help  or have suggestions for new features

Contact macroflight on Discord (username mk3830) or post on the
Aerowinx forum.
