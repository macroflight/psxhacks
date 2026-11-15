# Turbulence research

Background for frankenweather's turbulence: what real airliner turbulence
looks like, and how the addon's turbulence measured against it.

- [realism-targets.md](realism-targets.md): research report (2026-10-26) on
  real-world turbulence (g per category, 747 scaling, encounter durations,
  patchiness, frequency per flight hour, SIGMET/forecast areas, mountain
  wave, convective and low-level turbulence, incidents such as SQ321 and
  UA826) and the targets recommended for frankenweather.
- [notes/](notes/): the four research notes behind the report, with sources:
  - [g-levels-response-incidents.md](notes/g-levels-response-incidents.md)
  - [temporal-structure.md](notes/temporal-structure.md)
  - [frequency-and-forecasts.md](notes/frequency-and-forecasts.md)
  - [wave-convective-lowlevel.md](notes/wave-convective-lowlevel.md)
- [night-campaign-2026-10-26.md](night-campaign-2026-10-26.md): overnight
  flight-test campaign of frankenweather 1.27.0 (71 legs, stable vs testing
  profile), findings and tables;
  [night-campaign-2026-10-26-notes.md](night-campaign-2026-10-26-notes.md)
  are the running notes from that night.
- [event-shape-calibration.md](event-shape-calibration.md): how PSX answers
  big scripted up/down bursts (ringing, adding up), the shapes of
  frankenweather's turbulence events and how they flew (2026-10-26).
- [../turbulence-calibration.md](../turbulence-calibration.md): what a single
  WxBurst and frankenweather's intensity (%) do to the PSX 747 (2026-10-25).

Design goal (from the developer): realistic by default, not a roller coaster.
A SEV TURB SIGMET transit should make the crew pay attention, but usually
without severe or extreme turbulence, and never smooth. Severe and extreme
events stay possible at real-world odds, and the aircraft's response to them
is realistic, even if that means the autopilot can't cope. A setting can make
moderate, severe and extreme events more likely for training or entertainment.
