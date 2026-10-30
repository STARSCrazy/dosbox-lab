import json
import unittest
from pathlib import Path

from dosbox_mcp import DmcpError
from dosbox_mcp.files import read_jsonl
from tests.util import class_box, need_build, run_typed, start_idle, wait_prompt, wait_until


@need_build
class CallsParamTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)

    def assertBad(self, **kw):
        with self.assertRaises(DmcpError) as ctx:
            self.box.calls_start(**kw)
        self.assertEqual(ctx.exception.code, "bad_argument")

    def test_needs_filter(self):
        self.assertBad()

    def test_bad_values(self):
        self.assertBad(ziele=["l:1000"], stack_bytes=2000)
        self.assertBad(ziele=["l:1000"], max_je_ziel=0)
        self.assertBad(ziele=["l:1000"], max_bytes=100)
        self.assertBad(ziele=["kein_symbol_xyz"])
        self.assertBad(ziele=["p:1000"])
        self.assertBad(bereiche=[{"von": "l:2000", "bis": "l:1000"}])
        self.assertBad(module=["GIBTSNICHT.EXE"])
        self.assertBad(ints=[{"nummer": "1FF"}])
        self.assertBad(ziele=["l:1000"], name="a/b")

    def test_start_stop_files(self):
        r = self.box.calls_start(ziele=["l:00001000"], bereiche=[{"von": "l:2000", "bis": "l:20FF"}],
                                 ints=[{"nummer": "21", "ah": "3F"}], name="calls_leer")
        self.assertTrue(r["aktiv"])
        self.assertTrue(self.box.status()["befehls_hook"])
        s = self.box.calls_stop()
        self.assertFalse(s["aktiv"])
        self.assertEqual(s["geschrieben"], 0)
        self.assertEqual(Path(s["datei"]).read_text(), "")
        meta = json.loads(Path(s["json_pfad"]).read_text())
        self.assertEqual(meta["format"], "dosbox-mcp-calls/1")
        self.assertEqual(meta["datei"], "calls_leer.jsonl")
        self.assertEqual(meta["filter"]["ziele"][0]["linear"], "0x00001000")
        self.assertEqual(meta["filter"]["bereiche"][0]["bis"]["linear"], "0x000020FF")
        self.assertEqual(meta["filter"]["ints"], [{"nummer": "0x21", "ah": "0x3F"}])
        self.assertIsNone(meta["stack_bytes"])
        self.assertEqual(meta["max_je_ziel"], 1000)
        self.assertEqual(meta["weggelassen"], [])
        self.assertFalse(self.box.status()["befehls_hook"])


def calls_psp(box, modul):
    module = wait_until(lambda: next((m for m in box.modules() if m["modul"] == modul), None), timeout=15)
    return int(module["psp"], 16)


def lin(e, feld):
    return int(e[feld]["linear"], 16)


