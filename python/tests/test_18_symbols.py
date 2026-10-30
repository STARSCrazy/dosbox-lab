import hashlib
import json
import unittest

from dosbox_mcp import DmcpError
from tests.util import TESTPROGS, class_box, need_build, run_typed, start_idle, wait_until

FLOW_SYMBOLS = {
    "format": "decomp-symbole/1", "modul": "FLOW.COM",
    "sha256": hashlib.sha256((TESTPROGS / "FLOW.COM").read_bytes()).hexdigest(),
    "adressraum": "rel16",
    "symbole": [
        {"name": "ziel_a", "adr": "0000:0160", "art": "funktion", "groesse": 1},
        {"name": "tabelle", "adr": "0000:0180", "art": "variable", "groesse": 4}],
}


def write(box, name, data):
    path = box.inst.rundir / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@need_build
class SymbolsRel16Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "FLOW")
        module = wait_until(lambda: next((m for m in cls.box.modules() if m["modul"] == "FLOW.COM"), None),
                            timeout=15)
        cls.psp = int(module["psp"], 16)
        cls.symfile = write(cls.box, "flow_symbole.json", FLOW_SYMBOLS)
        cls.loaded = cls.box.symbols_load(cls.symfile)


    def test_loaded_without_warnings(self):
        self.assertEqual(self.loaded["anzahl"], 2)
        self.assertEqual(self.loaded["hinweise"], [])

    def test_translate_by_name(self):
        addr = self.box.translate("ziel_a")
        self.assertEqual(addr["linear"], f"{self.psp * 16 + 0x160:08X}")
        self.assertEqual(addr["symbol"], "ziel_a")
        self.assertEqual(addr["rel"], "0000:0160")
        self.assertEqual(self.box.translate("FLOW.COM!ziel_a")["linear"], addr["linear"])

    def test_offset_inside_symbol(self):
        self.assertEqual(self.box.translate("rel:0000:0182")["symbol"], "tabelle+0x2")
        self.assertNotIn("symbol", self.box.translate("rel:0000:0184"))

    def test_read_by_name_and_lookup(self):
        self.assertEqual(self.box.mem_read("tabelle", 2), (0x160).to_bytes(2, "little"))
        found = self.box.symbol_lookup(adresse=f"r:{self.psp:04X}:0160")
        self.assertEqual(found["adresse"]["symbol"], "ziel_a")

    def test_reload_replaces_and_sha_warning(self):
        try:
            other = dict(FLOW_SYMBOLS, sha256="00" * 32, symbole=[{"name": "neu", "adr": "0000:0100", "art": "label"}])
            result = self.box.symbols_load(write(self.box, "flow_neu.json", other))
            self.assertTrue(any("sha256 passt nicht" in h for h in result["hinweise"]))
            with self.assertRaises(DmcpError) as ctx:
                self.box.translate("ziel_a")
            self.assertEqual(ctx.exception.code, "bad_argument")
            self.assertEqual(self.box.translate("neu")["linear"], f"{self.psp * 16 + 0x100:08X}")
        finally:
            self.box.symbols_load(self.symfile)

    def test_label_without_size_does_not_hide_function(self):
        try:
            data = dict(FLOW_SYMBOLS, symbole=[
                {"name": "func", "adr": "0000:0100", "art": "funktion", "groesse": 0x40},
                {"name": "lab", "adr": "0000:0110", "art": "label"}])
            self.box.symbols_load(write(self.box, "flow_label.json", data))
            self.assertEqual(self.box.translate("rel:0000:0120")["symbol"], "func+0x20")
            self.assertEqual(self.box.translate("rel:0000:0110")["symbol"], "lab")
            self.assertNotIn("symbol", self.box.translate("rel:0000:0140"))
        finally:
            self.box.symbols_load(self.symfile)

    def test_bad_files_and_names(self):
        for data in ({"format": "anders"}, dict(FLOW_SYMBOLS, adressraum="flach"),
                     dict(FLOW_SYMBOLS, symbole=[{"name": "x", "adr": "zz"}])):
            with self.assertRaises(DmcpError, msg=data) as ctx:
                self.box.symbols_load(write(self.box, "kaputt.json", data))
            self.assertEqual(ctx.exception.code, "bad_argument")
        with self.assertRaises(DmcpError) as ctx:
            self.box.translate("gibtsnicht")
        self.assertEqual(ctx.exception.code, "bad_argument")


@need_build
class SymbolsVa32Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "PMODE")
        wait_until(lambda: cls.box.registers()["paging"], timeout=15)
        wait_until(lambda: cls.box.mem_read("l:01000000", 4) == b"DMCP")


    def symbols(self, **extra):
        data = {"format": "decomp-symbole/1", "modul": "PMODE.COM", "adressraum": "va32",
                "symbole": [{"name": "marke", "adr": "0x0", "art": "variable", "groesse": 4},
                            {"name": "zaehler", "adr": "0x4", "art": "variable", "groesse": 4}]}
        data.update(extra)
        return write(self.box, "pmode_symbole.json", data)

    def test_basis_linear(self):
        result = self.box.symbols_load(self.symbols(basis_linear="0x01000000"))
        self.assertEqual(result["abbildung"], "symbole_basis")
        addr = self.box.translate("zaehler")
        self.assertEqual(addr["linear"], "01000004")
        self.assertEqual(addr["va"], "00000004")
        self.assertEqual(addr["symbol"], "zaehler")
        self.assertEqual(self.box.mem_read("marke", 4), b"DMCP")

    def test_segments_win_over_basis(self):
        result = self.box.symbols_load(self.symbols(
            basis_linear="0x02000000",
            segmente=[{"name": "daten", "va_start": "0x0", "va_ende": "0xFFF", "basis_linear": "0x01000000"}]))
        self.assertEqual(result["abbildung"], "symbole_segmente")
        self.assertEqual(self.box.translate("zaehler")["linear"], "01000004")

    def test_segments_without_own_basis_use_file_basis(self):
        result = self.box.symbols_load(self.symbols(
            basis_linear="0x01000000", segmente=[{"name": "daten", "va_start": "0x0", "va_ende": "0xFFF"}]))
        self.assertEqual(result["abbildung"], "symbole_segmente")
        self.assertEqual(self.box.translate("zaehler")["linear"], "01000004")

    def test_unreadable_basis_linear(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.symbols_load(self.symbols(basis_linear="zz"))
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("basis_linear", str(ctx.exception))
        with self.assertRaises(DmcpError) as ctx:
            self.box.symbols_load(self.symbols(
                segmente=[{"name": "daten", "va_start": "0x0", "va_ende": "0xFFF", "basis_linear": "zz"}]))
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("basis_linear", str(ctx.exception))
        with self.assertRaises(DmcpError) as ctx:
            self.box.symbols_load(self.symbols(
                basis_linear="zz", segmente=[{"name": "daten", "va_start": "0x0", "va_ende": "0xFFF"}]))
        self.assertIn("basis_linear", str(ctx.exception))

    def test_load_parameter_as_mapping(self):
        self.box.clear_modules("PMODE.COM")
        result = self.box.symbols_load(self.symbols(), basis_linear=0x01000000)
        self.assertEqual(result["abbildung"], "gesetzt")
        self.assertEqual(self.box.translate("marke")["linear"], "01000000")


if __name__ == "__main__":
    unittest.main()
