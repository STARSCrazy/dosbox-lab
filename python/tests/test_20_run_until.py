import unittest

from dosbox_mcp import DmcpError
from dosbox_mcp.api import Dosbox
from tests.util import class_box, need_build, prog_psp, start_prog, wait_until


@need_build
class RunUntilTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")
        wait_until(lambda: cls.wert(cls) > 2, timeout=15)


    def tearDown(self):
        self.box.break_remove(all=True)
        if self.box.status()["paused"]:
            self.box.resume()

    def wert(self):
        return int.from_bytes(self.box.mem_read(f"r:{self.psp:04X}:0180", 2), "little")

    def adr(self, off):
        return f"r:{self.psp:04X}:{off:04X}"

    def test_until_address(self):
        st = self.box.run_until(adresse=self.adr(0x140))
        self.assertTrue(st["paused"])
        self.assertEqual(st["stop_reason"], "run_until")
        self.assertEqual(int(st["cs"], 16), self.psp)
        self.assertEqual(int(st["eip"], 16), 0x140)
        self.assertNotIn("wartet_auf", st)

    def test_runs_at_least_one_instruction(self):
        self.box.run_until(adresse=self.adr(0x10A))
        before = self.wert()
        st = self.box.run_until(adresse=self.adr(0x10A))
        self.assertEqual(int(st["eip"], 16), 0x10A)
        self.assertEqual(self.wert(), (before + 1) & 0xFFFF)

    def test_until_address_and_expression(self):
        self.box.run_until(adresse=self.adr(0x10E))
        target = (int(self.box.registers()["gp"]["eax"], 16) + 3) & 0xFFFF
        st = self.box.run_until(adresse=self.adr(0x10E), ausdruck=f"ax=={target}")
        self.assertEqual(st["stop_reason"], "run_until")
        self.assertEqual(int(self.box.registers()["gp"]["eax"], 16) & 0xFFFF, target)

    def test_until_memory_expression(self):
        self.box.run_until(adresse=self.adr(0x10A))  # angehalten: wert steht still, kein Wettlauf
        target = (self.wert() + 2) & 0xFFFF
        self.box.run_until(ausdruck=f"word[{self.adr(0x180)}]=={target}")
        self.assertEqual(self.wert(), target)
        self.assertEqual(int(self.box.status()["eip"], 16), 0x111)

    def test_until_cs(self):
        st = self.box.run_until(cs=f"{self.psp:04X}")
        self.assertEqual(int(st["cs"], 16), self.psp)

    def test_eval_operators(self):
        self.box.run_until(adresse=self.adr(0x10A))
        self.box.set_register("ebx", 0x1234)
        cases = [("bx", 0x1234), ("BH", 0x12), ("bl==0x34", 1), ("bx>0x1000 && bl<0x40", 1),
                 ("!(bx==0x1234)", 0), ("bx&0xFF00|1", 0x1201), ("-1", 0xFFFFFFFF), ("~0", 0xFFFFFFFF),
                 ("5-7", 0xFFFFFFFE), ("0x10>=16", 1), ("2+3==5 || 0", 1), ("bx^0x1234", 0),
                 ("cs", self.psp), ("pmode", 0), ("cpl", 0), ("cf|zf|sf <= 1", 1),
                 (f"word[{self.adr(0x180)}]", self.wert())]
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(int(self.box.eval(text)["wert"], 16), expected)

    def test_bad_arguments(self):
        for kwargs in ({}, {"ausdruck": "ax=="}, {"ausdruck": "foo==1"}, {"ausdruck": "(ax"}, {"cpl": 4},
                       {"adresse": "p:1000"}, {"ausdruck": "byte[nirgendwo]"}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DmcpError) as ctx:
                    self.box.run_until(**kwargs)
                self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertFalse(self.box.status()["paused"])

    def test_timeout_disarms(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.run_until(adresse="l:FFFFFFF0", timeout_s=0.5)
        self.assertEqual(ctx.exception.code, "timeout")
        st = self.box.status()
        self.assertFalse(st["paused"])
        self.assertNotIn("wartet_auf", st)

    def test_plain_pause_clears_condition(self):
        self.box.call("emu.run_until", adresse="l:FFFFFFF0")
        self.assertEqual(self.box.status()["wartet_auf"], "run_until")
        self.box.pause()
        self.assertNotIn("wartet_auf", self.box.status())

    def test_eval_nesting_limits(self):
        # Tiefe Verschachtelung darf den Stapel des Emulations-Threads nicht sprengen (Regel 4.5).
        for text in ("(" * 1000 + "1" + ")" * 1000, "!" * 5000 + "1", "-" * 5000 + "1",
                     "+".join(["1"] * 1000), "1" * 5000):
            with self.subTest(text=text[:20]):
                with self.assertRaises(DmcpError) as ctx:
                    self.box.eval(text)
                self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertEqual(int(self.box.eval("(" * 20 + "1" + ")" * 20)["wert"], 16), 1)
        self.assertEqual(int(self.box.eval("!" * 40 + "1")["wert"], 16), 1)
        self.assertIn("paused", self.box.status())


class WaitArmedTest(unittest.TestCase):
    """Ohne Emulator: Zeitablauf von _wait_armed darf einen echten Halt im Abbruchfenster nicht wegwerfen."""

    class Fake(Dosbox):
        def __init__(self, pause_reason):
            self.calls = []
            self.pause_reason = pause_reason

        def call(self, method, **params):
            self.calls.append(method)
            if method == "emu.pause":
                return {"paused": True, "stop_reason": self.pause_reason}
            if method == "emu.run_until":
                return {"paused": False, "stop_reason": "none", "wartet_auf": "run_until"}
            return {"paused": False, "stop_reason": "none"}

        def wait_paused(self, timeout_s=10.0):
            raise DmcpError("timeout", "simuliert")

    def test_breakpoint_in_abort_window_is_returned(self):
        for reason in ("breakpoint", "int_breakpoint", "watch", "run_until"):
            with self.subTest(reason=reason):
                fake = self.Fake(reason)
                st = fake.run_until(adresse="l:1000", timeout_s=0.01)
                self.assertEqual(st["stop_reason"], reason)
                self.assertNotIn("emu.continue", fake.calls)

    def test_own_pause_continues_and_raises_timeout(self):
        fake = self.Fake("pause")
        with self.assertRaises(DmcpError) as ctx:
            fake.run_until(adresse="l:1000", timeout_s=0.01)
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertEqual(fake.calls[-1], "emu.continue")
