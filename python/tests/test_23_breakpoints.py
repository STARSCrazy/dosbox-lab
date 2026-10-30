import time
import unittest

from dosbox_mcp import DmcpError
from tests.util import class_box, need_build, prog_psp, start_prog


@need_build
class BreakpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")
        cls.box.call("symbols.set", modul="DEBUG.COM", adressraum="rel16",
                     symbole=[{"name": "unter", "adr": "0000:0140"}])


    def tearDown(self):
        self.box.break_remove(all=True)
        self.box.break_int_remove(all=True)
        if self.box.status()["paused"]:
            self.box.resume()

    def adr(self, off):
        return f"r:{self.psp:04X}:{off:04X}"

    def wert(self):
        return int.from_bytes(self.box.mem_read(self.adr(0x180), 2), "little")

    def test_symbol_breakpoint(self):
        listing = self.box.break_add("unter")
        entry = listing["eintraege"][0]
        self.assertEqual(entry["adresse"]["symbol"], "unter")
        self.assertEqual(entry["quelle"], "unter")
        st = self.box.wait_paused()
        self.assertEqual(st["stop_reason"], "breakpoint")
        self.assertEqual(int(st["eip"], 16), 0x140)
        self.assertEqual(self.box.break_list()["eintraege"][0]["treffer"], 1)

    def test_condition(self):
        self.box.run_until(adresse=self.adr(0x10A))
        target = (self.wert() + 3) & 0xFFFF
        self.box.break_add(self.adr(0x10E), bedingung=f"ax=={target}")
        self.box.resume()
        st = self.box.wait_paused()
        self.assertEqual(int(st["eip"], 16), 0x10E)
        self.assertEqual(int(self.box.registers()["gp"]["eax"], 16) & 0xFFFF, target)
        self.assertEqual(self.box.break_list()["eintraege"][0]["bedingung"], f"ax=={target}")

    def test_condition_false_never_stops(self):
        self.box.break_add(self.adr(0x10E), bedingung="ax==0x1FFFF")
        before = self.wert()
        time.sleep(1.0)
        self.assertFalse(self.box.status()["paused"])
        self.assertEqual(self.box.break_list()["eintraege"][0]["treffer"], 0)
        self.assertNotEqual(self.wert(), before)

    def test_once(self):
        self.box.break_add(self.adr(0x140), einmalig=True)
        self.box.wait_paused()
        self.assertEqual(self.box.break_list()["eintraege"], [])
        self.box.resume()
        time.sleep(0.5)
        self.assertFalse(self.box.status()["paused"])

    def test_hit_counter_and_replace(self):
        self.box.break_add(self.adr(0x140))
        for i in range(3):
            self.box.wait_paused()
            if i < 2:
                self.box.resume()  # the third stop stays parked: no fourth hit before the list
        self.assertEqual(self.box.break_list()["eintraege"][0]["treffer"], 3)
        listing = self.box.break_add(self.adr(0x140), bedingung="1")
        self.assertEqual(len(listing["eintraege"]), 1)
        self.assertEqual(listing["eintraege"][0]["treffer"], 0)
        self.assertEqual(listing["breakpoints"], [f"{self.psp * 16 + 0x140:08X}"])

    def test_legacy_linear(self):
        self.box.break_add(self.psp * 16 + 0x140)
        st = self.box.wait_paused()
        self.assertEqual(int(st["stop_linear"], 16), self.psp * 16 + 0x140)

    def test_int_breakpoint(self):
        listing = self.box.break_int_add(0x16, ah=0x01)
        self.assertEqual(listing["int_haltepunkte"][0]["nummer"], "16")
        st = self.box.wait_paused()
        self.assertEqual(st["stop_reason"], "int_breakpoint")
        self.assertEqual(int(st["eip"], 16), 0x113)
        self.assertEqual(st["stop_int"]["nummer"], "16")
        self.assertEqual(int(st["stop_int"]["ax"], 16) >> 8, 0x01)
        before = self.wert()
        self.box.resume()  # INT laeuft jetzt, naechste Runde haelt wieder
        st = self.box.wait_paused()
        self.assertEqual(int(st["eip"], 16), 0x113)
        self.assertEqual(self.wert(), (before + 1) & 0xFFFF)

    def test_int_filter_ah(self):
        self.box.break_int_add(0x16, ah=0x00)
        time.sleep(0.5)
        self.assertFalse(self.box.status()["paused"])
        self.box.key("A")
        st = self.box.wait_paused()
        self.assertEqual(int(st["eip"], 16), 0x119)

    def test_breakpoint_event(self):
        self.box.events_start(typen=["breakpoint"], name="ev_bp")
        try:
            self.box.break_add(self.adr(0x140))
            self.box.wait_paused()
            event = self.box.wait_event(lambda e: e["typ"] == "breakpoint")
            self.assertEqual(event["art"], "code")
            self.assertEqual(event["linear"], f"0x{self.psp * 16 + 0x140:08X}")
            self.assertEqual(event["treffer"], 1)
        finally:
            self.box.events_stop()

    def test_errors(self):
        for kwargs in ({"ziel": "gibtsnicht"}, {"ziel": self.adr(0x140), "bedingung": "ax=="}, {}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DmcpError) as ctx:
                    self.box.break_add(**kwargs)
                self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertFalse(self.box.break_remove(self.adr(0x199))["entfernt"])
        with self.assertRaises(DmcpError):
            self.box.break_int_add(0x100)
