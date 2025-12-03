"""Over a certain altitude, use MSFS wind and temperature data for PSX."""
# pylint: disable=invalid-name,duplicate-code
import argparse
import asyncio
import ctypes
import logging
import signal
import time
import SimConnect  # pylint: disable=import-error
import psx  # pylint: disable=unused-import

__MY_CLIENT_ID__ = 'ICING'
__MY_DISPLAY_NAME__ = 'FrankenFreeze'


class FrankenFreezeException(Exception):
    """FrankenFreeze exception.

    For now, no special handling, this class just exists to make
    pylint happy. :)
    """


class FrankenFreeze():  # pylint: disable=too-many-instance-attributes
    """Improve PSX icing using MSFS data."""

    def __init__(self):
        """Initialize the class."""
        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
        )
        ctypes.windll.kernel32.SetDllDirectoryW(None)

        self.logger = logging.getLogger("frankenfreeze")
        self.args = {}
        # MSFS SimConncect object
        self.msfs_sc = None
        self.msfs_aq = None
        self.msfs_ae = None
        self.msfs_connected = False

        self.msfs_in_cloud = None
        self.focused_zone = 0  # default to global weather until updated
        self.hidden_cloud = None

        # Main PSX connection object
        self.psx = None
        self.psx_connected = False

        # Cache for unmodified PSX weather that we can restore when
        # MSFS no longer in cloud or we exit the script.
        self.psx_wx = {}
        # Timestamp of our last weather update to PSX. We don't cache
        # data that comes back within N seconds of pushing new data to
        # PSX, as it's likely to be an adjusted version of our own
        # fake data.
        self.psx_wx_last_push = 0

        # Current PSX altitude
        self.psx_altitude = 0
        # The altitude where we last updated the fake weather.
        self.psx_altitude_last_update = 9999999

        # How many octas of PSX cloud cover is considered "in cloud" (for the purposes of icing)?
        self.psx_in_cloud_limit = 1
        # the PSX altitude must be at least this far into a cloud layer to be "in cloud"
        self.psx_in_cloud_margin = 1000

        # Graceful shutdown
        self.logger.info("Setting up signal handler")
        signal.signal(signal.SIGINT, self.signal_handler)

    def restore_weather(self):
        """Exit cleanly by restoring PSX weather."""
        zonename = "WxBasic"
        if int(self.focused_zone) > 0:
            zonename = f"Wx{self.focused_zone}"
        self.logger.info("Resetting PSX weather in zone %s", zonename)
        if zonename in self.psx_wx:
            psx_weather_new = self.psx_wx[zonename]
            self.psx_send_and_set(zonename, psx_weather_new)
        else:
            self.logger.info("No weather to restore for %s", zonename)

    def signal_handler(self, signum, frame):  # pylint: disable=unused-argument
        """Handle the TERM signal by removing the dummt waypoint and shutting down."""
        self.restore_weather()
        raise SystemExit("Ctrl-C pressed")

    def _handle_args(self):
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog='frankenfreeze',
            formatter_class=argparse.ArgumentDefaultsHelpFormatter,
            description='Sync MSFS in-cloud status to PSX to improve icing')
        parser.add_argument('--debug',
                            action='store_true')
        parser.add_argument('--tweak-oat',
                            action='store_true',
                            help="Tweak PSX surface temp to make PSX OAT be close to MSFS OAT")
        parser.add_argument('--allowed.oat-sim-update', default=5.0, action='store', type=float,
                            help="How often (seconds) we will fetch data from MSFS.")
        parser.add_argument('--sim-update', default=5.0, action='store', type=float,
                            help="How often (seconds) we will fetch data from MSFS.")
        parser.add_argument('--psx-host', default='127.0.0.1', action='store', type=str,
                            help="The IP address of the PSX server.")
        parser.add_argument('--psx-port', default=10747, action='store', type=int,
                            help="The port number to connect to on the PSX server.")
        self.args = parser.parse_args()
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)

    def psx_send_and_set(self, psx_variable, new_psx_value):
        """Send variable to PSX and store in local db."""
        self.logger.debug("TO PSX: %s -> %s", psx_variable, new_psx_value)
        self.psx.send(psx_variable, new_psx_value)
        self.psx._set(psx_variable, new_psx_value)  # pylint: disable=protected-access

    def handle_piba_change(self, key, value):  # pylint: disable=unused-argument
        """Handle a PSX altitude change."""
        (_, _, _, altitude, _, _, _) = value.split(';')
        # To avoid too many updates, we store the altitude rounded to the nearest flight level
        new_altitude = 100 * round(float(altitude) / 100000)
        if self.psx_altitude != new_altitude:
            self.psx_altitude = new_altitude
            # If we have moved far enough from the altitude where we
            # last updated the weather, clear the weather and update again.
            if abs(self.psx_altitude - self.psx_altitude_last_update) > 1000:
                self.logger.info(
                    "PSX has moved >1000ft from altitude of last update" +
                    " (last: %s, now: %s), forcing update",
                    self.psx_altitude_last_update, self.psx_altitude)
                self.psx_altitude_last_update = self.psx_altitude
                self.restore_weather()
                self.merge_msfs_weather_into_focused_zone()

    def handle_wx_change(self, key, value):  # pylint: disable=unused-argument
        """Update as needed when when weather changes in PSX or MSFS."""
        elapsed = time.time() - self.psx_wx_last_push
        if elapsed < 5.0:
            self.logger.debug("Weather change by us: %s to %s", key, value)
        else:
            self.logger.debug("Weather change NOT by us, caching: %s to %s", key, value)
            self.psx_wx[key] = value
            self.logger.debug("Weather cache: %s", self.psx_wx)

    def handle_wx_focus_change(self, key, value):  # pylint: disable=unused-argument
        """Update as needed when when weather changes in PSX or MSFS."""
        # Note: basic zone is zone 0
        self.logger.debug("Weather zone focus change: %s is now %s", key, value)
        self.focused_zone = value
        self.merge_msfs_weather_into_focused_zone()

    def merge_msfs_weather_into_focused_zone(self):  # pylint: disable=too-many-branches,too-many-statements,too-many-locals
        """Get MSFS weather and merge into the focused zone."""
        if self.msfs_in_cloud == 1:
            self.logger.info(
                "Updating PSX weather in zone %s. MSFS is in cloud",
                self.focused_zone)
        else:
            self.logger.info(
                "Updating PSX weather in zone %s. MSFS is NOT in cloud",
                self.focused_zone)

        # Get the PSX weather for the zone
        zonename = "WxBasic"
        if int(self.focused_zone) > 0:
            zonename = f"Wx{self.focused_zone}"
        self.logger.debug("Name of PSX focussed zone is %s", zonename)
        try:
            psx_weather = self.psx.get(zonename)
        except:  # pylint: disable=bare-except
            self.logger.info("Failed to get PSX var %s", zonename)
        self.logger.debug("Current PSX wx: %s", psx_weather)
        if psx_weather is None:
            self.logger.warning("Current PSX is None")
            return
        data = psx_weather.split(";")
        hiCloudCov = int(data[0])
        hiCloudTop = int(data[1])
        hiCloudBase = int(data[2])
        loCloudCov = int(data[3])
        loCloudTop = int(data[4])
        loCloudBase = int(data[5])
        surfaceTemp = float(data[22])

        # Figure out if we need to do a temperature correction
        if self.args.tweak_oat:
            # Get MSFS and PSX OAT
            msfs_oat = None
            try:
                msfs_oat = float(self.msfs_aq.get('AMBIENT_TEMPERATURE'))
                msfs_qnh = float(self.msfs_aq.get('SEA_LEVEL_PRESSURE'))
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.logger.info("Failed to get MSFS OAT: %s", exc)
            psx_oat = None
            try:
                psx_MiscFltData = self.psx.get("MiscFltData")
                (_, psx_oat, _, _, _, _, _) = psx_MiscFltData.split(";")
                psx_oat = float(psx_oat) / 10.0
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self.logger.info("Failed to get PSX OAT: %s", exc)
            if msfs_oat is None or psx_oat is None:
                self.logger.warning("Failed to fetch OAT data, cannot adjust temperature")
            else:
                self.logger.info(
                    "MSFS QNH=%s, OAT=%.1f, PSX OAT=%.1f, PSX surface temp=%s",
                    msfs_qnh, msfs_oat, psx_oat, surfaceTemp)
                oat_change_needed = msfs_oat - psx_oat
                new_surface_temp = surfaceTemp + oat_change_needed
                if data[22] != str(round(new_surface_temp)):
                    self.logger.info(
                        "Adjusting PSX surface temperature from %s to %s",
                        data[22], str(round(new_surface_temp)))
                    data[22] = str(round(new_surface_temp))
                else:
                    self.logger.info("No PSX surface temp adjustment needed/possible")

        # Find out if PSX already is in cloud
        psx_in_cloud = False
        psx_in_dense_cloud = False
        # Check top layer
        if (hiCloudBase + self.psx_in_cloud_margin) <= self.psx_altitude <= (hiCloudTop - self.psx_in_cloud_margin):  # pylint: disable=line-too-long
            psx_in_cloud = "hi"
            if hiCloudCov >= self.psx_in_cloud_limit:
                self.logger.debug("PSX is in the hiCloud layer")
                psx_in_dense_cloud = True
        if (loCloudBase + self.psx_in_cloud_margin) <= self.psx_altitude <= (loCloudTop - self.psx_in_cloud_margin):  # pylint: disable=line-too-long
            psx_in_cloud = "low"
            if loCloudCov >= self.psx_in_cloud_limit:
                self.logger.debug("PSX is in the loCloud layer")
                psx_in_dense_cloud = True
        # Note: PSX should not be able to be in both layers, so we don't need to handle that.
        if self.msfs_in_cloud == 1:
            if psx_in_dense_cloud:
                # No action needed
                self.logger.info("MSFS and PSX both in cloud, no change needed")
                psx_weather_new = psx_weather
            else:
                # Figure out which cloud layer to change
                if not psx_in_cloud:
                    self.logger.info(
                        "MSFS in cloud, PSX not in any cloud layer, change hi layer alt+coverage")
                    if data[0] == "0":
                        self.logger.info("No PSX top layer, so create an 8/8 thin one")
                        data[1] = str(self.psx_altitude + self.psx_in_cloud_margin)
                        data[2] = str(self.psx_altitude - self.psx_in_cloud_margin)
                    elif self.psx_altitude > (hiCloudTop - self.psx_in_cloud_margin):
                        self.logger.info(
                            "PSX is above a top layer with >0/8, raise top and set 8/8")
                        data[1] = str(self.psx_altitude + self.psx_in_cloud_margin)
                    elif self.psx_altitude < (hiCloudBase + self.psx_in_cloud_margin):
                        self.logger.info(
                            "PSX is below a top layer with >0/8, lower base and set 8/8")
                        data[2] = str(self.psx_altitude - self.psx_in_cloud_margin)
                    data[0] = "8"  # hi coverage
                elif psx_in_cloud == 'hi':
                    self.logger.info(
                        "MSFS in cloud, PSX in hi cloud layer, change hi layer coverage")
                    data[0] = "8"  # hi coverage
                else:
                    self.logger.info(
                        "MSFS in cloud, PSX in lo cloud layer, change lo layer coverage")
                    data[3] = "8"  # lo coverage
                psx_weather_new = ";".join(data)
        else:
            if psx_in_dense_cloud:
                self.logger.info("MSFS not in cloud, PSX in cloud, remove PSX cloud coverage")
                if psx_in_cloud == 'hi':
                    data[0] = "0"  # hi coverage
                else:
                    data[3] = "0"  # lo coverage
                psx_weather_new = ";".join(data)
            else:
                self.logger.info(
                    "MSFS not in cloud, PSX not in cloud, restore original PSX weather")
                self.logger.debug("Weather cache: %s", self.psx_wx)
                if zonename in self.psx_wx:
                    psx_weather_new = self.psx_wx[zonename]
                else:
                    self.logger.warning("No cached Wx for %s", zonename)
                    return
        if psx_weather == psx_weather_new:
            self.logger.info("No change: PSX wx in %s: %s", zonename, psx_weather)
        else:
            self.logger.info("Current PSX wx in %s: %s", zonename, psx_weather)
            self.logger.info("New     PSX wx in %s: %s", zonename, psx_weather_new)
            self.psx_wx_last_push = time.time()
            self.psx_send_and_set(zonename, psx_weather_new)
        self.psx_altitude_last_update = self.psx_altitude

    async def setup_msfs_connection(self):
        """Connect to MSFS and setup connection details."""
        while True:
            try:
                self.msfs_sc = SimConnect.SimConnect()  # pylint: disable=undefined-variable
                self.msfs_aq = SimConnect.AircraftRequests(self.msfs_sc)
                self.msfs_ae = SimConnect.AircraftEvents(self.msfs_sc)
            except ConnectionError:
                self.logger.warning("MSFS not started, sleeping")
                await asyncio.sleep(1.0)
                continue
            else:
                break
        self.msfs_connected = True
        self.logger.info("SimConnect established connection to MSFS")

    async def setup_psx_connection(self):
        """Set up the PSX connection."""
        def setup():
            self.logger.info("Connected to PSX, setting up")
            self.psx_connected = True

        def teardown():
            self.logger.info("Disconnected from PSX, tearing down")
            self.psx_connected = False

        def connected(key, value):
            self.logger.info("Connected to PSX %s %s as #%s", key, value, self.psx.get('id'))
            self.psx_connected = True
            self.psx.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__}")

        self.psx = psx.Client()
        # self.psx.logger = self.logger.debug  # .info to see traffic

        self.psx.subscribe("id")
        self.psx.subscribe("version", connected)

        self.psx.subscribe("WxBasic")
        self.psx.subscribe("Wx1", self.handle_wx_change)
        self.psx.subscribe("Wx2", self.handle_wx_change)
        self.psx.subscribe("Wx3", self.handle_wx_change)
        self.psx.subscribe("Wx4", self.handle_wx_change)
        self.psx.subscribe("Wx5", self.handle_wx_change)
        self.psx.subscribe("Wx6", self.handle_wx_change)
        self.psx.subscribe("Wx7", self.handle_wx_change)
        self.psx.subscribe("WxMode1")
        self.psx.subscribe("WxMode2")
        self.psx.subscribe("WxMode3")
        self.psx.subscribe("WxMode4")
        self.psx.subscribe("WxMode5")
        self.psx.subscribe("WxMode6")
        self.psx.subscribe("WxMode7")
        self.psx.subscribe("FocussedWxZone", self.handle_wx_focus_change)
        self.psx.subscribe("PiBaHeAlTas", self.handle_piba_change)
        self.psx.subscribe("MiscFltData")

        self.psx.onResume = setup
        self.psx.onPause = teardown
        self.psx.onDisconnect = teardown

        await self.psx.connect(host=self.args.psx_host, port=self.args.psx_port)

    async def get_sim_data(self):  # pylint: disable=too-many-locals
        """Get PSX and MSFS data at requested frequency."""
        i = 0
        while True:
            if not self.psx_connected:
                self.logger.warning("PSX not connected, sleeping")
                await asyncio.sleep(1.0)
                continue
            if not self.msfs_connected:
                self.logger.warning("MSFS not connected, sleeping")
                await asyncio.sleep(1.0)
                continue
            i += 1
            if i > 1:
                await asyncio.sleep(self.args.sim_update)
            try:
                msfs_var = int(self.msfs_aq.get('AMBIENT_IN_CLOUD'))
            except TypeError as exc:
                self.logger.info("Got bad data from MSFS, continuing: %s", exc)
            # self.logger.debug("Fetched data from MSFS: AMBIENT_IN_CLOUD is %s", msfs_var)
            msfs_in_cloud_new = bool(msfs_var == 1)
            if msfs_in_cloud_new != self.msfs_in_cloud:
                self.logger.info(
                    "MSFS in-cloud state changed from %s to %s",
                    self.msfs_in_cloud, msfs_in_cloud_new)
                self.msfs_in_cloud = msfs_in_cloud_new
                self.merge_msfs_weather_into_focused_zone()

    async def main(self):
        """Start the script."""
        self._handle_args()
        await asyncio.gather(
            self.setup_psx_connection(),
            self.setup_msfs_connection(),
            self.get_sim_data(),
        )

    def run(self):
        """Start everything up."""
        asyncio.run(self.main())


if __name__ == '__main__':
    me = FrankenFreeze()
    me.run()
