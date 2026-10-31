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
from dosbox_lab.scenario import _Player, load_scenario, play_scenario
from tests.util import TESTPROGS, class_box, need_build, start_idle, wait_until


@need_build
class FrameCounterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.box = class_box(cls, start_idle())

    def test_frames_increase(self):
        a = self.box.status()["frames"]
        wait_until(lambda: self.box.status()["frames"] > a + 3, timeout=10)

    def test_frame_step_counts(self):
        self.box.pause()
        a = self.box.status()["frames"]
        self.box.frame_step(5)
        self.assertEqual(self.box.status()["frames"], a + 5)
        self.box.resume()


@need_build
class OutdirTest(unittest.TestCase):
    def test_outdir(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "new" / "out"
            box = Dosbox.start(mounts=[], autoexec=[], headless=True, outdir=str(target))
            try:
                self.assertEqual(box.inst.outdir, target.resolve())
                wait_until(lambda: box.status()["frames"] > 0, timeout=15)  # a screenshot needs a frame
                info = box.screenshot("probe")
                self.assertEqual(Path(info["path"]).parent.resolve(), target.resolve())
                box.inst.client.close()  # the named pipe serves only one client
                box = Dosbox.attach(box.id)
                self.assertEqual(box.inst.outdir, target.resolve())
            finally:
                box.stop()


GOOD = {"format": "dosbox-lab-scenario/1", "name": "probe", "variables": {"PROG": "SCENE"},
        "start": {"mounts": [{"drive": "C", "path": "."}], "layout": "us"},
        "steps": [{"wait_text": "C:\\>"}, {"type": "${PROG}\n"}, {"wait_text": "READY", "timeout_s": 5},  # TESTPROG-TEXT
                  {"key": "a"}, {"wait_memory": {"address": "l:00000000", "hex": "00", "mask": "FF"}},
                  {"wait_frames": 3}, {"state_save": "s"}, {"note": "x", "screenshot": "b"}]}


class LoadTest(unittest.TestCase):
    def error(self, sz, text, variables=None):
        with self.assertRaises(DmcpError) as ctx:
            load_scenario(sz, variables)
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn(text, ctx.exception.message)

    def with_(self, **changes):
        sz = copy.deepcopy(GOOD)
        sz.update(changes)
        return sz

    def test_good(self):
        s = load_scenario(GOOD)
        self.assertEqual(s["name"], "probe")
        self.assertEqual(s["steps"][1], {"kind": "type", "value": "SCENE\n", "timeout_s": 30.0, "note": None})
        self.assertEqual(s["steps"][2]["timeout_s"], 5.0)
        self.assertEqual(s["steps"][7]["note"], "x")
        self.assertEqual(s["start"]["mounts"][0].drive, "C")
        self.assertEqual(s["start"]["conf"]["dosbox"]["quit warning"], "false")

    def test_variables_override_and_dollar(self):
        sz = self.with_(steps=[{"type": "${PROG} $$5\n"}])
        self.assertEqual(load_scenario(sz, {"PROG": "TO"})["steps"][0]["value"], "TO $5\n")

    def test_variable_in_mount(self):
        sz = self.with_(variables={"DIR": str(Path.cwd())}, start={"mounts": [{"drive": "D", "path": "${DIR}"}]},
                        steps=[{"wait_s": 1}])
        self.assertEqual(Path(load_scenario(sz)["start"]["mounts"][0].host), Path.cwd().resolve())

    def test_relative_to_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "game").mkdir()
            file = Path(tmp) / "s.json"
            sz = self.with_(start={"mounts": [{"drive": "C", "path": "game"}]},
                            steps=[{"state_load": "x.sav"}])
            file.write_text(json.dumps(sz), encoding="utf-8")
            s = load_scenario(str(file))
            self.assertEqual(Path(s["start"]["mounts"][0].host), (Path(tmp) / "game").resolve())
            self.assertEqual(Path(s["steps"][0]["value"]), (Path(tmp) / "x.sav").resolve())
            self.assertEqual(s["source"], str(file.resolve()))

    def test_bom_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "bom.json"
            file.write_text(json.dumps(GOOD), encoding="utf-8-sig")  # PowerShell/Notepad write a BOM
            self.assertEqual(load_scenario(str(file))["name"], "probe")

    def test_error_cases(self):
        self.error(self.with_(format="x"), "format")
        self.error(self.with_(name="a/b"), "name")
        self.error(self.with_(extra=1), "extra")
        self.error(self.with_(steps=[{}]), "steps[0]")
        self.error(self.with_(steps=[{"type": "a", "key": "b"}]), "steps[0]")
        self.error(self.with_(steps=[{"jump": 1}]), "jump")
        self.error(self.with_(steps=[{"wait_frames": True}]), "wait_frames")
        self.error(self.with_(steps=[{"wait_frames": 0}]), "wait_frames")
        self.error(self.with_(steps=[{"wait_text": "a", "timeout_s": 0}]), "timeout_s")
        self.error(self.with_(steps=[{"type": "${NONE}"}]), "NONE")
        self.error(self.with_(steps=[{"wait_memory": {"address": "l:0", "hex": "0"}}]), "hex")
        self.error(self.with_(steps=[{"wait_memory": {"address": "l:0", "hex": "00", "mask": "FFFF"}}]),
                   "mask")
        self.error(self.with_(steps=[{"wait_text": {"text": "(", "regex": True}}]), "regex")
        self.error(self.with_(steps=[{"wait_halt": {"address": "x", "then": "never"}}]), "then")
        self.error(self.with_(steps=[{"recording_start": {"kind": "all"}}]), "kind")
        self.error(self.with_(steps=[{"continue": False}]), "continue")
        self.error(self.with_(start={"mounts": [{"drive": "C", "path": "does_not_exist_xyz"}]}), "path")
        self.error(self.with_(variables={"A": 1}), "variables")
        self.error(self.with_(start={"color": 1}), "color")

    def test_type_checks_tightened(self):
        mp = {"x": 1, "y": 2, "x_address": "l:0", "y_address": "l:2"}
        ok = load_scenario(self.with_(steps=[{"mouse_program": dict(mp, width=3, tolerance=0, attempts=2,
                                                                    wait_s=0.5)}]))
        self.assertEqual(ok["steps"][0]["value"]["width"], 3)
        for change, text in (({"x": 1.5}, "x"), ({"y": True}, "y"), ({"x_address": ""}, "x_address"),
                             ({"y_address": 5}, "y_address"), ({"width": 0}, "width"),
                             ({"tolerance": -1}, "tolerance"), ({"attempts": "2"}, "attempts"),
                             ({"wait_s": 0}, "wait_s"), ({"wait_s": float("nan")}, "wait_s")):
            self.error(self.with_(steps=[{"mouse_program": dict(mp, **change)}]), text)
        self.error(self.with_(steps=[{"click": {"x": 1, "y": 1, "button": 3}}]), "button")
        self.error(self.with_(steps=[{"click": {"x": 1, "y": 1, "button": "above"}}]), "button")
        self.error(self.with_(start={"mounts": [{"drive": "C", "path": ".", "cdrom": "false"}]}), "cdrom")
        self.error(self.with_(start={"mounts": [{"drive": "ä", "path": "."}]}), "drive")
        self.error(self.with_(steps=[{"wait_s": float("inf")}]), "wait_s")
        self.error(self.with_(steps=[{"wait_s": 10 ** 400}]), "wait_s")
        self.error(self.with_(steps=[{"wait_text": "a", "timeout_s": float("nan")}]), "timeout_s")
        self.error(self.with_(steps=[{"wait_text": "a", "timeout_s": 10 ** 400}]), "timeout_s")

    def test_note_variables(self):
        s = load_scenario(self.with_(steps=[{"wait_s": 1, "note": "${PROG} $$"}]))
        self.assertEqual(s["steps"][0]["note"], "SCENE $")


