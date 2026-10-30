"""run_batch: einen DOS-Befehl headless ausfuehren, Exit-Code, Ausgaben und Dateien einsammeln (Spec 5.9)."""
import json
import os
import time
from pathlib import Path

from .errors import DmcpError
from .instance import Instance

# quit warning=false: Fenster-Schliessen fragt nie nach (kein Dialog, der den Emulations-Thread blockiert).
STANDARD_CONF = {"cpu": {"cycles": "max"}, "dosbox": {"quit warning": "false"}}
STANDARD_MAX_AUSGABE = 16 * 1024 * 1024
MAX_DATEIEN = 20000
KURZ_BYTES = 4096
ENDE_WARTEN_S = 3.0  # so lange darf der Prozess nach Verbindungsverlust noch leben (Herunterfahren nach EXIT)


def _bestand(mounts):
    """Dateien der beschreibbaren Mounts: {(laufwerk, relpfad): (groesse, mtime_ns, hostpfad)}, vollstaendig?"""
    stand = {}
    for mount in mounts:
        if mount.cdrom:
            continue
        root = Path(mount.host)
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if len(stand) >= MAX_DATEIEN:
                    return stand, False
                host = Path(dirpath) / name
                try:
                    st = host.stat()
                except OSError:
                    continue
                stand[(mount.drive.upper(), str(host.relative_to(root)))] = (st.st_size, st.st_mtime_ns, str(host))
    return stand, True


def _vergleich(vorher, nachher):
    dateien = []
    for key, (groesse, mtime, host) in sorted(nachher.items()):
        alt = vorher.get(key)
        if alt is not None and alt[:2] == (groesse, mtime):
            continue
        dateien.append({"laufwerk": key[0], "pfad": key[1], "host": host, "groesse": groesse,
                        "art": "neu" if alt is None else "geaendert"})
    for key, (groesse, _mtime, host) in sorted(vorher.items()):
        if key not in nachher:
            dateien.append({"laufwerk": key[0], "pfad": key[1], "host": host, "groesse": groesse,
                            "art": "geloescht"})
    return dateien


def _kurz(pfad):
    if pfad is None or not pfad.exists():
        return ""
    with open(pfad, "rb") as f:
        return f.read(KURZ_BYTES).decode("cp437")


def _warte_ende(inst, sekunden):
    """True, wenn der Prozess innerhalb von `sekunden` endet (oder schon beendet ist)."""
    ende = time.monotonic() + sekunden
    while inst.alive():
        if time.monotonic() >= ende:
            return False
        time.sleep(0.05)
    return True


def _normal_beendet(inst):
    """Prozess mit Exit-Code 0 beendet (normales Herunterfahren); ohne eigenen Prozess-Handle nicht pruefbar."""
    proc = getattr(inst, "_proc", None)
    return proc is None or proc.poll() == 0


