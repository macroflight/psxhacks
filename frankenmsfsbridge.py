"""FrankenMSFSBridge — bridge MSFS SimConnect data to PSX via addon messages."""
import argparse
import asyncio
import json
import logging
import sys
import time
from typing import Optional

import psx

try:
    import SimConnect as _sc_mod  # pylint: disable=import-error
except ImportError:
    _sc_mod = None

_MY_ADDON = "FRANKENMSFSBRIDGE"
_SIMVARS = ("AMBIENT_IN_CLOUD", "SEA_LEVEL_PRESSURE")
_HEARTBEAT_S = 60.0


class Bridge:  # pylint: disable=too-few-public-methods
    """Poll MSFS via SimConnect and publish data to PSX as an addon message."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Set up args, logger, and PSX client."""
        self.args = args
        self._logger = logging.getLogger(_MY_ADDON)
        self._poll_task: Optional[asyncio.Task] = None
        self._last_sent: dict = {}
        self._last_sent_at: float = 0.0
        self._psx = psx.Client()
        self._psx.logger = lambda msg: self._logger.debug("PSX: %s", msg)
        self._psx.onConnect = self._on_psx_connect
        self._psx.onDisconnect = self._on_psx_disconnect

    def _on_psx_connect(self) -> None:
        """Start SimConnect polling when PSX connects."""
        self._last_sent_at = 0.0  # force immediate send on first poll
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = asyncio.create_task(self._sc_coro())

    def _on_psx_disconnect(self) -> None:
        """Cancel SimConnect polling when PSX disconnects."""
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll(self, aq) -> None:
        """Poll SimConnect in a loop and publish results to PSX."""
        while True:
            await asyncio.sleep(self.args.interval)
            data = {}
            raw = aq.get("AMBIENT_IN_CLOUD")
            if raw is not None:
                data["in_cloud"] = bool(int(raw))
            raw = aq.get("SEA_LEVEL_PRESSURE")
            if raw is not None:
                data["qnh_hpa"] = round(float(raw), 2)
            if not data:
                continue
            now = time.monotonic()
            changed = data != self._last_sent
            if changed or now - self._last_sent_at >= _HEARTBEAT_S:
                self._psx.send("addon", f"{_MY_ADDON}:{json.dumps(data)}")
                self._logger.debug("Sent (%s): %s", "changed" if changed else "heartbeat", data)
                self._last_sent = data
                self._last_sent_at = now

    async def _sc_coro(self) -> None:
        """Connect to MSFS via SimConnect and run the poll loop."""
        while True:
            try:
                import ctypes  # pylint: disable=import-outside-toplevel
                ctypes.windll.kernel32.SetDllDirectoryW(None)  # pylint: disable=no-member
            except (ImportError, AttributeError):
                pass
            try:
                sc = _sc_mod.SimConnect()
                aq = _sc_mod.AircraftRequests(sc)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._logger.warning("SimConnect unavailable (%s), retrying in 30s", exc)
                await asyncio.sleep(30.0)
                continue
            for var in _SIMVARS:
                obj = aq.find(var)
                if obj is not None:
                    obj.time = 2000
            self._logger.info("SimConnect connected to MSFS")
            try:
                await self._poll(aq)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._logger.warning("MSFS connection lost (%s), reconnecting in 30s", exc)
                await asyncio.sleep(30.0)

    async def run(self) -> None:
        """Connect to PSX and run until interrupted."""
        await self._psx.connect(self.args.psx_host, self.args.psx_port)


def main() -> None:
    """Parse args and run the bridge."""
    parser = argparse.ArgumentParser(
        description="Bridge MSFS SimConnect data to PSX via addon messages.")
    parser.add_argument("--psx-host", default="127.0.0.1", metavar="HOST",
                        help="PSX main server host (default: 127.0.0.1)")
    parser.add_argument("--psx-port", type=int, default=10747, metavar="PORT",
                        help="PSX main server port (default: 10747)")
    parser.add_argument("--interval", type=float, default=5.0, metavar="SEC",
                        help="SimConnect poll interval in seconds (default: 5)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s: %(message)s",
        datefmt="%H:%M:%S")
    if _sc_mod is None:
        logging.error("SimConnect not available — run: pip install SimConnect")
        sys.exit(1)
    bridge = Bridge(args)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
