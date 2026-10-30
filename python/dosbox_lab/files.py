"""Lesehilfen fuer die Aufzeichnungsdateien (auch fuer externe Werkzeuge nutzbar)."""
import json
import struct
from pathlib import Path


def read_jsonl(path):
    """Eine JSON-Zeile je Eintrag; leere Zeilen werden uebersprungen."""
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def read_coverage(path):
    """DMCV-Datei (Spec 5.7) -> {linear_start: bitmap (8192 Byte)}."""
    data = Path(path).read_bytes()
    if data[:4] != b"DMCV":
        raise ValueError(f"{path}: keine DMCV-Datei")
    version, block_size, count = struct.unpack_from("<III", data, 4)
    if version != 1 or block_size != 65536:
        raise ValueError(f"{path}: Version {version} / Blockgroesse {block_size} unbekannt")
    blocks, pos = {}, 16
    for _ in range(count):
        (start,) = struct.unpack_from("<I", data, pos)
        blocks[start] = data[pos + 4:pos + 4 + 8192]
        pos += 4 + 8192
    if pos != len(data):
        raise ValueError(f"{path}: Laenge passt nicht zur Blockzahl")
    return blocks


def covered(blocks, linear):
    """True, wenn an `linear` ein Befehl begonnen hat."""
    bitmap = blocks.get(linear & 0xFFFF0000)
    if bitmap is None:
        return False
    offset = linear & 0xFFFF
    return bool(bitmap[offset >> 3] & (1 << (offset & 7)))
