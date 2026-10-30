"""Kommandozeile: duenne Huelle ueber die Bibliothek. Ausgabe immer JSON."""
import argparse
import json
import sys

from .api import Dosbox
from .batch import run_batch
from .errors import DmcpError
from .instance import RUNS, Mount, _pid_alive
from .scenario import play_scenario


def _instances():
    found = []
    for info_path in RUNS.glob("*/instance.json"):
        try:
            info = json.loads(info_path.read_text(encoding="utf-8"))
            info["laeuft"] = _pid_alive(info["pid"])
            info["_mtime"] = info_path.stat().st_mtime
        except (OSError, ValueError, KeyError):
            continue  # kaputte/halbfertige Altlast ueberspringen
        found.append(info)
    return sorted(found, key=lambda i: i["_mtime"], reverse=True)


def newest_instance():
    for info in _instances():
        if info["laeuft"]:
            return info["id"]
    return None


def _num(text):
    return int(text, 16)


def _int_rules(values):
    rules = []
    for value in values or []:
        num, _, ah = value.partition(":")
        int(num, 16)  # ValueError -> bad_argument (main)
        rule = {"nummer": num}
        if ah:
            int(ah, 16)
            rule["ah"] = ah
        rules.append(rule)
    return rules or None


def build_parser():
    p = argparse.ArgumentParser(prog="dosbox_mcp")
    # --id vor oder nach dem Befehl erlaubt (SUPPRESS: Unterparser ueberschreibt nicht mit None)
    idopt = argparse.ArgumentParser(add_help=False)
    idopt.add_argument("--id", default=argparse.SUPPRESS,
                       help="Instanz (Standard: zuletzt gestartete laufende)")
    p.add_argument("--id", default=None, help="Instanz (Standard: zuletzt gestartete laufende)")
    sub = p.add_subparsers(dest="befehl", required=True)

    def add(name):
        return sub.add_parser(name, parents=[idopt])

    s = add("start")
    s.add_argument("--dir", required=True, help="Ordner fuer Laufwerk C:")
    s.add_argument("--cdrom", help="Ordner fuer CD-Laufwerk D:")
    s.add_argument("--cmd", action="append", default=[], help="Autoexec-Zeile (mehrfach)")
    s.add_argument("--headless", action="store_true")
    s = add("batch")
    s.add_argument("--dir", required=True, help="Ordner fuer Laufwerk C: (beschreibbar)")
    s.add_argument("--cmd", required=True, help="DOS-Befehl, z. B. \"TCC HALLO.C\"")
    s.add_argument("--d", help="Ordner fuer Laufwerk D:")
    s.add_argument("--cdrom-d", action="store_true", help="D: nur lesbar (CD)")
    s.add_argument("--timeout", type=float, default=60.0)
    s.add_argument("--max-ausgabe", type=int, default=16 * 1024 * 1024)
    s = add("szenario")
    s.add_argument("--datei", required=True, help="Szenario-Datei (dosbox-mcp-szenario/1)")
    s.add_argument("--var", action="append", default=[], help="Variable NAME=WERT (mehrfach)")
    s.add_argument("--fenster", action="store_true", help="mit Fenster statt headless")
    s.add_argument("--offen", action="store_true", help="Emulator am Ende laufen lassen")
    s.add_argument("--ausgabe", help="Ausgabeordner statt .runs/<id>/out")
    for name in ("stop", "list", "status", "resume", "regs", "text", "video", "events-stop", "cov-stop",
                 "flow-stop", "modules", "calls-stop", "calls-status"):
        add(name)
    s = add("pause"); s.add_argument("--bis", choices=["pmode"])
    s = add("events-start"); s.add_argument("--typ", action="append")
    s.add_argument("--int", action="append", help="NR oder NR:AH in Hex, z. B. 21:3D (mehrfach)")
    s.add_argument("--rueckgabe", action="store_true"); s.add_argument("--max-bytes", type=int)
    s.add_argument("--vorschau", type=int, help="Bytes Vorschau im Ereignis datei")
    s = add("calls-start"); s.add_argument("--ziel", action="append", help="Adresse/Symbol (mehrfach)")
    s.add_argument("--bereich", nargs=2, action="append", metavar=("VON", "BIS"), help="Adressbereich (mehrfach)")
    s.add_argument("--modul", action="append"); s.add_argument("--int", action="append", help="NR oder NR:AH in Hex")
    s.add_argument("--stack", type=int); s.add_argument("--max-je-ziel", type=int, default=1000)
    s.add_argument("--max-bytes", type=int); s.add_argument("--name", default="calls")
    s.add_argument("--zustaende", type=int, default=0); s.add_argument("--max-zustaende", type=int, default=1000)
    s = add("func-run"); s.add_argument("--zustand", required=True)
    s.add_argument("--schreiben", action="append", help="ADRESSE=HEX (mehrfach)")
    s.add_argument("--reg", action="append", help="NAME=WERT (mehrfach, Hex)")
    s.add_argument("--stack"); s.add_argument("--irqs", action="store_true")
    s.add_argument("--max-befehle", type=int); s.add_argument("--name", default="func")
    s.add_argument("--timeout", type=float, default=60.0)
    s = add("events-read"); s.add_argument("--seit", type=int, default=0); s.add_argument("--max", type=int, default=50)
    s = add("cov-start"); s.add_argument("--hits", action="store_true"); s.add_argument("--max-bloecke", type=int)
    s = add("cov-save"); s.add_argument("name", nargs="?", default="coverage")
    s = add("flow-start"); s.add_argument("--returns", action="store_true"); s.add_argument("--ring", type=int, default=4096)
    s = add("flow-save"); s.add_argument("name", nargs="?", default="indirekt")
    s = add("flow-recent"); s.add_argument("anzahl", nargs="?", type=int, default=100)
    s = add("segments"); s.add_argument("modul"); s.add_argument("segmente", help="JSON-Liste")
    s = add("symbols"); s.add_argument("pfad"); s.add_argument("--basis")
    s = add("symbol"); s.add_argument("name")
    s = add("snapshot"); s.add_argument("label"); s.add_argument("--ext", action="store_true")
    s.add_argument("--bis", choices=["pmode"])
    s = add("step"); s.add_argument("count", type=int, nargs="?", default=1)
    s = add("translate"); s.add_argument("adresse")
    s = add("read"); s.add_argument("adresse"); s.add_argument("laenge", type=_num)
    s = add("write"); s.add_argument("adresse"); s.add_argument("hex")
    s = add("dump"); s.add_argument("adresse"); s.add_argument("laenge", type=_num); s.add_argument("name")
    s = add("search"); s.add_argument("adresse"); s.add_argument("laenge", type=_num)
    g = s.add_mutually_exclusive_group(required=True); g.add_argument("--hex"); g.add_argument("--text")
    s = add("key"); s.add_argument("taste")
    s = add("type"); s.add_argument("text"); s.add_argument("--layout", default="de")
    s = add("move"); s.add_argument("x", type=float); s.add_argument("y", type=float)
    s = add("click"); s.add_argument("x", type=float); s.add_argument("y", type=float)
    s.add_argument("--button", default="links")
    s = add("shot"); s.add_argument("name", nargs="?", default="screen")
    s.add_argument("--rect", help="x,y,breite,hoehe")
    s = add("until"); s.add_argument("--adresse"); s.add_argument("--pmode", action="store_true")
    s.add_argument("--paging", action="store_true"); s.add_argument("--cs"); s.add_argument("--cpl", type=int)
    s.add_argument("--ausdruck"); s.add_argument("--timeout", type=float, default=10.0)
    s = add("step-over"); s.add_argument("--timeout", type=float, default=10.0)
    s = add("frame"); s.add_argument("anzahl", nargs="?", type=int, default=1)
    s = add("eval"); s.add_argument("ausdruck")
    s = add("speed"); s.add_argument("modus", choices=["normal", "turbo", "cycles"]); s.add_argument("--cycles", type=int)
    s = add("break"); s.add_argument("ziel"); s.add_argument("--bedingung"); s.add_argument("--einmalig", action="store_true")
    s = add("unbreak"); s.add_argument("ziel", nargs="?"); s.add_argument("--alle", action="store_true")
    s = add("break-int"); s.add_argument("nummer"); s.add_argument("--ah"); s.add_argument("--al")
    add("break-list")
    s = add("dis"); s.add_argument("adresse", nargs="?"); s.add_argument("--anzahl", type=int, default=10)
    s.add_argument("--bits", type=int, choices=[16, 32]); s.add_argument("--name")
    s = add("watch"); s.add_argument("adresse"); s.add_argument("--laenge", type=_num, default=1)
    s.add_argument("--art", default="schreiben", choices=["lesen", "schreiben", "beides"])
    s.add_argument("--aktion", default="anhalten", choices=["anhalten", "protokollieren"])
    s = add("unwatch"); s.add_argument("id", nargs="?", type=int); s.add_argument("--alle", action="store_true")
    add("watch-list")
    s = add("watch-log"); s.add_argument("name", nargs="?", default="zugriffe"); s.add_argument("--leeren", action="store_true")
    s = add("state-save"); s.add_argument("name", nargs="?", default="state")
    s = add("state-load"); s.add_argument("name", nargs="?", default="state"); s.add_argument("--pfad")
    s.add_argument("--ohne-pruefung", action="store_true")
    s = add("dos"); s.add_argument("art", choices=["programs", "mcb", "files", "target"])
    s = add("call"); s.add_argument("methode"); s.add_argument("params", nargs="?", default="{}")
    return p


