"""Shared helpers for the end-to-end tests."""
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from dosbox_lab import DmcpError, Dosbox, Mount
from dosbox_lab.instance import DEFAULT_EXE, REPO

TESTPROGS = REPO / "testprogs"


def need_build(cls):
    """Skip test classes as long as the emulator has not been built."""
    return unittest.skipUnless(DEFAULT_EXE.exists(), "emulator not built yet (build.ps1)")(cls)


def start_prog(name, headless=True, before=(), conf=None):
    """Start an instance with testprogs as C: and run `name`."""
    return Dosbox.start(mounts=[Mount("C", str(TESTPROGS))], autoexec=[*before, name], headless=headless, conf=conf)


def wait_until(fn, timeout=10.0, interval=0.05):
    """Call fn repeatedly until it returns something truthy; return that value."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        last = fn()
        if last:
            return last
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s (last value {last!r})")


US = {"dos": {"keyboardlayout": "us"}}


def start_idle(headless=True):
    """Instance with testprogs as C: and no program (DOS prompt); guest keyboard US."""
    return Dosbox.start(mounts=[Mount("C", str(TESTPROGS))], autoexec=[], headless=headless, conf=US)


def wait_prompt(box, timeout=15):
    """Wait for the prompt C:\\> (startup, including the MOUNT from the configuration, is then finished)."""
    def prompt_visible():
        try:
            return any(line.startswith("C:\\>") for line in box.screen_text()["lines"])
        except DmcpError:  # startup phase: not in text mode yet
            return False

    wait_until(prompt_visible, timeout=timeout)


def run_typed(box, command, timeout=15):
    """Wait for the prompt C:\\> and type `command` + Enter."""
    wait_prompt(box, timeout)
    box.type_text(command + "\n", layout="us")


def prog_psp(box, name, timeout=15):
    """PSP segment (int) of the program `name` (e.g. "DEBUG.COM") from the module list, once it has started."""
    module = wait_until(lambda: next((m for m in box.modules() if m["module"] == name and "psp" in m), None),
                        timeout=timeout)
    return int(module["psp"], 16)


def batch_workdir(test):
    """Temporary directory with a copy of BATCH.COM (writable drive for batch tests)."""
    path = Path(tempfile.mkdtemp(prefix="dmcp-batch-"))
    test.addCleanup(shutil.rmtree, path, True)
    shutil.copy(TESTPROGS / "BATCH.COM", path / "BATCH.COM")
    return path


def class_box(cls, box):
    """Register box.stop as a class cleanup right after the start: even if the rest of setUpClass fails,
    no emulator keeps running (it would lock the exe at the next build, LNK1104)."""
    cls.addClassCleanup(box.stop)
    return box
