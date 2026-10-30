import copy
import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from dosbox_lab import Dosbox, DmcpError
from dosbox_lab.files import read_jsonl
from dosbox_lab.instance import REPO
from dosbox_lab.scenario import _Spieler, load_scenario, play_scenario
from tests.util import TESTPROGS, class_box, need_build, start_idle, wait_until


@need_build
class BildzaehlerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())

    def test_bilder_steigt(self):
        a = self.box.status()["bilder"]
        wait_until(lambda: self.box.status()["bilder"] > a + 3, timeout=10)

    def test_frame_step_zaehlt(self):
        self.box.pause()
        a = self.box.status()["bilder"]
        self.box.frame_step(5)
        self.assertEqual(self.box.status()["bilder"], a + 5)
        self.box.resume()


@need_build
class OutdirTest(unittest.TestCase):
    def test_outdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            ziel = Path(tmp) / "neu" / "aus"
            box = Dosbox.start(mounts=[], autoexec=[], headless=True, outdir=str(ziel))
            try:
                self.assertEqual(box.inst.outdir, ziel.resolve())
                wait_until(lambda: box.status()["bilder"] > 0, timeout=15)  # Screenshot braucht ein Bild
                info = box.screenshot("probe")
                self.assertEqual(Path(info["pfad"]).parent.resolve(), ziel.resolve())
                box.inst.client.close()  # die Named Pipe bedient nur einen Client
                box = Dosbox.attach(box.id)
                self.assertEqual(box.inst.outdir, ziel.resolve())
            finally:
                box.stop()

GUT = {"format": "dosbox-lab-szenario/1", "name": "probe", "variablen": {"PROG": "SZENE"},
       "start": {"mounts": [{"laufwerk": "C", "pfad": "."}], "layout": "us"},
       "schritte": [{"warte_text": "C:\\>"}, {"tippe": "${PROG}\n"}, {"warte_text": "BEREIT", "timeout_s": 5},
                    {"taste": "a"}, {"warte_speicher": {"adresse": "l:00000000", "hex": "00", "maske": "FF"}},
                    {"warte_bilder": 3}, {"speicherstand": "s"}, {"notiz": "x", "screenshot": "b"}]}


