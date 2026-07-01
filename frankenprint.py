"""FrankenPrint — forward PSX virtual printer output to an Epson TM-T20iii.

PSX printer variable used:

  PrinterText (Qs119)  Text content of the current print job (up to 24 576 chars).
                       Lines are delimited by '^'.  PSX clears this variable
                       automatically a few seconds after setting it.

PSX manages the printer busy state (Qi115) independently; this addon only
needs to observe Qs119 and print whenever it becomes non-empty.

Printer connection options:

  Windows printer name  Default (auto-selected when --printer is omitted).
                        Requires pywin32 (pip install pywin32).  Use the name
                        shown in Windows Settings → Bluetooth & devices →
                        Printers, e.g. "EPSON TM-T20III".

  COM port              e.g. COM3 — use when the Epson APD has assigned a
                        virtual serial port to the printer.

  TCP address           e.g. 192.168.1.10:9100 — for Ethernet-connected
                        printers with raw-socket printing enabled.
"""
import argparse
import asyncio
import logging
import socket
import sys
from typing import Callable, Optional

import psx

try:
    import win32print as _win32print  # pylint: disable=import-error
except ImportError:
    _win32print = None

__MYNAME__ = 'frankenprint'
__MY_CLIENT_ID__ = 'PRINTER'
__MY_DISPLAY_NAME__ = 'FrankenPrinter'
__MY_DESCRIPTION__ = 'Print PSX virtual printer output on an Epson TM-T20iii'

# ESC/POS command sequences for the Epson TM-T20iii
_ESC_INIT = b'\x1b@'       # ESC @ — initialise / reset the printer
_ESC_FEED = b'\x1bd\x04'   # ESC d 4 — feed 4 lines before cutting
_GS_CUT = b'\x1dV\x01'    # GS V 1 — partial cut

_TEST_MESSAGE = "FrankenPrint test\nConnected to PSX OK\n"


def _make_win32_writer(printer_name: str) -> Callable[[bytes], None]:
    """Return a callable that sends raw ESC/POS bytes to a named Windows printer."""
    def _write(data: bytes) -> None:
        handle = _win32print.OpenPrinter(printer_name)
        try:
            _win32print.StartDocPrinter(handle, 1, ("FrankenPrint", None, "RAW"))
            _win32print.StartPagePrinter(handle)
            _win32print.WritePrinter(handle, data)
            _win32print.EndPagePrinter(handle)
            _win32print.EndDocPrinter(handle)
        finally:
            _win32print.ClosePrinter(handle)
    return _write


