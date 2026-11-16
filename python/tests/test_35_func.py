import json
import unittest
from pathlib import Path

from dosbox_lab import DmcpError, Dosbox
from dosbox_lab.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, wait_until


def func_psp(box):
    module = wait_until(lambda: next((m for m in box.modules() if m["module"] == "FUNC.COM"), None), timeout=15)
    return int(module["psp"], 16)


def record_func(box, states=2, name="fcalls"):
    """Start FUNC.COM, record all calls in 0200-02FF (with savestates) and return
    (psp, base, entries, calls_stop response). Afterwards FUNC.COM sits in its HLT loop."""
    run_typed(box, "FUNC")
    psp = func_psp(box)
    base = psp * 16
    box.calls_start(ranges=[{"from": f"l:{base + 0x200:08X}", "to": f"l:{base + 0x2FF:08X}"}],
                    states=states, name=name)
    box.mem_write(f"r:{psp:04X}:03F1", b"\x01")
    wait_until(lambda: box.mem_read(f"r:{psp:04X}:03F0", 1) == b"\x01", timeout=30)
    stop = box.calls_stop()
    return psp, base, read_jsonl(stop["file"]), stop


def by_target(entries, base):
    out = {}
    for e in entries:
        out.setdefault(int(e["target"]["linear"], 16) - base, []).append(e)
    return out


@need_build
class FuncStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.psp, cls.base, cls.entries, cls.stop = record_func(cls.box)
        cls.t = by_target(cls.entries, cls.base)

    def test_state_fields(self):
        calls = self.t[0x200]
        self.assertEqual(len(calls), 3)  # main 1x, called 2x
        with_state = [e for e in calls if "state" in e]
        self.assertEqual(len(with_state), 2)  # states=2 per target
        self.assertNotIn("state", sorted(calls, key=lambda e: e["call_id"])[2])
        for e in with_state:
            sav = Path(e["state"])
            self.assertTrue(sav.exists())
            self.assertEqual(sav.name, f"fcalls_{e['call_id']}.sav")
            info = json.loads(sav.with_suffix(".json").read_text())
            self.assertEqual(info["format"], "dosbox-lab-state/1")
            a = info["call"]
            self.assertEqual(a["call_id"], e["call_id"])
            self.assertEqual(a["kind"], "near")
            self.assertEqual(a["bits"], 16)
            self.assertEqual(a["calls_file"], "fcalls.jsonl")
            self.assertEqual(a["mode"], "real")
            self.assertEqual(a["target"]["linear"], e["target"]["linear"])
            ret = int(e["from"]["off"], 16) + 3  # call rel16 = 3 bytes
            self.assertEqual(int(a["return_address"]["off"], 16), ret)
            self.assertEqual(int(a["return_address"]["sel"], 16), int(e["from"]["seg"], 16))
            sp = int(e["regs_in"]["esp"], 16) & 0xFFFF
            self.assertEqual(int(a["esp_in"], 16), sp)
            self.assertEqual(int(a["esp_after"], 16), sp + 2)
            self.assertEqual(int(a["ss"], 16), int(e["regs_in"]["ss"], 16))
        meta = json.loads(Path(self.stop["json_path"]).read_text())
        self.assertEqual(meta["states"], 2)
        self.assertEqual(meta["max_states"], 1000)
        self.assertEqual(meta["states_written"], 7)  # sum 2, caller 1, with_int 1, tick 1, child 1, hma 1
        self.assertEqual(meta["states_omitted"], 0)

    def test_state_loads_exactly_at_target(self):
        e = self.t[0x220][0]
        self.box.pause()
        r = self.box.state_load(path=e["state"])
        self.assertTrue(r["loaded"])
        regs = self.box.registers()
        cs_base = int(regs["seg"]["cs"]["base"], 16)
        self.assertEqual(cs_base + int(regs["gp"]["eip"], 16), int(e["target"]["linear"], 16))
        for name in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp"):
            self.assertEqual(int(regs["gp"][name], 16), int(e["regs_in"][name], 16), name)

    def test_program_continues_running_after_load(self):
        e = self.t[0x200][0]
        self.box.pause()
        self.box.state_load(path=e["state"])
        self.box.mem_write(f"r:{self.psp:04X}:03F0", b"\x00")
        self.box.resume()
        wait_until(lambda: self.box.mem_read(f"r:{self.psp:04X}:03F0", 1) == b"\x01", timeout=30)
        self.assertEqual(self.box.mem_read(f"r:{self.psp:04X}:0300", 2), b"\x05\x00")

    def test_bad_states(self):
        for kw in ({"states": -1}, {"states": 1, "max_states": 0}):
            with self.assertRaises(DmcpError) as ctx:
                self.box.calls_start(targets=["l:1000"], **kw)
            self.assertEqual(ctx.exception.code, "bad_argument")


