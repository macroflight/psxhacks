# frankenboostproxy changelog

## 2.0.0 (2026-10-14)

- **`frankenboostproxy` and `show_boost_gauges` now have real version
  numbers and are built/released as standalone EXEs** (previously source
  only). They share one version number and one release
  (`frankenboostproxy-v<version>`, a `.zip` with both EXEs - the same
  pattern `psxutils` uses for its four utilities), since
  `show_boost_gauges` only exists to display `frankenboostproxy`'s
  telemetry and has never had independent version history. See
  `docs/BuildSystem.md`.

- **Rewritten: elevation-offset feature dropped, acceleration/position
  telemetry added instead.** `frankenboostproxy.py` no longer applies a
  runtime elevation offset to the relayed boost feed (the old
  `addon=FRANKENBOOSTPROXY:OFFSET:<float>` protocol and
  `--max-offset-rate` option are gone, along with the router-side support
  for computing that offset). The raw-feed relay itself is kept
  (`--proxy-boost-port`, still lets multiple downstream clients share one
  upstream boost connection), now forwarding each line byte-for-byte with
  no transform.
- **New: 5 Hz acceleration/position telemetry**, derived from the boost
  feed via `frankenturb/boost.py`'s `AccelerationComputer` (position,
  heading, pitch, bank, derived ground speed, and body-frame
  heave/surge/sway/roll-rate/pitch-rate/yaw-rate). Available to any
  number of downstream clients on the new `--accel-port` (default 10751)
  at the full 5 Hz rate, and optionally (`--send-addon`, opt-in, off by
  default) broadcast on the PSX network as
  `addon=FRANKENBOOSTPROXY:ACCEL:<json>`, throttled to at most once per
  `--addon-interval` seconds (default 1.0).
- Added to `start_scripts`' `start_<x>.ps1`/`stop_<x>.ps1`/
  `restart_<x>.ps1` framework; see `Changelog-start_scripts.md` 1.9.0.
- **New: PSX connection (`--psx-host`/`--psx-port`) is now always
  maintained**, not just when `--send-addon` is set, so it can supply
  information the boost feed itself doesn't carry. The telemetry record
  (on `--accel-port` and in the addon broadcast) now also includes
  `ias_kt` (`Ias`/`Qi131` - a last-known value, since PSX only resends
  this one at connection/situ-load time, not continuously), and
  `sim_name`/`router_uuid` identifying the frankenrouter it's connected
  to, if any (via the FRDP SUBSCRIBE opt-in - see
  `router/docs/NOTES.md`'s "FRDP SUBSCRIBE" section - picking its own
  router's ROUTERINFO out from any others relayed in a multi-router
  shared cockpit by matching its own `name=`-provided client id). All
  three fields are `null` until PSX has actually supplied them, or
  permanently if not connected to a frankenrouter.
- **New: `vertical_speed_fpm` and `aoa_deg`** in the telemetry record,
  derived from the boost feed (`frankenturb/boost.py`'s
  `AccelerationComputer`, same central-difference technique already used
  for `ground_speed_kt`). `vertical_speed_fpm` is exact (rate of climb/
  descent over the ground); `aoa_deg` (pitch attitude minus the
  ground-track flight path angle) is only an approximation of true angle
  of attack, since it uses ground speed/track rather than true airspeed
  through the air mass - real wind, especially vertical wind, biases it.
