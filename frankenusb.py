"""Replace PSX USB subsystem."""
# pylint: disable=invalid-name
import argparse
import asyncio
import importlib
import logging
import time
from collections import defaultdict
import pygame  # pylint: disable=import-error
import psx  # pylint: disable=unused-import

# The type of message we use to display the tiller status in the sim
TILLER_MSG = "FreeMsgM"


class FrankenUsbException(Exception):
    """FrankenUSB exception.

    For now, no special handling, this class just exists to make
    pylint happy. :)
    """


class FrankenUsb():  # pylint: disable=too-many-instance-attributes,too-many-public-methods
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
        self.config = None
        self.config_misc = None
        # Pygame events we are intersted in are added to this queue
        self.axis_event_queue = asyncio.Queue(maxsize=0)
        # Variables to be sent to PSX are added to this queue
        self.psx_axis_queue = asyncio.Queue(maxsize=0)
        self.joysticks = {}
        self.args = {}
        # Keeps track of what and when we have sent to PSX
        self.psx_send_state = defaultdict(dict)
        # Main PSX connection object
        self.psx = None
        self.psx_connected = False
        self.axis_cache = defaultdict(dict)
        self.button_cache = defaultdict(dict)
        self.aileron_tiller_active = False
        # We use a button to toggle between reverse and normal mode for the throttles
        self.axis_reverse_mode = {}

    def _handle_args(self):
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog='frankenusb',
            description='(partial)Replacement for PSX USB controller subsystem',
            epilog='Good luck!')
        parser.add_argument('--config-file',
                            action='store', default="frankenusb-frankensim.conf")
        parser.add_argument('--debug',
                            action='store_true')
        parser.add_argument('--quiet',
                            action='store_true')
        parser.add_argument('--max-rate',
                            action='store', default=20.0, type=float,
                            help='the maximum rate we update a PSX variable (Hz)',
                            )
        parser.add_argument('--axis-jitter-limit-low',
                            action='store', default=0.005, type=float,
                            help='axis movements smaller than this are filtered out',
                            )
        parser.add_argument('--axis-jitter-limit-high',
                            action='store', default=1.5, type=float,
                            help='axis movements larger than this are filtered out',
                            )
        parser.add_argument('--psx-server',
                            action='store', default="127.0.0.1", type=str,
                            help='Hostname or IP address of the main PSX server',
                            )

        self.args = parser.parse_args()
        self.logger.info("PSX max rate is set to %.1f Hz", self.args.max_rate)
        if self.args.quiet:
            self.logger.setLevel(logging.CRITICAL)
        elif self.args.debug:
            self.logger.setLevel(logging.DEBUG)

    def load_module_from_file(self, module_name, path):
        """Load config file."""
        loader = importlib.machinery.SourceFileLoader(module_name, path)
        spec = importlib.util.spec_from_loader(loader.name, loader)
        module = importlib.util.module_from_spec(spec)
        loader.exec_module(module)
        return module

    def joystick_get_axis_position(self, joystick_name, axis):
        """Get the current position for a given axis."""
        for _, joystick in self.joysticks.items():
            if joystick.get_name() == joystick_name:
                return joystick.get_axis(axis)
        return False

    def joystick_get_button_position(self, joystick_name, button):
        """Get the current position for a given button."""
        for _, joystick in self.joysticks.items():
            if joystick.get_name() == joystick_name:
                return joystick.get_button(button)
        return False

    def centre_ailerons_and_tiller(self):
        """Send events to PSX that centres the aileron and tiller.

        Used whenever we switch tiller mode on and off.
        """
        self.logger.info("centreing aileron and tiller")
        self.psx_axis_queue.put({
            'variable': 'Tiller',
            'indexes': [0],
            'value': 0,
        })
        self.psx_axis_queue.put({
            'variable': 'FltControls',
            'indexes': [1],
            'value': 0,
        })

    def towing_heading_change(self, increment):
        """Change the pushback target heading by increment degrees.

        Towing is a string of six digits. Wee care about digits 4, 5 and 6, which are the heading.
        """
        towing = str(self.psx.get('Towing'))
        self.logger.debug("Towing string: %s", towing)
        self.logger.debug("Current towing heading str: %s", towing[3:6])
        heading = int(towing[3:6])
        self.logger.debug("Current towing heading: %s", heading)
        heading_new = heading + increment
        if heading_new > 360:
            heading_new -= 360
        if heading_new < 0:
            heading_new += 360
        self.logger.debug("New towing heading: %s", heading_new)
        towing_new = towing[:3] + str(heading_new).zfill(3)
        self.logger.debug("New towing string: %s", towing_new)
        self.psx_send_and_set('Towing', towing_new)

    def towing_direction_toggle(self):
        """Toggle the towing direction.

        Towing is a string of six digits. Wee care about digit 1 (1 = pushback, 2 = push forward)
        """
        towing = str(self.psx.get('Towing'))
        self.logger.debug("Towing string: %s", towing)
        direction = towing[0]
        if direction == "1":
            direction = "2"
        else:
            direction = "1"
        self.logger.debug("New towing direction: %s", direction)
        towing_new = direction + towing[1:]
        self.logger.debug("New towing string: %s", towing_new)
        self.psx_send_and_set('Towing', towing_new)

    def towing_mode_toggle(self):
        """Toggle the towing mode (start/stop).

        Towing is a string of six digits. Wee care about digit 2 and 3 (20=stop, 80=start)
        We never use auto.
        """
        towing = str(self.psx.get('Towing'))
        self.logger.debug("Towing string: %s", towing)
        mode = towing[1:3]
        self.logger.debug("Towing mode: %s", mode)
        if mode == "10":
            mode = "98"
        else:
            mode = "20"
        self.logger.debug("New towing mode: %s", mode)
        towing_new = towing[:1] + mode + towing[3:]
        self.logger.debug("New towing string: %s", towing_new)
        self.psx_send_and_set('Towing', towing_new)

    async def handle_axis_motion_normal(self, event, axis_config):
        """Handle motion on a normal axis."""
        # pygame axes are always -1 .. +1?
        axis_min = -1.0
        axis_max = 1.0
        # Apply static zones to pygame axis value
        if 'static zones' in axis_config:
            for zone in axis_config['static zones']:
                if event.value >= zone[0] and event.value <= zone[1]:
                    self.logger.debug("In static zone: %s -> %s", event.value, zone[2])
                    event.value = zone[2]
        # Swap axis if neede
        if 'axis swap' in axis_config:
            if axis_config['axis swap'] is True:
                event.value = -event.value
        # Normalize
        axis_normalized = (event.value - axis_min) / (axis_max - axis_min)

        tiller_axis_in_tiller_mode = False
        if 'tiller' in axis_config and axis_config['tiller'] is True and self.aileron_tiller_active:
            tiller_axis_in_tiller_mode = True

        if tiller_axis_in_tiller_mode:
            # Tiller mode
            psx_min = -999
            psx_max = 999
            psx_range = psx_max - psx_min
            psx_value = int(psx_min + psx_range * axis_normalized)
            await self.psx_axis_queue.put({
                'variable': 'Tiller',
                'indexes': [0],
                'value': psx_value,
            })
        else:
            # Normal mode
            psx_range = axis_config['psx max'] - axis_config['psx min']
            psx_value = int(axis_config['psx min'] + psx_range * axis_normalized)
            await self.psx_axis_queue.put({
                'variable': axis_config['psx variable'],
                'indexes': axis_config['indexes'],
                'value': psx_value,
            })

    async def handle_axis_motion_speedbrake(self, event, axis_config):
        """Handle motion on a speedbrake axis.

        PSX values: 0-800

        armed: a range around 41 (61 is no longer armed)
        max in flight: 375
        full ground 800
        """
        axis_position = event.value
        axis_min = -1.0
        axis_max = 1.0

        if 'axis swap' in axis_config:
            if axis_config['axis swap'] is True:
                axis_position = -axis_position
        # Normalize axis position to range 0..1
        axis_position = (axis_position - axis_min) / (axis_max - axis_min)
        self.logger.info("speedbrake axis position is %s", axis_position)
        if axis_position < axis_config['limit stowed']:
            self.logger.info("speedbrake STOWED")
            psx_value = int(0)
        elif axis_position < axis_config['limit armed']:
            self.logger.info("speedbrake ARMED")
            psx_value = int(41)
        elif axis_position > axis_config['limit flight upper']:
            self.logger.info("speedbrake MAX GROUND")
            psx_value = int(800)
        else:
            # Flight range
            flightrange_axis = axis_config['limit flight upper'] - axis_config['limit armed']
            flightrange_psx = 375 - 61
            psx_per_axis_unit = flightrange_psx / flightrange_axis
            psx_speedbrake = 61 + (axis_position - axis_config['limit armed']) * psx_per_axis_unit
            psx_value = int(psx_speedbrake)
            self.logger.info("speedbrake FLIGHT %s", psx_value)

        await self.psx_axis_queue.put({
            'variable': axis_config['psx variable'],
            'indexes': [0],
            'value': psx_value,
        })

    async def handle_throttle_reverse_button(self, mode, joystick_name, event, config, reverse):  # pylint:disable=too-many-arguments,too-many-locals,too-many-branches,too-many-positional-arguments,too-many-statements
        """Handle throttle with thrust reverser button."""
        if 'axis min' in config:
            axis_min = config['axis min']
        else:
            axis_min = -1.0
        if 'axis max' in config:
            axis_max = config['axis max']
        else:
            axis_max = 1.0

        def get_axis_mode(axis):
            if joystick_name not in self.axis_reverse_mode:
                self.axis_reverse_mode[joystick_name] = {}
            if axis not in self.axis_reverse_mode[joystick_name]:
                self.axis_reverse_mode[joystick_name][axis] = 'normal'
            try:
                return self.axis_reverse_mode[joystick_name][axis]
            except KeyError:
                return 'normal'

        def set_axis_mode(axis, mode):
            self.axis_reverse_mode[joystick_name][axis] = mode

        if mode == 'button':
            axis_position = self.joystick_get_axis_position(joystick_name, config['axis'])
            axis_config = self.config[joystick_name]['axis motion'][config['axis']]
            if axis_position > axis_config['reverse lever unlocked range'][1]:
                self.logger.info("Cannot toggle reverse, lever position %s", axis_position)
                return
            if axis_position < axis_config['reverse lever unlocked range'][0]:
                self.logger.info("Cannot toggle reverse, lever position %s", axis_position)
                return

            if get_axis_mode(config['axis']) == 'normal':
                self.logger.info("Set axis mode for axis %s to reverse", config['axis'])
                set_axis_mode(config['axis'], 'reverse')
                reverse = True
            else:
                self.logger.info("Set axis mode for axis %s to normal", config['axis'])
                set_axis_mode(config['axis'], 'normal')
                reverse = False
        else:
            reverse = bool(get_axis_mode(event.axis) == 'reverse')
            axis_position = event.value
            axis_config = config

        # Apply static zones to pygame axis value
        if 'static zones' in axis_config:
            for zone in axis_config['static zones']:
                if zone[1] >= axis_position >= zone[0]:
                    self.logger.info("In static zone: %s -> %s", axis_position, zone[2])
                    axis_position = zone[2]
        # Swap axis if needed
        if 'axis swap' in axis_config:
            if axis_config['axis swap'] is True:
                axis_position = -axis_position
        # Normalize
        axis_normalized = (axis_position - axis_min) / (axis_max - axis_min)
        # Convert to PSX value
        if reverse:
            psx_min = axis_config['psx reverse idle']
            psx_max = axis_config['psx reverse full']
            psx_range = psx_max - psx_min
            psx_value = int(psx_min + psx_range * axis_normalized)
            # Never send a value outside the expected range
            psx_value = max(psx_max, psx_value)
            psx_value = min(psx_min, psx_value)
        else:
            psx_min = axis_config['psx idle']
            psx_max = axis_config['psx full']
            psx_range = psx_max - psx_min
            psx_value = int(psx_min + psx_range * axis_normalized)
            # Never send a value outside the expected range
            psx_value = min(psx_max, psx_value)
            psx_value = max(psx_min, psx_value)

        if self.autothrottle_active():
            self.logger.info("Throttle movement to %s, but A/T active, blocking", psx_value)
            tla = int(self.psx.get('Tla').split(';')[axis_config['engine indexes'][0]])
            self.logger.info("This Tla is %s", tla)
            diff = abs(tla - psx_value)
            if diff < 100:
                self.logger.info("Axis is close to Tla angle - diff=%s", diff)
                pygame.mixer.Sound(self.config_misc["THROTTLE_SYNC_SOUND"]).play()
            else:
                self.logger.info("Axis is far from Tla angle - diff=%s", diff)
        else:
            await self.psx_axis_queue.put({
                'variable': axis_config['psx variable'],
                'indexes': axis_config['engine indexes'],
                'value': psx_value,
            })

    async def handle_axis_motion(self, event):
        """Handle any axis motion."""
        try:
            joystick_name = self.joysticks[event.instance_id].get_name()
        except KeyError:
            self.logger.warning(
                "Dropping event for joystick %s (normal if joystick just added or removed)",
                event.instance_id
            )
            return
        try:
            axis_config = self.config[joystick_name]['axis motion'][event.axis]
        except KeyError:
            # Not handling this axis
            return
        # Filter out very small movements
        try:
            last_seen = self.axis_cache[event.instance_id][event.axis]
        except KeyError:
            self.logger.debug("No axis_cache data for %s/%s, no action",
                              event.instance_id, event.axis)
            self.axis_cache[event.instance_id][event.axis] = event.value
        else:
            axis_move_absolute = abs(event.value - last_seen)
            if axis_move_absolute < self.args.axis_jitter_limit_low:
                self.logger.debug("Ignoring small move (%s) for axis %s/%s, no action",
                                  axis_move_absolute, event.instance_id, event.axis)
                return
            if axis_move_absolute > self.args.axis_jitter_limit_high:
                self.logger.debug("Ignoring large move (%s) for axis %s/%s, no action",
                                  axis_move_absolute, event.instance_id, event.axis)
                return
        # Update cache
        self.axis_cache[event.instance_id][event.axis] = event.value
        self.logger.debug("axis cache: %s", self.axis_cache)

        if axis_config['axis type'] == 'NORMAL':
            await self.handle_axis_motion_normal(event, axis_config)
        elif axis_config['axis type'] == 'THROTTLE_WITH_REVERSE_BUTTON':
            await self.handle_throttle_reverse_button(
                'axis', joystick_name, event, axis_config, None)
        elif axis_config['axis type'] == 'SPEEDBRAKE':
            await self.handle_axis_motion_speedbrake(event, axis_config)
        else:
            raise FrankenUsbException(f"Unknown axis type {axis_config['axis type']}")

    async def handle_button(self, event):  # pylint: disable=too-many-branches,too-many-statements
        """Handle button press/release."""
        direction = 'up' if event.type == pygame.JOYBUTTONUP else 'down'
        try:
            joystick_name = self.joysticks[event.instance_id].get_name()
        except KeyError:
            self.logger.error(
                "Failed to lookup joystick name for instance ID %s", event.instance_id)
            return

        # Store this press in the button cache. This cache is used to
        # detect rapid button presses, e.g to move the altitude
        # quicker when the knob is turned faster

        try:
            last_event = self.button_cache[event.instance_id][event.button]
        except KeyError:
            self.logger.debug("No button cache data for %s/%s",
                              event.instance_id, event.button)
            last_event = 0
        self.button_cache[event.instance_id][event.button] = time.time()
        time_since_last_event = time.time() - last_event
        self.logger.debug("Button %s/%s last pressed %.2f s ago",
                          event.instance_id, event.button,
                          time_since_last_event)
        try:
            button_config = self.config[joystick_name][f"button {direction}"][event.button]
        except KeyError:
            # Not handling this button/direction
            return
        if button_config['button type'] == "SET":
            # Set a PSX variable to the value in config
            self.psx_send_and_set(button_config['psx variable'], button_config['value'])
        elif button_config['button type'] == "SET_ACCELERATED":
            minimum_interval = button_config['minimum interval']
            acceleration = button_config['acceleration']
            # Assumes this is a delta variable (where we send e.g 1 or -1 normally).
            # If the time since the last event for this button is low
            # enough, we multiply the value by 5.
            if time.time() - last_event < minimum_interval:
                new_value = button_config['value'] * acceleration
                self.logger.debug("Button %s/%s last pressed %.2f s ago, ACCELERATED",
                                  event.instance_id, event.button,
                                  time_since_last_event)
                self.psx_send_and_set(button_config['psx variable'], new_value)
            else:
                self.psx_send_and_set(button_config['psx variable'], button_config['value'])
        elif button_config['button type'] == "REVERSE_LEVER":
            if direction == 'down':
                # mode, joystick_name, event, config, reverse
                await self.handle_throttle_reverse_button(
                    'button', joystick_name, event, button_config, False)
            else:
                await self.handle_throttle_reverse_button(
                    'button', joystick_name, event, button_config, True)
        elif button_config['button type'] == 'INCREMENT':
            value = int(self.psx.get(button_config['psx variable']))
            increment = int(button_config['increment'])
            new_value = value + increment
            wrap = False
            if 'wrap' in button_config and button_config['wrap'] is True:
                wrap = True
            if 'min' in button_config and new_value < button_config['min']:
                if wrap:
                    new_value = button_config['max']
                else:
                    new_value = button_config['min']
            elif 'max' in button_config and new_value > button_config['max']:
                if wrap:
                    new_value = button_config['min']
                else:
                    new_value = button_config['max']
            if new_value != value:
                self.psx_send_and_set(button_config['psx variable'], new_value)
        elif button_config['button type'] == 'BIGMOMPSH':
            self.logger.debug("BIGMOMPSH event for %s", button_config['psx variable'])
            value = int(self.psx.get(button_config['psx variable']))
            new_value = value | 1
            if new_value != value:
                self.psx_send_and_set(button_config['psx variable'], new_value)
        elif button_config['button type'] == 'TOWING_HEADING':
            self.towing_heading_change(button_config['increment'])
        elif button_config['button type'] == 'TOWING_DIRECTION_TOGGLE':
            self.towing_direction_toggle()
        elif button_config['button type'] == 'TOWING_MODE_TOGGLE':
            self.towing_mode_toggle()
        elif button_config['button type'] == 'TILLER_TOGGLE':
            if self.aileron_tiller_active:
                # Remove warning, centre aileron and tiller, disable tiller mode
                self.psx.send(TILLER_MSG, "")
                self.centre_ailerons_and_tiller()
                self.aileron_tiller_active = False
            else:
                # Display warning message, centre aileron and tiller, enable tiller mode
                self.psx.send(TILLER_MSG, "TILLER ACTIVE")
                self.centre_ailerons_and_tiller()
                # Enable tiller mode
                self.aileron_tiller_active = True
        elif button_config['button type'] == 'ACTION_RUNWAY_ENTRY':
            # Transponder TARA, all lights except outer landing lights on
            self.logger.info("Runway entry action: NOT IMPLEMENTED")
        elif button_config['button type'] == 'ACTION_CLEARED_TAKEOFF':
            # Outer landing lights on
            self.logger.info("Cleared takeoff action: NOT IMPLEMENTED")
        elif button_config['button type'] == 'ACTION_EXITED_RUNWAY':
            # Transponder standby, landing lights off, taxi lights on, APU start, autobrake disable
            self.logger.info("Runway exited action: NOT IMPLEMENTED")
        else:
            raise FrankenUsbException(f"Unknown button type {button_config['button type']}")

    async def handle_pygame_events(self):
        """Read pygame events from queue and handle them."""
        while True:
            thisevent = await self.axis_event_queue.get()
            self.logger.debug("handle_pygame_events got %s", thisevent)
            if thisevent.type == pygame.JOYAXISMOTION:
                await self.handle_axis_motion(thisevent)
            elif thisevent.type in [pygame.JOYBUTTONDOWN, pygame.JOYBUTTONUP]:
                await self.handle_button(thisevent)
            else:
                raise FrankenUsbException(f"Got event type we do not handle: {thisevent.type}")
            await asyncio.sleep(0.01)

    async def read_pygame_events(self):  # pylint: disable=too-many-branches
        """Read pygame events from queue and handle them."""
        while True:
            if not self.psx_connected:
                self.logger.warning("PSX not connected, not reading any pygame events")
                await asyncio.sleep(1.0)
                continue
            if self.axis_event_queue.qsize() > 10:
                self.logger.warning("WARNING: event queue size: %d",
                                    self.axis_event_queue.qsize())
            axis_events = {}
            other_events = []
            for event in pygame.event.get():
                # If a device is added or removed, restart
                if event.type == pygame.JOYDEVICEREMOVED:
                    self.logger.info("Joystick device removed, re-init joysticks")
                    await self.init_joysticks()
                if event.type == pygame.JOYDEVICEADDED:
                    self.logger.info("Joystick device added, re-init joysticks")
                    await self.init_joysticks()
                # Filter out events we won't handle anyway
                if event.type == pygame.JOYAXISMOTION:
                    # To avoid overloading the event handler, cache
                    # the events and only put the last event for a
                    # certain axis in the queue.
                    axis_events[(event.instance_id, event.axis)] = event
                elif event.type in [pygame.JOYBUTTONUP, pygame.JOYBUTTONDOWN]:
                    other_events.append(event)
            for event in other_events:
                # If queue is full, we wait until a slot is available,
                # we never want to drop button events.
                await self.axis_event_queue.put(event)
            for _, event in axis_events.items():
                # It's OK to drop axis events if the queue is full
                try:
                    self.axis_event_queue.put_nowait(event)
                except asyncio.QueueFull:
                    self.logger.warning("Dropping pygame axis events as queue is full")
            # 0.01s here leads to a buildup of events in the queue
            # when moving two axes. Why? Not enough time left over for
            # the other coroutines that will process the events? 0.05s
            # seems fine.
            await asyncio.sleep(0.05)

    def autothrottle_active(self):
        """Check if the autothrottle is managing the levers.

        If the AFDS mode is blank or HOLD, we own the levers :)

        BLANK = 0
        HOLD = 21
        Source: https://aerowinx.com/board/index.php/topic,4408.msg72250.html#msg72250
        """
        afds = self.psx.get("Afds")
        atmode = int(afds.split(';')[0])
        if atmode in [0, 21]:
            return False
        return True

    def print_psx_variable(self, key, value):
        """Log the value of a PSX variable."""
        self.logger.info("PSX variable %s is now %s", key, value)

    async def setup_psx_connection(self):
        """Set up the PSX connection."""
        def setup():
            self.psx.send("demand", "GroundSpeed")
            self.psx_connected = True
            self.aileron_tiller_active = False
            self.logger.info("Connected to PSX")

        def teardown():
            self.logger.info("Disconnected from PSX, tearing down")
            self.psx.send(TILLER_MSG, "")
            self.psx_connected = False

        def connected(key, value):
            self.logger.info("Connected to PSX %s %s as #%s", key, value, self.psx.get('id'))
            self.psx_connected = True

        self.psx = psx.Client()
        self.psx.logger = self.logger.debug  # .info to see traffic

        self.psx.subscribe("id")
        self.psx.subscribe("version", connected)

        # Needed for tiller mode
        self.psx.subscribe("Tiller")

        # Needed for autothrottle
        self.psx.subscribe("Afds", self.print_psx_variable)

        self.psx.onResume = setup
        self.psx.onPause = teardown
        self.psx.onDisconnect = teardown

        # We need to subscribe to PSX variables included in the config
        psx_variables = set()
        for _, data in self.config.items():
            for _, data in data.items():
                for _, action in data.items():
                    if 'psx variable' in action:
                        psx_variables.add(action['psx variable'])
        self.logger.info("Subscribing to PSX variables %s", psx_variables)
        for psx_variable in psx_variables:
            self.psx.subscribe(psx_variable)
        self.logger.info("PSX subscribed variables: %s", ', '.join(self.psx.variables.keys()))
        # Nothing happens until we connect()
        await self.psx.connect(host=self.args.psx_server)

    def psx_send_and_set(self, psx_variable, new_psx_value):
        """Send variable to PSX and store in local db."""
        self.logger.debug("TO PSX: %s -> %s", psx_variable, new_psx_value)
        self.psx.send(psx_variable, new_psx_value)
        self.psx._set(psx_variable, new_psx_value)  # pylint: disable=protected-access

    async def psx_axis_sender(self):  # pylint: disable=too-many-branches
        """Send axis data to PSX.

        Pygame axis events can easily arrive faster than we want to
        push data to PSX, to those variables are handled like this:

        if variable not in psx_send_state
          send variable to PSX and store the time and last value sent in psx_send_state
        else
          check elapsed time since last send
          if enough time elapsed
            send variable to PSX and store the time and last value sent in psx_send_state
          else
            store value we want to send in psx_send_state

        We also check psx_send_state on each loop, and if enough time has
        passed for some variable, we send the saved value to PSX.

        Since multiple axes can provide data (e.g elevator and aileron
        both use FltControls) to the same PSX variable, we need to
        handle this. And we must not overwrite data already in the

        variable that we don't update. So a read-modify-write is
        needed.

        state["FltControls"] = {
           'last sent': 12345567.0,
           'new data' : {
             0: 576,
             1: 224,
           },
        }

        The above will result in FltControls="576;224;X" being sent to
        PSX where X is the existing value of the third element that we
        do not update. X will only be read from PSX just before we
        will update the variable.

        """
        while True:
            if not self.psx_connected:
                self.logger.warning("PSX not connected, not looking at self.psx_axis_queue")
                await asyncio.sleep(1.0)
                continue
            if self.psx_axis_queue.qsize() > 10:
                self.logger.warning("WARNING: psx_axis queue size: %d", self.psx_axis_queue.qsize())
            try:
                # If an event is available, process it
                thisevent = self.psx_axis_queue.get_nowait()
                try:
                    elapsed = time.time() - self.psx_send_state[thisevent['variable']]['last sent']
                except KeyError:
                    elapsed = time.time()  # never sent

                # Store the new value(s) in state
                for index in thisevent['indexes']:
                    if 'new data' not in self.psx_send_state[thisevent['variable']]:
                        self.psx_send_state[thisevent['variable']]['new data'] = {}
                    self.psx_send_state[
                        thisevent['variable']]['new data'][index] = thisevent['value']
                # If enough time has passed sinc the last send, set last sent to zero,
                # triggering sending to PSX
                if elapsed > (1.0 / self.args.max_rate):
                    self.psx_send_state[thisevent['variable']]['last sent'] = 0.0
            except asyncio.QueueEmpty:
                # self.logger.debug("No event from PSX queue")
                pass

            # Check self.psx_send_state for variables to send to PSX
            for variable, data in self.psx_send_state.items():
                if 'new data' not in data or len(data['new data']) == 0:
                    # No data to send for this variable
                    continue
                elapsed = time.time() - data['last sent']
                if elapsed > (1.0 / self.args.max_rate):
                    # Read data from PSX, modify and write back
                    psx_value = self.psx.get(variable)
                    elems = psx_value.split(';')
                    new_data = data['new data']
                    for index, value in new_data.items():
                        elems[index] = str(value)
                    new_psx_value = ";".join(elems)
                    self.psx_send_and_set(variable, new_psx_value)
                    data['last sent'] = time.time()
                    data['new data'] = {}
            await asyncio.sleep(0.01)

    async def init_joysticks(self):
        """Initialize the joysticks."""
        self.logger.info("Initializing joysticks...")
        self.joysticks = {}
        # Since the IDs might change, we need to empty the queue of any old events
        self.logger.info("Considering dropping events from queue")
        while not self.axis_event_queue.empty():
            self.logger.info("Dropping one event from queue")
            self.axis_event_queue.get_nowait()
            self.axis_event_queue.task_done()
        while not self.psx_axis_queue.empty():
            self.logger.info("Dropping one event from queue")
            self.psx_axis_queue.get_nowait()
            self.psx_axis_queue.task_done()
        self.logger.info("Done considering dropping events from queue")

        for i in range(pygame.joystick.get_count()):
            joystick_name = pygame.joystick.Joystick(i).get_name()
            if joystick_name not in self.config:
                self.logger.warning(
                    "Joystick %s (%s) found but not used in config file", i, joystick_name)
                continue
            self.logger.info("Joystick %s found: %s", i, joystick_name)
            joy = pygame.joystick.Joystick(i)
            joy.init()
            self.joysticks[joy.get_instance_id()] = joy
        if len(self.joysticks) <= 0:
            self.logger.warning("Found no configured joysticks!")
        else:
            self.logger.info("Watching %d joysticks for events", len(self.joysticks))

    async def main(self):
        """Start the script."""
        self._handle_args()
        try:
            self.config = self.load_module_from_file("self.config", self.args.config_file).CONFIG
            self.config_misc = self.load_module_from_file("self.config_misc",
                                                          self.args.config_file).CONFIG_MISC
        except IOError as inst:
            raise FrankenUsbException(
                f"Failed to open config file {self.args.config_file}: {inst}") from inst

        pygame.init()
        self.logger.debug("Waiting a little after pygame.init()")
        # iadbound had problems with devices not showing up unless
        # --debug was used. This seems to have made his setup stable.
        await asyncio.sleep(2.0)
        pygame.joystick.init()
        self.logger.debug("Waiting a little after pygame.joystickinit()")
        # see above comment about iadbound
        await asyncio.sleep(2.0)

        await asyncio.gather(
            self.read_pygame_events(),
            self.handle_pygame_events(),
            self.psx_axis_sender(),
            self.setup_psx_connection(),
        )

    def run(self):
        """Start everything up."""
        asyncio.run(self.main())


if __name__ == '__main__':
    me = FrankenUsb()
    me.run()
