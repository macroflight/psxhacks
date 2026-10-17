# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['show_boost_gauges.py'],
    pathex=[],
    binaries=[],
    # Shares frankenboostproxy's version file (see show_boost_gauges.py's
    # own __version__ = get_version("frankenboostproxy", __file__)) -
    # must be bundled here too, since a frozen build only ever looks
    # inside its own bundle (sys._MEIPASS), never next to the EXE on disk.
    datas=[('frankenboostproxy.version', '.')],
    hiddenimports=[],
    hookspath=[],
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
    name='show_boost_gauges',
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
