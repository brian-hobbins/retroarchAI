"""Tiny dependency-free PNG writer (stdlib only).

Good enough for occasional screenshots / observation frames. Not optimized for
speed; for the ~256x224 SNES framebuffer it's plenty fast at a few fps.
"""

from __future__ import annotations

import struct
import zlib


def write_png(path: str, width: int, height: int, rgb: bytes) -> None:
    """Write RGB888 pixel data (3 bytes/pixel, row-major, top-to-bottom) to a PNG file."""
    if len(rgb) != width * height * 3:
        raise ValueError(
            f"rgb length {len(rgb)} != width*height*3 ({width*height*3})"
        )
    with open(path, "wb") as f:
        f.write(encode_png(width, height, rgb))


def encode_png(width: int, height: int, rgb: bytes) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        out = struct.pack(">I", len(data)) + tag + data
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return out + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    # IHDR: width, height, bit depth 8, color type 2 (truecolor RGB), no interlace
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    # Each scanline is prefixed with a filter-type byte (0 = none).
    stride = width * 3
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw.extend(rgb[y * stride : (y + 1) * stride])
    idat = zlib.compress(bytes(raw), 6)

    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")
