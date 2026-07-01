# -*- mode: python ; coding: utf-8 -*-
import os

# The Python SimConnect package does not bundle SimConnect.dll — it loads it
# by absolute path from its own package directory at runtime. We find it from
# the MSFS SDK at build time and place it there ('SimConnect' destination).
_sc_candidates = [
    os.path.join(os.environ.get('MSFS_SDK', ''), 'SimConnect SDK', 'lib', 'SimConnect.dll'),
    r'C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.dll',
]
_sc_dll = next((p for p in _sc_candidates if p and os.path.isfile(p)), None)
if _sc_dll:
    print(f'frankenmsfsbridge: found SimConnect.dll at {_sc_dll}')
else:
    print('frankenmsfsbridge: WARNING — SimConnect.dll not found; MSFS connection will fail at runtime.')

a = Analysis(
    ['frankenmsfsbridge.py'],
    pathex=[],
    binaries=[],
    datas=[(_sc_dll, 'SimConnect')] if _sc_dll else [],
    hiddenimports=[],
    hookspath=['pyinstaller_hooks'],
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
    name='frankenmsfsbridge',
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
