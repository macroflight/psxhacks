"""Shared helper for reading an addon's version number.

Every addon's version lives in a small sibling text file (e.g.
frankenweather.version next to frankenweather.py), not inside the addon's
own .py file -- see release.py, which is the only thing that ever writes
these files. Keeping the version out of the addon source means a release is
a one-line diff in a dedicated file, never entangled with an unrelated code
commit.

get_version() works identically whether the addon is run directly as a .py
script or has been frozen into a standalone EXE by PyInstaller: each
addon's .spec bundles its own <name>.version file as PyInstaller data, at
the root of the bundle, so the frozen lookup (via sys._MEIPASS) finds the
exact same file format the source checkout uses.
"""
import sys
from pathlib import Path

_UNKNOWN_VERSION = "unknown"


def get_version(name: str, script_file: str) -> str:
    """Return the version string for the addon named `name`.

    `script_file` should be the calling script's own __file__ -- used to
    locate the sibling <name>.version file when running from source.
    """
    if getattr(sys, 'frozen', False):
        base_dir = Path(sys._MEIPASS)  # pylint: disable=protected-access,no-member
    else:
        base_dir = Path(script_file).resolve().parent

    version_file = base_dir / f"{name}.version"
    try:
        return version_file.read_text(encoding='utf-8').strip() or _UNKNOWN_VERSION
    except OSError:
        return _UNKNOWN_VERSION
