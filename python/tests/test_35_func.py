import json
import unittest
from pathlib import Path

from dosbox_mcp import DmcpError, Dosbox
from dosbox_mcp.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, wait_until


def func_psp(box):
    module = wait_until(lambda: next((m for m in box.modules() if m["modul"] == "FUNC.COM"), None), timeout=15)
    return int(module["psp"], 16)


def record_func(box, zustaende=2, name="fcalls"):
    """Startet FUNC.COM, zeichnet alle Aufrufe in 0200-02FF auf (mit Speicherstaenden) und gibt
    (psp, base, eintraege, calls_stop-Antwort) zurueck. Danach steht FUNC.COM in seiner HLT-Schleife."""
    run_typed(box, "FUNC")
    psp = func_psp(box)
    base = psp * 16
    box.calls_start(bereiche=[{"von": f"l:{base + 0x200:08X}", "bis": f"l:{base + 0x2FF:08X}"}],
                    zustaende=zustaende, name=name)
    box.mem_write(f"r:{psp:04X}:03F1", b"\x01")
    wait_until(lambda: box.mem_read(f"r:{psp:04X}:03F0", 1) == b"\x01", timeout=30)
    stop = box.calls_stop()
    return psp, base, read_jsonl(stop["datei"]), stop


def by_target(entries, base):
    out = {}
    for e in entries:
        out.setdefault(int(e["ziel"]["linear"], 16) - base, []).append(e)
    return out


@need_build
class FuncZustandTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.psp, cls.base, cls.entries, cls.stop = record_func(cls.box)
        cls.t = by_target(cls.entries, cls.base)

    def test_zustand_felder(self):
        summe = self.t[0x200]
        self.assertEqual(len(summe), 3)  # main 1x, ruft 2x
        mit = [e for e in summe if "zustand" in e]
        self.assertEqual(len(mit), 2)  # zustaende=2 je Ziel
        self.assertNotIn("zustand", sorted(summe, key=lambda e: e["call_id"])[2])
        for e in mit:
            sav = Path(e["zustand"])
            self.assertTrue(sav.exists())
            self.assertEqual(sav.name, f"fcalls_{e['call_id']}.sav")
            info = json.loads(sav.with_suffix(".json").read_text())
            self.assertEqual(info["format"], "dosbox-mcp-state/1")
            a = info["aufruf"]
            self.assertEqual(a["call_id"], e["call_id"])
            self.assertEqual(a["art"], "near")
            self.assertEqual(a["bits"], 16)
            self.assertEqual(a["calls_datei"], "fcalls.jsonl")
            self.assertEqual(a["modus"], "real")
            self.assertEqual(a["ziel"]["linear"], e["ziel"]["linear"])
            ret = int(e["von"]["off"], 16) + 3  # call rel16 = 3 Byte
            self.assertEqual(int(a["ruecksprung"]["off"], 16), ret)
            self.assertEqual(int(a["ruecksprung"]["sel"], 16), int(e["von"]["seg"], 16))
            sp = int(e["regs_ein"]["esp"], 16) & 0xFFFF
            self.assertEqual(int(a["esp_ein"], 16), sp)
            self.assertEqual(int(a["esp_nach"], 16), sp + 2)
            self.assertEqual(int(a["ss"], 16), int(e["regs_ein"]["ss"], 16))
        meta = json.loads(Path(self.stop["json_pfad"]).read_text())
        self.assertEqual(meta["zustaende"], 2)
        self.assertEqual(meta["max_zustaende"], 1000)
        self.assertEqual(meta["zustaende_geschrieben"], 7)  # summe 2, ruft 1, mitint 1, tick 1, kind 1, hma 1
        self.assertEqual(meta["zustaende_weggelassen"], 0)

    def test_zustand_laedt_genau_am_ziel(self):
        e = self.t[0x220][0]
        self.box.pause()
        r = self.box.state_load(pfad=e["zustand"])
        self.assertTrue(r["geladen"])
        regs = self.box.registers()
        cs_basis = int(regs["seg"]["cs"]["basis"], 16)
        self.assertEqual(cs_basis + int(regs["gp"]["eip"], 16), int(e["ziel"]["linear"], 16))
        for name in ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp"):
            self.assertEqual(int(regs["gp"][name], 16), int(e["regs_ein"][name], 16), name)

    def test_programm_laeuft_nach_laden_weiter(self):
        e = self.t[0x200][0]
        self.box.pause()
        self.box.state_load(pfad=e["zustand"])
        self.box.mem_write(f"r:{self.psp:04X}:03F0", b"\x00")
        self.box.resume()
        wait_until(lambda: self.box.mem_read(f"r:{self.psp:04X}:03F0", 1) == b"\x01", timeout=30)
        self.assertEqual(self.box.mem_read(f"r:{self.psp:04X}:0300", 2), b"\x05\x00")

    def test_bad_zustaende(self):
        for kw in ({"zustaende": -1}, {"zustaende": 1, "max_zustaende": 0}):
            with self.assertRaises(DmcpError) as ctx:
                self.box.calls_start(ziele=["l:1000"], **kw)
            self.assertEqual(ctx.exception.code, "bad_argument")


