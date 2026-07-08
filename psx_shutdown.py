"""Shut down PSX cleanly."""
import argparse
import asyncio
import sys


async def main(host, port):
    """Connect to PSX and send the quit command."""
    _, writer = await asyncio.open_connection(host, port)
    writer.write("pleaseBeSoKindAndQuit\n".encode())
    await asyncio.sleep(2.0)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--psx-host", default="127.0.0.1", help="PSX host (default: 127.0.0.1)")
    parser.add_argument("--psx-port", type=int, default=10747, help="PSX port (default: 10747)")
    parser.add_argument(
        "--psx-port-override", type=int, default=None,
        help="Override --psx-port with this value (a warning is printed). "
             "Used by start_scripts to force connecting to the correct router port.")
    args = parser.parse_args()
    if args.psx_port_override is not None:
        print(f"WARNING: --psx-port-override={args.psx_port_override} "
              f"overrides --psx-port={args.psx_port}", file=sys.stderr)
        args.psx_port = args.psx_port_override
    asyncio.run(main(args.psx_host, args.psx_port))
