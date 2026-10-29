# Reviewer scenarios: design

Status: proposal (2026-10-27), nothing implemented yet.

Real 747 pilots fly short, scripted scenarios in their own PSX sim and
say how realistic frankenweather's weather was. Their answers become the
reference that frankenweather's turbulence (and later other weather) is
tuned against.

## Why

Overnight flight tests measure g, pitch, speed and altitude, and compare
them with the research (see
[turbulence-research](turbulence-research/README.md)). They can't say
whether it feels right to someone who has flown the aircraft through the
weather. Known gaps that only pilots can close:

- frankenweather's intensity labels run about one category too strong
  below 50% ([turbulence-calibration.md](turbulence-calibration.md)), and
  the label-to-g mapping is from literature, not from pilots;
- whether a SEV TURB SIGMET transit is "rough enough to pay attention,
  usually without severe" in practice;
- how real crews react: when they disconnect the autopilot, change from
  FLCH to V/S, slow down;
- what the sim hardware adds (seat shakers, motion platforms, MSFS cloud
  visuals).

## Overview

The whole user interface is in Flight Centre (the PSCC portal, repository
`../psccfc`). The pilot picks a scenario on a Flight Centre page, the sim
loads it, and questions appear on the page (phone or tablet) at the right
moments.

| Part | Where | Does |
|---|---|---|
| Scenario catalogue, start/abort, questions, results, aggregation | Flight Centre | all user interface |
| Conductor | frankenpush (new module it imports) | loads the situ, stabilises, freezes, runs the timeline, sends answers and results back |
| Scenario mode | frankenweather | frozen weather, seeded turbulence, synthetic areas, summaries |
| Scenario files | JSON, written by Claude, reviewed by the developer, uploaded to Flight Centre | what to fly and ask |

Frankenpush already keeps an outgoing WebSocket to Flight Centre
(`/ws/push`, no port forwarding needed) and already handles messages coming
back from the portal (`flight_deck_doorbell`, `crew_message`,
`psx_print`), so scenario messages use the same channel.

## Scenario files

One JSON file per scenario. A sketch:

```json
{
  "id": "sev-turb-sigmet-transit-01",
  "version": 1,
  "title": "SEV TURB SIGMET transit at FL350",
  "purpose": "Is a SEV TURB SIGMET transit rough enough to pay attention to, without being a roller coaster?",
  "requires": {"frankenweather": ">=1.28.0", "profile": "testing"},
  "aircraft": {
    "position": "N52 30.0 W030 00.0", "altitude_ft": 35000, "heading": 270,
    "speed": "M.84", "mcp_modes": ["HDG HOLD", "ALT HOLD", "SPD"], "zfw_t": 230
  },
  "weather": {
    "frozen": true,
    "zones_snapshot": "...",
    "wind_profile": {"FL350": "270/85", "FL300": "265/70"},
    "wind_corridor": "...",
    "sigmets": [{"hazard": "TURB", "qualifier": "SEV", "polygon": "...",
                 "base_ft": 30000, "top_ft": 39000}]
  },
  "turbulence": {"seed": 12345,
                 "event_mult": {"moderate": 1, "severe": 0, "extreme": 0}},
  "timeline": [
    {"t": 0, "action": "freeze_and_brief",
     "text": "You are 5 minutes from a SEV TURB SIGMET area (FL300-390). Fly as you would in line operations. Answer questions as they appear."},
    {"t": 0, "action": "unfreeze"},
    {"t": 420, "action": "question", "id": "q1", "type": "choice",
     "text": "Classify the last 2 minutes",
     "options": ["smooth", "light", "moderate", "severe", "extreme"]},
    {"t": 900, "action": "trigger", "event": "severe"},
    {"t": 960, "action": "question", "id": "q2", "type": "choice",
     "text": "Classify the jolt you just felt",
     "options": ["light", "moderate", "severe", "extreme"]},
    {"t": 1200, "action": "end"},
    {"t": 1200, "action": "question", "id": "q3", "type": "scale", "min": 1, "max": 5,
     "text": "How realistic was this transit compared with real SEV TURB SIGMET areas you have flown through?"},
    {"t": 1200, "action": "question", "id": "q4", "type": "text",
     "text": "What felt wrong, if anything?"}
  ],
  "record": ["g", "pitch", "autopilot_modes", "control_inputs", "fw_events"],
  "expected": {"q1": ["light", "moderate"], "q2": ["severe"]},
  "measured": {"note": "filled in from Claude's own test flight: g p95/max, pitch swing, event times"}
}
```

- **`purpose` and `expected`** tie each scenario to a claim being tested,
  so Flight Centre can show where pilots disagree with what was intended.
- **`version`**: answers to different versions aren't comparable, so an
  edit makes a new version instead of overwriting.
- **Positions** in degrees and decimal minutes (`N52 30.0 W030 00.0`).
- **`weather`** is a snapshot of a real moment (zones, winds, wind corridor,
  SIGMETs), taken from a live frankenweather state or a recorded test leg:
  real weather, frozen.
- **`measured`**: before a scenario goes to review, Claude flies it on the
  test sim, checks that it behaves as written (stable at the brief, events
  on time) and records the numbers, so the review covers the numbers as well
  as the text.
- Timeline actions: `freeze_and_brief`, `unfreeze`, `brief` (text without
  freezing), `question`, `trigger` (a turbulence event), `set` (a
  frankenweather setting), `jump` (reposition along the track, e.g. to skip
  to the interesting part), `end`.
- Question types: `choice`, `scale`, `text`, and `ab` (which of two runs
  felt more realistic, see below).

