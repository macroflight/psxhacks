"""A replacement for PSX's builtin USB handling."""
# pylint: disable=invalid-name
import asyncio
import logging
import sys
import threading
import time
import pygame  # pylint: disable=import-error
from psx import Client

# We will never send data to PSX faster than this
MAX_PSX_HZ = 20.0

CONFIG = {
    'MFG Crosswind V2': {
        'axis motion': {
            2: {
                # Rudder
                'psx action type': 'AXIS_NORMAL',
                'psx variable': 'FltControls',
                'indexes': [2],
                'psx min': -999,
                'psx max': 999,
                'axis nullzone': (-0.05, 0.05, 0.0),  # axis min, axis max, axis replacement value
                'axis swap': False,
            },
            0: {
                # Toe Brake Left
                'psx action type': 'AXIS_NORMAL',
                'psx variable': 'Brakes',
                'indexes': [0],
                'psx min': 0,
                'psx max': 1000,
                'axis swap': False,
            },
            1: {
                # Toe Brake Left
                'psx action type': 'AXIS_NORMAL',
                'psx variable': 'Brakes',
                'indexes': [1],
                'psx min': 0,
                'psx max': 1000,
                'axis swap': False,
            },
        },
    },
    'TCA YOKE BOEING': {
        'axis motion': {
            0: {
                # Aileron
                'psx action type': 'AXIS_NORMAL',
                'psx variable': 'FltControls',
                'indexes': [1],
                'psx min': -999,
                'psx max': 999,
                'axis nullzone': (-0.01, 0.01, 0.0),  # axis min, axis max, axis replacement value
                'axis swap': False,
            },
            1: {
                # Elevator
                'psx action type': 'AXIS_NORMAL',
                'psx variable': 'FltControls',
                'indexes': [0],
                'psx min': -999,
                'psx max': 999,
                'axis nullzone': (-0.01, 0.01, 0.0),  # axis min, axis max, axis replacement value
                'axis swap': False,
            },
        },
        'button down': {
            11: {
                # AP disconnect
                'psx action type': 'PUSH_DELTA1',
                'psx variable': 'ApDisc',
            },
        },
    },
    'TCA Quadrant Boeing 1&2': {
        'axis motion': {
            4: {
                # A throttle axis that uses a button to switch into
                # reverse mode.
                'psx action type': 'THROTTLE_WITH_REVERSE_BUTTON',
                'psx variable': 'Tla',
                'axis min': -1.0,
                'axis max': 1.0,
                'axis swap': True,
                # Values to send to PSX at idle and full thrust
                'psx idle': 0,
                'psx full': 5000,
                # PSX values to send at reverse idle and reverse full
                'psx reverse idle': -100,
                'psx reverse full': -8925,
                # The PSX engines (1-4) controlled by this throttle
                'engine indexes': [0, 1],
                # The button that triggers reverse
                'reverse button': 4,
            },
            5: {
                'psx action type': 'THROTTLE_WITH_REVERSE_BUTTON',
                'psx variable': 'Tla',
                'axis min': -1.0,
                'axis max': 1.0,
                'axis swap': True,
                'psx idle': 0,
                'psx full': 5000,
                'psx reverse idle': -100,
                'psx reverse full': -8925,
                'engine indexes': [2, 3],
                'reverse button': 5,
            },
            3: {
                # Speedbrake
                'psx action type': 'AXIS_SPEEDBRAKE',
                'psx variable': 'ApDisc',
            },
        },
        'button down': {
            4: {
                # A button used for a THROTTLE_WITH_REVERSE_BUTTON axis
                'psx action type': 'BUTTON_FOR_THROTTLE_WITH_REVERSE_BUTTON',
                'psx reverse': True,
                'axis': 4,
            },
            5: {
                'psx action type': 'BUTTON_FOR_THROTTLE_WITH_REVERSE_BUTTON',
                'psx reverse': True,
                'axis': 5,
            },
            8: {
                # Raise flaps
                'psx action type': 'INCREMENT',
                'psx variable': 'FlapLever',
                'increment': -1,
                'min': 0, 'max': 6,
            },
            9: {
                # Toe brake both down
                'psx action type': 'SET',
                'psx variable': 'ToeBrakeTogg',
                'value': 3,
            },
            10: {
                # Lower flaps
                'psx action type': 'INCREMENT',
                'psx variable': 'FlapLever',
                'increment': 1,
                'min': 0, 'max': 6,
            },
            15: {
                'psx action type': 'BUTTON_ROTARY_TMB',
                'direction': 'cw',
                'button': 'down',
                # Which buttons control the mode: speed, heading, altitude
                'modebuttons': [11, 12, 13],
            },
            14: {
                'psx action type': 'BUTTON_ROTARY_TMB',
                'direction': 'ccw',
                'button': 'down',
                'modebuttons': [11, 12, 13],
            },
            16: {
                'psx action type': 'BUTTON_ROTARY_TMB',
                'direction': 'push',
                'modebuttons': [11, 12, 13],
            },
        },
        'button up': {
            4: {
                'psx action type': 'BUTTON_FOR_THROTTLE_WITH_REVERSE_BUTTON',
                'psx reverse': False,
                'axis': 4,
            },
            5: {
                'psx action type': 'BUTTON_FOR_THROTTLE_WITH_REVERSE_BUTTON',
                'psx reverse': False,
                'axis': 5,
            },
            9: {
                # Toe brake both down
                'psx action type': 'SET',
                'psx variable': 'ToeBrakeTogg',
                'value': 0,
            },
            15: {
                'psx action type': 'BUTTON_ROTARY_TMB',
                'direction': 'cw',
                'button': 'up',
                # Which buttons control the mode: speed, heading, altitude
                'modebuttons': [11, 12, 13],
            },
            14: {
                'psx action type': 'BUTTON_ROTARY_TMB',
                'direction': 'ccw',
                'button': 'up',
                'modebuttons': [11, 12, 13],
            },
        },
    },
}

