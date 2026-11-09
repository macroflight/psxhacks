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

### A zone's CB circles its centre once an hour

*Confirmed (Hardy, [forum](https://aerowinx.com/board/index.php/topic,4287.msg45487.html#msg45487);
measured on the ND 2026-11-04).* PSX draws a weather zone's CB always 7 nm
from the zone centre, on the bearing
`(sim minutes × 6 + sim seconds × 0.1 + zone elevation in ft + 2000) % 360`,
from `TimeEarth`. So every CB goes
round its zone clockwise once an hour, at about 44 kt. Measured: within
about 3 degrees through a full turn; writing `TimeEarth` moves it at once;
+90 ft of zone elevation turns it +90 degrees.

- **Consequences:** an addon can't place a CB where it wants by placing
  the zone, and a CB meant to stand still (or drift with the wind) moves
  in a 14 nm circle. Changing a zone's elevation (e.g. snapping it to an
  airport) turns its CB to another bearing at once.
- **Workaround:** frankenweather's drifting CBs (`--advanced-zones`) hold
  the bearing with the zone elevation (0-359 ft above the zone's own,
  6 ft lower each minute), and put the zone centre 7 nm from where the CB
  should be, every 15 seconds. That lifts the zone's clouds by as much and
  warms the air aloft a little (+500 ft: OAT +0.6 C, PFD altitude
  unchanged); +60 ft over 10 minutes moved the OAT 0.1 C. Moving the
  centre round the CB instead would swing the other zones' distances to it
  by 7 nm.
- **Request:** a way to set a zone's CB position (or bearing and
  distance), or to stop the rotation.

### Moving a zone every few seconds freezes the weather radar picture

*Confirmed (test sim, 2026-11-04, motion freeze).* With a zone's `WxMode`
(its position) written every 5 seconds, the weather radar picture stopped
changing altogether: not one pixel in 12 minutes, though the zone's CB
moved. Every 15 or 30 seconds, it redrew normally (about 15 s after a
change). `WxSlowTransit` before each write made no difference.

- **Workaround:** frankenweather moves its drifting zones every 15 seconds.
- **Request:** redraw the radar picture regardless of how often zones move,
  or document the minimum interval.

### A VA SIGMET without an area in its text may damage aircraft anywhere

*Observed on two sims (2026-11-05); not reproduced on the test sim.* The
Japan Meteorological Agency issued a volcanic-ash SIGMET for Sakurajima
with only the volcano's position and a level, no area:
`PSN N3136 E13039 VA CLD OBS AT 1317Z ABV FL060 MOV SE`.
aviationweather.gov's data has a 50 nm circle around the volcano, but PSX
reads the area from the SIGMET's text. About 100 s after frankenweather
passed it to PSX (13:40:13Z), PSX itself activated engine flameouts, all
four pitots and cargo fire warnings in turn on the main sim, over the
North Sea at 11,000 ft, 5,000 nm from the volcano: the volcanic-ash damage
pattern. No malfunction was written over the network and no traffic was
within 9 nm (not the collision damage). At almost the same time another
sim with frankenweather (SIMFEST), on the ground on a taxiway, got the
same failures, starting with the aft cargo fire warning.

We couldn't reproduce it on the test sim (same PSX version). PSX drew the
SIGMET as a small circle around the volcano, the Instructor's SIGMET tab
stayed clear, and nothing failed in any of these:

- frankenweather's exact 13:40 SIGMET text, at the main sim's position,
  altitude and time;
- the same text on the ground with engines running;
- PSX downloading its own SIGMETs, without frankenweather;
- both main-sim autosaves (from just before and just after the failures)
  loaded in PSX.

A VA SIGMET whose area covers the aircraft does give the same damage there
(about 90 s after entering it: pitots, then cargo fire). So what made both
sims "inside" the ash is unknown. A PSX that has been running for hours
may hold state that a fresh situ load doesn't.

Suspected trigger: a write to PSX's clock. PSX did nothing for 101 s
after the SIGMET arrived. The first failure (MfFlameOut3, 13:41:54.953Z)
came 61 ms after frankenweather's periodic clock sync wrote `TimeEarth`,
`TimeClockL` and `TimeClockR` (13:41:54.892Z), the first clock write after
the SIGMET. The write changed the clock by only a few milliseconds, and
nothing else unusual was written to PSX in between. frankenweather wrote
the clock about 45 times an hour then, so a failure that close to one by
chance is under 0.1 %. Perhaps a clock write makes PSX re-evaluate its
SIGMETs, and on the main sim that put the aircraft inside this one. The
same clock write on the test sim, with the main sim's autosave from just
after the failures loaded (both a 5 h and a millisecond-sized change), did
nothing.

