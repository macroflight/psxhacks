"""Shut down PSX cleanly."""
import argparse
import asyncio


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
    args = parser.parse_args()
    asyncio.run(main(args.psx_host, args.psx_port))