class LegacyFormatTest(unittest.TestCase):
    """The old German format is still read and translated (legacy German names on purpose)."""

    # legacy German names on purpose
    LEGACY = {"format": "dosbox-lab-szenario/1", "name": "alt", "variablen": {"PROG": "SCENE"},
              "start": {"mounts": [{"laufwerk": "C", "pfad": "."}], "layout": "us"},
              "schritte": [{"tippe": "${PROG}\n", "notiz": "n"},
                           {"taste": {"name": "a", "ms": 50}},
                           {"klick": {"x": 1, "y": 2, "knopf": "rechts"}},
                           {"warte_text": {"text": "X", "zeile": 3}},
                           {"warte_halt": {"adresse": "l:1000", "danach": "anhalten"}},
                           {"speicherstand": "s"},
                           {"aufzeichnung_start": {"art": "calls", "ziele": ["l:0"],
                                                   "bereiche": [{"von": "l:1", "bis": "l:2"}],
                                                   "max_je_ziel": 5}},
                           {"aufzeichnung_stop": {"art": "calls"}},
                           {"weiter": True}]}

    def test_legacy_scenario_loads(self):
        s = load_scenario(self.LEGACY)
        kinds = [step["kind"] for step in s["steps"]]
        self.assertEqual(kinds, ["type", "key", "click", "wait_text", "wait_halt", "state_save",
                                 "recording_start", "recording_stop", "continue"])
        self.assertEqual(s["steps"][0]["value"], "SCENE\n")
        self.assertEqual(s["steps"][0]["note"], "n")
        self.assertEqual(s["steps"][2]["value"]["button"], "right")
        self.assertEqual(s["steps"][3]["value"]["line"], 3)
        self.assertEqual(s["steps"][4]["value"], {"address": "l:1000", "then": "halt"})
        rec = s["steps"][6]["value"]
        self.assertEqual(rec["targets"], ["l:0"])
        self.assertEqual(rec["ranges"], [{"from": "l:1", "to": "l:2"}])
        self.assertEqual(rec["max_per_target"], 5)
        self.assertEqual(s["start"]["mounts"][0].drive, "C")

    def test_old_mcp_format_name(self):
        sz = copy.deepcopy(self.LEGACY)
        sz["format"] = "dosbox-mcp-szenario/1"  # legacy German names on purpose
        self.assertEqual(load_scenario(sz)["name"], "alt")

    def test_legacy_errors_still_reported(self):
        sz = copy.deepcopy(self.LEGACY)
        sz["schritte"] = [{"warte_halt": {"adresse": "x", "danach": "nie"}}]  # legacy German names on purpose
        with self.assertRaises(DmcpError) as ctx:
            load_scenario(sz)
        self.assertEqual(ctx.exception.code, "bad_argument")


