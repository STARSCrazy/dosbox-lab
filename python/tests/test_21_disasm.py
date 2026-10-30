import unittest
from pathlib import Path

from dosbox_lab import DmcpError
from dosbox_lab.files import read_jsonl
from tests.util import class_box, need_build, prog_psp, start_prog, wait_until


@need_build
class DisasmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("DEBUG.COM"))
        cls.psp = prog_psp(cls.box, "DEBUG.COM")
        cls.box.call("symbols.set", modul="DEBUG.COM", adressraum="rel16",
                     symbole=[{"name": "unter", "adr": "0000:0140"}, {"name": "schleife", "adr": "0000:0107"}])


    def adr(self, off):
        return f"r:{self.psp:04X}:{off:04X}"

    def test_known_lines(self):
        result = self.box.disassemble(self.adr(0x107), anzahl=6)
        lines = result["zeilen"]
        self.assertEqual([int(z["adr"]["off"], 16) for z in lines], [0x107, 0x10A, 0x10D, 0x10E, 0x111, 0x113])
        self.assertEqual([z["bytes"] for z in lines], ["E83600", "A18001", "40", "A38001", "B401", "CD16"])
        self.assertEqual([z["laenge"] for z in lines], [3, 3, 1, 3, 2, 2])
        self.assertEqual([z["text"].split()[0] for z in lines], ["call", "mov", "inc", "mov", "mov", "int"])
        self.assertEqual(result["naechste"], f"r:{self.psp:04X}:0115")

    def test_symbols_and_targets(self):
        lines = self.box.disassemble(self.adr(0x107), anzahl=1)["zeilen"]
        self.assertEqual(lines[0]["adr"]["symbol"], "schleife")
        self.assertEqual(int(lines[0]["ziel"]["off"], 16), 0x140)
        self.assertEqual(lines[0]["ziel"]["symbol"], "unter")
        jz = self.box.disassemble(self.adr(0x115), anzahl=1)["zeilen"][0]
        self.assertEqual(jz["text"].split()[0], "je")
        self.assertEqual(int(jz["ziel"]["off"], 16), 0x107)
        self.assertNotIn("ziel", self.box.disassemble(self.adr(0x10A), anzahl=1)["zeilen"][0])

    def test_default_is_current_instruction(self):
        st = self.box.pause()
        try:
            line = self.box.disassemble(anzahl=1)["zeilen"][0]
            self.assertEqual(int(line["adr"]["off"], 16), int(st["eip"], 16))
        finally:
            self.box.resume()

    def test_file_output(self):
        result = self.box.disassemble(self.adr(0x100), anzahl=50, name="dis_debug")
        self.assertEqual(result["anzahl"], 50)
        lines = read_jsonl(result["pfad"])
        self.assertEqual(len(lines), 50)
        self.assertEqual(lines[0]["adr"]["linear"], f"0x{self.psp * 16 + 0x100:08X}")
        self.assertTrue(Path(result["pfad"]).name.startswith("dis_debug"))

    def test_bad_arguments(self):
        for kwargs in ({"anzahl": 0}, {"anzahl": 201}, {"adresse": "p:1000"}, {"bits": 8}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(DmcpError) as ctx:
                    self.box.disassemble(**kwargs)
                self.assertEqual(ctx.exception.code, "bad_argument")


@need_build
class Disasm32Test(unittest.TestCase):
    """PMODE.COM: Endlosschleife im 32-Bit-Teil (CS=0008): inc dword [01000004] (FF0504000001), jmp short zurueck."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_prog("PMODE.COM"))
        wait_until(lambda: cls.box.registers()["paging"], timeout=15)
        cls.box.flow_start(ring_groesse=1000)
        cls.jmp = wait_until(lambda: next((e for e in read_jsonl(cls.box.flow_recent(50, "dis32_flow")["pfad"])
                                           if e["art"] == "jmp"), None), timeout=15)
        cls.box.pause()

    def test_32_bit_instructions(self):
        loop = self.jmp["ziel"]["off"][2:]  # Schleifenanfang laut Flow-Aufzeichnung ("0x...")
        inc, jmp = self.box.disassemble(f"0008:{loop}", anzahl=2)["zeilen"]
        self.assertEqual(inc["adr"]["bits"], 32)
        self.assertEqual(jmp["adr"]["bits"], 32)
        self.assertEqual(inc["bytes"], "FF0504000001")
        self.assertEqual(inc["laenge"], 6)
        self.assertEqual(inc["text"].split()[0], "inc")
        self.assertIn("01000004", inc["text"].upper())
        self.assertEqual(jmp["text"].split()[0], "jmp")
        self.assertEqual(jmp["ziel"]["linear"], inc["adr"]["linear"])  # Sprung zurueck an den Schleifenanfang
        self.assertEqual(jmp["ziel"]["bits"], 32)

    def test_default_bits_follow_code_segment(self):
        self.assertEqual(self.box.disassemble(anzahl=1)["zeilen"][0]["adr"]["bits"], 32)
