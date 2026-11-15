# frankenweather CB messages (`FRANKENWEATHER:CBS`)

frankenweather puts thunderstorms (CBs) into PSX's weather zones. PSX shows
them on the weather radar, and an add-on that draws PSX's weather in another
simulator (PSX.NET.Orchestration in MSFS) can get these CBs from the zone
variables. That gives one fixed-size CB per zone with no anvil and no motion.

The **CBS message** tells such an add-on everything frankenweather knows
about each CB: where it is now, its base and tops, how it drifts, whether
it has an anvil and which way the anvil points, and the surface wind.
This page is the contract between frankenweather and the add-ons that read it.

## The message

One line on the PSX network, sent by frankenweather (through the routers):

```text
addon=FRANKENWEATHER:CBS:<frankenweather instance uuid>:<json>
```

The JSON is plain (no compression), about 350 bytes per CB, and lists **every**
CB frankenweather has in PSX at that moment. A CB missing from a message is gone.

```json
{
  "v": 1,
  "time": "2026-11-15T07:30:00Z",
  "tropopause_ft": 36500,
  "cbs": [
    {
      "id": "Z3-1794727800",
      "zone": 3,
      "lat": 48.46712, "lon": 1.51703,
      "oktas": 6,
      "base_ft_msl": 2500,
      "top_ft_msl_fw": 47000,
      "top_ft_msl_psx": 44999,
      "radar_core_nm": 6.0, "radar_amber_nm": 18.0,
      "drift": {"hdg_true": 245, "kt": 32,
                "since": "2026-11-15T07:05:00Z", "until": "2026-11-15T07:48:00Z"},
      "anvil": {"visible": true, "hdg_true": 260, "length_nm": 40},
      "surface_wind": {"dir_true": 230, "kt": 14, "gust_kt": 25},
      "source": "radar",
      "msfs_only": false
    }
  ]
}
```

### Top level

| Field | Meaning |
|---|---|
| `v` | Message version, now 1. See [Changes](#changes). |
| `time` | When the positions are valid: PSX's own clock (UTC, TimeEarth), which may differ from real time. |
| `tropopause_ft` | The tropopause at the aircraft (ft MSL) from the forecast, or `null` if there's no forecast. |
| `cbs` | Every CB, in zone order; can be empty. |

### Each CB

| Field | Meaning |
|---|---|
| `id` | Stays the same while the CB lives. A new id means a new CB, even in the same zone. The id is the zone and when the CB was first seen; don't read meaning into it. |
| `zone` | PSX weather zone (1-7) that holds the CB. |
| `lat`, `lon` | The **CB** now (degrees). This is not the zone centre: PSX draws a zone's CB 7 nm from the centre, so use this position. |
| `oktas` | Coverage 1-8. It ramps up when a CB grows and down when it fades. |
| `base_ft_msl` | Cloud base, ft above sea level. |
| `top_ft_msl_fw` | The top frankenweather's data says. It can be higher than PSX's limit, for example a tropical CB at 52,000 ft. |
| `top_ft_msl_psx` | The top PSX got. PSX allows at most 44,999 ft above the zone's elevation. |
| `radar_core_nm` | How wide PSX paints this CB's red core (magenta included) on its weather radar: diameter, nm. |
| `radar_amber_nm` | Diameter of amber plus red on PSX's radar (nm). |
| `drift` | How the CB moves: `hdg_true` (towards, degrees true), `kt`, and `since`/`until` (PSX clock) for when the drift started and when the CB starts to decay (`until` is `null` if it has no end). `null` if the CB doesn't move. |
| `anvil` | `visible` (always true when present), `hdg_true` (the way it points, downwind) and `length_nm`. `null` without an anvil. |
| `surface_wind` | The zone's surface wind: `dir_true` (from, degrees true), `kt`, `gust_kt` (`null` without gusts). For an airport zone this is the airport's METAR wind. `null` if unknown. |
| `source` | What made frankenweather put the CB there: `radar`, `lightning`, `model` (forecast), `metar` (a thunderstorm in a METAR), `sigmet` (a TS SIGMET) or `test` (placed by hand on frankenweather's CB test page). |
| `msfs_only` | Reserved for CBs only another simulator should draw (they'll have no PSX zone). Always `false` in version 1. |

All directions are **true** (not magnetic) and all heights are feet above
sea level.

## When it's sent

- Soon after anything other than the positions changes (at most every 15 s): a CB comes or goes, or its oktas, drift, anvil or source change, or its base or top moves by about 2,000 ft or more. Between messages a growing CB's top may have risen a little; the next message has it.
- Otherwise every 60 seconds.
- Between messages, run each CB on along its drift from `time`. A CB without a drift stays put. PSX's own zone CB circles slowly round the zone centre (one turn an hour), and `lat`/`lon` follows that circle.
- Not sent while PSX itself controls the weather (frankenweather's weather off).

## How it gets to you

frankenrouter passes CBS messages only to the other frankenrouters and to
clients named in its `[filtering] orchestration_names` setting (by default
`PSX.NET.Orchestration`). Other clients don't get them. Every router in a
shared cockpit passes them on, so each sim's Orchestration gets the same
CBs. A router older than this feature doesn't pass them to Orchestration.

The routers delay the weather-zone variables they give Orchestration by
frankenweather's Orchestration delay, to match how late PSX's radar shows a
change. The CBS message is **not** delayed: its positions are for `time`.

## How frankenweather works the fields out

- **Anvil**: an anvil forms where the updraft reaches the tropopause and
  spreads downwind. It is shown when:
  - the CB's top is within 3,000 ft of the tropopause, or above it (the
    forecast tropopause at the CB);
  - and the CB has at least 6 oktas, or came from radar or lightning.

  It points the way the wind at the CB's top blows, and its length is
  10 nm + 0.5 × that wind (kt), at most 80 nm.
- **Top above PSX's limit**: when PSX's limit cut the top, `top_ft_msl_fw` is the
  tropopause (forecast at the CB, or the climate value for the latitude and
  month if higher) plus 2,000 ft.
- **Drift**: frankenweather's advanced zones (on by default, off with
  `--no-advanced-zones`) move enroute CBs with the 850-300 hPa mean wind,
  5 degrees to the right. With `--radar-cbs` a CB on a radar storm cell
  moves with the cell's own motion, tracked from frame to frame. Without
  advanced zones, `drift` is always `null`, as it is near the departure and
  destination airports and below 10,000 ft.
- **Radar size**: what PSX actually paints, measured on PSX's radar
  (2026-11-15) and interpolated per CB from its oktas and depth (top
  minus base). A big or deep CB (3 oktas or more and 22,000 ft or more
  deep, for instance) is about 6 nm red and 18.6 nm amber+red; a small or
  shallow one less: 1 okta 32,000 ft deep 3.2 / 9.9 nm, 2 oktas 12,000 ft
  deep 1.9 / 5.7 nm, 1 okta 8,000 ft deep barely a dot. The zone's size
  and nearby zones make no difference.

## Changes

New fields may be added to version 1 at any time; ignore fields you don't
know. Removing or renaming a field, or changing what one means, makes a new
version (`v`: 2), announced here first.
