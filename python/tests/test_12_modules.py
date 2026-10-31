import unittest

from dosbox_lab import DmcpError
from tests.util import TESTPROGS, class_box, need_build, run_typed, start_idle, start_prog, wait_until


def module(box, name):
    return next((m for m in box.modules() if m["module"] == name), None)


@need_build
class ModulesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "FLOW")
        cls.flow = wait_until(lambda: module(cls.box, "FLOW.COM"), timeout=15)
        cls.psp = int(cls.flow["psp"], 16)
        wait_until(lambda: cls.box.mem_read(f"r:{cls.psp:04X}:01F0", 1) == b"\x01", timeout=15)


    def test_exec_registers_module(self):
        m = self.flow
        self.assertEqual(m["kind"], "com")
        self.assertEqual(m["file"], "C:\\FLOW.COM")
        self.assertEqual(m["rel_base"], m["psp"])  # COM: rel is relative to the PSP
        self.assertEqual(int(m["load_seg"], 16), self.psp + 0x10)
        self.assertTrue(m["target"])
        self.assertEqual(m["segments"], [])

    def test_rel_address(self):
        addr = self.box.translate("rel:0000:0160")
        self.assertEqual(addr["linear"], f"{self.psp * 16 + 0x160:08X}")
        self.assertEqual(addr["module"], "FLOW.COM")
        self.assertEqual(addr["rel"], "0000:0160")
        self.assertEqual(self.box.mem_read("rel:FLOW.COM:0000:0160", 1), b"\xC3")  # ret

    def test_plain_address_gets_module(self):
        addr = self.box.translate(f"r:{self.psp:04X}:0107")
        self.assertEqual(addr["module"], "FLOW.COM")
        self.assertEqual(addr["rel"], "0000:0107")

    def test_va_mapping_and_errors(self):
        base = self.psp * 16
        result = self.box.set_segments("TESTMOD", [
            {"name": "code", "va_start": "0x0", "va_end": "0xFFF", "base_linear": f"0x{base:X}"}])
        self.assertTrue(result["applied"])
        self.assertEqual([s["origin"] for s in result["module_info"]["segments"]], ["set"])
        self.assertEqual([s["origin"] for s in module(self.box, "TESTMOD")["segments"]], ["set"])
        try:
            addr = self.box.translate("va:TESTMOD:160")
            self.assertEqual(addr["linear"], f"{base + 0x160:08X}")
            self.assertEqual(addr["module"], "TESTMOD")
            self.assertEqual(addr["va"], "00000160")
            self.assertEqual(self.box.translate("va:160")["linear"], f"{base + 0x160:08X}")  # the only module with va
            with self.assertRaises(DmcpError) as ctx:
                self.box.set_segments("TESTMOD", [
                    {"name": "a", "va_start": "0x0", "va_end": "0x1FF", "base_linear": "0x0"},
                    {"name": "b", "va_start": "0x100", "va_end": "0x2FF", "base_linear": "0x0"}])
            self.assertEqual(ctx.exception.code, "bad_argument")
            with self.assertRaises(DmcpError) as ctx:
                self.box.translate("va:TESTMOD:5000")
            self.assertEqual(ctx.exception.code, "bad_address")
        finally:
            self.box.clear_modules("TESTMOD")
        self.assertIsNone(module(self.box, "TESTMOD"))

    def test_bad_module_addresses(self):
        for bad in ("va:NIX:10", "rel:NIX.EXE:0:0", "va:zz", "rel:1:2:3:4:5", "va:10"):
            with self.assertRaises(DmcpError, msg=bad) as ctx:
                self.box.translate(bad)
            self.assertEqual(ctx.exception.code, "bad_argument", bad)


@need_build
class ModulesOverlayTest(unittest.TestCase):
    def test_overlay_module(self):
        box = start_prog("OVERLAY.COM")
        self.addCleanup(box.stop)
        m = wait_until(lambda: (lambda m: m if m and m.get("kind") == "overlay" else None)(
            module(box, "EXITCODE.COM")), timeout=15)
        size = (TESTPROGS / "EXITCODE.COM").stat().st_size
        self.assertEqual(m["kind"], "overlay")
        self.assertEqual(m["load_seg"], "3000")
        self.assertEqual(m["rel_base"], "3000")  # overlay: rel is relative to the load segment
        self.assertEqual(int(m["paragraphs"], 16), (size + 15) // 16)  # image size, not memory block
        self.assertEqual(m["exec_count"], 1)
        self.assertEqual(m["segments"], [])


@need_build
class ModulesExeTest(unittest.TestCase):
    def test_exe_rel_base_is_load_segment(self):
        box = start_prog("LE.EXE")
        self.addCleanup(box.stop)
        m = wait_until(lambda: module(box, "LE.EXE"), timeout=15)
        self.assertEqual(m["kind"], "exe")
        self.assertEqual(m["rel_base"], m["load_seg"])
        self.assertEqual(int(m["load_seg"], 16), int(m["psp"], 16) + 0x10)
        self.assertNotEqual(m["rel_base"], m["psp"])

    def test_clear_all(self):
        box = start_idle()
        self.addCleanup(box.stop)
        run_typed(box, "EXITCODE")
        wait_until(lambda: module(box, "EXITCODE.COM"), timeout=15)
        box.set_segments("EXTRA", [{"name": "a", "va_start": "0x0", "va_end": "0xFF", "base_linear": "0x1000"}])
        self.assertGreaterEqual(len(box.modules()), 2)
        self.assertEqual(box.clear_modules()["count"], 0)
        self.assertEqual(box.modules(), [])


@need_build
class ModulesRerunTest(unittest.TestCase):
    def test_rerun_keeps_one_entry(self):
        box = start_idle()
        try:
            run_typed(box, "EXITCODE")
            wait_until(lambda: (module(box, "EXITCODE.COM") or {}).get("exec_count") == 1, timeout=15)
            run_typed(box, "EXITCODE")
            wait_until(lambda: (module(box, "EXITCODE.COM") or {}).get("exec_count") == 2, timeout=15)
            self.assertEqual(len([m for m in box.modules() if m["module"] == "EXITCODE.COM"]), 1)
        finally:
            box.stop()


if __name__ == "__main__":
    unittest.main()