- **Workaround:** frankenweather no longer uses VA SIGMETs by default, so
  PSX gets none from it. A toggle on the router's /efb page (SIGMET types,
  Normal preset) switches them back on if wanted. The SIGMET texts
  themselves are passed on unchanged.
- frankenweather also no longer writes PSX's clock every minute or two
  (only when it's more than 5 s off), which removes the suspected trigger.
- **Request:** ignore a SIGMET whose text has no area, or use the
  position with a small radius, and respect its level (here "ABV FL060");
  and if possible say what makes PSX apply a VA SIGMET to the aircraft,
  and whether a write to `TimeEarth` makes it re-evaluate its SIGMETs.

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

### The autothrottle stops moving the levers for 8-20 s after a WxBurst (10.191+)

*Confirmed (2026-11-08), reported to Aerowinx (forum, 2026-11-09).* Since PSX 10.191 (changelog
191.09: "For turbulence induced airspeed deviation peaks, the autothrottle
is now more tolerant and accordingly remains dormant until the tolerance is
exceeded"), the A/T in SPD mode leaves the thrust levers where they are for
a fixed time after some WxBurst (Qi141) values, however far IAS then drifts
from the MCP speed. 10.190 and earlier don't do it (tested 10.178, 10.186,
10.190 vs 10.191, 10.195 on the headless test sim, a burst sent by a client
3 s after an MCP speed change, levels at 5,000 ft):

- GUST 401-408: levers still exactly 8.0 s (sent by a client; 409: 2 s late;
  410-418: 1 s late, sometimes a 5 s pause after; 420, 450, 499: no freeze).
  PSX's own 409s froze them about 20 s (orbit near LPPD, 29030G43KT). PSX's own gust bursts on a real approach (FTECH2/MACRO at
  SCCI, 2026-11-08, zone wind 190/07G15): 401-408 every 12-37 s, the levers
  still for exactly 9.0 or 20.2 s after almost every one, IAS 16-20 kt below
  the MCP speed meanwhile; Aerowinx's own situ "03 Approach 008 - Chicago -
  Crosswind gusts" (411-413): 8 freezes of 6-19 s on 10.195, none on 10.190.
- SPD minus -301 to -329: 8.6-12 s (the weaker, the longer); from -332 on
  about 1 s, -399 and +350, +399 normal. +307: 2 s late.
- SINK, BANK, YAW: no effect.

- **Cost:** speed decays 15-20 kt below target (or overshoots) on approach
  in gusty weather and in frankenweather turbulence; crews reported "slow
  autothrottle" from November 2026 (Baker 11-05, FTECH2/MACRO 11-08).
- **Workaround:** none for PSX's own gust bursts (the A/T has frozen before
  anything reaches the network; the real surface gusts stay, user's call).
  frankenweather (63317dd, `--no-psx-at-workaround` to turn off): its GUST
  bursts go out as SPD, SPD-minus weaker than 35 as 35 with probability
  strength/35.
- **Request:** the tolerance should be about the speed deviation, not a
  fixed dormant time after a burst; at least the A/T must respond once IAS
  is clearly below the MCP speed.
- Test tools: `psx-at-burst-test.py` (standalone, any PSX), headless
  `tests/gustat.py`, `tests/chicago.py`, `tests/psxversion.sh`.

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

### A main client keeps parts of its start situ after its first welcome

*Confirmed (2026-11-03, test setup, slave sim restarted three times in
flight; slave router traffic logs).* A freshly started PSX main client
reacts to its first welcome as if it had loaded its own start situ, and
that overwrites part of the welcome inside the client. The welcome itself
was correct: it was identical to a later "Reset clients" welcome, which
fixed the client almost every time. What we saw:

- DRIVE DISC lights on for all four engines (Overhead). The welcome had
  `DriveDisc1`-`4` = 96 (in flight, light off); Basic 000 has 352, which
  is most likely the light bit (cold and dark).
- A wrong IAS (322 kt against 315-317 kt), and a wrong standby horizon
  with a low airspeed. Each client computes these itself.
- No wind corridor or no SIGMETs: for about 8 s after its welcome the
  client writes its own `WxCorridorTxt`/`WxSigmet` (`#`), `MaweaBits`
  (2048, as in Basic 000), `CrashInhib` 2000 and `FuelCtrL`/`FuelCtrR`.
  The router drops most of these, but the client keeps them, since PSX
  doesn't send a client's write back. The five main clients also
  ping-ponged `FuelCtrL`/`FuelCtrR` between 1 and 257 for about 4 s.

Other state that's wrong inside a client but never sent can't be seen
on the network at all.

- **Cost:** after a sim restart, one main client or another shows
  subtly wrong state until someone notices and resets it.
- **Workaround:** a slave frankenrouter resets each whitelisted PSX main
  client's welcome once, 15 s after its first one
  (`client_auto_reset_delay`), and a reset restarts the post-connect
  quarantine. When the quarantine drops a client's write, the router sends
  it the network's value back. Holding the welcome back 10 s didn't help
  (tested 2026-11-03): the clients wrote nothing while waiting, then the
  same values at the same times after `load3`, so it's their reaction to
  their first welcome, not a race with their own startup. After a reset
  they usually don't, but one of five still wrote an empty corridor.
- **Request:** a main client should apply its first welcome as it applies
  later ones, without values of its own start situ.

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

### No PSX instance reports its frame rate on the network

*Feature request (2026-11-07).* PSX shows its frame rate only in its own
window. In a shared cockpit a slow PSX (main server or any main client in
any sim) causes odd behaviour that is hard to trace, and nothing on the
network says which instance is struggling: the boost feed of a test sim
had gaps of up to a second today with no other sign. An ordinary
variable doesn't help, because it is one shared value that every
instance would overwrite.

- **Workaround (planned):** frankenboostproxy measures its own sim's boost
  feed (rate, longest gap) as a stand-in.
- **Fix needed (Aerowinx):** let every PSX instance send its own frame
  rate now and then, tagged with its client id (e.g. a non-shared message
  like `addon=AEROWINX:FPS:<id>:<fps>`), so routers can show all of them.

### The FMC's next step climb isn't on the network

*Feature request (2026-11-08).* frankenpilot should do a step climb when
VNAV says it's due ("STEP TO FL350 / AT NOW" on VNAV CRZ page 2/3, NG FMC),
whether the step comes from the LEGS page ("FL330S") or from VNAV's
automatic steps (STEP size). None of that is on the network except the CDU
screen lines of a CDU that happens to show the page: the step size is
(`StepSizeNg`, Qi266: 0, the size in ft, -2 = RVSM), but not the STEP TO
level nor the AT time/distance or NOW. (`StepClbFreeze`, Qs563, doesn't
change when a step becomes due or is no longer due.) The STEP TO level
isn't simply the current level plus the step size either: from FL310 VNAV
showed FL340 with STEP 1000 and FL350 with STEP 2000.

- **Workaround (planned):** frankenpilot switches its own side's CDU to
  VNAV CRZ 2/3 for a moment, reads STEP TO / AT from the screen lines and
  switches back to the page that was showing.
- **Fix needed (Aerowinx):** a variable with VNAV's next step (STEP TO
  level, the step point as time and distance, and whether it's due now),
  for example `StepClbNext` = `FL350;1010;265;0` / `FL340;;;1` for NOW.

### A main client's aircraft bounces on its gear during refuelling

*Seen 2026-11-08, parked: not reproduced.* In a shared cockpit (master
FTECH2, slave sim MACRO, both PSX 10.195), the MSFS external model of the
slave sim bounced up and down while parked at YSSY during PSX's own
refuelling (Instructor, to a preselected amount). The slave's PSX main
client's boost output showed it: flight-deck height swinging by about 1 ft
(sd 0.23 ft), a slow sink and then jumps of up to 0.23 ft, in time with
the master's fuel updates (`FuelQty`, 5 per second). It dropped to a fifth
(sd 0.045 ft) in the same second refuelling stopped, but didn't go away
completely. The master's own position updates (`PiBaHeAlTas`, about once
a second while parked) were smooth.

