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
(corrected to 45000). The correction appears to make the pending smooth
transit snap, which jumps the PFD altitude.

- **Workaround:** sentinels 44950/44900 and a CB top cap of 44,999 ft
  (`fw_cb.py`, `CB_TOP_MAX_FT`).
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

- all 4 share one CB profile (WxBasic's coverage, base, top);
- if any cell is more than about 250-300 nm from the aircraft, PSX
  replaces the whole set within 15 s;
- PSX recomputes the cells after a reposition.

- **Request:** more cells, a profile per cell, and a documented range
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

### Momentary switches stay held if the release is lost

*Confirmed (2026-08-09).* PSX treats momentary switches (e.g. `Qh147`/
`Qh148` IsfdPushPlu/Min) as held while `=1`. A client that drops the `=0`
release leaves the switch held; with both ISFD buttons stuck, `Qi42`
oscillated at ~117 messages/s and flooded every client.

- **Workaround:** frankenrouter's per-client rate limit and master
  caution.
- **Request:** a timeout on held momentary switches, or edge-triggered
  variants.

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
