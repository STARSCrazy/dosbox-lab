# SPDX-License-Identifier: GPL-2.0-or-later
"""MCP-Server (stdio) fuer dosbox-lab. Start: python -m dosbox_lab.mcp_server"""
import json
from typing import Optional

from mcp.server.fastmcp import FastMCP, Image

from .api import Dosbox
from .batch import run_batch as _run_batch
from .cli import newest_instance
from .errors import DmcpError
from .instance import Mount

mcp = FastMCP("dosbox-lab")
_boxes: dict = {}


def _box(instanz: Optional[str]) -> Dosbox:
    inst_id = instanz or newest_instance()
    if inst_id is None:
        raise DmcpError("bad_argument", "keine laufende Instanz - erst dosbox_start aufrufen")
    if inst_id not in _boxes:
        _boxes[inst_id] = Dosbox.attach(inst_id)
    return _boxes[inst_id]


@mcp.tool()
def dosbox_start(laufwerk_c: str, befehle: list[str] = [], cdrom_d: Optional[str] = None,
                 headless: bool = False) -> dict:
    """Startet eine Emulator-Instanz. laufwerk_c: Host-Ordner fuer C:. befehle: Autoexec-Zeilen
    (z. B. ["SPIEL.EXE"]). cdrom_d: optionaler Host-Ordner als CD-Laufwerk D:. headless: ohne Fenster."""
    mounts = [Mount("C", laufwerk_c)] + ([Mount("D", cdrom_d, cdrom=True)] if cdrom_d else [])
    box = Dosbox.start(mounts=mounts, autoexec=befehle, headless=headless)
    _boxes[box.id] = box
    return {"instanz": box.id, "ausgabeordner": str(box.inst.outdir)}


@mcp.tool()
def dosbox_stop(instanz: Optional[str] = None) -> dict:
    """Beendet eine Instanz."""
    box = _box(instanz)
    box.stop()
    _boxes.pop(box.id, None)
    return {"gestoppt": box.id}


@mcp.tool()
def dosbox_list() -> dict:
    """Listet bekannte Instanzen (laufend und beendet)."""
    from .cli import _instances
    return {"instanzen": [{k: v for k, v in i.items() if not k.startswith("_")} for i in _instances()]}


@mcp.tool()
def run_batch(laufwerk_c: str, befehl: str, timeout_s: float = 60.0, laufwerk_d: Optional[str] = None,
              cdrom_d: bool = False, max_ausgabe: int = 16 * 1024 * 1024) -> dict:
    """Fuehrt einen DOS-Befehl (Programm, .BAT oder Shell-Befehl) in einer eigenen Instanz ohne Fenster aus und
    beendet sie danach. laufwerk_c: Host-Ordner fuer C: (beschreibbar, aktuelles Laufwerk). laufwerk_d: optionaler
    Ordner fuer D: (cdrom_d=True: nur lesbar). Ergebnis: status (fertig/timeout/shell_beendet/abgestuerzt;
    shell_beendet = EXIT im Befehl), exit_code (Errorlevel), stdout/stderr als Dateipfade plus Anfang als Text, screen (screen.txt oder screen.png), dateien (neu/geaendert/
    geloescht in C:/D:), laufzeit_s. Programme, die auf eine Taste warten, laufen in die Zeitgrenze."""
    mounts = [Mount("C", laufwerk_c)] + ([Mount("D", laufwerk_d, cdrom=cdrom_d)] if laufwerk_d else [])
    return _run_batch(mounts, befehl, timeout_s=timeout_s, max_ausgabe=max_ausgabe)


@mcp.tool()
def emu_status(instanz: Optional[str] = None) -> dict:
    """Pausiert? Warum angehalten? CS:EIP, Turbo, Protected Mode."""
    return _box(instanz).status()