class LadenTest(unittest.TestCase):
    def fehler(self, sz, text, variablen=None):
        with self.assertRaises(DmcpError) as ctx:
            load_scenario(sz, variablen)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn(text, ctx.exception.message)

    def mit(self, **aenderung):
        sz = copy.deepcopy(GUT)
        sz.update(aenderung)
        return sz

    def test_gut(self):
        s = load_scenario(GUT)
        self.assertEqual(s["name"], "probe")
        self.assertEqual(s["schritte"][1], {"art": "tippe", "wert": "SZENE\n", "timeout_s": 30.0, "notiz": None})
        self.assertEqual(s["schritte"][2]["timeout_s"], 5.0)
        self.assertEqual(s["schritte"][7]["notiz"], "x")
        self.assertEqual(s["start"]["mounts"][0].drive, "C")
        self.assertEqual(s["start"]["conf"]["dosbox"]["quit warning"], "false")

    def test_variablen_ueberschreiben_und_dollar(self):
        sz = self.mit(schritte=[{"tippe": "${PROG} $$5\n"}])
        self.assertEqual(load_scenario(sz, {"PROG": "NACH"})["schritte"][0]["wert"], "NACH $5\n")

    def test_variable_im_mount(self):
        sz = self.mit(variablen={"DIR": str(Path.cwd())}, start={"mounts": [{"laufwerk": "D", "pfad": "${DIR}"}]},
                      schritte=[{"warte_s": 1}])
        self.assertEqual(Path(load_scenario(sz)["start"]["mounts"][0].host), Path.cwd().resolve())

    def test_relativ_zur_datei(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "spiel").mkdir()
            datei = Path(tmp) / "s.json"
            sz = self.mit(start={"mounts": [{"laufwerk": "C", "pfad": "spiel"}]},
                          schritte=[{"zustand_laden": "x.sav"}])
            datei.write_text(json.dumps(sz), encoding="utf-8")
            s = load_scenario(str(datei))
            self.assertEqual(Path(s["start"]["mounts"][0].host), (Path(tmp) / "spiel").resolve())
            self.assertEqual(Path(s["schritte"][0]["wert"]), (Path(tmp) / "x.sav").resolve())
            self.assertEqual(s["quelle"], str(datei.resolve()))

    def test_bom_datei(self):
        with tempfile.TemporaryDirectory() as tmp:
            datei = Path(tmp) / "bom.json"
            datei.write_text(json.dumps(GUT), encoding="utf-8-sig")  # PowerShell/Notepad schreiben ein BOM
            self.assertEqual(load_scenario(str(datei))["name"], "probe")

    def test_fehlerfaelle(self):
        self.fehler(self.mit(format="x"), "format")
        self.fehler(self.mit(name="a/b"), "name")
        self.fehler(self.mit(extra=1), "extra")
        self.fehler(self.mit(schritte=[{}]), "schritte[0]")
        self.fehler(self.mit(schritte=[{"tippe": "a", "taste": "b"}]), "schritte[0]")
        self.fehler(self.mit(schritte=[{"springe": 1}]), "springe")
        self.fehler(self.mit(schritte=[{"warte_bilder": True}]), "warte_bilder")
        self.fehler(self.mit(schritte=[{"warte_bilder": 0}]), "warte_bilder")
        self.fehler(self.mit(schritte=[{"warte_text": "a", "timeout_s": 0}]), "timeout_s")
        self.fehler(self.mit(schritte=[{"tippe": "${NIX}"}]), "NIX")
        self.fehler(self.mit(schritte=[{"warte_speicher": {"adresse": "l:0", "hex": "0"}}]), "hex")
        self.fehler(self.mit(schritte=[{"warte_speicher": {"adresse": "l:0", "hex": "00", "maske": "FFFF"}}]),
                    "maske")
        self.fehler(self.mit(schritte=[{"warte_text": {"text": "(", "regex": True}}]), "regex")
        self.fehler(self.mit(schritte=[{"warte_halt": {"adresse": "x", "danach": "nie"}}]), "danach")
        self.fehler(self.mit(schritte=[{"aufzeichnung_start": {"art": "alles"}}]), "art")
        self.fehler(self.mit(schritte=[{"weiter": False}]), "weiter")
        self.fehler(self.mit(start={"mounts": [{"laufwerk": "C", "pfad": "gibt_es_nicht_xyz"}]}), "pfad")
        self.fehler(self.mit(variablen={"A": 1}), "variablen")
        self.fehler(self.mit(start={"farbe": 1}), "farbe")

    def test_typpruefung_nachgeschaerft(self):
        mp = {"x": 1, "y": 2, "x_adresse": "l:0", "y_adresse": "l:2"}
        ok = load_scenario(self.mit(schritte=[{"maus_programm": dict(mp, breite=3, toleranz=0, versuche=2,
                                                                      warte_s=0.5)}]))
        self.assertEqual(ok["schritte"][0]["wert"]["breite"], 3)
        for aend, text in (({"x": 1.5}, "x"), ({"y": True}, "y"), ({"x_adresse": ""}, "x_adresse"),
                           ({"y_adresse": 5}, "y_adresse"), ({"breite": 0}, "breite"),
                           ({"toleranz": -1}, "toleranz"), ({"versuche": "2"}, "versuche"),
                           ({"warte_s": 0}, "warte_s"), ({"warte_s": float("nan")}, "warte_s")):
            self.fehler(self.mit(schritte=[{"maus_programm": dict(mp, **aend)}]), text)
        self.fehler(self.mit(schritte=[{"klick": {"x": 1, "y": 1, "knopf": 3}}]), "knopf")
        self.fehler(self.mit(schritte=[{"klick": {"x": 1, "y": 1, "knopf": "oben"}}]), "knopf")
        self.fehler(self.mit(start={"mounts": [{"laufwerk": "C", "pfad": ".", "cdrom": "false"}]}), "cdrom")
        self.fehler(self.mit(start={"mounts": [{"laufwerk": "ä", "pfad": "."}]}), "laufwerk")
        self.fehler(self.mit(schritte=[{"warte_s": float("inf")}]), "warte_s")
        self.fehler(self.mit(schritte=[{"warte_s": 10 ** 400}]), "warte_s")
        self.fehler(self.mit(schritte=[{"warte_text": "a", "timeout_s": float("nan")}]), "timeout_s")
        self.fehler(self.mit(schritte=[{"warte_text": "a", "timeout_s": 10 ** 400}]), "timeout_s")

    def test_notiz_variablen(self):
        s = load_scenario(self.mit(schritte=[{"warte_s": 1, "notiz": "${PROG} $$"}]))
        self.assertEqual(s["schritte"][0]["notiz"], "SZENE $")


