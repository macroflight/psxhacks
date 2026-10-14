# FrankenUSB

A (partial) replacement for PSX's built-in USB controller subsystem,
for Aerowinx PSX.

## Who this is for

PSX's native USB handling covers most of what you need, but a few
things are either missing or don't quite match real hardware. FrankenUSB
sits in front of (not instead of) PSX's own USB handling for the devices
you configure, and lets you do things PSX's native USB page cannot:

- Bind different actions to long and short presses of the same button.
- Drive the reverse levers on throttle hardware like the real 747 (lift
  the lever to arm reverse, then advance for more reverse thrust), either
  via a dedicated hardware reverse lever axis or via a button that toggles
  reverse mode on a normal throttle axis.
- Handle the rotary knob/mode-selector on the Thrustmaster Boeing throttle
  quadrant, and a more general "rotary knob with modes" mechanism for other
  hardware.
- Handle two-button gear levers (UP/DOWN, as on the Thrustmaster Boeing
  yoke) and three-position gear levers built from two buttons with a
  neutral detent (as on the STECS throttle).
- Use a normal aileron axis as a nosewheel tiller while taxiing, toggled
  or forced on/off by a button, auto-disabled once you're fast enough to
  no longer need it.
- A speedbrake axis that spends most of its physical travel on the
  in-flight speedbrake range (which is much more sensitive to use than
  the full ground-to-flight range PSX's native handling gives you).
- A throttle/reverse axis that mimics PSX's native "Throttle"/"Reverse"
  axis interlock behavior (closed throttle required before reverse can be
  used) for cases where you don't want the separate reverse-lever-axis
  behavior above.
- "Latch"/"unlatch" throttle behavior and an audible throttle-sync cue, so
  taking back manual control from the autothrottle doesn't cause a visible
  jump in the PSX throttle position.
- Bind a button to common multi-step procedures done during busy phases of
  flight (e.g. runway entry, after takeoff, after vacating the runway).
- A seat-select button that swaps frankenusb's own left/right variable
  mapping, the PSX seat layout, the PSX Human Pilot seat, and (optionally)
  the PSX.NET.VATSIM ACP seat, all at once — for one set of hardware shared
  between two seats.

You can find sample config files in the
[config_examples](../config_examples) directory — see
[Example configs](#example-configs) below for a guided tour of them.

Works with any hardware that [pygame](https://www.pygame.org/) supports
(the only thing found so far that doesn't work is one specific type of
rudder pedals). If your USB device isn't detected by `show_usb.py`,
frankenusb won't be able to use it either, since both use pygame to
access USB devices.

## Running it

```text
python frankenusb.py --config-file frankenusb.conf
```

A standalone Windows EXE is also available — see the main
[README](../README.md#binary-packages) for where to get it and how
binary packages in general work. With the EXE, put your config file in
the same directory and name it `frankenusb.conf` (or pass
`--config-file`).

### Command line options

| Option | Default | Meaning |
| --- | --- | --- |
| `--config-file` | `frankenusb.conf` | Path to your config file (a Python module, see below). If this doesn't exist but `frankenusb-frankensim.conf` does, that's used instead (backwards compatibility with an older default name). |
| `--debug` | off | Verbose logging. |
| `--quiet` | off | Only critical messages. |
| `--max-rate` | `20.0` | Maximum rate (Hz) at which any single PSX variable is updated from axis motion. |
| `--axis-jitter-limit-low` | `0.005` | Axis movements smaller than this (in pygame's -1.0..1.0 units) are filtered out as noise. |
| `--axis-jitter-limit-high` | `1.5` | Axis movements larger than this are filtered out (protects against spurious USB glitches). |
| `--psx-host` | `127.0.0.1` | PSX Main Server / router hostname or IP. |
| `--psx-port` | `10747` | PSX Main Server / router port. |
| `--psx-port-override` | none | Forces `--psx-port` to this value (used by `start_scripts`; prints a warning if it conflicts with `--psx-port`). |
| `--long-press-limit` | `0.5` | Button presses held longer than this (seconds) are "long" presses. |
| `--disable-rudder-tiller-interconnect` | off | Disables the automatic rudder→tiller blending described under [Tiller mode](#tiller-mode). |
| `--version` | — | Print the version and exit. |

Run `python frankenusb.py --help` for the authoritative list.

## The config file

Despite the `.conf` extension, a frankenusb config file is a real Python
module — it's loaded with `importlib` and executed, and is checked by
`make configlint` (pylint) in this repo's own `config_examples`. That
means you can use real Python syntax: dict/tuple literals, f-strings,
loops, and helper variables to avoid repeating yourself (see the
[modal rotary example](#modal-rotary-knobs) for a case where that's used
on purpose).

A config file defines two module-level names:

```python
CONFIG_MISC = {
    'THROTTLE_SYNC_SOUND': 'C:/fs/psx/Aerowinx/Audio/Basics/cab1.wav',
    'THROTTLE_LATCH_SOUND': 'sounds/latch.wav',
    'THROTTLE_UNLATCH_SOUND': 'sounds/unlatch.wav',
    'DO_NOT_SEND_TO_PSX': ['SomeVariableName'],
}

CONFIG = {
    'Exact pygame joystick name': {
        'axis motion': { 0: {'axis type': '...', ...}, ... },
        'button down':  { 0: {'button type': '...', ...}, ... },
        'button up':    { 0: {'button type': '...', ...}, ... },
        'button long':  { 0: {'button type': '...', ...}, ... },
        'button short': { 0: {'button type': '...', ...}, ... },
    },
    'Another joystick name': { ... },
}
```

`CONFIG_MISC` is a flat dict of optional global settings:

- `THROTTLE_SYNC_SOUND` / `THROTTLE_LATCH_SOUND` / `THROTTLE_UNLATCH_SOUND`
  — paths to `.wav` files played via `pygame.mixer` for the throttle
  features described under [Throttle and reverse axes](#throttle-and-reverse-axes).
  All optional; a missing sound just means that cue is silent.
- `DO_NOT_SEND_TO_PSX` — a list of PSX variable names that are silently
  dropped before being sent to PSX, even though frankenusb still tracks
  and computes them internally. Useful while testing a new mapping
  without risking sending bad values to a live sim.

`CONFIG` is keyed by the **exact** pygame joystick name, as reported by
`show_usb.py` (run it and move/press the device you want to identify — see
the main [README](../README.md#short-frankenusb-tutorial-if-you-use-the-binary-package)
for the full workflow). Devices pygame finds but that aren't present in
`CONFIG` are logged as a warning and otherwise ignored.

Each device's sub-dict can have up to five keys, each keyed by the USB
axis/button number:

- `'axis motion'` — one entry per axis you want to handle. Every entry
  needs an `'axis type'` key (see [Axis types](#axis-types) below) plus
  that type's own keys.
- `'button down'` / `'button up'` — fire the instant the button is
  pressed/released. Every entry needs a `'button type'` key (see
  [Button types](#button-types) below) plus that type's own keys.
- `'button long'` / `'button short'` — alternative to (not in addition
  to) `'button up'` for the same button number, see
  [Long and short presses](#long-and-short-presses).

Any axis/button number that isn't listed is simply ignored (not an
error). Using an `'axis type'`/`'button type'` string frankenusb doesn't
recognize raises an exception at runtime — not caught at config-load time,
so trigger every button/axis of a new config at least once after editing.

## Axis types

Set via `'axis type'` in an `'axis motion'` entry.

### `NORMAL`

Direct linear mapping from the USB axis to a PSX variable.

```python
0: {
    'axis type': 'NORMAL',
    'psx variable': 'FltControls',
    'indexes': [0],       # which semicolon-delimited field(s) of the variable to write
    'psx min': -999,
    'psx max': 999,
},
```

- `'psx variable'`, `'indexes'`, `'psx min'`, `'psx max'` — mandatory.
- `'axis swap'` (default `False`) — invert the axis direction.
- `'static zones'` (default `[]`) — list of `(low, high, forced_value)`
  tuples; a raw axis value inside `[low, high]` is forced to
  `forced_value` before the normal linear mapping runs (e.g. a detent or
  dead zone).
- `'tiller': True` — while [tiller mode](#tiller-mode) is active, this
  axis drives the nosewheel `Tiller` variable instead of its normal PSX
  variable/indexes. See that section.

### `SPEEDBRAKE`

A speedbrake axis where most of the physical travel is spent on the
in-flight range, giving finer control there than PSX's native handling
(which splits travel evenly across the much larger stowed→armed→flight→ground
range).

```python
1: {
    'axis type': 'SPEEDBRAKE',
    'psx variable': 'SpdBrkLever',
    'limit stowed': 0.05,       # below this (normalized 0..1): stowed (PSX value 0)
    'limit armed': 0.1,         # below this: armed (PSX value 41)
    'limit flight upper': 0.9,  # above this: full/ground (PSX value 800)
    # between 'limit armed' and 'limit flight upper': in-flight range,
    # linearly mapped onto PSX's 61-375
},
```

`'axis swap'` is also supported (default `False`).

### `AXIS_SET`

For axes with discrete positions, e.g. a flap lever. Resyncs automatically
if something else changes the PSX value — the physical lever has to pass
back through the matching zone before it takes control again, to avoid a
jump.

```python
6: {
    'axis type': 'AXIS_SET',
    'psx variable': 'FlapLever',
    'zones': [
        (-1.00, -0.50, 0),  # (low, high, PSX value) — flaps up
        (-0.50, -0.25, 1),  # flaps 1
        (-0.25, 0.00, 2),   # flaps 5
        (0.00, 0.25, 3),    # flaps 10
        (0.25, 0.50, 4),    # flaps 20
        (0.50, 0.75, 5),    # flaps 25
        (0.75, 1.00, 6),    # flaps 30
    ],
},
```

To set up the zones for your own lever: run `show_usb.py`, note the axis
value at each detent position, and build the `zones` list from those
observed boundaries (see the worked example in
`config_examples/frankenusb-tm-boeing-throttle.conf`).

### `AXIS_PSX_THROTTLE` / `AXIS_PSX_REVERSE`

Mimics PSX's own native "Throttle"/"Reverse" axis types: a pair of axes
(one forward-only, one reverse-only) that share an interlock — you can't
move the reverse axis until the matching throttle axis is at idle, and
vice versa. Use these when your hardware has a *separate* reverse lever
axis, rather than a single lever plus a reverse-arming button (for that,
see [`THROTTLE_WITH_REVERSE_BUTTON`](#throttle_with_reverse_button)
below).

```python
0: {
    'axis type': 'AXIS_PSX_THROTTLE',
    'psx variable': 'Tla',          # mandatory, must be 'Tla'
    'engine indexes': [0, 1],       # mandatory, which engines this lever drives
    'axis swap': True,
},
1: {
    'axis type': 'AXIS_PSX_REVERSE',
    'psx variable': 'Tla',
    'engine indexes': [0, 1],       # must match the paired throttle axis
    'axis swap': True,
},
```

- `'engine indexes'` — mandatory, no default; list of engine indexes
  (0-3) this lever controls. Must match between the throttle/reverse
  pair.
- `'axis swap'` (default `False`), `'axis min'`/`'axis max'` (default
  `-1.0`/`1.0`), `'static zones'` (default `[]`) — as above.
- `'allow movement with at on'` (default `False`) — if `True`, this axis
  can move the PSX throttle even while the autothrottle is active
  (normally the autothrottle has exclusive control until disconnected).

### `THROTTLE_WITH_REVERSE_BUTTON`

A single throttle lever where a separate button (not a second axis)
arms/disarms reverse mode. While reverse is armed, advancing the same
lever moves from reverse idle towards full reverse instead of forward
idle towards full power. See
[Reverse levers via a button](#reverse-levers-via-a-button) for the
matching button config, and
[Throttle latch/unlatch and the sync sound](#throttle-latchunlatch-and-the-sync-sound)
for the autothrottle-handoff behavior shared with
`AXIS_PSX_THROTTLE`/`AXIS_PSX_REVERSE`.

```python
4: {
    'axis type': 'THROTTLE_WITH_REVERSE_BUTTON',
    'psx variable': 'Tla',
    'axis swap': True,
    'axis min': -1.00,
    'axis max': 1.00,
    'psx idle': 0,
    'psx full': 5000,
    'psx reverse idle': -3000,
    'psx reverse full': -8925,
    # Reverse can only be toggled while the lever is within this range
    # (i.e. at/near idle)
    'reverse lever unlocked range': (0.9, 1.0),
    'engine indexes': [0, 1],
    'reverse button': 4,  # the USB button number that arms/disarms reverse
},
```

## Button types

Set via `'button type'` in a `'button down'`/`'button up'`/`'button
long'`/`'button short'` entry.

### Setting PSX variables directly

- **`SET`** — `psx_send_and_set('psx variable', 'value')`. Keys:
  `'psx variable'`, `'value'`.
- **`SET_MULTI`** — same, for several variables at once. Key:
  `'psx variables'`: a list of `(key, value)` tuples.
- **`SET_ACCELERATED`** — like `SET`, but for a delta-type variable
  (where you normally send e.g. `1`/`-1`): if this button fired less than
  `'minimum interval'` seconds ago, the value is multiplied by
  `'acceleration'` first. Keys: `'psx variable'`, `'value'`,
  `'minimum interval'`, `'acceleration'`.
- **`INCREMENT`** — read an integer PSX variable, add `'increment'`,
  optionally clamp/wrap, write it back. Keys: `'psx variable'`,
  `'increment'`; optional `'min'`, `'max'`, `'wrap'` (`True`/`False`,
  default `False` — clamp instead of wrapping at the limit).
- **`INCREMENT_MULTI`** — `INCREMENT` for several variables at once. Key:
  `'psx variables'`: a list of `(key, increment, min, max, wrap)` tuples.
- **`BIGMOMPSH`** — OR bit 1 into an integer PSX variable (for
  momentary-push-type variables). Key: `'psx variable'`.
- **`ADDON`** — send a raw `addon=...` PSX message, e.g. to hand off to a
  sibling addon like `frankentanker.py`. Key: `'value'`.

All of the above (and most other button types below) run their
`'psx variable'` through the [right-seat translation](#right-seat-mode)
before sending.

### Reverse levers via a button

Companion to the `THROTTLE_WITH_REVERSE_BUTTON` axis type above. Each
needs the matching axis's number via `'axis'`:

- **`REVERSE_LEVER_UP`** — arm reverse (bind to the reverse-arming
  button's `'button up'`, i.e. lever raised).
- **`REVERSE_LEVER_DOWN`** — disarm reverse (bind to `'button down'`).
- **`REVERSE_TOGGLE`** — toggle reverse on a single momentary button
  press.
- **`STEPPED_REVERSE_TOGGLE`** — cycles normal → reverse idle → full
  reverse → reverse idle → normal on each press, for hardware with a
  single reverse button and no separate lever to read a position from.

All four require `'axis'` to name the throttle axis they act on, and (for
`REVERSE_LEVER_UP`/`DOWN`/`TOGGLE`) that axis's position must currently be
inside its `'reverse lever unlocked range'` or the request is ignored
(logged, not an error) — mirroring the real aircraft, where you can't
engage reverse except at idle.

### Flight control locks

Sends a `FRANKENROUTER:1:...` `addon=` message that a frankenrouter in
your network interprets (see [frankenrouter's docs](../router/README.md)
for what it does on that side). No extra keys.

- **`FLIGHT_CONTROL_MY_CONTROLS`** — release the flight-control lock to
  yourself (`MY_CONTROLS`).
- **`FLIGHT_CONTROL_NO_CONTROL_LOCKS`** — release all locks
  (`NO_CONTROL_LOCKS`).
- **`FLIGHT_CONTROL_ALL_CONTROL_LOCKS`** — lock all flight controls
  (`ALL_CONTROL_LOCKS`).

### Tiller toggle

- **`TILLER_TOGGLE`** — toggles [tiller mode](#tiller-mode) by default.
  Optional key `'set'`: `'on'` forces tiller mode on regardless of
  current state, `'off'` forces it off; omit `'set'` to just toggle.

### Flight phase buttons

Multi-step procedures for busy phases of flight — see
[Flight phase procedures](#flight-phase-procedures) below for exactly
what each phase does.

- **`ACTION_FLIGHT_PHASE_BUTTON`** — run one named phase immediately on
  press. Key: `'phase'`.
- **`ACTION_FLIGHT_PHASE_TRIGGER`** — for a multi-position rotary switch
  built from several momentary buttons (one per position): checks which
  of the listed buttons is currently physically held down and runs the
  matching phase. Key: `'button to phase'`: a dict `{button_number:
  phase_name}`.

### Thrustmaster Boeing throttle rotary

Hardware-specific support for the 3-position mode selector + rotary knob
on the Thrustmaster TCA Boeing throttle quadrant.

- **`TMBOEING_ROTARY_MODE`** — sets the active mode. Key: `'position'`,
  one of `'IAS/MACH'`, `'HDG/TRK'`, `'ALTITUDE'`.
- **`TMBOEING_ROTARY_SEL`** — pushes the MCP select button matching the
  current mode (`McpPshSpdSel`/`McpPshHdgSel`/`McpPshAltSel`). No extra
  keys.
- **`TMBOEING_ROTARY`** — turns the knob; which MCP "turn" variable it
  writes to (`McpTurnSpd`/`McpTurnHdg`/`McpTurnAlt`) depends on the
  current mode. Key: `'direction'`: `'cw'`/`'ccw'`.

See [Example configs](#example-configs) for the full wiring — the rotary
itself needs two buttons (one per direction), each bound on both
`'button down'` and `'button up'`.

### Modal rotary knobs

A more general version of the above, for any rotary knob whose action
should depend on a mode you select separately — see
`config_examples/frankenusb-modal-rotary-example.conf` (listed under
[Example configs](#example-configs) below) for a full worked example.

- **`MODAL_ROTARY`** — the knob turn itself. Keys: `'rotary id'` (links
  this knob to its mode selector buttons), `'direction'` (`'cw'`/`'ccw'`),
  `'modes'` (dict: mode name → mode config, see below); optional
  `'minimum interval'`/`'acceleration'` (same meaning as
  `SET_ACCELERATED`).
- **`MODAL_ROTARY_MODE_SET`** — directly set the active mode for a rotary
  id. Keys: `'rotary id'`, `'mode'`.
- **`MODAL_ROTARY_MODE_CYCLE`** — cycle through an ordered list of modes
  on each press. Keys: `'rotary id'`, `'mode order'` (list of mode
  names; the first entry is also the initial mode on first press).

Each entry in a `MODAL_ROTARY`'s `'modes'` dict is either:

- `{'psx variable': ..., 'value cw': ..., 'value ccw': ...}` — send a
  fixed value depending on direction, or
- `{'psx variable': ..., 'increment': ...}` — add (cw) or subtract (ccw)
  this amount from the variable; for a semicolon-delimited variable, add
  `'increment index'` (which field to place the increment in) and
  `'fields'` (total field count) to zero-fill the rest.

### Radio tuning

For RCP-style radio panels where one physical knob tunes either the "big"
or "small" digits, selected separately.

- **`RADIO_TUNE_TOGGLE_KNOB`** — toggle which knob (`'big'`/`'small'`)
  subsequent `RADIO_TUNE` turns affect. No extra keys.
- **`RADIO_TUNE`** — turn the currently-selected knob. Keys: `'value'`
  (turn amount), `'psx variable'` (e.g. `'RotSelRcpL'`).
- **`RADIO_SWITCH`** — push the standby/active frequency swap
  (`SwitchesRcpL`/`R`) and reset the knob selection back to `'big'`, so
  the next tune always starts on the big knob. No extra keys.

### Seat select

- **`SEAT_SELECT`** — see [Right-seat mode](#right-seat-mode) below for
  the full behavior. Optional keys (all boolean, default `True` unless
  noted): `'select frankenusb left right swap'`, `'select layout'`
  (together with `'layout left right'`, default `(1, 2)`: the PSX
  `layout` values for left/right seat), `'select psx human pilot seat'`,
  `'select psxnetvatsim acp'`; `'seat'` (default `'TOGGLE'`; or fix it to
  `'LEFT'`/`'RIGHT'` for a button that always selects one specific seat).

## Long and short presses

For a given button number, you choose **either** the plain
`'button down'`/`'button up'` pair **or** the `'button long'`/`'button
short'` pair (they can coexist for *different* button numbers on the
same device, just not usefully for the same one).

- `'button down'` fires immediately on press, regardless of how long it's
  eventually held.
- On release, if the press was held longer than `--long-press-limit`
  (default 0.5s) and a `'button long'` entry exists for that button
  number, it fires. Otherwise, if a `'button short'` entry exists, it
  fires. If no matching long/short config exists, the plain `'button
  up'` entry (if any) fires instead.

```python
'button short': {
    2: {'button type': 'SET', 'psx variable': 'ApDisc', 'value': 1},
},
'button long': {
    2: {'button type': 'SET', 'psx variable': 'McpPshCmdL', 'value': 1},
},
```

(from `config_examples/frankenusb-longpress-example.conf`)

## Tiller mode

Many hardware setups don't have a dedicated nosewheel tiller. Tiller mode
lets a normal aileron (or any axis) temporarily drive PSX's `Tiller`
variable instead:

- Any `NORMAL` axis with `'tiller': True` is remapped to `Tiller`
  (-999..999) instead of its configured PSX variable while tiller mode is
  active.
- A `TILLER_TOGGLE` button turns it on/off (or forces a specific state via
  `'set'`, see above). Turning it on or off also centers both the
  aileron and the tiller.
- Automatically disabled once ground speed exceeds 40 kt.
- Always forced off on every PSX (re)connect, so a frankenusb restart
  mid-flight can never leave it stuck on.

Independently of all this, a **rudder-tiller interconnect** continuously
blends a small nosewheel command derived from the rudder pedals
(`0.1 × rudder`) into `Tiller`, picking whichever of hardware tiller input
or rudder-derived input has the larger magnitude. This runs regardless of
tiller mode and is on by default; disable it with
`--disable-rudder-tiller-interconnect`.

## Throttle and reverse axes

### Latch/unlatch and the sync sound

`THROTTLE_WITH_REVERSE_BUTTON` axes have "latch"/"unlatch" behavior: if
the physical lever is far from PSX's current throttle position (e.g.
after the autothrottle or another pilot moved it), moving the lever does
nothing until it gets close enough to PSX's value, at which point it
"latches" on (playing `THROTTLE_LATCH_SOUND` if configured) and takes
control. If something else then moves the PSX throttle away from where
you last set it, the lever "unlatches" (playing `THROTTLE_UNLATCH_SOUND`)
and goes back to being ignored until it catches up again.

For `AXIS_PSX_THROTTLE` (not `THROTTLE_WITH_REVERSE_BUTTON`), the related
`THROTTLE_SYNC_SOUND` plays instead whenever the autothrottle is active
and your physical lever position converts to a value close to PSX's
actual throttle — the intended workflow (per
`config_examples/frankenusb-tm-boeing-throttle.conf`) is: move the
physical throttle until you hear the sound, then disconnect the
autothrottle, for a handoff without a visible jump in PSX's throttle
position.

Whether the autothrottle currently has control is determined by reading
PSX's `Afds` variable (field 0): modes `BLANK` (0) and `HOLD` (21) count
as "not flying the levers"; anything else counts as active. By default,
axis input is ignored while the autothrottle is active (on
`AXIS_PSX_THROTTLE`/`REVERSE`, override with `'allow movement with at on':
True`).

### Flying the latch

Your USB throttle levers are not motorized. PSX's own throttles move on
their own -- driven by the autothrottle, or by another pilot in a shared
cockpit -- but your physical levers can't follow them; there's no way to
make a USB throttle quadrant physically track PSX's commanded position.
The latch/unlatch mechanism above exists entirely to work around that one
fact, and flying it well is mostly about building the right mental model
and habits, not about the mechanism itself.

**The real-aircraft analogy**: in the real jet, when you move your hand
from the yoke to the throttles, you're reaching for wherever they
*already are* -- your hand finds them, and you grab on. With FrankenUSB,
there's no "finding them where they are," because your lever doesn't
move itself. Instead, *you* move your hand and the lever together to
wherever PSX's throttle position currently is, and the latch sound is
what tells you "this is that position" -- it stands in for the real
aircraft's sense of touch. Only after you hear it are you actually
holding the throttle in a meaningful sense; before that, you're just
holding a disconnected lever.

A good technique follows directly from that analogy: treat the final
approach to the latch point as two separate motions. First, *gently* ease
the lever toward where you expect PSX's throttle to be -- this is the
"moving my hand to the throttle" step, and it should be a light, searching
movement, not a firm grip, precisely because you don't yet know you're in
the right place. Only once you hear the latch sound should you grab the
lever firmly and start actually flying it. Treating the whole motion as
one firm grab risks slamming the lever through the latch point and well
past it, exactly where a real lever would have stopped you by physically
being there.

Some situations worth knowing in advance:

- **Preflight**: before you need the throttles for anything, get latched
  deliberately rather than by accident -- ease your USB levers slightly
  until you hear the latch sound, so you know your levers are tracking
  reality before you need to rely on them.
- **After `TOGA`**: the autothrottle takes the levers and drives them
  forward itself. Push your USB throttles fully forward and leave them
  there -- you'll be latched (PSX's throttles are headed roughly the same
  way), and you're ready for whatever comes next without having to re-find
  the position later.
- **While the autothrottle is active** in any mode other than `HOLD`,
  FrankenUSB will not let your USB lever move PSX's throttle at all,
  latched or not -- this is a separate check, independent of latch state
  (see `autothrottle_active()` above). The autothrottle owns the levers in
  every active mode except `HOLD`.
- **In `HOLD` mode**, the autothrottle is technically still armed but
  isn't actively flying the levers, so FrankenUSB does allow you to move
  the throttles yourself -- but the latch rule still applies first. If you
  haven't been following the lever, you'll need to find the latch point
  again before your movement does anything.
- **Rejected takeoff**: if the throttles happen to already be in `HOLD`,
  you can just yank your USB levers closed -- `HOLD` allows lever control,
  so as long as you were latched (which you should be, having left them
  forward since `TOGA`), closing them closes PSX's throttles immediately.
  If they're *not* in `HOLD`, yanking the levers does nothing, regardless
  of latch state. Since you don't want to be checking which mode you're in
  during an RTO, the simplest, always-correct habit is to press the
  autothrottle disconnect first, then yank the levers closed -- that
  removes the autothrottle's claim on the levers unconditionally, so your
  lever movement is guaranteed to take effect no matter what mode you were
  in a moment ago.

## Flight phase procedures

`flight_phase_setup()` phases, triggered via
`ACTION_FLIGHT_PHASE_BUTTON`/`ACTION_FLIGHT_PHASE_TRIGGER`:

- **`RUNWAY_ENTRY`** — transponder to TA/RA, landing/strobe/wing/logo
  lights and runway turnoff lights on.
- **`CLEARED_TAKEOFF`** — outer landing lights on.
- **`AFTER_TAKEOFF`** — wing/logo/runway-turnoff/taxi lights off.
- **`NORMAL_FLIGHT`** — toggles the altimeter STD/QNH setting.
- **`EXITED_RUNWAY`** — strobe and outer landing lights off; taxi light
  on with inner landing lights following PSX's own taxi-light
  configuration; transponder to XPDR; speedbrake and flaps retracted;
  autobrake off; flight director and autothrottle both off; and starts
  the APU (toggling `ApuStart` through its start sequence with a short
  delay between steps).

An unrecognized phase name just logs a warning — it's not a config error.

## Right-seat mode

Toggled by a `SEAT_SELECT` button. When active (`self.right_seat`),
every PSX variable name listed in `frankenusb.py`'s `LEFT_RIGHT_CONTROLS`
table (LCP, ECP, MCP, RCP, clock, master warning, source selector and
display-brightness controls) is transparently redirected to its
right-seat (`Fo`) equivalent before being sent — so the same physical
button/knob config works from either seat without duplicating every
entry.

`SEAT_SELECT` can, in one press, also:

- Swap PSX's `layout` between the configured left/right values.
- Flip the PSX Human Pilot seat bit (`PnfMode`) without disturbing its
  other bits (callouts, silent tasks, step-climb flags).
- Select the matching seat on PSX.NET.VATSIM's ACP, if you use it.

Each of those three is individually optional (see the `SEAT_SELECT` keys
above), so you can use `SEAT_SELECT` purely for the frankenusb-side L/R
variable swap if that's all you need.

If a `SEAT_SELECT` button is configured with `'seat': 'TOGGLE'` (the
default), frankenusb also infers the real current seat from PSX's own
`layout` value every time a full state dump arrives (on connect and on
every reconnect) — so a frankenusb restart mid-flight doesn't leave it
out of sync with whichever seat PSX itself currently thinks is active.

## Example configs

The [config_examples](../config_examples) directory has real,
runnable-with-edits config files. None of them will work unmodified for
your own hardware (joystick names and button/axis numbers are specific to
each person's devices) — use `show_usb.py` to find your own numbers, then
adapt the relevant example.

- **`frankenusb-frankensim.conf`** — the author's own full sim config:
  MFG Crosswind rudder/toe-brakes, an S-TECS throttle quadrant
  (`THROTTLE_WITH_REVERSE_BUTTON`, `SPEEDBRAKE`, dozens of MCP/radio/light
  buttons, flight-phase triggers, `RADIO_SWITCH`), and a VKB Gladiator
  joystick (tiller, flight-control locks via long/short press, an
  `ADDON` button talking to `frankentanker.py`). The broadest
  general-purpose reference.
- **`frankenusb-reverselever.conf`** — minimal two-axis example of
  `AXIS_PSX_THROTTLE`/`AXIS_PSX_REVERSE` on a dedicated reverse-lever
  axis.
- **`frankenusb-tm-boeing-throttle.conf`** — a complete
  `THROTTLE_WITH_REVERSE_BUTTON` + `TMBOEING_ROTARY*` setup, with the
  clearest inline comments of any example file; good starting point for
  understanding both features together.
- **`frankenusb-bluestar.conf`** — `AXIS_PSX_THROTTLE`/`REVERSE`,
  `SPEEDBRAKE`, `AXIS_SET` flaps, and the two-button, three-position
  gear lever pattern (`GearLever` UP/neutral/DOWN from only two physical
  buttons).
- **`frankenusb-longpress-example.conf`** — minimal illustration of
  `'button long'`/`'button short'` alongside plain `'button down'`/`'button
  up'` on different buttons of the same device.
- **`frankenusb-modal-rotary-example.conf`** — a dedicated, heavily
  commented reference for `MODAL_ROTARY`/`MODAL_ROTARY_MODE_SET`/
  `MODAL_ROTARY_MODE_CYCLE`, including both ways to select a mode
  (one button per mode, or one button that cycles) and the
  semicolon-field `'increment index'`/`'fields'` mechanism.
- **`frankenusb-honeycomb-bravo.conf`** — a TCA Boeing yoke + Honeycomb
  Bravo throttle quadrant setup with four independent
  `THROTTLE_WITH_REVERSE_BUTTON` axes and tiller mode on the yoke.

## Help

Ask macroflight in the Aerowinx forum or on Discord (username mk3830).
