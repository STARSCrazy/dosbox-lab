import json
import unittest
from pathlib import Path

from dosbox_lab import DmcpError
from dosbox_lab.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, wait_prompt, wait_until


@need_build
class CallsParamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)

    def assertBad(self, **kw):
        with self.assertRaises(DmcpError) as ctx:
            self.box.calls_start(**kw)
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_needs_filter(self):
        self.assertBad()

    def test_bad_values(self):
        self.assertBad(targets=["l:1000"], stack_bytes=2000)
        self.assertBad(targets=["l:1000"], max_per_target=0)
        self.assertBad(targets=["l:1000"], max_bytes=100)
        self.assertBad(targets=["no_symbol_xyz"])
        self.assertBad(targets=["p:1000"])
        self.assertBad(ranges=[{"from": "l:2000", "to": "l:1000"}])
        self.assertBad(modules=["NOSUCHMODULE.EXE"])
        self.assertBad(ints=[{"number": "1FF"}])
        self.assertBad(targets=["l:1000"], name="a/b")

    def test_start_stop_files(self):
        r = self.box.calls_start(targets=["l:00001000"], ranges=[{"from": "l:2000", "to": "l:20FF"}],
                                 ints=[{"number": "21", "ah": "3F"}], name="calls_empty")
        self.assertTrue(r["active"])
        self.assertTrue(self.box.status()["instruction_hook"])
        s = self.box.calls_stop()
        self.assertFalse(s["active"])
        self.assertEqual(s["written"], 0)
        self.assertEqual(Path(s["file"]).read_text(), "")
        meta = json.loads(Path(s["json_path"]).read_text())
        self.assertEqual(meta["format"], "dosbox-lab-calls/1")
        self.assertEqual(meta["file"], "calls_empty.jsonl")
        self.assertEqual(meta["filter"]["targets"][0]["linear"], "0x00001000")
        self.assertEqual(meta["filter"]["ranges"][0]["to"]["linear"], "0x000020FF")
        self.assertEqual(meta["filter"]["ints"], [{"number": "0x21", "ah": "0x3F"}])
        self.assertIsNone(meta["stack_bytes"])
        self.assertEqual(meta["max_per_target"], 1000)
        self.assertEqual(meta["omitted"], [])
        self.assertFalse(self.box.status()["instruction_hook"])


def calls_psp(box, module):
    module = wait_until(lambda: next((m for m in box.modules() if m["module"] == module), None), timeout=15)
    return int(module["psp"], 16)


def lin(e, field):
    return int(e[field]["linear"], 16)