def szene(*schritte, name="szene"):
    return {"format": "dosbox-lab-szenario/1", "name": name,
            "start": {"mounts": [{"laufwerk": "C", "pfad": str(TESTPROGS)}], "layout": "us",
                      "conf": {"dos": {"keyboardlayout": "us"}}},
            "schritte": [{"warte_text": "C:\\>", "timeout_s": 15}, {"tippe": "SZENE\n"},
                         {"warte_text": "BEREIT", "timeout_s": 15}] + list(schritte)}


@need_build
class SpielTest(unittest.TestCase):
    def test_ablauf_ok(self):
        sz = szene({"taste": "q"}, {"warte_text": {"text": "TASTE\\s+q", "regex": True}},
                   {"warte_speicher": {"adresse": "rel:SZENE.COM:0000:0300", "hex": "71"}},
                   {"warte_speicher": {"adresse": "rel:SZENE.COM:0000:0301", "hex": "50", "maske": "F0"}},
                   {"warte_bilder": 3}, {"warte_bild": 1}, {"screenshot": "bild"}, {"speicherstand": "ende"})
        box, r = play_scenario(sz)
        try:
            self.assertEqual(r["status"], "ok", r["fehler"])
            self.assertEqual(r["format"], "dosbox-lab-szenario-ergebnis/1")
            self.assertEqual(r["schritte_fertig"], r["schritte_gesamt"])
            self.assertEqual(len(r["schritte"]), r["schritte_gesamt"])
            self.assertGreater(r["bilder"], 3)
            self.assertTrue(Path(r["speicherstaende"][0]["pfad"]).exists())
            self.assertTrue(Path(r["screenshots"][0]["pfad"]).exists())
            datei = json.loads(Path(r["pfad"]).read_text(encoding="utf-8"))
            self.assertEqual(datei["status"], "ok")
            self.assertEqual(Path(r["pfad"]).name, "szene.szenario.json")
            box.state_load(pfad=r["speicherstaende"][0]["pfad"])  # Stand ist ladbar
        finally:
            if box is not None:
                box.stop()

    def test_ausgabe_und_stop_am_ende(self):
        with tempfile.TemporaryDirectory() as tmp:
            box, r = play_scenario(szene({"taste": "a"}, {"screenshot": "x"}), ausgabe=tmp, stop_am_ende=True)
            self.assertIsNone(box)
            self.assertEqual(r["status"], "ok", r["fehler"])
            self.assertEqual(Path(r["pfad"]).parent.resolve(), Path(tmp).resolve())
            self.assertEqual(Path(r["screenshots"][0]["pfad"]).parent.resolve(), Path(tmp).resolve())


@need_build
class SpielFehlerTest(unittest.TestCase):
    def test_timeout_mit_diagnose(self):
        box, r = play_scenario(szene({"warte_text": "GIBTSNICHT", "timeout_s": 1, "notiz": "n"}))
        try:
            self.assertEqual(r["status"], "fehler")
            f = r["fehler"]
            self.assertEqual((f["nr"], f["art"], f["grund"]), (3, "warte_text", "timeout"))
            self.assertIn("BEREIT", Path(f["bildschirm"]).read_text(encoding="utf-8"))
            self.assertTrue(Path(f["screenshot"]).exists())
            self.assertEqual(r["schritte_fertig"], 3)
            self.assertEqual(len(r["schritte"]), 3)
        finally:
            if box is not None:
                box.stop()