class FrankenPrint:
    """Forward PSX virtual printer output to an Epson TM-T20iii via ESC/POS."""

    def __init__(self) -> None:
        """Initialise with empty state."""
        self.args: Optional[argparse.Namespace] = None
        self.logger = logging.getLogger(__MYNAME__)
        self._write: Optional[Callable[[bytes], None]] = None

    # ------------------------------------------------------------------
    # Printer selection and connection
    # ------------------------------------------------------------------

    def _auto_select_printer(self) -> Optional[str]:
        """Return the sole installed printer name, or print a list and return None."""
        if _win32print is None:
            self.logger.error(
                "win32print not available — run: pip install pywin32")
            return None
        try:
            flags = (_win32print.PRINTER_ENUM_LOCAL |
                     _win32print.PRINTER_ENUM_CONNECTIONS)
            names = [p[2] for p in _win32print.EnumPrinters(flags, None, 1)]
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.error("Cannot enumerate printers: %s", exc)
            return None
        if not names:
            self.logger.error("No printers found; use --printer to specify one")
            return None
        if len(names) == 1:
            self.logger.info("Auto-selected printer: %s", names[0])
            return names[0]
        print("Multiple printers found. Use --printer NAME to specify one:")
        for name in names:
            print(f'  --printer "{name}"')
        return None

    def _open_printer(self) -> bool:
        """Open the printer connection.

        Supports Windows printer name (via win32print), COM port, and TCP.
        Returns True on success, False on failure.
        """
        dest = self.args.printer
        if dest is None:
            dest = self._auto_select_printer()
            if dest is None:
                return False
        try:
            if ':' in dest:
                host, port_str = dest.rsplit(':', 1)
                sock = socket.create_connection((host, int(port_str)), timeout=10)
                self._write = sock.sendall
                self.logger.info("Printer connected via TCP to %s", dest)
            elif dest.upper().startswith('COM'):
                fh = open(f'\\\\.\\{dest}', 'wb', buffering=0)  # pylint: disable=consider-using-with
                self._write = fh.write
                self.logger.info("Printer connected via %s", dest)
            else:
                if _win32print is None:
                    self.logger.error(
                        "win32print not available — run: pip install pywin32")
                    return False
                handle = _win32print.OpenPrinter(dest)
                _win32print.ClosePrinter(handle)
                self._write = _make_win32_writer(dest)
                self.logger.info(
                    "Printer connected via Windows printer '%s'", dest)
            return True
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.error("Cannot open printer '%s': %s", dest, exc)
            return False

    def _send(self, data: bytes) -> None:
        """Write raw bytes to the printer, logging any error."""
        if not self._write:
            return
        try:
            self._write(data)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.logger.error("Printer write error: %s", exc)

    # ------------------------------------------------------------------
    # ESC/POS print job
    # ------------------------------------------------------------------

    def _print_job(self, text: str) -> None:
        """Send a print job: initialise, text (^ → newline), feed, then partial cut."""
        lines = text.replace('^', '\n')
        pre = '\n' * self.args.lines_before
        post = '\n' * self.args.lines_after
        encoded = (pre + lines + post).encode('ascii', errors='replace')
        self.logger.info("Printing:\n%s", lines)
        self._send(_ESC_INIT + encoded + _ESC_FEED + _GS_CUT)

    # ------------------------------------------------------------------
    # PSX variable callback
    # ------------------------------------------------------------------

    def _on_printer_text(self, _key: str, value: str) -> None:
        """Print the text received in Qs119; ignore the empty-string reset from PSX."""
        if value:
            self._print_job(value)

    # ------------------------------------------------------------------
    # PSX connection
    # ------------------------------------------------------------------

    async def _psx_coro(self) -> None:
        """Maintain the PSX connection and subscribe to the printer text variable."""
        def connected(_key: str, _value: str) -> None:
            self.logger.info("PSX connected")
            client.send("name", f"{__MY_CLIENT_ID__}:{__MY_DISPLAY_NAME__}")
            if self.args.test_print:
                self._print_job(_TEST_MESSAGE)

        client = psx.Client()
        client.logger = lambda msg: self.logger.debug("PSX: %s", msg)
        client.onConnect = lambda: None
        client.subscribe("version", connected)
        client.subscribe("PrinterText", self._on_printer_text)
        await client.connect(self.args.psx_host, self.args.psx_port)
        self.logger.warning("PSX connection ended")

    # ------------------------------------------------------------------
    # Argument parsing and entry point
    # ------------------------------------------------------------------

    def handle_args(self) -> None:
        """Parse command-line arguments."""
        parser = argparse.ArgumentParser(
            prog=__MYNAME__,
            description=__MY_DESCRIPTION__,
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument(
            '--psx-host', default='127.0.0.1', metavar='HOST',
            help="PSX server or router hostname.")
        parser.add_argument(
            '--psx-port', type=int, default=10747, metavar='PORT',
            help="PSX server or router port.")
        parser.add_argument(
            '--printer', default=None, metavar='NAME|COMn|HOST:PORT',
            help="Windows printer name, COM port (e.g. COM3), or TCP address "
                 "(e.g. 192.168.1.10:9100). If omitted and exactly one printer "
                 "is installed it is used automatically.")
        parser.add_argument(
            '--lines-before', type=int, default=0, metavar='N',
            help="Blank lines to print before the message.")
        parser.add_argument(
            '--lines-after', type=int, default=0, metavar='N',
            help="Blank lines to print after the message (before the cut).")
        parser.add_argument(
            '--test-print', action='store_true',
            help="Print a short test message on each PSX connection.")
        parser.add_argument(
            '--debug', action='store_true',
            help="Enable debug logging.")
        self.args = parser.parse_args()

    async def run(self) -> None:
        """Parse args, open the printer, then run the PSX connection loop."""
        self.handle_args()
        logging.basicConfig(
            level=logging.DEBUG if self.args.debug else logging.INFO,
            format="%(asctime)s: %(message)s",
            datefmt="%H:%M:%S")
        if not self._open_printer():
            sys.exit(1)
        await self._psx_coro()


def main() -> None:
    """Parse arguments and run FrankenPrint."""
    fp = FrankenPrint()
    try:
        asyncio.run(fp.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