@need_build
class CallsRecordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        cls.base = cls.psp * 16
        b = cls.base
        cls.box.calls_start(ranges=[{"from": f"l:{b + 0x200:08X}", "to": f"l:{b + 0x2FF:08X}"}],
                            ints=[{"number": "21", "ah": "30"}], max_per_target=3, name="calls_rec")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.mem_read(f"r:{cls.psp:04X}:03F0", 1) == b"\x01", timeout=15)
        wait_until(lambda: cls.box.calls_status()["open"] == 1, timeout=10)
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["file"])
        cls.meta = json.loads(Path(cls.stop["json_path"]).read_text())
        cls.by_target = {}
        for e in cls.entries:
            cls.by_target.setdefault(lin(e, "target") - b, []).append(e)

    def test_counts(self):
        self.assertEqual(len(self.entries), 15)
        self.assertEqual(self.stop["written"], 15)  # 14 completed + 1 open
        self.assertEqual(self.stop["open"], 1)
        self.assertEqual({k: len(v) for k, v in self.by_target.items() if 0x200 <= k < 0x300},
                         {0x200: 2, 0x210: 1, 0x220: 3, 0x230: 1, 0x240: 1, 0x250: 3, 0x260: 1, 0x270: 1, 0x280: 1})

    def test_nested(self):
        outer = self.by_target[0x210][0]
        inner = self.by_target[0x200]
        self.assertEqual(outer["depth"], 0)
        self.assertIsNone(outer["parent_id"])
        self.assertEqual([e["parent_id"] for e in inner], [outer["call_id"]] * 2)
        self.assertEqual([e["depth"] for e in inner], [1, 1])
        self.assertEqual([int(e["regs_in"]["eax"], 16) & 0xFFFF for e in inner], [5, 6])
        self.assertEqual([int(e["regs_out"]["eax"], 16) & 0xFFFF for e in inner], [6, 7])
        self.assertEqual(lin(inner[0], "from") - self.base, 0x210)
        self.assertTrue(all(e["end"] == "ret" and e["kind"] == "near" and e["bits"] == 16 for e in inner))
        self.assertLess(inner[0]["nr"], inner[0]["nr_end"])
        self.assertLess(inner[1]["nr_end"], outer["nr_end"])

    def test_recursion(self):
        rec = sorted(self.by_target[0x220], key=lambda e: e["depth"])
        self.assertEqual([e["depth"] for e in rec], [0, 1, 2])
        self.assertEqual(rec[1]["parent_id"], rec[0]["call_id"])
        self.assertEqual(rec[2]["parent_id"], rec[1]["call_id"])
        self.assertEqual([int(e["regs_in"]["ecx"], 16) & 0xFFFF for e in rec], [3, 2, 1])
        written_order = [e["call_id"] for e in self.entries if lin(e, "target") - self.base == 0x220]
        self.assertEqual(written_order, [rec[2]["call_id"], rec[1]["call_id"], rec[0]["call_id"]])

    def test_far(self):
        far = self.by_target[0x230][0]
        self.assertEqual(far["kind"], "far")
        self.assertEqual(far["end"], "retf")
        self.assertEqual(int(far["regs_out"]["eax"], 16) & 0xFFFF, 0x1234)

    def test_stack_and_ret_n(self):
        e = self.by_target[0x240][0]
        sp = int(e["regs_in"]["esp"], 16) & 0xFFFF
        length = min(32, 0x10000 - sp)  # 16-bit stack: the read ends at the segment end
        self.assertGreaterEqual(length, 6)
        self.assertEqual(e["stack_length"], length)
        self.assertEqual(len(e["stack"]), 2 * length)
        ret = lin(e, "from") - self.base + 3  # call near rel16 = 3 bytes
        self.assertEqual(e["stack"][:4], f"{ret & 0xFF:02X}{ret >> 8:02X}")
        self.assertEqual(e["stack"][4:12], "BB00AA00")
        self.assertEqual(int(e["regs_out"]["eax"], 16) & 0xFFFF, 0x165)
        sp_in = int(e["regs_in"]["esp"], 16) & 0xFFFF
        self.assertEqual(int(e["regs_out"]["esp"], 16) & 0xFFFF, sp_in + 2 + 4)
        self.assertEqual(e["end"], "ret")

    def test_limit_per_target(self):
        self.assertEqual(len(self.by_target[0x250]), 3)
        omitted = [(int(w["target"]["linear"], 16) - self.base, w["count"]) for w in self.meta["omitted"]]
        self.assertEqual(omitted, [(0x250, 2)])

    def test_escape_without_return(self):
        outer, inner = self.by_target[0x260][0], self.by_target[0x270][0]
        self.assertEqual(outer["end"], "ret")
        self.assertEqual(inner["end"], "unknown")
        self.assertEqual(inner["parent_id"], outer["call_id"])
        self.assertNotIn("regs_out", inner)
        self.assertEqual(self.box.mem_read(f"r:{self.psp:04X}:03F0", 1), b"\x01")  # 0xEE was never written

    def test_int_entry(self):
        ints = [e for e in self.entries if e["kind"] == "int"]
        self.assertEqual(len(ints), 1)
        e = ints[0]
        self.assertEqual(e["vector"], "0x21")
        self.assertEqual(e["end"], "iret")
        self.assertEqual(int(e["regs_in"]["eax"], 16) & 0xFF00, 0x3000)
        self.assertIsNone(e["parent_id"])

    def test_open_at_stop(self):
        e = self.by_target[0x280][0]
        self.assertTrue(e["open"])
        self.assertNotIn("end", e)
        self.assertNotIn("regs_out", e)
        self.assertEqual(sum(1 for x in self.entries if x.get("open")), 1)

    def test_nothing_open_after_stop(self):
        self.assertEqual(self.box.calls_status()["open"], 0)
        self.assertEqual(self.box.calls_stop()["open"], 0)  # second stop: no stale frame

    def test_context_main(self):
        self.assertTrue(all(e["context"] == "main" for e in self.entries))

    def test_entry_fields(self):
        e = self.by_target[0x200][0]
        for field in ("call_id", "parent_id", "depth", "context", "nr", "nr_end", "from", "target", "kind", "bits",
                     "regs_in", "flags_in", "stack", "stack_length", "regs_out", "flags_out", "end"):
            self.assertIn(field, e)
        self.assertEqual(set(e["regs_in"]), {"eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "ds", "es",
                                              "fs", "gs", "ss"})
        self.assertEqual(e["target"]["module"], "CALLS.COM")
        self.assertTrue(e["flags_in"].startswith("0x"))