@mcp.tool()
def emu_pause(bis: Optional[str] = None, timeout_s: float = 10.0, instanz: Optional[str] = None) -> dict:
    """Haelt die Emulation an. bis="pmode": erst beim ersten Befehl im Protected Mode (wartet bis timeout_s),
    noetig fuer Selektor-Adressen bei DOS-Extendern. Fuer andere Bedingungen emu_run_until nutzen."""
    return _box(instanz).pause(until=bis, timeout_s=timeout_s)


@mcp.tool()
def emu_continue(instanz: Optional[str] = None) -> dict:
    """Laesst die Emulation weiterlaufen."""
    return _box(instanz).resume()


@mcp.tool()
def emu_step(anzahl: int = 1, instanz: Optional[str] = None) -> dict:
    """Fuehrt im Pausenzustand genau `anzahl` Befehle aus."""
    return _box(instanz).step(anzahl)


@mcp.tool()
def emu_speed(modus: str, cycles: Optional[int] = None, instanz: Optional[str] = None) -> dict:
    """Geschwindigkeit: "normal", "turbo" oder "cycles" (dann cycles=100..2000000, feste Zyklen)."""
    return _box(instanz).speed(modus, cycles=cycles)


@mcp.tool()
def break_add(ziel: Optional[str] = None, bedingung: Optional[str] = None, einmalig: bool = False,
              linear: Optional[str] = None, instanz: Optional[str] = None) -> dict:
    """Code-Haltepunkt. ziel: jede Adressform ("0043:000406F1", "r:1234:0100", "l:010406F1",
    "va:PROGRAMM.EXE:406F1", "rel:...") oder ein Symbolname. bedingung: Ausdruck wie "ax==5 && zf".
    einmalig: nach dem ersten Halt entfernen. linear (Hex) nur fuer alte Aufrufer."""
    box = _box(instanz)
    if ziel is None and linear is not None:
        return box.break_add(int(linear, 16), bedingung=bedingung, einmalig=einmalig)
    return box.break_add(ziel, bedingung=bedingung, einmalig=einmalig)


@mcp.tool()
def break_remove(ziel: Optional[str] = None, alle: bool = False, linear: Optional[str] = None,
                 instanz: Optional[str] = None) -> dict:
    """Entfernt einen Haltepunkt (ziel wie bei break_add, oder linear als Hex) oder alle."""
    box = _box(instanz)
    if alle:
        return box.break_remove(all=True)
    return box.break_remove(int(linear, 16) if ziel is None and linear else ziel)


@mcp.tool()
def break_list(instanz: Optional[str] = None) -> dict:
    """Alle Haltepunkte mit Adresse, Bedingung, Trefferzahl; dazu die Interrupt-Haltepunkte."""
    return _box(instanz).break_list()


@mcp.tool()
def break_int_add(nummer: str, ah: Optional[str] = None, al: Optional[str] = None,
                  instanz: Optional[str] = None) -> dict:
    """Haelt vor einer INT-Instruktion (nummer/ah/al als Hex-Strings, z. B. "21", "4C")."""
    return _box(instanz).break_int_add(nummer, ah=ah, al=al)


@mcp.tool()
def break_int_remove(nummer: Optional[str] = None, ah: Optional[str] = None, al: Optional[str] = None,
                     alle: bool = False, instanz: Optional[str] = None) -> dict:
    """Entfernt einen Interrupt-Haltepunkt oder alle."""
    return _box(instanz).break_int_remove(nummer, ah=ah, al=al, all=alle)


@mcp.tool()
def emu_run_until(adresse: Optional[str] = None, pmode: Optional[bool] = None, paging: Optional[bool] = None,
                  cs: Optional[str] = None, cpl: Optional[int] = None, ausdruck: Optional[str] = None,
                  timeout_s: float = 10.0, instanz: Optional[str] = None) -> dict:
    """Laeuft, bis alle angegebenen Bedingungen zugleich gelten (mindestens ein Befehl). Beispiel:
    cs="0043", cpl=3. ausdruck: Register/Flags/Speicher, z. B. "eax==5 && byte[l:01000004]>3"."""
    return _box(instanz).run_until(adresse=adresse, pmode=pmode, paging=paging, cs=cs, cpl=cpl,
                                   ausdruck=ausdruck, timeout_s=timeout_s)


