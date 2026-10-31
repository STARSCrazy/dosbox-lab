# SPDX-License-Identifier: GPL-2.0-or-later
"""Snapshots: memory, registers, descriptors, paging and frame in one directory."""
import hashlib
import json
import os
import re
from pathlib import Path

from .errors import DmcpError

FORMAT = "dosbox-lab-snapshot/1"
MAX_RANGE = 64 << 20
CONV = 0x100000


def _hex(value):
    return f"0x{value:08X}"


def _ranges(box, status, regs):
    """Linear ranges from the va mapping; without a mapping, in protected mode from CS/DS (base != 0)."""
    found = []
    for module in box.modules():
        for seg in module["segments"]:
            base = int(seg["base_linear"], 16)
            start = (base + int(seg["va_start"], 16)) & 0xFFFFFFFF
            end = min(base + int(seg["va_end"], 16) + 1, start + MAX_RANGE, 1 << 32)
            found.append((start, end, module["module"], seg["name"]))
    if not found and status["pmode"]:
        for name in ("cs", "ds"):
            sel, base = regs["seg"][name]["sel"], int(regs["seg"][name]["base"], 16)
            if base == 0:
                continue  # flat segment: no useful range
            try:
                table = "ldt" if int(sel, 16) & 4 else "gdt"
                entry = box.descriptors(table, selector=f"0x{sel}")["entries"][0]
                limit = int(entry["limit"], 16)
            except (DmcpError, IndexError, KeyError):
                continue
            found.append((base, min(base + limit + 1, base + MAX_RANGE, 1 << 32), None, name))
    return _pieces(found)


def _pieces(found):
    """Merge ranges (start, end, module, name) and split them into pieces of at most MAX_RANGE
    (mem.dump accepts no more)."""
    merged = []
    for start, end, module, name in sorted(found):
        if merged and start <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], end)
            continue
        merged.append({"start": start, "end": end, "module": module, "name": name})
    pieces = []
    for r in merged:
        pos = r["start"]
        while pos < r["end"]:
            pieces.append({**r, "start": pos, "end": min(r["end"], pos + MAX_RANGE)})
            pos += MAX_RANGE
    return pieces


def _next_nr(out):
    """Next snapshot number: highest existing one + 1."""
    nrs = [int(m.group(1)) for p in out.glob("snapshot_*") if (m := re.match(r"snapshot_(\d+)", p.name))]
    return max(nrs, default=0) + 1


def _move(info, target, stem):
    """mem.dump creates <name>.bin/.json in the output directory; move them into the snapshot directory."""
    os.replace(info["path"], target / f"{stem}.bin")
    os.replace(info["json_path"], target / f"{stem}.json")
    return [target / f"{stem}.bin", target / f"{stem}.json"]


def take(box, label, with_extension=False, until=None):
    status = box.status()
    was_paused = status["paused"]
    we_paused = False
    try:
        if not was_paused:
            status = box.pause(until=until)  # on timeout the api disarms the condition and keeps running
            we_paused = True
        return _take(box, label, status, was_paused, with_extension)
    finally:
        if we_paused:
            box.resume()


def _take(box, label, status, was_paused, with_extension):
    out = box.inst.outdir
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", label)[:40] or "snapshot"
    nr = _next_nr(out)
    target = out / f"snapshot_{nr:03d}_{safe}"
    target.mkdir()
    files = []

    def save_json(name, value):
        path = target / name
        path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
        files.append(path)

    files += _move(box.mem_dump("p:00000000", CONV, "_snap_conv"), target, "mem_conv")
    regs = box.registers()
    ranges = _ranges(box, status, regs)
    for r in ranges:
        info = box.mem_dump(f"l:{r['start']:08X}", r["end"] - r["start"], "_snap_lin")
        files += _move(info, target, f"mem_lin_{r['start']:08X}")
    if with_extension and status["ram_bytes"] > CONV:
        ext = target / "mem_phys_ext.bin"
        with ext.open("wb") as fh:
            pos = CONV
            while pos < status["ram_bytes"]:
                length = min(MAX_RANGE, status["ram_bytes"] - pos)
                info = box.mem_dump(f"p:{pos:08X}", length, "_snap_ext")
                fh.write(Path(info["path"]).read_bytes())
                os.remove(info["path"])
                os.remove(info["json_path"])
                pos += length
        files.append(ext)
    save_json("regs.json", regs)
    descriptors = {}
    for table in ("gdt", "ldt", "idt"):
        try:
            descriptors[table] = box.descriptors(table)
        except DmcpError as exc:
            descriptors[table] = {"error": exc.code, "message": exc.message}
    save_json("descriptors.json", descriptors)
    paging = {"cr3": regs["ctl"]["cr3"], "paging": regs["paging"], "ranges": []}
    for r in ranges:
        try:
            paging["ranges"].append(box.paging(f"l:{r['start']:08X}", r["end"] - r["start"]))
        except DmcpError as exc:
            paging["ranges"].append({"start": _hex(r["start"]), "error": exc.code, "message": exc.message})
    save_json("paging.json", paging)
    save_json("video.json", box.video_state())
    try:
        shot = box.screenshot("_snap_screen")
        os.replace(shot["path"], target / "screen.png")
        files.append(target / "screen.png")
    except DmcpError:
        pass  # no frame (e.g. none rendered yet): the snapshot stays valid
    range_info = [{"start": _hex(r["start"]), "end": _hex(r["end"]), "module": r["module"], "name": r["name"]}
                  for r in ranges]
    hints = []
    if not status["pmode"] and any(r["end"] > status["ram_bytes"] for r in ranges):
        hints.append("guest in real mode, program paging inactive: linear ranges above the RAM are missing "
                        "(mem_lin_*.json 'missing'). Remedy: snapshot(..., until=\"pmode\")")
    meta = {"format": FORMAT, "label": label, "time_ms": status["time_ms"], "paused_before": was_paused,
            "status": status, "modules": box.modules(), "ranges": range_info,
            "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}}
    result = {"path": str(target), "files": sorted(p.name for p in files) + ["meta.json"], "ranges": range_info}
    if hints:
        meta["hints"] = result["hints"] = hints
    (target / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
