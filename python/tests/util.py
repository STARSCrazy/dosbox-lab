"""Gemeinsame Hilfen fuer die End-to-End-Tests."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from dosbox_lab import DmcpError, Dosbox, Mount
from dosbox_lab.instance import DEFAULT_EXE, REPO

TESTPROGS = REPO / "testprogs"


def need_build(cls):
    """Ueberspringt Testklassen, solange der Emulator nicht gebaut ist."""
    return unittest.skipUnless(DEFAULT_EXE.exists(), "Emulator noch nicht gebaut (build.ps1)")(cls)


def start_prog(name, headless=True, before=(), conf=None):
    """Startet eine Instanz mit testprogs als C: und fuehrt `name` aus."""
    return Dosbox.start(mounts=[Mount("C", str(TESTPROGS))], autoexec=[*before, name], headless=headless, conf=conf)


def wait_until(fn, timeout=10.0, interval=0.05):
    """Ruft fn wiederholt auf, bis es etwas Wahres liefert; gibt den Wert zurueck."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"Bedingung nicht erfuellt nach {timeout}s (letzter Wert {last!r})")


US = {"dos": {"keyboardlayout": "us"}}


def start_idle(headless=True):
    """Instanz mit testprogs als C: ohne Programm (DOS-Prompt); Gast-Tastatur US."""
    return Dosbox.start(mounts=[Mount("C", str(TESTPROGS))], autoexec=[], headless=headless, conf=US)


def wait_prompt(box, timeout=15):
    """Wartet auf den Prompt C:\\> (der Start, auch das MOUNT aus der Konfiguration, ist dann fertig)."""
    def prompt_visible():
        try:
            return any(line.startswith("C:\\>") for line in box.screen_text()["zeilen"])
        except DmcpError:  # Startphase: noch kein Textmodus
            return False

    wait_until(prompt_visible, timeout=timeout)


def run_typed(box, command, timeout=15):
    """Wartet auf den Prompt C:\\> und tippt `command` + Enter."""
    wait_prompt(box, timeout)
    box.type_text(command + "\n", layout="us")


def prog_psp(box, name, timeout=15):
    """PSP-Segment (int) des Programms `name` (z. B. "DEBUG.COM") aus der Modulliste, sobald es gestartet ist."""
    module = wait_until(lambda: next((m for m in box.modules() if m["modul"] == name and "psp" in m), None),
                        timeout=timeout)
    return int(module["psp"], 16)


def batch_workdir(test):
    """Temporaerer Ordner mit einer Kopie von BATCH.COM (beschreibbares Laufwerk fuer Batch-Tests)."""
    path = Path(tempfile.mkdtemp(prefix="dmcp-batch-"))
    test.addCleanup(shutil.rmtree, path, True)
    shutil.copy(TESTPROGS / "BATCH.COM", path / "BATCH.COM")
    return path


def class_box(cls, box):
    """Registriert box.stop als Klassen-Aufraeumer, sofort nach dem Start: auch wenn der Rest von setUpClass
    scheitert, bleibt kein Emulator laufen (sonst sperrt er beim naechsten Build die exe, LNK1104)."""
    cls.addClassCleanup(box.stop)
    return box