@need_build
class CallsRecordTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        cls.base = cls.psp * 16
        b = cls.base
        cls.box.calls_start(bereiche=[{"von": f"l:{b + 0x200:08X}", "bis": f"l:{b + 0x2FF:08X}"}],
                            ints=[{"nummer": "21", "ah": "30"}], max_je_ziel=3, name="calls_rec")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.mem_read(f"r:{cls.psp:04X}:03F0", 1) == b"\x01", timeout=15)
        wait_until(lambda: cls.box.calls_status()["offen"] == 1, timeout=10)
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["datei"])
        cls.meta = json.loads(Path(cls.stop["json_pfad"]).read_text())
        cls.by_target = {}
        for e in cls.entries:
            cls.by_target.setdefault(lin(e, "ziel") - b, []).append(e)

    def test_counts(self):
        self.assertEqual(len(self.entries), 15)
        self.assertEqual(self.stop["geschrieben"], 15)  # 14 abgeschlossene + 1 offen
        self.assertEqual(self.stop["offen"], 1)
        self.assertEqual({k: len(v) for k, v in self.by_target.items() if 0x200 <= k < 0x300},
                         {0x200: 2, 0x210: 1, 0x220: 3, 0x230: 1, 0x240: 1, 0x250: 3, 0x260: 1, 0x270: 1, 0x280: 1})

    def test_nested(self):
        aussen = self.by_target[0x210][0]
        innen = self.by_target[0x200]
        self.assertEqual(aussen["tiefe"], 0)
        self.assertIsNone(aussen["parent_id"])
        self.assertEqual([e["parent_id"] for e in innen], [aussen["call_id"]] * 2)
        self.assertEqual([e["tiefe"] for e in innen], [1, 1])
        self.assertEqual([int(e["regs_ein"]["eax"], 16) & 0xFFFF for e in innen], [5, 6])
        self.assertEqual([int(e["regs_aus"]["eax"], 16) & 0xFFFF for e in innen], [6, 7])
        self.assertEqual(lin(innen[0], "von") - self.base, 0x210)
        self.assertTrue(all(e["ende"] == "ret" and e["art"] == "near" and e["bits"] == 16 for e in innen))
        self.assertLess(innen[0]["nr"], innen[0]["nr_ende"])
        self.assertLess(innen[1]["nr_ende"], aussen["nr_ende"])

    def test_recursion(self):
        rek = sorted(self.by_target[0x220], key=lambda e: e["tiefe"])
        self.assertEqual([e["tiefe"] for e in rek], [0, 1, 2])
        self.assertEqual(rek[1]["parent_id"], rek[0]["call_id"])
        self.assertEqual(rek[2]["parent_id"], rek[1]["call_id"])
        self.assertEqual([int(e["regs_ein"]["ecx"], 16) & 0xFFFF for e in rek], [3, 2, 1])
        written_order = [e["call_id"] for e in self.entries if lin(e, "ziel") - self.base == 0x220]
        self.assertEqual(written_order, [rek[2]["call_id"], rek[1]["call_id"], rek[0]["call_id"]])

    def test_far(self):
        far = self.by_target[0x230][0]
        self.assertEqual(far["art"], "far")
        self.assertEqual(far["ende"], "retf")
        self.assertEqual(int(far["regs_aus"]["eax"], 16) & 0xFFFF, 0x1234)

    def test_stack_and_ret_n(self):
        e = self.by_target[0x240][0]
        sp = int(e["regs_ein"]["esp"], 16) & 0xFFFF
        laenge = min(32, 0x10000 - sp)  # 16-bit stack: the read ends at the segment end
        self.assertGreaterEqual(laenge, 6)
        self.assertEqual(e["stack_laenge"], laenge)
        self.assertEqual(len(e["stack"]), 2 * laenge)
        ret = lin(e, "von") - self.base + 3  # call near rel16 = 3 Byte
        self.assertEqual(e["stack"][:4], f"{ret & 0xFF:02X}{ret >> 8:02X}")
        self.assertEqual(e["stack"][4:12], "BB00AA00")
        self.assertEqual(int(e["regs_aus"]["eax"], 16) & 0xFFFF, 0x165)
        sp_ein = int(e["regs_ein"]["esp"], 16) & 0xFFFF
        self.assertEqual(int(e["regs_aus"]["esp"], 16) & 0xFFFF, sp_ein + 2 + 4)
        self.assertEqual(e["ende"], "ret")

    def test_limit_per_target(self):
        self.assertEqual(len(self.by_target[0x250]), 3)
        omitted = [(int(w["ziel"]["linear"], 16) - self.base, w["anzahl"]) for w in self.meta["weggelassen"]]
        self.assertEqual(omitted, [(0x250, 2)])

    def test_escape_without_return(self):
        aussen, innen = self.by_target[0x260][0], self.by_target[0x270][0]
        self.assertEqual(aussen["ende"], "ret")
        self.assertEqual(innen["ende"], "unbekannt")
        self.assertEqual(innen["parent_id"], aussen["call_id"])
        self.assertNotIn("regs_aus", innen)
        self.assertEqual(self.box.mem_read(f"r:{self.psp:04X}:03F0", 1), b"\x01")  # 0xEE nie geschrieben

    def test_int_entry(self):
        ints = [e for e in self.entries if e["art"] == "int"]
        self.assertEqual(len(ints), 1)
        e = ints[0]
        self.assertEqual(e["vektor"], "0x21")
        self.assertEqual(e["ende"], "iret")
        self.assertEqual(int(e["regs_ein"]["eax"], 16) & 0xFF00, 0x3000)
        self.assertIsNone(e["parent_id"])

    def test_open_at_stop(self):
        e = self.by_target[0x280][0]
        self.assertTrue(e["offen"])
        self.assertNotIn("ende", e)
        self.assertNotIn("regs_aus", e)
        self.assertEqual(sum(1 for x in self.entries if x.get("offen")), 1)

    def test_nothing_open_after_stop(self):
        self.assertEqual(self.box.calls_status()["offen"], 0)
        self.assertEqual(self.box.calls_stop()["offen"], 0)  # zweites stop: keine alten Rahmen

    def test_kontext_haupt(self):
        self.assertTrue(all(e["kontext"] == "haupt" for e in self.entries))

    def test_entry_fields(self):
        e = self.by_target[0x200][0]
        for feld in ("call_id", "parent_id", "tiefe", "kontext", "nr", "nr_ende", "von", "ziel", "art", "bits",
                     "regs_ein", "flags_ein", "stack", "stack_laenge", "regs_aus", "flags_aus", "ende"):
            self.assertIn(feld, e)
        self.assertEqual(set(e["regs_ein"]), {"eax", "ebx", "ecx", "edx", "esi", "edi", "ebp", "esp", "ds", "es",
                                              "fs", "gs", "ss"})
        self.assertEqual(e["ziel"]["modul"], "CALLS.COM")
        self.assertTrue(e["flags_ein"].startswith("0x"))


