"""Run frankenweather with its turbulence engine replaying a past day's weather (testing only).

Usage: python3 -m flighttest.fw_replay --replay-file FILE [frankenweather options]

FILE holds "<ISO time> <epoch it was set>": the turbulence engine runs
at that time, advancing in real time since it was set, so a plan of test
legs can move it per leg (flighttest/legs.py, replay_time). frankenweather
itself is unchanged; patched at runtime:

- its wind and CAPE data (frankenturb HourlyForecastCache) come from
  Open-Meteo's historical-forecast API (a separate quota), cached next
  to FILE;
- the turbulence engine's and CAPE fetcher's clock is the replay time.

Everything else (zone weather, METARs, SIGMETs) stays live.
"""
import argparse
import os
import runpy
import sys
import time
from datetime import datetime, timedelta

import requests

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

# pylint: disable=wrong-import-position
import frankenturb                      # noqa: E402
from frankenturb import cape            # noqa: E402
import frankenturb.openmeteo as om      # noqa: E402

HIST_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
_REPLAY_FILE = ""


def replay_now() -> datetime:
    """Return the replay time now."""
    with open(_REPLAY_FILE, encoding="utf-8") as replay:
        iso, set_at = replay.read().split()
    return datetime.fromisoformat(iso) + timedelta(seconds=time.time() - float(set_at))


class HistClient(om.OpenMeteoClient):
    """Open-Meteo client for the historical-forecast API, without a usage file."""

    def __init__(self):
        """Create the client."""
        super().__init__(usage_path=None)

    def get(self, params, kind, proxy=None):
        """Fetch the days around the replay time instead of the forecast."""
        when = replay_now()
        query = {k: v for k, v in params.items() if k != "forecast_days"}
        query["start_date"] = (when - timedelta(days=1)).strftime("%Y-%m-%d")
        query["end_date"] = (when + timedelta(days=1)).strftime("%Y-%m-%d")
        try:
            response = requests.get(HIST_URL, params=query, timeout=30,
                                    proxies={"https": proxy} if proxy else None)
        except requests.RequestException as exc:
            print(f"REPLAY: {kind} historical fetch failed: {exc}", flush=True)
            return None
        if response.status_code != 200:
            print(f"REPLAY: {kind} historical fetch HTTP {response.status_code}: "
                  f"{response.text[:200]}", flush=True)
            return None
        return response.json()


def _patch() -> None:
    cache_init = om.HourlyForecastCache.__init__
    compute = frankenturb.TurbulenceEngine.compute
    cape_get = cape.CapeFetcher.get

    def _cache_init(self, kind, hourly, _path, *args, **kwargs):
        cache_dir = os.path.dirname(os.path.abspath(_REPLAY_FILE))
        cache_init(self, kind, hourly, os.path.join(cache_dir, f"replay_{kind}.json"),
                   *args, **kwargs)
        self._client = HistClient()  # pylint: disable=protected-access

    def _compute(self, lat, lon, alt_ft, sim_time_utc=None):
        return compute(self, lat, lon, alt_ft, sim_time_utc or replay_now())

    def _cape_get(self, lat, lon, sim_time_utc=None):
        return cape_get(self, lat, lon, sim_time_utc or replay_now())

    om.HourlyForecastCache.__init__ = _cache_init
    frankenturb.TurbulenceEngine.compute = _compute
    cape.CapeFetcher.get = _cape_get


def main() -> None:
    """Patch frankenturb and run frankenweather with the remaining arguments."""
    global _REPLAY_FILE  # pylint: disable=global-statement
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--replay-file", required=True,
                        help='"<ISO time> <epoch it was set>", e.g. written by a test plan')
    args, rest = parser.parse_known_args()
    _REPLAY_FILE = os.path.expanduser(args.replay_file)
    _patch()
    print(f"REPLAY: turbulence engine at {replay_now().isoformat()}", flush=True)
    sys.argv = ["frankenweather.py"] + rest
    os.chdir(REPO)
    runpy.run_path(os.path.join(REPO, "frankenweather.py"), run_name="__main__")


if __name__ == "__main__":
    main()
