# SPDX-License-Identifier: GPL-2.0-or-later
"""Hohe Ebene: eine Methode pro Emulator-Funktion."""
import hashlib
import json
import time
from pathlib import Path
from .errors import DmcpError
from .instance import Instance


def _hex2(value):
    """int -> Hex-String ("21"); Strings bleiben, wie sie sind (der Emulator prueft)."""
    return f"{value:02X}" if isinstance(value, int) else value


class Dosbox:
    def __init__(self, instance):
        self.inst = instance

    @classmethod
    def start(cls, **kwargs):
        return cls(Instance.start(**kwargs))

    @classmethod
    def attach(cls, inst_id):
        return cls(Instance.attach(inst_id))

    @property
    def id(self):
        return self.inst.id

    def stop(self):
        self.inst.stop()

    def call(self, method, **params):
        try:
            return self.inst.client.call(method, params)
        except DmcpError as exc:
            if exc.code == "instance_dead" or not self.inst.alive():
                raise DmcpError("instance_dead", f"{exc.message}\n--- dosbox.log ---\n{self.inst.log_tail()}") from exc
            raise

    def ping(self):
        return self.call("dmcp.ping")
    # --- Ablauf ---
    def status(self):
        return self.call("emu.status")

    def pause(self, until=None, timeout_s=10.0):
        """Haelt an. until="pmode": erst beim ersten Befehl im Protected Mode (wartet bis timeout_s).
        War der Emulator schon pausiert, gilt die Bedingung ab dem naechsten resume()."""
        if until is None:
            return self.call("emu.pause")
        return self._wait_armed(self.call("emu.pause", bis=until), timeout_s, until)

    def _wait_armed(self, status, timeout_s, label):
        """Wartet, bis eine scharf gestellte Haltebedingung greift. Bei Zeitablauf: Bedingung entschaerfen,
        weiterlaufen lassen, timeout melden - ausser im Abbruchfenster hat irgendein Halt gegriffen (Bedingung,
        Haltepunkt, Waechter): dann bleibt der Emulator stehen und der Status kommt zurueck."""
        if status["paused"]:
            return status
        try:
            return self.wait_paused(timeout_s)
        except DmcpError as exc:
            if exc.code != "timeout":
                raise
            status = self.call("emu.pause")  # entschaerft; war schon angehalten, bleibt stop_reason stehen
            if status["stop_reason"] != "pause":
                return status  # ein echter Halt (Treffer gezaehlt, einmalig entfernt): nicht wegwerfen
            self.call("emu.continue")
            raise DmcpError("timeout", f"Emulator hat nicht rechtzeitig angehalten; die Bedingung "
                                       f"\"{label}\" wurde abgebrochen, der Emulator laeuft weiter") from None

    def run_until(self, adresse=None, pmode=None, paging=None, cs=None, cpl=None, ausdruck=None, timeout_s=10.0):
        """Laeuft, bis alle angegebenen Bedingungen zugleich gelten (mindestens ein Befehl), und gibt den Status
        zurueck. adresse: jede Adressform; cs: Selektor/Segment als Hex-String; ausdruck: z. B. "ax==5 && zf"."""
        params = {key: value for key, value in (("adresse", adresse), ("pmode", pmode), ("paging", paging),
                                                ("cs", cs), ("cpl", cpl), ("ausdruck", ausdruck))
                  if value is not None}
        return self._wait_armed(self.call("emu.run_until", **params), timeout_s, "run_until")

    def eval(self, ausdruck):
        """Wertet einen Bedingungsausdruck auf dem aktuellen Zustand aus: {"ausdruck", "wert"} (Hex)."""
        return self.call("emu.eval", ausdruck=ausdruck)

    def wait_paused(self, timeout_s=10.0):
        end = time.monotonic() + timeout_s
        while True:
            status = self.status()
            if status["paused"]:
                return status
            if time.monotonic() > end:
                raise DmcpError("timeout", "Emulator hat nicht rechtzeitig angehalten (Bedingung nicht erreicht)")
            time.sleep(0.02)

    def resume(self):
        return self.call("emu.continue")

    def step(self, count=1):
        return self.call("emu.step", count=count)

    def step_over(self, timeout_s=10.0):
        """Wie step, aber CALL/INT/REP-Stringbefehle laufen bis zum Folgebefehl durch (Stapelzeiger wieder
        mindestens auf Startwert). Kehrt der Aufruf nicht zurueck: timeout, der Emulator laeuft weiter."""
        status = self.call("emu.step_over")
        if status.get("art") == "schritt":
            return status
        return self._wait_armed(status, timeout_s, "step_over")

    def frame_step(self, anzahl=1, timeout_s=10.0):
        """Laeuft anzahl fertige Bilder weiter und haelt dann an (stop_reason "frame")."""
        return self._wait_armed(self.call("emu.frame_step", anzahl=anzahl), timeout_s, "frame")

    def speed(self, mode, cycles=None):
        """mode: "normal", "turbo" oder "cycles" (dann cycles=100..2000000, feste Zyklen pro ms)."""
        if mode == "cycles":
            return self.call("emu.speed", mode=mode, cycles=cycles)
        return self.call("emu.speed", mode=mode)

    def break_add(self, ziel=None, bedingung=None, einmalig=False, linear=None, adresse=None):
        """Code-Haltepunkt. ziel: int = lineare Adresse, str = jede Adressform oder Symbolname.
        bedingung: Ausdruck wie bei run_until ("ax==5"); einmalig: nach dem ersten Halt entfernen."""
        if isinstance(ziel, int):
            linear = ziel
        elif ziel is not None:
            adresse = ziel
        params = {"einmalig": bool(einmalig)}
        if adresse is not None:
            params["adresse"] = adresse
        if linear is not None:
            params["linear"] = linear
        if bedingung is not None:
            params["bedingung"] = bedingung
        return self.call("emu.break_add", **params)

    def break_remove(self, ziel=None, all=False, linear=None):
        """ziel wie bei break_add (int = linear, str = Adressform); linear= wie bei break_add."""
        if all:
            return self.call("emu.break_remove", all=True)
        if isinstance(ziel, int):
            linear = ziel
        if linear is not None:
            return self.call("emu.break_remove", linear=linear)
        return self.call("emu.break_remove", adresse=ziel)

    def break_int_add(self, nummer, ah=None, al=None):
        """Haelt vor INT nummer (optional nur bei passendem AH/AL). Werte als int oder Hex-String."""
        params = {"nummer": _hex2(nummer)}
        if ah is not None:
            params["ah"] = _hex2(ah)
        if al is not None:
            params["al"] = _hex2(al)
        return self.call("emu.break_int_add", **params)

    def break_int_remove(self, nummer=None, ah=None, al=None, all=False):
        if all:
            return self.call("emu.break_int_remove", all=True)
        params = {"nummer": _hex2(nummer)}
        if ah is not None:
            params["ah"] = _hex2(ah)
        if al is not None:
            params["al"] = _hex2(al)
        return self.call("emu.break_int_remove", **params)

    def break_list(self):
        return self.call("emu.break_list")

    # --- Waechter ---
    def watch_add(self, adresse, laenge=1, art="schreiben", aktion="anhalten"):
        """Speicher-Waechter auf einen linearen Bereich. art: lesen|schreiben|beides;
        aktion: anhalten (Halt nach dem Zugriff, Status stop_watch) oder protokollieren (Zugriffslog)."""
        return self.call("watch.add", adresse=adresse, laenge=laenge, art=art, aktion=aktion)

    def watch_remove(self, id=None, all=False):
        if all:
            return self.call("watch.remove", all=True)
        return self.call("watch.remove", id=id)

    def watch_list(self):
        return self.call("watch.list")

    def watch_log(self, name="zugriffe", leeren=False):
        """Schreibt das Zugriffslog nach <name>.jsonl (Antwort: pfad, anzahl, voll)."""
        info = self.call("watch.log", name=name, leeren=bool(leeren))
        info["sha256"] = hashlib.sha256(Path(info["pfad"]).read_bytes()).hexdigest()
        return info

    def watch_clear_log(self, max_eintraege=None):
        if max_eintraege is None:
            return self.call("watch.clear_log")
        return self.call("watch.clear_log", max_eintraege=int(max_eintraege))

    # --- CPU ---
    def registers(self):
        return self.call("cpu.registers")

    def set_register(self, name, value):
        """value: int oder Hex-String wie in den Antworten ("00000050", "0x50")."""
        if isinstance(value, str):
            try:
                value = int(value, 16)
            except ValueError:
                raise DmcpError("bad_argument", f"Registerwert ist keine Hexzahl: {value!r}") from None
        return self.call("cpu.set_register", name=name, value=value)

    # --- Speicher ---
    def mem_read_info(self, addr, length):
        return self.call("mem.read", adresse=addr, laenge=length)

    def mem_read(self, addr, length):
        return bytes.fromhex(self.mem_read_info(addr, length)["hex"])

    def mem_write(self, addr, data):
        return self.call("mem.write", adresse=addr, hex=bytes(data).hex().upper())

    def mem_dump(self, addr, length, name):
        info = self.call("mem.dump", adresse=addr, laenge=length, name=name)
        info["sha256"] = hashlib.sha256(Path(info["pfad"]).read_bytes()).hexdigest()
        return info

    def mem_search(self, addr, length, pattern, max_hits=100):
        if isinstance(pattern, str):
            result = self.call("mem.search", adresse=addr, laenge=length, text=pattern, max_treffer=max_hits)
        else:
            result = self.call("mem.search", adresse=addr, laenge=length, hex=bytes(pattern).hex().upper(),
                               max_treffer=max_hits)
        return result["treffer"]

    def descriptors(self, table="gdt", selector=None):
        params = {"tabelle": table}
        if selector is not None:
            params["selektor"] = selector
        return self.call("cpu.descriptors", **params)

    def translate(self, addr):
        return self.call("cpu.translate", adresse=addr)

    def paging(self, addr, length=0x1000):
        return self.call("cpu.paging", adresse=addr, laenge=length)

    def disassemble(self, adresse=None, anzahl=10, bits=None, name=None):
        """Disassembly ab adresse (Standard: aktueller Befehl). Bis 200 Zeilen in der Antwort; mit name bis
        100 000 Zeilen als <name>.jsonl (Antwort: pfad). Zeilen: adr, bytes, laenge, text, ziel (direkte Spruenge)."""
        params = {"anzahl": anzahl}
        if adresse is not None:
            params["adresse"] = adresse
        if bits is not None:
            params["bits"] = bits
        if name is not None:
            params["name"] = name
        return self.call("cpu.disassemble", **params)

    # --- Module ---
    def modules(self):
        return self.call("modules.list")["module"]

    def set_segments(self, modul, segmente):
        """segmente: [{"name", "va_start", "va_ende", "basis_linear"}] als Hex-Strings; linear = basis_linear + va."""
        return self.call("modules.set_segments", modul=modul, segmente=list(segmente))

    def clear_modules(self, modul=None):
        if modul is None:
            return self.call("modules.clear", alle=True)
        return self.call("modules.clear", modul=modul)

    # --- Eingabe ---
    def key(self, name, ms=60):
        return self.call("input.key", taste=name, dauer_ms=ms)

    def type_text(self, text, layout="de", gap_ms=40):
        return self.call("input.type", text=text, layout=layout, abstand_ms=gap_ms)

    def input_pending(self):
        return self.call("input.pending")

    def wait_input(self, timeout_s=10.0):
        end = time.monotonic() + timeout_s
        while self.input_pending()["ausstehend"] > 0:
            if time.monotonic() > end:
                raise DmcpError("timeout", "Eingaben wurden nicht rechtzeitig abgearbeitet (Emulator pausiert?)")
            time.sleep(0.02)

    def release_all(self):
        return self.call("input.release_all")

    def own_input(self, on):
        return self.call("input.own", an=bool(on))

    def mouse_move(self, x, y, coords="spiel"):
        return self.call("input.mouse_move", x=x, y=y, koordinaten=coords)

    def mouse_move_rel(self, dx, dy):
        return self.call("input.mouse_move_rel", dx=dx, dy=dy)

    def mouse_button(self, button, down):
        return self.call("input.mouse_button", knopf=button, gedrueckt=bool(down))

    def mouse_click(self, x=None, y=None, button="links", hold_ms=80):
        if x is not None and y is not None:
            self.mouse_move(x, y)
        self.mouse_button(button, True)
        time.sleep(hold_ms / 1000.0)
        return self.mouse_button(button, False)

    def mouse_wheel(self, steps):
        return self.call("input.mouse_wheel", schritte=steps)

    def mouse_state(self):
        return self.call("input.mouse_state")

    def mouse_move_program(self, x, y, x_adresse, y_adresse, breite=2, versuche=12, toleranz=0, warte_s=0.06):
        """Bewegt die Maus, bis die Positionsvariablen des Programms (x_adresse/y_adresse, je `breite` Byte,
        little endian, vorzeichenlos) x/y zeigen. Fuer Programme mit eigenem Maus-Handler, deren Cursor nicht
        der INT-33h-Position folgt (z. B. DOS-Extender-Spiele). Regelt mit mouse_move_rel nach und lernt den Faktor
        Programmeinheiten pro Bewegungsschritt aus der beobachteten Wirkung."""
        def read():
            px = int.from_bytes(self.mem_read(x_adresse, breite), "little")
            py = int.from_bytes(self.mem_read(y_adresse, breite), "little")
            return px, py

        def steps(delta, factor):
            if delta == 0:
                return 0
            step = round(delta / factor)
            return step if step != 0 else (1 if delta > 0 else -1)

        def close(delta, factor):
            # Rest bis zur halben Schrittweite ist mit diesem Programm nicht genauer erreichbar
            return abs(delta) <= toleranz or abs(delta) <= abs(factor) / 2

        def result(px, py, attempts, fx, fy):
            res = {"x": px, "y": py, "versuche": attempts, "faktor": [fx, fy]}
            if (px, py) != (x, y) and (abs(x - px) > toleranz or abs(y - py) > toleranz):
                res["hinweis"] = (f"Ziel mit Schrittweite {max(abs(fx), abs(fy)):g} nicht genau erreichbar "
                                  f"(Rest {x - px},{y - py})")
            return res

        def probe(delta, factor, done):
            # Ein Rest unter der halben gelernten Schrittweite ist nur dann unerreichbar, wenn auch ein einzelner
            # Schritt so weit springt: Programme mit Beschleunigung lernen aus grossen Bewegungen einen zu grossen
            # Faktor. Darum einmal je Achse einen Einzelschritt probieren (danach wird der Faktor neu gelernt).
            return not done and delta != 0 and abs(delta) > toleranz and abs(factor) > 1 and close(delta, factor)

        fx = fy = 1.0
        probed_x = probed_y = False
        px, py = read()
        for attempt in range(versuche):
            dx, dy = x - px, y - py
            px_probe, py_probe = probe(dx, fx, probed_x), probe(dy, fy, probed_y)
            if close(dx, fx) and close(dy, fy) and not (px_probe or py_probe):
                return result(px, py, attempt, fx, fy)
            sx = (1 if dx > 0 else -1) if px_probe else (0 if close(dx, fx) else steps(dx, fx))
            sy = (1 if dy > 0 else -1) if py_probe else (0 if close(dy, fy) else steps(dy, fy))
            probed_x, probed_y = probed_x or px_probe, probed_y or py_probe
            self.mouse_move_rel(sx, sy)
            time.sleep(warte_s)
            nx, ny = read()
            if sx and nx != px:
                fx = min(10.0, max(0.1, (nx - px) / sx))
            if sy and ny != py:
                fy = min(10.0, max(0.1, (ny - py) / sy))
            px, py = nx, ny
        if close(x - px, fx) and close(y - py, fy):
            return result(px, py, versuche, fx, fy)
        raise DmcpError("timeout", f"Programmposition {px},{py} statt {x},{y} nach {versuche} Versuchen "
                                   f"(Faktor {fx:.2f}/{fy:.2f}; am Rand oder falsche Adressen?)")

    # --- Bild ---
    def screenshot(self, name="screen", rect=None):
        params = {"name": name}
        if rect is not None:
            x, y, w, h = rect
            params.update(x=x, y=y, breite=w, hoehe=h)
        info = self.call("screen.capture", **params)
        info["sha256"] = hashlib.sha256(Path(info["pfad"]).read_bytes()).hexdigest()
        return info

    def screen_text(self):
        return self.call("screen.text")

    def video_state(self):
        return self.call("screen.video_state")

    # --- Ereignisse ---
    def events_start(self, typen=None, int_filter=None, int_modus=None, mit_rueckgabe=False, max_bytes=None,
                     name="events", datei_vorschau=None):
        """int_filter: [{"nummer": "21", "ah": "3D"}, ...] (Hex-Strings); Standard 21h, 2Fh, 31h, 33h.
        datei_vorschau: Bytes der uebertragenen Daten im Ereignis datei (Standard 16, 0 = aus)."""
        params = {"name": name, "mit_rueckgabe": bool(mit_rueckgabe)}
        if typen is not None:
            params["typen"] = list(typen)
        if int_filter is not None:
            params["int_filter"] = list(int_filter)
        if int_modus is not None:
            params["int_modus"] = int_modus
        if max_bytes is not None:
            params["max_bytes"] = int(max_bytes)
        if datei_vorschau is not None:
            params["datei_vorschau"] = int(datei_vorschau)
        return self.call("events.start", **params)

    def events_stop(self):
        return self.call("events.stop")

    def events_status(self):
        return self.call("events.status")

    def events_read(self, seit_nr=0, max=50):
        return self.call("events.read", seit_nr=seit_nr, max=max)

    def wait_event(self, predicate, timeout_s=15.0, seit_nr=0):
        """Liest neue Ereignisse, bis predicate(ereignis) wahr ist, und gibt dieses Ereignis zurueck."""
        end = time.monotonic() + timeout_s
        nr = seit_nr
        while True:
            batch = self.events_read(seit_nr=nr, max=500)
            for event in batch["ereignisse"]:
                if predicate(event):
                    return event
            if batch["ereignisse"]:
                nr = batch["ereignisse"][-1]["nr"]
            elif time.monotonic() > end:
                raise DmcpError("timeout", "erwartetes Ereignis kam nicht rechtzeitig")
            else:
                time.sleep(0.05)

    # --- Coverage ---
    def coverage_start(self, max_bloecke=None, fortsetzen=False, hits=False):
        params = {"fortsetzen": bool(fortsetzen), "hits": bool(hits)}
        if max_bloecke is not None:
            params["max_bloecke"] = int(max_bloecke)
        return self.call("coverage.start", **params)

    def coverage_stop(self):
        return self.call("coverage.stop")

    def coverage_status(self):
        return self.call("coverage.status")

    def coverage_save(self, name="coverage"):
        info = self.call("coverage.save", name=name)
        info["sha256"] = hashlib.sha256(Path(info["pfad"]).read_bytes()).hexdigest()
        return info

    # --- Kontrollfluss ---
    def flow_start(self, indirekt=True, ring=True, ring_groesse=4096, mit_returns=False, max_paare=None):
        params = {"indirekt": bool(indirekt), "ring": bool(ring), "ring_groesse": int(ring_groesse),
                  "mit_returns": bool(mit_returns)}
        if max_paare is not None:
            params["max_paare"] = int(max_paare)
        return self.call("flow.start", **params)

    def flow_stop(self):
        return self.call("flow.stop")

    def flow_status(self):
        return self.call("flow.status")

    def flow_save(self, name="indirekt"):
        return self.call("flow.save", name=name)

    def flow_recent(self, anzahl=100, name="recent"):
        """Schreibt die letzten `anzahl` Kontrollflusswechsel (aelteste zuerst) nach <name>.jsonl."""
        return self.call("flow.recent", anzahl=anzahl, name=name)

    # --- Aufrufprotokoll (D29) ---
    def calls_start(self, ziele=None, bereiche=None, module=None, ints=None, stack_bytes=None, max_je_ziel=1000,
                    max_bytes=None, name="calls", zustaende=0, max_zustaende=1000):
        """Zeichnet jeden Aufruf der gefilterten Ziele nach <name>.jsonl auf (ein Eintrag je Aufruf, beim
        Ruecksprung). ziele: Adresstexte/Symbole; bereiche: [{"von": ..., "bis": ...}] (bis einschliesslich);
        module: Modulnamen; ints: [{"nummer": "21", "ah": "3F"}]. Mindestens ein Filter ist Pflicht.
        zustaende=N: Speicherstand am Eintritt der ersten N Aufrufe je Ziel (Feld zustand, fuer func_run),
        hoechstens max_zustaende je Lauf."""
        params = {"name": name, "max_je_ziel": int(max_je_ziel)}
        if zustaende != 0:
            params["zustaende"] = int(zustaende)
        if max_zustaende != 1000:
            params["max_zustaende"] = int(max_zustaende)
        if ziele is not None:
            params["ziele"] = list(ziele)
        if bereiche is not None:
            params["bereiche"] = list(bereiche)
        if module is not None:
            params["module"] = list(module)
        if ints is not None:
            params["ints"] = list(ints)
        if stack_bytes is not None:
            params["stack_bytes"] = int(stack_bytes)
        if max_bytes is not None:
            params["max_bytes"] = int(max_bytes)
        return self.call("calls.start", **params)

    def calls_stop(self):
        """Beendet die Aufzeichnung: offene Aufrufe als offen=true, Begleitdatei <name>.json (json_pfad)."""
        return self.call("calls.stop")

    def calls_status(self):
        return self.call("calls.status")

    # --- Funktion einzeln ausfuehren (D31) ---
    def func_run(self, zustand, schreiben=None, regs=None, stack=None, irqs=False, max_befehle=None, name="func",
                 timeout_s=60.0):
        """Laedt einen Speicherstand aus calls_start(zustaende=N) (Feld zustand), setzt optional Register (regs
        {"eax": "0x10"}), Stackbytes ab SS:ESP (stack, Hex) und Bytes (schreiben [{"adresse", "hex"}], z. B.
        Nachbau-Code), laeuft bis zum Ruecksprung und liefert Register, geaenderten Speicher und INT-Aufrufe.
        irqs=False haelt Hardware-Interrupts waehrend des Laufs zurueck. Zeitablauf: ende "abgebrochen" + hinweis.
        Der Emulator bleibt danach angehalten."""
        params = {"zustand": str(zustand), "irqs": bool(irqs), "name": name, "timeout_ms": 60000}
        if schreiben is not None:
            params["schreiben"] = [{"adresse": s["adresse"],
                                    "hex": s["hex"] if isinstance(s["hex"], str) else bytes(s["hex"]).hex().upper()}
                                   for s in schreiben]
        if regs is not None:
            params["regs"] = {k: (v if isinstance(v, str) else f"0x{v:X}") for k, v in regs.items()}
        if stack is not None:
            params["stack"] = stack if isinstance(stack, str) else bytes(stack).hex().upper()
        if max_befehle is not None:
            params["max_befehle"] = int(max_befehle)
        self._call_when_ready("func.start", params)
        end = time.monotonic() + timeout_s
        while True:
            result = self.call("func.result")
            if not result.get("laeuft"):
                return result
            if time.monotonic() > end:
                self.call("emu.pause")
                result = self.call("func.result")
                # Nur ein Halt durch unser emu.pause ist der Zeitablauf; kam der Ruecksprung kurz davor, bleibt er.
                if result.get("ende") == "unterbrochen" and result.get("stop_reason") == "pause":
                    result["ende"] = "abgebrochen"
                    result["stop_reason"] = None
                    result["hinweis"] = "timeout_s erreicht"
                return result
            time.sleep(0.02)

    # --- Symbole ---
    def symbols_load(self, path, basis_linear=None):
        """Symboldatei (Format decomp-symbole/1) laden.
        basis_linear: Abbildung, falls die Datei keine mitbringt (linear = basis_linear + va)."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DmcpError("bad_argument", f"Symboldatei nicht lesbar: {exc}") from exc
        if not isinstance(data, dict) or data.get("format") != "decomp-symbole/1":
            raise DmcpError("bad_argument", "Symboldatei hat nicht format=decomp-symbole/1")
        params = {key: data[key] for key in ("modul", "adressraum", "symbole", "segmente", "basis_linear")
                  if key in data}
        if basis_linear is not None:
            params["basis_linear_geladen"] = basis_linear if isinstance(basis_linear, str) else f"0x{basis_linear:X}"
        if data.get("sha256") and not isinstance(data["sha256"], str):
            raise DmcpError("bad_argument", "sha256 der Symboldatei muss ein String sein")
        result = self.call("symbols.set", **params)
        if data.get("sha256"):
            result["hinweise"] = list(result["hinweise"]) + self._check_sha256(data["modul"], data["sha256"])
        return result

    def _check_sha256(self, modul, expected):
        module = next((m for m in self.modules() if m["modul"] == modul.upper()), None)
        if module is None or "datei" not in module:
            return [f"sha256 nicht pruefbar: {modul} wurde in dieser Instanz noch nicht gestartet"]
        host = self.inst.host_path(module["datei"])
        if host is None or not host.exists():
            return [f"sha256 nicht pruefbar: {module['datei']} liegt in keinem bekannten Mount"]
        actual = hashlib.sha256(host.read_bytes()).hexdigest()
        if actual.lower() != expected.lower():
            return [f"sha256 passt nicht: Symboldatei {expected[:12]}..., geladenes Programm {actual[:12]}..."]
        return []

    def symbol_lookup(self, name=None, adresse=None):
        if name is not None:
            return self.call("symbols.lookup", name=name)
        return self.call("symbols.lookup", adresse=adresse)

    def symbols_clear(self, modul):
        return self.call("symbols.clear", modul=modul)

    # --- Momentaufnahme ---
    def snapshot(self, label, mit_erweiterung=False, bis=None):
        """Haelt kurz an (falls noetig), sichert Speicher/Register/Deskriptoren/Paging/Bild, laeuft weiter.
        bis="pmode": erst im Protected Mode anhalten (DOS-Extender stehen oft im Real Mode, dann fehlen die
        linearen Bereiche oberhalb des RAMs; das Ergebnis traegt dann `hinweise`).
        Ist der Emulator schon angehalten, wird sofort aufgenommen; `bis` gilt dann nicht."""
        from . import snapshot
        return snapshot.take(self, label, mit_erweiterung=mit_erweiterung, bis=bis)

    # --- Speicherstaende ---
    def state_save(self, name="state"):
        """Vollstaendiger Emulatorzustand als <name>.sav (+ Begleitdatei <name>.json) im Ausgabeordner.
        Haltepunkte, Waechter und Aufzeichnungen gehoeren nicht dazu."""
        info = self.call("state.save", name=name, timeout_ms=60000)
        info["sha256"] = hashlib.sha256(Path(info["pfad"]).read_bytes()).hexdigest()
        return info

    def state_load(self, name=None, pfad=None, ohne_pruefung=False):
        """Laedt einen Speicherstand (name im Ausgabeordner oder pfad). Prueft vorher Datei und Begleitdatei."""
        params = {"ohne_pruefung": bool(ohne_pruefung), "timeout_ms": 60000}
        if pfad is not None:
            params["pfad"] = str(pfad)
        else:
            params["name"] = name or "state"
        return self._call_when_ready("state.load", params)

    def _call_when_ready(self, method, params, timeout_s=30.0):
        """Laden vor dem Hochlaufen der DOS-Shell bringt den Emulator zum Absturz; er lehnt dann mit not_ready ab.
        Laeuft der Emulator, wird gewartet; ist er angehalten, kommt der Fehler sofort (er wird nie fertig)."""
        end = time.monotonic() + timeout_s
        while True:
            try:
                return self.call(method, **params)
            except DmcpError as exc:
                if exc.code != "not_ready" or self.status()["paused"] or time.monotonic() > end:
                    raise
            time.sleep(0.1)

    # --- DOS ---
    def dos_programs(self):
        """PSP-Kette ab dem aktuellen Programm (aktuelles zuerst) mit Namen, Befehlszeile, Load-Segment."""
        return self.call("dos.programs")

    def dos_mcb(self):
        """Speicherblock-Kette (MCB) ab dem ersten Block."""
        return self.call("dos.mcb")

    def dos_files(self):
        """Offene Dateien (System-Dateitabelle) und die Handles des aktuellen Programms."""
        return self.call("dos.files")

    def dos_target(self):
        """Zielprogramm (zuletzt mit EXEC 4B00 gestartet): PSP, Load-Segment, Einsprung, Stack, Relocation."""
        return self.call("dos.target")
