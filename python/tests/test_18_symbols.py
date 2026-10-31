import hashlib
import json
import unittest

from dosbox_lab import DmcpError
from tests.util import TESTPROGS, class_box, need_build, run_typed, start_idle, wait_until

FLOW_SYMBOLS = {
    "format": "decomp-symbols/1", "module": "FLOW.COM",
    "sha256": hashlib.sha256((TESTPROGS / "FLOW.COM").read_bytes()).hexdigest(),
    "address_space": "rel16",
    "symbols": [
        {"name": "target_a", "addr": "0000:0160", "kind": "function", "size": 1},
        {"name": "table", "addr": "0000:0180", "kind": "variable", "size": 4}],
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
        module = wait_until(lambda: next((m for m in cls.box.modules() if m["module"] == "FLOW.COM"), None),
                            timeout=15)
        cls.psp = int(module["psp"], 16)
        cls.symfile = write(cls.box, "flow_symbols.json", FLOW_SYMBOLS)
        cls.loaded = cls.box.symbols_load(cls.symfile)


    def test_loaded_without_warnings(self):
        self.assertEqual(self.loaded["count"], 2)
        self.assertEqual(self.loaded["hints"], [])

    def test_translate_by_name(self):
        addr = self.box.translate("target_a")
        self.assertEqual(addr["linear"], f"{self.psp * 16 + 0x160:08X}")
        self.assertEqual(addr["symbol"], "target_a")
        self.assertEqual(addr["rel"], "0000:0160")
        self.assertEqual(self.box.translate("FLOW.COM!target_a")["linear"], addr["linear"])

    def test_offset_inside_symbol(self):
        self.assertEqual(self.box.translate("rel:0000:0182")["symbol"], "table+0x2")
        self.assertNotIn("symbol", self.box.translate("rel:0000:0184"))

    def test_read_by_name_and_lookup(self):
        self.assertEqual(self.box.mem_read("table", 2), (0x160).to_bytes(2, "little"))
        found = self.box.symbol_lookup(address=f"r:{self.psp:04X}:0160")
        self.assertEqual(found["address"]["symbol"], "target_a")

    def test_reload_replaces_and_sha_warning(self):
        try:
            other = dict(FLOW_SYMBOLS, sha256="00" * 32, symbols=[{"name": "new", "addr": "0000:0100", "kind": "label"}])
            result = self.box.symbols_load(write(self.box, "flow_new.json", other))
            self.assertTrue(any("sha256 does not match" in h for h in result["hints"]))
            with self.assertRaises(DmcpError) as ctx:
                self.box.translate("target_a")
            self.assertEqual(ctx.exception.code, "bad_argument")
            self.assertEqual(self.box.translate("new")["linear"], f"{self.psp * 16 + 0x100:08X}")
        finally:
            self.box.symbols_load(self.symfile)

    def test_label_without_size_does_not_hide_function(self):
        try:
            data = dict(FLOW_SYMBOLS, symbols=[
                {"name": "func", "addr": "0000:0100", "kind": "function", "size": 0x40},
                {"name": "lab", "addr": "0000:0110", "kind": "label"}])
            self.box.symbols_load(write(self.box, "flow_label.json", data))
            self.assertEqual(self.box.translate("rel:0000:0120")["symbol"], "func+0x20")
            self.assertEqual(self.box.translate("rel:0000:0110")["symbol"], "lab")
            self.assertNotIn("symbol", self.box.translate("rel:0000:0140"))
        finally:
            self.box.symbols_load(self.symfile)

    def test_bad_files_and_names(self):
        for data in ({"format": "other"}, dict(FLOW_SYMBOLS, address_space="flat"),
                     dict(FLOW_SYMBOLS, symbols=[{"name": "x", "addr": "zz"}])):
            with self.assertRaises(DmcpError, msg=data) as ctx:
                self.box.symbols_load(write(self.box, "broken.json", data))
            self.assertEqual(ctx.exception.code, "bad_argument")
        with self.assertRaises(DmcpError) as ctx:
            self.box.translate("doesnotexist")
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
        data = {"format": "decomp-symbols/1", "module": "PMODE.COM", "address_space": "va32",
                "symbols": [{"name": "marker", "addr": "0x0", "kind": "variable", "size": 4},
                            {"name": "counter", "addr": "0x4", "kind": "variable", "size": 4}]}
        data.update(extra)
        return write(self.box, "pmode_symbols.json", data)

    def test_base_linear(self):
        result = self.box.symbols_load(self.symbols(base_linear="0x01000000"))
        self.assertEqual(result["mapping"], "symbols_base")
        addr = self.box.translate("counter")
        self.assertEqual(addr["linear"], "01000004")
        self.assertEqual(addr["va"], "00000004")
        self.assertEqual(addr["symbol"], "counter")
        self.assertEqual(self.box.mem_read("marker", 4), b"DMCP")

    def test_segments_win_over_base(self):
        result = self.box.symbols_load(self.symbols(
            base_linear="0x02000000",
            segments=[{"name": "data", "va_start": "0x0", "va_end": "0xFFF", "base_linear": "0x01000000"}]))
        self.assertEqual(result["mapping"], "symbols_segments")
        self.assertEqual(self.box.translate("counter")["linear"], "01000004")

    def test_segments_without_own_base_use_file_base(self):
        result = self.box.symbols_load(self.symbols(
            base_linear="0x01000000", segments=[{"name": "data", "va_start": "0x0", "va_end": "0xFFF"}]))
        self.assertEqual(result["mapping"], "symbols_segments")
        self.assertEqual(self.box.translate("counter")["linear"], "01000004")

    def test_unreadable_base_linear(self):
        with self.assertRaises(DmcpError) as ctx:
            self.box.symbols_load(self.symbols(base_linear="zz"))
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("base_linear", str(ctx.exception))
        with self.assertRaises(DmcpError) as ctx:
            self.box.symbols_load(self.symbols(
                segments=[{"name": "data", "va_start": "0x0", "va_end": "0xFFF", "base_linear": "zz"}]))
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("base_linear", str(ctx.exception))
        with self.assertRaises(DmcpError) as ctx:
            self.box.symbols_load(self.symbols(
                base_linear="zz", segments=[{"name": "data", "va_start": "0x0", "va_end": "0xFFF"}]))
        self.assertIn("base_linear", str(ctx.exception))

    def test_load_parameter_as_mapping(self):
        self.box.clear_modules("PMODE.COM")
        result = self.box.symbols_load(self.symbols(), base_linear=0x01000000)
        self.assertEqual(result["mapping"], "set")
        self.assertEqual(self.box.translate("marker")["linear"], "01000000")


if __name__ == "__main__":
    unittest.main()