def wait_prompt_after(box, text, timeout=15):
    """Waits until a C:\\> prompt appears again after the last screen line containing `text` (program exited)."""
    def done():
        lines = box.screen_text()["lines"]
        idx = [i for i, z in enumerate(lines) if text in z]
        return bool(idx) and any(z.startswith("C:\\>") for z in lines[idx[-1] + 1:])

    wait_until(done, timeout=timeout)


@need_build
class CallsTargetStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        b = cls.base = cls.psp * 16
        cls.box.calls_start(targets=[f"l:{b + 0x200:08X}", f"l:{b + 0x220:08X}", f"l:{b + 0x280:08X}"],
                            stack_bytes=8, name="calls_targets")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.calls_status()["open"] == 1, timeout=15)
        cls.box.state_save("calls_state")
        cls.box.state_load("calls_state")
        cls.open_after_load = cls.box.calls_status()["open"]
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["file"])

    def test_only_targets(self):
        targets = sorted({lin(e, "target") - self.base for e in self.entries})
        self.assertEqual(targets, [0x200, 0x220, 0x280])

    def test_parent_only_recorded(self):
        inner = [e for e in self.entries if lin(e, "target") - self.base == 0x200]
        self.assertEqual([e["parent_id"] for e in inner], [None, None])
        self.assertEqual([e["depth"] for e in inner], [0, 0])

    def test_stack_bytes(self):
        for e in self.entries:
            length = min(8, 0x10000 - (int(e["regs_in"]["esp"], 16) & 0xFFFF))  # 16-bit stack: up to the segment end
            self.assertEqual((e["stack_length"], len(e["stack"])), (length, 2 * length))

    def test_state_load_closes_open(self):
        self.assertEqual(self.open_after_load, 0)
        hanging = [e for e in self.entries if lin(e, "target") - self.base == 0x280]
        self.assertEqual(len(hanging), 1)
        self.assertEqual(hanging[0]["end"], "unknown")
        self.assertNotIn("open", hanging[0])
        self.assertEqual(self.stop["open"], 0)


