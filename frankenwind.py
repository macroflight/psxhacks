"""Over a certain altitude, use MSFS wind and temperature data for PSX."""
# pylint: disable=invalid-name,duplicate-code
import argparse
import asyncio
import logging
import math
import signal
import SimConnect  # pylint: disable=import-error
import psx  # pylint: disable=unused-import


class FrankenWindException(Exception):
    """FrankenWind exception.

    For now, no special handling, this class just exists to make
    pylint happy. :)
    """


class FrankenWind():  # pylint: disable=too-many-instance-attributes
    """Sync MSFS ambient conditions to PSX.

    S1200.0W17500.0
    Nddmm.?Wdddmm.?
    S      E

    N7705.4E02959.7

    """

    def __init__(self):
        """Initialize the class."""
        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
        )

        self.logger = logging.getLogger("frankenwind")
        self.args = {}
        # MSFS SimConncect object
        self.msfs_sc = None
        self.msfs_aq = None
        self.msfs_ae = None
        self.msfs_connected = False

        self.altimeter_mode = None
        self.saved_corridor = None
        self.saved_corridor_sel = None

        self.dummy_waypoint = 'MACRO'  # no such point in AIRAC 2410 :)

        self.msfs_vars = [
            "AMBIENT_TEMPERATURE",     # degrees C
            "AMBIENT_WIND_DIRECTION",  # degrees
            "AMBIENT_WIND_VELOCITY",   # knots
        ]

        # Main PSX connection object
        self.psx = None
        self.psx_connected = False

        # Graceful shutdown
        self.logger.info("Setting up signal handler")
        signal.signal(signal.SIGINT, self.sigint_handler)

    def sigint_handler(self, signum, frame):  # pylint: disable=unused-argument
        """Handle the TERM signal by removing the dummt waypoint and shutting down."""
        if self.psx_connected:
            self.psx_send_and_set("WxCorridorTxt", self.saved_corridor)
            self.psx_send_and_set("WxCorridorSel", "200")
        else:
            self.logger.info("Not connected to PSX")
        raise SystemExit("Ctrl-C pressed")

    def _handle_args(self):
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog='comparator',
            description='Compare MSFS and PSX data')
        parser.add_argument('--debug',
                            action='store_true')
        parser.add_argument('--sim-update', default=60.0, action='store', type=float,
                            help="How often (seconds) we will fetch data from MSFS.")
        parser.add_argument('--psx-host', default='127.0.0.1', action='store', type=str,
                            help="The IP address of the PSX server.")
        parser.add_argument('--psx-port', default=10747, action='store', type=int,
                            help="The port number to connect to on the PSX server.")

        self.args = parser.parse_args()
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)

    def dd2dms(self, decimaldegree, direction='x'):
        """Convert decimal degrees to the format used in Cirrus."""
        decimaldegree = float(decimaldegree)
        if decimaldegree < 0:
            decimaldegree = -decimaldegree
            if direction == 'x':
                appendix = 'W'
            else:
                appendix = 'S'
        else:
            if direction == 'x':
                appendix = 'E'
            else:
                appendix = 'N'
        degree_whole = int(math.floor(decimaldegree))
        decimalminutes = float((decimaldegree - degree_whole) * 60)
        # We need to handle the case when e.g 59.96 degrees is rounded up to 60.0 when printed.
        # The wind corridor parser (correctly) considers 60 minutes invalid.
        decimalminutes_rounded = round(decimalminutes, ndigits=1)
        if decimalminutes_rounded >= 60.0:
            self.logger.info("Rounding up fix")
            decimalminutes_rounded -= 60.0
            degree_whole += 1
        if appendix in ['N', 'S']:
            return f"{appendix}{degree_whole:02d}{decimalminutes_rounded:04.1f}"
        return f"{appendix}{degree_whole:03d}{decimalminutes_rounded:04.1f}"

    def psx_send_and_set(self, psx_variable, new_psx_value):
        """Send variable to PSX and store in local db."""
        self.logger.debug("TO PSX: %s -> %s", psx_variable, new_psx_value)
        self.psx.send(psx_variable, new_psx_value)
        self.psx._set(psx_variable, new_psx_value)  # pylint: disable=protected-access

    def handle_altimeter_change(self, key, value):  # pylint: disable=unused-argument
        """Update as needed when when altimeter changes.

        We only care about the STD/QNH mode.
        """
        altimeter_mode = value[0]
        if altimeter_mode != self.altimeter_mode:
            self.logger.info("Altimeter mode changed to %s", altimeter_mode)
            if altimeter_mode == 's':
                self.ensure_dummy_waypoint()
            else:
                self.ensure_dummy_waypoint_absent()
            self.altimeter_mode = altimeter_mode

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
        for varname in self.msfs_vars:
            var = self.msfs_aq.find(varname)
            var.time = 1000  # Max 1Hz
        self.msfs_connected = True
        self.logger.info("SimConnect established connection to MSFS")

    async def setup_psx_connection(self):
        """Set up the PSX connection."""
        def setup():
            self.logger.info("Connected to PSX, setting up")
            self.psx.send("demand", "GroundSpeed")
            self.psx_connected = True

        def teardown():
            self.logger.info("Disconnected from PSX, tearing down")
            self.psx_connected = False

        def connected(key, value):
            self.logger.info("Connected to PSX %s %s as #%s", key, value, self.psx.get('id'))
            self.psx_connected = True
            self.psx.send("name", "WIND:FRANKEN.PY frankenwind MSFS to PSX wind sync")

        self.psx = psx.Client()
        # self.psx.logger = self.logger.debug  # .info to see traffic

        self.psx.subscribe("id")
        self.psx.subscribe("version", connected)

        self.psx.subscribe("WxCorridorTxt")
        self.psx.subscribe("WxCorridorSel")
        self.psx.subscribe("PiBaHeAlTas")
        self.psx.subscribe("LeftPfdAlt", self.handle_altimeter_change)

        self.psx.onResume = setup
        self.psx.onPause = teardown
        self.psx.onDisconnect = teardown

        await self.psx.connect(host=self.args.psx_host, port=self.args.psx_port)

    def ensure_dummy_waypoint(self):  # pylint: disable=too-many-locals,too-many-statements
        """Add dummy waypoint if needed, or remove it."""
        if self.saved_corridor is None:
            self.saved_corridor = self.psx.get("WxCorridorTxt")
            self.saved_corridor_sel = self.psx.get("WxCorridorSel")
            self.logger.debug("Saved original PWX weather corridor data: %s", self.saved_corridor)

        # Get data from MSFS SimConnect API
        try:
            msfs_ambient_temperature = float(self.msfs_aq.get("AMBIENT_TEMPERATURE"))
            msfs_ambient_wind_direction = float(self.msfs_aq.get("AMBIENT_WIND_DIRECTION"))
            msfs_ambient_wind_velocity = float(self.msfs_aq.get("AMBIENT_WIND_VELOCITY"))
            self.logger.debug("MSFS ambient: T=%.1fC wind %.0f/%.0f",
                              msfs_ambient_temperature, msfs_ambient_wind_direction,
                              msfs_ambient_wind_velocity)
        except TypeError as exc:
            self.logger.info("Got bad data from MSFS, continuing: %s", exc)
            return
        # We should now have semi-valid data (at least it was converted to floats correctly)

        try:
            # Get PSX altitude and position
            piba = self.psx.get("PiBaHeAlTas")
            self.logger.debug("PiBaHeAlTas=%s", piba)
            (_, _, _, altitude, _, latitude, longitude) = piba.split(';')
            altitude_true_ft = float(altitude) / 1000
            latitude_r = float(latitude)  # radians
            longitude_r = float(longitude)  # radians
            latitude_d = math.degrees(latitude_r)
            longitude_d = math.degrees(longitude_r)
            longitude_dms = self.dd2dms(longitude_d, direction='x')
            latitude_dms = self.dd2dms(latitude_d, direction='y')
            self.logger.debug("PSX altitude: %.0f ft", altitude_true_ft)
            self.logger.debug("PSX position: longitude=%.2f latitude=%.2f",
                              longitude_d, latitude_d)
            self.logger.debug("PSX position DNS: longitude=%s latitude=%s",
                              longitude_dms, latitude_dms)
        except TypeError as exc:
            self.logger.info("Got bad data from PSX, continuing: %s", exc)
            return
        # Create the dummy waypoint entry
        psx_fl = int(altitude_true_ft / 100)
        fl1 = psx_fl - 40
        fl2 = psx_fl - 20
        fl3 = psx_fl + 20
        fl4 = psx_fl + 40
        temperature = self.ambient_float_to_text(msfs_ambient_temperature)
        wind = (f"{self.roundWindDirection(msfs_ambient_wind_direction):02.0f}" +
                f"{msfs_ambient_wind_velocity:03.0f}")
        coordinates = f"{latitude_dms}{longitude_dms}"
        # NOTE: if making the generated section longer, update self.dummy_section_length
        corridor_txt = ""
        corridor_txt += "^"
        corridor_txt += f"                                        {fl1:03.0f}   {fl2:03.0f}   {fl3:03.0f}   {fl4:03.0f}"       # pylint: disable=line-too-long
        corridor_txt += "^"
        corridor_txt += f"{coordinates} {self.dummy_waypoint} 042 017 {psx_fl:03d} {temperature} {wind} {wind} {wind} {wind}"  # pylint: disable=line-too-long
        corridor_txt += "^"
        corridor_txt_orig = self.psx.get("WxCorridorTxt")
        if corridor_txt != corridor_txt_orig:
            self.logger.debug(corridor_txt)
            self.logger.debug(corridor_txt_orig)
            self.logger.info(
                "Replacing weather corridor with MSFS ambient: T=%s wind %.0f/%.0f (%s) at %s",
                temperature, msfs_ambient_wind_direction,
                msfs_ambient_wind_velocity, wind, coordinates)
            self.logger.debug("New corridor: %s", corridor_txt)
            self.psx_send_and_set("WxCorridorTxt", corridor_txt)
            self.psx_send_and_set("WxCorridorSel", "200")

    def ensure_dummy_waypoint_absent(self):  # pylint: disable=too-many-locals,too-many-statements
        """Remove dummy waypoint if needed and replace with original corridor."""
        if self.saved_corridor is None:
            self.logger.debug("No saved corridor, assuming original corridor active")
            return
        # Replace the current weather corridor with the cached original one
        self.logger.info("Replacing weather corridor with original data")
        self.psx_send_and_set("WxCorridorTxt", self.saved_corridor)
        self.psx_send_and_set("WxCorridorSel", self.saved_corridor_sel)

    def roundWindDirection(self, direction):
        """Convert wind direction in degrees to tens of degrees.

        e.g 352 -> 35.
        """
        return round(direction / 10)

    def ambient_float_to_text(self, ambient):
        """Convert decimal ambient temperature.

        e.g -26 -> M26.
        """
        if ambient < 0.0:
            return "M" + f"{int(-ambient):02d}"
        return "P" + f"{int(ambient):02d}"

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
                # Get altimeter mode. We start adding our dummy waypoint when
                # altimeter switched to STD.
                altimeter_mode = self.psx.get("LeftPfdAlt")[0]
                self.logger.debug("Altimeter mode: %s", altimeter_mode)
                if altimeter_mode == 's':
                    self.ensure_dummy_waypoint()
                else:
                    self.ensure_dummy_waypoint_absent()
                self.altimeter_mode = altimeter_mode
            except TypeError as exc:
                self.logger.info("Got bad data from PSX, continuing: %s", exc)

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
    me = FrankenWind()
    me.run()
