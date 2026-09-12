# -*- mode: python ; coding: utf-8 -*-

# frankenrouter_ident reports frankenrouter's own version (see
# psxhacks_version.get_version's version_dir override in frankenrouter_ident.py),
# so it bundles router/frankenrouter.version here -- not a version file of its own.

a = Analysis(
    ['frankenrouter_ident.py'],
    pathex=[],
    binaries=[],
    datas=[('router/frankenrouter.version', '.')],
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
    name='frankenrouter_ident',
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