@need_build
class FuncRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.psp, cls.base, cls.entries, _ = record_func(cls.box)
        cls.t = by_target(cls.entries, cls.base)
        cls.sum = sorted(cls.t[0x200], key=lambda e: e["call_id"])[0]   # main: ax=3, bx=4
        cls.caller = cls.t[0x220][0]

    def low16(self, regs, name):
        return int(regs[name], 16) & 0xFFFF

    def test_ret_matches_recording(self):
        for e in (self.sum, self.caller, self.t[0x240][0]):
            r = self.box.func_run(e["state"])
            self.assertEqual(r["end"], "ret")
            self.assertEqual(r["call_id"], e["call_id"])
            for name in ("eax", "ebx", "ecx", "edx", "esp", "ebp"):
                self.assertEqual(r["regs_out"][name], e["regs_out"][name], name)
            self.assertEqual(r["regs_in"]["eax"], e["regs_in"]["eax"])
            self.assertGreater(r["instructions"], 0)
            self.assertFalse(r["irqs"])
            self.assertTrue(Path(r["path"]).exists())

    def test_twice_gives_same_result(self):
        a = self.box.func_run(self.caller["state"])
        b = self.box.func_run(self.caller["state"])
        self.assertEqual(a["regs_out"], b["regs_out"])
        self.assertEqual(a["instructions"], b["instructions"])

    def test_write(self):
        addr = f"l:{self.base + 0x200:08X}"
        r = self.box.func_run(self.sum["state"], write=[{"address": addr, "hex": "B82A00C3"}])  # mov ax,2Ah; ret
        self.assertEqual(r["end"], "ret")
        self.assertEqual(self.low16(r["regs_out"], "eax"), 0x2A)
        self.assertEqual(len(r["written"]), 1)
        self.assertEqual(r["written"][0]["length"], 4)
        self.assertEqual(int(r["written"][0]["address"]["linear"], 16), self.base + 0x200)

    def test_regs(self):
        r = self.box.func_run(self.sum["state"], regs={"eax": "0x10", "ebx": "0x20"})
        self.assertEqual(self.low16(r["regs_in"], "eax"), 0x10)
        self.assertEqual(self.low16(r["regs_out"], "eax"), 0x30)

    def test_stack(self):
        e = self.sum
        stack = e["stack"][:4] + "3412"   # keep the return address, next word = 1234h
        r = self.box.func_run(e["state"], stack=stack)
        self.assertEqual(r["end"], "ret")
        ss = int(e["regs_in"]["ss"], 16)
        sp = int(e["regs_in"]["esp"], 16) & 0xFFFF
        self.assertEqual(self.box.mem_read(f"r:{ss:04X}:{sp + 2:04X}", 2), b"\x34\x12")

    def test_aborted(self):
        addr = f"l:{self.base + 0x200:08X}"
        r = self.box.func_run(self.sum["state"], write=[{"address": addr, "hex": "EBFE"}], max_instructions=100000)
        self.assertEqual(r["end"], "aborted")
        self.assertEqual(r["instructions"], 100000)

    def test_bad(self):
        info = self.box.state_save("without_call")
        for kw in ({"state": info["path"]},
                   {"state": self.sum["state"], "write": [{"address": "l:1000", "hex": "ABC"}]},
                   {"state": self.sum["state"], "regs": {"cs": "0x10"}},
                   {"state": self.sum["state"], "max_instructions": 0}):
            with self.assertRaises(DmcpError) as ctx:
                self.box.func_run(**kw)
            self.assertEqual(ctx.exception.code, "bad_argument", kw)