@need_build
class CallsRestartTest(unittest.TestCase):
    """A run ends right after a CALL (breakpoint at the target, transfer still open) and the program keeps running
    without recording. The next run must not add the old CALL as a frame."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        b = cls.base = cls.psp * 16
        range = [{"from": f"l:{b + 0x200:08X}", "to": f"l:{b + 0x2FF:08X}"}]
        cls.box.break_add(b + 0x210, once=True)  # outer: CALL from 010E is executed, not yet resolved
        cls.box.calls_start(ranges=range, name="calls_new1")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        cls.box.wait_paused(15)
        cls.box.calls_stop()
        cls.box.break_add(b + 0x220, once=True)  # rec (cx=3), first instruction of the next run
        cls.box.resume()
        cls.box.wait_paused(15)
        cls.box.calls_start(ranges=range, name="calls_new2")
        cls.box.resume()
        wait_until(lambda: cls.box.mem_read(f"r:{cls.psp:04X}:03F0", 1) == b"\x01", timeout=15)
        wait_until(lambda: cls.box.calls_status()["open"] == 1, timeout=10)  # hangs
        cls.entries = read_jsonl(cls.box.calls_stop()["file"])

    def test_no_stale_frame(self):
        self.assertFalse(any(lin(e, "from") - self.base == 0x10E for e in self.entries))
        rec = sorted((e for e in self.entries if lin(e, "target") - self.base == 0x220), key=lambda e: e["call_id"])
        self.assertEqual([int(e["regs_in"]["ecx"], 16) & 0xFFFF for e in rec], [2, 1])
        self.assertIsNone(rec[0]["parent_id"])
        self.assertEqual(rec[0]["depth"], 0)
        self.assertEqual(rec[1]["parent_id"], rec[0]["call_id"])

    def test_top_level(self):
        top_level = [e for e in self.entries if lin(e, "target") - self.base != 0x220 or
                int(e["regs_in"]["ecx"], 16) & 0xFFFF == 2]
        self.assertEqual(len(top_level), 11)  # rec, far, withparam, 5x often, escape_outer/-inner, hangs
        self.assertTrue(all(e["parent_id"] is None and e["depth"] == 0 for e in top_level
                            if lin(e, "target") - self.base != 0x270))
        self.assertTrue(all(e["context"] == "main" for e in self.entries))


@need_build
class CallsStateLoadPendingTest(unittest.TestCase):
    """state.load in the middle of a run while a CALL (call hangs) is not yet resolved: the old transfer
    belongs to the replaced state and must not produce a frame after the load."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        b = cls.base = cls.psp * 16
        cls.box.break_add(b + 0x250, once=True)  # often, first call (cx=5)
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        cls.box.wait_paused(15)
        cls.box.state_save("calls_pending")
        cls.box.calls_start(ranges=[{"from": f"l:{b + 0x250:08X}", "to": f"l:{b + 0x250:08X}"}],
                            name="calls_pend")
        cls.box.break_add(b + 0x280, once=True)  # hangs: the hanging call (013A) is not yet resolved
        cls.box.resume()
        cls.box.wait_paused(15)
        cls.box.state_load("calls_pending")
        cls.box.resume()
        wait_until(lambda: cls.box.mem_read(f"r:{cls.psp:04X}:03F0", 1) == b"\x01", timeout=15)
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["file"])

    def test_no_stale_frame(self):
        # often 2..5 before and after the load, each from 0128, all top level and ended with ret
        self.assertEqual(len(self.entries), 8)
        self.assertTrue(all(lin(e, "from") - self.base == 0x128 for e in self.entries))
        self.assertTrue(all(e["parent_id"] is None and e["depth"] == 0 for e in self.entries))
        self.assertTrue(all(e["end"] == "ret" for e in self.entries))
        self.assertEqual(self.stop["open"], 0)


@need_build
class CallsExitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)
        cls.box.calls_start(ints=[{"number": "21", "ah": "4C"}], name="calls_exit")
        run_typed(cls.box, "EXITCODE")
        wait_until(lambda: any(m["module"] == "EXITCODE.COM" for m in cls.box.modules()), timeout=15)
        wait_prompt_after(cls.box, "EXITCODE stderr")
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["file"])

    def test_exit_closes_frame(self):
        own = [e for e in self.entries if (int(e["regs_in"]["eax"], 16) & 0xFFFF) == 0x4C2A]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["end"], "unknown")
        self.assertNotIn("open", own[0])