def scene(*steps, name="scene"):
    return {"format": "dosbox-lab-scenario/1", "name": name,
            "start": {"mounts": [{"drive": "C", "path": str(TESTPROGS)}], "layout": "us",
                      "conf": {"dos": {"keyboardlayout": "us"}}},
            "steps": [{"wait_text": "C:\\>", "timeout_s": 15}, {"type": "SCENE\n"},
                      {"wait_text": "READY", "timeout_s": 15}] + list(steps)}  # TESTPROG-TEXT


@need_build
class PlayTest(unittest.TestCase):
    def test_flow_ok(self):
        sz = scene({"key": "q"}, {"wait_text": {"text": "KEY\\s+q", "regex": True}},  # TESTPROG-TEXT
                   {"wait_memory": {"address": "rel:SCENE.COM:0000:0300", "hex": "71"}},
                   {"wait_memory": {"address": "rel:SCENE.COM:0000:0301", "hex": "50", "mask": "F0"}},
                   {"wait_frames": 3}, {"wait_frame": 1}, {"screenshot": "frame"}, {"state_save": "end"})
        box, r = play_scenario(sz)
        try:
            self.assertEqual(r["status"], "ok", r["error"])
            self.assertEqual(r["format"], "dosbox-lab-scenario-result/1")
            self.assertEqual(r["steps_done"], r["steps_total"])
            self.assertEqual(len(r["steps"]), r["steps_total"])
            self.assertGreater(r["frames"], 3)
            self.assertTrue(Path(r["savestates"][0]["path"]).exists())
            self.assertTrue(Path(r["screenshots"][0]["path"]).exists())
            file = json.loads(Path(r["path"]).read_text(encoding="utf-8"))
            self.assertEqual(file["status"], "ok")
            self.assertEqual(Path(r["path"]).name, "scene.scenario.json")
            box.state_load(path=r["savestates"][0]["path"])  # the savestate is loadable
        finally:
            if box is not None:
                box.stop()

    def test_output_dir_and_stop_at_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            box, r = play_scenario(scene({"key": "a"}, {"screenshot": "x"}), output_dir=tmp, stop_at_end=True)
            self.assertIsNone(box)
            self.assertEqual(r["status"], "ok", r["error"])
            self.assertEqual(Path(r["path"]).parent.resolve(), Path(tmp).resolve())
            self.assertEqual(Path(r["screenshots"][0]["path"]).parent.resolve(), Path(tmp).resolve())


