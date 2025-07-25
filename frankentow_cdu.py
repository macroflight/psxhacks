# pylint: disable=line-too-long,missing-module-docstring,missing-class-docstring,too-many-instance-attributes,protected-access,f-string-without-interpolation,fixme,invalid-name,missing-function-docstring,unused-variable,too-many-locals,too-many-statements,too-many-branches
import asyncio
import time

from psx import Client, MCDU


class FrankenTow():
    def __init__(self):
        self.psx = None
        self.page = None

        self.move_distance = float(1.0)
        self.turn_amount = int(1)
        self.scratchpad_text = ''

        self.mcduL = None
        self.mcduR = None

        self.basictug_lateral_mode = 'straight'
        self.basictug_longitudinal_mode = 'stop'
        self.basictug_turnradius = 45.0  # meters
        self.basictug_speed = 1.0  # m/s

    def setup(self):
        print("Simulation started")
        self.psx.send("name", "TOW-CDU:FRANKEN.PY towing services CDU interface")
        self.mcduL.plugin_to(self.psx)
        self.mcduR.plugin_to(self.psx)
    # setup()

    def teardown(self):
        print("Simulation stopped")
        self.mcduL.unplug()
        self.mcduR.unplug()
    # teardown()

    async def send_and_set(self, key, value):
        self.psx.send(key, value)
        self.psx._set(key, value)

    def paintTowPage(self, mcdu):
        # Allow PSX enough time to paint <ACT. Cosmetical only.
        time.sleep(0.5)

        # Handy local shortcuts.
        A = "amber"
        B = "black"   # TODO PSX doc says blue?
        C = "cyan"
        G = "green"
        M = "magenta"
        R = "red"
        W = "white"
        Y = "yellow"  # TODO PSX displays as white on gray?
        L = "large"
        S = "small"

        mcdu.clear()
        mcdu.paint(0, 0, L, A, "    FRANKENTOW MENU     ")
        mcdu.paint(1, 0, L, C, "                        ")
        mcdu.paint(2, 0, L, C, "<SLEW                   ")
        mcdu.paint(4, 0, L, C, "<TUG                    ")
        mcdu.paint(6, 0, S, C, "                        ")
        mcdu.paint(7, 0, S, C, "                        ")
        mcdu.paint(8, 0, S, C, "                        ")
        mcdu.paint(10, 0, S, C, "                        ")
        mcdu.paint(12, 0, S, C, "                        ")

        self.page = 'tow'
    # paintTowPage()

    def paintSlewPage(self, mcdu):

        time.sleep(0.5)

        # Handy local shortcuts.
        A = "amber"
        B = "black"   # TODO PSX doc says blue?
        C = "cyan"
        G = "green"
        M = "magenta"
        R = "red"
        W = "white"
        Y = "yellow"  # TODO PSX displays as white on gray?
        L = "large"
        S = "small"

        active = None

        mcdu.clear()
        mcdu.paint(0, 0, L, A, "  FRANKENTOW SLEW MENU  ")
        mcdu.paint(2, 0, L, C, "<LEFT             RIGHT>")
        mcdu.paint(3, 0, S, C, "             DISTANCE(M)")
        mcdu.paint(4, 0, L, C, f"<FORWARD           {self.move_distance:5.1f}")
        mcdu.paint(6, 0, L, C, "<BACK                   ")
        mcdu.paint(7, 0, S, C, "              ANGLE(DEG)")
        mcdu.paint(8, 0, L, C, f"                     {self.turn_amount:3d}")
        mcdu.paint(10, 0, L, C, "<NOSE LEFT   NOSE RIGHT>")

        self.page = 'slew'
    # paintTowPage()

    def paintTugPage(self, mcdu):
        time.sleep(0.5)

        # Handy local shortcuts.
        A = "amber"
        B = "black"   # TODO PSX doc says blue?
        C = "cyan"
        G = "green"
        M = "magenta"
        R = "red"
        W = "white"
        Y = "yellow"  # TODO PSX displays as white on gray?
        L = "large"
        S = "small"

        spd = f"{self.basictug_speed:3.1f}"

        l2 = None
        l4 = None
        l6 = None

        if self.basictug_longitudinal_mode == 'stop':
            l2 = f"<PULL FORWARD      {spd}"
            l4 = f"<*STOP*                 "
            l6 = f"<PUSH BACK              "
        elif self.basictug_longitudinal_mode == 'forward':
            l2 = f"<*PULL FORWARD*    {spd}"
            l4 = f"<STOP                   "
            l6 = f"<PUSH BACK              "
        elif self.basictug_longitudinal_mode == 'backward':
            l2 = f"<PULL FORWARD      {spd}"
            l4 = f"<STOP                   "
            l6 = f"<*PUSH BACK*            "
        else:
            print("Invalid tug_longitudinal_mode {self.basictug_longitudinal_mode}")
            l2 = f"<PULL FORWARD      {spd}"
            l4 = f"<STOP                   "
            l6 = f"<PUSH BACK              "

        l10 = None
        l12 = None

        tr = f"{self.basictug_turnradius:4.0f}"
        if self.basictug_lateral_mode == 'straight':
            l10 = f"<NOSE LEFT   NOSE RIGHT>"
            l12 = f"<*STRAIGHT*         {tr}"
        elif self.basictug_lateral_mode == 'noseleft':
            l10 = f"<*NOSE LEFT* NOSE RIGHT>"
            l12 = f"<STRAIGHT           {tr}"
        elif self.basictug_lateral_mode == 'noseright':
            l10 = f"<NOSE LEFT *NOSE RIGHT*>"
            l12 = f"<STRAIGHT           {tr}"

        mcdu.clear()
        mcdu.paint(0, 0, L, A, "  FRANKENTOW TUG MENU  ")
        mcdu.paint(1, 0, S, C, "            SPEED (M/S)")
        mcdu.paint(2, 0, L, C, l2)
        mcdu.paint(4, 0, L, C, l4)
        mcdu.paint(6, 0, L, C, l6)
        mcdu.paint(10, 0, L, C, l10)
        mcdu.paint(11, 0, S, C, "        TURN RADIUS (M)")
        mcdu.paint(12, 0, L, C, l12)
        self.page = 'tug'
    # paintTowPage()

    def mcduEvent(self, mcdu, type, value=None):  # pylint: disable=redefined-builtin
        """ Called by an MCDU when it has something to report or request. """
        print(f"MCDU event from {mcdu.location}: {type}={value}")
        if type in ["logon", "resume"]:
            self.paintTowPage(mcdu)
        elif type == "keypress":
            if value == "CLR":
                # Erase the scratch pad.
                mcdu.paint(13, 0, "large", "white", " " * 24)
                self.scratchpad_text = ''
            elif self.page == 'tow':
                if value == '1L':
                    self.paintSlewPage(mcdu)
                if value == '2L':
                    self.paintTugPage(mcdu)
            elif self.page == 'slew':
                if value == '1L':
                    self.psx.send(f"addon=FRANKENTOW:SLEW:LEFT:{self.move_distance}")
                elif value == '1R':
                    self.psx.send(f"addon=FRANKENTOW:SLEW:RIGHT:{self.move_distance}")
                elif value == '2L':
                    self.psx.send(f"addon=FRANKENTOW:SLEW:FORWARD:{self.move_distance}")
                elif value == '3L':
                    self.psx.send(f"addon=FRANKENTOW:SLEW:BACKWARD:{self.move_distance}")
                elif value == '5L':
                    self.psx.send(f"addon=FRANKENTOW:SLEW:NOSELEFT:{self.turn_amount}")
                elif value == '5R':
                    self.psx.send(f"addon=FRANKENTOW:SLEW:NOSERIGHT:{self.turn_amount}")
                elif value == '2R':
                    self.move_distance = float(self.scratchpad_text)
                    mcdu.paint(13, 0, "large", "white", " " * 24)
                    self.scratchpad_text = ''
                    self.paintSlewPage(mcdu)
                elif value == '4R':
                    self.turn_amount = int(self.scratchpad_text)
                    mcdu.paint(13, 0, "large", "white", " " * 24)
                    self.scratchpad_text = ''
                    self.paintSlewPage(mcdu)
                elif value in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '.']:
                    # allow entering of numbers
                    self.scratchpad_text += str(value)
                    mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)
                else:
                    print(f"Not allowed on slew page: {value}")
            elif self.page == 'tug':
                if value == '1L':
                    self.basictug_longitudinal_mode = 'forward'
                    self.psx.send(f"addon=FRANKENTOW:BASICTUG:FORWARD:{self.basictug_speed}")
                    self.paintTugPage(mcdu)
                elif value == '2L':
                    self.basictug_longitudinal_mode = 'stop'
                    self.psx.send(f"addon=FRANKENTOW:BASICTUG:STOP")
                    self.paintTugPage(mcdu)
                elif value == '3L':
                    self.basictug_longitudinal_mode = 'backward'
                    self.psx.send(f"addon=FRANKENTOW:BASICTUG:BACKWARD:{self.basictug_speed}")
                    self.paintTugPage(mcdu)
                elif value == '5L':
                    self.basictug_lateral_mode = 'noseleft'
                    self.psx.send(f"addon=FRANKENTOW:BASICTUG:NOSELEFT:{self.basictug_turnradius}")
                    self.paintTugPage(mcdu)
                elif value == '6L':
                    self.basictug_lateral_mode = 'straight'
                    self.psx.send(f"addon=FRANKENTOW:BASICTUG:STRAIGHT")
                    self.paintTugPage(mcdu)
                elif value == '5R':
                    self.basictug_lateral_mode = 'noseright'
                    self.psx.send(f"addon=FRANKENTOW:BASICTUG:NOSERIGHT:{self.basictug_turnradius}")
                    self.paintTugPage(mcdu)
                elif value == '1R':
                    self.basictug_speed = float(self.scratchpad_text)
                    mcdu.paint(13, 0, "large", "white", " " * 24)
                    self.scratchpad_text = ''
                    self.paintTugPage(mcdu)
                elif value == '6R':
                    self.basictug_turnradius = max(26, int(self.scratchpad_text))
                    mcdu.paint(13, 0, "large", "white", " " * 24)
                    self.scratchpad_text = ''
                    self.paintTugPage(mcdu)
                elif value in ['0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '.']:
                    # allow entering of numbers
                    self.scratchpad_text += str(value)
                    mcdu.paint(13, 0, "large", "magenta", self.scratchpad_text)
            else:
                pass
        else:
            print(f"Unhandled MCDU event from {mcdu.location}: {type}={value}")
    # mcduEvent()

    def run(self):
        with Client() as self.psx:
            self.psx.logger = lambda msg: print(f"   {msg}")

            self.psx.subscribe("id")
            self.psx.subscribe(
                "version", lambda key, value:
                print(f"Connected to PSX {value} as client #{self.psx.get('id')}"))

            self.psx.onResume = self.setup
            self.psx.onPause = self.teardown
            self.psx.onDisconnect = self.teardown

            # Create two MCDU heads, on the Left and Right MCDU, at L4.
            self.mcduL = MCDU("L", "L", 4, "<FTOW", self.mcduEvent)
            self.mcduR = MCDU("R", "L", 4, "<FTOW", self.mcduEvent)

            try:
                asyncio.run(self.psx.connect())
            except KeyboardInterrupt:
                print("\nStopped by keyboard interrupt (Ctrl-C)")


if __name__ == "__main__":
    FrankenTow().run()