class TotTest(unittest.TestCase):
    """Emulator stirbt: Ergebnis fehler/instance_dead, ohne Ausnahme (Attrappe, kein Emulator)."""

    def test_instance_dead(self):
        from dosbox_lab.scenario import _Spieler, load_scenario
        sz = load_scenario({"format": "dosbox-lab-szenario/1", "name": "tot", "schritte": [{"warte_s": 0.01},
                                                                                            {"tippe": "x"}]})

        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)

        class Tot:
            def __init__(self):
                self.inst = type("I", (), {"outdir": Path(tmpdir), "id": "x", "alive": lambda s: False})()
                self.id = "x"

            def status(self):
                raise DmcpError("instance_dead", "weg")

            def type_text(self, *a, **k):
                raise DmcpError("instance_dead", "weg")

        r = _Spieler(sz, Tot()).spiele()
        self.assertEqual(r["status"], "fehler")
        self.assertEqual(r["fehler"]["grund"], "instance_dead")
        self.assertIsNone(r["fehler"]["bildschirm"])
        self.assertIsNone(r["fehler"]["screenshot"])


# HLT-Schleife nach dem Tastendruck (sti; hlt; jmp), nach jedem Timer-IRQ wieder erreicht. fertig_fn (0200) taugt
# nicht: Ein Szenario kann nicht zugleich auf den Halt warten und die Taste schicken, und nach der Taste ist
# fertig_fn schon durchgelaufen, bevor warte_halt den Haltepunkt setzt.
HALT = "rel:SZENE.COM:0000:011B"


@need_build
class HaltTest(unittest.TestCase):
    def test_halt_bleibt_stehen_und_haltepunkt_weg(self):
        box, r = play_scenario(szene({"taste": "a"}, {"warte_halt": {"adresse": HALT}}, {"speicherstand": "eingang"}))
        try:
            self.assertEqual(r["status"], "ok", r["fehler"])
            st = box.status()
            self.assertTrue(st["paused"])
            self.assertEqual(st["stop_reason"], "breakpoint")
            self.assertEqual(box.break_list()["eintraege"], [])
        finally:
            if box is not None:
                box.stop()

    def test_halt_danach_weiter(self):
        box, r = play_scenario(szene({"taste": "a"}, {"warte_halt": {"adresse": HALT, "danach": "weiter"}},
                                     {"warte_text": "FERTIG"}), stop_am_ende=True)
        self.assertEqual(r["status"], "ok", r["fehler"])

    def test_angehalten(self):
        box, r = play_scenario(szene({"taste": "a"}, {"warte_halt": {"adresse": HALT}},
                                     {"warte_text": "FERTIG", "timeout_s": 5}))
        try:
            self.assertEqual((r["fehler"]["nr"], r["fehler"]["grund"]), (5, "angehalten"))
        finally:
            if box is not None:
                box.stop()

    def test_weiter(self):
        box, r = play_scenario(szene({"taste": "a"}, {"warte_halt": {"adresse": HALT}},
                                     {"weiter": True}, {"warte_text": "FERTIG"}), stop_am_ende=True)
        self.assertEqual(r["status"], "ok", r["fehler"])

    def test_halt_timeout(self):
        box, r = play_scenario(szene({"warte_halt": {"adresse": HALT}, "timeout_s": 1}))
        try:
            self.assertEqual(r["fehler"]["grund"], "timeout")
            self.assertEqual(box.break_list()["eintraege"], [])  # auch beim Fehler entfernt
        finally:
            if box is not None:
                box.stop()

    def test_zustand_laden_laeuft_danach(self):
        """Stand am Haltepunkt (angehalten) laden: danach laeuft der Emulator ohne weiter (Spec 3)."""
        with tempfile.TemporaryDirectory() as tmp:
            sav = str(Path(tmp) / "halt.sav")
            box, r = play_scenario(szene({"taste": "a"}, {"warte_halt": {"adresse": HALT}},
                                         {"speicherstand": "halt"}, {"zustand_laden": sav},
                                         {"warte_text": "FERTIG", "timeout_s": 10}), ausgabe=tmp, stop_am_ende=True)
            self.assertEqual(r["status"], "ok", r["fehler"])
            self.assertEqual(Path(r["speicherstaende"][0]["pfad"]).resolve(), Path(sav).resolve())


