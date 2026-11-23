# frankenstutter

frankenstutter finds out what causes stutters in Microsoft Flight Simulator
2024. It runs on the MSFS PC and logs every visible stutter together with
where you were and what the whole PC was doing at that moment, guesses the
cause, and makes a report: which airports, flight phases and views stutter,
and what goes with it (the CPU, the GPU, disk streaming, downloads, VRAM,
a driver, another program).

It changes nothing in MSFS or in PSX: it only watches.

## What it uses

- **PresentMon** (Intel, free): the time of every frame MSFS makes and of
  every change of the picture on screen, and per frame how long the CPU and
  the GPU were busy. Download the console version
  (`PresentMon-2.x.x-x64.exe`, not the installer) from
  <https://github.com/GameTechDev/PresentMon/releases> and put it in
  `%USERPROFILE%\.cache\frankenstutter\` (or give `--presentmon PATH`).
- **SimConnect**: position, altitude, speed, on the ground, camera view,
  sim rate and pause. It finds `SimConnect.dll` in the same folder or in
  `C:\MSFS 2024 SDK` (or `--dll PATH`).
- **The whole PC** (psutil, part of psxhacks' Python): CPU per core, Windows
  DPC and interrupt time, the CPU's real clock (so a power limit holding it
  back shows), memory and page file, disk and network, and every process's
  CPU, memory, disk reads and writes and threads.
- **The GPU** (nvidia-smi, comes with the NVIDIA driver): load, VRAM,
  clock, power, temperature and throttle reasons.

### PresentMon permission

PresentMon needs to be allowed to trace: either run frankenstutter as
administrator, or (better, once) add your Windows user to the
**Performance Log Users** group in an administrator Command Prompt, then
sign out and in:

```text
net localgroup "Performance Log Users" %USERNAME% /add
```

Without PresentMon (not found or not allowed) frankenstutter uses
SimConnect's frame events instead: it still sees the longer stutters, but
not what reaches the screen and not whether the CPU or the GPU was busy.

## Starting it

From the start scripts on the MSFS sim (slave sim, or a sim without a
router): `$StartFrankenstutter = $true` in your override file (see the
FrankenStutter section of `psxhacks-start-override-EXAMPLE.ps1`). Or by
hand:

```powershell
python frankenstutter.py
```

Leave it running for the whole flight. When you notice a stutter, type a
word or two in its window and press Enter (or just Enter): a marker with
the time. Every 10 seconds it prints the frame rate, the 99th percentile
frame time and the stutters of the last minute. Ctrl+C stops it.

| Option | Meaning |
|---|---|
| `--presentmon PATH` | the PresentMon console exe |
| `--no-presentmon` | SimConnect frames only |
| `--dll PATH` | SimConnect.dll |
| `--small-ms 30`, `--medium-ms 60`, `--major-ms 150` | the stutter sizes |
| `--factor 1.75` | a stutter is also this many times the recent typical frame |
| `--out DIR` | where the session folders go |
| `--web-port 9787`, `--web-bind 0.0.0.0` | the web page and API (`--web-port 0`: off) |
| `--report FOLDER ...` | write the report for these sessions and exit |

## What is a stutter

A picture held on screen for at least 30 ms (one missed refresh at 60 Hz)
and at least 1.75 times the recent median: **small** 30-60 ms, **medium**
60-150 ms, **major** 150 ms and more. With frame generation on,
frankenstutter watches what reaches the screen.

For each stutter it looks at the slowest frame MSFS made just before:

- **CPU**: the CPU was busy most of the frame: the sim's own work (the main
  thread, loading scenery, add-ons).
- **GPU**: the GPU was busy most of the frame: rendering (settings, VRAM).
- **presentation**: the frames were made in time but shown late (frame
  generation, VSync pacing, Windows' compositor).
- **waiting**: neither was busy (waiting for the disk, a lock, a stalled
  thread).

## The web page and API

A read-only page on port 9787: frame rate, stutters, CPU per core, DPC time,
the CPU clock, MSFS, GPU and VRAM, network, the busiest processes and the
last stutters, refreshed every 2 seconds. Nothing can be changed through
it. Windows Firewall needs an inbound rule for it to be reached from
another PC:

```text
netsh advfirewall firewall add rule name="frankenstutter" dir=in action=allow protocol=TCP localport=9787
```

JSON: `/api/status`, `/api/seconds?n=300` (the last hour is kept),
`/api/stutters?n=100`, `/api/processes?n=30`, `/api/session`,
`/api/markers`.

## The logs and the report

Each run writes a folder `%USERPROFILE%\.cache\frankenstutter\sessions\<UTC start>\`:

- `session.json`: CPU, RAM, GPU and driver, power plan, hardware GPU
  scheduling, Game Mode, disks, the options.
- `UserCfg.opt`: a copy of MSFS's graphics and data settings at the start.
- `seconds.csv`: one row per second (frame statistics, position, altitude,
  speed, camera, nearest airport, the PC, MSFS and the GPU).
- `stutters.jsonl`: one line per stutter with its context.
- `processes.csv`: the busiest processes, every second.
- `markers.csv`: your notes.

`python frankenstutter.py --report FOLDER [FOLDER ...]` prints a report and
writes `report.md` into the first folder: stutters per minute by place
(airport or enroute), flight phase and camera view, the likely causes, how
much more often disk reads, downloads, page faults, other programs, DPC
time, a fully busy core or a full VRAM came just before stutters than at
other times, the other programs busiest then, the 20 worst stutters and the
markers. The report also runs on Linux.
