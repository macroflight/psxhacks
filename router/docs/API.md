# The PSX protocol router "frankenrouter" REST API and web pages

## Web pages

The router has a few simple web pages that you can use to control some
features of the router. This is not a replacement for the config file,
the web pages are only intended for things that you often need to
change while the router is running.

If you http://127.0.0.1:8747/ you will get a start page with links to
all available pages (replace 8747 with the API port number if you have
changed it in the config file).

### Mini web page: /filter

If you open http://127.0.0.1:8747/filter you get a small control panel
that lets see the status of the filter that prevents MSFS elevation
data from being forwarded and the filter that prevents vPilot
traffic/TCAS data from being forwarded. You can also toggle the filters
from there.

### Mini web page: /upstream

If you open http://127.0.0.1:8747/upstream you get a small control
panel that lets see the status of the upstream (master sim)
connection. You can also choose to connect to another upstream without
restarting the router.


## REST API

The router has a (currently very small) REST API. It is enabled by
default and running on port 8747. You can change the port number or
disable the API using the config file.

### GET /api/stats

Returns some basic performance statistics, including

- upstream and client queue length
- time taken to write network data to clients (max, median, mean, stdev)
- time taken to write traffic log entries (max, median, mean, stdev)
- writes to clients per second (add the history param and you get the last 60s)
- received messages per second (add the history param and you get the last 60s)

Note: the number of received messages is what we read, but since most
messages are forwarded to all connected clients the number of writes
is usually hich higher.

Example:

```text
$ curl -s "http://127.0.0.1:8747/api/stats" | jq .
{
  "upstream_queue": 0,
  "client_queue": 0,
  "write_times_ms": {
    "max": 0.1556980423629284,
    "median": 0.011681986507028341,
    "mean": 0.01822532818187028,
    "stdev": 0.014659556388030985
  },
  "log_times_ms": {
    "max": 0.1443460350856185,
    "median": 0.04162697587162256,
    "mean": 0.04298254556488246,
    "stdev": 0.00994319622434591
  },
  "writes_per_second": {
    "last": 3391
  },
  "messages_per_second": {
    "last": 340
  }
}
```

### GET /api/clients

Returns information about connected clients, including

- ip: the IP address the client connected from
- port: the source port of the client connection
- id: unique client connection ID for this router, starts at 1 for the
  first client that connects
- display_name: a name that can come from many sources, e.g a name= or
  clientName= message, the [[access]] config entry, etc. Used by the
  text interface status display. Will always be a string.
- messages_sent: how many PSX messages (lines) the router has sent to the client
- messages_received: how many PSX messages (lines) the router has received from the client
- client_provided_id: the first part of a name=ID:display_message from
  the client. Will be null if no name= message received from client.
- client_provided_display_name: the second part of a
  name=ID:display_message from the client.  Will be null if no name=
  message received from client.
- write_times_ms: statisticts on the time it took to write the messages to the client network connection

Example usage:

``` text
$ curl -s http://127.0.0.1:8747/api/clients | jq .
[
  {
    "ip": "127.0.0.1",
    "id": 3,
    "port": 42674,
    "display_name": "FilterClient1",
    "messages_sent": 5062,
    "messages_received": 2,
    "client_provided_id": "FC1",
    "client_provided_display_name": "FilterClient1"
  },
  {
    "ip": "127.0.0.1",
    "id": 5,
    "port": 42690,
    "display_name": "FilterClient2"
    "messages_sent": 123,
    "messages_received": 456
    "client_provided_id": null,
    "client_provided_display_name": null
  }
]
```

### POST /api/disconnect

Disconnect a client. The client_id parameter should be the "id"
returned by the /api/clients call or shown in the router status output.

Example usage:

``` text
$ curl -d "client_id=2" -X POST http://127.0.0.1:8747/api/disconnect
Client connection 3 closed
```

### GET /api/routerinfo

Return all data received over the FRTP ROUTERINFO protocol, e.g
connected clients, uptime, ...

Example usage:

``` text
$ curl -s http://127.0.0.1:8747/api/routerinfo | jq .
{
  "d89cebbc5def3a548b2f771c5ad79da2": {
    "timestamp": 1758090730.3745098,
    "router_name": "filtermaster1",
    "simulator_name": "FilterMaster",
    "uuid": "d89cebbc5def3a548b2f771c5ad79da2",
    "performance": {
      "uptime": 200
    },
    "filter_elevation": false,
    "connections": [
      {
        "upstream": false,
        "uuid": "45a4ec2cba7b31cdb197803cc2654555",
        "client_id": 1,
        "is_frankenrouter": true,
        "display_name": "FilterClient2",
        "connected_time": 197
      },
      {
        "upstream": true,
        "uuid": null,
        "client_id": null,
        "is_frankenrouter": false,
        "display_name": "unknown connection",
        "connected_time": 199
      }
    ],
    "received": 1758090730.3745403
  },
  "45a4ec2cba7b31cdb197803cc2654555": {
    "timestamp": 1758090713.9745913,
    "router_name": "FilterClientRouter2",
    "simulator_name": "FilterClient1",
    "uuid": "45a4ec2cba7b31cdb197803cc2654555",
    "performance": {
      "uptime": 282
    },
    "filter_elevation": true,
    "connections": [
      {
        "upstream": true,
        "uuid": "d89cebbc5def3a548b2f771c5ad79da2",
        "client_id": null,
        "is_frankenrouter": true,
        "display_name": "filtermaster1",
        "connected_time": 180
      }
    ],
    "received": 1758090713.9748962
  }
}
```

### GET /api/upstream

Return information about the current upstream connection,

Example usage:

``` text
$ curl -s http://127.0.0.1:8747/api/upstream | jq .
{
  "connected": true,
  "host": "127.0.0.1",
  "port": 20747,
  "password": "somesecrethuh"
}
```

### POST /api/upstream

Change upstream connection details (while router is running, does not
affect the config file).

Example usage (change the upstream connection to 127.0.0.1 port 20748):

``` text
curl -d "host=127.0.0.1&port=20748&password=somesecrethuh" -X POST http://127.0.0.1:8747/api/upstream
```

### GET /api/sharedinfo

Return the latest SHAREDINFO data, e.g the seat map.

Example usage:

``` text
$ curl -s http://127.0.0.1:8747/api/sharedinfo | jq .
{
  "filter-master": "LEFT",
  "filter-client1": "RIGHT"
}
```

### GET /api/filter/elevation/enable

Enable the router elevation injection filter.

### GET /api/filter/elevation/disable

Disable the router elevation injection filter.

### GET /api/filter/traffic/enable

Enable the router traffic injection filter.

### GET /api/filter/traffic/disable

Disable the router traffic injection filter.

### GET /api/blocklist

Return the current IP blocklist.

### POST /api/blocklist/add

Add an entry to the IP blocklist.

### POST /api/blocklist/remove

Remove an entry from the IP blocklist.

### POST /api/vpilotprint/message

Only used from the vPilot plugin to print messages to PSX printer. Do
not use for anything else.


<!---
https://github.com/markdownlint/markdownlint
https://github.com/markdownlint/markdownlint/blob/main/docs/RULES.md

Live preview:

retext router/docs/API.md &
press Ctrl-e
--->