FUNC_SYMBOLS = {"format": "decomp-symbols/1", "module": "FUNC.COM", "address_space": "rel16",
                "symbols": [{"name": "result", "addr": "0000:0300", "kind": "variable", "size": 2},
                            {"name": "counter", "addr": "0000:0302", "kind": "variable", "size": 2}]}


@need_build
class FuncChangedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.psp, cls.base, cls.entries, _ = record_func(cls.box)
        t = by_target(cls.entries, cls.base)
        cls.sum = sorted(t[0x200], key=lambda e: e["call_id"])[0]
        cls.caller = t[0x220][0]
        cls.hma = t[0x2E0][0]
        path = cls.box.inst.rundir / "func_sym.json"
        path.write_text(json.dumps(FUNC_SYMBOLS), encoding="utf-8")
        cls.box.symbols_load(str(path))

    def test_changed_nur_global(self):
        r = self.box.func_run(self.sum["state"])
        self.assertEqual(r["end"], "ret")
        self.assertFalse(r["changed_truncated"])
        self.assertEqual(len(r["changed"]), 1, r["changed"])
        g = r["changed"][0]
        self.assertEqual(int(g["address"]["linear"], 16), self.base + 0x300)
        self.assertEqual(g["length"], 1)              # only the low byte changes (00 -> 07)
        self.assertEqual(g["before"], "00")
        self.assertEqual(g["after"], "07")
        self.assertEqual(g["address"]["symbol"], "result")
        self.assertEqual(g["symbol_offset"], "0x0")
        self.assertEqual(g["address"]["module"], "FUNC.COM")
        self.assertIn("phys", g["address"])

    def test_gaps_are_bridged(self):
        r = self.box.func_run(self.caller["state"])  # counter 0->1 (0302), result 07->05 (0300)
        self.assertEqual(len(r["changed"]), 1, r["changed"])
        g = r["changed"][0]
        self.assertEqual(int(g["address"]["linear"], 16), self.base + 0x300)
        self.assertEqual(g["length"], 3)
        self.assertEqual(g["before"], "070000")
        self.assertEqual(g["after"], "050001")

    def test_hma_in_segment_form(self):
        r = self.box.func_run(self.hma["state"])  # writes BEEFh to FFFF:FF00
        self.assertEqual(r["end"], "ret")
        self.assertEqual(len(r["changed"]), 1, r["changed"])
        a = r["changed"][0]["address"]
        self.assertEqual(int(a["linear"], 16), 0x10FEF0)
        self.assertEqual(int(a["seg"], 16), 0xFFFF)
        self.assertEqual(int(a["off"], 16), 0xFF00)
        self.assertEqual(r["changed"][0]["after"], "EFBE")