def wait_prompt_after(box, text, timeout=15):
    """Wartet, bis nach der letzten Bildschirmzeile mit `text` wieder ein Prompt C:\\> steht (Programm beendet)."""
    def done():
        zeilen = box.screen_text()["zeilen"]
        idx = [i for i, z in enumerate(zeilen) if text in z]
        return bool(idx) and any(z.startswith("C:\\>") for z in zeilen[idx[-1] + 1:])

    wait_until(done, timeout=timeout)


@need_build
class CallsZieleStateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        b = cls.base = cls.psp * 16
        cls.box.calls_start(ziele=[f"l:{b + 0x200:08X}", f"l:{b + 0x220:08X}", f"l:{b + 0x280:08X}"],
                            stack_bytes=8, name="calls_ziele")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.calls_status()["offen"] == 1, timeout=15)
        cls.box.state_save("calls_state")
        cls.box.state_load("calls_state")
        cls.offen_nach_load = cls.box.calls_status()["offen"]
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["datei"])

    def test_only_targets(self):
        targets = sorted({lin(e, "ziel") - self.base for e in self.entries})
        self.assertEqual(targets, [0x200, 0x220, 0x280])

    def test_parent_only_recorded(self):
        innen = [e for e in self.entries if lin(e, "ziel") - self.base == 0x200]
        self.assertEqual([e["parent_id"] for e in innen], [None, None])
        self.assertEqual([e["tiefe"] for e in innen], [0, 0])

    def test_stack_bytes(self):
        for e in self.entries:
            laenge = min(8, 0x10000 - (int(e["regs_ein"]["esp"], 16) & 0xFFFF))  # 16-bit stack: bis Segmentende
            self.assertEqual((e["stack_laenge"], len(e["stack"])), (laenge, 2 * laenge))

    def test_state_load_closes_open(self):
        self.assertEqual(self.offen_nach_load, 0)
        haengt = [e for e in self.entries if lin(e, "ziel") - self.base == 0x280]
        self.assertEqual(len(haengt), 1)
        self.assertEqual(haengt[0]["ende"], "unbekannt")
        self.assertNotIn("offen", haengt[0])
        self.assertEqual(self.stop["offen"], 0)


