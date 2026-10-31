import unittest

from dosbox_lab import DmcpError
from tests.util import class_box, need_build, prog_psp, start_prog


class DebugProg:
    def addr(self, off):
        return f"r:{self.psp:04X}:{off:04X}"

    def word(self, off):
        return int.from_bytes(self.box.mem_read(self.addr(off), 2), "little")


@need_build
class StepOverTest(unittest.TestCase, DebugProg):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")


    def tearDown(self):
        if self.box.status()["paused"]:
            self.box.resume()

    def test_step_over_call(self):
        self.box.run_until(address=self.addr(0x107))
        calls = self.word(0x182)
        st = self.box.step_over()
        self.assertEqual(st["stop_reason"], "step_over")
        self.assertEqual(int(st["eip"], 16), 0x10A)
        self.assertEqual(self.word(0x182), (calls + 1) & 0xFFFF)

    def test_step_over_int(self):
        self.box.run_until(address=self.addr(0x113))
        st = self.box.step_over()
        self.assertEqual(st["stop_reason"], "step_over")
        self.assertEqual(int(st["eip"], 16), 0x115)
        self.assertEqual(int(st["cs"], 16), self.psp)

    def test_plain_instruction_is_single_step(self):
        self.box.run_until(address=self.addr(0x10A))
        st = self.box.step_over()
        self.assertEqual(st["kind"], "step")
        self.assertEqual(st["stop_reason"], "step")
        self.assertEqual(int(st["eip"], 16), 0x10D)

    def test_requires_pause(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.step_over()
        self.assertEqual(ctx.exception.code, "not_paused")

    def test_frame_step(self):
        st = self.box.pause()
        before = float(st["time_ms"])
        st = self.box.frame_step(10)
        self.assertTrue(st["paused"])
        self.assertEqual(st["stop_reason"], "frame")
        self.assertGreater(float(st["time_ms"]) - before, 50.0)
        self.assertLess(float(st["time_ms"]) - before, 1000.0)

    def test_run_until_after_step_on_breakpoint(self):
        # E4: run_until from the halt after a step executes at least one instruction, even if a breakpoint
        # sits on the current instruction.
        self.box.run_until(address=self.addr(0x10A))
        st = self.box.step()
        self.assertEqual(int(st["eip"], 16), 0x10D)
        self.box.break_add(self.addr(0x10D))
        try:
            st = self.box.run_until(address=self.addr(0x140))
            self.assertEqual(st["stop_reason"], "run_until")
            self.assertEqual(int(st["eip"], 16), 0x140)
            self.assertEqual(self.box.break_list()["entries"][0]["hits"], 0)
        finally:
            self.box.break_remove(all=True)

    def test_step_over_after_step_on_breakpoint(self):
        self.box.run_until(address=self.addr(0x115))
        st = self.box.step()  # jz loop (no key) -> 0107
        self.assertEqual(int(st["eip"], 16), 0x107)
        self.box.break_add(self.addr(0x107))
        try:
            st = self.box.step_over()
            self.assertEqual(st["stop_reason"], "step_over")
            self.assertEqual(int(st["eip"], 16), 0x10A)
        finally:
            self.box.break_remove(all=True)

    def test_fixed_cycles(self):
        original = self.box.status()
        st = self.box.speed("cycles", cycles=5000)
        self.assertEqual(st["cycles"], 5000)
        self.assertFalse(st["cycles_auto"])
        with self.assertRaises(DmcpError) as ctx:
            self.box.speed("cycles", cycles=10)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.box.speed("cycles", cycles=20000)
        st = self.box.speed("normal")
        self.assertEqual(st["cycles_auto"], original["cycles_auto"])
        self.assertEqual(st["cycles"], original["cycles"])


@need_build
class StepOverEndTest(unittest.TestCase, DebugProg):
    """step_over across INT 21h/4Ch: never returns -> timeout, condition disarmed, emulator running."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")


    def test_step_over_never_returns(self):
        self.box.key("ESC")
        st = self.box.run_until(address=self.addr(0x122))
        self.assertEqual(int(st["eip"], 16), 0x122)
        self.assertEqual(int(self.box.registers()["gp"]["eax"], 16) & 0xFFFF, 0x4C07)
        with self.assertRaises(DmcpError) as ctx:
            self.box.step_over(timeout_s=1.0)
        self.assertEqual(ctx.exception.code, "timeout")
        st = self.box.status()
        self.assertFalse(st["paused"])
        self.assertNotIn("waiting_for", st)