@mcp.tool()
def emu_step_over(timeout_s: float = 10.0, instanz: Optional[str] = None) -> dict:
    """Ein Schritt, CALL/INT/REP laufen durch bis zum Folgebefehl (Emulator muss pausiert sein)."""
    return _box(instanz).step_over(timeout_s=timeout_s)


@mcp.tool()
def emu_frame_step(anzahl: int = 1, timeout_s: float = 10.0, instanz: Optional[str] = None) -> dict:
    """Laeuft anzahl Bilder weiter und haelt dann an."""
    return _box(instanz).frame_step(anzahl, timeout_s=timeout_s)


@mcp.tool()
def emu_eval(ausdruck: str, instanz: Optional[str] = None) -> dict:
    """Wertet einen Ausdruck (Register, Flags, byte/word/dword[ADR]) auf dem aktuellen Zustand aus."""
    return _box(instanz).eval(ausdruck)


@mcp.tool()
def cpu_disassemble(adresse: Optional[str] = None, anzahl: int = 10, bits: Optional[int] = None,
                    name: Optional[str] = None, instanz: Optional[str] = None) -> dict:
    """Disassembly mit Symbolen und Sprungzielen ab adresse (Standard: aktueller Befehl). Bis 200 Zeilen;
    mehr nur mit name (dann Datei <name>.jsonl, Antwort pfad)."""
    return _box(instanz).disassemble(adresse, anzahl=anzahl, bits=bits, name=name)


@mcp.tool()
def watch_add(adresse: str, laenge: int = 1, art: str = "schreiben", aktion: str = "anhalten",
              instanz: Optional[str] = None) -> dict:
    """Speicher-Waechter auf einen linearen Bereich. art: lesen|schreiben|beides. aktion: anhalten
    (Halt nach dem Zugriff, Status stop_watch) oder protokollieren (Zugriffslog, siehe watch_log)."""
    return _box(instanz).watch_add(adresse, laenge=laenge, art=art, aktion=aktion)


@mcp.tool()
def watch_remove(id: Optional[int] = None, alle: bool = False, instanz: Optional[str] = None) -> dict:
    """Entfernt einen Waechter oder alle."""
    return _box(instanz).watch_remove(id, all=alle)


@mcp.tool()
def watch_list(instanz: Optional[str] = None) -> dict:
    """Waechter mit Trefferzahl und Zustand des Zugriffslogs."""
    return _box(instanz).watch_list()


@mcp.tool()
def watch_log(name: str = "zugriffe", leeren: bool = False, instanz: Optional[str] = None) -> dict:
    """Schreibt das Zugriffslog nach <name>.jsonl (Antwort: pfad, anzahl)."""
    return _box(instanz).watch_log(name, leeren=leeren)


@mcp.tool()
def watch_clear_log(max_eintraege: Optional[int] = None, instanz: Optional[str] = None) -> dict:
    """Leert das Zugriffslog; optional neue Groesse (1000..1000000 Eintraege)."""
    return _box(instanz).watch_clear_log(max_eintraege)


@mcp.tool()
def state_save(name: str = "state", instanz: Optional[str] = None) -> dict:
    """Speichert den vollstaendigen Emulatorzustand (<name>.sav + <name>.json im Ausgabeordner)."""
    return _box(instanz).state_save(name)


@mcp.tool()
def state_load(name: Optional[str] = None, pfad: Optional[str] = None, ohne_pruefung: bool = False,
               instanz: Optional[str] = None) -> dict:
    """Laedt einen Speicherstand (Haltepunkte, Waechter, Aufzeichnungen bleiben wie sie sind)."""
    return _box(instanz).state_load(name, pfad=pfad, ohne_pruefung=ohne_pruefung)


@mcp.tool()
def dos_programs(instanz: Optional[str] = None) -> dict:
    """PSP-Kette: aktuelles Programm zuerst, mit Namen, Befehlszeile, Load-Segment."""
    return _box(instanz).dos_programs()


