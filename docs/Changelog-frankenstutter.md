# frankenstutter changelog

## 0.2.0 (2026-11-24)

- **Fixed: false stutters when PresentMon's output came in bursts.** A
  second without PresentMon lines fell back to SimConnect's frames and
  measured the gap from a frame seconds or minutes old: "stutters" of 3 s
  to 9 min while MSFS ran a steady 30 fps (MACRO 2026-11-24). SimConnect's
  frames are now used only after 5 s without PresentMon's. The report
  leaves such false ones out of older sessions.
- **Pauses, menus and loads are no longer major stutters.** A picture held
  5 s or more (`--hold-ms`), anything while paused, and the first 10 s
  after either count as holds instead. The report leaves them out of older
  sessions as well.
- **New add-on: frankenstutter**, finds what causes stutters in MSFS 2024:
  every visible stutter (PresentMon) with where you were (SimConnect) and
  what the whole PC was doing (every process, CPU per core, DPC time, the
  real CPU clock, disk, network, GPU, VRAM), a guess at the cause (CPU, GPU,
  presentation, waiting), a read-only web page and JSON API, and a report by
  airport, flight phase and view. See docs/frankenstutter.md.
- Tested here with recorded frame data; the first live run on the MSFS PC
  (without PresentMon and psutil) logged SimConnect frames, the GPU and the
  position correctly.