Not reproduced on the headless test sim (also 10.195): no bounce on a main
server or a main client (behind two frankenrouters, like the slave sim)
during refuelling, light or at the slave's weight, with or without a
constant MSFS-style ground elevation injection every 0.5 s or a
frankenboostproxy in the boost path. Ruled out: the boost proxy (it passes
lines unchanged), the elevation injection (constant), PSX.NET.MSFS.Router
feedback (it writes nothing that depends on the boost data),
frankenweather gusts (none), CPU load (under 50 %). Untested: network
timing between master and slave.

PSX's own formulas (Hardy Heinlin, Aerowinx forum 2019): boost
flight-deck altitude = reference altitude + 28.412 ft + 92.5 ft x
sin(pitch); on the ground the reference altitude is the elevation plus
3 - gross weight (kg) / 100,000 ft, the gross weight being fuel
(`FuelQty`) + `TrueZfw`, not lift. So a parked aircraft sinks smoothly
as fuel goes in: about 0.26 ft for the 26 t of this refuelling. The fuel
went in evenly (an update every 0.20 s, +356 units, one tank group at a
time, now and then a late update with two steps): no update was big
enough to move the height by more than a thousandth of a foot, so
neither explains jumps of 0.23 ft.

- **Workaround:** none needed so far; it stops with the refuelling.
- **For Aerowinx, if it comes back:** a main client seems to work out the
  height on the gear from the weight itself and to be corrected by the
  master's slower position updates.

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
