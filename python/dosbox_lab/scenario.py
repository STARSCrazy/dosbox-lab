"""D35: Szenario-Skripte laden, pruefen und abspielen (Spec docs/superpowers/specs/2026-10-14-szenarien-design.md)."""
import copy
import inspect
import json
import math
import re
import time
from pathlib import Path

from .api import Dosbox
from .errors import DmcpError
from .instance import Mount

FORMAT = "dosbox-lab-szenario/1"
ERGEBNIS_FORMAT = "dosbox-lab-szenario-ergebnis/1"
ALTE_FORMATE = ("dosbox-mcp-szenario/1",)  # frueherer Name, wird weiter gelesen
STANDARD_TIMEOUT_S = 30.0
KOPF = {"format", "name", "variablen", "start", "schritte"}
START = {"mounts", "autoexec", "conf", "exe", "layout"}
GEMEINSAM = {"timeout_s", "notiz"}
AUFZEICHNUNGEN = ("calls", "coverage", "events", "flow")
ARTEN = ("tippe", "taste", "klick", "maus_programm", "warte_text", "warte_speicher", "warte_halt", "weiter",
         "warte_bilder", "warte_bild", "warte_s", "zustand_laden", "speicherstand", "screenshot",
         "aufzeichnung_start", "aufzeichnung_stop")
KNOEPFE = ("links", "rechts", "mitte")
PFADZEICHEN = set('\\/:*?"<>|')
_VAR = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _fail(stelle, grund):
    raise DmcpError("bad_argument", f"{stelle}: {grund}")


def _ist_zahl(wert):
    """Endliche Zahl (kein bool, kein NaN/Infinity, kein Ueberlauf)."""
    if not isinstance(wert, (int, float)) or isinstance(wert, bool):
        return False
    try:
        return math.isfinite(wert)
    except OverflowError:
        return False


def _ist_ganz(wert):
    return isinstance(wert, int) and not isinstance(wert, bool)


def _einsetzen(wert, variablen, stelle):
    """${NAME} und $$ in allen Strings (rekursiv) ersetzen."""
    if isinstance(wert, str):
        def ersetze(m):
            if m.group(0) == "$$":
                return "$"
            name = m.group(1)
            if name not in variablen:
                _fail(stelle, f"Variable {name} hat keinen Wert")
            return variablen[name]
        return _VAR.sub(ersetze, wert)
    if isinstance(wert, list):
        return [_einsetzen(v, variablen, f"{stelle}[{i}]") for i, v in enumerate(wert)]
    if isinstance(wert, dict):
        return {k: _einsetzen(v, variablen, f"{stelle}.{k}") for k, v in wert.items()}
    return wert


def _name_ok(text):
    return isinstance(text, str) and text != "" and not (set(text) & PFADZEICHEN)


def _objekt(wert, stelle, pflicht, erlaubt=()):
    if not isinstance(wert, dict):
        _fail(stelle, "muss ein Objekt sein")
    for key in pflicht:
        if key not in wert:
            _fail(stelle, f"{key} fehlt")
    for key in wert:
        if key not in set(pflicht) | set(erlaubt):
            _fail(stelle, f"unbekannter Schluessel {key}")
    return wert


def _start_optionen(art):
    """Erlaubte Optionen von aufzeichnung_start: Parameter von <art>_start, bei coverage/flow dazu name
    (Dateiname fuer coverage_save/flow_save)."""
    namen = set(inspect.signature(getattr(Dosbox, f"{art}_start")).parameters) - {"self"}
    return namen | {"name"}


def _hex_ok(text):
    if not isinstance(text, str) or text == "" or len(text) % 2:
        return False
    try:
        bytes.fromhex(text)
    except ValueError:
        return False
    return True


