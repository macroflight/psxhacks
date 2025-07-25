# psx.py: Python connector between a PSX Main Server and a database
# structure that allows for easy variable tracking. Also provides the basic
# MCDU support.

# Jeroen Hoppenbrouwers <hoppie@hoppie.nl> July 2025

VERSION = "AA-beta-05"


##### Modules ############################################################

import asyncio


##### PSX Client #########################################################


class Client():
  """ Normally there is no need to have more than one PSX Client connected
  to the same simulator, but who knows what people invent? """

  def __init__(self):
    # PSX network memory.
    self.reader = None
    self.writer = None
    self.onConnect    = lambda: None  # Replace these with functions.
    self.onPause      = lambda: None
    self.onResume     = lambda: None
    self.onDisconnect = lambda: None

    # PSX variables memory.
    self.lexicon   = dict()    # (key, name)
    self.variables = dict()    # (key, value)
    self.callbacks = dict()    # (key, [callback, callback, ...])

    # General memory.
    self.logger = lambda msg: None  # Replace this with a function.
  # init()


  def __enter__(self):
    """ Nifty helper to allow "with psx" contexts. """
    return self
  # enter()


  def __exit__(self, exc_type, exc_val, exc_tb):
    """ Nifty helper to allow "with psx" contexts. """
    pass
  # exit()


  async def connect(self, host="127.0.0.1", port=10747):
    """ Keep trying forever to open a connection and run it. """

    while True:
      await asyncio.sleep(2)
      self.logger(f"Connecting to PSX Main Server at {host}:{port}...")
      try:
        self.reader, self.writer = await asyncio.open_connection(host, port)
      except OSError:
        self.logger("Oops, that failed. Has the PSX Main Server been started?")
        self.logger("Will retry in 10 seconds")
        await asyncio.sleep(10)
        continue

      # We got a connection. Respond to incoming data in a loop, until the
      # connection is closed ("exit"), breaks (EOF), or an exception occurs.
      try:
        self.onConnect()
        firstConnect = True
        while True:
          line = await self.reader.readline()
          if self.reader.at_eof():
            break                  # Main Server link went away.
          key, sep, value = line.decode().strip().partition("=")
          # Some keys are relevant at the connection level.
          if key=="load1" and firstConnect:
            self.logger(f"Loaded {len(self.lexicon)} lexicon entries")
            # Suppress "sim pause" on first connect: we're not up, yet.
            firstConnect = False
          elif key=="load1":
            self.onPause()
          elif key=="load3":
            self.onResume()
          elif key=="exit":
            break                  # Main Server is disconnecting us.
          elif key[0]=="L":
            self._lex(key, value)   # This is a lexicon mapping.
          else:
            self._set(key, value)   # This is a normal Q-code.
        self.logger("Disconnected by PSX Main Server")
        # We can try to cleanly disconnect as long as we don't close.
        # PSX listens for a little while after sending us "exit".
        self.onDisconnect()
        self.writer.close()
        await self.writer.wait_closed()
      except asyncio.exceptions.CancelledError:
        # The connector task is being canceled; typically by a Ctrl-C.
        self.logger("Disconnecting from PSX Main Server")
        self.onDisconnect()
        # Wait a little while to assure PSX commands from the onDisconnect()
        # get all flushed.
        await asyncio.sleep(1)
        # Let PSX know we're quitting.
        self.send("exit")
        self.writer.close()
        await self.writer.wait_closed()
        break # to stop trying to connect.
  # connect()


  def send(self, key, value=None):
    """ Submit the given key,value combo to the PSX Main Server. """
    if self.writer is None:
      # Nothing yet.
      self.logger(f"TX: no connection yet, ignored ({key})")
      return
    if key=="demand":
      # The "demand" key is a command, and its value is the variable.
      mapped = self._mapToQ(value)
      if mapped is not None:
        value = mapped  # Found a matching Lexicon mapping.
    mapped = self._mapToQ(key)
    if mapped is not None:
      txkey = mapped    # Found a matching Lexicon mapping.
    else:
      txkey = key
    if value is None:
      self.logger(f"TX: ({key}) {txkey}")
      self.writer.write(f"{txkey}\n".encode())
    else:
      self.logger(f"TX: ({key}) {txkey}={value}")
      self.writer.write(f"{txkey}={value}\n".encode())
  # send()


  ##### PSX Variable Register ############################################

  def subscribe(self, key, cb=None):
    """ Add the key to the monitor list and optionally
        the given function to the key's callback list. """
    self.logger(f"Subscription on {key}")
    if key not in self.variables:
      self.variables[key] = None    # Do not overwrite already known values.
    if cb != None:
      if key in self.callbacks:
        self.callbacks[key].append(cb)
      else:
        self.callbacks[key] = [cb]    # Remember to create a list.
  # subscribe()


  def get(self, key):
    """ Retrieve the value stored on the given key. """
    if key in self.variables:
      return self.variables[key]
    else:
      self.logger(f"Get {key} which is unknown; trying to return empty string")
      return ""
  # get()


  def _lex(self, key, name):
    """ Process a lexicon entry. Gotta Catch 'em All! """
    # Lexicon key syntax: "Li35(E)". We want the "i35" part.
    q, _, _ = key[1:].partition("(")
    self.lexicon["Q"+q] = name
  # _lex()


  def _mapToQ(self, name):
    """ Translate a lexicon name to the Q code, for easy transmitting. """
    for k,n in self.lexicon.items():
      if name==n:
        return k
    else:
      return None
  # _mapToQ()


  def _set(self, key, value):
    """ Set the new key to the given value and call the subscribers.
        Only process variables that have been subscribed to. """
    if key in self.lexicon:
      rxkey = self.lexicon[key]    # Rewrite the Q code with the name.
    else:
      rxkey = key
    if rxkey in self.variables:
      self.logger(f"RX: ({rxkey}) {key} = {value}")
      self.variables[rxkey] = value
      # See whether there are any callbacks registered on this key.
      if rxkey in self.callbacks:
        for callback in self.callbacks[rxkey]:
          # Call all subscribers with the key and new value as parameters.
          # The key is very useful for multi-key-handling callbacks.
          callback(rxkey, value)
  # _set()


