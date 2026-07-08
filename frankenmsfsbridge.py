"""FrankenMSFSBridge — bridge MSFS SimConnect data to PSX via addon messages."""
import argparse
import asyncio
import ctypes
import ctypes.wintypes as _wt
import json
import logging
import os
import sys
import time
from typing import Optional

import psx

_MY_ADDON = "FRANKENMSFSBRIDGE"
_HEARTBEAT_S = 60.0

# ---------------------------------------------------------------------------
# Thin ctypes wrapper around the SimConnect DLL (MSFS 2020 / 2024)
# ---------------------------------------------------------------------------

_SC_DATATYPE_FLOAT64 = 4  # SIMCONNECT_DATATYPE_FLOAT64
_SC_PERIOD_SECOND = 4  # SIMCONNECT_PERIOD_SECOND
_SC_OBJECT_ID_USER = 0  # SIMCONNECT_OBJECT_ID_USER
_SC_UNUSED = 0xFFFFFFFF  # SIMCONNECT_UNUSED (sentinel "no datum ID")
_SC_RECV_SIMOBJECT_DATA = 8  # SIMCONNECT_RECV_ID_SIMOBJECT_DATA
_SC_S_OK = 0


class _RecvHeader(ctypes.Structure):  # pylint: disable=too-few-public-methods
    _fields_ = [("dwSize", _wt.DWORD), ("dwVersion", _wt.DWORD), ("dwID", _wt.DWORD)]


class _RecvSimObjectData(ctypes.Structure):  # pylint: disable=too-few-public-methods
    _fields_ = [
        ("header", _RecvHeader),
        ("dwRequestID", _wt.DWORD),
        ("dwObjectID", _wt.DWORD),
        ("dwDefineID", _wt.DWORD),
        ("dwFlags", _wt.DWORD),
        ("dwentrynumber", _wt.DWORD),
        ("dwoutof", _wt.DWORD),
        ("dwDefineCount", _wt.DWORD),
    ]


# Variable data follows immediately after this header in the dispatch buffer.
_DATA_OFFSET = ctypes.sizeof(_RecvSimObjectData)


class SimConnect:
    """Read MSFS simulation variables via the SimConnect DLL."""

    def __init__(self, app_name: str = "FrankenMSFSBridge",
                 sdk_path: Optional[str] = None) -> None:
        """Open the SimConnect connection and prepare an empty variable list."""
        # Build an ordered list of directories to search for the DLL.
        # Python removes the current directory from the DLL search path at startup,
        # so we resolve explicit paths rather than relying on LoadLibrary search order.
        _script_dir = os.path.dirname(os.path.abspath(__file__))
        search_dirs = []
        if sdk_path:
            search_dirs.append(os.path.join(sdk_path, "SimConnect SDK", "lib"))
        search_dirs += [_script_dir, os.getcwd()]

        dll = None
        for base in search_dirs:
            for leaf in ("SimConnect.dll", "SimConnect_internal.dll"):
                path = os.path.join(base, leaf)
                if os.path.exists(path):
                    try:
                        dll = ctypes.WinDLL(path)
                        break
                    except OSError:
                        pass
            if dll:
                break
        # Fall back to bare name (PATH / System32 / registered DLL).
        if dll is None:
            for name in ("SimConnect", "SimConnect_internal"):
                try:
                    dll = ctypes.WinDLL(name)
                    break
                except OSError:
                    pass
        if dll is None:
            raise OSError(
                "SimConnect.dll not found — use --sdk-path to point to the MSFS SDK, "
                "or copy SimConnect.dll next to this script")
        self._dll = dll
        self._handle = _wt.HANDLE(None)
        hr = dll.SimConnect_Open(
            ctypes.byref(self._handle), app_name.encode(), None, 0, None, 0)
        if hr != _SC_S_OK:
            raise OSError(f"SimConnect_Open failed: 0x{hr & 0xFFFFFFFF:08x}")
        self._define_id = 1
        self._request_id = 1
        self._vars: list = []

    def add_variable(self, simvar: str, unit: str, key: str, cast_fn) -> None:
        """Append one variable to the single shared data definition."""
        hr = self._dll.SimConnect_AddToDataDefinition(
            self._handle, self._define_id,
            simvar.encode(), unit.encode(),
            _SC_DATATYPE_FLOAT64, ctypes.c_float(0.0), _SC_UNUSED)
        if hr != _SC_S_OK:
            raise OSError(
                f"AddToDataDefinition failed for '{simvar}' (unit '{unit}'): "
                f"0x{hr & 0xFFFFFFFF:08x}")
        self._vars.append((key, cast_fn))

    def start(self) -> None:
        """Request data updates at one per sim second."""
        hr = self._dll.SimConnect_RequestDataOnSimObject(
            self._handle, self._request_id, self._define_id,
            _SC_OBJECT_ID_USER, _SC_PERIOD_SECOND, 0, 0, 0, 0)
        if hr != _SC_S_OK:
            raise OSError(
                f"RequestDataOnSimObject failed: 0x{hr & 0xFFFFFFFF:08x}")

    def poll(self) -> dict:
        """Drain the dispatch queue; return the latest data snapshot or {}."""
        latest: dict = {}
        pp_data = ctypes.c_void_p()
        cb_data = _wt.DWORD()
        while True:
            hr = self._dll.SimConnect_GetNextDispatch(
                self._handle, ctypes.byref(pp_data), ctypes.byref(cb_data))
            if hr != _SC_S_OK or not pp_data.value:
                break
            if _RecvHeader.from_address(pp_data.value).dwID == _SC_RECV_SIMOBJECT_DATA:
                obj = _RecvSimObjectData.from_address(pp_data.value)
                if obj.dwRequestID == self._request_id:
                    n = len(self._vars)
                    doubles = (ctypes.c_double * n).from_address(
                        pp_data.value + _DATA_OFFSET)
                    latest = {k: fn(doubles[i]) for i, (k, fn) in enumerate(self._vars)}
        return latest

    def close(self) -> None:
        """Close the SimConnect connection."""
        if self._handle:
            self._dll.SimConnect_Close(self._handle)
            self._handle = _wt.HANDLE(None)