@need_build
class FuncRunTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.psp, cls.base, cls.entries, _ = record_func(cls.box)
        cls.t = by_target(cls.entries, cls.base)
        cls.summe = sorted(cls.t[0x200], key=lambda e: e["call_id"])[0]   # main: ax=3, bx=4
        cls.ruft = cls.t[0x220][0]

    def low16(self, regs, name):
        return int(regs[name], 16) & 0xFFFF

    def test_ret_wie_aufzeichnung(self):
        for e in (self.summe, self.ruft, self.t[0x240][0]):
            r = self.box.func_run(e["zustand"])
            self.assertEqual(r["ende"], "ret")
            self.assertEqual(r["call_id"], e["call_id"])
            for name in ("eax", "ebx", "ecx", "edx", "esp", "ebp"):
                self.assertEqual(r["regs_aus"][name], e["regs_aus"][name], name)
            self.assertEqual(r["regs_ein"]["eax"], e["regs_ein"]["eax"])
            self.assertGreater(r["befehle"], 0)
            self.assertFalse(r["irqs"])
            self.assertTrue(Path(r["pfad"]).exists())

    def test_zweimal_gleich(self):
        a = self.box.func_run(self.ruft["zustand"])
        b = self.box.func_run(self.ruft["zustand"])
        self.assertEqual(a["regs_aus"], b["regs_aus"])
        self.assertEqual(a["befehle"], b["befehle"])

    def test_schreiben(self):
        adr = f"l:{self.base + 0x200:08X}"
        r = self.box.func_run(self.summe["zustand"], schreiben=[{"adresse": adr, "hex": "B82A00C3"}])  # mov ax,2Ah; ret
        self.assertEqual(r["ende"], "ret")
        self.assertEqual(self.low16(r["regs_aus"], "eax"), 0x2A)
        self.assertEqual(len(r["geschrieben"]), 1)
        self.assertEqual(r["geschrieben"][0]["laenge"], 4)
        self.assertEqual(int(r["geschrieben"][0]["adresse"]["linear"], 16), self.base + 0x200)

    def test_regs(self):
        r = self.box.func_run(self.summe["zustand"], regs={"eax": "0x10", "ebx": "0x20"})
        self.assertEqual(self.low16(r["regs_ein"], "eax"), 0x10)
        self.assertEqual(self.low16(r["regs_aus"], "eax"), 0x30)

    def test_stack(self):
        e = self.summe
        stack = e["stack"][:4] + "3412"   # Ruecksprungadresse behalten, naechstes Wort = 1234h
        r = self.box.func_run(e["zustand"], stack=stack)
        self.assertEqual(r["ende"], "ret")
        ss = int(e["regs_ein"]["ss"], 16)
        sp = int(e["regs_ein"]["esp"], 16) & 0xFFFF
        self.assertEqual(self.box.mem_read(f"r:{ss:04X}:{sp + 2:04X}", 2), b"\x34\x12")

    def test_abgebrochen(self):
        adr = f"l:{self.base + 0x200:08X}"
        r = self.box.func_run(self.summe["zustand"], schreiben=[{"adresse": adr, "hex": "EBFE"}], max_befehle=100000)
        self.assertEqual(r["ende"], "abgebrochen")
        self.assertEqual(r["befehle"], 100000)

    def test_bad(self):
        info = self.box.state_save("ohne_aufruf")
        for kw in ({"zustand": info["pfad"]},
                   {"zustand": self.summe["zustand"], "schreiben": [{"adresse": "l:1000", "hex": "ABC"}]},
                   {"zustand": self.summe["zustand"], "regs": {"cs": "0x10"}},
                   {"zustand": self.summe["zustand"], "max_befehle": 0}):
            with self.assertRaises(DmcpError) as ctx:
                self.box.func_run(**kw)
            self.assertEqual(ctx.exception.code, "bad_argument", kw)


