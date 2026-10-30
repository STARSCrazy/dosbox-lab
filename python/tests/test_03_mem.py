import hashlib
import time
import unittest
from pathlib import Path

from dosbox_lab import DmcpError
from tests.util import class_box, need_build, start_prog, wait_until

MARK = b"DMCP-MEMTEST-MARK"


@need_build
class MemTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("MEMTEST.COM"))
        hits = wait_until(lambda: cls.box.mem_search("l:0", 0xA0000, MARK), timeout=15)
        cls.mark = int(hits[0]["linear"], 16)


    def counter(self):
        return int.from_bytes(self.box.mem_read(f"l:{self.mark + 17:X}", 4), "little")

    def test_counter_runs(self):
        first = self.counter()
        time.sleep(0.3)
        self.assertGreater(self.counter(), first)

    def test_read_matches_marker(self):
        self.assertEqual(self.box.mem_read(f"l:{self.mark:X}", len(MARK)), MARK)

    def test_real_mode_segoff_and_phys(self):
        seg, off = self.mark >> 4, self.mark & 0xF
        info = self.box.mem_read_info(f"r:{seg:04X}:{off:04X}", 4)
        self.assertEqual(int(info["adresse"]["linear"], 16), self.mark)
        self.assertEqual(info["adresse"]["modus"], "real")
        self.assertEqual(self.box.mem_read(f"p:{self.mark:X}", 4), MARK[:4])

    def test_write_is_mirrored(self):
        self.box.mem_write(f"l:{self.mark + 25:X}", bytes.fromhex("44332211"))
        wait_until(lambda: self.box.mem_read(f"l:{self.mark + 21:X}", 4) == bytes.fromhex("44332211"))

    def test_dump_file(self):
        info = self.box.mem_dump("l:0", 0x100000, "conv")
        data = Path(info["pfad"]).read_bytes()
        self.assertEqual(len(data), 0x100000)
        self.assertEqual(info["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(info["fehlend"], [])
        self.assertIn(MARK, data)
        self.assertTrue(Path(info["json_pfad"]).exists())

    def test_bad_addresses(self):
        for addr in ("12345", "zz:10", "l:", "va:1000", "p:XYZ"):
            with self.assertRaises(DmcpError) as ctx:
                self.box.mem_read(addr, 4)
            self.assertEqual(ctx.exception.code, "bad_argument", addr)
        for length in (0, 4097):
            with self.assertRaises(DmcpError) as ctx:
                self.box.mem_read("l:0", length)
            self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertGreater(self.counter(), 0)  # Emulator laeuft noch

    def test_beyond_ram_is_missing(self):
        # memsize=16: alles ab 01000000 liegt ausserhalb des Gast-RAMs (DOSBox-X liefert dort still FF).
        info = self.box.mem_read_info("l:01000000", 16)
        self.assertEqual(info["fehlend"], [["01000000", "01000010"]])
        self.assertEqual(info["hex"], "00" * 16)
        self.assertEqual(info["hinweis"], "Gast im Real Mode, Programm-Paging inaktiv")
        info = self.box.mem_read_info("l:00FFFFF8", 16)   # halb drin, halb draussen
        self.assertEqual(info["fehlend"], [["01000000", "01000008"]])
        self.assertEqual(info["hex"][16:], "00" * 8)
        info = self.box.mem_read_info("p:FFFFF000", 16)
        self.assertEqual(info["fehlend"], [["FFFFF000", "FFFFF010"]])
        self.assertEqual(info["hex"], "00" * 16)
        dump = self.box.mem_dump("p:00FFF000", 0x2000, "ramende")
        self.assertEqual(dump["fehlend"], [["01000000", "01001000"]])
        self.assertIn("hinweis", dump)
        for addr in ("p:FFFFF000", "l:00FFFFFF"):
            with self.assertRaises(DmcpError) as ctx:
                self.box.mem_write(addr, b"\x00\x00")
            self.assertEqual(ctx.exception.code, "bad_address", addr)

    def test_search_ignores_missing_pages(self):
        # Bereich ragt ueber das RAM-Ende (01000000) hinaus: fehlende Bytes lesen als 0, duerfen aber nicht treffen (B3).
        res = self.box.call("mem.search", adresse="l:00FF0000", laenge=0x20000, hex="00000000", max_treffer=1000000)
        for hit in res["treffer"]:
            self.assertLessEqual(int(hit["linear"], 16), 0x01000000 - 4, hit)
        self.assertEqual(res["fehlend"], [["01000000", "01010000"]])
        self.assertEqual(self.box.mem_search("l:01000000", 0x1000, b"\x00\x00\x00\x00"), [])

    def test_search_top_of_address_space(self):
        # Fehlender Bereich endet bei 2^32 (Ende als 00000000 gespeichert): trotzdem keine Scheintreffer.
        res = self.box.call("mem.search", adresse="l:FFFF0000", laenge=0x10000, hex="00000000")
        self.assertEqual(res["treffer"], [])
        self.assertTrue(res["fehlend"])

    def test_search_large_missing_region_is_fast(self):
        start = time.monotonic()
        res = self.box.call("mem.search", adresse="l:01000000", laenge=0x08000000, hex="00000000", timeout_ms=60000)
        # Grenze grosszuegig, damit ein belasteter Rechner den Test nicht kippt; faengt trotzdem ein Haengen ab
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(res["treffer"], [])
        self.assertTrue(res["fehlend"])

    def test_real_mode_selector_offset_explains(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.mem_read("0018:00010000", 4)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("pmode=true", ctx.exception.message)
        self.assertIn("l:<linear>", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