@need_build
class CallsRestartTest(unittest.TestCase):
    """Ein Lauf endet direkt nach einem CALL (Haltepunkt am Ziel, Transfer noch offen), das Programm laeuft ohne
    Aufzeichnung weiter. Der naechste Lauf darf den alten CALL nicht als Rahmen nachtragen."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        b = cls.base = cls.psp * 16
        bereich = [{"von": f"l:{b + 0x200:08X}", "bis": f"l:{b + 0x2FF:08X}"}]
        cls.box.break_add(b + 0x210, einmalig=True)  # aussen: CALL von 010E ist ausgefuehrt, noch nicht aufgeloest
        cls.box.calls_start(bereiche=bereich, name="calls_neu1")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        cls.box.wait_paused(15)
        cls.box.calls_stop()
        cls.box.break_add(b + 0x220, einmalig=True)  # rek (cx=3), erster Befehl des naechsten Laufs
        cls.box.resume()
        cls.box.wait_paused(15)
        cls.box.calls_start(bereiche=bereich, name="calls_neu2")
        cls.box.resume()
        wait_until(lambda: cls.box.mem_read(f"r:{cls.psp:04X}:03F0", 1) == b"\x01", timeout=15)
        wait_until(lambda: cls.box.calls_status()["offen"] == 1, timeout=10)  # haengt
        cls.entries = read_jsonl(cls.box.calls_stop()["datei"])

    def test_no_stale_frame(self):
        self.assertFalse(any(lin(e, "von") - self.base == 0x10E for e in self.entries))
        rek = sorted((e for e in self.entries if lin(e, "ziel") - self.base == 0x220), key=lambda e: e["call_id"])
        self.assertEqual([int(e["regs_ein"]["ecx"], 16) & 0xFFFF for e in rek], [2, 1])
        self.assertIsNone(rek[0]["parent_id"])
        self.assertEqual(rek[0]["tiefe"], 0)
        self.assertEqual(rek[1]["parent_id"], rek[0]["call_id"])

    def test_top_level(self):
        oben = [e for e in self.entries if lin(e, "ziel") - self.base != 0x220 or
                int(e["regs_ein"]["ecx"], 16) & 0xFFFF == 2]
        self.assertEqual(len(oben), 11)  # rek, fern, mitparam, 5x oft, flucht_aussen/-innen, haengt
        self.assertTrue(all(e["parent_id"] is None and e["tiefe"] == 0 for e in oben
                            if lin(e, "ziel") - self.base != 0x270))
        self.assertTrue(all(e["kontext"] == "haupt" for e in self.entries))


@need_build
class CallsStateLoadPendingTest(unittest.TestCase):
    """state.load mitten in einem Lauf, waehrend ein CALL (call haengt) noch nicht aufgeloest ist: Der alte Transfer
    gehoert zum ersetzten Zustand und darf nach dem Laden keinen Rahmen erzeugen."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        b = cls.base = cls.psp * 16
        cls.box.break_add(b + 0x250, einmalig=True)  # oft, erster Aufruf (cx=5)
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        cls.box.wait_paused(15)
        cls.box.state_save("calls_pending")
        cls.box.calls_start(bereiche=[{"von": f"l:{b + 0x250:08X}", "bis": f"l:{b + 0x250:08X}"}],
                            name="calls_pend")
        cls.box.break_add(b + 0x280, einmalig=True)  # haengt: call haengt (013A) ist noch nicht aufgeloest
        cls.box.resume()
        cls.box.wait_paused(15)
        cls.box.state_load("calls_pending")
        cls.box.resume()
        wait_until(lambda: cls.box.mem_read(f"r:{cls.psp:04X}:03F0", 1) == b"\x01", timeout=15)
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["datei"])

    def test_no_stale_frame(self):
        # oft 2..5 vor und nach dem Laden, jeweils von 0128 aus, alle oben und mit ret beendet
        self.assertEqual(len(self.entries), 8)
        self.assertTrue(all(lin(e, "von") - self.base == 0x128 for e in self.entries))
        self.assertTrue(all(e["parent_id"] is None and e["tiefe"] == 0 for e in self.entries))
        self.assertTrue(all(e["ende"] == "ret" for e in self.entries))
        self.assertEqual(self.stop["offen"], 0)


@need_build
class CallsExitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        wait_prompt(cls.box)
        cls.box.calls_start(ints=[{"nummer": "21", "ah": "4C"}], name="calls_exit")
        run_typed(cls.box, "EXITCODE")
        wait_until(lambda: any(m["modul"] == "EXITCODE.COM" for m in cls.box.modules()), timeout=15)
        wait_prompt_after(cls.box, "EXITCODE stderr")
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["datei"])

    def test_exit_closes_frame(self):
        own = [e for e in self.entries if (int(e["regs_ein"]["eax"], 16) & 0xFFFF) == 0x4C2A]
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]["ende"], "unbekannt")
        self.assertNotIn("offen", own[0])