@need_build
class PlayErrorTest(unittest.TestCase):
    def test_timeout_with_diagnosis(self):
        box, r = play_scenario(scene({"wait_text": "DOESNOTEXIST", "timeout_s": 1, "note": "n"}))
        try:
            self.assertEqual(r["status"], "error")
            f = r["error"]
            self.assertEqual((f["nr"], f["kind"], f["reason"]), (3, "wait_text", "timeout"))
            self.assertIn("READY", Path(f["screen"]).read_text(encoding="utf-8"))  # TESTPROG-TEXT
            self.assertTrue(Path(f["screenshot"]).exists())
            self.assertEqual(r["steps_done"], 3)
            self.assertEqual(len(r["steps"]), 3)
        finally:
            if box is not None:
                box.stop()


class DeadEmulatorTest(unittest.TestCase):
    """The emulator dies: result error/instance_dead, no exception (fake emulator, no real one)."""

    def test_instance_dead(self):
        sz = load_scenario({"format": "dosbox-lab-scenario/1", "name": "dead", "steps": [{"wait_s": 0.01},
                                                                                        {"type": "x"}]})

        tmpdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmpdir, ignore_errors=True)

        class Dead:
            def __init__(self):
                self.inst = type("I", (), {"outdir": Path(tmpdir), "id": "x", "alive": lambda s: False})()
                self.id = "x"

            def status(self):
                raise DmcpError("instance_dead", "gone")

            def type_text(self, *a, **k):
                raise DmcpError("instance_dead", "gone")

        r = _Player(sz, Dead()).play()
        self.assertEqual(r["status"], "error")
        self.assertEqual(r["error"]["reason"], "instance_dead")
        self.assertIsNone(r["error"]["screen"])
        self.assertIsNone(r["error"]["screenshot"])


# HLT loop before the keypress (sti; hlt; jmp), reached again on every timer IRQ. done_fn (0200) is not
# suitable: a scenario cannot wait for the halt and send the key at the same time, and after the key
# done_fn has already run before wait_halt sets the breakpoint.
HALT = "rel:SCENE.COM:0000:011B"


@need_build
class HaltTest(unittest.TestCase):
    def test_halt_stays_and_breakpoint_removed(self):
        box, r = play_scenario(scene({"key": "a"}, {"wait_halt": {"address": HALT}}, {"state_save": "entry"}))
        try:
            self.assertEqual(r["status"], "ok", r["error"])
            st = box.status()
            self.assertTrue(st["paused"])
            self.assertEqual(st["stop_reason"], "breakpoint")
            self.assertEqual(box.break_list()["entries"], [])
        finally:
            if box is not None:
                box.stop()

    def test_halt_then_continue(self):
        box, r = play_scenario(scene({"key": "a"}, {"wait_halt": {"address": HALT, "then": "continue"}},
                                     {"wait_text": "DONE"}), stop_at_end=True)  # TESTPROG-TEXT
        self.assertEqual(r["status"], "ok", r["error"])

    def test_paused(self):
        box, r = play_scenario(scene({"key": "a"}, {"wait_halt": {"address": HALT}},
                                     {"wait_text": "DONE", "timeout_s": 5}))  # TESTPROG-TEXT
        try:
            self.assertEqual((r["error"]["nr"], r["error"]["reason"]), (5, "paused"))
        finally:
            if box is not None:
                box.stop()

    def test_continue(self):
        box, r = play_scenario(scene({"key": "a"}, {"wait_halt": {"address": HALT}},
                                     {"continue": True}, {"wait_text": "DONE"}), stop_at_end=True)  # TESTPROG-TEXT
        self.assertEqual(r["status"], "ok", r["error"])

    def test_halt_timeout(self):
        box, r = play_scenario(scene({"wait_halt": {"address": HALT}, "timeout_s": 1}))
        try:
            self.assertEqual(r["error"]["reason"], "timeout")
            self.assertEqual(box.break_list()["entries"], [])  # removed on error as well
        finally:
            if box is not None:
                box.stop()

    def test_state_load_keeps_running_afterwards(self):
        """Load a savestate taken at the breakpoint (paused): the emulator then runs without a continue."""
        with tempfile.TemporaryDirectory() as tmp:
            sav = str(Path(tmp) / "halt.sav")
            box, r = play_scenario(scene({"key": "a"}, {"wait_halt": {"address": HALT}},
                                         {"state_save": "halt"}, {"state_load": sav},
                                         {"wait_text": "DONE", "timeout_s": 10}),  # TESTPROG-TEXT
                                   output_dir=tmp, stop_at_end=True)
            self.assertEqual(r["status"], "ok", r["error"])
            self.assertEqual(Path(r["savestates"][0]["path"]).resolve(), Path(sav).resolve())


