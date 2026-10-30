"""Momentaufnahmen (Spec 5.8): Speicher, Register, Deskriptoren, Paging und Bild in einem Verzeichnis."""
import hashlib
import json
import os
import re
from pathlib import Path

from .errors import DmcpError

FORMAT = "dosbox-mcp-snapshot/1"
MAX_BEREICH = 64 << 20
KONV = 0x100000


def _hex(value):
    return f"0x{value:08X}"


def _ranges(box, status, regs):
    """Lineare Bereiche aus der va-Abbildung; ohne Abbildung im PM aus CS/DS (Basis != 0)."""
    found = []
    for module in box.modules():
        for seg in module["segmente"]:
            basis = int(seg["basis_linear"], 16)
            start = (basis + int(seg["va_start"], 16)) & 0xFFFFFFFF
            end = min(basis + int(seg["va_ende"], 16) + 1, start + MAX_BEREICH, 1 << 32)
            found.append((start, end, module["modul"], seg["name"]))
    if not found and status["pmode"]:
        for name in ("cs", "ds"):
            sel, basis = regs["seg"][name]["sel"], int(regs["seg"][name]["basis"], 16)
            if basis == 0:
                continue  # flaches Segment: kein sinnvoller Bereich
            try:
                table = "ldt" if int(sel, 16) & 4 else "gdt"
                entry = box.descriptors(table, selector=f"0x{sel}")["eintraege"][0]
                limit = int(entry["limit"], 16)
            except (DmcpError, IndexError, KeyError):
                continue
            found.append((basis, min(basis + limit + 1, basis + MAX_BEREICH, 1 << 32), None, name))
    return _stuecke(found)


def _stuecke(found):
    """Bereiche (start, ende, modul, name) zusammenlegen und in Stuecke von hoechstens MAX_BEREICH teilen
    (mem.dump nimmt nicht mehr)."""
    merged = []
    for start, end, modul, name in sorted(found):
        if merged and start <= merged[-1]["ende"]:
            merged[-1]["ende"] = max(merged[-1]["ende"], end)
            continue
        merged.append({"start": start, "ende": end, "modul": modul, "name": name})
    pieces = []
    for r in merged:
        pos = r["start"]
        while pos < r["ende"]:
            pieces.append({**r, "start": pos, "ende": min(r["ende"], pos + MAX_BEREICH)})
            pos += MAX_BEREICH
    return pieces


def _next_nr(out):
    """Naechste Snapshot-Nummer: hoechste vorhandene + 1."""
    nrs = [int(m.group(1)) for p in out.glob("snapshot_*") if (m := re.match(r"snapshot_(\d+)", p.name))]
    return max(nrs, default=0) + 1


def _move(info, target, stem):
    """mem.dump legt <name>.bin/.json im Ausgabeordner an; hier ins Snapshot-Verzeichnis verschieben."""
    os.replace(info["pfad"], target / f"{stem}.bin")
    os.replace(info["json_pfad"], target / f"{stem}.json")
    return [target / f"{stem}.bin", target / f"{stem}.json"]


def take(box, label, mit_erweiterung=False, bis=None):
    status = box.status()
    was_paused = status["paused"]
    we_paused = False
    try:
        if not was_paused:
            status = box.pause(until=bis)  # on timeout the api disarms the condition and keeps running
            we_paused = True
        return _take(box, label, status, was_paused, mit_erweiterung)
    finally:
        if we_paused:
            box.resume()


def _take(box, label, status, was_paused, mit_erweiterung):
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

    files += _move(box.mem_dump("p:00000000", KONV, "_snap_conv"), target, "mem_conv")
    regs = box.registers()
    ranges = _ranges(box, status, regs)
    for r in ranges:
        info = box.mem_dump(f"l:{r['start']:08X}", r["ende"] - r["start"], "_snap_lin")
        files += _move(info, target, f"mem_lin_{r['start']:08X}")
    if mit_erweiterung and status["ram_bytes"] > KONV:
        ext = target / "mem_phys_ext.bin"
        with ext.open("wb") as fh:
            pos = KONV
            while pos < status["ram_bytes"]:
                length = min(MAX_BEREICH, status["ram_bytes"] - pos)
                info = box.mem_dump(f"p:{pos:08X}", length, "_snap_ext")
                fh.write(Path(info["pfad"]).read_bytes())
                os.remove(info["pfad"])
                os.remove(info["json_pfad"])
                pos += length
        files.append(ext)
    save_json("regs.json", regs)
    descriptors = {}
    for table in ("gdt", "ldt", "idt"):
        try:
            descriptors[table] = box.descriptors(table)
        except DmcpError as exc:
            descriptors[table] = {"fehler": exc.code, "meldung": exc.message}
    save_json("descriptors.json", descriptors)
    paging = {"cr3": regs["ctl"]["cr3"], "paging": regs["paging"], "bereiche": []}
    for r in ranges:
        try:
            paging["bereiche"].append(box.paging(f"l:{r['start']:08X}", r["ende"] - r["start"]))
        except DmcpError as exc:
            paging["bereiche"].append({"start": _hex(r["start"]), "fehler": exc.code, "meldung": exc.message})
    save_json("paging.json", paging)
    save_json("video.json", box.video_state())
    try:
        shot = box.screenshot("_snap_screen")
        os.replace(shot["pfad"], target / "screen.png")
        files.append(target / "screen.png")
    except DmcpError:
        pass  # kein Bild (z. B. noch kein Frame): Momentaufnahme bleibt gueltig
    bereiche = [{"start": _hex(r["start"]), "ende": _hex(r["ende"]), "modul": r["modul"], "name": r["name"]}
                for r in ranges]
    hinweise = []
    if not status["pmode"] and any(r["ende"] > status["ram_bytes"] for r in ranges):
        hinweise.append("Gast im Real Mode, Programm-Paging inaktiv: lineare Bereiche oberhalb des RAMs fehlen "
                        "(mem_lin_*.json 'fehlend'). Abhilfe: snapshot(..., bis=\"pmode\")")
    meta = {"format": FORMAT, "label": label, "zeit_ms": status["zeit_ms"], "pausiert_vorher": was_paused,
            "status": status, "module": box.modules(), "bereiche": bereiche,
            "dateien": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}}
    result = {"pfad": str(target), "dateien": sorted(p.name for p in files) + ["meta.json"], "bereiche": bereiche}
    if hinweise:
        meta["hinweise"] = result["hinweise"] = hinweise
    (target / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