- **New: `show_boost_gauges.py`**, a standalone Tkinter GUI (Linux and
  Windows, stdlib only) that connects to `--accel-port` and shows 16
  live analogue gauges: heave/surge/sway (g), roll/pitch/yaw rate
  (deg/s), pitch/bank attitude (deg), heading (deg, a proper
  compass-style gauge - needle wraps through 360° rather than hitting a
  false speedometer end-stop at north), IAS/TAS/ground speed (kt),
  vertical speed (fpm), approximate AoA (deg), gross weight (kg), and
  kinetic energy (GJ, TAS-based). The heave gauge spans -2.0..+4.0g,
  labelled every 0.5g, with a green reference line at 1.0g (level
  flight) and red limit lines at -1.0/+2.5/+3.75g; other gauges keep a
  blue 0 reference line (speeds/weight/energy have none). Each gauge
  shades an arc (and shows as text) the min/max value observed since
  the window opened or since "Reset Min/Max" was last pressed (not
  shown for heading, where that isn't a meaningful concept). IAS/TAS/
  ground speed/gross weight/kinetic energy also show a trend arc: a
  short purple arc inside the tick circle from the current value to a
  linear projection of where it'll be in 10 s. A row of toggle buttons
  at the top shows/hides individual gauges (each button doubles as a
  live value/min/max readout for that gauge, so it stays useful even
  while its analogue gauge is hidden), plus "Select All"/"Deselect All"
  buttons; only Heave, Roll Rate, and Kinetic Energy are shown by
  default. The grid reflows to however many columns the current window
  width allows and gains vertical/horizontal scrollbars once the
  visible gauges no longer fit. Reconnects automatically if
  frankenboostproxy isn't running yet or drops.
- **New: `tas_kt`, `gross_weight_kg`, `kinetic_energy_gj`** in the
  telemetry record. `tas_kt` comes from the PSX connection's new
  `PiBaHeAlTas` subscription (Mode=ECON, resent on effectively every PSX
  tick while flying - unlike `Ias`/Qi131, this one stays live).
  `gross_weight_kg` is `TrueZfw` (Qi123) plus the first 9 fields of
  `FuelQty` (Qs438, same per-tank-quantity format `psx_fuel_transfer.py`
  already relies on) - both reported by PSX in lbs, converted to kg
  here - via new PSX subscriptions. `kinetic_energy_gj` = 0.5·mass·TAS²,
  using `gross_weight_kg` and `tas_kt` - null whenever either input
  isn't known yet.
- **`aoa_deg`'s formula improved, and a `sideslip_deg` field tried and
  removed.** Both angles are now computed by rotating the ground-track
  velocity vector into the body frame (the standard aerodynamic
  definition) rather than the original pitch-minus-flight-path-angle
  formula, which was only exact at zero bank - verified against a
  rotation-orthonormality check, a zero-bank regression check, and a
  crab-angle check. `sideslip_deg` was then removed again: simulating a
  genuinely coordinated turn (zero true sideslip) with an ordinary 30 kt
  cruise crosswind component showed 3-6° of apparent sideslip purely
  from ground-track-vs-heading drift, confirming it isn't usable without
  a real 3D wind vector to correct for, which isn't available here.
  `aoa_deg` keeps the same caveat (and the same wind sensitivity, just
  smaller in practice) but was left in since it wasn't reported unusable.
- **The addon broadcast's message type is now `TELEMETRY`, not `ACCEL`**
  (`addon=FRANKENBOOSTPROXY:TELEMETRY:<json>`), reflecting that the
  record carries much more than acceleration data now (position,
  attitude, speeds, weight, energy, PSX-sourced state, ...).
- **`show_boost_gauges.py`: added a second way to receive the
  telemetry** - `--psx-port`/`--psx-host` connect to the PSX network
  itself (e.g. a frankenrouter or the PSX Main Server) instead of
  frankenboostproxy's `--accel-port`, and read the same data out of its
  `addon=FRANKENBOOSTPROXY:TELEMETRY:...` broadcasts (`AddonReader`,
  using `psx.py` - only receives anything if frankenboostproxy was run
  with `--send-addon`, and only as often as its `--addon-interval`
  allows). `--host`/`--psx-host` and `--port`/`--psx-port` are each
  mutually exclusive pairs. Also added `--single-instrument=<key>`
  (shows just one named gauge, no selection/reset buttons, no fixed
  window size - e.g. for embedding/screenshotting) and
  `--geometry=<Tk geometry string>` (window size and/or placement).
- **Toggle buttons now double as a live value/min/max readout** for
  their gauge (so that information stays visible even while the gauge
  itself is hidden), gained "Select All"/"Deselect All" buttons, and
  only Heave, Roll Rate, and Kinetic Energy are shown by default
  (everything else starts hidden, toggled on as needed) - previously
  all gauges started visible and buttons were plain on/off toggles.
- **Trend indicator changed from a text arrow to a dial arc.** The
  original design appended a ▲/▼/→ arrow to the value text; this wasn't
  reliably visible when a quantity happened to barely be changing right
  then (easy to miss, especially for slow-moving values like gross
  weight). It's now a short purple arc drawn on the dial itself, from
  the current value to a linear projection of where it'll be in 10 s -
  see `_TREND_PROJECTION_S`/`_TREND_WINDOW_S` if that horizon needs
  adjusting for a particular gauge (e.g. TAS barely moves in 10 s during
  steady cruise, so its arc is naturally small - that's correct, not a
  bug, but a longer horizon or an enforced minimum arc length are both
  easy follow-ups if more visibility is wanted).