@need_build
class RecordingTest(unittest.TestCase):
    def test_calls_with_states_and_func_run(self):
        box, r = play_scenario(scene(
            {"recording_start": {"kind": "calls", "ranges": [{"from": "rel:SCENE.COM:0000:0200",
                                                              "to": "rel:SCENE.COM:0000:02FF"}],
                                 "states": 1, "name": "sz_calls"}},
            {"recording_start": {"kind": "coverage"}},
            {"key": "a"}, {"wait_text": "DONE"},  # TESTPROG-TEXT
            {"recording_stop": {"kind": "calls"}}))
        try:
            self.assertEqual(r["status"], "ok", r["error"])
            a = {e["kind"]: e for e in r["recordings"]}
            self.assertEqual((a["calls"]["nr_start"], a["calls"]["nr_stop"]), (3, 7))
            self.assertFalse(a["calls"]["self_stopped"])
            self.assertTrue(a["coverage"]["self_stopped"])  # stopped at the end of the scenario
            self.assertIsNone(a["coverage"]["nr_stop"])
            self.assertTrue(a["coverage"]["files"])
            self.assertTrue(all(Path(p).exists() for p in a["coverage"]["files"]))
            self.assertEqual(Path(a["calls"]["files"][0]).name, "sz_calls.jsonl")
            entries = read_jsonl(a["calls"]["files"][0])
            self.assertEqual(len(entries), 1)
            f = box.func_run(entries[0]["state"])
            self.assertEqual(f["end"], "ret")
        finally:
            if box is not None:
                box.stop()

    def test_self_stopped_on_error(self):
        box, r = play_scenario(scene({"recording_start": {"kind": "events"}},
                                     {"wait_text": "NEVER", "timeout_s": 1}))
        try:
            e = r["recordings"][0]
            self.assertTrue(e["self_stopped"])
            self.assertTrue(Path(e["files"][0]).exists())
            self.assertFalse(box.events_status()["active"])
        finally:
            if box is not None:
                box.stop()

    def test_replaced_keeps_first_file(self):
        box, r = play_scenario(scene({"recording_start": {"kind": "events", "name": "ev1"}},
                                     {"recording_start": {"kind": "events", "name": "ev2"}},
                                     {"key": "a"}, {"wait_text": "DONE"}), stop_at_end=True)  # TESTPROG-TEXT
        self.assertEqual(r["status"], "ok", r["error"])
        first, second = r["recordings"]
        self.assertEqual(first["hint"], "replaced")
        self.assertEqual(Path(first["files"][0]).name, "ev1.jsonl")
        self.assertTrue(Path(first["files"][0]).exists())
        self.assertTrue(second["self_stopped"])
        self.assertEqual(Path(second["files"][0]).name, "ev2.jsonl")

    def test_flow_with_name(self):
        box, r = play_scenario(scene({"recording_start": {"kind": "flow", "name": "sz_flow"}},
                                     {"key": "a"}, {"wait_text": "DONE"},  # TESTPROG-TEXT
                                     {"recording_stop": {"kind": "flow"}}), stop_at_end=True)
        self.assertEqual(r["status"], "ok", r["error"])
        path = Path(r["recordings"][0]["files"][0])
        self.assertEqual(path.name, "sz_flow.jsonl")
        self.assertTrue(path.exists())


