import json
import unittest
from pathlib import Path

from dosbox_lab.files import covered, read_coverage
from tests.util import class_box, need_build, run_typed, start_idle, wait_until


def flow_psp(box):
    module = wait_until(lambda: next((m for m in box.modules() if m["modul"] == "FLOW.COM"), None), timeout=15)
    psp = int(module["psp"], 16)
    wait_until(lambda: box.mem_read(f"r:{psp:04X}:01F0", 1) == b"\x01", timeout=15)
    return psp


@need_build
class CoverageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.box.coverage_start()
        run_typed(cls.box, "FLOW")
        cls.psp = flow_psp(cls.box)
        cls.info = cls.box.coverage_save("cov_flow")
        cls.blocks = read_coverage(cls.info["pfad"])
        cls.meta = json.loads(Path(cls.info["json_pfad"]).read_text(encoding="utf-8"))


    def lin(self, off):
        return self.psp * 16 + off

    def test_executed_instruction_starts(self):
        for off in (0x100, 0x104, 0x107, 0x109, 0x10C, 0x10F, 0x111, 0x140, 0x144, 0x14E, 0x160, 0x168, 0x170):
            self.assertTrue(covered(self.blocks, self.lin(off)), hex(off))

    def test_not_executed(self):
        for off in (0x101, 0x115, 0x130, 0x180):
            self.assertFalse(covered(self.blocks, self.lin(off)), hex(off))

    def test_header_and_order(self):
        data = Path(self.info["pfad"]).read_bytes()
        self.assertEqual(data[:4], b"DMCV")
        starts = list(self.blocks)
        self.assertEqual(starts, sorted(starts))
        self.assertTrue(all(s % 0x10000 == 0 for s in starts))
        self.assertTrue(all(any(bitmap) for bitmap in self.blocks.values()))
        self.assertEqual(self.info["bloecke"], len(starts))

    def test_json_contexts(self):
        self.assertEqual(self.meta["format"], "dosbox-lab-coverage/1")
        start = self.lin(0x100) & 0xFFFF0000
        block = next(b for b in self.meta["bloecke"] if int(b["linear_start"], 16) == start)
        ctx = next(k for k in block["kontexte"] if k.get("modul") == "FLOW.COM")
        self.assertEqual(ctx, {"seg": f"0x{self.psp:04X}", "modus": "real", "bits": 16,
                               "modul": "FLOW.COM", "rel_seg": "0x0000"})


@need_build
class CoverageEdgeTest(unittest.TestCase):
    def setUp(self):
        self.box = start_idle()

    def tearDown(self):
        self.box.stop()

    def test_save_without_data_and_double_start(self):
        info = self.box.coverage_save("leer")
        self.assertEqual(read_coverage(info["pfad"]), {})
        self.box.coverage_stop()
        self.box.coverage_start()
        self.box.coverage_start()
        self.assertTrue(self.box.coverage_status()["aktiv"])

    def test_block_limit_stops_cleanly(self):
        self.box.events_start(typen=["limit_reached"])
        self.box.coverage_start(max_bloecke=1)
        run_typed(self.box, "FLOW")
        status = wait_until(lambda: (lambda s: s if s["limit_erreicht"] else None)(self.box.coverage_status()),
                            timeout=15)
        self.assertFalse(status["aktiv"])
        self.assertEqual(status["bloecke"], 1)
        event = self.box.wait_event(lambda e: e["typ"] == "limit_reached")
        self.assertEqual(event["aufzeichnung"], "coverage")
        flow_psp(self.box)  # Emulator laeuft weiter, FLOW kommt ans Ende


if __name__ == "__main__":
    unittest.main()