@need_build
class AufzeichnungTest(unittest.TestCase):
    def test_calls_mit_zustand_und_func_run(self):
        box, r = play_scenario(szene(
            {"aufzeichnung_start": {"art": "calls", "bereiche": [{"von": "rel:SZENE.COM:0000:0200",
                                                                   "bis": "rel:SZENE.COM:0000:02FF"}],
                                    "zustaende": 1, "name": "sz_calls"}},
            {"aufzeichnung_start": {"art": "coverage"}},
            {"taste": "a"}, {"warte_text": "FERTIG"},
            {"aufzeichnung_stop": {"art": "calls"}}))
        try:
            self.assertEqual(r["status"], "ok", r["fehler"])
            a = {e["art"]: e for e in r["aufzeichnungen"]}
            self.assertEqual((a["calls"]["nr_start"], a["calls"]["nr_stop"]), (3, 7))
            self.assertFalse(a["calls"]["selbst_gestoppt"])
            self.assertTrue(a["coverage"]["selbst_gestoppt"])  # am Ende vom Szenario gestoppt
            self.assertIsNone(a["coverage"]["nr_stop"])
            self.assertTrue(a["coverage"]["dateien"])
            self.assertTrue(all(Path(p).exists() for p in a["coverage"]["dateien"]))
            self.assertEqual(Path(a["calls"]["dateien"][0]).name, "sz_calls.jsonl")
            eintraege = read_jsonl(a["calls"]["dateien"][0])
            self.assertEqual(len(eintraege), 1)
            f = box.func_run(eintraege[0]["zustand"])
            self.assertEqual(f["ende"], "ret")
        finally:
            if box is not None:
                box.stop()

    def test_selbst_gestoppt_beim_fehler(self):
        box, r = play_scenario(szene({"aufzeichnung_start": {"art": "events"}},
                                     {"warte_text": "NIE", "timeout_s": 1}))
        try:
            e = r["aufzeichnungen"][0]
            self.assertTrue(e["selbst_gestoppt"])
            self.assertTrue(Path(e["dateien"][0]).exists())
            self.assertFalse(box.events_status()["aktiv"])
        finally:
            if box is not None:
                box.stop()

    def test_ersetzt_behaelt_erste_datei(self):
        box, r = play_scenario(szene({"aufzeichnung_start": {"art": "events", "name": "ev1"}},
                                     {"aufzeichnung_start": {"art": "events", "name": "ev2"}},
                                     {"taste": "a"}, {"warte_text": "FERTIG"}), stop_am_ende=True)
        self.assertEqual(r["status"], "ok", r["fehler"])
        erst, zweit = r["aufzeichnungen"]
        self.assertEqual(erst["hinweis"], "ersetzt")
        self.assertEqual(Path(erst["dateien"][0]).name, "ev1.jsonl")
        self.assertTrue(Path(erst["dateien"][0]).exists())
        self.assertTrue(zweit["selbst_gestoppt"])
        self.assertEqual(Path(zweit["dateien"][0]).name, "ev2.jsonl")

    def test_flow_mit_name(self):
        box, r = play_scenario(szene({"aufzeichnung_start": {"art": "flow", "name": "sz_flow"}},
                                     {"taste": "a"}, {"warte_text": "FERTIG"},
                                     {"aufzeichnung_stop": {"art": "flow"}}), stop_am_ende=True)
        self.assertEqual(r["status"], "ok", r["fehler"])
        pfad = Path(r["aufzeichnungen"][0]["dateien"][0])
        self.assertEqual(pfad.name, "sz_flow.jsonl")
        self.assertTrue(pfad.exists())


@need_build
class SpeicherFehlendTest(unittest.TestCase):
    def test_fehlende_seiten_sind_noch_nicht(self):
        # oberhalb des RAMs (16 MB): mem.read liefert Nullen mit fehlend - das ist kein Treffer
        box, r = play_scenario(szene({"warte_speicher": {"adresse": "l:01000000", "hex": "00"}, "timeout_s": 1}))
        try:
            self.assertEqual((r["fehler"]["nr"], r["fehler"]["grund"]), (3, "timeout"))
        finally:
            if box is not None:
                box.stop()


