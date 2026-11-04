"""Drifting CBs for PSX.NET.Orchestration: the WxMode that puts a zone's CB where it should be.

PSX.NET.Orchestration draws PSX's CBs in MSFS from the weather zones it
gets. PSX always draws a zone's CB 7 nm (CB_OFFSET_NM) from the zone
centre, on the bearing (sim minutes x 6 + sim seconds x 0.1 + zone
elevation in ft + 2000) % 360 (Hardy's formula, from TimeEarth), so a CB
meant to stay put or drift with the wind needs its zone's elevation and
centre changed all the time. frankenweather does that for PSX every 15 s
(more often froze PSX's weather radar), and describes each drifting CB in
its FRANKENWEATHER:CBFEED messages; with these functions every router
works out, several times a second, the WxMode its Orchestration clients
get. frankenweather's fw_drift.DriftCell.mode_for() is the same
calculation (on the ellipsoid; flat here, a few metres apart over 7 nm).
"""
import math
import unittest
from datetime import datetime, timezone

CB_OFFSET_NM = 7.0


def time_term_deg(time_earth_ms):
    """Return PSX's CB bearing for a zone at elevation 0 (degrees) at TimeEarth time_earth_ms."""
    t = datetime.fromtimestamp(time_earth_ms / 1000.0, tz=timezone.utc)
    return (t.minute * 6.0 + (t.second + t.microsecond / 1e6) * 0.1 + 2000.0) % 360.0


def offset(lat, lon, bearing_deg, dist_nm):
    """Return (lat, lon) dist_nm from (lat, lon) on bearing_deg (flat earth)."""
    rad = math.radians(bearing_deg)
    lat2 = lat + dist_nm * math.cos(rad) / 60.0
    lon2 = lon + dist_nm * math.sin(rad) / (60.0 * math.cos(math.radians(lat)))
    return lat2, ((lon2 + 180.0) % 360.0) - 180.0


def cb_position(lat, lon, elev_ft, time_earth_ms):
    """Return (lat, lon) of the CB PSX draws for a zone centred at (lat, lon) at elev_ft."""
    return offset(lat, lon, (time_term_deg(time_earth_ms) + elev_ft) % 360.0, CB_OFFSET_NM)


def wxmode_for(zone, time_earth_ms, elapsed_s):
    """Return the WxMode that puts zone's CB where it is elapsed_s after zone's time (or before).

    zone is one FRANKENWEATHER:CBFEED zone: "lat", "lon" (the CB at the
    message's time), "hdg" and "kt" (its drift), "bearing" (the CB's
    bearing from the zone centre, held), "elev0" (the zone's own
    elevation, ft) and "mode" (its WxMode string, of which only the
    position and elevation change). time_earth_ms is the PSX clock the
    client will draw it with.
    """
    # elapsed_s may be negative (the time shown before the message's): the CB
    # was back along the same straight line then
    lat, lon = offset(zone['lat'], zone['lon'], zone['hdg'], zone['kt'] * elapsed_s / 3600.0)
    time_term = time_term_deg(time_earth_ms)
    # The elevation (0-359 ft above the zone's own) that keeps the bearing
    raise_ft = round((zone['bearing'] - time_term - zone['elev0']) % 360.0) % 360
    elev_ft = round(zone['elev0']) + raise_ft
    centre = offset(lat, lon, (time_term + elev_ft + 180.0) % 360.0, CB_OFFSET_NM)
    parts = zone['mode'].split(';')
    parts[0] = f"{math.radians(centre[0]):.8f}"
    parts[1] = f"{math.radians(centre[1]):.8f}"
    parts[3] = str(int(elev_ft))
    return ';'.join(parts)


class TestOrchestration(unittest.TestCase):
    """The WxMode puts PSX's CB where the zone says, at any time."""

    ZONE = {'lat': 47.3, 'lon': -25.0, 'hdg': 90.0, 'kt': 40.0, 'bearing': 123.0,
            'elev0': 0.0, 'mode': "0.8;-0.4;320;0;11;X12301p"}

    def test_cb_where_it_should_be(self):
        """For an hour, the CB drawn from the WxMode is on the drift line within 30 m."""
        t0 = 1797000000000
        for k in range(0, 3600, 37):
            mode = wxmode_for(self.ZONE, t0 + k * 1000, k).split(';')
            lat, lon = math.degrees(float(mode[0])), math.degrees(float(mode[1]))
            cb = cb_position(lat, lon, float(mode[3]), t0 + k * 1000)
            want = offset(self.ZONE['lat'], self.ZONE['lon'], 90.0, 40.0 * k / 3600.0)
            err_nm = math.hypot((cb[0] - want[0]) * 60.0,
                                (cb[1] - want[1]) * 60.0 * math.cos(math.radians(cb[0])))
            self.assertLess(err_nm * 1852.0, 30.0, k)
            self.assertTrue(0 <= int(mode[3]) < 360, mode[3])
            self.assertEqual(mode[5], "X12301p")

    def test_elevation_kept_above_the_zones_own(self):
        """A zone at 7,620 ft gets 7,620-7,979 ft."""
        zone = dict(self.ZONE, elev0=7620.0)
        elevs = {int(wxmode_for(zone, 1797000000000 + k * 1000, k).split(';')[3])
                 for k in range(0, 3600, 30)}
        self.assertTrue(all(7620 <= e < 7980 for e in elevs), elevs)