@need_build
class MissingPagesTest(unittest.TestCase):
    def test_missing_pages_do_not_match_yet(self):
        # above the RAM (16 MB): mem.read returns zeros with missing - that is not a hit
        box, r = play_scenario(scene({"wait_memory": {"address": "l:01000000", "hex": "00"}, "timeout_s": 1}))
        try:
            self.assertEqual((r["error"]["nr"], r["error"]["reason"]), (3, "timeout"))
        finally:
            if box is not None:
                box.stop()


class _FakeBox:
    """Fake emulator for flow tests without a real emulator."""

    def __init__(self, outdir, paused=False):
        self.inst = type("I", (), {"outdir": Path(outdir), "id": "x", "alive": lambda s: True})()
        self.id = "x"
        self.paused = paused
        self.frame_calls = []
        self.resumes = 0
        self.frame_duration = 0.0
        self.frame_reason = "frame"

    def status(self):
        return {"paused": self.paused, "stop_reason": "pause" if self.paused else "none", "frames": 7}

    def frame_step(self, count=1, timeout_s=10.0):
        self.frame_calls.append((count, timeout_s))
        time.sleep(self.frame_duration)
        self.paused = True
        return {"paused": True, "stop_reason": self.frame_reason}

    def resume(self):
        self.resumes += 1
        self.paused = False

    def screen_text(self):
        return {"lines": ["LINE"]}

    def screenshot(self, name):
        return {"path": f"{name}.png"}


