import json
import unittest
import zipfile
from pathlib import Path

from dosbox_lab import DmcpError
from tests.util import class_box, need_build, prog_psp, start_idle, start_prog, wait_prompt, wait_until


@need_build
class StateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")
        cls.out = Path(cls.box.inst.outdir)


    def tearDown(self):
        if self.box.status()["paused"]:
            self.box.resume()

    def adr(self, off):
        return f"r:{self.psp:04X}:{off:04X}"

    def wert(self):
        return int.from_bytes(self.box.mem_read(self.adr(0x180), 2), "little")

    def test_save_and_load_restores(self):
        self.box.run_until(adresse=self.adr(0x10A))
        saved = self.wert()
        info = self.box.state_save("st_debug")
        self.assertTrue(Path(info["pfad"]).exists())
        self.assertGreater(info["groesse"], 0)
        meta = json.loads(Path(info["info_pfad"]).read_text(encoding="utf-8"))
        self.assertEqual(meta["format"], "dosbox-lab-state/1")
        self.box.resume()
        wait_until(lambda: (self.wert() - saved) & 0xFFFF >= 3, timeout=15)
        self.box.pause()
        result = self.box.state_load("st_debug")
        self.assertTrue(result["geladen"])
        self.assertEqual(self.wert(), saved)
        regs = self.box.registers()
        self.assertEqual(int(regs["gp"]["eip"], 16), 0x10A)
        self.box.resume()
        wait_until(lambda: self.wert() != saved, timeout=15)

    def test_load_missing(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("gibtsnicht")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("paused", self.box.status())  # kein Dialog: Emulator antwortet

    def test_load_not_a_savestate(self):
        (self.out / "kaputt.sav").write_bytes(b"das ist kein zip")
        (self.out / "kaputt.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("kaputt")
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_load_without_sidecar_needs_flag(self):
        self.box.run_until(adresse=self.adr(0x10A))
        info = self.box.state_save("st_ohne")
        Path(info["info_pfad"]).unlink()
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_ohne")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertTrue(self.box.state_load("st_ohne", ohne_pruefung=True)["geladen"])

    def test_sidecar_mismatch(self):
        self.box.run_until(adresse=self.adr(0x10A))
        info = self.box.state_save("st_falsch")
        meta = json.loads(Path(info["info_pfad"]).read_text(encoding="utf-8"))
        meta["speicher_seiten"] += 256
        Path(info["info_pfad"]).write_text(json.dumps(meta), encoding="utf-8")
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_falsch")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("speicher", ctx.exception.message.lower())

    def test_zip_memory_size_mismatch(self):
        # Memory_Size im Archiv passt nicht zur Instanz: nie laden (Upstream kopiert sonst ueber MemBase hinaus),
        # auch nicht mit ohne_pruefung oder mit einer (veralteten) passenden Begleitdatei.
        self.box.run_until(adresse=self.adr(0x10A))
        info = self.box.state_save("st_mem")
        with zipfile.ZipFile(info["pfad"]) as src, zipfile.ZipFile(self.out / "st_mem2.sav", "w") as dst:
            for item in src.infolist():
                data = src.read(item)
                if item.filename == "Memory_Size":
                    data = str(int(data) + 256).encode()
                dst.writestr(item, data)
        (self.out / "st_mem2.json").write_text(Path(info["info_pfad"]).read_text(encoding="utf-8"), encoding="utf-8")
        for flag in (False, True):
            with self.subTest(ohne_pruefung=flag):
                with self.assertRaises(DmcpError) as ctx:
                    self.box.state_load("st_mem2", ohne_pruefung=flag)
                self.assertEqual(ctx.exception.code, "bad_argument")
                self.assertIn("speicher", ctx.exception.message.lower())
        (self.out / "st_mem2.json").unlink()
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_mem2", ohne_pruefung=True)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("paused", self.box.status())

    def test_load_drops_stop_condition(self):
        self.box.run_until(adresse=self.adr(0x10A))
        self.box.state_save("st_until")
        self.box.call("emu.run_until", adresse="l:FFFFFFF0")
        self.assertEqual(self.box.status()["wartet_auf"], "run_until")
        self.box.state_load("st_until")
        self.assertNotIn("wartet_auf", self.box.status())

    def test_breakpoint_at_loaded_position_hits(self):
        # Der alte Halt (breakpoint) gehoert nicht zum geladenen CS:EIP: ein Haltepunkt dort darf nicht
        # uebersprungen werden.
        self.box.run_until(adresse=self.adr(0x10A))
        saved = self.wert()
        self.box.state_save("st_bp")
        self.box.break_add(self.adr(0x10A))
        try:
            self.box.resume()
            st = self.box.wait_paused()
            self.assertEqual(st["stop_reason"], "breakpoint")
            self.box.state_load("st_bp")
            self.assertEqual(self.wert(), saved)
            self.box.resume()
            st = self.box.wait_paused()
            self.assertEqual(st["stop_reason"], "breakpoint")
            self.assertEqual(int(st["eip"], 16), 0x10A)
            self.assertEqual(self.wert(), saved)
        finally:
            self.box.break_remove(all=True)

    def test_bad_name(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_save("..\\boese")
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_corrupt_zip_with_valid_sidecar(self):
        self.box.run_until(adresse=self.adr(0x10A))
        info = self.box.state_save("st_echt")
        (self.out / "st_muell.sav").write_bytes(b"PK\x03\x04" + b"garbage" * 100)
        (self.out / "st_muell.json").write_text(Path(info["info_pfad"]).read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_muell")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("paused", self.box.status())

    def test_truncated_state(self):
        self.box.run_until(adresse=self.adr(0x10A))
        info = self.box.state_save("st_ganz")
        data = Path(info["pfad"]).read_bytes()
        (self.out / "st_kurz.sav").write_bytes(data[: len(data) // 2])
        (self.out / "st_kurz.json").write_text(Path(info["info_pfad"]).read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_kurz")
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_reserved_names(self):
        for name in ("nul", "CON.txt", "com1", "name.", "name "):
            with self.assertRaises(DmcpError, msg=name) as ctx:
                self.box.state_save(name)
            self.assertEqual(ctx.exception.code, "bad_argument")

    def test_pfad_not_a_string(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.call("state.load", pfad=5)
        self.assertEqual(ctx.exception.code, "bad_argument")


@need_build
class StateEarlyLoadTest(unittest.TestCase):
    """Laden, bevor die DOS-Shell laeuft, brachte den Emulator zum Absturz (Abnahme D31)."""

    def test_load_before_shell_is_refused_not_crash(self):
        donor = start_idle()
        try:
            wait_prompt(donor)
            donor.pause()
            pfad = donor.state_save("frueh")["pfad"]
        finally:
            donor.stop()
        box = start_idle()
        try:
            box.pause()  # direkt nach dem Start: Shell noch nicht da
            with self.assertRaises(DmcpError) as ctx:
                box.state_load(pfad=pfad)
            self.assertEqual(ctx.exception.code, "not_ready")
            self.assertTrue(box.inst.alive())
            box.resume()
            wait_prompt(box)
            box.pause()
            self.assertTrue(box.state_load(pfad=pfad)["geladen"])
            box.resume()
            wait_prompt(box)
        finally:
            box.stop()

    def test_load_while_starting_waits(self):
        donor = start_idle()
        try:
            wait_prompt(donor)
            donor.pause()
            pfad = donor.state_save("frueh2")["pfad"]
        finally:
            donor.stop()
        box = start_idle()
        try:
            self.assertTrue(box.state_load(pfad=pfad)["geladen"])  # laeuft noch an: die Bibliothek wartet
            self.assertTrue(box.inst.alive())
        finally:
            box.stop()


@need_build
class StateAfterBootTest(unittest.TestCase):
    """Nach BOOT eines Gast-Systems ist der DOS-Kernel wieder aus; Laden brachte den Emulator dann zum Absturz
    (Review D31). Erwartet: saubere Ablehnung not_ready, Emulator lebt."""

    def test_load_after_boot(self):
        import shutil
        import tempfile
        from dosbox_lab import Dosbox, Mount
        from tests.util import US
        donor = start_idle()
        try:
            wait_prompt(donor)
            donor.pause()
            pfad = donor.state_save("vor_boot")["pfad"]
        finally:
            donor.stop()
        tmp = Path(tempfile.mkdtemp(prefix="dmcp-boot-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        sector = bytes([0xFA, 0xEB, 0xFE]) + bytes(507) + b"\x55\xAA"  # cli; jmp $
        (tmp / "BOOT.IMG").write_bytes(sector + bytes(1474560 - 512))
        box = Dosbox.start(mounts=[Mount("C", str(tmp))], autoexec=[], headless=True, conf=US)
        try:
            wait_prompt(box)
            box.type_text("BOOT C:\BOOT.IMG\n", layout="us")

            def booted():
                regs = box.registers()
                return int(regs["seg"]["cs"]["basis"], 16) + int(regs["gp"]["eip"], 16) in (0x7C01, 0x7C02, 0x7C03)
            wait_until(booted, timeout=20)
            box.pause()  # angehalten: die Bibliothek wartet nicht, der Fehler kommt sofort
            with self.assertRaises(DmcpError) as ctx:
                box.state_load(pfad=pfad)
            self.assertEqual(ctx.exception.code, "not_ready")
            self.assertTrue(box.inst.alive())
            self.assertTrue(box.status()["paused"])
        finally:
            box.stop()
