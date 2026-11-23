# frankenstutter changelog

## Unreleased

- **New add-on: frankenstutter**, finds what causes stutters in MSFS 2024:
  every visible stutter (PresentMon) with where you were (SimConnect) and
  what the whole PC was doing (every process, CPU per core, DPC time, the
  real CPU clock, disk, network, GPU, VRAM), a guess at the cause (CPU, GPU,
  presentation, waiting), a read-only web page and JSON API, and a report by
  airport, flight phase and view. See docs/frankenstutter.md.
- Tested here with recorded frame data; the first live run on the MSFS PC
  (without PresentMon and psutil) logged SimConnect frames, the GPU and the
  position correctly.