def run(args):
    if args.befehl == "list":
        return {"instanzen": [{k: v for k, v in i.items() if not k.startswith("_")} for i in _instances()]}
    if args.befehl == "start":
        mounts = [Mount("C", args.dir)] + ([Mount("D", args.cdrom, cdrom=True)] if args.cdrom else [])
        box = Dosbox.start(mounts=mounts, autoexec=args.cmd, headless=args.headless)
        return {"id": box.id, "rundir": str(box.inst.rundir)}
    if args.befehl == "batch":
        mounts = [Mount("C", args.dir)] + ([Mount("D", args.d, cdrom=args.cdrom_d)] if args.d else [])
        return run_batch(mounts, args.cmd, timeout_s=args.timeout, max_ausgabe=args.max_ausgabe)
    if args.befehl == "szenario":
        variablen = {}
        for item in args.var:
            if "=" not in item:
                raise DmcpError("bad_argument", f"--var braucht die Form NAME=WERT: {item}")
            k, v = item.split("=", 1)
            variablen[k] = v
        _box, ergebnis = play_scenario(args.datei, variablen=variablen, headless=not args.fenster,
                                       stop_am_ende=not args.offen, ausgabe=args.ausgabe)
        return ergebnis
    inst_id = args.id or newest_instance()
    if inst_id is None:
        raise DmcpError("bad_argument", "keine laufende Instanz (erst start)")
    box = Dosbox.attach(inst_id)
    if args.befehl == "stop":
        box.stop()
        return {"gestoppt": inst_id}
    if args.befehl == "status": return box.status()
    if args.befehl == "resume": return box.resume()
    if args.befehl == "step": return box.step(args.count)
    if args.befehl == "regs": return box.registers()
    if args.befehl == "translate": return box.translate(args.adresse)
    if args.befehl == "read": return box.mem_read_info(args.adresse, args.laenge)
    if args.befehl == "write": return box.mem_write(args.adresse, bytes.fromhex(args.hex))
    if args.befehl == "dump": return box.mem_dump(args.adresse, args.laenge, args.name)
    if args.befehl == "search":
        pattern = args.text if args.text is not None else bytes.fromhex(args.hex)
        return {"treffer": box.mem_search(args.adresse, args.laenge, pattern)}
    if args.befehl == "key": return box.key(args.taste)
    if args.befehl == "type": return box.type_text(args.text, layout=args.layout)
    if args.befehl == "move": return box.mouse_move(args.x, args.y)
    if args.befehl == "click": return box.mouse_click(args.x, args.y, button=args.button)
    if args.befehl == "shot":
        rect = tuple(int(v) for v in args.rect.split(",")) if args.rect else None
        return box.screenshot(args.name, rect=rect)
    if args.befehl == "text": return box.screen_text()
    if args.befehl == "video": return box.video_state()
    if args.befehl == "pause": return box.pause(until=args.bis)
    if args.befehl == "events-start":
        return box.events_start(typen=args.typ, int_filter=_int_rules(args.int), mit_rueckgabe=args.rueckgabe,
                                max_bytes=args.max_bytes, datei_vorschau=args.vorschau)
    if args.befehl == "calls-start":
        bereiche = [{"von": v, "bis": b} for v, b in args.bereich] if args.bereich else None
        return box.calls_start(ziele=args.ziel, bereiche=bereiche, module=args.modul,
                               ints=_int_rules(args.int) if args.int else None, stack_bytes=args.stack,
                               max_je_ziel=args.max_je_ziel, max_bytes=args.max_bytes, name=args.name,
                               zustaende=args.zustaende, max_zustaende=args.max_zustaende)
    if args.befehl == "func-run":
        def pairs(items, what):
            for item in items or []:
                if "=" not in item:
                    raise DmcpError("bad_argument", f"{what} braucht die Form A=B: {item}")
            return [item.split("=", 1) for item in items or []]
        schreiben = [{"adresse": a, "hex": h} for a, h in pairs(args.schreiben, "--schreiben")]
        regs = dict(pairs(args.reg, "--reg"))
        return box.func_run(args.zustand, schreiben=schreiben or None, regs=regs or None, stack=args.stack,
                            irqs=args.irqs, max_befehle=args.max_befehle, name=args.name, timeout_s=args.timeout)
    if args.befehl == "calls-stop": return box.calls_stop()
    if args.befehl == "calls-status": return box.calls_status()
    if args.befehl == "events-stop": return box.events_stop()
    if args.befehl == "events-read": return box.events_read(args.seit, args.max)
    if args.befehl == "cov-start": return box.coverage_start(args.max_bloecke, hits=args.hits)
    if args.befehl == "cov-stop": return box.coverage_stop()
    if args.befehl == "cov-save": return box.coverage_save(args.name)
    if args.befehl == "flow-start": return box.flow_start(ring_groesse=args.ring, mit_returns=args.returns)
    if args.befehl == "flow-stop": return box.flow_stop()
    if args.befehl == "flow-save": return box.flow_save(args.name)
    if args.befehl == "flow-recent": return box.flow_recent(args.anzahl)
    if args.befehl == "modules": return {"module": box.modules()}
    if args.befehl == "segments": return box.set_segments(args.modul, json.loads(args.segmente))
    if args.befehl == "symbols": return box.symbols_load(args.pfad, args.basis)
    if args.befehl == "symbol": return box.symbol_lookup(name=args.name)
    if args.befehl == "snapshot": return box.snapshot(args.label, mit_erweiterung=args.ext, bis=args.bis)
    if args.befehl == "until":
        return box.run_until(adresse=args.adresse, pmode=args.pmode or None, paging=args.paging or None,
                             cs=args.cs, cpl=args.cpl, ausdruck=args.ausdruck, timeout_s=args.timeout)
    if args.befehl == "step-over": return box.step_over(timeout_s=args.timeout)
    if args.befehl == "frame": return box.frame_step(args.anzahl)
    if args.befehl == "eval": return box.eval(args.ausdruck)
    if args.befehl == "speed": return box.speed(args.modus, cycles=args.cycles)
    if args.befehl == "break":
        return box.break_add(args.ziel, bedingung=args.bedingung, einmalig=args.einmalig)
    if args.befehl == "unbreak":
        return box.break_remove(all=True) if args.alle else box.break_remove(args.ziel)
    if args.befehl == "break-int": return box.break_int_add(args.nummer, ah=args.ah, al=args.al)
    if args.befehl == "break-list": return box.break_list()
    if args.befehl == "dis": return box.disassemble(args.adresse, anzahl=args.anzahl, bits=args.bits, name=args.name)
    if args.befehl == "watch":
        return box.watch_add(args.adresse, laenge=args.laenge, art=args.art, aktion=args.aktion)
    if args.befehl == "unwatch":
        return box.watch_remove(all=True) if args.alle else box.watch_remove(args.id)
    if args.befehl == "watch-list": return box.watch_list()
    if args.befehl == "watch-log": return box.watch_log(args.name, leeren=args.leeren)
    if args.befehl == "state-save": return box.state_save(args.name)
    if args.befehl == "state-load":
        return box.state_load(args.name, pfad=args.pfad, ohne_pruefung=args.ohne_pruefung)
    if args.befehl == "dos":
        return {"programs": box.dos_programs, "mcb": box.dos_mcb, "files": box.dos_files,
                "target": box.dos_target}[args.art]()
    if args.befehl == "call":
        params = json.loads(args.params)
        if not isinstance(params, dict):
            raise DmcpError("bad_argument", "params muss ein JSON-Objekt sein, z. B. '{\"count\": 2}'")
        return box.call(args.methode, **params)
    raise DmcpError("bad_argument", f"unbekannter Befehl {args.befehl}")


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        result = run(args)
    except DmcpError as exc:
        print(json.dumps({"fehler": exc.code, "meldung": exc.message}, ensure_ascii=True))
        return 1
    except OSError as exc:  # z. B. Ausgabedatei nicht lesbar; Startfehler wandelt schon Instance.start um
        print(json.dumps({"fehler": "internal", "meldung": f"operating system error: {exc}"}, ensure_ascii=True))
        return 1
    except (ValueError, TypeError) as exc:  # Argumentumwandlung (JSON, Hex, --rect); JSONDecodeError ist ValueError
        print(json.dumps({"fehler": "bad_argument", "meldung": str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 1 if args.befehl == "szenario" and result.get("status") == "fehler" else 0
