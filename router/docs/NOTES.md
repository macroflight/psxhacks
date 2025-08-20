# Notes on building a PSX router

Note: some of this is implemented in the current prototype, but not
everything.

This document should be seen as the design for the next major version
of the router.

## The PSX network protocol

Sources:

- [Hoppie’s notes](https://www.hoppie.nl/psx/router/conn.html)
- [PSX network docs](https://aerowinx.com/board/index.php/topic,1570.0.html)

### Variable types

- Qh == “human” variable, integer
    - mechanical objects on the flight deck that can be operated by humans
    - send to network if you operate something
    - PSX main server sends back if something was operated
    - you do not get your own variable back
- Qi == “internal” variable, integer
    - created by the simulation, cannot be changed directly
- Qs == “string”
    - Usually read, modify, write
    - Leading and trailing whitespace matters

### Network modes

- C - continous: will be sent to the network all the time even when
  unchanged
- E - ECON: sent only when changed
- D - DELTA: sent only when changed, addons should not store as they
  are usually reset to 0. Never cache ECON? Always forward
- BIGMOM, MCPMOM: part DELTA, part ECON
- START - sent during situ loading or client connection
- A few START variables are part START, part ECON

## Challenges when writing a PSX router

- Many clients will not reconnect automatically. For easy shared
  cockpit use, we want to be able to switch to another upstream IP and
  port without restarting the router.
- Some clients do bad things when they receive unexpected variables,
  even if the value of those variables is OK. Examples:
    - PSX Sound plays a sound when it gets Qi191
    - PSX.NET.Router jumps into the air when it receives loadX (all of
      them or just one?)
- Some clients need the Lexicon, e.g psx.py
    - This is only sent by the PSX main server on initial connection or
      when `lexicon` is sent.
- Many clients need the START variables on startup to e.g synchronize
  the nose door,but also set the initial position
    - Some clients will not like this and will e.g jump to a new
      position in an ugly way?

## What the router needs to do

(As of 2025-06-30, parts of this is implemented in the prototype router.)

### General

- We need to make explicit forwarding decisions
    - Can be implemented as its own class using the existing code and
      re-used later?
- Use a cache, in memory and also on disk to handle clients that
  connect while the server connection is down.
    - OK to cache everything, we decide later when and where we will use
      the data based on the variable type and situation.
    - Proably useful to cache in a format that allows extra data, e.g
      last_updated time
- Demand mode
    - Forward `demand=` from clients
    - Keep track of which clients sent `demand=` for which variables
    - On upstream reconnect, re-send the demand= requests for all
      connected clients
    - There is no need to filter DEMAND variables as the PSX main server
      will send them to all clients as long as the client that requested
      demand= is connected.
- START keywords
    - Many clients will need the START keywords on startup. How do we
      make sure the router has updated values to send when a client
      connects?
    - Forward `start` from clients (probably another router) to
      upstream and reset the `start_sent_at timestamp`
    - Send `start` to upstream when a local client connects and reset
      the start_sent_at timestamp
    - Pure START variables (Qs493 and Qi208 excluded as they are also
      ECON) are forwarded to
        - other routers
        - clients that have not been welcomed yet

### Upstream connection lost

- Send `load1` to clients (pause)

### When upstream connection established

- No special handling needed, upstream will deliver a full PSX welcome
  message which will be forwarded normally to connected clients.
- Send `demand=` for any variables that any connected client has requested.

### When a client connects

Send a fake "PSX welcome message" to the client:

- Set `welcome_keywords_sent` to an empty set
- For each keyword sent, add it to `welcome_keywords_sent`
- Send `id=` (locally generated on the router)
- Send `version=` (from cache)
- Send `layout=` (from cache)
- Send the Lexicon (from cache, in the same order as a PSX main server)
- Send `load1`
- Set `waiting_for_start_keywords`==True for client
- Send `start` to upstream and reset the start_sent_at timestamp
- Wait for a limited time for the expected START variables to be
  delivered to the client.
- Continue after all START keywords have been sent to the client or we
  hit the timeout.
- Set `waiting_for_start_keywords`==False for client
- Send all unsent keywords from the cache
    - Use the same order as a PSX main server
    - Do not send pure DELTA type variables
- Send `load2` and `load3`
- Send `metar=`
- Set `welcome_sent` to True, empty `welcome_keywords_sent`
- Send any messages from the client's `pending_messages` list

### When a client disconnects

- Close the connection

### Key-value message from client

- Apply optional filtering (e.g to work around addon bugs)
- Special handling for
    - If `demand=`: add variable to client connection's list of
      demanded variables
- Forward to upstream unless otherwise stated in special handling
- Forward to all clients except the one sending the message

### Key-value message from upstream

- id: update in router's cache, do not forward
- version: update in router's cache, do not forward
- Apply optional filtering
- Decide on forwarding based on variable network mode and router config
    - Pure (not Qs493 and Qi208) START keyword, for each client:
        - if client is a router: forward to client
        - elif `waiting_for_start_keywords` is True
            - forward to client
            - add to `welcome_keywords_sent` for client
        - else
            - do nothing
    - Do not forward `nolong` variables to clients with the `nolong` flag set
- If no custom rules match, forward to all clients

### When sending a keyword to a client

- If the connection's `welcome_sent` is
    - True: send message to client
    - False: append message to client's `pending_messages` list.

### Non key-value message from client

- `bang`: do not forward, but reply with all non-DELTA variables from
   the cache
- `start`: forward to upstream and set start_sent_at timestamp
- `exit`: send exit back, sleep 500ms, close connection
- `again`: forward to upstream only
- `nolong`: toggle nolong flag for client
- `pleaseBeSoKindAndQuit`
- forward to connected clients
- forward to upstream ONLY if config option used
- Anything else else: log warning and forward to all

### Non key-value message from upstream

- `load1`: forward to all clients
- `load2`: forward to all clients
- `load3`: forward to all clients
- `exit`: forward to clients
- Anything else: log warning and forward to clients

## Router discovery in a multi-router setup - FRDP

Routers need to handle connections from other routers differently than
normal clients. We also want to measure the latency between routers,
send information about connected clients, etc.

To achieve this easily we will send some `addon=` data on the PSX
network, using the prefix `addon=FRANKENROUTER:`. The whole setup is
called FRDP - FrankenRouter Discovery Protocol.

The next element in the message is the FRDP protocol version. This is
needed to check that all routers connecting to the network has the
same protocol version so they can understand eachother.

`addon=FRANKENROUTER:<protocol version`

In order to identify a router connecting to another router as a router
(and also to be a good PSX network citizen), the connecting router
will send `name=<simname>:FRANKEN.PY frankenrouter PSX router
<routername>` as soon as the connection is established.

The upstream router will then send a FRDP PING message to the
client. The client will see this message and do two things:

- Realize that the upstream connection is to a router
- Send a FRDP PONG message back to the upstream router
- Send a FRDP PING message to the upstream router

The upstream router then replies to the PING.

Both sides of the connection now know that the other side is another
router, and what the latency is. Both sides will then send PING
messages at regular intervals.

```text
upstream router                client router
<-------------- TCP connection -------------
<-------------- name=... -------------------
--------------- FRDP PING ----------------->
<-------------- FRDP PONG ------------------
<-------------- FRDP PING ------------------
--------------- FRDP PONG ----------------->
```

### FRDP PING

`addon=FRANKENROUTER:<protocol version>:PING:<ID>` is sent by one router to another. ID
is a random string used to make sure that we measure the latency
correctly.

FRDP PING messages should never be forwarded to upstream or connected
clients.

### FRDP PONG

`addon=FRANKENROUTER:<protocol version>:PONG:<ID>` is sent back by a router to another
router upon receiving a PING message. The ID should be the same as in
the PING message.

FRDP PONG messages should never be forwarded to upstream or connected
clients.

### FRDP AUTH

`addon=FRANKENROUTER:<protocol version>:AUTH:<PASSWORD>` is sent by a router to another if
password authentication is being used.

If the password is accepted by the upstream router, the connection is
established and a normal PSX welcome message is sent by the upstream
router.

If the password is not accepted a short error message "unauthorized"
is sent and the connection closed by the upstream router.

```text
upstream router                client router
<-------------- TCP connection -------------
[...]
<-------------- AUTH:<invalid password -----
--------------- unauthorized -------------->
[connection closed]
```

```text
upstream router                client router
<-------------- TCP connection -------------
[...]
<-------------- AUTH:<password -------------
--------------- id=... -------------------->
--------------- version=... --------------->
--------------- Qs123=456  ---------------->
[...]
```

### FRDP IDENT

`addon=FRANKENROUTER:<protocol version>:IDENT:<simulator name>:<router name>:<uuid>` is
sent by a frankenrouter to another frankenrouter to identify
itself. The uuid is automatically generated based on host ID and
listen port and used to ensure a unique router ID, even if someone
accidentally use the same simulator and router name as another router.

### FRDP ROUTERINFO

`addon=FRANKENROUTER:<protocol version>:ROUTERINFO:<JSON data>` is send by all
frankenrouters in the network. Sinc addon messages are forwarded to
the entire network, each router will have information about all other
routers and will therefore have an updated view of connected clients,
etc.

Note: this differs from the CLIENTINFO messgage. CLIENTINFO is
terminated by the first router that sees it and is used to set usable
names for clients connected to that router.

FIXME: document the JSON data format.

### FRDP CLIENTINFO

`addon=FRANKENROUTER:<protocol version>:CLIENTINFO:<JSON data>` can be sent by any client
to its upstream router to provide extra information about itself or
other local clients. This is used by a helper script that identifies
e.g running PSX instances by their window name and forwards that
information to the router.

FIXME: document the JSON data format

## Shared cockpit support

These setups need to work with the router:

- Single sim, single router
- Single sim, multiple routers
- Shared cockpit, single router (all connect to master sim router)
- Shared cockpit, client router (client router connects to master sim router)
    - Here it would be very useful to be able to switch the client
      router to another master sim (or PSX main server) without
      restarting clients.

### Authentication

To add basic protection from abuse and mistakes, the router will have
password support that can be used by a client router to connect. It
will also be possible to connect without a password if the IP address
is whitelisted (which will be needed for any non-router client
connecting to the router).

## Sim development and troubleshooting support

- Option to log all data in a single file, with timestamps and from
  where the data was sent and received to.
- Check for bad data
    - Config option for if bad data should be logged and/or dropped
    - Data where the length or type does not match the variable type
    - Lines terminated by only linefeed (some addons require CR+LF)
    - Keywords not listed in Variables.txt or otherwise known
    - Keyword with no value when value expected
- Option to log anything related to one or more variables
- Statistics data
    - Must support 24h flights
        - Aggregate data and save min, max, ... for last minute, hour, etc.
        - Save everything, but dump data older than N minutes every M minutes
    - Be able to show the most common variables sent per connection
    - Keep traffic counters (bytes/lines - sent/received)
    - Keep data on write buffer length and writedrain times
    - Keep FRDP ping data

## Python notes

- Will develop for version 3.13 from now on (has some useful things
  like asyncio.TaskGroups).
- For now, I will put the entire router in one module: "router". If it
  turns out later some parts (e.g the variable handline) are useful
  for other scripts, put them in a shared module.

<!---
https://github.com/markdownlint/markdownlint
https://github.com/markdownlint/markdownlint/blob/main/docs/RULES.md

Live preview:

retext router/docs/NOTES.md &
press Ctrl-e
--->