def _pruefe_wert(art, wert, stelle, basis):
    """Prueft den Wert einer Schritt-Art und gibt ihn (ggf. normalisiert) zurueck."""
    if art in ("tippe", "zustand_laden"):
        if not isinstance(wert, str) or wert == "":
            _fail(stelle, f"{art} braucht einen Text")
        return str((basis / wert).resolve()) if art == "zustand_laden" else wert
    if art in ("speicherstand", "screenshot"):
        if not _name_ok(wert):
            _fail(stelle, f"{art} braucht einen Namen ohne Pfadzeichen")
        return wert
    if art == "taste":
        if isinstance(wert, str) and wert:
            return {"name": wert, "ms": 60}
        _objekt(wert, stelle, ("name",), ("ms",))
        if not isinstance(wert["name"], str) or not _ist_ganz(wert.get("ms", 60)) or wert.get("ms", 60) <= 0:
            _fail(stelle, "taste braucht name (Text) und ms > 0")
        return {"name": wert["name"], "ms": wert.get("ms", 60)}
    if art == "klick":
        _objekt(wert, stelle, ("x", "y"), ("knopf",))
        if not _ist_ganz(wert["x"]) or not _ist_ganz(wert["y"]):
            _fail(stelle, "klick braucht ganze Zahlen x und y")
        knopf = wert.get("knopf", "links")
        if knopf not in KNOEPFE:
            _fail(stelle, "knopf muss " + ", ".join(KNOEPFE) + " sein")
        return {"x": wert["x"], "y": wert["y"], "knopf": knopf}
    if art == "maus_programm":
        _objekt(wert, stelle, ("x", "y", "x_adresse", "y_adresse"), ("breite", "toleranz", "versuche", "warte_s"))
        if not _ist_ganz(wert["x"]) or not _ist_ganz(wert["y"]):
            _fail(stelle, "maus_programm braucht ganze Zahlen x und y")
        for key in ("x_adresse", "y_adresse"):
            if not isinstance(wert[key], str) or wert[key] == "":
                _fail(stelle, f"{key} muss ein nichtleerer Text sein")
        for key, minimum in (("breite", 1), ("versuche", 1), ("toleranz", 0)):
            if key in wert and (not _ist_ganz(wert[key]) or wert[key] < minimum):
                _fail(stelle, f"{key} muss eine ganze Zahl >= {minimum} sein")
        if "warte_s" in wert and (not _ist_zahl(wert["warte_s"]) or wert["warte_s"] <= 0):
            _fail(stelle, "warte_s muss eine Zahl > 0 sein")
        return dict(wert)
    if art == "warte_text":
        if isinstance(wert, str) and wert:
            wert = {"text": wert}
        _objekt(wert, stelle, ("text",), ("zeile", "regex"))
        if not isinstance(wert["text"], str) or wert["text"] == "":
            _fail(stelle, "warte_text braucht einen Text")
        zeile = wert.get("zeile")
        if zeile is not None and (not _ist_ganz(zeile) or zeile < 0):
            _fail(stelle, "zeile muss eine ganze Zahl >= 0 sein")
        regex = wert.get("regex", False)
        if not isinstance(regex, bool):
            _fail(stelle, "regex muss true oder false sein")
        if regex:
            try:
                re.compile(wert["text"])
            except re.error as exc:
                _fail(stelle, f"regex ungueltig: {exc}")
        return {"text": wert["text"], "zeile": zeile, "regex": regex}
    if art == "warte_speicher":
        _objekt(wert, stelle, ("adresse", "hex"), ("maske",))
        if not isinstance(wert["adresse"], str) or not _hex_ok(wert["hex"]):
            _fail(stelle, "warte_speicher braucht adresse (Text) und hex (gerade Zahl von Hexziffern)")
        maske = wert.get("maske")
        if maske is not None and (not _hex_ok(maske) or len(maske) != len(wert["hex"])):
            _fail(stelle, "maske muss Hex mit derselben Laenge wie hex sein")
        return {"adresse": wert["adresse"], "hex": wert["hex"], "maske": maske}
    if art == "warte_halt":
        _objekt(wert, stelle, ("adresse",), ("danach",))
        danach = wert.get("danach", "anhalten")
        if not isinstance(wert["adresse"], str) or danach not in ("anhalten", "weiter"):
            _fail(stelle, "warte_halt braucht adresse (Text), danach \"anhalten\" oder \"weiter\"")
        return {"adresse": wert["adresse"], "danach": danach}
    if art == "weiter":
        if wert is not True:
            _fail(stelle, "weiter braucht den Wert true")
        return True
    if art == "warte_bilder":
        if not _ist_ganz(wert) or wert < 1:
            _fail(stelle, "warte_bilder braucht eine ganze Zahl >= 1")
        return wert
    if art == "warte_bild":
        if not _ist_ganz(wert) or wert < 0:
            _fail(stelle, "warte_bild braucht eine ganze Zahl >= 0")
        return wert
    if art == "warte_s":
        if not _ist_zahl(wert) or wert <= 0:
            _fail(stelle, "warte_s braucht eine Zahl > 0")
        return float(wert)
    if art in ("aufzeichnung_start", "aufzeichnung_stop"):
        if not isinstance(wert, dict) or wert.get("art") not in AUFZEICHNUNGEN:
            _fail(stelle, f"{art} braucht art ({', '.join(AUFZEICHNUNGEN)})")
        if art == "aufzeichnung_stop":
            _objekt(wert, stelle, ("art",))
        else:
            _objekt(wert, stelle, ("art",), _start_optionen(wert["art"]))
        return dict(wert)
    raise AssertionError(art)


