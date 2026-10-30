import unittest
from pathlib import Path

from tests.util import class_box, need_build, start_prog, wait_until


@need_build
class PmodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("PMODE.COM"))
        wait_until(lambda: cls.box.registers()["paging"], timeout=15)
        wait_until(lambda: cls.box.mem_read("l:01000000", 4) == b"DMCP")


    def test_registers_in_pm(self):
        regs = self.box.registers()
        self.assertEqual(regs["modus"], "pm")
        self.assertEqual(regs["bits"], 32)
        self.assertEqual(regs["seg"]["cs"]["sel"], "0008")
        self.assertEqual(regs["ctl"]["cr3"], "00300000")

    def test_gdt(self):
        gdt = self.box.descriptors("gdt")
        entries = {e["selektor"]: e for e in gdt["eintraege"]}
        self.assertEqual(entries["0008"]["art"], "code")
        self.assertEqual(entries["0008"]["bits"], 32)
        self.assertEqual(entries["0018"]["basis"], "01000000")
        self.assertTrue(entries["0018"]["praesent"])

    def test_translate_selector(self):
        addr = self.box.translate("0018:00000000")
        self.assertEqual(addr["modus"], "pm")
        self.assertEqual(addr["linear"], "01000000")
        self.assertEqual(addr["phys"], "00200000")
        self.assertEqual(addr["bits"], 32)

    def test_paging_info(self):
        info = self.box.paging("l:01000000", 0x2000)
        self.assertTrue(info["paging"])
        self.assertEqual(info["seiten"][0]["phys"], "00200000")
        self.assertEqual(info["seiten"][1]["phys"], "00201000")
        self.assertFalse(self.box.paging("l:01400000")["seiten"][0]["praesent"])

    def test_read_linear_and_phys(self):
        self.assertEqual(self.box.mem_read("0018:00000000", 4), b"DMCP")
        self.assertNotIn("hinweis", self.box.mem_read_info("0018:00000000", 4))
        self.assertEqual(self.box.mem_read("p:00200000", 4), b"DMCP")

    def test_dump_partially_unmapped(self):
        info = self.box.mem_dump("l:013FF000", 0x2000, "grenze")
        data = Path(info["pfad"]).read_bytes()
        self.assertEqual(len(data), 0x2000)
        self.assertEqual(info["fehlend"], [["01400000", "01401000"]])
        self.assertEqual(data[0x1000:], bytes(0x1000))