@mcp.tool()
def dos_mcb(instanz: Optional[str] = None) -> dict:
    """DOS-Speicherblock-Kette (MCB)."""
    return _box(instanz).dos_mcb()


@mcp.tool()
def dos_files(instanz: Optional[str] = None) -> dict:
    """Offene Dateien und die Handles des aktuellen Programms."""
    return _box(instanz).dos_files()


@mcp.tool()
def dos_target(instanz: Optional[str] = None) -> dict:
    """Zielprogramm: PSP, Load-Segment, Einsprung, Stack, Relocation."""
    return _box(instanz).dos_target()


@mcp.tool()
def mouse_move_program(x: int, y: int, x_adresse: str, y_adresse: str, breite: int = 2, toleranz: int = 0,
                       instanz: Optional[str] = None) -> dict:
    """Bewegt die Maus, bis die Positionsvariablen des Programms x/y zeigen (fuer eigene Maus-Handler)."""
    return _box(instanz).mouse_move_program(x, y, x_adresse, y_adresse, breite=breite, toleranz=toleranz)


@mcp.tool()
def cpu_registers(instanz: Optional[str] = None) -> dict:
    """Alle Register inkl. Segmentbasen, CR0-CR4, Flags, Modus (real/pm/v86), Bitness, CPL, Paging."""
    return _box(instanz).registers()


@mcp.tool()
def cpu_set_register(name: str, wert: str, instanz: Optional[str] = None) -> dict:
    """Setzt eax..edi, ebp, esp, eip oder eflags (wert als Hex-String)."""
    return _box(instanz).set_register(name, int(wert, 16))


@mcp.tool()
def cpu_descriptors(tabelle: str = "gdt", selektor: Optional[str] = None, instanz: Optional[str] = None) -> dict:
    """GDT, LDT oder IDT als Liste (Protected Mode)."""
    return _box(instanz).descriptors(tabelle, int(selektor, 16) if selektor else None)


@mcp.tool()
def cpu_translate(adresse: str, instanz: Optional[str] = None) -> dict:
    """Rechnet eine Adresse in alle Formen um. Formen: "SSSS:OOOOOOOO", "r:SSSS:OOOO", "l:<hex>", "p:<hex>"."""
    return _box(instanz).translate(adresse)


@mcp.tool()
def cpu_paging(adresse: str, laenge: int = 4096, instanz: Optional[str] = None) -> dict:
    """Seitentabellen-Eintraege fuer einen linearen Bereich (CR3, praesent, physisch)."""
    return _box(instanz).paging(adresse, laenge)


@mcp.tool()
def mem_read(adresse: str, laenge: int, instanz: Optional[str] = None) -> dict:
    """Liest bis 4096 Byte als Hex. Fuer mehr: mem_dump."""
    return _box(instanz).mem_read_info(adresse, laenge)


@mcp.tool()
def mem_write(adresse: str, hex: str, instanz: Optional[str] = None) -> dict:
    """Schreibt Bytes (Hex-String)."""
    return _box(instanz).mem_write(adresse, bytes.fromhex(hex))


@mcp.tool()
def mem_dump(adresse: str, laenge: int, name: str, instanz: Optional[str] = None) -> dict:
    """Schreibt einen Speicherbereich in <name>.bin (+ .json); liefert Pfad, SHA-256, fehlende Seiten."""
    return _box(instanz).mem_dump(adresse, laenge, name)


@mcp.tool()
def mem_search(adresse: str, laenge: int, text: Optional[str] = None, hex: Optional[str] = None,
               max_treffer: int = 100, instanz: Optional[str] = None) -> dict:
    """Sucht Text oder Hex-Bytes in einem Bereich."""
    pattern = text if text is not None else bytes.fromhex(hex or "")
    return {"treffer": _box(instanz).mem_search(adresse, laenge, pattern, max_treffer)}


