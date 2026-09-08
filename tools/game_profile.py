"""Generate a minimal uncompressed TMNF SystemConfig, with no account data.

Chunk layouts: CSystemConfig{,Display}.chunkl under
https://github.com/BigBang1112/gbx-net/tree/master/Src/GBX.NET/Engines/System
Unnamed fields in those layouts remain unknown here, with preset values retained.
Only display and opponent settings are supplied. The game fills other defaults.
"""
import struct


def words(*values):
    return struct.pack('<' + 'I' * len(values), *values)


def chunk(ident, data):
    return words(ident) + b'PIKS' + words(len(data)) + data


def generate(settings):
    # GBX int fields are signed 32-bit. These are encoding/enum limits, not
    # claims about resolutions, thread counts or opponent counts the game supports.
    for name, low, high in (('width', 1, 0x7FFFFFFF), ('height', 1, 0x7FFFFFFF),
                           ('thread_count', 1, 0x7FFFFFFF), ('max_opponents', 0, 0x7FFFFFFF),
                           ('shader_quality', 0, 3), ('anisotropy', 0, 6)):
        if name not in settings or type(settings[name]) is not int or not low <= settings[name] <= high:
            raise ValueError(f'{name} must be an integer in {low}..{high}')
    for name in ('post_processing', 'water_geometry'):
        if name not in settings or type(settings[name]) is not bool:
            raise ValueError(f'{name} must be a boolean')

    end = words(0xFACADE01)
    display = b''.join([
        # ScreenSizeFS; three unknown ints; RefreshRate; sync/mode bool; unknown bool.
        chunk(0x0B013001, words(settings['width'], settings['height'], 2, 1, 0, 0, 0, 0)),
        # Two unknown ints; PostFx; unknown bool.
        chunk(0x0B013009, words(0, 0, int(settings['post_processing']), 0)),
        # GeomLodScaleZ.
        chunk(0x0B013010, struct.pack('<f', 1.0)),
        # MultiThread; ThreadCountMax.
        chunk(0x0B01301C, words(1, settings['thread_count'])),
        # WaterGeomStadium.
        chunk(0x0B013021, words(int(settings['water_geometry']))),
        # Unknown int; Customize; two unknown ints; shader quality; unknown int;
        # FilterAnisoQ. Shader quality is inferred from the later 0x02A layout
        # (Very Fast/Fast/Nice/Very Nice), not named in GBX.NET's TMF 0x022.
        chunk(0x0B013022, words(1, 1, 0, 2, settings['shader_quality'], 1, settings['anisotropy'])),
        end,
    ])
    # IsSafeMode=false, followed by the direct CSystemConfigDisplay chunks.
    body = chunk(0x0B005020, words(0) + display)
    # TmCarQuality; two unknown ints; TmOpponents; TmMaxOpponents;
    # unknown bool; TmBackgroundQuality.
    body += chunk(0x0B005034, words(1, 1, 0, 1, settings['max_opponents'], 0, 0)) + end
    # Version 6, binary integers, uncompressed reference table/body, root class,
    # empty user header, one node, no external node references.
    return b'GBX' + struct.pack('<H', 6) + b'BUUR' + words(0x0B005000, 0, 1, 0) + body