@need_build
class FuncFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.psp, cls.base, cls.entries, _ = record_func(cls.box)
        cls.t = by_target(cls.entries, cls.base)
        cls.sum = sorted(cls.t[0x200], key=lambda e: e["call_id"])[0]
        cls.caller = cls.t[0x220][0]

    def test_ints(self):
        r = self.box.func_run(self.t[0x240][0]["state"])
        self.assertEqual(r["end"], "ret")
        self.assertEqual(len(r["ints"]), 1)
        i = r["ints"][0]
        self.assertEqual(i["vector"], "0x21")
        self.assertEqual((int(i["regs_in"]["eax"], 16) >> 8) & 0xFF, 0x30)
        self.assertIn("regs_out", i)
        self.assertEqual(i["regs_out"]["eax"], r["regs_out"]["eax"])
        self.assertEqual(int(i["addr"]["linear"], 16), self.base + 0x242)
        self.assertEqual(r["ints_omitted"], 0)

    def test_irq_hold(self):
        tick = self.t[0x280][0]["state"]
        r = self.box.func_run(tick, max_instructions=2000000)
        self.assertEqual(r["end"], "aborted")
        r = self.box.func_run(tick, irqs=True)
        self.assertEqual(r["end"], "ret")
        self.assertTrue(r["irqs"])

    def test_program_end(self):
        addr = f"l:{self.base + 0x200:08X}"
        r = self.box.func_run(self.sum["state"], write=[{"address": addr, "hex": "B8074CCD21"}])
        self.assertEqual(r["end"], "program_end")

    def test_interrupted_breakpoint(self):
        self.box.break_add(self.base + 0x200)
        try:
            r = self.box.func_run(self.caller["state"])
        finally:
            self.box.break_remove(self.base + 0x200)
        self.assertEqual(r["end"], "interrupted")
        self.assertEqual(r["stop_reason"], "breakpoint")
        self.assertFalse(self.box.status()["func"])

    def test_status_while_running(self):
        addr = f"l:{self.base + 0x200:08X}"
        self.box.call("func.start", state=self.sum["state"], write=[{"address": addr, "hex": "EBFE"}],
                      max_instructions=10 ** 12, timeout_ms=60000)
        s = self.box.status()
        self.assertTrue(s["func"])
        self.assertTrue(s["instruction_hook"])
        self.box.pause()
        r = self.box.call("func.result")
        self.assertEqual(r["end"], "interrupted")
        self.assertEqual(r["stop_reason"], "pause")
        self.assertFalse(self.box.status()["func"])
        # IRQ hold is off again: the timer tick keeps running
        t0 = self.box.mem_read("r:0040:006C", 4)
        self.box.resume()
        wait_until(lambda: self.box.mem_read("r:0040:006C", 4) != t0, timeout=5)

    def test_child_program_is_not_program_end(self):
        r = self.box.func_run(self.t[0x2A0][0]["state"])
        self.assertEqual(r["end"], "ret")
        self.assertEqual(int(r["regs_out"]["eax"], 16) & 0xFFFF, 0x1234)

    def test_error_before_load_changes_nothing(self):
        self.box.pause()
        info = self.box.state_save("without_call2")
        self.box.mem_write(f"r:{self.psp:04X}:03F0", b"\x55")
        with self.assertRaises(DmcpError) as ctx:
            self.box.func_run(info["path"])
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertEqual(self.box.mem_read(f"r:{self.psp:04X}:03F0", 1), b"\x55")  # not loaded
        with self.assertRaises(DmcpError) as ctx:
            self.box.func_run(self.sum["state"], regs={"xyz": "0x1"})
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertEqual(self.box.mem_read(f"r:{self.psp:04X}:03F0", 1), b"\x55")

    def test_error_after_load_halts(self):
        self.box.resume()
        with self.assertRaises(DmcpError) as ctx:
            self.box.func_run(self.sum["state"], write=[{"address": "p:FFFFFFF0", "hex": "00"}])
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertTrue(self.box.status()["paused"])
        self.assertFalse(self.box.status()["func"])

    def test_state_load_ends_run(self):
        addr = f"l:{self.base + 0x200:08X}"
        self.box.call("func.start", state=self.sum["state"], write=[{"address": addr, "hex": "EBFE"}],
                      max_instructions=10 ** 12, timeout_ms=60000)
        self.box.state_load(path=self.caller["state"])
        r = self.box.call("func.result")
        self.assertEqual(r["end"], "interrupted")
        self.assertEqual(r["stop_reason"], "state_load")
        self.assertEqual(r["changed"], [])


