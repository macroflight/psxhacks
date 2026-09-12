# EXE build system

`.github/workflows/build.yml` builds standalone Windows EXEs for individual
addons and publishes them as GitHub Releases, automatically, whenever a
release is cut with `release.py`. This file explains how it works and how
to operate it.

## Where it runs

The workflow only ever runs in the **public** repo,
[macroflight/psxhacks](https://github.com/macroflight/psxhacks). It is
never added to, and never runs in, `psxhacks-devel` (private) — every job
in the workflow has an explicit `if: github.repository ==
'macroflight/psxhacks'` guard, so even if a branch happened to exist with
the same name in the private repo, nothing would build there. Public repos
get unlimited GitHub Actions minutes and storage, so there is no minutes
budget to manage here (unlike a private repo, where a Windows runner costs
2x the raw minutes).

Day-to-day work on `devel` is completely unaffected — nothing in this
system reacts to `devel` at all.

## When it builds

The workflow triggers on push to two branches:

- **`testing`** — for trying the build system itself, or a specific
  addon's build, without touching `main`. Creates GitHub **pre-releases**
  tagged `<addon>-testing-v<version>`.
- **`main`** — real releases. Creates normal GitHub releases tagged
  `<addon>-v<version>`.

It does **not** rebuild everything on every push. A `detect` job (cheap,
runs on `ubuntu-latest`) diffs the pushed commits and looks specifically
for changed `*.version` files — the same files `release.py` is the only
thing that ever writes (see the repo's main `CLAUDE.md` /
`psxhacks_version.py`). Only the addon(s) whose `.version` file actually
changed get built; if none did, the (expensive, Windows) `build` job is
skipped entirely.

Special case: `router/frankenrouter.version` changing triggers builds of
**both** `frankenrouter` and `frankenrouter_ident`, since
`frankenrouter_ident` has no version file of its own — it always mirrors
frankenrouter's version (see `frankenrouter_ident.py`).

The addon/version-file mapping lives in exactly one place,
`release.py`'s `_ADDONS` dict, so it can never drift out of sync between
`release.py` itself and the CI workflow: the detect job pipes the list of
changed files straight into `release.py --detect-changed`, which prints
the resulting addon list as JSON.

## Manual builds

Use the "Run workflow" button on the Actions tab (`workflow_dispatch`).
Leave the `addon` input blank to run the same detection logic as a normal
push (useful to retry after a transient failure), or fill in a specific
addon name to force-build just that one regardless of whether its
`.version` file changed.

## How a release gets made

For each addon that needs building, the `build` job:

1. Runs `makepackages.ps1 -Addon <name>` (the same script you'd run
   locally — see its own header comment and `docs/BuildSystem.md`'s
   sibling, the repo's main README, for manual usage).
2. Reads the resulting version string from the addon's `.version` file
   (or `router/frankenrouter.version` for `frankenrouter`/
   `frankenrouter_ident`).
3. Creates a GitHub Release tagged `<addon>-v<version>` (`main`) or
   `<addon>-testing-v<version>` (`testing`, marked pre-release), with the
   bare `dist\<addon>.exe` as the only release asset.
4. Prunes older releases for that same addon+branch: anything beyond the
   configured retention count gets deleted (both the release and its git
   tag).

Only the venv is set up once per workflow run and PyInstaller/build steps
are reused across every addon in that run, `venv` reused via
`makepackages.ps1`'s own existing-venv check.

## Downloading an addon

Each addon has its own independent list of releases — go to the repo's
Releases page and find the tag for the addon you want
(`frankenweather-v1.2.4`, `frankenrouter-testing-v1.4.5`, etc.). Each
release has exactly one asset: that addon's `.exe`.

## Retention

Configured via the `RETAIN_MAIN` and `RETAIN_TESTING` env vars at the top
of `.github/workflows/build.yml` — each defaults to **5**, and they're
independent (an addon's `testing` history and `main` history are pruned
separately, since they use different tag prefixes). Raise or lower either
by editing that one line; no other logic needs to change.

## If something needs changing

- **Adding a new buildable addon**: give it a `.version` file (see
  `psxhacks_version.py`) and add it to `_ADDONS` in `release.py` — the
  workflow picks it up automatically, no workflow file changes needed.
- **Changing the addon → spec-file mapping**: that lives in
  `makepackages.ps1`'s `$AddonSpecs` table, not in the workflow.