def _pruefe_start(start, basis):
    start = _objekt(start if start is not None else {}, "start", (), START)
    mounts = []
    for i, m in enumerate(start.get("mounts", [])):
        stelle = f"start.mounts[{i}]"
        _objekt(m, stelle, ("laufwerk", "pfad"), ("cdrom",))
        if (not isinstance(m["laufwerk"], str) or len(m["laufwerk"]) != 1 or not m["laufwerk"].isascii()
                or not m["laufwerk"].isalpha()):
            _fail(stelle, "laufwerk muss ein Buchstabe sein")
        if not isinstance(m["pfad"], str):
            _fail(stelle, "pfad muss ein Text sein")
        if not isinstance(m.get("cdrom", False), bool):
            _fail(stelle, "cdrom muss true oder false sein")
        host = (basis / m["pfad"]).resolve()
        if not host.is_dir():
            _fail(stelle, f"pfad {host} ist kein Ordner")
        mounts.append(Mount(m["laufwerk"].upper(), str(host), m.get("cdrom", False)))
    autoexec = start.get("autoexec", [])
    if not isinstance(autoexec, list) or not all(isinstance(z, str) for z in autoexec):
        _fail("start.autoexec", "muss eine Liste von Texten sein")
    conf = copy.deepcopy(start.get("conf", {}))
    if not isinstance(conf, dict) or not all(isinstance(v, dict) for v in conf.values()):
        _fail("start.conf", "muss ein Objekt {abschnitt: {schluessel: wert}} sein")
    conf.setdefault("dosbox", {}).setdefault("quit warning", "false")
    exe = start.get("exe")
    if exe is not None:
        if not isinstance(exe, str):
            _fail("start.exe", "muss ein Text sein")
        exe = str((basis / exe).resolve())
    layout = start.get("layout", "de")
    if not isinstance(layout, str):
        _fail("start.layout", "muss ein Text sein")
    return {"mounts": mounts, "autoexec": autoexec, "conf": conf, "exe": exe, "layout": layout}