@need_build
class Func32Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS32")
        module = wait_until(lambda: next((m for m in cls.box.modules() if m["module"] == "CALLS32.COM"), None),
                            timeout=15)
        psp = int(module["psp"], 16)
        cls.base = psp * 16
        cls.box.calls_start(ranges=[{"from": f"l:{cls.base + 0x280:08X}", "to": f"l:{cls.base + 0x2FF:08X}"}],
                            states=1, name="f32")
        cls.box.mem_write(f"r:{psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.mem_read("l:00500000", 4) == b"C32!", timeout=15)
        cls.entries = read_jsonl(cls.box.calls_stop()["file"])

    def test_pm32(self):
        e = self.entries[0]
        info = json.loads(Path(e["state"]).with_suffix(".json").read_text())
        self.assertEqual(info["call"]["mode"], "pm")
        self.assertEqual(info["call"]["bits"], 32)
        self.assertEqual(int(info["call"]["esp_after"], 16), 0x0008FFFC)  # before the call; ret 4 -> 0x90000
        r = self.box.func_run(e["state"])
        self.assertEqual(r["end"], "ret")
        self.assertEqual(int(r["regs_out"]["eax"], 16), 0x11223345)
        self.assertEqual(int(r["regs_out"]["esp"], 16), 0x00090000)
        self.assertEqual(r["regs_out"], e["regs_out"])
        self.assertEqual(r["changed"], [])
        self.assertEqual(r["halt_addr"]["mode"], "pm")


@need_build
class FuncStackSwitchTest(unittest.TestCase):
    """A ring-3 function calls INT 30h; the ring-0 handler runs on the TSS stack (FUNCPM.COM)."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "FUNCPM")
        module = wait_until(lambda: next((m for m in cls.box.modules() if m["module"] == "FUNCPM.COM"), None),
                            timeout=15)
        psp = int(module["psp"], 16)
        base = psp * 16
        cls.box.calls_start(ranges=[{"from": f"l:{base + 0x2C0:08X}", "to": f"l:{base + 0x2DF:08X}"}],
                            states=1, name="fpm")
        cls.box.mem_write(f"r:{psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.mem_read("l:00500000", 4) == b"R3OK", timeout=15)
        cls.entries = read_jsonl(cls.box.calls_stop()["file"])

    def test_tss_stack_not_in_changed(self):
        e = self.entries[0]
        r = self.box.func_run(e["state"])
        self.assertEqual(r["end"], "ret")
        self.assertEqual(r["regs_out"], e["regs_out"])
        self.assertEqual(len(r["changed"]), 1, r["changed"])  # without the frame and pushes on the ring-0 stack
        g = r["changed"][0]
        self.assertEqual(int(g["address"]["linear"], 16), 0x00500010)
        self.assertEqual(g["after"], "44332211")


@need_build
class FuncCoreTest(unittest.TestCase):
    def test_needs_normal_core(self):
        box = Dosbox.start(mounts=[], autoexec=[], headless=True, conf={"cpu": {"core": "dynamic"}})
        try:
            with self.assertRaises(DmcpError) as ctx:
                box.func_run("does_not_exist.sav")
            self.assertEqual(ctx.exception.code, "unsupported_core")
            self.assertIn("core", ctx.exception.message)
        finally:
            box.stop()


class FuncRunTimeoutTest(unittest.TestCase):
    """Python logic of func_run on timeout, without an emulator."""

    def make(self, results):
        box = object.__new__(Dosbox)
        calls = []

        def call(method, **params):
            calls.append(method)
            if method == "func.result":
                return results.pop(0)
            return {}
        box.call = call
        box.status = lambda: {"paused": False}
        return box, calls

    def test_timeout_becomes_aborted(self):
        box, calls = self.make([{"running": True},
                                {"end": "interrupted", "stop_reason": "pause", "instructions": 5}])
        r = box.func_run("x.sav", timeout_s=0)
        self.assertEqual(calls, ["func.start", "func.result", "emu.pause", "func.result"])
        self.assertEqual(r["end"], "aborted")
        self.assertIsNone(r["stop_reason"])
        self.assertEqual(r["hint"], "timeout_s reached")

    def test_done_at_the_last_moment_stays_ret(self):
        box, _ = self.make([{"running": True}, {"end": "ret", "stop_reason": None, "instructions": 5}])
        r = box.func_run("x.sav", timeout_s=0)
        self.assertEqual(r["end"], "ret")
        self.assertNotIn("hint", r)
