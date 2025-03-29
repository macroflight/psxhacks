"""Extract MSFS 2024 parking spot data from LittleNavMap database.

Output the data in the "g5.csv" format that GateFinder wants.
"""

import argparse
import logging
import sqlite3
import os


class MakeGateFinderDatabase():  # pylint: disable=too-few-public-methods
    """Make GateFinder-compatible data file."""

    def __init__(self):
        """Initialize the class."""
        log_format = "%(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
        )
        self.logger = logging.getLogger("frankenusb")
        self.args = None

    def _handle_args(self):
        """Handle command line arguments."""
        default_navdata_sqlite = os.path.join(
            os.path.expanduser("~"),
            'AppData',
            'Roaming',
            'ABarthel',
            'little_navmap_db',
            'little_navmap_msfs24.sqlite',
        )
        default_output_csv = os.path.join(
            os.path.expanduser("~"),
            'Documents',
            'g5.csv',
        )

        parser = argparse.ArgumentParser(
            prog='make_gatefinder_database',
        )
        parser.add_argument('--debug',
                            action='store_true')
        parser.add_argument('--lnm-db',
                            default=default_navdata_sqlite,
                            action='store')
        parser.add_argument('--output',
                            default=default_output_csv,
                            action='store')
        parser.add_argument('--overwrite',
                            default=False,
                            action='store_true')

        self.args = parser.parse_args()
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)

    def run(self):  # pylint: disable=too-many-locals
        """Read sqlite data and convert to CSV format."""
        self._handle_args()

        if not os.path.exists(self.args.lnm_db):
            raise SystemExit(
                f"{self.args.lnm_db} not found, is LittleNavMap installed?" +
                " Use --lnm-db to specify non-standard location"
            )
        con = sqlite3.connect(self.args.lnm_db)
        cur = con.cursor()
        res = cur.execute("""
        select airport.ident,
               parking.name,
               parking.number,
               parking.suffix,
               parking.heading,
               parking.lonx,
               parking.laty,
               parking.radius,
               from airport inner join parking where parking.airport_id is airport.airport_id;
        """)

        rows = []

        while row := res.fetchone():
            rows.append(row)

        # Do we want to sort the output? GateFinder seems to present
        # the gates in the order they appear in the CSV file.

        # rows.sort(key = lambda x: (x[0], x[2]))

        if os.path.exists(self.args.output) and not self.args.overwrite:
            raise SystemExit(
                f"Output file {self.args.output} exists, stopping!" +
                " Use --overwrite to overwrite existing file"
            )
        with open(self.args.output, "w", encoding='utf-8') as outputfile:
            for row in rows:
                # MakeRunways format
                # %.4s  ICAO
                # %s    parking prefix
                # %d    parking number
                # %.6f  lat
                # %.6f  lon
                # %.1f  parking radius
                # %.1f  heading
                # %d%s  jetways         - not needed for GateFinder
                # %.4s  airlinecodes    - not needed for GateFinder
                icao = row[0]
                if icao is None:
                    continue
                name = row[1]
                if name is None:
                    name = ""
                # Many "regular" gates have the G prefix in the MSFS
                # data, but it makes more sense to remove the G
                if name.startswith("G"):
                    name = name[1:]
                number = int(row[2])
                suffix = str(row[3])
                if suffix == "None":
                    suffix = ""
                heading = float(row[4])
                lonx = float(row[5])
                laty = float(row[6])
                radius = float(row[7])

                outputline = ""
                outputline += f"{icao},"
                outputline += f"{name},"
                # By adding the suffix here, we can distinguish
                # between e.g gate D11, D11L and D11R
                outputline += f"{number}{suffix},"
                outputline += f"{laty:.6f},"
                outputline += f"{lonx:.6f},"
                outputline += f"{radius:.1f},"
                outputline += f"{heading:.1f},"
                outputline += ","  # skip jetway data, not needed
                outputline += ","  # skip airline codes, not needed
                self.logger.debug(outputline)
                outputline += "\n"
                outputfile.write(outputline)


if __name__ == '__main__':
    me = MakeGateFinderDatabase()
    me.run()