def _batch_datei(inst):
    """Letzter Stand aus <outdir>/batch.json (schreibt DMCPBAT bei START, Programmstart/-ende und ENDE)."""
    try:
        return json.loads((inst.outdir / "batch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _bildschirm(inst):
    """Textmodus -> screen.txt, sonst screen.png; None, wenn kein Bild zu bekommen ist."""
    try:
        text = inst.client.call("screen.text", {})
    except DmcpError as exc:
        if exc.code != "not_text_mode":
            return None
        try:
            return inst.client.call("screen.capture", {"name": "screen"})["pfad"]
        except DmcpError:
            return None
    pfad = inst.outdir / "screen.txt"
    pfad.write_text("\n".join(line.rstrip() for line in text["zeilen"]) + "\n", encoding="utf-8")
    return str(pfad)


def run_batch(mounts, befehl, timeout_s=60.0, max_ausgabe=STANDARD_MAX_AUSGABE, conf=None, exe=None):
    """Fuehrt `befehl` (Programm, Batch-Datei oder Shell-Befehl) in einer eigenen headless Instanz aus.
    mounts: Liste von Mount (das erste ist das aktuelle Laufwerk).
    Ergebnis: dict, status fertig/timeout/shell_beendet/abgestuerzt."""
    if not isinstance(befehl, str) or not befehl.strip() or "\n" in befehl or "\r" in befehl:
        raise DmcpError("bad_argument", "befehl muss eine einzelne, nicht leere Befehlszeile sein")
    if not befehl.isascii():
        raise DmcpError("bad_argument", "befehl darf nur ASCII-Zeichen enthalten (Config-Datei und Gast-Codepage)")
    if not mounts:
        raise DmcpError("bad_argument", "mindestens ein Mount noetig (z. B. Laufwerk C:)")
    if not timeout_s or timeout_s <= 0:
        raise DmcpError("bad_argument", "timeout_s muss groesser als 0 sein")
    if not isinstance(max_ausgabe, int) or max_ausgabe < 1:
        raise DmcpError("bad_argument", "max_ausgabe muss eine ganze Zahl >= 1 sein")
    merged = {section: dict(values) for section, values in STANDARD_CONF.items()}
    for section, values in (conf or {}).items():
        merged.setdefault(section, {}).update(values)
    vorher, voll_vorher = _bestand(mounts)
    autoexec = [f"@Z:\\DMCPBAT.COM START {max_ausgabe}", f"@CALL {befehl}", "@Z:\\DMCPBAT.COM ENDE"]
    beginn = time.monotonic()
    inst = Instance.start(mounts=list(mounts), autoexec=autoexec, headless=True, exe=exe, conf=merged)
    ergebnis = {"instanz": inst.id, "befehl": befehl}
    status, batch = "timeout", None
    try:
        deadline = beginn + timeout_s
        while True:
            try:
                batch = inst.client.call("batch.status", {})
            except DmcpError as exc:
                # Verbindung weg: der Prozess faehrt evtl. noch herunter (EXIT im Befehl), kurz auf sein Ende warten
                if not inst.alive() or (exc.code != "timeout" and _warte_ende(inst, ENDE_WARTEN_S)):
                    datei = _batch_datei(inst)
                    if datei is not None and datei.get("aktiv") and _normal_beendet(inst):
                        status, batch = "shell_beendet", datei  # Shell beendet (EXIT), DMCPBAT ENDE lief nie
                    else:
                        status = "abgestuerzt"
                else:
                    ergebnis["rpc_fehler"] = f"{exc.code}: {exc.message}"
                break
            if batch["fertig"]:
                status = "fertig"
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        ergebnis["laufzeit_s"] = round(time.monotonic() - beginn, 3)
        if status == "abgestuerzt":
            ergebnis["screen"] = None
            ergebnis["log_ende"] = inst.log_tail()
        elif status == "shell_beendet" or "rpc_fehler" in ergebnis:
            ergebnis["screen"] = None  # Prozess beendet bzw. Emulator haengt: keine weiteren RPCs
        else:
            ergebnis["screen"] = _bildschirm(inst)
            try:
                batch = inst.client.call("batch.status", {})  # Byte-Zaehler nach dem letzten Stand
            except DmcpError:
                pass
    finally:
        inst.stop()
    out, err = inst.outdir / "stdout.txt", inst.outdir / "stderr.txt"
    nachher, voll_nachher = _bestand(mounts)
    ergebnis.update({
        "status": status,
        "exit_code": (batch["exit_code"] if status == "fertig"
                      else batch.get("letzter_exit_code") if status == "shell_beendet" else None),
        "programme": batch["programme"] if batch else 0,
        "abgeschnitten": bool(batch and batch["abgeschnitten"]),
        "stdout": str(out) if out.exists() else None,
        "stderr": str(err) if err.exists() else None,
        "stdout_text": _kurz(out),
        "stderr_text": _kurz(err),
        "log": str(inst.rundir / "dosbox.log"),
        "dateien": _vergleich(vorher, nachher),
        "dateien_vollstaendig": voll_vorher and voll_nachher,
    })
    if batch and batch.get("fehler"):
        ergebnis["fehler"] = batch["fehler"]
    return ergebnis
