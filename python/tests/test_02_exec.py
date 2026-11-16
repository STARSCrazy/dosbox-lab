import time
import unittest

from dosbox_lab import DmcpError
from tests.util import class_box, need_build, start_prog, wait_until


@need_build
class ExecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("MEMTEST.COM"))
        wait_until(lambda: cls.box.registers()["mode"] == "real")


    def tearDown(self):
        self.box.break_remove(all=True)
        self.box.resume()

    def test_pause_and_resume(self):
        st = self.box.pause()
        self.assertTrue(st["paused"])
        self.assertEqual(st["stop_reason"], "pause")
        eip1 = self.box.registers()["gp"]["eip"]
        eip2 = self.box.registers()["gp"]["eip"]
        self.assertEqual(eip1, eip2, "EIP must not change while paused")
        self.assertFalse(self.box.resume()["paused"])

    def test_step_changes_eip(self):
        # MEMTEST waits in the BIOS keyboard poll with HLT (STI; HLT; IRET at F000:D185). A step in the
        # HLT state executes nothing: EIP only changes through an interrupt, which only arrives while the
        # emulator runs. So first try to pause outside of HLT.
        halted = self.box.pause()["halted"]
        for _ in range(50):
            if not halted:
                break
            self.box.resume()
            time.sleep(0.01)
            halted = self.box.pause()["halted"]
        before = self.box.registers()["gp"]["eip"]
        st = self.box.step(1)
        self.assertEqual(st["steps_done"], 1)
        after = self.box.registers()["gp"]["eip"]
        if halted:
            self.assertTrue(st["halted"])
            self.assertEqual(before, after)
        else:
            self.assertNotEqual(before, after)

    def test_step_requires_pause(self):
        self.box.resume()
        with self.assertRaises(DmcpError) as ctx:
            self.box.step(1)
        self.assertEqual(ctx.exception.code, "not_paused")

    def test_breakpoint_hits_loop(self):
        # The main loop of MEMTEST keeps passing CS:IP: use the current address as a breakpoint.
        self.box.pause()
        regs = self.box.registers()
        linear = int(regs["seg"]["cs"]["base"], 16) + int(regs["gp"]["eip"], 16)
        self.box.break_add(linear)
        self.box.step(1)          # step off the breakpoint
        self.box.resume()
        st = wait_until(lambda: (s := self.box.status())["paused"] and s, timeout=5)
        self.assertEqual(st["stop_reason"], "breakpoint")
        self.assertEqual(int(st["stop_linear"], 16), linear)

    def test_registers_shape(self):
        regs = self.box.registers()
        self.assertEqual(regs["mode"], "real")
        self.assertEqual(regs["bits"], 16)
        for name in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "eip", "eflags"):
            self.assertRegex(regs["gp"][name], r"^[0-9A-F]{8}$")
        self.assertIn("if", regs["flags"])

    def test_set_register(self):
        self.box.pause()
        regs = self.box.set_register("eax", 0x12345678)
        self.assertEqual(regs["gp"]["eax"], "12345678")

    def test_set_register_hex_string(self):
        # Strings are hex like in the RPC responses; "00000050" used to be octal (strtoull base 0) = 0x28.
        self.box.pause()
        self.assertEqual(self.box.set_register("edi", "00000050")["gp"]["edi"], "00000050")
        self.assertEqual(self.box.set_register("edi", "0x1A0")["gp"]["edi"], "000001A0")
        self.assertEqual(self.box.set_register("edi", "C0DE")["gp"]["edi"], "0000C0DE")

    def test_raw_rpc_numeric_strings(self):
        # Raw RPC: a number as a string is decimal (never octal), only "0x" means hex.
        self.box.pause()
        old = self.box.registers()["gp"]["ebx"]
        self.addCleanup(self.box.set_register, "ebx", old)
        regs = self.box.call("cpu.set_register", name="ebx", value="0100")
        self.assertEqual(regs["gp"]["ebx"], "00000064")
        regs = self.box.call("cpu.set_register", name="ebx", value="0x100")
        self.assertEqual(regs["gp"]["ebx"], "00000100")
        with self.assertRaises(DmcpError) as ctx:
            self.box.call("cpu.set_register", name="ebx", value="01x")
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_speed(self):
        self.assertTrue(self.box.speed("turbo")["turbo"])
        self.assertFalse(self.box.speed("normal")["turbo"])

    def test_huge_timeout_is_clamped(self):
        # 2**32 ms used to be truncated to 32 bits (= 0 ms, immediate timeout); now it is clamped to 600000.
        self.assertIn("paused", self.box.call("emu.status", timeout_ms=2 ** 32))

    def test_timed_out_call_never_runs_late(self):
        # While paused, the emulation thread picks up requests only about every millisecond: with
        # timeout_ms=1 some calls expire before they start. Those must never be executed late.
        self.box.pause()
        self.box.mem_write("l:4F0", bytes(4))
        not_run = []
        for i in range(1, 101):
            value = i.to_bytes(4, "little")
            try:
                self.box.call("mem.write", address="l:4F0", hex=value.hex().upper(), timeout_ms=1)
            except DmcpError as exc:
                self.assertEqual(exc.code, "timeout")
                if "not executed" in exc.message:
                    not_run.append(value)
                else:
                    self.assertIn("may have executed", exc.message)
            time.sleep(0.02)
            self.assertNotIn(self.box.mem_read("l:4F0", 4), not_run, f"Call {i}")
        if not not_run:
            self.skipTest("no call expired before starting (emulator too fast), nothing to check")


@need_build
class StepOverDosCallTest(unittest.TestCase):
    """emu.step over a DOS service that uses CALLBACK_RunRealInt internally (nested RunMachine)."""

    @classmethod
    def setUpClass(cls):
        # ansi.sys=false: DOS prints the character via CALLBACK_RunRealInt(0x29) instead of directly.
        cls.box = class_box(cls, start_prog("KEYS.COM", conf={"dos": {"keyboardlayout": "us", "ansi.sys": "false"}}))
        time.sleep(1.5)


    def keys_count(self):
        return int.from_bytes(self.box.mem_read("l:4F2", 2), "little")

    def test_step_through_int21_output(self):
        # KEYS.COM after every key: mov dl,al / mov ah,2 / int 21h
        hits = wait_until(lambda: self.box.mem_search("l:0", 0xA0000, bytes.fromhex("88C2B402CD21")))
        for hit in hits:
            self.box.break_add(int(hit["linear"], 16) + 4)
        try:
            self.box.key("A")
            st = wait_until(lambda: (s := self.box.status())["paused"] and s, timeout=5)
            self.assertEqual(st["stop_reason"], "breakpoint")
            st = self.box.call("emu.step", count=50, timeout_ms=5000)
            self.assertEqual(st["steps_done"], 50)
            self.assertTrue(st["paused"])
        finally:
            self.box.break_remove(all=True)
            self.box.resume()
        count = self.keys_count()
        self.box.key("B")
        wait_until(lambda: self.keys_count() > count)


if __name__ == "__main__":
    unittest.main()
