"""Compare PSX and MSFS position data."""
# pylint: disable=invalid-name,duplicate-code
import argparse
import asyncio
import logging
import math
import pprint
import time
import winsound  # pylint: disable=import-error
import SimConnect  # pylint: disable=import-error
import psx  # pylint: disable=unused-import


class ComparatorException(Exception):
    """Comparator exception.

    For now, no special handling, this class just exists to make
    pylint happy. :)
    """


class Comparator():  # pylint: disable=too-many-instance-attributes
    """Replaces the PSX USB subsystem."""

    def __init__(self):
        """Initialize the class."""
        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
        )
        self.logger = logging.getLogger("frankenusb")
        self.args = {}
        # MSFS SimConncect object
        self.msfs_sc = None
        self.msfs_aq = None
        self.msfs_ae = None
        self.msfs_connected = False

        self.msfs_vars = [
            "PLANE_PITCH_DEGREES",  # NOTE: actually radians
            "PLANE_BANK_DEGREES",  # NOTE: actually radians
            "PLANE_ALTITUDE",  # ft
            "PLANE_HEADING_DEGREES_TRUE",  # NOTE: actually radians
            "PLANE_LATITUDE",  # radians
            "PLANE_LONGITUDE",  # radians
        ]

        # Main PSX connection object
        self.psx = None
        self.psx_connected = False
        self.data = {
            'psx': {
                'pitch_r': None,
                'bank_r': None,
                'heading_true_r': None,
                'latitude_r': None,
                'longitude_r': None,
                'altitude_true_ft': None,
                'groundspeed_kt': None,
                'updated': None,
            },
            'msfs': {
                'pitch_r': None,
                'bank_r': None,
                'heading_true_r': None,
                'latitude_r': None,
                'longitude_r': None,
                'altitude_true_ft': None,
                'groundspeed_kt': None,
                'camera_pitch_r': None,
                'camera_yaw_r': None,
                'updated': None,
            },
        }

    def _handle_args(self):
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog='comparator',
            description='Compare MSFS and PSX data')
        parser.add_argument('--debug',
                            action='store_true')
        parser.add_argument('--quiet',
                            action='store_true')
        parser.add_argument('--sim-update-hz', default=2.0, action='store',
                            help="How often we will fetch data from PSX and MSFS.")
        parser.add_argument('--output-hz', default=1.0, action='store',
                            help="How often we will compare and output data.")

        self.args = parser.parse_args()
        if self.args.quiet:
            self.logger.setLevel(logging.CRITICAL)
        elif self.args.debug:
            self.logger.setLevel(logging.DEBUG)

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
            var.time = 1000 / self.args.sim_update_hz
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

        self.psx = psx.Client()
        # self.psx.logger = self.logger.debug  # .info to see traffic

        self.psx.subscribe("id")
        self.psx.subscribe("version", connected)

        self.psx.subscribe("PiBaHeAlTas")
        self.psx.subscribe("GroundSpeed")

        self.psx.onResume = setup
        self.psx.onPause = teardown
        self.psx.onDisconnect = teardown

        await self.psx.connect()

    async def get_sim_data(self):  # pylint: disable=too-many-locals
        """Get PSX and MSFS data at requested frequency."""
        while True:
            if not self.psx_connected:
                self.logger.warning("PSX not connected, sleeping")
                await asyncio.sleep(1.0)
                continue
            if not self.msfs_connected:
                self.logger.warning("MSFS not connected, sleeping")
                await asyncio.sleep(1.0)
                continue
            # self.logger.debug("Getting data from PSX")
            try:
                data = self.psx.get("PiBaHeAlTas")
                # self.logger.debug("data=%s", data)
                (pitch, bank, heading, altitude, _, latitude, longitude) = data.split(';')
                pitch_r = float(pitch) / 100000
                bank_r = float(bank) / 100000
                heading_true_r = float(heading)
                altitude_true_ft = float(altitude) / 1000
                latitude_r = float(latitude)
                longitude_r = float(longitude)
                groundspeed_kt = float(self.psx.get("GroundSpeed"))
                self.logger.debug("PSX pitch=%.2f bank=%.2f",
                                  math.degrees(pitch_r),
                                  math.degrees(bank_r))
                self.data['psx'] = {
                    'pitch_r': pitch_r,
                    'bank_r': bank_r,
                    'heading_true_r': heading_true_r,
                    'latitude_r': latitude_r,
                    'longitude_r': longitude_r,
                    'altitude_true_ft': altitude_true_ft,
                    'groundspeed_kt': groundspeed_kt,
                    'updated': time.time(),
                }
            except TypeError as exc:
                self.logger.info("Got bad data from PSX, continuing: %s", exc)
            # self.logger.debug("Getting data from MSFS")
            try:
                camera_pitch_r = float(self.msfs_aq.get("CAMERA_GAMEPLAY_PITCH_YAW:0"))
                camera_yaw_r = float(self.msfs_aq.get("CAMERA_GAMEPLAY_PITCH_YAW:1"))
                pitch_r = float(self.msfs_aq.get("PLANE_PITCH_DEGREES"))
                bank_r = float(self.msfs_aq.get("PLANE_BANK_DEGREES"))
                heading_true_r = float(self.msfs_aq.get("PLANE_HEADING_DEGREES_TRUE"))
                altitude_true_ft = float(self.msfs_aq.get("PLANE_ALTITUDE"))
                latitude_r = float(self.msfs_aq.get("PLANE_LATITUDE"))
                longitude_r = float(self.msfs_aq.get("PLANE_LONGITUDE"))
                groundspeed_kt = float(self.msfs_aq.get("GROUND_VELOCITY"))
                self.logger.debug("MSFS pitch=%.2f bank=%.2f",
                                  math.degrees(pitch_r),
                                  math.degrees(bank_r))
                self.data['msfs'] = {
                    'pitch_r': -pitch_r,  # different sign compared to PSX
                    'bank_r': bank_r,
                    'heading_true_r': heading_true_r,
                    'latitude_r': latitude_r,
                    'longitude_r': longitude_r,
                    'altitude_true_ft': altitude_true_ft,
                    'groundspeed_kt': groundspeed_kt,
                    'camera_pitch_r': camera_pitch_r,
                    'camera_yaw_r': camera_yaw_r,
                    'updated': time.time(),
                }
            except TypeError as exc:
                self.logger.info("Got bad data from MSFS, continuing: %s", exc)
            await asyncio.sleep(1 / self.args.sim_update_hz)

    async def compare_and_log(self):  # pylint: disable=too-many-locals,too-many-branches,too-many-statements
        """Compare PSX and MSFS data and output log."""
        pitch_diff_limit = math.radians(2.0)
        bank_diff_limit = math.radians(2.0)
        heading_true_diff_limit = math.radians(2.0)
        camera_diff_limit = math.radians(1.0)
        groundspeed_diff_limit = 3.0  # kt
        # Only warn for problems longer than this
        problem_duration_before_warning = 2.0

        camera_problem_detected = False
        camera_problem_first_seen = None
        wasm_problem_detected = False
        wasm_problem_first_seen = None

        while True:
            await asyncio.sleep(1 / self.args.output_hz)
            psx_data = self.data['psx']
            msfs_data = self.data['msfs']
            if psx_data['updated'] is None or msfs_data['updated'] is None:
                self.logger.info("WARNING: not connected")
                continue
            if abs(psx_data['updated'] - msfs_data['updated']) > 0.5:
                self.logger.info("WARNING: stale data")
                continue
            pitch_diff_r = psx_data['pitch_r'] - msfs_data['pitch_r']
            bank_diff_r = psx_data['bank_r'] - msfs_data['bank_r']
            heading_true_diff_r = psx_data['heading_true_r'] - msfs_data['heading_true_r']
            groundspeed_diff_kt = psx_data['groundspeed_kt'] - msfs_data['groundspeed_kt']
            # Check WASM attitude matching PSX_DATA
            if (
                abs(pitch_diff_r > pitch_diff_limit) or
                abs(bank_diff_r > bank_diff_limit) or
                abs(heading_true_diff_r > heading_true_diff_limit) or
                abs(groundspeed_diff_kt > groundspeed_diff_limit)
            ):
                self.logger.info("WARN: Pdiff: %.2f  Bdiff: %.2f  Hdiff: %.2f  GSdiff: %.1f",
                                 math.degrees(pitch_diff_r),
                                 math.degrees(bank_diff_r),
                                 math.degrees(heading_true_diff_r),
                                 groundspeed_diff_kt,
                                 )
                if not wasm_problem_detected:
                    # First sign of problem, just log time
                    wasm_problem_detected = True
                    wasm_problem_first_seen = time.time()
                else:
                    time_since_start = time.time() - wasm_problem_first_seen
                    if time_since_start > problem_duration_before_warning:
                        winsound.Beep(800, 150)
            else:
                wasm_problem_detected = False
                wasm_problem_first_seen = None
            # Check MSFS camera locked at zero angle
            if (
                    abs(msfs_data['camera_pitch_r']) > camera_diff_limit or
                    abs(msfs_data['camera_yaw_r']) > camera_diff_limit
            ):
                self.logger.info(
                    "WARN: MSFS camera not centered! pitch: %.2f yaw: %.2f",
                    msfs_data['camera_pitch_r'], msfs_data['camera_yaw_r'])
                if not camera_problem_detected:
                    # First sign of problem, just log time
                    camera_problem_detected = True
                    camera_problem_first_seen = time.time()
                else:
                    time_since_start = time.time() - camera_problem_first_seen
                    if time_since_start > problem_duration_before_warning:
                        winsound.Beep(1600, 150)
            else:
                camera_problem_detected = False
                camera_problem_first_seen = None

            self.logger.info("P: %.2f/%.2f  B: %.2f/%.2f  H: %.2f/%.2f  GS: %.1f/%.1f",
                             math.degrees(psx_data['pitch_r']),
                             math.degrees(msfs_data['pitch_r']),
                             math.degrees(psx_data['bank_r']),
                             math.degrees(msfs_data['bank_r']),
                             math.degrees(psx_data['heading_true_r']),
                             math.degrees(msfs_data['heading_true_r']),
                             psx_data['groundspeed_kt'],
                             msfs_data['groundspeed_kt'],
                             )
            self.logger.debug("data=%s", pprint.pformat(self.data))

    async def main(self):
        """Start the script."""
        self._handle_args()
        await asyncio.gather(
            self.setup_psx_connection(),
            self.setup_msfs_connection(),
            self.get_sim_data(),
            self.compare_and_log(),
        )

    def run(self):
        """Start everything up."""
        asyncio.run(self.main())


if __name__ == '__main__':
    me = Comparator()
    me.run()
