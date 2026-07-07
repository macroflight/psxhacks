# -*- mode: python ; coding: utf-8 -*-

import os

# frankenrouter/webapi.py imports fw_webui (first-party module at the
# psxhacks repo root) via a runtime sys.path.insert, since PyInstaller's
# static import analysis can't see paths added at runtime, it never bundles
# fw_webui.py unless the repo root is also on pathex here. SPECPATH is the
# directory containing this spec file (router/), so this works regardless
# of the cwd the build is invoked from.
_PSXHACKS_ROOT = os.path.join(SPECPATH, '..')  # noqa: F821  pylint: disable=undefined-variable

a = Analysis(
    ['frankenrouter.py'],
    pathex=[_PSXHACKS_ROOT],
    binaries=[],
    datas=[('frankenrouter/static', 'frankenrouter/static')],
    hiddenimports=['fw_webui'],
    hookspath=[os.path.join(_PSXHACKS_ROOT, 'pyinstaller_hooks')],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='frankenrouter',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