FUNC_SYMBOLS = {"format": "decomp-symbole/1", "modul": "FUNC.COM", "adressraum": "rel16",
                "symbole": [{"name": "ergebnis", "adr": "0000:0300", "art": "variable", "groesse": 2},
                            {"name": "zaehler", "adr": "0000:0302", "art": "variable", "groesse": 2}]}


@need_build
class FuncGeaendertTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.psp, cls.base, cls.entries, _ = record_func(cls.box)
        t = by_target(cls.entries, cls.base)
        cls.summe = sorted(t[0x200], key=lambda e: e["call_id"])[0]
        cls.ruft = t[0x220][0]
        cls.hma = t[0x2E0][0]
        path = cls.box.inst.rundir / "func_sym.json"
        path.write_text(json.dumps(FUNC_SYMBOLS), encoding="utf-8")
        cls.box.symbols_load(str(path))

    def test_geaendert_nur_global(self):
        r = self.box.func_run(self.summe["zustand"])
        self.assertEqual(r["ende"], "ret")
        self.assertFalse(r["geaendert_gekuerzt"])
        self.assertEqual(len(r["geaendert"]), 1, r["geaendert"])
        g = r["geaendert"][0]
        self.assertEqual(int(g["adresse"]["linear"], 16), self.base + 0x300)
        self.assertEqual(g["laenge"], 1)              # nur das Low-Byte aendert sich (00 -> 07)
        self.assertEqual(g["vorher"], "00")
        self.assertEqual(g["nachher"], "07")
        self.assertEqual(g["adresse"]["symbol"], "ergebnis")
        self.assertEqual(g["symbol_offset"], "0x0")
        self.assertEqual(g["adresse"]["modul"], "FUNC.COM")
        self.assertIn("phys", g["adresse"])

    def test_luecken_werden_ueberbrueckt(self):
        r = self.box.func_run(self.ruft["zustand"])  # zaehler 0->1 (0302), ergebnis 07->05 (0300)
        self.assertEqual(len(r["geaendert"]), 1, r["geaendert"])
        g = r["geaendert"][0]
        self.assertEqual(int(g["adresse"]["linear"], 16), self.base + 0x300)
        self.assertEqual(g["laenge"], 3)
        self.assertEqual(g["vorher"], "070000")
        self.assertEqual(g["nachher"], "050001")

    def test_hma_in_segmentform(self):
        r = self.box.func_run(self.hma["zustand"])  # schreibt BEEFh nach FFFF:FF00
        self.assertEqual(r["ende"], "ret")
        self.assertEqual(len(r["geaendert"]), 1, r["geaendert"])
        a = r["geaendert"][0]["adresse"]
        self.assertEqual(int(a["linear"], 16), 0x10FEF0)
        self.assertEqual(int(a["seg"], 16), 0xFFFF)
        self.assertEqual(int(a["off"], 16), 0xFF00)
        self.assertEqual(r["geaendert"][0]["nachher"], "EFBE")


