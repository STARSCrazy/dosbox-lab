"""Bremswirkung messen: DEBUG.COM je 5 s, Zuwachs von wert (16 Bit, Umlaeufe mitgezaehlt).

Aufruf (aus python/): C:/Python314/python.exe tools/t10_speed.py [cycles]   (Standard cycles=max; Abnahme Stufe 3)
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.util import start_prog, prog_psp  # noqa: E402

DAUER = 5.0
cycles = sys.argv[1] if len(sys.argv) > 1 else "max"
box = start_prog("DEBUG.COM", conf={"cpu": {"cycles": cycles}})
psp = prog_psp(box, "DEBUG.COM")
adr = f"r:{psp:04X}:0180"


def wert():
    return int.from_bytes(box.mem_read(adr, 2), "little")


def messen():
    total = 0
    maxd = 0
    last = wert()
    t0 = time.monotonic()
    while time.monotonic() - t0 < DAUER:
        time.sleep(0.002)
        v = wert()
        d = (v - last) & 0xFFFF
        maxd = max(maxd, d)
        total += d
        last = v
    return total / (time.monotonic() - t0), maxd


time.sleep(2)
ergebnis = {}
for runde in range(2):
    a = messen()
    ergebnis.setdefault("a ohne Hook", []).append(a)

    box.break_add(f"r:{psp:04X}:010A", bedingung="eax==0xDEADBEEF")
    b = messen()
    bl = box.break_list()
    box.break_remove(all=True)
    ergebnis.setdefault("b Haltepunkt nie wahr", []).append(b)
    assert not box.status()["paused"], "Haltepunkt hat gehalten"

    w = box.watch_add("l:200000", laenge=4, art="beides", aktion="protokollieren")
    c = messen()
    wl = box.watch_list()
    box.watch_remove(all=True)
    ergebnis.setdefault("c Waechter fremde Seite", []).append(c)

    box.call("emu.run_until", ausdruck="eax==0xDEADBEEF")
    d = messen()
    st = box.call("emu.pause")
    box.resume()
    ergebnis.setdefault("d run_until ausdruck", []).append(d)

print("cycles", cycles, "break_list", bl.get("eintraege"), "watch", wl)
base = sum(x[0] for x in ergebnis["a ohne Hook"]) / 2
for k, v in ergebnis.items():
    rate = sum(x[0] for x in v) / len(v)
    print(f"{k:28s} {rate/1e3:10.1f} k/s  Faktor {base/rate:5.2f}  ({rate/base*100:5.1f} %)  max_delta {max(x[1] for x in v)}  einzeln {[round(x[0]/1e3,1) for x in v]}")
box.stop()