# ---------------------------------------------------------------------------
# Variables to bridge from MSFS to PSX
# ---------------------------------------------------------------------------

def _r1(v):
    return round(v, 1)


def _r2(v):
    return round(v, 2)


def _bool_int(v):
    return bool(int(v))


_VARIABLES = (
    # SimConnect variable name   unit         json key         cast
    ("AMBIENT IN CLOUD", "Bool", "in_cloud", _bool_int),
    ("SEA LEVEL PRESSURE", "Millibars", "qnh_hpa", _r2),
    ("AMBIENT TEMPERATURE", "Celsius", "oat_c", _r1),
    ("AMBIENT WIND DIRECTION", "Degrees", "wind_dir", _r1),
    ("AMBIENT WIND VELOCITY", "Knots", "wind_spd", _r1),
    ("AMBIENT WIND Y", "Knots", "wind_vert", _r1),
    ("ENV CLOUD DENSITY", "Number", "cloud_density", _r1),
    ("AMBIENT PRECIP STATE", "mask", "precip_state", int),  # 2=none 4=rain 8=snow
)


# ---------------------------------------------------------------------------
# Bridge
# ---------------------------------------------------------------------------

class Bridge:  # pylint: disable=too-few-public-methods
    """Poll MSFS via SimConnect and publish data to PSX as an addon message."""

    def __init__(self, args: argparse.Namespace) -> None:
        """Initialise the bridge with parsed command-line arguments."""
        self.args = args
        self._logger = logging.getLogger(_MY_ADDON)
        self._poll_task: Optional[asyncio.Task] = None
        self._last_sent: dict = {}
        self._last_sent_at: float = 0.0
        self._psx = psx.Client()
        self._psx.logger = lambda msg: self._logger.debug("PSX: %s", msg)
        self._psx.onConnect = self._on_psx_connect
        self._psx.onDisconnect = self._on_psx_disconnect
        self._psx.subscribe("version", self._on_psx_version)

    def _on_psx_version(self, _key: str, _value: str) -> None:
        self._psx.send("name", "MSFSBRDG:FrankenMSFSBridge")

    def _on_psx_connect(self) -> None:
        self._last_sent_at = 0.0
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
        self._poll_task = asyncio.create_task(self._sc_coro())

    def _on_psx_disconnect(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
            self._poll_task = None

    async def _poll(self, sc: SimConnect) -> None:
        while True:
            await asyncio.sleep(self.args.interval)
            data = sc.poll()
            if not data:
                continue
            now = time.monotonic()
            changed = data != self._last_sent
            if changed or now - self._last_sent_at >= _HEARTBEAT_S:
                self._psx.send("addon", f"{_MY_ADDON}:{json.dumps(data)}")
                self._logger.debug(
                    "Sent (%s): %s", "changed" if changed else "heartbeat", data)
                self._last_sent = data
                self._last_sent_at = now

    async def _sc_coro(self) -> None:
        while True:
            sc = None
            try:
                sc = SimConnect(sdk_path=self.args.sdk_path)
                for simvar, unit, key, cast_fn in _VARIABLES:
                    sc.add_variable(simvar, unit, key, cast_fn)
                sc.start()
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._logger.warning("SimConnect unavailable (%s), retrying in 30s", exc)
                if sc:
                    try:
                        sc.close()
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                await asyncio.sleep(30.0)
                continue
            self._logger.info("SimConnect connected to MSFS")
            try:
                await self._poll(sc)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                self._logger.warning("MSFS connection lost (%s), reconnecting in 30s", exc)
            finally:
                try:
                    sc.close()
                except Exception:  # pylint: disable=broad-exception-caught
                    pass
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
    parser.add_argument("--psx-port-override", type=int, default=None, metavar="PORT",
                        help="Override --psx-port with this value (a warning is printed). "
                             "Used by start_scripts to force connecting to the correct "
                             "router port.")
    parser.add_argument("--interval", type=float, default=5.0, metavar="SEC",
                        help="SimConnect poll interval in seconds (default: 5)")
    parser.add_argument("--sdk-path", default=None, metavar="DIR",
                        help="MSFS SDK root directory; the DLL is loaded from "
                             "<DIR>\\SimConnect SDK\\lib\\SimConnect.dll")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging")
    args = parser.parse_args()
    if args.psx_port_override is not None:
        print(f"WARNING: --psx-port-override={args.psx_port_override} "
              f"overrides --psx-port={args.psx_port}", file=sys.stderr)
        args.psx_port = args.psx_port_override
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s: %(message)s",
        datefmt="%H:%M:%S")
    bridge = Bridge(args)
    try:
        asyncio.run(bridge.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