# Global variable containing our PSX connection object
PSX = None
joysticks = {}

# Cache rotary events when they are too rapid, we can't afford to lose
# any
rotary_cache = {}

#
# Helper functions to do common PSX things, e.g send a simple value,
# push a MOM buttont, ...
#


def psx_send_and_set(variable, value):
    """Send a variable to PSX and set it in the local db."""
    PSX.send(variable, value)
    PSX._set(variable, value)  # pylint: disable=protected-access


def psx_push_mom(buttonname):
    """Push a Big or MCP Momentary Action Switch.

    E.g McpPshThr

    See https://aerowinx.com/assets/networkers/Network%20Documentation.txt

    To push such a switch we need to get its current value (things
    like if the light is on), then enable the 1 bit and send the
    result back to PSX.
    """
    logging.info("PUSH_MOM for %s", buttonname)
    try:
        value = int(PSX.get(buttonname))
    except ValueError:
        logging.info("ERROR: could not get %s from PSX", buttonname)
        return False
    value_new = value | 1
    logging.info("MOMSW %s is %s, setting to %s", buttonname, value, value_new)
    psx_send_and_set(buttonname, value_new)
    return True


def psx_push_delta(buttonname, value=1):
    """Push a button that has Mode=DELTA; Min=0; Max=1.

    E.g McpPshHdgSel

    See https://aerowinx.com/assets/networkers/Network%20Documentation.txt
    """
    value = int(value)
    psx_send_and_set(buttonname, str(value))


def psx_increment_delta(buttonname, increment=1, min_value=None, max_value=None):
    """Read a value and increment it."""
    try:
        value = int(PSX.get(buttonname))
    except ValueError:
        logging.info("ERROR: could not get %s from PSX", buttonname)
        return False
    increment = int(increment)
    new_value = value + increment
    if min_value is not None and new_value < min_value:
        new_value = min_value
    if max_value is not None and new_value > max_value:
        new_value = max_value
    if new_value != value:
        psx_send_and_set(buttonname, str(new_value))
    return True


def psx_set(buttonname, value):
    """Set a PSX variable to value.

    E.g GearLever

    See https://aerowinx.com/assets/networkers/Network%20Documentation.txt
    """
    psx_send_and_set(buttonname, str(value))


def axis2psx(axis_position, psx_min, psx_max, axis_swap=False):
    """Translate an axis value to a PSX value."""
    # For now, assume all joystick axes return values between -1.0 and +1.0.
    axis_min = -1.0
    axis_max = 1.0
    if axis_swap:
        axis_position = -1.0 * axis_position
    # Normalize axis position to range 0..1
    axis_position = (axis_position - axis_min) / (axis_max - axis_min)
    # How large is the PSX range?
    psx_range = psx_max - psx_min
    return int(psx_min + psx_range * axis_position)