def load_scenario(quelle, variablen=None):
    """Szenario (Pfad oder Objekt) laden, pruefen, Variablen einsetzen. Fehler: DmcpError bad_argument."""
    if isinstance(quelle, (str, Path)):
        pfad = Path(quelle).resolve()
        try:
            roh = json.loads(pfad.read_text(encoding="utf-8-sig"))  # -sig: BOM von PowerShell/Notepad
        except (OSError, ValueError) as exc:
            _fail(str(pfad), f"nicht lesbar: {exc}")
        basis, herkunft = pfad.parent, str(pfad)
    else:
        roh, basis, herkunft = copy.deepcopy(quelle), Path.cwd(), None
    _objekt(roh, "szenario", ("format", "name", "schritte"), KOPF)
    if roh["format"] != FORMAT and roh["format"] not in ALTE_FORMATE:
        _fail("format", f"muss {FORMAT} sein")
    if not _name_ok(roh["name"]):
        _fail("name", "muss ein Name ohne Pfadzeichen sein")
    werte = roh.get("variablen", {})
    if not isinstance(werte, dict) or not all(isinstance(v, str) for v in werte.values()):
        _fail("variablen", "muss ein Objekt {NAME: \"wert\"} mit Texten sein")
    werte = dict(werte)
    for k, v in (variablen or {}).items():
        if not isinstance(v, str):
            _fail("variablen", f"{k} muss ein Text sein")
        werte[k] = v
    start = _pruefe_start(_einsetzen(roh.get("start"), werte, "start"), basis)
    if not isinstance(roh["schritte"], list):
        _fail("schritte", "muss eine Liste sein")
    schritte = []
    for i, roh_schritt in enumerate(roh["schritte"]):
        stelle = f"schritte[{i}]"
        if not isinstance(roh_schritt, dict):
            _fail(stelle, "muss ein Objekt sein")
        arten = [k for k in roh_schritt if k not in GEMEINSAM]
        if len(arten) != 1:
            _fail(stelle, "braucht genau eine Schritt-Art")
        art = arten[0]
        if art not in ARTEN:
            _fail(stelle, f"unbekannte Schritt-Art {art}")
        timeout = roh_schritt.get("timeout_s", STANDARD_TIMEOUT_S)
        if not _ist_zahl(timeout) or timeout <= 0:
            _fail(stelle, "timeout_s muss eine Zahl > 0 sein")
        notiz = roh_schritt.get("notiz")
        if notiz is not None and not isinstance(notiz, str):
            _fail(stelle, "notiz muss ein Text sein")
        notiz = _einsetzen(notiz, werte, f"{stelle}.notiz")
        wert = _pruefe_wert(art, _einsetzen(roh_schritt[art], werte, stelle), f"{stelle}.{art}", basis)
        schritte.append({"art": art, "wert": wert, "timeout_s": float(timeout), "notiz": notiz})
    return {"name": roh["name"], "quelle": herkunft, "basis": basis, "variablen": werte, "start": start,
            "schritte": schritte}


ABFRAGE_S = 0.05


class _Schrittfehler(Exception):
    def __init__(self, grund, meldung):
        super().__init__(meldung)
        self.grund, self.meldung = grund, meldung


