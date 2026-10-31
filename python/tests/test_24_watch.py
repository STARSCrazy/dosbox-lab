import time
import unittest

from dosbox_lab import DmcpError
from dosbox_lab.files import read_jsonl
from tests.util import class_box, need_build, prog_psp, run_typed, start_idle, start_prog, wait_prompt, wait_until


class DebugProg:
    def addr(self, off):
        return f"r:{self.psp:04X}:{off:04X}"

    def value(self):
        return int.from_bytes(self.box.mem_read(self.addr(0x180), 2), "little")


@need_build
class WatchTest(unittest.TestCase, DebugProg):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")


    def tearDown(self):
        self.box.watch_remove(all=True)
        self.box.watch_clear_log()
        if self.box.status()["paused"]:
            self.box.resume()

    def test_write_watch_stops_after_write(self):
        info = self.box.watch_add(self.addr(0x180), length=2, kind="write")
        self.assertEqual(int(info["start"], 16), self.psp * 16 + 0x180)
        st = self.box.wait_paused()
        self.assertEqual(st["stop_reason"], "watch")
        self.assertEqual(int(st["eip"], 16), 0x111)
        hit = st["stop_watch"]
        self.assertEqual(hit["kind"], "write")
        self.assertEqual(hit["width"], 2)
        self.assertEqual(int(hit["addr"]["off"], 16), 0x10E)
        self.assertEqual(int(hit["target"]["linear"], 16), self.psp * 16 + 0x180)
        self.assertEqual(int(hit["value"], 16), (int(hit["old"], 16) + 1) & 0xFFFF)
        self.assertEqual(int(hit["value"], 16), self.value())
        self.assertEqual(self.box.watch_list()["watchpoint"][0]["hits"], 1)

    def test_breakpoint_right_after_watch_stop(self):
        # The watchpoint halts before 0111; a breakpoint right there must not be skipped when resuming.
        self.box.watch_add(self.addr(0x180), length=2, kind="write")
        self.box.break_add(self.addr(0x111))
        try:
            st = self.box.wait_paused()
            self.assertEqual(st["stop_reason"], "watch")
            self.assertEqual(int(st["eip"], 16), 0x111)
            before = self.value()
            self.box.resume()
            st = self.box.wait_paused()
            self.assertEqual(st["stop_reason"], "breakpoint")
            self.assertEqual(int(st["eip"], 16), 0x111)
            self.assertEqual(self.value(), before)
        finally:
            self.box.break_remove(all=True)

    def test_read_watch(self):
        self.box.watch_add(self.addr(0x180), length=2, kind="read")
        st = self.box.wait_paused()
        self.assertEqual(int(st["eip"], 16), 0x10D)
        self.assertEqual(st["stop_watch"]["kind"], "read")
        self.assertEqual(int(st["stop_watch"]["addr"]["off"], 16), 0x10A)
        self.assertNotIn("old", st["stop_watch"])

    def test_continue_stops_again(self):
        self.box.watch_add(self.addr(0x180), length=2)
        first = int(self.box.wait_paused()["stop_watch"]["value"], 16)
        self.box.resume()
        second = int(self.box.wait_paused()["stop_watch"]["value"], 16)
        self.assertEqual(second, (first + 1) & 0xFFFF)

    def test_step_stops_at_watch(self):
        self.box.run_until(address=self.addr(0x10A))
        self.box.watch_add(self.addr(0x180), length=2)
        st = self.box.step(200)
        self.assertEqual(st["stop_reason"], "watch")
        self.assertEqual(int(st["eip"], 16), 0x111)
        self.assertLess(st["steps_done"], 200)
        self.assertEqual(int(st["stop_watch"]["addr"]["off"], 16), 0x10E)

    def test_step_over_plain_instruction_stops_at_watch(self):
        self.box.run_until(address=self.addr(0x10E))
        self.box.watch_add(self.addr(0x180), length=2)
        st = self.box.step_over()
        self.assertEqual(st["kind"], "step")
        self.assertEqual(st["stop_reason"], "watch")
        self.assertEqual(int(st["eip"], 16), 0x111)
        self.assertEqual(int(st["stop_watch"]["addr"]["off"], 16), 0x10E)

    def test_log_mode(self):
        self.box.watch_add(self.addr(0x180), length=2, kind="both", action="log")
        time.sleep(1.0)
        self.assertFalse(self.box.status()["paused"])
        result = self.box.watch_log(name="accesses_debug")
        entries = read_jsonl(result["path"])
        self.assertGreater(len(entries), 4)
        self.assertEqual(result["count"], len(entries))
        offs = {(e["kind"], int(e["addr"]["off"], 16)) for e in entries}
        self.assertEqual(offs, {("read", 0x10A), ("write", 0x10E)})
        self.assertTrue(entries[0]["value"].startswith("0x"))
        for read, write in zip(entries, entries[1:]):
            if read["kind"] == "read" and write["kind"] == "write":
                self.assertEqual(int(write["value"], 16), (int(read["value"], 16) + 1) & 0xFFFF)
        nrs = [e["nr"] for e in entries]
        self.assertEqual(nrs, sorted(nrs))

    def test_no_code_fetch_hits(self):
        # The code at 0140..0144 runs every round: an instruction fetch is not a read access.
        self.box.watch_add(self.addr(0x140), length=5, kind="read")
        time.sleep(1.0)
        self.assertFalse(self.box.status()["paused"])
        self.assertEqual(self.box.watch_list()["watchpoint"][0]["hits"], 0)

    def test_physical_address_without_paging(self):
        info = self.box.watch_add(f"p:{self.psp * 16 + 0x182:X}", length=2)
        self.assertTrue(info["hints"])
        st = self.box.wait_paused()
        self.assertEqual(int(st["stop_watch"]["addr"]["off"], 16), 0x140)

    def test_errors(self):
        for kwargs in ({"length": 0}, {"length": 2_000_000}, {"kind": "x"}, {"action": "x"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DmcpError) as ctx:
                    self.box.watch_add(self.addr(0x180), **kwargs)
                self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertFalse(self.box.watch_remove(9999)["removed"])

    def test_remove_restores_speed_path(self):
        info = self.box.watch_add(self.addr(0x180), length=2, action="log")
        self.box.watch_remove(info["id"])
        time.sleep(0.3)
        self.assertEqual(self.box.watch_list()["watchpoint"], [])
        before = self.value()
        wait_until(lambda: self.value() != before, timeout=5)


@need_build
class WatchWithRecordingTest(unittest.TestCase, DebugProg):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")


    def test_stops_with_recording(self):
        self.box.flow_start(ring_size=100000)
        self.box.coverage_start()
        self.box.watch_add(self.addr(0x180), length=2)
        values = []
        for _ in range(3):
            values.append(int(self.box.wait_paused()["stop_watch"]["value"], 16))
            self.box.resume()
        self.box.watch_remove(all=True)
        self.assertEqual(values, [values[0], (values[0] + 1) & 0xFFFF, (values[0] + 2) & 0xFFFF])
        recent = read_jsonl(self.box.flow_recent(100000, "rec_watch")["path"])
        self.assertFalse([e for e in recent if e["source"]["linear"] == e["target"]["linear"]
                          and e["kind"] not in ("irq", "exception", "int")])
        self.box.flow_stop()
        self.box.coverage_stop()


@need_build
class WatchPmodeTest(unittest.TestCase):
    """Create a watchpoint before the switch to PM with paging; it must trigger after the TLB rebuild."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)


    def test_watch_survives_mode_switch(self):
        info = self.box.watch_add("l:01000004", length=4)
        self.assertTrue(info["hints"])  # page not mapped in real mode
        run_typed(self.box, "PMODE")
        st = self.box.wait_paused(timeout_s=15)
        self.assertEqual(st["stop_reason"], "watch")
        self.assertTrue(st["pmode"])
        hit = st["stop_watch"]
        self.assertEqual(int(hit["target"]["linear"], 16), 0x01000004)
        self.assertEqual(hit["width"], 4)
        first = int(hit["value"], 16)
        self.box.resume()
        st = self.box.wait_paused()
        self.assertEqual(int(st["stop_watch"]["value"], 16), first + 1)
        self.assertEqual(int(st["stop_watch"]["old"], 16), first)
