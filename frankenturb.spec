# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all, collect_data_files

# rasterio ships with bundled GDAL/PROJ DLLs and data files on Windows.
# collect_all pulls in the shared libraries, data directories, and the hook's
# hidden-import list (GDAL drivers, PROJ transforms, etc.).
datas_rasterio, binaries_rasterio, hiddenimports_rasterio = collect_all('rasterio')

# CA certificate bundle used by requests for HTTPS (tile downloads, Open-Meteo).
datas_certifi = collect_data_files('certifi')

a = Analysis(
    ['frankenturb.py'],
    pathex=[],
    binaries=binaries_rasterio,
    datas=datas_rasterio + datas_certifi,
    hiddenimports=(
        hiddenimports_rasterio +
        [
            # rasterio is imported lazily inside _read_geotiff — invisible to Analysis
            'rasterio',
            # frankenturb/ package: name collision with frankenturb.py may prevent recursive scan
            'frankenturb',
            'frankenturb.terrain',
            'frankenturb.terrain.tiles',
            'frankenturb.terrain.elevation',
            'frankenturb.wind',
            'frankenturb.wind.fetcher',
            'frankenturb.wind.profile',
            'frankenturb.turbulence',
            # charset_normalizer: requests dependency with an optional compiled extension
            'charset_normalizer',
        ]
    ),
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
