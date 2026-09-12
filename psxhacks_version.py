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
from typing import Optional

_UNKNOWN_VERSION = "unknown"


def get_version(name: str, script_file: str, version_dir: Optional[str] = None) -> str:
    """Return the version string for the addon named `name`.

    `script_file` should be the calling script's own __file__ -- used to
    locate the sibling <name>.version file when running from source and
    version_dir is not given. `version_dir`, when given, overrides that
    lookup directory for the non-frozen case only; it is for addons like
    frankenrouter_ident.py that report another addon's version (frankenrouter's)
    rather than having one of their own -- it has no effect once frozen,
    since a frozen build always bundles its own addon's .version file at
    the bundle root regardless of which directory it originally came from.
    """
    if getattr(sys, 'frozen', False):
        base_dir = Path(sys._MEIPASS)  # pylint: disable=protected-access,no-member
    elif version_dir is not None:
        base_dir = Path(version_dir)
    else:
        base_dir = Path(script_file).resolve().parent

    version_file = base_dir / f"{name}.version"
    try:
        return version_file.read_text(encoding='utf-8').strip() or _UNKNOWN_VERSION
    except OSError:
        return _UNKNOWN_VERSION
