#!/usr/bin/env python3
"""Release management helper for psxhacks addons.

Bumps an addon's version number (stored in a sibling <addon>.version text
file -- see psxhacks_version.py for how that reaches the running addon) and
reminds about updating its changelog. Does NOT commit, push, or tag --
that is left to the user.

Usage:
    ./release.py                    # scan git history, suggest which addons may need a bump
    ./release.py <addon>            # bump the patch version, e.g. 1.23.4 -> 1.23.5
    ./release.py <addon> --minor    # bump the minor version, e.g. 1.23.4 -> 1.24.0
    ./release.py <addon> --major    # bump the major version, e.g. 1.23.4 -> 2.0.0

A patch release needs no changelog reminder. A minor release gets a
suggestion to add a changelog entry. A major release requires typing "YES"
to confirm the changelog has actually been updated.

<addon> is one of: frankencduproxy, frankenprint, frankenpush, frankenrouter,
frankentanker, frankenusb, frankenweather, psxutils, start_scripts.

frankenrouter_ident is not a separate release target -- it always reports
frankenrouter's own version (see frankenrouter_ident.py), so bumping
frankenrouter's version covers it too.

psxutils is a shared version number for a group of small diagnostic
utilities (show_hid, show_psx, show_usb, temporary_weather_logger) that are
published together as one release containing a zip of all four EXEs, rather
than each having its own version/release -- see docs/BuildSystem.md.

start_scripts is plain PowerShell the user runs directly (see
start_scripts/functions.ps1's Get-StartScriptsVersion) -- it is never built
into an EXE, so bumping it never triggers a CI build (it isn't in this
file's _ADDONS, only in the separate _NONBUILD_ADDONS).

CI usage (see .github/workflows/build.yml and docs/BuildSystem.md):
    git diff --name-only <before> <after> | ./release.py --detect-changed
Reads newline-separated file paths from stdin and prints a JSON array of
addon names whose .version file is among them -- the single source of
truth the build workflow uses to decide what to build, so it never drifts
out of sync with this file's own addon/version-file mapping.
"""
import argparse
import datetime
import json
import pathlib
import subprocess
import sys
from typing import Optional

_ROOT = pathlib.Path(__file__).resolve().parent

# addon name -> (version file, changelog file)
_ADDONS = {
    'frankencduproxy': (
        _ROOT / 'frankencduproxy.version',
        _ROOT / 'docs' / 'Changelog-frankencduproxy.md'),
    'frankenprint': (
        _ROOT / 'frankenprint.version',
        _ROOT / 'docs' / 'Changelog-frankenprint.md'),
    'frankenpush': (
        _ROOT / 'frankenpush.version',
        _ROOT / 'docs' / 'Changelog-frankenpush.md'),
    'frankenrouter': (
        _ROOT / 'router' / 'frankenrouter.version',
        _ROOT / 'router' / 'docs' / 'Changelog.md'),
    'frankentanker': (
        _ROOT / 'frankentanker.version',
        _ROOT / 'docs' / 'Changelog-frankentanker.md'),
    'frankenusb': (
        _ROOT / 'frankenusb.version',
        _ROOT / 'docs' / 'Changelog-frankenusb.md'),
    'frankenweather': (
        _ROOT / 'frankenweather.version',
        _ROOT / 'docs' / 'Changelog-frankenweather.md'),
    'psxutils': (
        _ROOT / 'psxutils.version',
        _ROOT / 'docs' / 'Changelog-psxutils.md'),
}

# Same shape as _ADDONS (name -> (version file, changelog file)), but for
# things this script can bump that are NOT built/released as an EXE by
# build.yml -- kept out of _ADDONS (and therefore out of
# _map_changed_files/--detect-changed) so bumping one can never trigger a
# CI build. start_scripts is plain PowerShell the user runs directly, not
# a PyInstaller target.
_NONBUILD_ADDONS = {
    'start_scripts': (
        _ROOT / 'start_scripts' / 'start_scripts.version',
        _ROOT / 'docs' / 'Changelog-start_scripts.md'),
}

_ALL_ADDONS = {**_ADDONS, **_NONBUILD_ADDONS}