def speedbrake2psx(axis_position, axis_swap=False):
    """Translate axis to PSX SpdBrkLever.

    PSX values: 0-800

    armed: a range around 41 (61 is no longer armed)
    max in flight: 375
    full ground 800
    """
    axis_min = -1.0
    axis_max = 1.0

    STOWED_LIMIT = 0.1
    ARMED_LIMIT = 0.3
    FLIGHTRANGE_LIMIT = 0.9

    if axis_swap:
        axis_position = -1.0 * axis_position
    # Normalize axis position to range 0..1
    axis_position = (axis_position - axis_min) / (axis_max - axis_min)
    if axis_position < STOWED_LIMIT:
        return int(0)
    if axis_position < ARMED_LIMIT:
        return int(41)
    if axis_position > FLIGHTRANGE_LIMIT:
        return int(800)
    # Flight range
    flightrange_axis = FLIGHTRANGE_LIMIT - ARMED_LIMIT
    flightrange_psx = 375 - 61
    psx_per_axis_unit = flightrange_psx / flightrange_axis
    psx_speedbrake = 61 + (axis_position - ARMED_LIMIT) * psx_per_axis_unit
    return int(psx_speedbrake)


def psx_axis_modify_element(variable, indexes, new_value):
    """Read a variable and update one or more elements.

    E.g for toe brakes or throttles.

    If one axis controls several engines, indexes can be e.g [0,1]
    """
    try:
        psx_value = PSX.get(variable)
    except ValueError:
        logging.info("ERROR: could not get %s from PSX", variable)
        return False
    elems = psx_value.split(';')
    for index in indexes:
        elems[index] = str(new_value)
    variable_new = ';'.join(elems)
    psx_send_and_set(variable, variable_new)
    return True


def throttle_with_reverse_button_get_tla(conf, axis_position, reverse_thrust):
    """Get the thrust lever angle matching this axis position."""
    if reverse_thrust:
        # idle reverse starts at approx -3000, -8925 is full reverse
        tla = axis2psx(axis_position, -3000, -8925, axis_swap=conf['axis swap'])
    else:
        # 0 is idle, 5000 full thrust
        tla = axis2psx(axis_position, 0, 5000, axis_swap=conf['axis swap'])
    return tla

#
# Basic PSX functions
#


def psx_setup():
    """Run when connected to PSX."""
    logging.info("Simulation started")


def psx_teardown():
    """Run when disconnected from PSX."""
    logging.info("Simulation stopped")


