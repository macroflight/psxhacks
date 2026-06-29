# -*- mode: python ; coding: utf-8 -*-

a = Analysis(
    ['frankenturb.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # rasterio is imported lazily inside _read_geotiff — invisible to Analysis
        'rasterio',
        # frankenturb/ package: name collision with frankenturb.py may prevent recursive scan
        'frankenturb',
        'frankenturb.boost',
        'frankenturb.cape',
        'frankenturb.cb',
        'frankenturb.cb_turbulence',
        'frankenturb.gairmet',
        'frankenturb.pirep',
        'frankenturb.terrain',
        'frankenturb.terrain.elevation',
        'frankenturb.terrain.tiles',
        'frankenturb.turbulence',
        'frankenturb.wind',
        'frankenturb.wind.fetcher',
        'frankenturb.wind.profile',
        # charset_normalizer: requests dependency with an optional compiled extension
        'charset_normalizer',
    ],
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
    name='frankenturb',
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
