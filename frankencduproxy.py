"""frankencduproxy.py - Proxy between the Cockpit Simulator CDU Bridge and PSX.

Sits between the CS CDU Bridge and the PSX Main Server. All PSX protocol
traffic is forwarded transparently, except CDU-tagged keywords, which have
their CDU position letter (L / C / R) translated so the physical CDU can
act as any of the three software CDUs.

The CDU bridge is always configured for one position (--cs-cdu-config, e.g.
"L"). The proxy maps that position to the desired PSX CDU (--cd-cdu-swap-with,
e.g. "R"), rewriting keywords in both directions:

  PSX→bridge : R* / *R keywords → L* / *L  (bridge sees target CDU data)
  bridge→PSX : L* / *L keywords → R* / *R  (keypresses reach target CDU)

The original bridge-CDU data from PSX is dropped to prevent display
conflicts; all other CDU data passes through unchanged.
"""

import argparse
import asyncio
import logging
import re
import socket
import sys
from typing import Callable, List, Optional, Set, Tuple

__version__ = "1.0.0"


# ── CDU keyword classification ───────────────────────────────────────────────

# Stems for suffix-style keywords that end with the CDU letter.
_SUFFIX_STEM_RE = re.compile(
    r'^(?:KeybCdu|CduColTi|CduColSp|CduCol\d+[sb]|'
    r'BlankTimeCdu|LightsCdu|BrtCdu|BrtPushCdu|cdu)$'
)


def _get_cdu_letter(name: str) -> Optional[str]:
    """Return the CDU letter (L/C/R) if name is a CDU-tagged keyword."""
    if not name:
        return None
    # Prefix style: {L|C|R}cdu…   e.g. LcduTitle, RcduLine3b
    if name[0] in ('L', 'C', 'R') and name[1:].startswith('cdu'):
        return name[0]
    # Suffix style: {stem}{L|C|R}  e.g. KeybCduL, LightsCduR, CduColTiC
    if name[-1] in ('L', 'C', 'R') and _SUFFIX_STEM_RE.match(name[:-1]):
        return name[-1]
    return None


def _swap_cdu_letter(name: str, from_l: str, to_l: str) -> str:
    """Replace CDU letter in name. Assumes _get_cdu_letter(name) == from_l."""
    if name[0] == from_l and name[1:].startswith('cdu'):
        return to_l + name[1:]       # prefix style
    return name[:-1] + to_l          # suffix style


# ── PSX lexicon ──────────────────────────────────────────────────────────────

# Q-code format: Q{type}{number}  where type ∈ {s, h, i}
_QCODE_RE = re.compile(r'^Q[shi]\d+$')
# Lexicon line format: L{type}{number}(mode)=name
_LEXLINE_RE = re.compile(r'^L[a-z]\d+')


class _Lexicon:
    """Maps PSX Q-codes to variable names and back."""

    def __init__(self):
        """Initialise empty lexicon."""
        self._q_to_name: dict = {}
        self._name_to_q: dict = {}

    def learn(self, lex_key: str, name: str) -> None:
        """Process a PSX lexicon-line key like 'Ls62(E)' → Q-code 'Qs62'."""
        base, _, _ = lex_key[1:].partition('(')
        qcode = 'Q' + base
        self._q_to_name[qcode] = name
        self._name_to_q[name] = qcode

    def to_name(self, key: str) -> Tuple[str, bool]:
        """Return (name, was_qcode). Resolves a Q-code to its variable name."""
        if _QCODE_RE.match(key):
            return self._q_to_name.get(key, key), True
        return key, False

    def to_qcode(self, name: str, fallback: str) -> str:
        """Return Q-code for a variable name, or fallback if unknown."""
        return self._name_to_q.get(name, fallback)


# ── PSX display cache ─────────────────────────────────────────────────────────

class _PsxCache:
    """Stores the last known PSX line for each CDU display variable."""

    def __init__(self) -> None:
        """Initialise empty cache."""
        self._data: dict = {}

    def update(self, name: str, stripped: str) -> None:
        """Cache the stripped PSX line for a named CDU variable."""
        self._data[name] = stripped

    def replay_cdu(self, cdu: str) -> List[str]:
        """Return CRLF-terminated lines for all cached variables of cdu."""
        return [
            line + '\r\n'
            for name, line in self._data.items()
            if _get_cdu_letter(name) == cdu
        ]


# ── Line-level translation ────────────────────────────────────────────────────