# class Client


##### PSX MCDU HEAD ######################################################

class MCDU:
  """ Each prompt accesses a different subsystem. Even if the same script
      handles multiple subsystems, they have different prompts and different
      internal states (last known page buffer). So for each prompt we need a
      different head, and this for multiple MCDUs, too.

      NOTE: these are PSX translator heads. They have no more functionality
      than a real ARINC 739A MCDU (excluding its backup functions). To add
      complete subsystem behaviour, you need to add code. See the MCDU.py
      library for a nice solution.
  """

  def __init__(self, location, menuSide, menuRow, menuText, eventFunc):
    """ Set up a MCDU head that keeps state between PSX connections, but
        does not immediately plug in. You can talk to this head but it
        does nothing with the simulator until it is plugged in. """
    assert location in ["L", "C", "R"], location
    assert menuSide in ["L", "R"], menuSide
    assert 1<=menuRow<=6, menuRow
    assert callable(eventFunc)
    self.location = location
    if menuSide=="L":
      self.menuSide = "1"
    else:
      self.menuSide = "2"
    self.menuRow  = str(menuRow)
    self.menuText = menuText
    # Character buffer: holds the complete matrix of the display.
    self.charBuf = [ [" " for c in range(0,24)] for r in range(0,14) ]
    # Font size buffer: holds the complete matrix of the display.
    self.fontBuf = [ ["-" for c in range(0,24)] for r in range(0,14) ]
    # Color buffer: holds the complete matrix of the display.
    self.colorBuf = [ ["w" for c in range(0,24)] for r in range(0,14) ]

    # What to call in case of an MCDU event.
    self.fireEvent = eventFunc

    # We don't know our PSX server yet. Wait for plugin_to().
    self.server         = None
    self.haveSubscribed = False

    # MCDU subsystem and heads start out idle.
    self.subsysState = "logoff"
    self.headState   = "inactive"

  # init()


  def plugin_to(self, server):
    """ Connect a MCDU head to the PSX system bus.
        Feed it the PSX server connection of the aircraft. """
    self.server = server
    self.server.logger(f"Plugging in MCDU {self.location}")
    # Create prompt on the right spot on the flight deck.
    self.server.send("cdu"+self.location,
                     self.menuSide+self.menuRow+self.menuText)
    if not self.haveSubscribed:
      # Only subscribe once, there is no unsubscribe yet. Maybe never.
      self.server.subscribe("CdusActSubsys", self._subsystem)
      self.server.subscribe("KeybCdu"+self.location, self._keypress)
      self.haveSubscribed = True
  # plugin_to()


  def unplug(self):
    """ Disconnect a MCDU head from the PSX system bus. """
    if self.server is None:
      return
    self.server.logger(f"Unplugging MCDU {self.location}")
    # Remove the prompt from the PSX MCDU display and disconnect.
    self.server.send("cdu"+self.location, self.menuSide+self.menuRow)
    self.server = None
    # MCDU subsystem and head return to idle.
    self.subsysState = "logoff"
    self.headState   = "inactive"
  # unplug()


  def paint(self, row, col, fsize, color, text=""):
    """ Paint a text string on the MCDU head. This is PSX-specific but the
    API is aligned with general ARINC 739A conventions.
    NOTE: Only the NG FMC extension of PSX supports LCD (color) MCDUs. """
    assert 0<=row<14, row
    assert 0<=col<25, col
    assert fsize in ['large','small']
    assert color in ['black','cyan','red','yellow','green',
                     'magenta','amber','white']
    assert (col+len(text))<25, f"MCDU column overflow: '{text}'"

    if self.headState!="active":
      # The subsystem should not have issued paint commands, as it should
      # know that the head is on hold.
      self.server.logger(f"(head {self.location} inactive, ignored paint)")
      return

    # ARINC 739A does not use printable characters for these. But it is much
    # more convenient to use lowercase characters instead of nonprintables.
    """
    A through Z
    0 through 9
    < > . , : / % + - = * _ ( ) #
    o : degrees
    b : box
    u : up arrow
    d : down arrow
    l : left arrow
    r : right arrow
    t : triangle
    """
    # Replace a space with a _. PSX requires this. Maybe more, later.
    table = str.maketrans({
      " ":"_"
    })
    text = text.translate(table)

    """
    If you send an empty string, the white default color is used.
    a = amber
    b = blue -- looks like black?
    c = cyan
    g = green
    m = magenta
    r = red
    w = white
    y = gray background -- where is yellow?
    """
    table = {
      "black":"b", "cyan":"c", "red":"r", "yellow":"y", "green":"g",
      "magenta":"m","amber":"a","white":"w"
    }
    color = table[color]

    # Update the local character buffer.
    self.charBuf[row][col:col+len(text)] = text
    # Update the local font size buffer.
    if fsize=="large":
      fsize = "+"
    else:
      fsize = "-"
    self.fontBuf[row][col:col+len(text)] = fsize*len(text)
    # Update the local color buffer.
    self.colorBuf[row][col:col+len(text)] = color*len(text)

    # Create the PSX network strings.
    line  = "".join(self.charBuf[row]+self.fontBuf[row])
    cline = "".join(self.colorBuf[row])

    # Calculate the PSX row keys.
    if row==0:
      key  = self.location + "cduTitle"
      ckey = "CduColTi" + self.location
    elif row==13:
      key  = self.location + "cduScrPad"
      ckey = "CduColSp" + self.location
      # PSX scratch pad does not support small font; drop last half of line.
      line = line[0:24]
    else:
      if row%2!=0:
        # Odd rows.
        key  = self.location + "cduLine" + str(int((row+1)/2)) + "s"
        ckey = "CduCol" + str(int((row+1)/2)) + "s" + self.location
      else:
        # Even rows.
        key  = self.location + "cduLine" + str(int(row/2)) + "b"
        ckey = "CduCol" + str(int((row+1)/2)) + "b" + self.location

    # Send the updated full lines to PSX.
    # TODO This can be optimized with a buffer difference analysis,
    # but probably this isn't necessary.
    # Consider "block" and "release" blocking instructions instead of a
    # timeout. "block" can even be part of clear().
    self.server.send(key, line)
    self.server.send(ckey, cline)
  # paint()


  def clear(self):
    """ Straightforward Clear Display. """
    for r in range(0,14):
      self.paint(r, 0, "large", "white", " "*24)
  # clear()


  def blank(self, time=400):
    """ Brief (about 400 ms) screen blank, does not clear display buffer.
    This is often useful to make a clean, update-at-once appearance.
    PSX itself will time the blank; nothing freezes. """
    self.server.send("BlankTimeCdu"+self.location, time)
  # blank()


  def annun(self):
    # TODO
    """
    Each CDU keyboard has a dedicated Qi bitmask for its five lights:

    LightsCduL (Qi86)
    LightsCduC (Qi87)
    LightsCduR (Qi88)

    bit 0001 : EXEC light contact closed by CDU
    bit 0002 : DSPY light contact closed by CDU
    bit 0004 : FAIL light contact closed by CDU
    bit 0008 : MSG light contact closed by CDU
    bit 0016 : OFST light contact closed by CDU
    bit 8192 : all light contacts closed by MD & T
    """
  # annun()


  def _subsystem(self, key, value):
    """ PSX subsystem and MCDU head state changes are complicated.
        This method tracks PSX and directs the state of this MCDU head and
        the associated subsystem. """
    # Value is LLRRCC where each two chars are (side,subsys).
    # Side is: 1:left row nr; 2:right row nr; else:intern PSX.
    # Internal PSX subsystems: 11=FMC, 12=ACARS, 21=EFIS, 22=EICAS, 23=CTL.
    if self.location=="L":
      sel = value[0:2]
    elif self.location=="R":
      sel = value[2:4]
    else:
      sel = value[4:6]

    # The subsystem toggles between logon and logoff.
    # The MCDU head toggles between active and inactive. This causes events,
    # but no subsystem state change.
    if self.menuRow==sel[1]:
      if self.menuSide==sel[0]:
        # Our subsystem selected.
        if self.subsysState=="logoff":
          self.headState   = "active"
          self.subsysState = "logon"
          self.fireEvent(self, "logon")
        elif self.headState=="inactive":
          self.headState   = "active"
          self.fireEvent(self, "resume")
      else:
        # MENU selected.
        if self.subsysState=="logon":
          self.headState = "inactive"
          self.fireEvent(self, "hold")
    else:
      # Another subsystem selected.
      if self.subsysState!="logoff":
        self.headState   = "inactive"
        self.subsysState = "logoff"
        self.fireEvent(self, "logoff")
  # _subsystem()


  def _keypress(self, key, value):
    """ Keypress received from a PSX MCDU.
    key holds the originating MCDU, value holds the PSX key number.
    Note that this has already been filtered for correct MCDU only, but not
    for active/inactive subsystem.
    """
    if self.headState=="active":
      # All keys except CLR and ATC fire a one-shot keypress event. CLR and
      # ATC fire a press event and a REL-ease event, as they can in ARINC
      # 739A have a repeat bit set. To not complicate the implementation, a
      # repeated CLR is reported up as CLR+ and ATC as ATC+.
      # TODO can store this map once, of course.
      map = {
        -1:"REL",
        0:"0", 1:"1", 2:"2", 3:"3", 4:"4", 5:"5", 6:"6", 7:"7", 8:"8", 9:"9",
        10:"A", 11:"B", 12:"C", 13:"D", 14:"E", 15:"F", 16:"G", 17:"H",
        18:"I", 19:"J", 20:"K", 21:"L", 22:"M", 23:"N", 24:"O", 25:"P",
        26:"Q", 27:"R", 28:"S", 29:"T", 30:"U", 31:"V", 32:"W", 33:"X",
        34:"Y", 35:"Z", 36:"SP", 37:"DEL", 38:"/", 39:"CLR", 40:"EXEC",
        41:"1L", 42:"2L", 43:"3L", 44:"4L", 45:"5L", 46:"6L",
        47:"MENU", 48:"NAVRAD", 49:"PREV", 50:"NEXT",
        51:"1R", 52:"2R", 53:"3R", 54:"4R", 55:"5R", 56:"6R",
        57:"INITREF", 58:"RTE", 59:"DEPARR", 60:"ATC", 61:"VNAV",
        62:"FIX", 63:"LEGS", 64:"HOLD", 65:"FMCCOMM", 66:"PROG",
        67:".", 68:"+/-"
      }
      keypress = map[int(value)]
      if keypress=="REL":
        # Key released. Cancel the pending repeat keystroke task, if any.
        try:
          self.repeat.cancel()
        except AttributeError:
          pass
      elif keypress=="CLR" or keypress=="ATC":
        # Set up a future task to fire a repeat event in 1.0 seconds unless
        # canceled before expiration. Embed the task into this method to
        # emphasize its locality; it is not meant to be used elsewhere.
        async def repeat(keypress):
          await asyncio.sleep(1.0)
          self.fireEvent(self, "keypress", keypress)
        self.repeat = asyncio.create_task(repeat(keypress+"+"))
      # Send the keypress event to the subsystem.
      self.fireEvent(self, "keypress", keypress)
  # _keypress()