def psx_action(this_action, axis_position=None, name=None):  # pylint: disable=too-many-branches,too-many-statements
    """Run PSX action, e.g a button press."""
    global rotary_cache  # pylint: disable=global-variable-not-assigned

    def rotary_push_or_cache(button, variable, increment):
        # This one is a little tricky... when turning the knob we get
        # a button down event per click and then a button up
        # event. However, if we turn the knob quickly, we get a single
        # button down event and then a button up.
        #
        # How to translate that into a PSX delta which is the number
        # of clicks the rotary is turned...?
        #
        # When we get a button down, cache it and log the time
        # When we get a button up, check the cache:
        # If time since button down < N: send a delta of 1 (or -1) to PSX
        # Else: send an increment that is X * time_since_button_down
        # Then: reset cache
        if button == 'down':
            rotary_cache[variable] = time.time()
        elif button == 'up':
            if variable not in rotary_cache:
                return  # should not happen, ignore
            elapsed = time.time() - rotary_cache[variable]
            if elapsed < 0.1:
                psx_push_delta(variable, increment)
            else:
                # A fast turn is ~180 degrees/s == 15 clicks/s
                ROTARY_CLICKS_PER_SECOND = 50
                increment = int(increment * ROTARY_CLICKS_PER_SECOND * elapsed)
                psx_push_delta(variable, increment)
            del rotary_cache[variable]
        else:
            logging.error("Got invalid button event %s", button)

    action_type = this_action['psx action type']
    if action_type == 'PUSH_MOM':
        psx_push_mom(this_action['psx variable'])
    elif action_type == 'PUSH_DELTA1':
        psx_push_delta(this_action['psx variable'])
    elif action_type == 'INCREMENT':
        psx_increment_delta(this_action['psx variable'], this_action['increment'],
                            min_value=this_action['min'], max_value=this_action['max'])
    elif action_type == 'SET':
        psx_set(this_action['psx variable'], this_action['value'])
    elif action_type == 'AXIS_NORMAL':
        # A normal (e.g elevator) axis with optional null zone
        if 'axis nullzone' in action:
            if this_action['axis nullzone'][0] < axis_position < this_action['axis nullzone'][1]:
                logging.info("Nulling %s as it is within nullzone", axis_position)
                axis_position = this_action['axis nullzone'][2]
        psx_value = axis2psx(axis_position, this_action['psx min'],
                             this_action['psx max'], axis_swap=this_action['axis swap'])
        psx_axis_modify_element(this_action['psx variable'], this_action['indexes'], psx_value)
    elif action_type == 'AXIS_SPEEDBRAKE':
        # Special axis handling for speedbrake.
        psx_speedbrake = speedbrake2psx(axis_position, axis_swap=False)
        psx_set('SpdBrkLever', str(psx_speedbrake))
    elif action_type == 'THROTTLE_WITH_REVERSE_BUTTON':
        # Throttle axis with reverser controlled by a button (e.g
        # Thrustmaster Boeing throttle)
        reverse_thrust = False
        if get_joy_byname(name).get_button(this_action['reverse button']) == 1:
            reverse_thrust = True
        tla = throttle_with_reverse_button_get_tla(action, axis_position, reverse_thrust)
        psx_axis_modify_element('Tla', this_action['engine indexes'], tla)
    elif action_type == 'BUTTON_FOR_THROTTLE_WITH_REVERSE_BUTTON':
        # We need to handle if the reverser button is pressed without moving the throttle
        reverse_thrust = this_action['psx reverse']
        # Get the axis position
        axis_position = get_joy_byname(name).get_axis(this_action['axis'])
        # We need the action dict for the axis
        action_axis = CONFIG[name]['axis motion'][this_action['axis']]
        tla = throttle_with_reverse_button_get_tla(action_axis, axis_position, reverse_thrust)
        psx_axis_modify_element('Tla', action_axis['engine indexes'], tla)
    elif action_type == 'BUTTON_ROTARY_TMB':
        # Thrustmaster Boeing Throttle Quadrant rotary knob
        joy = get_joy_byname(name)
        # We assume only one button can be pressed at a time
        if joy.get_button(this_action['modebuttons'][0]) == 1:
            variables = ('McpTurnSpd', 'McpPshSpdSel')
        elif joy.get_button(this_action['modebuttons'][1]) == 1:
            variables = ('McpTurnHdg', 'McpPshHdgSel')
        elif joy.get_button(this_action['modebuttons'][2]) == 1:
            variables = ('McpTurnAlt', 'McpPshAltSel')
        if this_action['direction'] == 'push':
            psx_push_delta(variables[1])
        elif this_action['direction'] == 'cw':
            rotary_push_or_cache(this_action['button'], variables[0], 1)
        elif this_action['direction'] == 'ccw':
            rotary_push_or_cache(this_action['button'], variables[0], -1)

#
# Threads
#


def get_joy_byname(name):
    """Get a joystick object by its name."""
    for _, joy in joysticks.items():
        if joy.get_name() == name:
            return joy
    return None


def psx_thread(name):
    """Handle the PSX connection."""
    global PSX  # pylint: disable=global-statement,global-variable-not-assigned
    logging.info("Thread %s starting", name)
    with Client() as PSX:
        # PSX.logger = lambda msg: logging.info(f"   {msg}")
        PSX.subscribe("id")
        PSX.onResume = psx_setup
        PSX.onPause = psx_teardown
        PSX.onDisconnect = psx_teardown
        try:
            asyncio.run(PSX.connect())
        except KeyboardInterrupt:
            logging.info("\nStopped by keyboard interrupt (Ctrl-C)")