def _translate_psx_to_bridge(
    line: str, lex: _Lexicon, bridge_cdu: str, target_cdu: str,
    cache: Optional[_PsxCache] = None,
) -> Optional[str]:
    """Translate one PSX line for forwarding to the CDU bridge.

    Returns the (possibly rewritten) line, or None to drop it.
    """
    stripped = line.rstrip('\r\n')
    if not stripped:
        return line

    key, sep, value = stripped.partition('=')

    # Lexicon line: learn the mapping, forward as-is.
    if _LEXLINE_RE.match(key):
        if sep:
            lex.learn(key, value)
        return line

    name, was_qcode = lex.to_name(key)

    cdu = _get_cdu_letter(name)
    if cache is not None and cdu is not None and sep:
        cache.update(name, stripped)

    if cdu == target_cdu:
        # Translate target-CDU display data → bridge CDU slot.
        new_name = _swap_cdu_letter(name, target_cdu, bridge_cdu)
        new_key = lex.to_qcode(new_name, new_name) if was_qcode else new_name
        return f"{new_key}={value}\r\n" if sep else f"{new_key}\r\n"
    if cdu == bridge_cdu and bridge_cdu != target_cdu:
        # Drop raw bridge-CDU data; it would conflict with the translated
        # target-CDU data we are already sending in that slot.
        return None

    return line


def _translate_bridge_to_psx(
    line: str, lex: _Lexicon, bridge_cdu: str, target_cdu: str
) -> Optional[str]:
    """Translate one CDU-bridge line for forwarding to PSX.

    Returns the (possibly rewritten) line, or None to drop it.
    """
    stripped = line.rstrip('\r\n')
    if not stripped:
        return line

    key, sep, value = stripped.partition('=')
    name, was_qcode = lex.to_name(key)

    cdu = _get_cdu_letter(name)
    if cdu == bridge_cdu:
        # Translate bridge-CDU input → target CDU.
        new_name = _swap_cdu_letter(name, bridge_cdu, target_cdu)
        new_key = lex.to_qcode(new_name, new_name) if was_qcode else new_name
        return f"{new_key}={value}\r\n" if sep else f"{new_key}\r\n"

    return line


# ── Proxy server ──────────────────────────────────────────────────────────────

