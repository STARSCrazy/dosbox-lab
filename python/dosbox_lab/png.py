# SPDX-License-Identifier: GPL-2.0-or-later
"""Minimal PNG reader for the frames written by the emulator (8-bit RGB, filter 0)."""
import struct
import zlib


def read_rgb(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, width, height, idat = 8, 0, 0, b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            if depth != 8 or color != 2:
                raise ValueError("only 8-bit RGB is supported")
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3
    rows = []
    for y in range(height):
        line = raw[y * (stride + 1):(y + 1) * (stride + 1)]
        if line[0] != 0:
            raise ValueError("only filter 0 is supported")
        rows.append(line[1:])
    return width, height, b"".join(rows)


def pixel(image, x, y):
    width, _, pixels = image
    i = (y * width + x) * 3
    return tuple(pixels[i:i + 3])
