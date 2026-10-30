import shutil
import tempfile
import unittest

from dosbox_mcp import Dosbox, Mount
from dosbox_mcp.files import read_jsonl
from tests.util import TESTPROGS, US, class_box, need_build, run_typed, wait_until


def start_fileio(cls):
    tmp = tempfile.mkdtemp(prefix="dmcp_fio_")
    cls.addClassCleanup(shutil.rmtree, tmp, True)
    shutil.copy(TESTPROGS / "FILEIO.COM", tmp)
    box = class_box(cls, Dosbox.start(mounts=[Mount("C", tmp)], autoexec=[], headless=True, conf=US))
    run_typed(box, "FILEIO")
    module = wait_until(lambda: next((m for m in box.modules() if m["modul"] == "FILEIO.COM"), None), timeout=15)
    return box, int(module["psp"], 16)


def run_fileio(box, psp):
    box.mem_write(f"r:{psp:04X}:03F1", b"\x01")
    wait_until(lambda: box.mem_read(f"r:{psp:04X}:03F0", 1) == b"\x01", timeout=15)


@need_build
class FileIoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box, cls.psp = start_fileio(cls)
        b = cls.base = cls.psp * 16
        cls.box.events_start(typen=["datei"], name="ev_fio")
        cls.box.calls_start(bereiche=[{"von": f"l:{b + 0x280:08X}", "bis": f"l:{b + 0x28F:08X}"}], name="calls_fio")
        run_fileio(cls.box, cls.psp)
        cls.events = read_jsonl(cls.box.events_stop()["datei"])
        cls.calls = read_jsonl(cls.box.calls_stop()["datei"])

    def test_three_accesses_no_device(self):
        self.assertEqual([(e["art"], int(e["offset"], 16), int(e["laenge"], 16), int(e["ergebnis"], 16))
                          for e in self.events],
                         [("schreiben", 0, 64, 64), ("lesen", 0x10, 0x10, 0x10), ("lesen", 0x20, 8, 8)])

    def test_fields(self):
        e = self.events[1]
        self.assertEqual(e["typ"], "datei")
        self.assertTrue(e["datei"].upper().endswith("FIO.DAT"))
        self.assertEqual(e["modus"], "real")
        self.assertEqual(int(e["puffer"]["linear"], 16), self.base + 0x400)
        self.assertEqual(e["vorschau"], "".join(f"{i:02X}" for i in range(0x10, 0x20)))
        self.assertEqual(self.events[0]["vorschau"], "".join(f"{i:02X}" for i in range(16)))
        self.assertEqual(self.events[2]["vorschau"], "".join(f"{i:02X}" for i in range(0x20, 0x28)))
        self.assertTrue(e["handle"].startswith("0x"))
        self.assertNotIn("fehler", e)

    def test_call_id_link(self):
        lesen = [c for c in self.calls if int(c["ziel"]["linear"], 16) == self.base + 0x280]
        self.assertEqual(len(lesen), 1)
        self.assertEqual(self.events[1]["call_id"], lesen[0]["call_id"])
        self.assertNotIn("call_id", self.events[0])
        self.assertNotIn("call_id", self.events[2])

    def test_adr_is_int_instruction(self):
        self.assertEqual(int(self.events[1]["adr"]["linear"], 16), self.base + 0x282)


@need_build
class FileIoNoPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box, cls.psp = start_fileio(cls)
        cls.box.events_start(typen=["datei", "int"], int_filter=[{"nummer": "10"}], datei_vorschau=0,
                             name="ev_fio0")
        run_fileio(cls.box, cls.psp)
        cls.events = read_jsonl(cls.box.events_stop()["datei"])

    def test_no_preview_and_independent_of_int_filter(self):
        datei = [e for e in self.events if e["typ"] == "datei"]
        self.assertEqual(len(datei), 3)
        self.assertTrue(all("vorschau" not in e for e in datei))

    def test_bad_preview(self):
        from dosbox_mcp import DmcpError
        with self.assertRaises(DmcpError):
            self.box.events_start(datei_vorschau=300, name="ev_bad")


@need_build
class FileIoStateLoadTest(unittest.TestCase):
    """state.load drops open accesses: the read at lesen (0282) is entered, then the state from before
    the INT is loaded and IP set past it (0284), so that read never returns in the loaded state."""

    @classmethod
    def setUpClass(cls):
        cls.box, cls.psp = start_fileio(cls)
        b = cls.psp * 16
        box = cls.box
        box.events_start(typen=["datei"], name="ev_fio_state")
        box.break_add(f"l:{b + 0x282:08X}")
        box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        box.wait_paused(timeout_s=15)
        box.break_remove(all=True)
        box.state_save("fio_vor")
        box.step()  # executes the INT 21h: the read is open now
        box.state_load("fio_vor")
        box.set_register("eip", 0x284)
        box.resume()
        wait_until(lambda: box.mem_read(f"r:{cls.psp:04X}:03F0", 1) == b"\x01", timeout=15)
        cls.events = read_jsonl(box.events_stop()["datei"])

    def test_no_stale_event(self):
        # offset of the last read is left out: it depends on how the state restores host file positions
        self.assertEqual([(e["art"], int(e["laenge"], 16)) for e in self.events],
                         [("schreiben", 64), ("lesen", 8)])


def count_int16(cls, datei):
    """Runs FILEIO with a counting INT 16h hook at PSP:0480 (counter 04F0, old vector 04F2); returns
    (INT 16h calls, datei events)."""
    box, psp = start_fileio(cls)
    old = box.mem_read("l:00000058", 4)
    hook = bytes.fromhex("2EFF06F004" "2EFF2EF204")  # inc word [cs:04F0]; jmp far [cs:04F2]
    box.mem_write(f"r:{psp:04X}:0480", hook)
    box.mem_write(f"r:{psp:04X}:04F0", bytes(2) + old)
    box.events_start(typen=["datei"] if datei else ["int"], name="ev_fio_con")
    box.mem_write("l:00000058", bytes([0x80, 0x04, psp & 0xFF, psp >> 8]))
    run_fileio(box, psp)
    count = int.from_bytes(box.mem_read(f"r:{psp:04X}:04F0", 2), "little")
    box.mem_write("l:00000058", old)
    events = read_jsonl(box.events_stop()["datei"])
    box.stop()
    return count, [e for e in events if e["typ"] == "datei"]


@need_build
class FileIoNoGuestCodeTest(unittest.TestCase):
    """The device check runs no guest code (Befund B1): FILEIO's write to CON calls the guest's INT 16h
    as often with datei active as without."""

    @classmethod
    def setUpClass(cls):
        cls.ohne, _ = count_int16(cls, False)
        cls.mit, cls.events = count_int16(cls, True)

    def test_con_write_runs_no_int16(self):
        self.assertEqual(len(self.events), 3)
        self.assertGreaterEqual(self.ohne, 1)  # the counter fires at all
        self.assertEqual(self.mit, self.ohne)
