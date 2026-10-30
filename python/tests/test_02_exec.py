import time
import unittest

from dosbox_mcp import DmcpError
from tests.util import class_box, need_build, start_prog, wait_until


@need_build
class ExecTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("MEMTEST.COM"))
        wait_until(lambda: cls.box.registers()["modus"] == "real")


    def tearDown(self):
        self.box.break_remove(all=True)
        self.box.resume()

    def test_pause_and_resume(self):
        st = self.box.pause()
        self.assertTrue(st["paused"])
        self.assertEqual(st["stop_reason"], "pause")
        eip1 = self.box.registers()["gp"]["eip"]
        eip2 = self.box.registers()["gp"]["eip"]
        self.assertEqual(eip1, eip2, "im Pausenzustand darf sich EIP nicht aendern")
        self.assertFalse(self.box.resume()["paused"])

    def test_step_changes_eip(self):
        # MEMTEST wartet in der BIOS-Tastaturabfrage per HLT (STI; HLT; IRET bei F000:D185). Ein Schritt im
        # HLT-Zustand fuehrt nichts aus: EIP aendert sich erst durch einen Interrupt, und der kommt nur im
        # laufenden Emulator. Darum erst versuchen, ausserhalb von HLT zu pausieren.
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
        # Die Hauptschleife von MEMTEST laeuft staendig an CS:IP vorbei: aktuelle Adresse als Haltepunkt.
        self.box.pause()
        regs = self.box.registers()
        linear = int(regs["seg"]["cs"]["basis"], 16) + int(regs["gp"]["eip"], 16)
        self.box.break_add(linear)
        self.box.step(1)          # vom Haltepunkt weg
        self.box.resume()
        st = wait_until(lambda: (s := self.box.status())["paused"] and s, timeout=5)
        self.assertEqual(st["stop_reason"], "breakpoint")
        self.assertEqual(int(st["stop_linear"], 16), linear)

    def test_registers_shape(self):
        regs = self.box.registers()
        self.assertEqual(regs["modus"], "real")
        self.assertEqual(regs["bits"], 16)
        for name in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "eip", "eflags"):
            self.assertRegex(regs["gp"][name], r"^[0-9A-F]{8}$")
        self.assertIn("if", regs["flags"])

    def test_set_register(self):
        self.box.pause()
        regs = self.box.set_register("eax", 0x12345678)
        self.assertEqual(regs["gp"]["eax"], "12345678")

    def test_set_register_hex_string(self):
        # Strings sind Hex wie in den RPC-Antworten; "00000050" war frueher oktal (strtoull Basis 0) = 0x28.
        self.box.pause()
        self.assertEqual(self.box.set_register("edi", "00000050")["gp"]["edi"], "00000050")
        self.assertEqual(self.box.set_register("edi", "0x1A0")["gp"]["edi"], "000001A0")
        self.assertEqual(self.box.set_register("edi", "C0DE")["gp"]["edi"], "0000C0DE")

    def test_raw_rpc_numeric_strings(self):
        # Roh-RPC: Zahl als String ist dezimal (nie oktal), nur "0x" bedeutet hex (B5).
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
        # 2**32 ms wurde frueher auf 32 Bit abgeschnitten (= 0 ms, sofortiger Timeout); jetzt auf 600000 begrenzt.
        self.assertIn("paused", self.box.call("emu.status", timeout_ms=2 ** 32))

    def test_timed_out_call_never_runs_late(self):
        # Im Pausenzustand holt der Emulations-Thread Auftraege nur etwa jede Millisekunde ab: Mit timeout_ms=1
        # laufen manche Aufrufe ab, bevor sie starten. Solche duerfen nie nachtraeglich ausgefuehrt werden.
        self.box.pause()
        self.box.mem_write("l:4F0", bytes(4))
        not_run = []
        for i in range(1, 101):
            value = i.to_bytes(4, "little")
            try:
                self.box.call("mem.write", adresse="l:4F0", hex=value.hex().upper(), timeout_ms=1)
            except DmcpError as exc:
                self.assertEqual(exc.code, "timeout")
                if "not executed" in exc.message:
                    not_run.append(value)
                else:
                    self.assertIn("may have executed", exc.message)
            time.sleep(0.02)
            self.assertNotIn(self.box.mem_read("l:4F0", 4), not_run, f"Aufruf {i}")
        if not not_run:
            self.skipTest("kein Aufruf lief vor dem Start ab (Emulator zu schnell), nichts zu pruefen")


@need_build
class StepOverDosCallTest(unittest.TestCase):
    """emu.step ueber einen DOS-Dienst, der intern CALLBACK_RunRealInt (verschachteltes RunMachine) nutzt."""

    @classmethod
    def setUpClass(cls):
        # ansi.sys=false: DOS gibt Zeichen per CALLBACK_RunRealInt(0x29) aus statt direkt.
        cls.box = class_box(cls, start_prog("KEYS.COM", conf={"dos": {"keyboardlayout": "us", "ansi.sys": "false"}}))
        time.sleep(1.5)


    def keys_count(self):
        return int.from_bytes(self.box.mem_read("l:4F2", 2), "little")

    def test_step_through_int21_output(self):
        # KEYS.COM nach jeder Taste: mov dl,al / mov ah,2 / int 21h
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
