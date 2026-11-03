# PSX bugs and feature requests

PSX behaviour that psxhacks has had to work around, collected during
development, for review before asking Aerowinx for fixes. Add to it
whenever a workaround is needed for something PSX does (or doesn't do).

Each item says what PSX does, what that costs us, how we work around it
today, and what we'd ask for. **Status**: *confirmed* (reproduced on a
test sim), *suspected* (seen, not yet isolated), *reported* (already
raised with Aerowinx).

## Weather

### Changing a weather zone's METAR rebuilds the zone

*Confirmed (2026-10-23).* When a zone's METAR changes its weather, PSX
rebuilds that zone's Wx about 1 s later from the METAR alone: CB coverage
0, PSX's own cloud tops, QNH from the METAR. A Wx written just before or
after is overwritten. Only TS/VCTS/RETS and CB cloud groups make PSX build
CBs.

- **Cost:** CBs placed by an addon (SIGMET areas, CAPE) vanish whenever
  the METAR is updated.
- **Workaround:** frankenweather writes a station-only METAR
  (`<name> <DDHHMMZ> RMK FRANKENWX ...`) for zones it builds itself, so
  PSX never rebuilds them (`frankenweather._station_only_metar()`).
- **Request:** a way to set a zone's METAR text without PSX rebuilding
  its Wx (e.g. a flag, or a separate display-only METAR variable).

### Out-of-range Wx field values are silently corrected

*Confirmed (2026-10-19, 2026-10-23).* PSX accepts some Wx values and
rewrites them within about 20-40 ms: cloud top/base 45000/45000 sentinels
(hi equal to lo makes PSX resend the previous Wx), CB tops above 45,000 ft
(corrected to 45000), and a lo cloud layer reaching above the hi layer's
base (2026-11-02: the lo top is cut to hi base - 50 ft and the lo base to
no more than that, whatever the coverages: hi 1077/100 made a lo
3500/2000 into 50/50). The correction appears to make the pending smooth
transit snap, which jumps the PFD altitude.

- **Workaround:** sentinels 44950/44900, a CB top cap of 44,999 ft
  (`fw_cb.py`, `CB_TOP_MAX_FT`), and the MSFS cloud sync keeping the lo
  layer below the hi layer (`frankenweather.py`, `_psx_layer_order()`).
- **Request:** document the valid ranges, or reject invalid values
  instead of correcting them.

### PFD altitude jumps on weather writes in flight

*Suspected; partly confirmed.* Several weather writes in flight have made
the PFD altitude jump (up to ~80 ft seen in flights) instead of following
the smooth transit armed with `WxSlowTransit` (Qi243):

- WxBasic writes while airborne (2026-10-20: every cruise snap of 9 ft or
  more coincided with one). Workaround: WxBasic is written only on the
  ground or while the aircraft is in the planet zone.
- WxAloft (tropopause) writes in flight (2026-10-22: 7 of 17 batches).
  Workaround: written rarely (hourly median, only significant changes,
  at most every 30 min). An isolated test on 2026-10-28 found WxAloft
  content changes alone (temperature, tropopause height, wind) never move
  the PFD, so the jumps need another write's transit to be cut short.
- A planet-zone QNH change of 10 hPa jumped the PFD ~300 ft within a
  second, with or without Qi243 armed (2026-10-28, first run). A second
  run with the same change barely moved the aircraft. Not yet understood.

- **Request:** QNH and temperature changes should follow the smooth
  transit; or document exactly which writes cut a transit short.

### Wind corridor rewrites can step the wind

*Suspected (2026-10-27).* With PSX's random wind/OAT deviation on
(`WxCorridorSel` second digit), each rewrite of `WxCorridorTxt` was
followed by a 20-38 kt wind change within about a minute. With the
deviation at 0 the same rewrites gave 0-7 kt. So PSX appears to pick new
random deviations on each rewrite (or on each `WxCorridorSel` write: not
yet isolated).