@need_build
class FuncAblaufTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        cls.psp, cls.base, cls.entries, _ = record_func(cls.box)
        cls.t = by_target(cls.entries, cls.base)
        cls.summe = sorted(cls.t[0x200], key=lambda e: e["call_id"])[0]
        cls.ruft = cls.t[0x220][0]

    def test_ints(self):
        r = self.box.func_run(self.t[0x240][0]["zustand"])
        self.assertEqual(r["ende"], "ret")
        self.assertEqual(len(r["ints"]), 1)
        i = r["ints"][0]
        self.assertEqual(i["vektor"], "0x21")
        self.assertEqual((int(i["regs_ein"]["eax"], 16) >> 8) & 0xFF, 0x30)
        self.assertIn("regs_aus", i)
        self.assertEqual(i["regs_aus"]["eax"], r["regs_aus"]["eax"])
        self.assertEqual(int(i["adr"]["linear"], 16), self.base + 0x242)
        self.assertEqual(r["ints_weggelassen"], 0)

    def test_irq_sperre(self):
        tick = self.t[0x280][0]["zustand"]
        r = self.box.func_run(tick, max_befehle=2000000)
        self.assertEqual(r["ende"], "abgebrochen")
        r = self.box.func_run(tick, irqs=True)
        self.assertEqual(r["ende"], "ret")
        self.assertTrue(r["irqs"])

    def test_programmende(self):
        adr = f"l:{self.base + 0x200:08X}"
        r = self.box.func_run(self.summe["zustand"], schreiben=[{"adresse": adr, "hex": "B8074CCD21"}])
        self.assertEqual(r["ende"], "programmende")

    def test_unterbrochen_haltepunkt(self):
        self.box.break_add(self.base + 0x200)
        try:
            r = self.box.func_run(self.ruft["zustand"])
        finally:
            self.box.break_remove(self.base + 0x200)
        self.assertEqual(r["ende"], "unterbrochen")
        self.assertEqual(r["stop_reason"], "breakpoint")
        self.assertFalse(self.box.status()["func"])

    def test_status_waehrend_lauf(self):
        adr = f"l:{self.base + 0x200:08X}"
        self.box.call("func.start", zustand=self.summe["zustand"], schreiben=[{"adresse": adr, "hex": "EBFE"}],
                      max_befehle=10 ** 12, timeout_ms=60000)
        s = self.box.status()
        self.assertTrue(s["func"])
        self.assertTrue(s["befehls_hook"])
        self.box.pause()
        r = self.box.call("func.result")
        self.assertEqual(r["ende"], "unterbrochen")
        self.assertEqual(r["stop_reason"], "pause")
        self.assertFalse(self.box.status()["func"])
        # IRQ-Sperre ist wieder aus: der Timer-Tick laeuft weiter
        t0 = self.box.mem_read("r:0040:006C", 4)
        self.box.resume()
        wait_until(lambda: self.box.mem_read("r:0040:006C", 4) != t0, timeout=5)

    def test_kindprogramm_ist_kein_programmende(self):
        r = self.box.func_run(self.t[0x2A0][0]["zustand"])
        self.assertEqual(r["ende"], "ret")
        self.assertEqual(int(r["regs_aus"]["eax"], 16) & 0xFFFF, 0x1234)

    def test_fehler_vor_dem_laden_aendert_nichts(self):
        self.box.pause()
        info = self.box.state_save("ohne_aufruf2")
        self.box.mem_write(f"r:{self.psp:04X}:03F0", b"\x55")
        with self.assertRaises(DmcpError) as ctx:
            self.box.func_run(info["pfad"])
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertEqual(self.box.mem_read(f"r:{self.psp:04X}:03F0", 1), b"\x55")  # nicht geladen
        with self.assertRaises(DmcpError) as ctx:
            self.box.func_run(self.summe["zustand"], regs={"xyz": "0x1"})
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertEqual(self.box.mem_read(f"r:{self.psp:04X}:03F0", 1), b"\x55")

    def test_fehler_nach_dem_laden_haelt_an(self):
        self.box.resume()
        with self.assertRaises(DmcpError) as ctx:
            self.box.func_run(self.summe["zustand"], schreiben=[{"adresse": "p:FFFFFFF0", "hex": "00"}])
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertTrue(self.box.status()["paused"])
        self.assertFalse(self.box.status()["func"])

    def test_state_load_beendet_lauf(self):
        adr = f"l:{self.base + 0x200:08X}"
        self.box.call("func.start", zustand=self.summe["zustand"], schreiben=[{"adresse": adr, "hex": "EBFE"}],
                      max_befehle=10 ** 12, timeout_ms=60000)
        self.box.state_load(pfad=self.ruft["zustand"])
        r = self.box.call("func.result")
        self.assertEqual(r["ende"], "unterbrochen")
        self.assertEqual(r["stop_reason"], "state_load")
        self.assertEqual(r["geaendert"], [])