def pygame_thread(name):
    """Wait for and process pygame events."""
    logging.info("Thread %s starting", name)

    global joysticks  # pylint: disable=global-statement,global-variable-not-assigned
    pygame.init()
    for i in range(pygame.joystick.get_count()):
        joystick_name = pygame.joystick.Joystick(i).get_name()
        if joystick_name not in CONFIG.keys():  # pylint: disable=consider-iterating-dictionary
            logging.info("Joystick %s (%s) but not configured", i, joystick_name)
            continue
        logging.info("Joystick %s found: %s", i, joystick_name)
        joy = pygame.joystick.Joystick(i)
        joy.init()
        joysticks[joy.get_instance_id()] = joy

    # Loop forever, fetching events (one or more at a time)
    while True:
        for event in pygame.event.get():
            if event.type == pygame.JOYBUTTONDOWN:
                joystick = joysticks[event.instance_id]
                try:
                    config = CONFIG[joystick.get_name()]['button down'][event.button]
                except KeyError:
                    logging.info("no config for %s button %s down",
                                 joystick.get_name(), event.button)
                    continue
                psx_action(config, name=joystick.get_name())
            elif event.type == pygame.JOYBUTTONUP:
                joystick = joysticks[event.instance_id]
                try:
                    config = CONFIG[joystick.get_name()]['button up'][event.button]
                except KeyError:
                    logging.info("no config for %s button %s up", joystick.get_name(), event.button)
                    continue
                psx_action(config, name=joystick.get_name())


def pygame_axis_thread(name):  # pylint: disable=too-many-branches
    """Poll managed axes at fixed frequency.

    Cache sent values and inhibit send if new value is too close to old value.
    """
    logging.info("Thread %s starting", name)

    last_axis_positions = {}

    axis_tolerance = 0.01  # global for now

    global joysticks  # pylint: disable=global-statement,global-variable-not-assigned

    while True:  # pylint: disable=too-many-nested-blocks
        start = time.time()
        for _, joy in joysticks.items():
            name = joy.get_name()
            if name not in CONFIG:
                logging.info("Joystick %s not managed", name)
                continue
            if 'axis motion' not in CONFIG[name]:
                logging.info("Joystick %s has no managed axis", name)
                continue
            for axis, config in CONFIG[name]['axis motion'].items():
                axis_position = joy.get_axis(axis)
                try:
                    last_axis_position = last_axis_positions[name][axis]
                except KeyError:
                    last_axis_position = None
                if last_axis_position is not None:
                    movement = abs(axis_position - last_axis_position)
                    if movement > axis_tolerance:
                        logging.info("Axis movement detected by polling: %s is %s",
                                     axis, axis_position)
                        psx_action(config, axis_position, name=name)
                        if name not in last_axis_positions:
                            last_axis_positions[name] = {}
                        last_axis_positions[name][axis] = axis_position
                else:
                    psx_action(config, axis_position, name=name)
                    if name not in last_axis_positions:
                        last_axis_positions[name] = {}
                    last_axis_positions[name][axis] = axis_position

        elapsed = time.time() - start
        sleep = (1.0 / MAX_PSX_HZ) - elapsed
        if sleep < 0:
            logging.info("TIMEOUT: axis poll thread not keeping up. elapsed=%.3fs", elapsed)
        else:
            time.sleep(sleep)


if __name__ == "__main__":
    log_format = "%(asctime)s: %(message)s"
    logging.basicConfig(format=log_format, level=logging.INFO,
                        datefmt="%H:%M:%S")
    psx_thread = threading.Thread(target=psx_thread, args=("PSX",), daemon=True)
    psx_thread.start()

    # We need to subscribe to PSX variables included in the config
    psx_variables = set()
    for device, data in CONFIG.items():
        for event_type, data in data.items():
            for ident, action in data.items():
                if 'psx variable' in action:
                    psx_variables.add(action['psx variable'])
    logging.info("Subscribing to PSX variables %s", psx_variables)
    for psx_variable in psx_variables:
        PSX.subscribe(psx_variable)

    logging.info("PSX subscribed variables: %s", ', '.join(PSX.variables.keys()))

    while True:
        logging.info("Waiting for PSX connection...")
        if PSX is not None:
            break
        time.sleep(1.0)
    logging.info("Connected to PSX!")
    time.sleep(1.0)

    PSX.subscribe("version", lambda key, value:
                  logging.info("Connected to PSX %s as client #%s", value, PSX.get('id')))

    logging.info("Starting pygame thread.")

    pygame_thread = threading.Thread(target=pygame_thread, args=("pygame",), daemon=True)
    pygame_thread.start()

    while len(joysticks) <= 0:
        logging.info("Waiting for joysticks to be initialized...")
        time.sleep(1.0)

    pygame_axis_thread = threading.Thread(target=pygame_axis_thread,
                                          args=("pygameaxis",), daemon=True)
    pygame_axis_thread.start()

    while True:
        try:
            time.sleep(1.0)
        except KeyboardInterrupt:
            logging.info("\nStopped by keyboard interrupt (Ctrl-C)")
            sys.exit()