class _Spieler:
    def __init__(self, sz, box):
        self.sz, self.box = sz, box
        self.r = {"format": ERGEBNIS_FORMAT, "name": sz["name"], "quelle": sz["quelle"],
                  "variablen": sz["variablen"], "instanz": box.id, "status": "ok", "schritte_fertig": 0,
                  "schritte_gesamt": len(sz["schritte"]), "dauer_s": 0.0, "bilder": None, "schritte": [],
                  "speicherstaende": [], "screenshots": [], "aufzeichnungen": [], "fehler": None, "pfad": None}
        self.laufend = {}  # art -> Eintrag in aufzeichnungen
        self._save_name = {}  # art -> name aus aufzeichnung_start (coverage/flow: Dateiname beim Speichern)

    # --- Hilfen ---
    def _status_laeuft(self):
        status = self.box.status()
        if status["paused"]:
            raise _Schrittfehler("angehalten", f"Emulator ist angehalten (stop_reason {status['stop_reason']})")
        return status

    def _warte(self, bedingung, timeout_s, was):
        ende = time.monotonic() + timeout_s
        while True:
            self._status_laeuft()
            if bedingung():
                return
            if time.monotonic() > ende:
                raise _Schrittfehler("timeout", f"{was} nach {timeout_s:g} s nicht erreicht")
            time.sleep(ABFRAGE_S)

    # --- Schritte ---
    def _schritt_tippe(self, wert, timeout_s):
        self._status_laeuft()
        self.box.type_text(wert, layout=self.sz["start"]["layout"])
        self.box.wait_input(timeout_s)

    def _schritt_taste(self, wert, timeout_s):
        self._status_laeuft()
        self.box.key(wert["name"], ms=wert["ms"])
        self.box.wait_input(timeout_s)

    def _schritt_klick(self, wert, timeout_s):
        self._status_laeuft()
        self.box.mouse_click(wert["x"], wert["y"], button=wert["knopf"])

    def _schritt_maus_programm(self, wert, timeout_s):
        self._status_laeuft()
        self.box.mouse_move_program(**wert)

    def _schritt_warte_text(self, wert, timeout_s):
        def passt():
            try:
                zeilen = self.box.screen_text()["zeilen"]
            except DmcpError as exc:
                if exc.code == "instance_dead":
                    raise
                return False  # Grafikmodus oder Startphase: noch nicht
            if wert["zeile"] is not None:
                zeilen = zeilen[wert["zeile"]:wert["zeile"] + 1]
            if wert["regex"]:
                return any(re.search(wert["text"], z) for z in zeilen)
            return any(wert["text"] in z for z in zeilen)
        self._warte(passt, timeout_s, f"Text {wert['text']!r}")

    def _schritt_warte_speicher(self, wert, timeout_s):
        soll = bytes.fromhex(wert["hex"])
        maske = bytes.fromhex(wert["maske"]) if wert["maske"] else b"\xFF" * len(soll)

        def passt():
            info = self.box.mem_read_info(wert["adresse"], len(soll))
            if info.get("fehlend"):
                return False  # fehlende Seiten lesen als Fuellbytes: noch nicht
            ist = bytes.fromhex(info["hex"])
            return all((i & m) == (s & m) for i, s, m in zip(ist, soll, maske))
        self._warte(passt, timeout_s, f"Speicher {wert['adresse']} = {wert['hex']}")

    def _schritt_warte_bilder(self, wert, timeout_s):
        self._status_laeuft()
        ende = time.monotonic() + timeout_s  # gilt fuer den ganzen Schritt, nicht je Teilstueck
        rest = wert
        while rest > 0:
            bleibt = ende - time.monotonic()
            if bleibt <= 0:
                self.box.resume()  # steht am Bildende des letzten Teilstuecks: weiterlaufen lassen
                raise _Schrittfehler("timeout", f"{wert} Bilder nach {timeout_s:g} s nicht erreicht")
            teil = min(rest, 10000)
            status = self.box.frame_step(teil, timeout_s=bleibt)
            if status["stop_reason"] != "frame":
                raise _Schrittfehler("angehalten", f"Emulator hielt waehrend warte_bilder an "
                                                   f"(stop_reason {status['stop_reason']})")
            rest -= teil
        self.box.resume()

    def _schritt_warte_bild(self, wert, timeout_s):
        self._warte(lambda: self.box.status()["bilder"] >= wert, timeout_s, f"Bild {wert}")

    def _schritt_warte_s(self, wert, timeout_s):
        self._status_laeuft()
        time.sleep(wert)

    def _schritt_warte_halt(self, wert, timeout_s):
        self._status_laeuft()
        ziel = self.box.translate(wert["adresse"])
        if "linear" not in ziel:
            raise _Schrittfehler("bad_argument", f"{wert['adresse']} hat keine lineare Adresse")
        linear = int(ziel["linear"], 16)
        self.box.break_add(linear)

        def am_ziel():
            """True bei Halt am Ziel; ein anderer Halt (Waechter, REP-Teilhalt) wird fortgesetzt."""
            status = self.box.status()
            if not status["paused"]:
                return False
            if status["stop_reason"] == "breakpoint" and int(status.get("stop_linear", "0"), 16) == linear:
                return True
            self.box.resume()
            return False
        try:
            ende = time.monotonic() + timeout_s
            while not am_ziel():
                if time.monotonic() > ende:
                    if am_ziel():  # Halt genau an der Zeitgrenze zaehlt noch
                        break
                    raise _Schrittfehler("timeout",
                                         f"Haltepunkt {wert['adresse']} nach {timeout_s:g} s nicht erreicht")
                time.sleep(ABFRAGE_S)
        finally:
            try:
                self.box.break_remove(linear)
            except DmcpError:
                pass
        if wert["danach"] == "weiter":
            self.box.resume()

    def _schritt_weiter(self, wert, timeout_s):
        self.box.resume()

    def _schritt_zustand_laden(self, wert, timeout_s):
        self.box.state_load(pfad=wert)
        if self.box.status()["paused"]:
            self.box.resume()  # state_load laesst den Laufzustand; Spec: danach laeuft der Emulator

    def _schritt_speicherstand(self, wert, timeout_s, nr):
        info = self.box.state_save(wert)
        self.r["speicherstaende"].append({"nr": nr, "name": wert, "pfad": info["pfad"]})

    def _schritt_screenshot(self, wert, timeout_s, nr):
        info = self.box.screenshot(wert)
        self.r["screenshots"].append({"nr": nr, "name": wert, "pfad": info["pfad"]})

    def _schritt_aufzeichnung_start(self, wert, timeout_s, nr):
        art = wert["art"]
        optionen = {k: v for k, v in wert.items() if k != "art"}
        name = optionen.pop("name", None) if art in ("coverage", "flow") else None
        if art in self.laufend:
            # Spec 3: die erste Datei bleibt im Ergebnis - also erst regulaer beenden (bei Fehler bleibt der Eintrag
            # in laufend und _aufraeumen versucht es noch einmal)
            alt = self.laufend[art]
            alt["dateien"] = self._stoppe(art)
            alt["hinweis"] = "ersetzt"
            del self.laufend[art]
        getattr(self.box, f"{art}_start")(**optionen)
        eintrag = {"art": art, "nr_start": nr, "nr_stop": None, "selbst_gestoppt": False, "dateien": []}
        self.laufend[art] = eintrag
        self._save_name[art] = name
        self.r["aufzeichnungen"].append(eintrag)

    def _stoppe(self, art):
        """Beendet eine Aufzeichnung und gibt ihre Dateien zurueck."""
        if art == "calls":
            info = self.box.calls_stop()
            return [info["datei"], info["json_pfad"]]
        if art == "events":
            return [self.box.events_stop()["datei"]]
        name = self._save_name.get(art)
        if art == "coverage":
            speichern, stoppen, standard = self.box.coverage_save, self.box.coverage_stop, "coverage"
        else:
            speichern, stoppen, standard = self.box.flow_save, self.box.flow_stop, "indirekt"
        fehler = None
        try:
            info = speichern(name or standard)
        except Exception as exc:  # trotzdem stoppen (bremst sonst weiter), dann den Fehler melden
            fehler = exc
        try:
            stoppen()
        except Exception:
            if fehler is None:
                raise
        if fehler is not None:
            raise fehler
        return [info[k] for k in ("pfad", "json_pfad", "hits_pfad") if info.get(k)]

    def _schritt_aufzeichnung_stop(self, wert, timeout_s, nr):
        art = wert["art"]
        if art not in self.laufend:
            raise _Schrittfehler("bad_argument", f"keine laufende Aufzeichnung {art}")
        eintrag = self.laufend.pop(art)
        eintrag["dateien"] = self._stoppe(art)
        eintrag["nr_stop"] = nr

    # --- Ablauf ---
    def _diagnose(self):
        """Bildschirmtext und Screenshot nach einem Fehler; None, wenn der Emulator nicht mehr lebt."""
        name = self.sz["name"]
        text_pfad = bild_pfad = None
        try:
            try:
                zeilen = self.box.screen_text()["zeilen"]
            except DmcpError as exc:
                if exc.code == "instance_dead":
                    raise
                zeilen = []
            text_pfad = Path(self.box.inst.outdir) / f"{name}.fehler.txt"
            try:
                text_pfad.write_text("\n".join(zeilen) + ("\n" if zeilen else ""), encoding="utf-8")
            except OSError:
                text_pfad = None
            bild_pfad = self.box.screenshot(f"{name}.fehler")["pfad"]
        except DmcpError:
            if not self._lebt():
                return None, None
        return (str(text_pfad) if text_pfad else None), bild_pfad

    def _lebt(self):
        try:
            return bool(self.box.inst.alive())
        except Exception:
            return False

    def spiele(self):
        start = time.monotonic()
        for nr, schritt in enumerate(self.sz["schritte"]):
            t0 = time.monotonic()
            methode = getattr(self, f"_schritt_{schritt['art']}")
            try:
                if schritt["art"] in ("speicherstand", "screenshot", "aufzeichnung_start", "aufzeichnung_stop"):
                    methode(schritt["wert"], schritt["timeout_s"], nr)
                else:
                    methode(schritt["wert"], schritt["timeout_s"])
            except (_Schrittfehler, DmcpError, ValueError, TypeError) as exc:
                if isinstance(exc, _Schrittfehler):
                    grund, meldung = exc.grund, exc.meldung
                elif isinstance(exc, DmcpError):
                    grund, meldung = exc.code, exc.message
                else:  # falscher Optionswert (api.py: int("viel") u. a.): Schritt-Fehler, keine Ausnahme
                    grund, meldung = "bad_argument", f"{type(exc).__name__}: {exc}"
                if grund != "instance_dead" and not self._lebt():
                    grund = "instance_dead"
                bildschirm, bild = (None, None) if grund == "instance_dead" else self._diagnose()
                self.r["status"] = "fehler"
                self.r["fehler"] = {"nr": nr, "art": schritt["art"], "grund": grund, "meldung": meldung,
                                    "bildschirm": bildschirm, "screenshot": bild}
                break
            self.r["schritte"].append({"nr": nr, "art": schritt["art"], "dauer_s": round(time.monotonic() - t0, 3),
                                       "notiz": schritt["notiz"]})
            self.r["schritte_fertig"] = nr + 1
        self._aufraeumen()
        self.r["dauer_s"] = round(time.monotonic() - start, 3)
        try:
            self.r["bilder"] = self.box.status()["bilder"]
        except DmcpError:
            self.r["bilder"] = None
        self._schreibe()
        return self.r

    def _aufraeumen(self):
        """Laufende Aufzeichnungen stoppen (selbst_gestoppt); lebt der Emulator nicht mehr, bleiben dateien leer."""
        for art, eintrag in list(self.laufend.items()):
            eintrag["selbst_gestoppt"] = True
            try:
                eintrag["dateien"] = self._stoppe(art)
            except Exception:  # je Aufzeichnung einzeln: ein Fehler bricht weder das Aufraeumen noch spiele() ab
                eintrag["dateien"] = []
        self.laufend.clear()

    def _schreibe(self):
        try:
            pfad = Path(self.box.inst.outdir) / f"{self.sz['name']}.szenario.json"
            self.r["pfad"] = str(pfad)
            pfad.write_text(json.dumps(self.r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            self.r["pfad"] = None


def play_scenario(quelle, variablen=None, headless=True, stop_am_ende=False, ausgabe=None):
    """Szenario laden und in einer neuen Instanz abspielen. Rueckgabe (box, ergebnis); box None bei
    stop_am_ende oder wenn der Emulator nicht mehr lebt. Schritt-Fehler stehen im Ergebnis (status "fehler")."""
    sz = load_scenario(quelle, variablen)
    start = sz["start"]
    box = Dosbox.start(mounts=start["mounts"], autoexec=start["autoexec"], headless=headless, exe=start["exe"],
                       conf=start["conf"], outdir=ausgabe)
    try:
        ergebnis = _Spieler(sz, box).spiele()
    except BaseException:
        try:
            box.stop()  # unerwarteter Fehler (kein Schritt-Fehler): keine verwaiste Instanz zuruecklassen
        except Exception:
            pass
        raise
    if stop_am_ende or not box.inst.alive():
        box.stop()
        return None, ergebnis
    return box, ergebnis