- **Cost:** a wind step every time an addon updates the corridor (hourly
  for frankenweather's enroute wind importer; it was every few minutes
  with the corridor points it briefly added around the aircraft, removed
  for that reason).
- **Workaround:** rewrite the corridor rarely; send `WxCorridorSel` only
  when PSX's value differs.
- **Request:** keep the random deviation stable (or drift it smoothly)
  across corridor updates.

### Malformed corridor text can be adopted, with the error shown only in PSX's UI

*Confirmed (2026-10-17).* A `WxCorridorTxt` write with a malformed record
was sometimes discarded (the previous corridor kept) and sometimes
adopted as the live value while PSX's UI showed a parse error. No network
variable reports the error (`DispatchError`, Qs455, didn't change).

- **Cost:** an addon can't tell that PSX is flying with a bad corridor,
  i.e. silently wrong winds and fuel predictions.
- **Workaround:** frankenweather builds Format-A records with fixed-width
  encoders (`wind_corridor.encode_wind_a()`); waypoint names are not yet
  validated.
- **Request:** reject a malformed corridor consistently, and expose
  corridor parse errors as a network variable.

### Adding or removing corridor points makes a wind spike

*Confirmed (2026-10-27, 10,000 ft).* Adding or removing waypoints near the
aircraft in `WxCorridorTxt` briefly about doubled the wind (40 -> 85 ->
45 kt within 2 minutes).

- **Workaround:** none needed now: frankenweather no longer adds points
  around the aircraft (it did off-route, briefly, in October 2026).
- **Request:** blend to the new corridor smoothly.

### Without a corridor nearby, zone surface winds reach cruise level

*Confirmed (2026-10-27).* Away from the wind corridor, PSX blends the
weather zones' surface winds up to the tropopause wind, so a zone update
or relocation can swing the wind at altitude (100° within a minute at
10,000 ft).

- **Workaround:** none; accepted. Corridor points around the aircraft
  fixed it but rewrote the corridor every few minutes, each rewrite a
  possible wind step (see above), so they were removed.
- **Request:** an option to blend from the zones only in the lower
  levels, or to use the tropopause/planet wind aloft.

### WxClust: 4 cells, one shared profile, replaced beyond ~250 nm

*Confirmed (2026-10-27/28).* `WxClust` holds the planet zone's 4 CB cell
positions. A client can set them, and placed cells are real CBs (radar,
lightning, turbulence), but:

- **they're only drawn on the weather radar while the aircraft is in the
  planet zone.** With the aircraft in one of the 7 weather zones
  (FocussedWxZone 1-7), none showed, at 24 to 245 nm, though WxBasic's CB
  coverage was 4 oktas; the same cells showed while the aircraft was in
  the planet zone. An addon that places zones around the aircraft (as
  frankenweather does) therefore can't use them to add CBs;
- all 4 share one CB profile (WxBasic's coverage, base, top);
- if any cell is more than about 250-300 nm from the aircraft, PSX
  replaces the whole set within 15 s;
- PSX recomputes the cells after a reposition;
- moving a cell makes its echo jump, even with `WxSlowTransit` armed;
  changing the coverage fades all 4 in or out over about 10 s (no PFD
  movement, FL350 and FL100).

- **Workaround:** frankenweather keeps the planet zone's CB coverage at 0,
  so PSX's own cells (placed wherever PSX likes) never become CBs.
- **Request:** draw the planet cells wherever they are, whichever zone the
  aircraft is in; more cells, a profile per cell, and a documented range
  limit (or no replacement of client-set cells).

### Zone names PSX rewrites

*Confirmed (2026-10-27).* A WxMode zone named like `Z1X` was rewritten by
PSX (to `SLLP28p`); names like `X9xx` are kept. PSX also rounds WxMode
lat/lon and changes the name suffix (`00n` -> `01p`).

- **Workaround:** frankenweather names its zones `X<digits>` and treats
  PSX's rounding as the same value.
- **Request:** document the zone-name rules.

### PSX's own turbulence uses the same WxBurst channels

*Confirmed.* PSX makes its own turbulence (CB, gusts, shear, CAT) and
lightning flashes on `WxBurst`, the same variable addons write, with no
way to tell the sender from the value.

- **Workaround:** frankenweather tracks its own writes and counts the
  rest as PSX's; the router logs show the sender.
- **Request:** a separate variable (or a flag) for PSX's own bursts.

### GUST bursts yaw and roll the aircraft

*Confirmed (2026-10-28).* `WxBurst` GUST codes (401-900) weather-vane the
aircraft: at 30% wave turbulence, mixes with GUST gave heading swings of
1.7° p95 and banks of 6.6° p95, against 0.01° and 0.04° without. At 60%
one GUST leg ended in a loss of control (under investigation).

- **Workaround (planned):** no GUST bursts for mountain wave.
- **Request:** a pure airspeed gust channel without the weather-vaning.

## Controls and systems

### Negative brake values give impossible acceleration

*Reported.* Negative `Brakes` (Qs357) values, e.g. from a misconfigured
pedal axis, produce a large sustained acceleration on the takeoff roll
(VHHH 2026-10-10, EDDF 2026-10-12; reproduced by injecting
`Qs357=-994;-994`).

- **Workaround:** fix the client's axis config.
- **Request:** clamp or reject negative brake values.

### The autopilot holds full nose-up through a stall, all the way down

*Suspected (2026-10-28).* In strong turbulence at FL350 (60% mountain
wave, test sim, frankenweather off, recorded burst sequence replayed),
the autopilot in ALT HOLD pitched up to hold altitude while the thrust
was at its limit, the speed bled away and the aircraft stalled. From
then on the autopilot stayed engaged and commanded full nose-up
(ApServo elevator +999) for over three minutes, at about 47° pitch and
up to ~60° bank, until the aircraft reached the sea. No disconnect, no
warning that ended it. Reproduced twice with different burst sequences.

- **Cost:** a turbulence encounter the crew doesn't react to ends in the
  ocean with the autopilot still on.
- **Workaround:** frankenweather's testing profile keeps wave turbulence
  below 50% and has no bank/gust bursts in wave.
- **To check first:** what a real 747-400 autopilot does in a stall (it
  has no envelope protection; does it disconnect, and when?).
- **Request:** if the real one disconnects (stall warning, attitude or
  servo limits), PSX should too.

### Momentary switches stay held if the release is lost

*Confirmed (2026-08-09).* PSX treats momentary switches (e.g. `Qh147`/
`Qh148` IsfdPushPlu/Min) as held while `=1`. A client that drops the `=0`
release leaves the switch held; with both ISFD buttons stuck, `Qi42`
oscillated at ~117 messages/s and flooded every client.

- **Workaround:** frankenrouter's per-client rate limit and master
  caution.
- **Request:** a timeout on held momentary switches, or edge-triggered
  variants.

## Networking and shared cockpit

### A PSX main client sends some of its own values right after connecting

*Confirmed (2026-11-02, WorldFlight; slave router traffic logs).* A PSX
main client that has just connected (here through a slave sim's router
to the shared cockpit) writes a few of its own values within 0.1-0.3 s,
before it has taken over the server's. Not its whole state: three to
eight variables per client, from this set: `Qi272`, `Qi225` CrashInhib,
`Qi130` MfEvent, `Qi190` MaweaBits, `Qs546` TkofRefNg, `Qs497`
WxCorridorSel, `Qs498` WxCorridorTxt and `Qs499` WxSigmet. One of a sim's
two main clients sent `Qi190` every time, the other never did. At the
CYVR gate this set `Qi190` to 257 (the A/P disconnect wailer bit) and
`Qs499` to `#` (no SIGMETs) in the shared cockpit; the same client did it
again after a restart (`Qi190` 257 then 2305). In a similar case
(2026-10-22) a slave sim's PSX instances sent `MfAcBus1`-`4`,
`ElecSysBitsMain`, CDU lines and Wx zones 20-40 s after connecting,
failing all four AC buses in the shared cockpit.

- **Cost:** a crew joining or restarting a PSX instance can start the
  wailer, wipe the SIGMETs or set malfunctions for everyone.
- **Workaround:** frankenrouter's post-connect quarantine drops these
  variables from new connections (malfunctions 30 s, TkofRefNg 5 s,
  MaweaBits 60 s, weather 120 s).
- **Request:** a main client shouldn't write values it hasn't got from
  its server, at least not until its welcome is complete.

### On the ground, an A/P disconnect press with the A/P off starts the wailer

*Confirmed (2026-11-03, test sim).* On the ground with the autopilot not
engaged, each A/P disconnect press (`Qh400`) toggles the wailer: a press
with it off turns it on, with AUTOPILOT DISC and the master warning
lights. In flight with the A/P off a press does nothing, and with the A/P
engaged the usual double press (disconnect, silence) works. Whether a
real 747-400 does the same on the ground is unknown.

- **Cost:** with a wailer already sounding (see above), a crew's quick
  presses kept starting it again at the gate.
- **Workaround:** none needed once the quarantine stops the stray wailer.
- **Request:** check against the real aircraft first.

### "Collision causes damage" breaks the aircraft from VATSIM traffic, in every PSX instance

*Confirmed (2026-11-03, main sim and test sim).* With Instructor > Human >
Traffic > "Collision causes damage" on (bit 1 of `TfcBits`, `Qi202`), a
traffic aircraft (`TfcPos`) at the ownship's position damages the
aircraft: a cabin explosion, engine FOD and compressor stalls, hydraulic
leaks in all four systems, and a few seconds later fuel tank leaks. It
happens parked at the gate, with the transponder in STBY, XPDR or TA/RA.
Every PSX instance that runs the check (the main server and the main
clients) activates the damage itself and sends it, all stamped with the
same sim time. In a shared cockpit on VATSIM five main clients of one
slave sim did this at once at the gate, and the engines then wouldn't
start. No aircraft in the list was at the ownship then. One had just
landed and was rolling towards it: 0.86 nm away, closing at about 100
kt, when it left the list 25 s before the damage. At its last speed it
would have passed about 300 m away at the moment of the damage (not yet
confirmed that PSX keeps moving a target that has left the list).

- **Cost:** random, severe damage out of the blue, even on the ground;
  in a shared cockpit it looks as if the slave sims are sending failures.
- **Workaround:** a master frankenrouter keeps the setting off (one of its
  enforced settings), and the bundled `known-good.situ` has it off.
- **Request:** ignore traffic that is on the ground or far below/above,
  or make the setting off by default; and let only the main server decide
  on collision damage, not every instance.

## Documentation

Things we had to learn from the forum, other addons' authors, or live
traffic, because `Variables.txt` doesn't say:

- `WxBurst` (Qi141) channel decoding (sink, bank, yaw, airspeed, gust,
  lightning above 900) and the 20 Hz injection limit.
- `WxCorridorSel` (Qs497) digits: embedded, Random slider position,
  Format menu.
- `WxMode` elevation is in feet, and Wx cloud/CB heights are above the
  zone's elevation.
- `SigmetOn` (Qi262) bits: 1 = embed, 2 = download.
- The GPS jamming/spoofing variables (Qs572, Qs573, Qi277, Qi276), and
  `TowTurnRadius` (Qi275), `IlsL` (Qs574), `NdMsg` (Qs570), `LocalRwys`
  (Qs571), all missing from `Variables.txt`; `GpsDrift` also has an
  undocumented 3-field transient form.
- The tropopause cap: PSX reads back 50,000 ft for anything higher.
