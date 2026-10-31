# SPDX-License-Identifier: GPL-2.0-or-later
"""Readers for the recording files (also usable by external tools)."""
import json
import struct
from pathlib import Path


def read_jsonl(path):
    """One JSON line per entry; empty lines are skipped."""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def read_coverage(path):
    """DMCV-File -> {linear_start: bitmap (8192 Byte)}."""
    data = Path(path).read_bytes()
    if data[:4] != b"DMCV":
        raise ValueError(f"{path}: not a DMCV file")
    version, block_size, count = struct.unpack_from("<III", data, 4)
    if version != 1 or block_size != 65536:
        raise ValueError(f"{path}: unknown version {version} / block size {block_size}")
    blocks, pos = {}, 16
    for _ in range(count):
        (start,) = struct.unpack_from("<I", data, pos)
        blocks[start] = data[pos + 4:pos + 4 + 8192]
        pos += 4 + 8192
    if pos != len(data):
        raise ValueError(f"{path}: length does not match the block count")
    return blocks


def covered(blocks, linear):
    """True if an instruction started at `linear`."""
    bitmap = blocks.get(linear & 0xFFFF0000)
    if bitmap is None:
        return False
    offset = linear & 0xFFFF
    return bool(bitmap[offset >> 3] & (1 << (offset & 7)))