@mcp.tool()
def key(taste: str, dauer_ms: int = 60, instanz: Optional[str] = None) -> dict:
    """Drueckt eine Taste/Kombination im Emulator, z. B. "ENTER", "ESC", "F1", "ALT+X"."""
    return _box(instanz).key(taste, dauer_ms)


@mcp.tool()
def type_text(text: str, layout: str = "de", instanz: Optional[str] = None) -> dict:
    """Tippt Text im Emulator (layout "de" fuer keyb gr, sonst "us")."""
    return _box(instanz).type_text(text, layout)


@mcp.tool()
def mouse_move(x: float, y: float, koordinaten: str = "spiel", instanz: Optional[str] = None) -> dict:
    """Bewegt die Emulator-Maus auf Spielpixel (x, y). Die echte Maus bleibt unberuehrt."""
    return _box(instanz).mouse_move(x, y, koordinaten)


@mcp.tool()
def mouse_click(x: Optional[float] = None, y: Optional[float] = None, knopf: str = "links",
                instanz: Optional[str] = None) -> dict:
    """Klickt (optional vorher bewegen). knopf: links, rechts, mitte."""
    return _box(instanz).mouse_click(x, y, knopf)


@mcp.tool()
def mouse_button(knopf: str, gedrueckt: bool, instanz: Optional[str] = None) -> dict:
    """Maustaste druecken/loslassen (fuer Ziehen)."""
    return _box(instanz).mouse_button(knopf, gedrueckt)


@mcp.tool()
def mouse_wheel(schritte: int, instanz: Optional[str] = None) -> dict:
    """Mausrad drehen (negativ = hoch)."""
    return _box(instanz).mouse_wheel(schritte)


@mcp.tool()
def mouse_state(instanz: Optional[str] = None) -> dict:
    """INT-33h-Position, Knoepfe, Bereich."""
    return _box(instanz).mouse_state()


@mcp.tool()
def input_own(an: bool, instanz: Optional[str] = None) -> dict:
    """an=True: echte Maus/Tastatur im Emulatorfenster ignorieren (Standard). False: a human may play along."""
    return _box(instanz).own_input(an)


@mcp.tool()
def screenshot(name: str = "screen", x: Optional[int] = None, y: Optional[int] = None,
               breite: Optional[int] = None, hoehe: Optional[int] = None, anzeigen: bool = True,
               instanz: Optional[str] = None):
    """Screenshot als PNG (optional Ausschnitt). anzeigen=True liefert das Bild direkt mit."""
    rect = (x, y, breite, hoehe) if breite is not None else None
    info = _box(instanz).screenshot(name, rect)
    if anzeigen:
        return [json.dumps(info, ensure_ascii=False), Image(path=info["pfad"])]
    return info


@mcp.tool()
def screen_text(instanz: Optional[str] = None) -> dict:
    """Textmodus-Bildschirm als Zeilen."""
    return _box(instanz).screen_text()


@mcp.tool()
def video_state(instanz: Optional[str] = None) -> dict:
    """Videomodus, Typ, Aufloesung."""
    return _box(instanz).video_state()


@mcp.tool()
def events_start(typen: Optional[list[str]] = None, int_filter: Optional[list[dict]] = None,
                 int_modus: Optional[str] = None, mit_rueckgabe: bool = False, max_bytes: Optional[int] = None,
                 name: str = "events", datei_vorschau: Optional[int] = None, instanz: Optional[str] = None) -> dict:
    """Startet den Ereignisstrom nach <ausgabeordner>/<name>.jsonl. typen: exec, exit, int, int_ret, mode,
    new_code_selector, datei (Standard alle). datei_vorschau: Bytes Vorschau im Ereignis datei. int_filter: [{"nummer": "21", "ah": "3D"}] (Standard 21h, 2Fh, 31h, 33h).
    int_modus: alle|pm|real. mit_rueckgabe: int_ret fuer jeden geloggten Interrupt (INT 31h immer)."""
    return _box(instanz).events_start(typen, int_filter, int_modus, mit_rueckgabe, max_bytes, name, datei_vorschau)


