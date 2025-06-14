"""A protocol-aware PSX router."""
# pylint: disable=invalid-name
import argparse
import asyncio
import logging
import sys

class FrankenrouterException(Exception):
    """Frankenrouter exception.

    For now, no special handling, this class just exists to make
    pylint happy. :)
    """


class Frankenrouter():  # pylint: disable=too-many-instance-attributes,too-many-public-methods
    """Replaces the PSX USB subsystem."""

    def __init__(self):
        """Initialize the class."""
        log_format = "%(asctime)s: %(message)s"
        logging.basicConfig(
            format=log_format,
            level=logging.INFO,
            datefmt="%H:%M:%S",
        )
        self.args = None
        self.logger = logging.getLogger("frankenrouter")
        self.handle_args()
        
    def handle_args(self):
        """Handle command line arguments."""
        parser = argparse.ArgumentParser(
            prog='frankenrouterctl',
            description='A simple tool to control the frankenrouter',
            formatter_class=argparse.ArgumentDefaultsHelpFormatter)
        parser.add_argument(
            '--listen-port', type=int,
            action='store', default=10748)
        parser.add_argument(
            '--listen-host', type=str,
            action='store', default="127.0.0.1")
        parser.add_argument(
            '--debug',
            action='store_true')
        parser.add_argument('command', type=str)

        self.args = parser.parse_args()
        if self.args.debug:
            self.logger.setLevel(logging.DEBUG)

    async def send_command(self):
        if self.args.command not in [ 'RouterStop', 'AllStop' ]:
            raise SystemExit("Unsupported command %s" % self.args.command)
        reader, writer = await asyncio.open_connection(
            self.args.listen_host,
            self.args.listen_port,
        )

        if self.args.command == 'AllStop':
            writer.write(f"pleaseBeSoKindAndQuit\n".encode())
            await asyncio.sleep(1)
            writer.write(f"RouterStop\n".encode())
        else:
            writer.write(f"{self.args.command}\n".encode())
        await writer.drain()
        # Wait for server to exit
        while True:
            data = await reader.readline()
            line = data.decode().strip()
            # self.logger.info("From server: %s", line)
            if line == "exit":
                print("Router is stopping")
                writer.close()
                await writer.wait_closed()
                return
        await asyncio.sleep(1)
            
if __name__ == '__main__':
    me = Frankenrouter()
    asyncio.run(me.send_command())


    
