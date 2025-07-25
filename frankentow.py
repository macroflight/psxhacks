# pylint: disable=line-too-long,missing-module-docstring,missing-class-docstring,too-many-instance-attributes,protected-access,f-string-without-interpolation,missing-function-docstring,too-many-statements,too-many-branches
import asyncio
import math

from pyproj import Geod

from psx import Client


class Pushback():
    def __init__(self):
        self.heading_r = None
        self.latitude_r = None
        self.longitude_r = None
        self.geod = Geod(ellps="WGS84")
        self.psx = None
        self.wanted_heading_r = None

        self.injection_hz = 5.0

        #  If true, basictug will not attempt to move the plane
        self.basictug_stop = True
        self.basictug_current_speed = 0.0
        self.basictug_current_radius = None
        self.basictug_turn_direction = None

    def send_and_set(self, key, value):
        print(f"send_and_set({key}, {value})")
        self.psx.send(key, value)
        self.psx._set(key, value)

    def brakes_on(self):
        brakes = self.psx.get('Brakes')
        print(f"Brakes={brakes}")
        if brakes == '':
            print("Brakes UNKNOWN, defaulting to on")
            return True
        if int(brakes.split(';')[0]) >= 950:
            return True
        if int(brakes.split(';')[1]) >= 950:
            return True
        return False

    def handle_addon(self, key, value):
        elems = value.split(":")
        if elems[0] != 'FRANKENTOW':
            return
        mode = elems[1]
        payload = elems[2:]
        if mode == 'SLEW':
            print(f"Got SLEW command: {payload}")
            self.handle_slew(payload)
        elif mode == 'BASICTUG':
            print(f"Got BASICTUG command: {payload}")
            self.handle_basictug(payload)
        else:
            print(f"Unsupported mode {mode} ({key}={value})")
            return

    def handle_basictug(self, payload):
        print(f"handle_basictug {payload}")
        mode = payload[0]
        if mode == 'STOP':
            print(f"Basictug stopping")
            self.basictug_stop = True
        elif mode == 'FORWARD':
            if self.brakes_on():
                print("BRAKES STILL ON, IGNORING COMNMAND")
                return
            speed = float(payload[1])
            self.basictug_stop = False
            self.basictug_current_speed = speed
            print(f"Basictug pulling, speed {self.basictug_current_speed:.1f} m/s")
        elif mode == 'BACKWARD':
            if self.brakes_on():
                print("BRAKES STILL ON, IGNORING COMNMAND")
                return
            speed = float(payload[1])
            self.basictug_stop = False
            self.basictug_current_speed = -speed
            print(f"Basictug pushing, speed {self.basictug_current_speed:.1f} m/s")
        elif mode == 'NOSELEFT':
            self.basictug_current_radius = float(payload[1])
            self.basictug_turn_direction = 'NL'
            print(f"Basictug turning aircraft nose left (radius {self.basictug_current_radius:.1f} m)")
        elif mode == 'NOSERIGHT':
            self.basictug_current_radius = float(payload[1])
            self.basictug_turn_direction = 'NR'
            print(f"Basictug turning aircraft nose right (radius {self.basictug_current_radius:.1f} m)")
        elif mode == 'STRAIGHT':
            print(f"Basictug straight")
            self.basictug_current_radius = None
            self.basictug_turn_direction = None

    def handle_slew(self, payload):
        print(f"handle_slew {payload}")
        direction = payload[0]
        move = False
        turn = False
        new_heading_r = None

        if direction == 'LEFT':
            move_direction_r = self.heading_r - math.radians(90)
            distance = float(payload[1])
            move = True
        elif direction == 'RIGHT':
            move_direction_r = self.heading_r + math.radians(90)
            distance = float(payload[1])
            move = True
        elif direction == 'FORWARD':
            move_direction_r = self.heading_r
            distance = float(payload[1])
            move = True
        elif direction == 'BACKWARD':
            move_direction_r = self.heading_r
            distance = -float(payload[1])
            move = True
        elif direction == 'NOSELEFT':
            turn_d = float(payload[1])
            move_direction_r = self.heading_r
            distance = 0.0
            new_heading_r = self.heading_r - math.radians(turn_d)
            turn = True
        elif direction == 'NOSERIGHT':
            turn_d = float(payload[1])
            move_direction_r = self.heading_r
            distance = 0.0
            new_heading_r = self.heading_r + math.radians(turn_d)
            turn = True
        else:
            print(f"Unsupported slew direction {direction}")
            return

        newpos = self.geod.fwd(lons=self.longitude_r, lats=self.latitude_r,
                               az=move_direction_r, dist=distance, radians=True)

        print(f"New position: {newpos}")
        current = self.psx.get('StartPiBaHeAlVsTasYw')
        print(f"<= StartPiBaHeAlVsTasYw={current}")
        elems = current.split(';')
        # Update mode
        elems[0] = '1'

        if move:
            newpos = self.geod.fwd(
                lons=self.longitude_r, lats=self.latitude_r,
                az=move_direction_r, dist=distance, radians=True)
            print(f"New position: {newpos}")
            # longitude
            elems[9] = str(newpos[0])
            # latitude
            elems[8] = str(newpos[1])
        if turn:
            # new heading
            elems[3] = str(int(1000 * new_heading_r))

        newvalue = ";".join(elems)
        print(f"=> StartPiBaHeAlVsTasYw={newvalue}")
        self.send_and_set('StartPiBaHeAlVsTasYw', newvalue)

    def position_changed(self, _, value):
        # print(f"Position changed: {value}")
        elems = value.split(';', 6)
        heading_r = float(elems[2])
        if heading_r != self.heading_r:
            print(f"Heading changed: {self.heading_r} -> {heading_r}")
            self.heading_r = heading_r
        latitude_r = float(elems[5])
        if latitude_r != self.latitude_r:
            print(f"Latitude changed: {self.latitude_r} -> {latitude_r}")
            self.latitude_r = latitude_r
        longitude_r = float(elems[6])
        if longitude_r != self.longitude_r:
            print(f"Longitude changed: {self.longitude_r} -> {longitude_r}")
            self.longitude_r = longitude_r

    async def basictug(self):
        while True:
            if self.basictug_stop:
                # Do nothing and wait a little longer
                await asyncio.sleep(1.0)
                continue

            if self.heading_r is None:
                print("PSX not connected yet")
                await asyncio.sleep(1.0)
                continue

            if self.brakes_on():
                print(f"Brakes applied, stopping tug!")
                self.basictug_stop = True

            print(f"Basictug active, speed {self.basictug_current_speed:.1f}")

            distance = (1 / self.injection_hz) * self.basictug_current_speed

            # alternate the direction when reversing
            turnswap = 1
            if self.basictug_current_speed < 0:
                turnswap = -1

            if self.basictug_turn_direction is None:
                turn = 0.0
            else:
                turnrate = self.basictug_current_speed / self.basictug_current_radius
                if self.basictug_turn_direction == 'NL':
                    turn = -1 * turnswap * (1 / self.injection_hz) * turnrate
                else:
                    turn = 1 * turnswap * (1 / self.injection_hz) * turnrate

            # Set tiller
            # Qh426="Tiller"; Mode=ECON; Min=-999; Max=999;
            # https://aerowinx.com/board/index.php/topic,7271.msg83495.html#msg83495
            # states min radius is 26m but PSX only supports 66m.
            # 26m radius then should result in tiller 100% (-999 or +999)
            # PSX uses ~420 units of tiller (42%) for its 66m radius
            # infinite radius should give tiller 0%
            if self.basictug_current_radius is None:
                tiller_abs = 0
            else:
                tiller_pct = 2600 / self.basictug_current_radius
                if self.basictug_turn_direction == 'NL':
                    tiller_abs = turnswap * int(-1000 * (tiller_pct / 100))
                else:
                    tiller_abs = turnswap * int(1000 * (tiller_pct / 100))

            print(f"Moving {distance} m ({self.basictug_current_speed:.1f} m/s), turning {math.degrees(turn):.1f} degrees")

            newpos = self.geod.fwd(
                lons=self.longitude_r, lats=self.latitude_r,
                az=self.heading_r, dist=distance, radians=True)
            print(f"New position: {newpos}")
            current = self.psx.get('StartPiBaHeAlVsTasYw')
            print(f"<= StartPiBaHeAlVsTasYw={current}")
            elems = current.split(';')
            # Update mode
            elems[0] = '1'
            # longitude
            elems[9] = str(newpos[0])
            # latitude
            elems[8] = str(newpos[1])

            # Also turn a little
            if self.wanted_heading_r is None:
                self.wanted_heading_r = self.heading_r
            self.wanted_heading_r += turn
            print(f"New wanted heading: {math.degrees(self.wanted_heading_r):.1f} deg")

            elems[3] = str(int(1000 * self.wanted_heading_r))

            newvalue = ";".join(elems)
            print(f"=> StartPiBaHeAlVsTasYw={newvalue}")
            self.send_and_set('StartPiBaHeAlVsTasYw', newvalue)
            self.send_and_set('Tiller', str(tiller_abs))

            # Send Qi198 to keep us tethered to the ground?
            # Values below -9000 causes PSX to revert back to its own elevation data

            await asyncio.sleep(1 / self.injection_hz)

    async def psxconn(self):
        """ Try to connect to the PSX Main Server and see what happens. """

        def setup():
            print("Simulation started")
            self.psx.send("name", "TOW:FRANKEN.PY towing services")
            # setup()

        def teardown():
            print("Simulation stopped")
            # teardown()

            # Create a PSX Client and install a custom logger.
        with Client() as self.psx:
            # self.psx.logger = lambda msg: print(f"   {msg}")
            self.psx.subscribe("id")
            self.psx.subscribe(
                "version", lambda key, value:
                print(f"Connected to PSX {value} as client #{self.psx.get('id')}"))
            self.psx.subscribe("PiBaHeAlTas", self.position_changed)
            self.psx.subscribe("StartPiBaHeAlVsTasYw")
            self.psx.subscribe("addon", self.handle_addon)
            self.psx.subscribe("Brakes")

            self.psx.onResume = setup
            self.psx.onPause = teardown
            self.psx.onDisconnect = teardown
            await self.psx.connect()

    async def start(self):
        taskgroup = None
        tasks = set()
        async with asyncio.TaskGroup() as taskgroup:
            task = taskgroup.create_task(self.psxconn(), name="PSX connection")
            tasks.add(task)
            task = taskgroup.create_task(self.basictug(), name="Basic tug")
            tasks.add(task)


if __name__ == '__main__':
    asyncio.run(Pushback().start())