# addon name -> (paths it owns, extra commit-message aliases to also match).
# Hand-maintained -- there's no directory structure yet that makes this
# derivable automatically (see the psxhacks_version.py / per-addon-directory
# idea floated alongside this feature). Used only by the no-args scan mode
# below; wrong or missing entries just make the scan miss/over-report, they
# can't cause an incorrect version bump or corrupt build.
#
# Files/dirs not listed anywhere here (psxhacks_version.py, release.py,
# psx.py, Makefile, .github/, requirements.txt, ...) are shared
# infrastructure, deliberately unattributed to any single addon.
_ADDON_PATHS = {
    'frankencduproxy': (['frankencduproxy.py', 'frankencduproxy.spec'], []),
    'frankenprint': (['frankenprint.py', 'frankenprint.spec'], []),
    'frankenpush': (['frankenpush.py', 'frankenpush.spec'], []),
    'frankenrouter': (
        ['router/', 'frankenrouter_ident.py', 'frankenrouter_ident.spec'],
        ['router']),
    'frankentanker': (['frankentanker.py', 'frankentanker.spec'], []),
    'frankenusb': (['frankenusb.py', 'frankenusb.spec'], []),
    'frankenweather': (
        ['frankenweather.py', 'frankenweather.spec', 'fw_webui.py', 'fw_cb.py',
         'fw_scanner.py', 'frankenturb/', 'docs/frankenweather.md'],
        ['weather']),
    'psxutils': (
        ['show_hid.py', 'show_hid.spec', 'show_psx.py', 'show_usb.py',
         'show_usb.spec', 'temporary_weather_logger.py',
         'temporary_weather_logger.spec'],
        []),
    'start_scripts': (['start_scripts/'], []),
}


def _git(args: list) -> Optional[str]:
    """Run a git command from _ROOT, returning stdout or None on any failure.

    Covers "not a git repo", "git not installed", and any other git error
    the same way -- this feature is a best-effort hint, never something
    that should crash the script or block a bump.
    """
    try:
        result = subprocess.run(
            ['git', *args], cwd=_ROOT, capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None
    return result.stdout


def _last_bump_commit(version_file: pathlib.Path) -> Optional[str]:
    """Return the SHA of the commit that last changed version_file, or None.

    None means either it has no git history yet (e.g. just created, not
    committed) or git itself is unavailable -- both are treated the same
    by callers: scan the addon's whole history instead of a range.
    """
    if not version_file.exists():
        return None
    out = _git(['log', '-1', '--format=%H', '--',
                str(version_file.relative_to(_ROOT))])
    return out.strip() if out and out.strip() else None


def _log_since(commit: Optional[str], args: list) -> list:
    """Return `git log --oneline` lines for `args` since `commit` (or ever)."""
    range_spec = [f'{commit}..HEAD'] if commit else []
    out = _git(['log', '--oneline', *range_spec, '--', *args]) if args else None
    return [line for line in (out or '').splitlines() if line.strip()]


def _log_since_by_message(commit: Optional[str], aliases: list) -> list:
    """Return `git log --oneline` lines whose message matches any alias since commit."""
    if not aliases:
        return []
    range_spec = [f'{commit}..HEAD'] if commit else []
    grep_args = []
    for alias in aliases:
        grep_args += ['--grep', alias]
    out = _git(['log', '--oneline', '-i', *grep_args, *range_spec])
    return [line for line in (out or '').splitlines() if line.strip()]


def _scan_for_pending_bumps() -> None:
    """Print, per addon, commits since its last bump that may warrant a new one.

    Looks at commits touching each addon's files or mentioning it by name
    (see _ADDON_PATHS) since the commit that last changed its version file
    -- a hint about what might need `./release.py <addon>` and a changelog
    entry.

    Heuristic, not authoritative: _ADDON_PATHS is hand-maintained and can
    miss a change routed through a file it doesn't know about, or
    over-report something like a docs-only tweak. Always look at the
    listed commits yourself before deciding whether/how much to bump.
    """
    if _git(['rev-parse', '--git-dir']) is None:
        print("Not a git repository (or git is unavailable) -- can't scan for "
              "pending bumps.", file=sys.stderr)
        return

    any_found = False
    for name in sorted(_ALL_ADDONS.keys()):
        version_file, _ = _ALL_ADDONS[name]
        paths, aliases = _ADDON_PATHS.get(name, ([], []))
        if not paths:
            continue
        last_bump = _last_bump_commit(version_file)
        by_file = _log_since(last_bump, paths)
        seen = {line.split(maxsplit=1)[0] for line in by_file}
        by_message = [line for line in _log_since_by_message(last_bump, aliases)
                      if line.split(maxsplit=1)[0] not in seen]
        commits = by_file + by_message
        if not commits:
            continue
        any_found = True
        current = version_file.read_text(encoding='utf-8').strip()
        print(f"\n{name} (currently {current}): {len(commits)} commit(s) "
              f"since last bump")
        for line in commits[:10]:
            print(f"  {line}")
        if len(commits) > 10:
            print(f"  ... and {len(commits) - 10} more")

    if not any_found:
        print("No addon shows commits since its last version bump.")
    else:
        print("\nReminder: this is a heuristic (see _ADDON_PATHS) -- check the "
              "commits above before bumping. Run ./release.py <addon> when ready.")


def _parse_version(text: str) -> tuple:
    """Parse a MAJOR.MINOR.PATCH string into a 3-tuple of ints."""
    parts = text.strip().split('.')
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"version {text!r} is not in MAJOR.MINOR.PATCH form")
    return tuple(int(p) for p in parts)


