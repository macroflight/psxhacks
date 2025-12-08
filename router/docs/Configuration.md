# Configuring the router

## Default values, configuration file and command line options

Frankenrouter uses a configuration file in [TOML](https://toml.io/)
format.

Some config file options can be overridden with command line
options. Run frankenrouter with the `--help` option to see the command
line options available.`

You can choose which config file to use on startup using the
`--config-file` command line option.

If you do not use the `--config-file` option, the default file
`frankenrouter.toml` will be read.

You can also do some config changes while the router is running using
the REST API, e.g

- Allow or disallow access from a remote sim (based on IP address or
  password)
- Disconnect and block a client (in case someone forgets their slave
  sim on).
- Force the router to change upstream connection

For complete example config files, see the
[`router/config_examples`](config_examples/) directory.

## Config file sections

All sections are optional. If you omit a section, the router will use
some safe default (e.g allow connections from 127.0.0.1, not log
traffic, etc.)

### `[identity]`

- `simulator`: a string desribing the name of the simulator the router
  is located in.
- `router`: a name describing the router. If you only have one router
  in your sim, you can use the same name as for the simulator.
- `stop_minded`: if you want the router to stop if encountering
  unhandled but not necessarily fatal problems, set this to
  true. Useful for e.g router development.

Example:

```text
# Hint: you can use comments in the comfig file
[identity]
simulator = "FrankenSim"
router = "router1"
stop_minded = false
```

### `[listen]`

- `port`: the port number the router should listen on
- `rest_api_port`: if this is set, the REST API is started and listens on this port

Note: the normal port for a PSX router is 10748. If you want to use
the router as a drop-in replacement for your PSX main server for a
shared cockpit setup, you probably want to use port 10747, as your
addons will already be configured to connect to that port.

### `[upstream]`
DEPRECATED, use [[upstream]] instead

### `[[upstream]]`

This section can be listed several times in the file. Each one
describes one upstream connection. An upstream connection can either
be directly to a PSX main server, or to another frankenrouter.

- `default`: set to true for the upstream that the router should
  connect to on startup. In most cases, this will be a PSX main server
  or other frankenrouter in your own sim.
- `name`: a name that identifies this upstream connection
- `host`: the upstream hostname or IP that the router should connect
  to.
- `port`: the upstream port that the router should connect
  to.
- `password`: if set, use this password to auenthicate to the upstream
  router. Only use this if the upstream is a frankenrouter that has a
  password configured.

Example - just one upstream:

```text
[[upstream]]
default = true
name = "My local PSX main server"
host = "127.0.0.1"
port = 10747
```

Example - three upstreams

```text
[[upstream]]
default = true
name = "My local PSX main server"
host = "127.0.0.1"
port = 10747

[[upstream]]
name = "Macroflight's master sim"
host = "123.123.123.123"
port = 10748

[[upstream]]
name = "Voipmeister's master sim"
host = "145.12.14.22"
port = 10748
```

### `[log]`

- `traffic`: if set to true, the router will write all traffic data to
  a log file.
- `directory`: set to the directory where the traffic log file should
  be written (default: the current working directory of the router
  process)
- `traffic_max_size`: if set, the traffic log file will be rotated
  before reaching this size (bytes). The default is to not rotate the
  log.
- `traffic_keep_versions`: controls how many versions of the traffic
  log file will be kept after being rotated.
- `output_max_size`: as traffic_max, size, but for the router status
  output log.
- `output_keep_versions`: as traffic_keep_versjons, but for the router
  status output log.

Example:

```text
[log]
traffic = true
directory = 'C:\fs\PSX\Routerlogs'
```

### `[psx]`

- `variables`: The path to the Variables.txt file (from the Devel
  folder of your Aerowinx installation or [downloaded from the
  Forum](https://aerowinx.com/assets/networkers/Variables.txt)). If
  the file is not found, the router will print a warning and try to
  download a copy of the file from Aerowinx.
- `filter_elevation`: defaults to false. If true, Qi198 elevation
  injections from clients (usually PSX.MSFS.Router) will be
  filtered. This filter can be toggled during runtime using the REST
  API.
- `filter_traffic`: defaults to false. If true, traffic information
  (usually from the vPilot plugin) will be filtered. This filter can
  be toggled during runtime using the REST API.
- `filter_flight_controls`: defaults to true. If true, the router will
  filter certain flight control axes (e.g rudder). The filtering can
  be controlled by e.g an USB button mapped through frankenusb. For a
  shared cockpit setup, the master sim frankenrouter must have this
  set to false.

Example:

```text
[psx]
variables = 'C:\fs\PSX\Variables.txt
filter_elevation = true
```

### `[[access]]`

This section can be listed (and usually will be) listed several times
in the file.

**Important: this section should have two brackets before and after
its name, i.e `[[access]]`, NOT `[access]`.**

Each access section describes one rule that control who can connect to
the router and what access level (e.g full, read-only) they get.

Each client will be given access based on its IP address, whether it
provided a password, etc.

The rules are checked in the order they appear in the config file and
the client is given access based on the first rule that matches.

So e.g if you want to give read-only access to anyone in the IP
network 123.123.123.0/24 and full access to anyone using a password,
you should place the password rule first.

Each group will have a human-readable display name, which is
displayed in the status display.

- `display_name`: The human-readable name that will be displayed in
  the status display. This is limited to 24 characters.
- `access level`
    - If set to `blocked`, matching clients will be automatically
      disconnected.
    - If set to `full`, matching clients will have full read/write
      access to the PSX network.
    - If set to `observer`, the client will have read-only access to
      the PSX network (but can send the demand keyword).
- `match ipv4`: If is set, any client connecting from this list of
  IPv4 networks will match. Note: to allow just one IP and not a
  larger network, use the IP/32 notation. To allow any IP to connect,
  set to `[ "ANY" ]`.
- `match_password`: If set, the router requires that the client provides
  this password to be given access.

Note: if both `match_password` and `match ipv4` are set, the client must
have both an approved IP address and provide the password.

Example:

```text
[[access]]
display_name = "Any client"
match_ipv4 = [ "ANY" ]
level = "full"

[[access]]
display_name = "Any local client"
match_ipv4 = [ "127.0.0.1/32", "192.168.86.34/32" ]
level = "full"

[[access]]
display_name = "Ventus"
match_ipv4 = [ "192.168.86.2/32" ]
level = "full"

# RemoteSim can only connect from this IP address and must provide a password
[[access]]
display_name = "RemoteSim"
match_ipv4 = [ "123.123.123.123/32" ]
match_password = "some secret"
level = "full"

[[access]]
display_name = "CDUPAD"
match_ipv4 = [ "192.168.86.8/32" ]
level = "full"

```

### `[[check]]`

This is a list of checks that can be used to verify you have the
expected number of various addons connected to the sim.

This section can be listed (and usually will be) listed several times
in the file.

**Important: this section should have two brackets before and after
its name, i.e `[[check]]`, NOT `[check]`.**

- `checktype`:
    - If set to `is_frankenrouter`, the number of connected
      frankenrouter clients are counted.
    - If set to `name_regexp`, the number of clients where the display
      name (whether given by the config file or by the client sending
      name=) will be counted.
- `limit_min`: if fewer than this many matching clients found, the
  router will show a warning in the status display.
- `limit_max`: if more than this many matching clients found, the
  router will show a warning in the status display.

For the `name_regexp` check:

- `regexp`: a regular expression that should match the clients we want
  to check.

Example:

```text
[[check]]
type = "name_regexp"
regexp = '.*PSX .*'
limit_min = 5
limit_max = 5
comment = "There should be exactly 5 PSX main clients connected"

[[access]]
type = "name_regexp"
regexp = '.*BACARS.*'
limit_min = 1
limit_max = 1
comment = "There should be exactly one BACARS"
```

### `[performance]`

Various limits that control when the router prints warning messages
regarding its performance. Note: all of these variables have defaults,
unless you find that you get lots of false warnings you should not
need to change any of these settings.

- `write_buffer_warning`: if a connected client's write buffer has
  more than this much data in it a warning will be shown.

- `queue_time_warning`: if a message sits for longer than this in the
  router's internal forwarding queue, a warning will be shown.

- `total_delay_warning`: if the total forwarding delay (queue time +
  forwarding time) for a message is longer than this, a warning will
  be shown.

- `monitor_delay_warning`: the router's internal monitoring coroutine
  sleeps for a certain amount of time between runs. If this sleep
  takes longer than expected, it is a sign that the router is
  overloaded. If the sleep is extended by more than this amount of
  time, a warning will be shown.

- `frdp_rtt_warning`: warn if the FRDP RTT is longer than this many
  seconds.

Example:

```text
[performance]
# Shared cockpit with some guy on the other side of the world,
# so only warn if ping times are above 500 ms
frdp_rtt_warning = 0.5
```


<!---
https://github.com/markdownlint/markdownlint
https://github.com/markdownlint/markdownlint/blob/main/docs/RULES.md

Live preview:

retext router/docs/Configuration.md &
press Ctrl-e
--->
