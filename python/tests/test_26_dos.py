import unittest

from tests.util import class_box, need_build, prog_psp, run_typed, start_idle, start_prog, wait_prompt


@need_build
class DosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")


    def test_target(self):
        t = self.box.dos_target()
        self.assertTrue(t["found"])
        self.assertEqual(t["module"], "DEBUG.COM")
        self.assertEqual(t["kind"], "com")
        self.assertEqual(int(t["psp"], 16), self.psp)
        self.assertEqual(t["entry"], f"{self.psp:04X}:0100")
        self.assertEqual(t["relocation"], "0000")

    def test_programs_chain(self):
        progs = self.box.dos_programs()["programs"]
        self.assertEqual(int(progs[0]["psp"], 16), self.psp)
        self.assertTrue(progs[0]["current"])
        self.assertEqual(progs[0]["name"], "DEBUG")
        self.assertEqual(progs[0]["module"], "DEBUG.COM")
        self.assertGreaterEqual(len(progs), 2)
        self.assertEqual(progs[0]["parent"], progs[1]["psp"])

    def test_mcb_chain(self):
        blocks = self.box.dos_mcb()["blocks"]
        self.assertEqual(blocks[-1]["type"], "Z")
        for a, b in zip(blocks, blocks[1:]):
            self.assertEqual(int(b["seg"], 16), int(a["seg"], 16) + a["paragraphs"] + 1)
        own = [b for b in blocks if int(b["owner"], 16) == self.psp and b["program"]]
        self.assertEqual(len(own), 1)
        self.assertEqual(int(own[0]["seg"], 16), self.psp - 1)
        self.assertEqual(own[0]["name"], "DEBUG")

    def test_standard_handles(self):
        files = self.box.dos_files()
        handles = {h["handle"]: h["sft"] for h in files["handles"]}
        for h in (0, 1, 2):
            self.assertIn(h, handles)
        names = {f["sft"]: f["name"] for f in files["files"]}
        self.assertIn("CON", names[handles[1]].upper())


@need_build
class DosOpenFileTest(unittest.TestCase):
    """INT21.COM opens itself (3Dh) and closes it right away (3Eh): halt before the close."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)


    def test_open_file_visible(self):
        self.box.break_int_add(0x21, ah=0x3E)
        run_typed(self.box, "INT21")
        self.box.wait_paused(timeout_s=15)
        handle = int(self.box.registers()["gp"]["ebx"], 16) & 0xFFFF
        files = self.box.dos_files()
        sft = {h["handle"]: h["sft"] for h in files["handles"]}[handle]
        name = next(f["name"] for f in files["files"] if f["sft"] == sft)
        self.assertIn("INT21.COM", name.upper())
        self.box.break_int_remove(all=True)
        self.box.resume()


@need_build
class DosMcbCorruptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)


    def test_corrupt_chain(self):
        blocks = self.box.dos_mcb()["blocks"]
        self.assertEqual(self.box.dos_mcb()["end"], "Z")
        seg = int(blocks[0]["seg"], 16)
        self.box.pause()
        try:
            self.box.mem_write(f"l:{seg << 4:X}", b"X")
            self.assertEqual(self.box.dos_mcb()["end"], "broken")
        finally:
            self.box.mem_write(f"l:{seg << 4:X}", blocks[0]["type"].encode())
            self.box.resume()
        self.assertEqual(self.box.dos_mcb()["end"], "Z")