class CduProxy:  # pylint: disable=too-few-public-methods
    """Async TCP proxy that translates CDU keywords between bridge and PSX."""

    def __init__(self, args: argparse.Namespace):
        """Initialise from parsed command-line arguments."""
        self.bridge_cdu: str = args.cs_cdu_config
        self.target_cdu: str = args.target_cdu if args.target_cdu else args.cs_cdu_config
        self.psx_host: str = args.psx_host
        self.psx_port: int = args.psx_port
        self.listen_port: int = args.listen_port
        self.logger = logging.getLogger('frankencduproxy')
        self._bridge_writers: Set[asyncio.StreamWriter] = set()

    @staticmethod
    def _set_nodelay(writer: asyncio.StreamWriter) -> None:
        sock = writer.transport.get_extra_info('socket')
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    async def _relay(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        translate_fn,
        direction: str,
    ) -> None:
        """Read lines from reader, translate, write to writer."""
        try:
            while True:
                line = await reader.readline()
                if not line:
                    self.logger.info("%s: connection closed", direction)
                    break
                text = line.decode('latin-1')
                translated = translate_fn(text)
                if translated is not None:
                    self.logger.debug("%s: %s", direction, translated.rstrip())
                    writer.write(translated.encode('latin-1'))
                else:
                    self.logger.debug("%s [dropped]: %s", direction, text.rstrip())
        except (ConnectionError, asyncio.IncompleteReadError, OSError) as exc:
            self.logger.info("%s: %s", direction, exc)

    def _check_addon(self, line: str, on_swap: Callable[[str], None]) -> None:
        stripped = line.rstrip('\r\n')
        key, sep, value = stripped.partition('=')
        if key != 'addon' or not sep:
            return
        prefix = 'FRANKENCDUPROXY:'
        if not value.upper().startswith(prefix):
            return
        new_cdu = value[len(prefix):].upper()
        if new_cdu not in ('L', 'C', 'R'):
            self.logger.warning("Addon message: unrecognised CDU %r", new_cdu)
            return
        old_target = self.target_cdu
        self.target_cdu = new_cdu
        if new_cdu == self.bridge_cdu:
            self.logger.info(
                "Swap disabled via addon message (bridge=%s)", self.bridge_cdu,
            )
        else:
            self.logger.info(
                "Swap changed via addon message: bridge=%s now controls PSX=%s",
                self.bridge_cdu, new_cdu,
            )
        if new_cdu != old_target:
            on_swap(new_cdu)

    async def _handle_bridge(
        self,
        br_reader: asyncio.StreamReader,
        br_writer: asyncio.StreamWriter,
    ) -> None:
        peer = br_writer.get_extra_info('peername')
        self.logger.info("Bridge connected from %s", peer)
        self._set_nodelay(br_writer)

        try:
            psx_reader, psx_writer = await asyncio.open_connection(
                self.psx_host, self.psx_port
            )
        except OSError as exc:
            self.logger.error(
                "Cannot connect to PSX at %s:%d: %s",
                self.psx_host, self.psx_port, exc,
            )
            br_writer.close()
            return

        self._set_nodelay(psx_writer)
        self.logger.info("Connected to PSX at %s:%d", self.psx_host, self.psx_port)
        self._bridge_writers.add(br_writer)

        lex = _Lexicon()
        cache = _PsxCache()

        def _on_psx(ln: str) -> Optional[str]:
            def _refresh(new_cdu: str) -> None:
                for cached_line in cache.replay_cdu(new_cdu):
                    out = _translate_psx_to_bridge(
                        cached_line, lex, self.bridge_cdu, new_cdu,
                    )
                    if out is not None:
                        br_writer.write(out.encode('latin-1'))
            self._check_addon(ln, _refresh)
            return _translate_psx_to_bridge(
                ln, lex, self.bridge_cdu, self.target_cdu, cache,
            )

        t1 = asyncio.create_task(self._relay(
            psx_reader, br_writer, _on_psx, 'PSX→bridge',
        ))
        t2 = asyncio.create_task(self._relay(
            br_reader, psx_writer,
            lambda ln: _translate_bridge_to_psx(ln, lex, self.bridge_cdu, self.target_cdu),
            'bridge→PSX',
        ))

        _, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

        self._bridge_writers.discard(br_writer)
        for writer in (br_writer, psx_writer):
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # pylint: disable=broad-except
                pass

        self.logger.info("Bridge session from %s ended", peer)

    async def run(self) -> None:
        """Start the proxy server and relay sessions until interrupted."""
        server = await asyncio.start_server(
            self._handle_bridge, '0.0.0.0', self.listen_port
        )
        swap_desc = (
            f"bridge={self.bridge_cdu} → PSX={self.target_cdu}"
            if self.bridge_cdu != self.target_cdu
            else f"no swap (bridge={self.bridge_cdu})"
        )
        self.logger.info(
            "CDU proxy listening on port %d  %s  PSX=%s:%d",
            self.listen_port, swap_desc, self.psx_host, self.psx_port,
        )
        try:
            async with server:
                await server.serve_forever()
        finally:
            for writer in list(self._bridge_writers):
                try:
                    writer.write(b'exit\r\n')
                except Exception:  # pylint: disable=broad-except
                    pass


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Parse arguments and run the CDU proxy."""
    parser = argparse.ArgumentParser(
        description='CDU proxy between the Cockpit Simulator CDU Bridge and PSX'
    )
    parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')
    parser.add_argument(
        '--psx-host', default='127.0.0.1', metavar='HOST',
        help='PSX Main Server hostname (default: 127.0.0.1)',
    )
    parser.add_argument(
        '--psx-port', type=int, default=10747, metavar='PORT',
        help='PSX Main Server port (default: 10747)',
    )
    parser.add_argument(
        '--listen-port', type=int, default=10748, metavar='PORT',
        help='Port to listen on for the CDU bridge (default: 10748)',
    )
    parser.add_argument(
        '--cs-cdu-config', default='L', choices=['L', 'C', 'R'],
        metavar='CDU',
        help='CDU position the CS Bridge is configured for: L, C, or R (default: L)',
    )
    parser.add_argument(
        '--cd-cdu-swap-with', default=None, choices=['L', 'C', 'R'],
        metavar='CDU', dest='target_cdu',
        help='PSX CDU position to control: L, C, or R (default: same as --cs-cdu-config)',
    )
    parser.add_argument('--debug', action='store_true', help='Enable debug logging')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format='%(asctime)s %(levelname)-5s %(name)s: %(message)s',
        datefmt='%H:%M:%S',
        stream=sys.stdout,
    )

    try:
        asyncio.run(CduProxy(args).run())
    except KeyboardInterrupt:
        print('\nStopped.')


if __name__ == '__main__':
    main()