# class MCDU


##### SELF TESTS #########################################################

""" The self test/demo is run when you execute this module as if it were a
    toplevel script. """

if __name__ == "__main__":
  """ Try to connect to the PSX Main Server and see what happens. """

  import datetime
  import time

  # Define a callback to cause some activity on the upper EICAS.
  def TimeEarth(key, value):
    epoch = int(value)/1000
    zulu = datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc)
    psx.send("FreeMsgC", zulu.strftime("%b %d %H:%M:%SZ").upper())
  # TimeEarth()

  def GroundSpeed(key, value):
    print("PSX ground speed:", value)
  # GroundSpeed()

  def setup():
    print("Simulation started")
    mcduL.plugin_to(psx)
    mcduR.plugin_to(psx)
    psx.send("FreeMsgW","FLYING PYTHON")
    # GroundSpeed is a DEMAND variable that needs to be requested from PSX.
    psx.send("demand","GroundSpeed")
  # setup()

  def teardown():
    print("Simulation stopped")
    mcduL.unplug()
    mcduR.unplug()
    psx.send("FreeMsgW","")
    psx.send("FreeMsgC","")
  # teardown()

  def paintPage(mcdu):
    """ Paint the demo page on the MCDU that requests it. """

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
    mcdu.paint( 0, 0, L, A, "        BAR MENU        ")
    mcdu.paint( 1, 0, S, C, "--------CAPTAIN---------")
    mcdu.paint( 2, 0, L, C, "<BOURBON         BURGER>")
    mcdu.paint( 4, 0, L, C, "<SCOTCH             DOG>")
    mcdu.paint( 6, 0, L, C, "<BEER           BREKKIE>")
    mcdu.paint( 7, 0, S, W, "-----FIRST OFFICER------")
    mcdu.paint( 8, 0, L, W, "<ORANGE J    MAC+CHEESE>")
    mcdu.paint(10, 0, L, W, "<SODA              SOUP>")
    mcdu.paint(12, 0, L, W, "<H2O           SANDWICH>")
  # paintPage()


  def mcduEvent(mcdu, type, value=None):
    """ Called by an MCDU when it has something to report or request. """
    print(f"MCDU event from {mcdu.location}: {type}={value}")
    if type in ["logon", "resume"]:
      paintPage(mcdu)
    elif type=="keypress":
      if value in ["1L","2L","3L","4L","5L","6L",
                   "1R","2R","3R","4R","5R","6R"]:
        # Write a message on the scratch pad.
        mcdu.paint(13, 0, "large", "magenta", "COMING UP RIGHT NOW")
      elif value=="CLR":
        # Erase the scratch pad.
        mcdu.paint(13, 0, "large", "white", " "*24)
    else:
      print(f"Unhandled MCDU event from {mcdu.location}: {type}={value}")
  # mcduEvent()


  ##### MAIN #############################################################

  print(f"Self-test for the PSX Client Module, version {VERSION}\n")

  # Create a PSX Client and install a custom logger.
  with Client() as psx:
    psx.logger = lambda msg: print(f"   {msg}")

    # Register some PSX variables we are interested in, and some callbacks.
    # NOTE: These subscriptions are registered in the connector module, but
    # until the PSX connection is activated, nothing will happen.
    psx.subscribe("id")
    psx.subscribe("version", lambda key, value:
      print(f"Connected to PSX {value} as client #{psx.get('id')}"))

    # A simple thing to play with EICAS.
    psx.subscribe("TimeEarth", TimeEarth)

    # Demand variable demo. This needs to be requested in setup().
    # Needs PSX >= 10.156 (June 2022).
    psx.subscribe("GroundSpeed", GroundSpeed)

    # Server-related action callbacks. Note that these do more than just the
    # MCDU head setup/teardown. Otherwise they could be direct mcdu.methods.
    psx.onResume     = setup
    psx.onPause      = teardown
    psx.onDisconnect = teardown

    # Create two MCDU heads, on the Left and Right MCDU, at L4.
    mcduL = MCDU("L", "L", 4, "<BAR", mcduEvent)
    mcduR = MCDU("R", "L", 4, "<BAR", mcduEvent)

    try:
      # Make and maintain a PSX Main Server connection until stopped.
      # Only here something actually happens!
      asyncio.run(psx.connect())
    except KeyboardInterrupt:
      print("\nStopped by keyboard interrupt (Ctrl-C)")

# EOF