Workflow: Claude designs scenarios and writes the JSON files; the developer
reviews them and uploads them to Flight Centre. Later, scenarios may also
come from pilot reports on real flights (see below).

## Question design

Questions should map to something that can be tuned:

- **Classify the intensity** (smooth/light/moderate/severe/extreme, by the
  standard definitions): tests the label calibration directly.
- **Blind A/B**: the same encounter flown twice with two settings, in random
  order, without saying which is which; "which felt more realistic?".
  Comparisons are far more reliable than absolute ratings.
- **What the pilot did** is recorded rather than asked: autopilot
  disconnects, mode changes, control inputs, speed changes.
- **Specifics**: too frequent or too rare, too long or too short, too
  rhythmic, wrong axis (too much roll, too little vertical).
- **Free text** at the end.

Every session also records the pilot's setup (seat shaker, motion platform,
visuals, PSX/MSFS versions), asked once per account and confirmed per
session, so hardware effects can be separated from frankenweather's.

Scenarios should be 5 to 10 minutes. Pilots' time is scarce: several short
scenarios give more data per hour than one long transit.

## Frankenweather scenario mode

The largest piece, and needed for any reproducible test, including the
developer's own A/B tests (e.g. the same descent encounter in FLCH and in
V/S):

- **Frozen weather**: zones, winds, wind corridor and SIGMETs from the
  scenario, no downloads, no zone relocation surprises. Manual mode already
  does part of this for zones.
- **Seeded turbulence**: the random number generators for bursts and events
  seeded from the scenario, or a recorded burst sequence replayed (as
  `flighttest/fw_replay.py` does), so every pilot gets the same jolts at
  the same moments.
- **Synthetic areas**: a SIGMET, TS area or wave placed where the scenario
  says.
- **Controlled by addon messages**: frankenweather already accepts
  `FRANKENWEATHER:COMMAND`, `TURBCOMMAND` (settings, presets, event
  triggers), `MANUALWXCOMMAND` and `DIAG`; scenario mode adds a command to
  load a scenario's weather and seed, and one to leave scenario mode.
- **Summaries for the conductor**, as addon messages: the once-a-minute
  Flight line, event start/end, and at the end a session summary (g
  percentiles and peaks, events, version, profile). Better than reading log
  files: frankenweather may run on another PC than frankenpush, and
  summaries keep the WebSocket traffic small.

Frankenrouter currently sends frankenweather's own messages (`STATE`,
`TURBSTATE`, ...) only to frankenrouters and the WEATHER client, so
frankenpush doesn't see them. It needs either a router rule letting
frankenpush receive the scenario summaries, or a dedicated message type for
them.

## Conductor (in frankenpush)

A separate module that frankenpush imports, so frankenpush itself stays
small. Per scenario:

- check the requirements (frankenweather version and profile, sim type);
- ask for confirmation in the cockpit (printed message or CDU) before
  changing anything;
- load the situ by sending its variables (as `flighttest/sim.py`'s
  `load_situ` does), so no situ files need copying to the pilot's PC;
- put frankenweather in scenario mode, stabilise the aircraft, freeze motion
  and show the brief;
- run the timeline: unfreeze, triggers, questions (sent to Flight Centre,
  answers come back), jumps;
- record what the scenario asks for (summaries, not raw data: g
  percentiles, peaks, pitch swing, autopilot events, control input
  statistics);
- at the end, take frankenweather out of scenario mode and send the results.

Much of the stabilise/freeze/record logic exists in `flighttest/`.

## Flight Centre

- **Reviewer flag** on approved accounts, set by an admin, for verified 747
  pilots; their answers can be weighted or shown separately.
- **Scenario catalogue**: upload (admin), list, details, versions.
- **Session page**: start/abort, brief, questions as they come, works on a
  phone.
- **Results**: per scenario and version, per frankenweather version;
  answers against `expected`; setup breakdown. Tracking across frankenweather
  releases makes this a regression test made of pilot judgement.
- **Pilot reports on normal flights**: a "report turbulence" button during
  any flight, recording the pilot's classification against position and
  time. Frankenweather's Flight line (logged once a minute) shows what was
  being simulated then. That gives realism data from ordinary VATSIM
  flights, and an interesting report can become a scenario.

## Security and privacy

Today the portal can only ring the doorbell or print a message on a sim.
Scenarios would let it load situations and change the weather, so:

- scenarios are off unless enabled locally (e.g. `--allow-scenarios` on
  frankenpush);
- only the sim owner's own Flight Centre session can start one on their sim;
- a fixed list of commands (start, abort, answer), never arbitrary PSX
  variables;
- a confirmation in the cockpit before anything is loaded;
- results are stored per account but analysed by reviewer ID, not name;
  the pilot is told what is recorded and agrees to it.

Raw recordings (tens of MB per leg) are not uploaded; summaries only, raw
data only on request.

## Possible phases

- Frankenweather scenario mode (frozen weather, seeded turbulence,
  synthetic areas). Useful on its own for A/B flight tests.
- Scenario file format and a validator; the first scenarios: clean cruise
  (control), SEV TURB SIGMET transit, CAT jolt with warning, TS area edge,
  the same descent encounter in FLCH and V/S, mountain wave descent.
- Conductor module in frankenpush, first driven locally (command line)
  without Flight Centre.
- Flight Centre: catalogue, session page, results; reviewer flag.
- Pilot reports on normal flights.

## Open questions

- Can the push WebSocket carry the extra traffic (answers, summaries)
  without affecting position updates?
- How are shared-cockpit sims handled: which seat answers, and does the
  scenario load on the master only?
- Does freezing motion, then unfreezing, disturb the autopilot or the
  turbulence (e.g. a burst on the first tick)?
- How should answers from pilots with different hardware be combined?