@need_build
class CallsLimitTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS")
        cls.psp = calls_psp(cls.box, "CALLS.COM")
        b = cls.psp * 16
        cls.box.events_start(typen=["limit_reached"], name="ev_limit")
        cls.box.calls_start(bereiche=[{"von": f"l:{b + 0x200:08X}", "bis": f"l:{b + 0x2FF:08X}"}],
                            max_bytes=4096, name="calls_limit")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_until(lambda: not cls.box.calls_status()["aktiv"], timeout=15)
        cls.status = cls.box.calls_status()
        cls.events = read_jsonl(cls.box.events_stop()["datei"])

    def test_limit(self):
        self.assertTrue(self.status["limit_erreicht"])
        self.assertLessEqual(Path(self.status["datei"]).stat().st_size, 4096)
        meta = json.loads(Path(self.status["datei"]).with_suffix(".json").read_text())
        self.assertTrue(meta["limit_erreicht"])
        self.assertTrue(any(e["typ"] == "limit_reached" and e["aufzeichnung"] == "calls" for e in self.events))
        self.assertFalse(self.box.status()["befehls_hook"])


@need_build
class CallsContextUnwindTest(unittest.TestCase):
    """CALLSEXC: ein Ausnahme-Kontext endet ohne eigenes IRET (IRET zum aeusseren Kontext, Programmende im Handler).
    Danach muessen Eintraege wieder im Kontext "haupt" liegen."""

    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLSEXC")
        cls.psp = calls_psp(cls.box, "CALLSEXC.COM")
        cls.base = cls.psp * 16
        cls.box.calls_start(ziele=[f"l:{cls.base + 0x200:08X}"], ints=[{"nummer": "21", "ah": "09"}],
                            name="calls_exc")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_prompt_after(cls.box, "CALLSEXC")
        run_typed(cls.box, "EXITCODE")
        wait_prompt_after(cls.box, "EXITCODE stderr")
        cls.stop = cls.box.calls_stop()
        cls.entries = read_jsonl(cls.stop["datei"])

    def test_iret_to_outer_context(self):
        ziel = [e for e in self.entries if e["art"] == "near"]
        self.assertEqual(len(ziel), 1)
        self.assertEqual(lin(ziel[0], "ziel") - self.base, 0x200)
        self.assertEqual(ziel[0]["kontext"], "haupt")
        self.assertIsNone(ziel[0]["parent_id"])
        self.assertEqual(ziel[0]["ende"], "ret")

    def test_exit_drops_interrupt_context(self):
        dos = [e for e in self.entries if e["art"] == "int" and e["von"].get("modul") == "EXITCODE.COM"]
        self.assertEqual(len(dos), 1)  # INT 21h 09h (stdout); 40h ist nicht im Filter
        e = dos[-1]
        self.assertEqual(e["kontext"], "haupt")
        self.assertEqual(e["ende"], "iret")
        self.assertEqual(self.stop["offen"], 0)


@need_build
class Calls32Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())
        run_typed(cls.box, "CALLS32")
        cls.psp = calls_psp(cls.box, "CALLS32.COM")
        b = cls.base = cls.psp * 16
        cls.box.calls_start(bereiche=[{"von": f"l:{b + 0x280:08X}", "bis": f"l:{b + 0x2FF:08X}"}], name="calls32")
        cls.box.mem_write(f"r:{cls.psp:04X}:03F1", b"\x01")
        wait_until(lambda: cls.box.mem_read("l:00500000", 4) == b"C32!", timeout=15)
        cls.entries = read_jsonl(cls.box.calls_stop()["datei"])

    def test_32bit_entry(self):
        self.assertEqual(len(self.entries), 1)
        e = self.entries[0]
        self.assertEqual(e["bits"], 32)
        self.assertEqual(e["art"], "near")
        self.assertEqual(e["ende"], "ret")
        self.assertEqual(e["ziel"]["modus"], "pm")
        self.assertEqual(lin(e, "ziel"), self.base + 0x280)
        self.assertEqual(e["stack_laenge"], 64)
        ret = lin(e, "von") + 5  # call rel32
        self.assertEqual(e["stack"][:8], ret.to_bytes(4, "little").hex().upper())
        self.assertEqual(e["stack"][8:16], "44332211")
        self.assertEqual(int(e["regs_ein"]["esp"], 16), 0x0008FFF8)
        self.assertEqual(int(e["regs_aus"]["esp"], 16), 0x00090000)
        self.assertEqual(int(e["regs_aus"]["eax"], 16), 0x11223345)
