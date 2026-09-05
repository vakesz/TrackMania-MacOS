"""Generate a minimal uncompressed TMNF SystemConfig, with no account data.

Chunk layouts: GBX.NET Engines/System/CSystemConfig{,Display}.chunkl.
Only display and opponent settings are supplied. The game fills other defaults.
"""
import struct


def words(*values):
    return struct.pack('<' + 'I' * len(values), *values)


def chunk(ident, data):
    return words(ident) + b'PIKS' + words(len(data)) + data


def generate(settings):
    end = words(0xFACADE01)
    display = b''.join([
        chunk(0x0B013001, words(settings['width'], settings['height'], 2, 1, 0, 0, 0, 0)),
        chunk(0x0B013009, words(0, 0, int(settings['post_processing']), 0)),
        chunk(0x0B013010, struct.pack('<f', 1.0)),
        chunk(0x0B01301C, words(1, settings['thread_count'])),
        chunk(0x0B013021, words(int(settings['water_geometry']))),
        chunk(0x0B013022, words(1, 1, 0, 2, settings['shader_quality'], 1, settings['anisotropy'])),
        end,
    ])
    body = chunk(0x0B005020, words(0) + display)
    body += chunk(0x0B005034, words(1, 1, 0, 1, settings['max_opponents'], 0, 0)) + end
    # Version 6, binary integers, uncompressed reference table/body, root class,
    # empty user header, one node, no external node references.
    return b'GBX' + struct.pack('<H', 6) + b'BUUR' + words(0x0B005000, 0, 1, 0) + body
