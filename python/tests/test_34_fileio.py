import shutil
import tempfile
import unittest

from dosbox_lab import Dosbox, Mount
from dosbox_lab.files import read_jsonl
from tests.util import TESTPROGS, US, class_box, need_build, run_typed, wait_until


def start_fileio(cls):
    tmp = tempfile.mkdtemp(prefix="dmcp_fio_")
    cls.addClassCleanup(shutil.rmtree, tmp, True)
    shutil.copy(TESTPROGS / "FILEIO.COM", tmp)
    box = class_box(cls, Dosbox.start(mounts=[Mount("C", tmp)], autoexec=[], headless=True, conf=US))
    run_typed(box, "FILEIO")
    module = wait_until(lambda: next((m for m in box.modules() if m["module"] == "FILEIO.COM"), None), timeout=15)
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
        cls.box.events_start(types=["file"], name="ev_fio")
        cls.box.calls_start(ranges=[{"from": f"l:{b + 0x280:08X}", "to": f"l:{b + 0x28F:08X}"}], name="calls_fio")
        run_fileio(cls.box, cls.psp)
        cls.events = read_jsonl(cls.box.events_stop()["file"])
        cls.calls = read_jsonl(cls.box.calls_stop()["file"])

    def test_three_accesses_no_device(self):
        self.assertEqual([(e["kind"], int(e["offset"], 16), int(e["length"], 16), int(e["result"], 16))
                          for e in self.events],
                         [("write", 0, 64, 64), ("read", 0x10, 0x10, 0x10), ("read", 0x20, 8, 8)])

    def test_fields(self):
        e = self.events[1]
        self.assertEqual(e["type"], "file")
        self.assertTrue(e["file"].upper().endswith("FIO.DAT"))
        self.assertEqual(e["mode"], "real")
        self.assertEqual(int(e["buffer"]["linear"], 16), self.base + 0x400)
        self.assertEqual(e["preview"], "".join(f"{i:02X}" for i in range(0x10, 0x20)))
        self.assertEqual(self.events[0]["preview"], "".join(f"{i:02X}" for i in range(16)))
        self.assertEqual(self.events[2]["preview"], "".join(f"{i:02X}" for i in range(0x20, 0x28)))
        self.assertTrue(e["handle"].startswith("0x"))
        self.assertNotIn("error", e)

    def test_call_id_link(self):
        read = [c for c in self.calls if int(c["target"]["linear"], 16) == self.base + 0x280]
        self.assertEqual(len(read), 1)
        self.assertEqual(self.events[1]["call_id"], read[0]["call_id"])
        self.assertNotIn("call_id", self.events[0])
        self.assertNotIn("call_id", self.events[2])

    def test_addr_is_int_instruction(self):
        self.assertEqual(int(self.events[1]["addr"]["linear"], 16), self.base + 0x282)


@need_build
class FileIoNoPreviewTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box, cls.psp = start_fileio(cls)
        cls.box.events_start(types=["file", "int"], int_filter=[{"number": "10"}], file_preview=0,
                             name="ev_fio0")
        run_fileio(cls.box, cls.psp)
        cls.events = read_jsonl(cls.box.events_stop()["file"])

    def test_no_preview_and_independent_of_int_filter(self):
        file_events = [e for e in self.events if e["type"] == "file"]
        self.assertEqual(len(file_events), 3)
        self.assertTrue(all("preview" not in e for e in file_events))

    def test_bad_preview(self):
        from dosbox_lab import DmcpError
        with self.assertRaises(DmcpError):
            self.box.events_start(file_preview=300, name="ev_bad")


@need_build
class FileIoStateLoadTest(unittest.TestCase):
    """state.load drops open accesses: the read at 0282 is entered, then the state from before the INT is
    loaded and IP is set past it (0284), so that read never returns in the loaded state."""

    @classmethod
    def setUpClass(cls):
        cls.box, cls.psp = start_fileio(cls)
        b = cls.psp * 16
        box = cls.box
        box.events_start(types=["file"], name="ev_fio_state")
        box.break_add(f"l:{b + 0x282:08X}")
        box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        box.wait_paused(timeout_s=15)
        box.break_remove(all=True)
        box.state_save("fio_before")
        box.step()  # executes the INT 21h: the read is open now
        box.state_load("fio_before")
        box.set_register("eip", 0x284)
        box.resume()
        wait_until(lambda: box.mem_read(f"r:{cls.psp:04X}:03F0", 1) == b"\x01", timeout=15)
        cls.events = read_jsonl(box.events_stop()["file"])

    def test_no_stale_event(self):
        # offset of the last read is left out: it depends on how the state restores host file positions
        self.assertEqual([(e["kind"], int(e["length"], 16)) for e in self.events],
                         [("write", 64), ("read", 8)])


def count_int16(cls, with_file_events):
    """Runs FILEIO with a counting INT 16h hook at PSP:0480 (counter 04F0, old vector 04F2); returns
    (INT 16h calls, file events)."""
    box, psp = start_fileio(cls)
    old = box.mem_read("l:00000058", 4)
    hook = bytes.fromhex("2EFF06F004" "2EFF2EF204")  # inc word [cs:04F0]; jmp far [cs:04F2]
    box.mem_write(f"r:{psp:04X}:0480", hook)
    box.mem_write(f"r:{psp:04X}:04F0", bytes(2) + old)
    box.events_start(types=["file"] if with_file_events else ["int"], name="ev_fio_con")
    box.mem_write("l:00000058", bytes([0x80, 0x04, psp & 0xFF, psp >> 8]))
    run_fileio(box, psp)
    count = int.from_bytes(box.mem_read(f"r:{psp:04X}:04F0", 2), "little")
    box.mem_write("l:00000058", old)
    events = read_jsonl(box.events_stop()["file"])
    box.stop()
    return count, [e for e in events if e["type"] == "file"]


@need_build
class FileIoNoGuestCodeTest(unittest.TestCase):
    """The device check runs no guest code (finding B1): FILEIO's write to CON calls the guest's INT 16h
    as often with file events active as without."""

    @classmethod
    def setUpClass(cls):
        cls.count_without, _ = count_int16(cls, False)
        cls.count_with, cls.events = count_int16(cls, True)

    def test_con_write_runs_no_int16(self):
        self.assertEqual(len(self.events), 3)
        self.assertGreaterEqual(self.count_without, 1)  # the counter fires at all
        self.assertEqual(self.count_with, self.count_without)
