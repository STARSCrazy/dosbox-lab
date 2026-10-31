"""Measure the slowdown: DEBUG.COM for 5 s each, increase of value (16 bit, wrap-arounds counted).

Call (from python/): C:/Python314/python.exe tools/t10_speed.py [cycles]   (default cycles=max; stage 3 acceptance)
"""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests.util import start_prog, prog_psp  # noqa: E402

DURATION = 5.0
cycles = sys.argv[1] if len(sys.argv) > 1 else "max"
box = start_prog("DEBUG.COM", conf={"cpu": {"cycles": cycles}})
psp = prog_psp(box, "DEBUG.COM")
addr = f"r:{psp:04X}:0180"


def value():
    return int.from_bytes(box.mem_read(addr, 2), "little")


def measure():
    total = 0
    maxd = 0
    last = value()
    t0 = time.monotonic()
    while time.monotonic() - t0 < DURATION:
        time.sleep(0.002)
        v = value()
        d = (v - last) & 0xFFFF
        maxd = max(maxd, d)
        total += d
        last = v
    return total / (time.monotonic() - t0), maxd


time.sleep(2)
result = {}
for _round in range(2):
    a = measure()
    result.setdefault("a without hook", []).append(a)

    box.break_add(f"r:{psp:04X}:010A", condition="eax==0xDEADBEEF")
    b = measure()
    bl = box.break_list()
    box.break_remove(all=True)
    result.setdefault("b breakpoint never true", []).append(b)
    assert not box.status()["paused"], "breakpoint halted"

    w = box.watch_add("l:200000", length=4, kind="both", action="log")
    c = measure()
    wl = box.watch_list()
    box.watch_remove(all=True)
    result.setdefault("c watchpoint on foreign page", []).append(c)

    box.call("emu.run_until", expression="eax==0xDEADBEEF")
    d = measure()
    st = box.call("emu.pause")
    box.resume()
    result.setdefault("d run_until expression", []).append(d)

print("cycles", cycles, "break_list", bl.get("entries"), "watch", wl)
base = sum(x[0] for x in result["a without hook"]) / 2
for k, v in result.items():
    rate = sum(x[0] for x in v) / len(v)
    print(f"{k:28s} {rate/1e3:10.1f} k/s  factor {base/rate:5.2f}  ({rate/base*100:5.1f} %)  max_delta {max(x[1] for x in v)}  single {[round(x[0]/1e3,1) for x in v]}")
box.stop()
