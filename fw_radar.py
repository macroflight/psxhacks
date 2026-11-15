"""Weather radar (RainViewer) helpers shared by frankenweather.py and fw_scanner.py.

RainViewer's tiles come in one colour scheme only since 2026 ("Universal
Blue": the colour scheme in a tile URL is ignored, zoom 7 is the most). Its
published colour table (rainviewer.com/files/rainviewer_api_colors_table.csv)
maps every colour to a reflectivity in whole dBZ, rain and snow apart, so a
tile decodes exactly (checked on live tiles, 2026-11-15). The old scheme-2
guess (blue light, green moderate, warm heavy) read Universal Blue's beige
drizzle (-10 to +10 dBZ) as heavy rain.
"""
from typing import Optional

# Echo classes (0 none, 1 light, 2 moderate, 3 heavy / CB core) by dBZ
ECHO_LIGHT_DBZ = 15
ECHO_MODERATE_DBZ = 35
ECHO_HEAVY_DBZ = 50

# Universal Blue colour (RGBA hex) -> dBZ, from RainViewer's colour table
_RAIN = {
    '63615914': -10, '66635a19': -9, '69665c1e': -8, '6c685d24': -7, '6f6b5f29': -6,
    '726e612e': -5, '75706234': -4, '78736439': -3, '7c75653e': -2, '7f786744': -1, '827b6949': 0,
    '857d6a4e': 1, '88806c54': 2, '8b826d59': 3, '8e856f5e': 4, '92887164': 5, '9e93756e': 6,
    'aa9e7978': 7, 'b6a97e82': 8, 'c2b4828c': 9, 'cec08796': 10, 'd2c48ba0': 11, 'd6c88faa': 12,
    'dacc93b4': 13, 'ded097be': 14, '88ddeeff': 15, '6cd1ebff': 16, '51c5e8ff': 17, '36bae5ff': 18,
    '1baee2ff': 19, '00a3e0ff': 20, '009ad5ff': 21, '0091caff': 22, '0088bfff': 23, '007fb4ff': 24,
    '0077aaff': 25, '0070a3ff': 26, '00699cff': 27, '006295ff': 28, '005b8eff': 29, '005588ff': 30,
    '005180ff': 31, '004e78ff': 32, '004a70ff': 33, '004768ff': 34, 'ffee00ff': 35, 'ffe000ff': 36,
    'ffd200ff': 37, 'ffc500ff': 38, 'ffb700ff': 39, 'ffaa00ff': 40, 'ff9f00ff': 41, 'ff9500ff': 42,
    'ff8b00ff': 43, 'ff8100ff': 44, 'ff4400ff': 45, 'f23600ff': 46, 'e62800ff': 47, 'd91b00ff': 48,
    'cd0d00ff': 49, 'c10000ff': 50, 'a80000ff': 51, '8f0000ff': 52, '760000ff': 53, '5d0000ff': 54,
    'ffaaffff': 55, 'ff9fffff': 56, 'ff95ffff': 57, 'ff8bffff': 58, 'ff81ffff': 59, 'ff77ffff': 60,
    'ff6cffff': 61, 'ff62ffff': 62, 'ff58ffff': 63, 'ff4effff': 64, 'ffffffff': 65, '00ff00ff': 75,
}
_SNOW = {
    'ceffff0c': -9, 'cdffff19': -8, 'ccffff26': -7, 'cbffff33': -6, 'cbffff3f': -5, 'caffff4c': -4,
    'c9ffff59': -3, 'c8ffff66': -2, 'c7ffff72': -1, 'c7ffff7f': 0, 'c6ffff8c': 1, 'c5ffff99': 2,
    'c4ffffa5': 3, 'c3ffffb2': 4, 'c3ffffbf': 5, 'c2ffffcc': 6, 'c1ffffd8': 7, 'c0ffffe5': 8,
    'bffffff2': 9, 'bfffffff': 10, 'b8f8ffff': 11, 'b2f2ffff': 12, 'abebffff': 13, 'a5e5ffff': 14,
    '9fdfffff': 15, '98d8ffff': 16, '92d2ffff': 17, '8bcbffff': 18, '85c5ffff': 19, '7fbfffff': 20,
    '78b8ffff': 21, '72b2ffff': 22, '6babffff': 23, '65a5ffff': 24, '5f9fffff': 25, '5b9bffff': 26,
    '5898ffff': 27, '5595ffff': 28, '5292ffff': 29, '4f8fffff': 30, '4b8bffff': 31, '4888ffff': 32,
    '4585ffff': 33, '4282ffff': 34, '3f7fffff': 35, '3b7bffff': 36, '3878ffff': 37, '3575ffff': 38,
    '3272ffff': 39, '2f6fffff': 40, '2b6bffff': 41, '2868ffff': 42, '2565ffff': 43, '2262ffff': 44,
    '1f5fffff': 45, '1b5bffff': 46, '1858ffff': 47, '1555ffff': 48, '1252ffff': 49, '0f4fffff': 50,
    '0c4bffff': 51, '0948ffff': 52, '0645ffff': 53, '0242ffff': 54, '003fffff': 55, '003bffff': 56,
    '0038ffff': 57, '0035ffff': 58, '0032ffff': 59, '002fffff': 60, '002bffff': 61, '0028ffff': 62,
    '0025ffff': 63, '0022ffff': 64, '001fffff': 65, '001bffff': 66, '0018ffff': 67, '0015ffff': 68,
    '0012ffff': 69, '000fffff': 70, '000cffff': 71, '0009ffff': 72, '0006ffff': 73, '0002ffff': 74,
    '0000ffff': 75,
}


def pixel_dbz(r: int, g: int, b: int, a: int) -> Optional[int]:
    """Return a tile pixel's reflectivity (dBZ), or None for no echo (or an unknown colour)."""
    if a == 0:
        return None
    key = f"{r:02x}{g:02x}{b:02x}{a:02x}"
    dbz = _RAIN.get(key)
    return dbz if dbz is not None else _SNOW.get(key)


def echo_class(dbz: Optional[int]) -> int:
    """Return the echo class 0-3 for a reflectivity (None: 0)."""
    if dbz is None or dbz < ECHO_LIGHT_DBZ:
        return 0
    if dbz < ECHO_MODERATE_DBZ:
        return 1
    if dbz < ECHO_HEAVY_DBZ:
        return 2
    return 3


def pixel_echo(r: int, g: int, b: int, a: int) -> int:
    """Return a tile pixel's echo class 0-3."""
    return echo_class(pixel_dbz(r, g, b, a))