class FakeFlowTest(unittest.TestCase):
    """Flow without an emulator (fake): wait_frames, diagnosis, unexpected exceptions, option validation."""

    def play(self, box, *steps):
        sz = load_scenario({"format": "dosbox-lab-scenario/1", "name": "fake", "steps": list(steps)})
        return _Player(sz, box).play()

    def test_wait_frames_other_halt(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = _FakeBox(tmp)
            box.frame_reason = "breakpoint"
            r = self.play(box, {"wait_frames": 3})
            self.assertEqual(r["error"]["reason"], "paused")
            self.assertEqual(box.resumes, 0)  # stays at the foreign halt
            self.assertTrue(box.paused)

    def test_wait_frames_timeout_covers_whole_step(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = _FakeBox(tmp)
            box.frame_duration = 0.6
            r = self.play(box, {"wait_frames": 30000, "timeout_s": 1})
            self.assertEqual(r["error"]["reason"], "timeout")
            self.assertEqual(len(box.frame_calls), 2)
            self.assertLess(box.frame_calls[1][1], 0.5)  # remaining time, not 1 s again
            self.assertFalse(box.paused)  # resumed before the error

    def test_wait_frames_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = _FakeBox(tmp)
            r = self.play(box, {"wait_frames": 12000})
            self.assertEqual(r["status"], "ok", r["error"])
            self.assertEqual([a for a, _ in box.frame_calls], [10000, 2000])
            self.assertFalse(box.paused)

    def test_diagnosis_text_file_not_writable(self):
        with tempfile.TemporaryDirectory() as tmp:
            box = _FakeBox(Path(tmp) / "does" / "not_exist", paused=True)
            r = self.play(box, {"type": "x"})
            self.assertEqual(r["error"]["reason"], "paused")
            self.assertIsNone(r["error"]["screen"])
            self.assertEqual(r["error"]["screenshot"], "fake.error.png")

    def test_unexpected_exception_stops_emulator(self):
        stopped = []
        box = type("B", (), {"id": "x", "stop": lambda s: stopped.append(True)})()
        with mock.patch("dosbox_lab.scenario.Dosbox.start", return_value=box), \
                mock.patch("dosbox_lab.scenario._Player.play", side_effect=RuntimeError("broken")):
            with self.assertRaises(RuntimeError):
                play_scenario({"format": "dosbox-lab-scenario/1", "name": "x", "steps": [{"wait_s": 1}]})
        self.assertEqual(stopped, [True])

    def test_wrong_option_value_is_step_error(self):
        """ValueError/TypeError from api.py (e.g. int("many")) is a step error, not an exception."""
        class Box(_FakeBox):
            def events_start(self, **k):
                raise ValueError("invalid literal for int()")

        with tempfile.TemporaryDirectory() as tmp:
            r = self.play(Box(tmp), {"recording_start": {"kind": "events", "max_bytes": "many"}})
        self.assertEqual(r["status"], "error")
        self.assertEqual((r["error"]["nr"], r["error"]["reason"]), (0, "bad_argument"))
        self.assertIn("invalid literal", r["error"]["message"])

    def test_programming_error_stays_exception(self):
        class Box(_FakeBox):
            def events_start(self, **k):
                raise AttributeError("real error")

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(AttributeError):
                self.play(Box(tmp), {"recording_start": {"kind": "events"}})

    def test_cleanup_catches_each_error_separately(self):
        calls = []

        class Box(_FakeBox):
            def coverage_start(self, **k):
                calls.append("coverage_start")

            def coverage_save(self, name):
                raise KeyError("path")  # not a DmcpError

            def coverage_stop(self):
                calls.append("coverage_stop")

            def events_start(self, **k):
                calls.append("events_start")

            def events_stop(self):
                calls.append("events_stop")
                return {"file": "ev.jsonl"}

        with tempfile.TemporaryDirectory() as tmp:
            r = self.play(Box(tmp), {"recording_start": {"kind": "coverage"}},
                          {"recording_start": {"kind": "events"}})
        self.assertEqual(r["status"], "ok", r["error"])
        a = {e["kind"]: e for e in r["recordings"]}
        self.assertEqual(a["coverage"]["files"], [])
        self.assertEqual(a["events"]["files"], ["ev.jsonl"])
        self.assertIn("coverage_stop", calls)  # stopped despite the failed coverage_save

    def test_wait_halt_hit_exactly_at_time_limit(self):
        class Box(_FakeBox):
            polls = 0

            def translate(self, address):
                return {"linear": "00001000"}

            def break_add(self, linear):
                pass

            def break_remove(self, linear):
                pass

            def status(self):
                if self.polls == 0:  # _status_running
                    self.polls += 1
                    return {"paused": False, "stop_reason": "none", "frames": 7}
                time.sleep(0.01)  # every poll takes longer than the time limit
                self.polls += 1
                if self.polls < 3:
                    return {"paused": False, "stop_reason": "none", "frames": 7}
                return {"paused": True, "stop_reason": "breakpoint", "stop_linear": "00001000", "frames": 7}

        with tempfile.TemporaryDirectory() as tmp:
            r = self.play(Box(tmp), {"wait_halt": {"address": "l:1000"}, "timeout_s": 0.001})
        self.assertEqual(r["status"], "ok", r["error"])

    def test_recording_options_checked_before_start(self):
        def load(options):
            return load_scenario({"format": "dosbox-lab-scenario/1", "name": "x",
                                  "steps": [{"recording_start": options}]})
        load({"kind": "coverage", "name": "c", "hits": True})
        load({"kind": "calls", "targets": ["l:0"], "states": 1, "name": "c"})
        with self.assertRaises(DmcpError) as ctx:
            load({"kind": "coverage", "color": 1})
        self.assertEqual(ctx.exception.code, "bad_argument")
        self.assertIn("color", ctx.exception.message)


@need_build
class CliScenarioTest(unittest.TestCase):
    def cli(self, *args):
        p = subprocess.run([sys.executable, "-m", "dosbox_lab", "scenario", *args], cwd=str(REPO / "python"),
                           capture_output=True, text=True, timeout=120)
        return p.returncode, json.loads(p.stdout)

    def test_ok_and_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            sz = scene({"key": "${T}"}, {"wait_text": "KEY ${T}"})  # TESTPROG-TEXT
            file = Path(tmp) / "s.json"
            file.write_text(json.dumps(sz), encoding="utf-8")
            code, r = self.cli("--file", str(file), "--var", "T=b", "--output-dir", str(Path(tmp) / "out"))
            self.assertEqual((code, r["status"]), (0, "ok"), r)
            self.assertEqual(r["variables"]["T"], "b")
            sz["steps"].append({"wait_text": "NEVER", "timeout_s": 1})
            file.write_text(json.dumps(sz), encoding="utf-8")
            code, r = self.cli("--file", str(file), "--var", "T=b")
            self.assertEqual((code, r["status"]), (1, "error"))

    def test_bad_var(self):
        code, r = self.cli("--file", "x.json", "--var", "without_equals")
        self.assertEqual((code, r["error"]), (1, "bad_argument"))
        self.assertIn("NAME=VALUE", r["message"])