@need_build
class Func32Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS32")
        module = wait_until(lambda: next((m for m in cls.box.modules() if m["modul"] == "CALLS32.COM"), None),
                            timeout=15)
        psp = int(module["psp"], 16)
        cls.base = psp * 16
        cls.box.calls_start(bereiche=[{"von": f"l:{cls.base + 0x280:08X}", "bis": f"l:{cls.base + 0x2FF:08X}"}],
                            zustaende=1, name="f32")
        cls.box.mem_write(f"r:{psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.mem_read("l:00500000", 4) == b"C32!", timeout=15)
        cls.entries = read_jsonl(cls.box.calls_stop()["datei"])

    def test_pm32(self):
        e = self.entries[0]
        info = json.loads(Path(e["zustand"]).with_suffix(".json").read_text())
        self.assertEqual(info["aufruf"]["modus"], "pm")
        self.assertEqual(info["aufruf"]["bits"], 32)
        self.assertEqual(int(info["aufruf"]["esp_nach"], 16), 0x0008FFFC)  # vor dem call; ret 4 -> 0x90000
        r = self.box.func_run(e["zustand"])
        self.assertEqual(r["ende"], "ret")
        self.assertEqual(int(r["regs_aus"]["eax"], 16), 0x11223345)
        self.assertEqual(int(r["regs_aus"]["esp"], 16), 0x00090000)
        self.assertEqual(r["regs_aus"], e["regs_aus"])
        self.assertEqual(r["geaendert"], [])
        self.assertEqual(r["halt_adr"]["modus"], "pm")


@need_build
class FuncStackwechselTest(unittest.TestCase):
    """Ring-3-Funktion ruft INT 30h; der Ring-0-Handler laeuft auf dem TSS-Stack (FUNCPM.COM)."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "FUNCPM")
        module = wait_until(lambda: next((m for m in cls.box.modules() if m["modul"] == "FUNCPM.COM"), None),
                            timeout=15)
        psp = int(module["psp"], 16)
        base = psp * 16
        cls.box.calls_start(bereiche=[{"von": f"l:{base + 0x2C0:08X}", "bis": f"l:{base + 0x2DF:08X}"}],
                            zustaende=1, name="fpm")
        cls.box.mem_write(f"r:{psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.mem_read("l:00500000", 4) == b"R3OK", timeout=15)
        cls.entries = read_jsonl(cls.box.calls_stop()["datei"])

    def test_tss_stack_nicht_in_geaendert(self):
        e = self.entries[0]
        r = self.box.func_run(e["zustand"])
        self.assertEqual(r["ende"], "ret")
        self.assertEqual(r["regs_aus"], e["regs_aus"])
        self.assertEqual(len(r["geaendert"]), 1, r["geaendert"])  # ohne Rahmen und Pushes auf dem Ring-0-Stack
        g = r["geaendert"][0]
        self.assertEqual(int(g["adresse"]["linear"], 16), 0x00500010)
        self.assertEqual(g["nachher"], "44332211")


@need_build
class FuncKernTest(unittest.TestCase):
    def test_braucht_core_normal(self):
        box = Dosbox.start(mounts=[], autoexec=[], headless=True, conf={"cpu": {"core": "dynamic"}})
        try:
            with self.assertRaises(DmcpError) as ctx:
                box.func_run("gibt_es_nicht.sav")
            self.assertEqual(ctx.exception.code, "bad_argument")
            self.assertIn("core", ctx.exception.message)
        finally:
            box.stop()


class FuncRunZeitablaufTest(unittest.TestCase):
    """Python-Logik von func_run beim Zeitablauf, ohne Emulator."""

    def make(self, results):
        box = object.__new__(Dosbox)
        calls = []

        def call(method, **params):
            calls.append(method)
            if method == "func.result":
                return results.pop(0)
            return {}
        box.call = call
        box.status = lambda: {"paused": False}
        return box, calls

    def test_zeitablauf_wird_abgebrochen(self):
        box, calls = self.make([{"laeuft": True},
                                {"ende": "unterbrochen", "stop_reason": "pause", "befehle": 5}])
        r = box.func_run("x.sav", timeout_s=0)
        self.assertEqual(calls, ["func.start", "func.result", "emu.pause", "func.result"])
        self.assertEqual(r["ende"], "abgebrochen")
        self.assertIsNone(r["stop_reason"])
        self.assertEqual(r["hinweis"], "timeout_s erreicht")

    def test_fertig_im_letzten_moment_bleibt_ret(self):
        box, _ = self.make([{"laeuft": True}, {"ende": "ret", "stop_reason": None, "befehle": 5}])
        r = box.func_run("x.sav", timeout_s=0)
        self.assertEqual(r["ende"], "ret")
        self.assertNotIn("hinweis", r)