@mcp.tool()
def events_stop(instanz: Optional[str] = None) -> dict:
    """Beendet den Ereignisstrom; die Datei ist danach vollstaendig."""
    return _box(instanz).events_stop()


@mcp.tool()
def events_status(instanz: Optional[str] = None) -> dict:
    """Aktiv? Anzahl, Bytes, Grenze, Datei."""
    return _box(instanz).events_status()


@mcp.tool()
def events_read(seit_nr: int = 0, max: int = 50, instanz: Optional[str] = None) -> dict:
    """Neue Ereignisse nach seit_nr (hoechstens 500, aus den letzten 10000). Massgeblich ist die Datei."""
    return _box(instanz).events_read(seit_nr, max)


@mcp.tool()
def coverage_start(max_bloecke: Optional[int] = None, fortsetzen: bool = False, hits: bool = False,
                   instanz: Optional[str] = None) -> dict:
    """Startet die Code-Abdeckung (Bit pro Befehlsstart, linear). hits: Trefferzaehler pro Basic Block."""
    return _box(instanz).coverage_start(max_bloecke, fortsetzen, hits)


@mcp.tool()
def coverage_stop(instanz: Optional[str] = None) -> dict:
    """Haelt die Code-Abdeckung an (Daten bleiben)."""
    return _box(instanz).coverage_stop()


@mcp.tool()
def coverage_save(name: str = "coverage", instanz: Optional[str] = None) -> dict:
    """Schreibt <name>.bin (DMCV) + <name>.json (+ <name>_hits.jsonl). Gibt Pfade zurueck."""
    return _box(instanz).coverage_save(name)


@mcp.tool()
def coverage_status(instanz: Optional[str] = None) -> dict:
    """Aktiv? Bloecke, Grenze."""
    return _box(instanz).coverage_status()


@mcp.tool()
def flow_start(indirekt: bool = True, ring: bool = True, ring_groesse: int = 4096, mit_returns: bool = False,
               instanz: Optional[str] = None) -> dict:
    """Startet die Kontrollfluss-Aufzeichnung: indirekte Spruenge/Aufrufe und Ringpuffer der letzten Wechsel."""
    return _box(instanz).flow_start(indirekt, ring, ring_groesse, mit_returns)


@mcp.tool()
def flow_stop(instanz: Optional[str] = None) -> dict:
    """Haelt die Kontrollfluss-Aufzeichnung an (Daten bleiben)."""
    return _box(instanz).flow_stop()


@mcp.tool()
def flow_save(name: str = "indirekt", instanz: Optional[str] = None) -> dict:
    """Schreibt die indirekten Paare nach <name>.jsonl."""
    return _box(instanz).flow_save(name)


@mcp.tool()
def flow_recent(anzahl: int = 100, name: str = "recent", instanz: Optional[str] = None) -> dict:
    """Schreibt die letzten Kontrollflusswechsel (aelteste zuerst) nach <name>.jsonl, z. B. vor einem Haltepunkt."""
    return _box(instanz).flow_recent(anzahl, name)


@mcp.tool()
def flow_status(instanz: Optional[str] = None) -> dict:
    """Aktiv? Paare, Ringgroesse, Befehlszaehler."""
    return _box(instanz).flow_status()


@mcp.tool()
def calls_start(ziele: Optional[list[str]] = None, bereiche: Optional[list[dict]] = None,
                module: Optional[list[str]] = None, ints: Optional[list[dict]] = None,
                stack_bytes: Optional[int] = None, max_je_ziel: int = 1000, max_bytes: Optional[int] = None,
                name: str = "calls", zustaende: int = 0, max_zustaende: int = 1000,
                instanz: Optional[str] = None) -> dict:
    """Aufrufprotokoll (D29) nach <ausgabeordner>/<name>.jsonl: je Aufruf Register/Flags/Stack beim Eintritt und
    Register beim Ruecksprung. Mindestens ein Filter: ziele (Adressen/Symbole), bereiche [{"von","bis"}],
    module (Modulnamen), ints [{"nummer": "21", "ah": "3F"}]. stack_bytes Standard 32 (16 Bit)/64 (32 Bit).
    zustaende=N: Speicherstand am Eintritt der ersten N Aufrufe je Ziel (Feld zustand, fuer func_run).
    Bremst den Emulator (Befehls-Hook), nach der Analyse calls_stop."""
    return _box(instanz).calls_start(ziele, bereiche, module, ints, stack_bytes, max_je_ziel, max_bytes, name,
                                     zustaende, max_zustaende)


