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
        self.assertEqual(int(info["address"]["linear"], 16), self.mark)
        self.assertEqual(info["address"]["mode"], "real")
        self.assertEqual(self.box.mem_read(f"p:{self.mark:X}", 4), MARK[:4])

    def test_write_is_mirrored(self):
        self.box.mem_write(f"l:{self.mark + 25:X}", bytes.fromhex("44332211"))
        wait_until(lambda: self.box.mem_read(f"l:{self.mark + 21:X}", 4) == bytes.fromhex("44332211"))

    def test_dump_file(self):
        info = self.box.mem_dump("l:0", 0x100000, "conv")
        data = Path(info["path"]).read_bytes()
        self.assertEqual(len(data), 0x100000)
        self.assertEqual(info["sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(info["missing"], [])
        self.assertIn(MARK, data)
        self.assertTrue(Path(info["json_path"]).exists())

    def test_bad_addresses(self):
        for addr in ("12345", "zz:10", "l:", "va:1000", "p:XYZ"):
            with self.assertRaises(DmcpError) as ctx:
                self.box.mem_read(addr, 4)
            self.assertEqual(ctx.exception.code, "bad_argument", addr)
        for length in (0, 4097):
            with self.assertRaises(DmcpError) as ctx:
                self.box.mem_read("l:0", length)
            self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertGreater(self.counter(), 0)  # emulator is still running

    def test_beyond_ram_is_missing(self):
        # memsize=16: everything from 01000000 on lies outside the guest RAM (DOSBox-X silently returns FF there).
        info = self.box.mem_read_info("l:01000000", 16)
        self.assertEqual(info["missing"], [["01000000", "01000010"]])
        self.assertEqual(info["hex"], "00" * 16)
        self.assertEqual(info["hint"], "guest is in real mode, program paging is inactive")
        info = self.box.mem_read_info("l:00FFFFF8", 16)   # half inside, half outside
        self.assertEqual(info["missing"], [["01000000", "01000008"]])
        self.assertEqual(info["hex"][16:], "00" * 8)
        info = self.box.mem_read_info("p:FFFFF000", 16)
        self.assertEqual(info["missing"], [["FFFFF000", "FFFFF010"]])
        self.assertEqual(info["hex"], "00" * 16)
        dump = self.box.mem_dump("p:00FFF000", 0x2000, "ram_end")
        self.assertEqual(dump["missing"], [["01000000", "01001000"]])
        self.assertIn("hint", dump)
        for addr in ("p:FFFFF000", "l:00FFFFFF"):
            with self.assertRaises(DmcpError) as ctx:
                self.box.mem_write(addr, b"\x00\x00")
            self.assertEqual(ctx.exception.code, "bad_address", addr)

    def test_search_ignores_missing_pages(self):
        # The range extends past the end of RAM (01000000): missing bytes read as 0 but must not match (B3).
        res = self.box.call("mem.search", address="l:00FF0000", length=0x20000, hex="00000000", max_hits=1000000)
        for hit in res["hits"]:
            self.assertLessEqual(int(hit["linear"], 16), 0x01000000 - 4, hit)
        self.assertEqual(res["missing"], [["01000000", "01010000"]])
        self.assertEqual(self.box.mem_search("l:01000000", 0x1000, b"\x00\x00\x00\x00"), [])

    def test_search_top_of_address_space(self):
        # The missing range ends at 2^32 (end stored as 00000000): still no false match.
        res = self.box.call("mem.search", address="l:FFFF0000", length=0x10000, hex="00000000")
        self.assertEqual(res["hits"], [])
        self.assertTrue(res["missing"])

    def test_search_large_missing_region_is_fast(self):
        start = time.monotonic()
        res = self.box.call("mem.search", address="l:01000000", length=0x08000000, hex="00000000", timeout_ms=60000)
        # Generous limit so a loaded machine does not fail the test; it still catches a hang
        self.assertLess(time.monotonic() - start, 15)
        self.assertEqual(res["hits"], [])
        self.assertTrue(res["missing"])

    def test_real_mode_selector_offset_explains(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.mem_read("0018:00010000", 4)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("pmode=true", ctx.exception.message)
        self.assertIn("l:<linear>", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