class _Attrappe:
    """Emulator-Attrappe fuer Ablauftests ohne Emulator."""

    def __init__(self, outdir, paused=False):
        self.inst = type("I", (), {"outdir": Path(outdir), "id": "x", "alive": lambda s: True})()
        self.id = "x"
        self.paused = paused
        self.frame_aufrufe = []
        self.resumes = 0
        self.frame_dauer = 0.0
        self.frame_grund = "frame"

    def status(self):
        return {"paused": self.paused, "stop_reason": "pause" if self.paused else "none", "bilder": 7}

    def frame_step(self, anzahl=1, timeout_s=10.0):
        self.frame_aufrufe.append((anzahl, timeout_s))
        time.sleep(self.frame_dauer)
        self.paused = True
        return {"paused": True, "stop_reason": self.frame_grund}

    def resume(self):
        self.resumes += 1
        self.paused = False

    def screen_text(self):
        return {"zeilen": ["ZEILE"]}

    def screenshot(self, name):
        return {"pfad": f"{name}.png"}


class AblaufAttrappeTest(unittest.TestCase):
    """Ablauf ohne Emulator (Attrappe): warte_bilder, Diagnose, unerwartete Ausnahmen, Optionspruefung."""

    def spiel(self, box, *schritte):
        sz = load_scenario({"format": "dosbox-lab-szenario/1", "name": "att", "schritte": list(schritte)})
        return _Spieler(sz, box).spiele()

    def test_warte_bilder_anderer_halt(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = _Attrappe(tmp)
            box.frame_grund = "breakpoint"
            r = self.spiel(box, {"warte_bilder": 3})
            self.assertEqual(r["fehler"]["grund"], "angehalten")
            self.assertEqual(box.resumes, 0)  # bleibt am fremden Halt stehen
            self.assertTrue(box.paused)

    def test_warte_bilder_timeout_fuer_den_ganzen_schritt(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = _Attrappe(tmp)
            box.frame_dauer = 0.6
            r = self.spiel(box, {"warte_bilder": 30000, "timeout_s": 1})
            self.assertEqual(r["fehler"]["grund"], "timeout")
            self.assertEqual(len(box.frame_aufrufe), 2)
            self.assertLess(box.frame_aufrufe[1][1], 0.5)  # Restzeit, nicht wieder 1 s
            self.assertFalse(box.paused)  # vor dem Fehler fortgesetzt

    def test_warte_bilder_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = _Attrappe(tmp)
            r = self.spiel(box, {"warte_bilder": 12000})
            self.assertEqual(r["status"], "ok", r["fehler"])
            self.assertEqual([a for a, _ in box.frame_aufrufe], [10000, 2000])
            self.assertFalse(box.paused)

    def test_diagnose_textdatei_nicht_schreibbar(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = _Attrappe(Path(tmp) / "gibt" / "es_nicht", paused=True)
            r = self.spiel(box, {"tippe": "x"})
            self.assertEqual(r["fehler"]["grund"], "angehalten")
            self.assertIsNone(r["fehler"]["bildschirm"])
            self.assertEqual(r["fehler"]["screenshot"], "att.fehler.png")

    def test_unerwartete_ausnahme_stoppt_emulator(self):
        gestoppt = []
        box = type("B", (), {"id": "x", "stop": lambda s: gestoppt.append(True)})()
        with mock.patch("dosbox_lab.scenario.Dosbox.start", return_value=box), \
                mock.patch("dosbox_lab.scenario._Spieler.spiele", side_effect=RuntimeError("kaputt")):
            with self.assertRaises(RuntimeError):
                play_scenario({"format": "dosbox-lab-szenario/1", "name": "x", "schritte": [{"warte_s": 1}]})
        self.assertEqual(gestoppt, [True])

    def test_falscher_optionswert_ist_schrittfehler(self):
        """ValueError/TypeError aus api.py (z. B. int("viel")) ist ein Schritt-Fehler, keine Ausnahme."""
        class Box(_Attrappe):
            def events_start(self, **k):
                raise ValueError("invalid literal for int()")

        with tempfile.TemporaryDirectory() as tmp:
            r = self.spiel(Box(tmp), {"aufzeichnung_start": {"art": "events", "max_bytes": "viel"}})
        self.assertEqual(r["status"], "fehler")
        self.assertEqual((r["fehler"]["nr"], r["fehler"]["grund"]), (0, "bad_argument"))
        self.assertIn("invalid literal", r["fehler"]["meldung"])

    def test_programmfehler_bleibt_ausnahme(self):
        class Box(_Attrappe):
            def events_start(self, **k):
                raise AttributeError("echter Fehler")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AttributeError):
                self.spiel(Box(tmp), {"aufzeichnung_start": {"art": "events"}})

    def test_aufraeumen_faengt_jeden_fehler_einzeln(self):
        aufrufe = []

        class Box(_Attrappe):
            def coverage_start(self, **k):
                aufrufe.append("coverage_start")

            def coverage_save(self, name):
                raise KeyError("pfad")  # kein DmcpError

            def coverage_stop(self):
                aufrufe.append("coverage_stop")

            def events_start(self, **k):
                aufrufe.append("events_start")

            def events_stop(self):
                aufrufe.append("events_stop")
                return {"datei": "ev.jsonl"}

        with tempfile.TemporaryDirectory() as tmp:
            r = self.spiel(Box(tmp), {"aufzeichnung_start": {"art": "coverage"}},
                           {"aufzeichnung_start": {"art": "events"}})
        self.assertEqual(r["status"], "ok", r["fehler"])
        a = {e["art"]: e for e in r["aufzeichnungen"]}
        self.assertEqual(a["coverage"]["dateien"], [])
        self.assertEqual(a["events"]["dateien"], ["ev.jsonl"])
        self.assertIn("coverage_stop", aufrufe)  # trotz gescheitertem coverage_save gestoppt

    def test_warte_halt_treffer_genau_an_der_zeitgrenze(self):
        class Box(_Attrappe):
            abfragen = 0

            def translate(self, adresse):
                return {"linear": "00001000"}

            def break_add(self, linear):
                pass

            def break_remove(self, linear):
                pass

            def status(self):
                if self.abfragen == 0:  # _status_laeuft
                    self.abfragen += 1
                    return {"paused": False, "stop_reason": "none", "bilder": 7}
                time.sleep(0.01)  # jede Abfrage dauert laenger als die Zeitgrenze
                self.abfragen += 1
                if self.abfragen < 3:
                    return {"paused": False, "stop_reason": "none", "bilder": 7}
                return {"paused": True, "stop_reason": "breakpoint", "stop_linear": "00001000", "bilder": 7}

        with tempfile.TemporaryDirectory() as tmp:
            r = self.spiel(Box(tmp), {"warte_halt": {"adresse": "l:1000"}, "timeout_s": 0.001})
        self.assertEqual(r["status"], "ok", r["fehler"])

    def test_aufzeichnung_optionen_vor_dem_start_geprueft(self):
        def laden(optionen):
            return load_scenario({"format": "dosbox-lab-szenario/1", "name": "x",
                                  "schritte": [{"aufzeichnung_start": optionen}]})
        laden({"art": "coverage", "name": "c", "hits": True})
        laden({"art": "calls", "ziele": ["l:0"], "zustaende": 1, "name": "c"})
        with self.assertRaises(DmcpError) as ctx:
            laden({"art": "coverage", "farbe": 1})
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("farbe", ctx.exception.message)


@need_build
class CliSzenarioTest(unittest.TestCase):
    def cli(self, *args):
        p = subprocess.run([sys.executable, "-m", "dosbox_lab", "szenario", *args], cwd=str(REPO / "python"),
                           capture_output=True, text=True, timeout=120)
        return p.returncode, json.loads(p.stdout)

    def test_ok_und_fehler(self):
        with tempfile.TemporaryDirectory() as tmp:
            sz = szene({"taste": "${T}"}, {"warte_text": "TASTE ${T}"})
            datei = Path(tmp) / "s.json"
            datei.write_text(json.dumps(sz), encoding="utf-8")
            code, r = self.cli("--datei", str(datei), "--var", "T=b", "--ausgabe", str(Path(tmp) / "aus"))
            self.assertEqual((code, r["status"]), (0, "ok"), r)
            self.assertEqual(r["variablen"]["T"], "b")
            sz["schritte"].append({"warte_text": "NIE", "timeout_s": 1})
            datei.write_text(json.dumps(sz), encoding="utf-8")
            code, r = self.cli("--datei", str(datei), "--var", "T=b")
            self.assertEqual((code, r["status"]), (1, "fehler"))

    def test_bad_var(self):
        code, r = self.cli("--datei", "x.json", "--var", "ohne_gleich")
        self.assertEqual((code, r["fehler"]), (1, "bad_argument"))
        self.assertIn("NAME=WERT", r["meldung"])
