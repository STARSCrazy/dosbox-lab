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

    def addr(self, off):
        return f"r:{self.psp:04X}:{off:04X}"

    def value(self):
        return int.from_bytes(self.box.mem_read(self.addr(0x180), 2), "little")

    def test_save_and_load_restores(self):
        self.box.run_until(address=self.addr(0x10A))
        saved = self.value()
        info = self.box.state_save("st_debug")
        self.assertTrue(Path(info["path"]).exists())
        self.assertGreater(info["size"], 0)
        meta = json.loads(Path(info["info_path"]).read_text(encoding="utf-8"))
        self.assertEqual(meta["format"], "dosbox-lab-state/1")
        self.box.resume()
        wait_until(lambda: (self.value() - saved) & 0xFFFF >= 3, timeout=15)
        self.box.pause()
        result = self.box.state_load("st_debug")
        self.assertTrue(result["loaded"])
        self.assertEqual(self.value(), saved)
        regs = self.box.registers()
        self.assertEqual(int(regs["gp"]["eip"], 16), 0x10A)
        self.box.resume()
        wait_until(lambda: self.value() != saved, timeout=15)

    def test_load_missing(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("gibtsnicht")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("paused", self.box.status())  # no dialog: the emulator answers

    def test_load_not_a_savestate(self):
        (self.out / "broken.sav").write_bytes(b"this is not a zip")
        (self.out / "broken.json").write_text("{}", encoding="utf-8")
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("broken")
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_load_without_sidecar_needs_flag(self):
        self.box.run_until(address=self.addr(0x10A))
        info = self.box.state_save("st_without")
        Path(info["info_path"]).unlink()
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_without")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertTrue(self.box.state_load("st_without", skip_check=True)["loaded"])

    def test_sidecar_mismatch(self):
        self.box.run_until(address=self.addr(0x10A))
        info = self.box.state_save("st_wrong")
        meta = json.loads(Path(info["info_path"]).read_text(encoding="utf-8"))
        meta["memory_pages"] += 256
        Path(info["info_path"]).write_text(json.dumps(meta), encoding="utf-8")
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_wrong")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("memory", ctx.exception.message.lower())

    def test_zip_memory_size_mismatch(self):
        # Memory_Size in the archive does not match the instance: never load it (upstream would otherwise copy past MemBase),
        # not even with skip_check or with a matching (outdated) sidecar.
        self.box.run_until(address=self.addr(0x10A))
        info = self.box.state_save("st_mem")
        with zipfile.ZipFile(info["path"]) as src, zipfile.ZipFile(self.out / "st_mem2.sav", "w") as dst:
            for item in src.infolist():
                data = src.read(item)
                if item.filename == "Memory_Size":
                    data = str(int(data) + 256).encode()
                dst.writestr(item, data)
        (self.out / "st_mem2.json").write_text(Path(info["info_path"]).read_text(encoding="utf-8"), encoding="utf-8")
        for flag in (False, True):
            with self.subTest(skip_check=flag):
                with self.assertRaises(DmcpError) as ctx:
                    self.box.state_load("st_mem2", skip_check=flag)
                self.assertEqual(ctx.exception.code, "bad_argument")
                self.assertIn("memory", ctx.exception.message.lower())
        (self.out / "st_mem2.json").unlink()
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_mem2", skip_check=True)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("paused", self.box.status())

    def test_load_drops_stop_condition(self):
        self.box.run_until(address=self.addr(0x10A))
        self.box.state_save("st_until")
        self.box.call("emu.run_until", address="l:FFFFFFF0")
        self.assertEqual(self.box.status()["waiting_for"], "run_until")
        self.box.state_load("st_until")
        self.assertNotIn("waiting_for", self.box.status())

    def test_breakpoint_at_loaded_position_hits(self):
        # The old halt (breakpoint) does not belong to the loaded CS:EIP: a breakpoint there must not
        # be skipped.
        self.box.run_until(address=self.addr(0x10A))
        saved = self.value()
        self.box.state_save("st_bp")
        self.box.break_add(self.addr(0x10A))
        try:
            self.box.resume()
            st = self.box.wait_paused()
            self.assertEqual(st["stop_reason"], "breakpoint")
            self.box.state_load("st_bp")
            self.assertEqual(self.value(), saved)
            self.box.resume()
            st = self.box.wait_paused()
            self.assertEqual(st["stop_reason"], "breakpoint")
            self.assertEqual(int(st["eip"], 16), 0x10A)
            self.assertEqual(self.value(), saved)
        finally:
            self.box.break_remove(all=True)

    def test_bad_name(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_save("..\\evil")
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_corrupt_zip_with_valid_sidecar(self):
        self.box.run_until(address=self.addr(0x10A))
        info = self.box.state_save("st_real")
        (self.out / "st_garbage.sav").write_bytes(b"PK\x03\x04" + b"garbage" * 100)
        (self.out / "st_garbage.json").write_text(Path(info["info_path"]).read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_garbage")
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("paused", self.box.status())

    def test_truncated_state(self):
        self.box.run_until(address=self.addr(0x10A))
        info = self.box.state_save("st_integer")
        data = Path(info["path"]).read_bytes()
        (self.out / "st_short.sav").write_bytes(data[: len(data) // 2])
        (self.out / "st_short.json").write_text(Path(info["info_path"]).read_text(encoding="utf-8"), encoding="utf-8")
        with self.assertRaises(DmcpError) as ctx:
            self.box.state_load("st_short")
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_reserved_names(self):
        for name in ("nul", "CON.txt", "com1", "name.", "name "):
            with self.assertRaises(DmcpError, msg=name) as ctx:
                self.box.state_save(name)
            self.assertEqual(ctx.exception.code, "bad_argument")

    def test_path_not_a_string(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.call("state.load", path=5)
        self.assertEqual(ctx.exception.code, "bad_argument")


@need_build
class StateEarlyLoadTest(unittest.TestCase):
    """Loading before the DOS shell is running used to crash the emulator (acceptance D31)."""

    def test_load_before_shell_is_refused_not_crash(self):
        donor = start_idle()
        try:
            wait_prompt(donor)
            donor.pause()
            path = donor.state_save("early")["path"]
        finally:
            donor.stop()
        box = start_idle()
        try:
            box.pause()  # right after the start: shell not there yet
            with self.assertRaises(DmcpError) as ctx:
                box.state_load(path=path)
            self.assertEqual(ctx.exception.code, "not_ready")
            self.assertTrue(box.inst.alive())
            box.resume()
            wait_prompt(box)
            box.pause()
            self.assertTrue(box.state_load(path=path)["loaded"])
            box.resume()
            wait_prompt(box)
        finally:
            box.stop()

    def test_load_while_starting_waits(self):
        donor = start_idle()
        try:
            wait_prompt(donor)
            donor.pause()
            path = donor.state_save("early2")["path"]
        finally:
            donor.stop()
        box = start_idle()
        try:
            self.assertTrue(box.state_load(path=path)["loaded"])  # still running: the library waits
            self.assertTrue(box.inst.alive())
        finally:
            box.stop()


@need_build
class StateAfterBootTest(unittest.TestCase):
    """After BOOT of a guest system the DOS kernel is gone again; loading used to crash the emulator then
    (review D31). Expected: a clean rejection not_ready, emulator alive."""

    def test_load_after_boot(self):
        import shutil
        import tempfile
        from dosbox_lab import Dosbox, Mount
        from tests.util import US
        donor = start_idle()
        try:
            wait_prompt(donor)
            donor.pause()
            path = donor.state_save("before_boot")["path"]
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
                return int(regs["seg"]["cs"]["base"], 16) + int(regs["gp"]["eip"], 16) in (0x7C01, 0x7C02, 0x7C03)
            wait_until(booted, timeout=20)
            box.pause()  # paused: the library does not wait, the error comes immediately
            with self.assertRaises(DmcpError) as ctx:
                box.state_load(path=path)
            self.assertEqual(ctx.exception.code, "not_ready")
            self.assertTrue(box.inst.alive())
            self.assertTrue(box.status()["paused"])
        finally:
            box.stop()