@mcp.tool()
def func_run(zustand: str, schreiben: Optional[list[dict]] = None, regs: Optional[dict] = None,
             stack: Optional[str] = None, irqs: bool = False, max_befehle: Optional[int] = None,
             name: str = "func", timeout_s: float = 60.0, instanz: Optional[str] = None) -> dict:
    """Fuehrt einen aufgezeichneten Aufruf neu aus (D31): laedt zustand (Feld zustand aus calls.jsonl, entsteht
    mit calls_start(zustaende=N)), setzt optional regs {"eax": "0x10"}, stack (Hex ab SS:ESP) und schreiben
    [{"adresse": "l:...", "hex": "..."}] (z. B. Nachbau-Bytes), laeuft bis zum Ruecksprung. Ergebnis: ende,
    regs_aus, geaendert (Speicherbereiche mit vorher/nachher), ints. Hardware-IRQs ruhen, ausser irqs=true.
    Der Emulator bleibt danach angehalten."""
    return _box(instanz).func_run(zustand, schreiben, regs, stack, irqs, max_befehle, name, timeout_s)


@mcp.tool()
def calls_stop(instanz: Optional[str] = None) -> dict:
    """Beendet das Aufrufprotokoll; offene Aufrufe als offen=true, Begleitdatei <name>.json (json_pfad)."""
    return _box(instanz).calls_stop()


@mcp.tool()
def calls_status(instanz: Optional[str] = None) -> dict:
    """Aktiv? Geschrieben, offen, weggelassen, Bytes, Datei."""
    return _box(instanz).calls_status()


@mcp.tool()
def modules_list(instanz: Optional[str] = None) -> dict:
    """Bekannte Module: EXEC-Daten (PSP, Load-Segment, rel_basis), va-Abbildung mit Herkunft, Symbolzahl."""
    return {"module": _box(instanz).modules()}


@mcp.tool()
def modules_set_segments(modul: str, segmente: list[dict], instanz: Optional[str] = None) -> dict:
    """va-Abbildung setzen: [{"name","va_start","va_ende","basis_linear"}] (Hex-Strings), linear = basis_linear + va."""
    return _box(instanz).set_segments(modul, segmente)


@mcp.tool()
def symbols_load(pfad: str, basis_linear: Optional[str] = None, instanz: Optional[str] = None) -> dict:
    """Laedt eine Symboldatei (Format decomp-symbole/1). Danach gehen Namen als Adressen (auch MODUL!name)."""
    return _box(instanz).symbols_load(pfad, basis_linear)


@mcp.tool()
def symbol_lookup(name: Optional[str] = None, adresse: Optional[str] = None, instanz: Optional[str] = None) -> dict:
    """Name -> Adresse oder Adresse -> Symbol (name+offset)."""
    return _box(instanz).symbol_lookup(name, adresse)


@mcp.tool()
def snapshot(label: str, mit_erweiterung: bool = False, bis: Optional[str] = None,
             instanz: Optional[str] = None) -> dict:
    """Momentaufnahme in ein eigenes Verzeichnis: Speicher (1 MB + Modulbereiche), Register, Deskriptoren,
    Paging, Bild, meta.json. mit_erweiterung: zusaetzlich der ganze Speicher ab 1 MB.
    bis="pmode": erst im Protected Mode anhalten (fuer DOS-Extender, sonst fehlen lineare Bereiche)."""
    return _box(instanz).snapshot(label, mit_erweiterung, bis=bis)


def main():
    """Einstieg fuer den Konsolenbefehl dosbox-lab-mcp (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