@need_build
class CallsLimitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        b = cls.psp * 16
        cls.box.events_start(types=["limit_reached"], name="ev_limit")
        cls.box.calls_start(ranges=[{"from": f"l:{b + 0x200:08X}", "to": f"l:{b + 0x2FF:08X}"}],
                            max_bytes=4096, name="calls_limit")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_until(lambda: not cls.box.calls_status()["active"], timeout=15)
        cls.status = cls.box.calls_status()
        cls.events = read_jsonl(cls.box.events_stop()["file"])

    def test_limit(self):
        self.assertTrue(self.status["limit_reached_flag"])
        self.assertLessEqual(Path(self.status["file"]).stat().st_size, 4096)
        meta = json.loads(Path(self.status["file"]).with_suffix(".json").read_text())
        self.assertTrue(meta["limit_reached_flag"])
        self.assertTrue(any(e["type"] == "limit_reached" and e["recording"] == "calls" for e in self.events))
        self.assertFalse(self.box.status()["instruction_hook"])


@need_build
class CallsContextUnwindTest(unittest.TestCase):
    """CALLSEXC: an exception context ends without its own IRET (IRET to the outer context, program end in the
    handler). Afterwards entries must be back in context "main"."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLSEXC")
        cls.psp = calls_psp(cls.box, "CALLSEXC.COM")
        cls.base = cls.psp * 16
        cls.box.calls_start(targets=[f"l:{cls.base + 0x200:08X}"], ints=[{"number": "21", "ah": "09"}],
                            name="calls_exc")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_prompt_after(cls.box, "CALLSEXC")
        run_typed(cls.box, "EXITCODE")
        wait_prompt_after(cls.box, "EXITCODE stderr")
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["file"])

    def test_iret_to_outer_context(self):
        target = [e for e in self.entries if e["kind"] == "near"]
        self.assertEqual(len(target), 1)
        self.assertEqual(lin(target[0], "target") - self.base, 0x200)
        self.assertEqual(target[0]["context"], "main")
        self.assertIsNone(target[0]["parent_id"])
        self.assertEqual(target[0]["end"], "ret")

    def test_exit_drops_interrupt_context(self):
        dos = [e for e in self.entries if e["kind"] == "int" and e["from"].get("module") == "EXITCODE.COM"]
        self.assertEqual(len(dos), 1)  # INT 21h 09h (stdout); 40h is not in the filter
        e = dos[-1]
        self.assertEqual(e["context"], "main")
        self.assertEqual(e["end"], "iret")
        self.assertEqual(self.stop["open"], 0)


@need_build
class Calls32Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS32")
        cls.psp = calls_psp(cls.box, "CALLS32.COM")
        b = cls.base = cls.psp * 16
        cls.box.calls_start(ranges=[{"from": f"l:{b + 0x280:08X}", "to": f"l:{b + 0x2FF:08X}"}], name="calls32")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.mem_read("l:00500000", 4) == b"C32!", timeout=15)
        cls.entries = read_jsonl(cls.box.calls_stop()["file"])

    def test_32bit_entry(self):
        self.assertEqual(len(self.entries), 1)
        e = self.entries[0]
        self.assertEqual(e["bits"], 32)
        self.assertEqual(e["kind"], "near")
        self.assertEqual(e["end"], "ret")
        self.assertEqual(e["target"]["mode"], "pm")
        self.assertEqual(lin(e, "target"), self.base + 0x280)
        self.assertEqual(e["stack_length"], 64)
        ret = lin(e, "from") + 5  # call rel32
        self.assertEqual(e["stack"][:8], ret.to_bytes(4, "little").hex().upper())
        self.assertEqual(e["stack"][8:16], "44332211")
        self.assertEqual(int(e["regs_in"]["esp"], 16), 0x0008FFF8)
        self.assertEqual(int(e["regs_out"]["esp"], 16), 0x00090000)
        self.assertEqual(int(e["regs_out"]["eax"], 16), 0x11223345)