def _bump(version: tuple, level: str) -> tuple:
    """Return the next version for level 'major', 'minor', or 'patch'."""
    ma, mi, pa = version
    if level == 'major':
        return (ma + 1, 0, 0)
    if level == 'minor':
        return (ma, mi + 1, 0)
    return (ma, mi, pa + 1)


def _format(version: tuple) -> str:
    return '.'.join(str(p) for p in version)


def _confirm(prompt: str, default: bool) -> bool:
    hint = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {hint} ").strip().lower()
    if not answer:
        return default
    return answer.startswith('y')


def _map_changed_files(paths) -> list:
    """Return the sorted addon names whose .version file appears in `paths`.

    router/frankenrouter.version implies both 'frankenrouter' and
    'frankenrouter_ident', since frankenrouter_ident has no version file of
    its own -- it always mirrors frankenrouter's (see get_version()'s
    version_dir override in frankenrouter_ident.py).
    """
    version_file_to_addon = {
        version_file.relative_to(_ROOT).as_posix(): name
        for name, (version_file, _) in _ADDONS.items()
    }
    addons: set = set()
    for path in paths:
        addon = version_file_to_addon.get(path.strip())
        if addon is None:
            continue
        addons.add(addon)
        if addon == 'frankenrouter':
            addons.add('frankenrouter_ident')
    return sorted(addons)


def main() -> None:  # pylint: disable=too-many-branches,too-many-statements
    """Bump the chosen addon's version number and remind about its changelog."""
    parser = argparse.ArgumentParser(description="Bump an addon's version number.")
    parser.add_argument('addon', nargs='?', default=None, choices=sorted(_ALL_ADDONS.keys()))
    parser.add_argument(
        '--detect-changed', action='store_true',
        help="CI mode: read newline-separated file paths from stdin, print a JSON "
             "array of addon names whose .version file is among them, and exit. "
             "Ignores every other argument.")
    bump_group = parser.add_mutually_exclusive_group()
    bump_group.add_argument(
        '--minor', action='store_true',
        help="Bump the minor version instead of the patch version "
             "(e.g. 1.23.4 -> 1.24.0).")
    bump_group.add_argument(
        '--major', action='store_true',
        help="Bump the major version instead of the patch version "
             "(e.g. 1.23.4 -> 2.0.0).")
    args = parser.parse_args()

    if args.detect_changed:
        paths = [line for line in sys.stdin.read().splitlines() if line.strip()]
        print(json.dumps(_map_changed_files(paths)))
        return

    if args.addon is None:
        _scan_for_pending_bumps()
        return

    level = 'major' if args.major else 'minor' if args.minor else 'patch'

    version_file, changelog_file = _ALL_ADDONS[args.addon]

    if not version_file.exists():
        print(f"Version file not found: {version_file}", file=sys.stderr)
        sys.exit(1)

    try:
        current = _parse_version(version_file.read_text(encoding='utf-8'))
    except ValueError as exc:
        print(f"Error reading {version_file}: {exc}", file=sys.stderr)
        sys.exit(1)

    new = _bump(current, level)
    current_str, new_str = _format(current), _format(new)

    print(f"The current {args.addon} version is {current_str}, "
          f"new version will be {new_str}")
    if not _confirm("Is this OK?", default=True):
        print("Aborted, no changes made.")
        sys.exit(0)

    if level == 'patch':
        # A patch release is small enough that no changelog reminder is
        # needed -- don't nag for the common case.
        pass
    else:
        print()
        if changelog_file.exists():
            print(f"Changelog for {args.addon} is located at: "
                  f"{changelog_file.relative_to(_ROOT)}")
        else:
            print(f"No changelog file found for {args.addon} "
                  f"(expected at {changelog_file.relative_to(_ROOT)}).")
            if _confirm("Create it now with a skeleton entry?", default=True):
                changelog_file.parent.mkdir(parents=True, exist_ok=True)
                today = datetime.date.today().isoformat()
                changelog_file.write_text(
                    f"# {args.addon} changelog\n\n"
                    f"## {new_str} ({today})\n\n"
                    f"- \n",
                    encoding='utf-8')
                print(f"Created {changelog_file.relative_to(_ROOT)} -- fill in "
                      f"the entry above before committing.")

        if level == 'minor':
            print("Consider adding a changelog entry for this release.")
        else:
            answer = input("Have you updated the changelog? Type YES to confirm: ")
            if answer.strip() != "YES":
                print("Aborted, no changes made.")
                sys.exit(0)

    version_file.write_text(new_str + "\n", encoding='utf-8')
    print()
    print(f"Updated {version_file.relative_to(_ROOT)} to {new_str}.")
    print("Remember to commit, push, and tag as needed -- this script does not do that for you.")


if __name__ == '__main__':
    main()
