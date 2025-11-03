# The PSX protocol router "frankenrouter" REST API

## REST API

The router has a (currently very small) REST API. It is enabled by
default and running on port 8747. You can change the port number or
disable the API using the config file.

### GET /clients

Returns information about connected clients (very basic)

Example usage:

``` text
$ curl -s http://127.0.0.1:8747/clients | jq .
[
  {
    "ip": "127.0.0.1",
    "id": 3,
    "port": 42674,
    "display_name": "FilterClient1"
  },
  {
    "ip": "127.0.0.1",
    "id": 5,
    "port": 42690,
    "display_name": "FilterClient2"
  }
]
```

### POST /disconnect

Disconnect a client. The client_id parameter should be the "id"
returned by the /clients call or shown in the router status output.

Example usage:

``` text
$ curl -d "client_id=2" -X POST http://127.0.0.1:8747/disconnect
Client connection 3 closed
```

### GET /routerinfo

Return all data received over the FRTP ROUTERINFO protocol, e.g
connected clients, uptime, ...

Example usage:

``` text
$ curl -s http://127.0.0.1:8747/routerinfo | jq .
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

### GET /upstream

Return information about the current upstream connection,

Example usage:

``` text
$ curl -s http://127.0.0.1:8747/upstream | jq .
{
  "connected": true,
  "host": "127.0.0.1",
  "port": 20747,
  "password": "somesecrethuh"
}
```

### POST /upstream/set

Change upstream connection details (while router is running, does not
affect the config file).

Example usage (change the upstream connection to 127.0.0.1 port 20748):

``` text
curl -d "host=127.0.0.1&port=20748&password=somesecrethuh" -X POST http://127.0.0.1:8747/upstream
```

### GET /sharedinfo

Return the latest SHAREDINFO data, e.g the seat map.

Example usage:

``` text
$ curl -s http://127.0.0.1:8747/sharedinfo | jq .
{
  "filter-master": "LEFT",
  "filter-client1": "RIGHT"
}
```

### Mini web page: /filter/elevation

If you open http://127.0.0.1:8747/filter/elevation you get a small
control panel that lets see the status of the filter that prevents
MSFS elevation data from being forwarded. You can also toggle the
filter from here.

### Mini web page: /filter/traffic

If you open http://127.0.0.1:8747/filter/traffic you get a small
control panel that lets see the status of the filter that prevents
vPilot traffic/TCAS data from being forwarded. You can also toggle the
filter from here.

<!---
https://github.com/markdownlint/markdownlint
https://github.com/markdownlint/markdownlint/blob/main/docs/RULES.md

Live preview:

retext router/docs/API.md &
press Ctrl-e
--->
