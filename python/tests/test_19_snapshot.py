import hashlib
import json
import unittest
from pathlib import Path

from tests.util import class_box, need_build, run_typed, start_idle, start_prog, wait_until


@need_build
class SnapshotTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("PMODE.COM"))
        wait_until(lambda: cls.box.registers()["paging"], timeout=15)
        wait_until(lambda: cls.box.mem_read("l:01000000", 4) == b"DMCP")
        cls.box.set_segments("PMODE.COM", [
            {"name": "daten", "va_start": "0x0", "va_ende": "0x1FFF", "basis_linear": "0x01000000"}])
        cls.snap = cls.box.snapshot("nach start", mit_erweiterung=True)
        cls.dir = Path(cls.snap["pfad"])
        cls.meta = json.loads((cls.dir / "meta.json").read_text(encoding="utf-8"))


    def test_directory_and_files(self):
        self.assertTrue(self.dir.name.startswith("snapshot_"))
        self.assertTrue(self.dir.name.endswith("_nach_start"))
        for name in ("mem_conv.bin", "mem_conv.json", "mem_lin_01000000.bin", "mem_lin_01000000.json",
                     "mem_phys_ext.bin", "regs.json", "descriptors.json", "paging.json", "video.json", "meta.json"):
            self.assertTrue((self.dir / name).exists(), name)

    def test_memory_contents(self):
        self.assertEqual((self.dir / "mem_conv.bin").stat().st_size, 0x100000)
        lin = (self.dir / "mem_lin_01000000.bin").read_bytes()
        self.assertEqual(len(lin), 0x2000)
        self.assertEqual(lin[:4], b"DMCP")
        self.assertEqual((self.dir / "mem_phys_ext.bin").stat().st_size, 16 * 1024 * 1024 - 0x100000)

    def test_meta(self):
        self.assertEqual(self.meta["format"], "dosbox-mcp-snapshot/1")
        self.assertEqual(self.meta["label"], "nach start")
        self.assertFalse(self.meta["pausiert_vorher"])
        self.assertEqual(self.meta["bereiche"][0]["start"], "0x01000000")
        self.assertEqual(self.meta["bereiche"][0]["ende"], "0x01002000")
        for name, digest in self.meta["dateien"].items():
            self.assertEqual(hashlib.sha256((self.dir / name).read_bytes()).hexdigest(), digest, name)

    def test_paging_and_registers(self):
        paging = json.loads((self.dir / "paging.json").read_text(encoding="utf-8"))
        self.assertTrue(paging["paging"])
        self.assertEqual(paging["cr3"], "00300000")
        self.assertEqual(paging["bereiche"][0]["seiten"][0]["phys"], "00200000")
        regs = json.loads((self.dir / "regs.json").read_text(encoding="utf-8"))
        self.assertEqual(regs["modus"], "pm")

    def test_emulator_runs_again(self):
        self.assertFalse(self.box.status()["paused"])

    def test_no_hint_in_pm(self):
        self.assertNotIn("hinweise", self.snap)


@need_build
class SnapshotModeTest(unittest.TestCase):
    """Extender stehen oft im Real Mode: snapshot(bis="pmode") haelt erst im Protected Mode an (Abnahme MAG)."""

    def setUp(self):
        self.box = start_idle()

    def tearDown(self):
        self.box.stop()

    def test_until_pmode(self):
        run_typed(self.box, "PMODE")  # Tastenfolge laeuft in emulierter Zeit; der Gast steht noch am Prompt
        snap = self.box.snapshot("pm", bis="pmode")
        meta = json.loads((Path(snap["pfad"]) / "meta.json").read_text(encoding="utf-8"))
        self.assertTrue(meta["status"]["pmode"])
        self.assertEqual(meta["status"]["stop_reason"], "pmode")
        self.assertFalse(self.box.status()["paused"])

    def test_hint_when_linear_ranges_unreachable(self):
        run_typed(self.box, "FLOW")
        wait_until(lambda: any(m["modul"] == "FLOW.COM" for m in self.box.modules()), timeout=15)
        self.box.set_segments("FLOW.COM", [
            {"name": "hoch", "va_start": "0x0", "va_ende": "0xFFF", "basis_linear": "0x02000000"}])
        snap = self.box.snapshot("real")
        self.assertEqual(len(snap["hinweise"]), 1)
        self.assertIn("bis=\"pmode\"", snap["hinweise"][0])
        meta = json.loads((Path(snap["pfad"]) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["hinweise"], snap["hinweise"])


if __name__ == "__main__":
    unittest.main()


class SnapshotUnitTest(unittest.TestCase):
    """Ohne Emulator: Bereichsstuecke (B7) und Nummernvergabe (B13)."""

    def test_overlapping_big_segments_split_into_64mb_pieces(self):
        from dosbox_mcp.snapshot import MAX_BEREICH, _stuecke
        mb = 1 << 20
        found = [(0x1000000, 0x1000000 + 48 * mb, "A", "a"), (0x1000000 + 24 * mb, 0x1000000 + 72 * mb, "B", "b")]
        pieces = _stuecke(found)
        self.assertGreater(len(pieces), 1)
        self.assertEqual(pieces[0]["start"], 0x1000000)
        self.assertEqual(pieces[-1]["ende"], 0x1000000 + 72 * mb)
        for p in pieces:
            self.assertLessEqual(p["ende"] - p["start"], MAX_BEREICH)
            self.assertGreater(p["ende"], p["start"])
        for a, b in zip(pieces, pieces[1:]):
            self.assertEqual(a["ende"], b["start"])

    def test_number_is_highest_plus_one(self):
        import tempfile
        from dosbox_mcp.snapshot import _next_nr
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertEqual(_next_nr(out), 1)
            (out / "snapshot_001_a").mkdir()
            (out / "snapshot_003_b").mkdir()
            self.assertEqual(_next_nr(out), 4)
