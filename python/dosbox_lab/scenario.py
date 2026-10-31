# SPDX-License-Identifier: GPL-2.0-or-later
"""Scenario scripts: load, validate and play."""
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
from .legacy import LEGACY_SCENARIO_FORMATS, normalize_scenario

FORMAT = "dosbox-lab-scenario/1"
RESULT_FORMAT = "dosbox-lab-scenario-result/1"
DEFAULT_TIMEOUT_S = 30.0
HEAD_KEYS = {"format", "name", "variables", "start", "steps"}
START_KEYS = {"mounts", "autoexec", "conf", "exe", "layout"}
COMMON_KEYS = {"timeout_s", "note"}
RECORDINGS = ("calls", "coverage", "events", "flow")
KINDS = ("type", "key", "click", "mouse_program", "wait_text", "wait_memory", "wait_halt", "continue",
         "wait_frames", "wait_frame", "wait_s", "state_load", "state_save", "screenshot",
         "recording_start", "recording_stop")
BUTTONS = ("left", "right", "middle")
PATH_CHARS = set('\\/:*?"<>|')
POLL_S = 0.05
_VAR = re.compile(r"\$\$|\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _fail(place, reason):
    raise DmcpError("bad_argument", f"{place}: {reason}")


def _is_number(value):
    """Finite number (no bool, no NaN/Infinity, no overflow)."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _substitute(value, variables, place):
    """Replace ${NAME} and $$ in all strings (recursively)."""
    if isinstance(value, str):
        def replace(m):
            if m.group(0) == "$$":
                return "$"
            name = m.group(1)
            if name not in variables:
                _fail(place, f"variable {name} has no value")
            return variables[name]
        return _VAR.sub(replace, value)
    if isinstance(value, list):
        return [_substitute(v, variables, f"{place}[{i}]") for i, v in enumerate(value)]
    if isinstance(value, dict):
        return {k: _substitute(v, variables, f"{place}.{k}") for k, v in value.items()}
    return value


def _name_ok(text):
    return isinstance(text, str) and text != "" and not (set(text) & PATH_CHARS)


def _object(value, place, required, allowed=()):
    if not isinstance(value, dict):
        _fail(place, "must be an object")
    for key in required:
        if key not in value:
            _fail(place, f"{key} is missing")
    for key in value:
        if key not in set(required) | set(allowed):
            _fail(place, f"unknown key {key}")
    return value


def _start_options(kind):
    """Allowed options of recording_start: the parameters of <kind>_start, plus name for coverage/flow
    (file name for coverage_save/flow_save)."""
    names = set(inspect.signature(getattr(Dosbox, f"{kind}_start")).parameters) - {"self"}
    return names | {"name"}


def _hex_ok(text):
    if not isinstance(text, str) or text == "" or len(text) % 2:
        return False
    try:
        bytes.fromhex(text)
    except ValueError:
        return False
    return True


def _check_value(kind, value, place, base):
    """Validate the value of a step kind and return it (normalized where needed)."""
    if kind in ("type", "state_load"):
        if not isinstance(value, str) or value == "":
            _fail(place, f"{kind} needs a text")
        return str((base / value).resolve()) if kind == "state_load" else value
    if kind in ("state_save", "screenshot"):
        if not _name_ok(value):
            _fail(place, f"{kind} needs a name without path characters")
        return value
    if kind == "key":
        if isinstance(value, str) and value:
            return {"name": value, "ms": 60}
        _object(value, place, ("name",), ("ms",))
        if not isinstance(value["name"], str) or not _is_integer(value.get("ms", 60)) or value.get("ms", 60) <= 0:
            _fail(place, "key needs name (text) and ms > 0")
        return {"name": value["name"], "ms": value.get("ms", 60)}
    if kind == "click":
        _object(value, place, ("x", "y"), ("button",))
        if not _is_integer(value["x"]) or not _is_integer(value["y"]):
            _fail(place, "click needs integers x and y")
        button = value.get("button", "left")
        if button not in BUTTONS:
            _fail(place, "button must be " + ", ".join(BUTTONS))
        return {"x": value["x"], "y": value["y"], "button": button}
    if kind == "mouse_program":
        _object(value, place, ("x", "y", "x_address", "y_address"), ("width", "tolerance", "attempts", "wait_s"))
        if not _is_integer(value["x"]) or not _is_integer(value["y"]):
            _fail(place, "mouse_program needs integers x and y")
        for key in ("x_address", "y_address"):
            if not isinstance(value[key], str) or value[key] == "":
                _fail(place, f"{key} must be a non-empty text")
        for key, minimum in (("width", 1), ("attempts", 1), ("tolerance", 0)):
            if key in value and (not _is_integer(value[key]) or value[key] < minimum):
                _fail(place, f"{key} must be an integer >= {minimum}")
        if "wait_s" in value and (not _is_number(value["wait_s"]) or value["wait_s"] <= 0):
            _fail(place, "wait_s must be a number > 0")
        return dict(value)
    if kind == "wait_text":
        if isinstance(value, str) and value:
            value = {"text": value}
        _object(value, place, ("text",), ("line", "regex"))
        if not isinstance(value["text"], str) or value["text"] == "":
            _fail(place, "wait_text needs a text")
        line = value.get("line")
        if line is not None and (not _is_integer(line) or line < 0):
            _fail(place, "line must be an integer >= 0")
        regex = value.get("regex", False)
        if not isinstance(regex, bool):
            _fail(place, "regex must be true or false")
        if regex:
            try:
                re.compile(value["text"])
            except re.error as exc:
                _fail(place, f"invalid regex: {exc}")
        return {"text": value["text"], "line": line, "regex": regex}
    if kind == "wait_memory":
        _object(value, place, ("address", "hex"), ("mask",))
        if not isinstance(value["address"], str) or not _hex_ok(value["hex"]):
            _fail(place, "wait_memory needs address (text) and hex (an even number of hex digits)")
        mask = value.get("mask")
        if mask is not None and (not _hex_ok(mask) or len(mask) != len(value["hex"])):
            _fail(place, "mask must be hex with the same length as hex")
        return {"address": value["address"], "hex": value["hex"], "mask": mask}
    if kind == "wait_halt":
        _object(value, place, ("address",), ("then",))
        then = value.get("then", "halt")
        if not isinstance(value["address"], str) or then not in ("halt", "continue"):
            _fail(place, "wait_halt needs address (text) and then \"halt\" or \"continue\"")
        return {"address": value["address"], "then": then}
    if kind == "continue":
        if value is not True:
            _fail(place, "continue needs the value true")
        return True
    if kind == "wait_frames":
        if not _is_integer(value) or value < 1:
            _fail(place, "wait_frames needs an integer >= 1")
        return value
    if kind == "wait_frame":
        if not _is_integer(value) or value < 0:
            _fail(place, "wait_frame needs an integer >= 0")
        return value
    if kind == "wait_s":
        if not _is_number(value) or value <= 0:
            _fail(place, "wait_s needs a number > 0")
        return float(value)
    if kind in ("recording_start", "recording_stop"):
        if not isinstance(value, dict) or value.get("kind") not in RECORDINGS:
            _fail(place, f"{kind} needs kind ({', '.join(RECORDINGS)})")
        if kind == "recording_stop":
            _object(value, place, ("kind",))
        else:
            _object(value, place, ("kind",), _start_options(value["kind"]))
        return dict(value)
    raise AssertionError(kind)


def _check_start(start, base):
    start = _object(start if start is not None else {}, "start", (), START_KEYS)
    mounts = []
    for i, m in enumerate(start.get("mounts", [])):
        place = f"start.mounts[{i}]"
        _object(m, place, ("drive", "path"), ("cdrom",))
        if (not isinstance(m["drive"], str) or len(m["drive"]) != 1 or not m["drive"].isascii()
                or not m["drive"].isalpha()):
            _fail(place, "drive must be a single letter")
        if not isinstance(m["path"], str):
            _fail(place, "path must be a text")
        if not isinstance(m.get("cdrom", False), bool):
            _fail(place, "cdrom must be true or false")
        host = (base / m["path"]).resolve()
        if not host.is_dir():
            _fail(place, f"path {host} is not a directory")
        mounts.append(Mount(m["drive"].upper(), str(host), m.get("cdrom", False)))
    autoexec = start.get("autoexec", [])
    if not isinstance(autoexec, list) or not all(isinstance(z, str) for z in autoexec):
        _fail("start.autoexec", "must be a list of texts")
    conf = copy.deepcopy(start.get("conf", {}))
    if not isinstance(conf, dict) or not all(isinstance(v, dict) for v in conf.values()):
        _fail("start.conf", "must be an object {section: {key: value}}")
    conf.setdefault("dosbox", {}).setdefault("quit warning", "false")
    exe = start.get("exe")
    if exe is not None:
        if not isinstance(exe, str):
            _fail("start.exe", "must be a text")
        exe = str((base / exe).resolve())
    layout = start.get("layout", "de")
    if not isinstance(layout, str):
        _fail("start.layout", "must be a text")
    return {"mounts": mounts, "autoexec": autoexec, "conf": conf, "exe": exe, "layout": layout}


def load_scenario(source, variables=None):
    """Load a scenario (path or object), validate it and substitute the variables.
    Errors: DmcpError bad_argument. Scenarios in the legacy German format are translated first."""
    if isinstance(source, (str, Path)):
        path = Path(source).resolve()
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))  # -sig: BOM from PowerShell/Notepad
        except (OSError, ValueError) as exc:
            _fail(str(path), f"not readable: {exc}")
        base, origin = path.parent, str(path)
    else:
        raw, base, origin = copy.deepcopy(source), Path.cwd(), None
    if isinstance(raw, dict) and raw.get("format") in LEGACY_SCENARIO_FORMATS:
        raw = normalize_scenario(raw)
        raw["format"] = FORMAT
    _object(raw, "scenario", ("format", "name", "steps"), HEAD_KEYS)
    if raw["format"] != FORMAT:
        _fail("format", f"must be {FORMAT}")
    if not _name_ok(raw["name"]):
        _fail("name", "must be a name without path characters")
    values = raw.get("variables", {})
    if not isinstance(values, dict) or not all(isinstance(v, str) for v in values.values()):
        _fail("variables", "must be an object {NAME: \"value\"} with text values")
    values = dict(values)
    for k, v in (variables or {}).items():
        if not isinstance(v, str):
            _fail("variables", f"{k} must be a text")
        values[k] = v
    start = _check_start(_substitute(raw.get("start"), values, "start"), base)
    if not isinstance(raw["steps"], list):
        _fail("steps", "must be a list")
    steps = []
    for i, raw_step in enumerate(raw["steps"]):
        place = f"steps[{i}]"
        if not isinstance(raw_step, dict):
            _fail(place, "must be an object")
        kinds = [k for k in raw_step if k not in COMMON_KEYS]
        if len(kinds) != 1:
            _fail(place, "needs exactly one step kind")
        kind = kinds[0]
        if kind not in KINDS:
            _fail(place, f"unknown step kind {kind}")
        timeout = raw_step.get("timeout_s", DEFAULT_TIMEOUT_S)
        if not _is_number(timeout) or timeout <= 0:
            _fail(place, "timeout_s must be a number > 0")
        note = raw_step.get("note")
        if note is not None and not isinstance(note, str):
            _fail(place, "note must be a text")
        note = _substitute(note, values, f"{place}.note")
        value = _check_value(kind, _substitute(raw_step[kind], values, place), f"{place}.{kind}", base)
        steps.append({"kind": kind, "value": value, "timeout_s": float(timeout), "note": note})
    return {"name": raw["name"], "source": origin, "base": base, "variables": values, "start": start,
            "steps": steps}


class _StepError(Exception):
    def __init__(self, reason, message):
        super().__init__(message)
        self.reason, self.message = reason, message


class _Player:
    def __init__(self, sz, box):
        self.sz, self.box = sz, box
        self.r = {"format": RESULT_FORMAT, "name": sz["name"], "source": sz["source"],
                  "variables": sz["variables"], "instance": box.id, "status": "ok", "steps_done": 0,
                  "steps_total": len(sz["steps"]), "duration_s": 0.0, "frames": None, "steps": [],
                  "savestates": [], "screenshots": [], "recordings": [], "error": None, "path": None}
        self.running = {}  # kind -> entry in recordings
        self._save_name = {}  # kind -> name from recording_start (coverage/flow: file name when saving)

    # --- Helpers ---
    def _status_running(self):
        status = self.box.status()
        if status["paused"]:
            raise _StepError("paused", f"emulator is paused (stop_reason {status['stop_reason']})")
        return status

    def _wait(self, condition, timeout_s, what):
        end = time.monotonic() + timeout_s
        while True:
            self._status_running()
            if condition():
                return
            if time.monotonic() > end:
                raise _StepError("timeout", f"{what} not reached after {timeout_s:g} s")
            time.sleep(POLL_S)

    # --- Steps ---
    def _step_type(self, value, timeout_s):
        self._status_running()
        self.box.type_text(value, layout=self.sz["start"]["layout"])
        self.box.wait_input(timeout_s)

    def _step_key(self, value, timeout_s):
        self._status_running()
        self.box.key(value["name"], ms=value["ms"])
        self.box.wait_input(timeout_s)

    def _step_click(self, value, timeout_s):
        self._status_running()
        self.box.mouse_click(value["x"], value["y"], button=value["button"])

    def _step_mouse_program(self, value, timeout_s):
        self._status_running()
        self.box.mouse_move_program(**value)

    def _step_wait_text(self, value, timeout_s):
        def matches():
            try:
                lines = self.box.screen_text()["lines"]
            except DmcpError as exc:
                if exc.code == "instance_dead":
                    raise
                return False  # graphics mode or start-up phase: not yet
            if value["line"] is not None:
                lines = lines[value["line"]:value["line"] + 1]
            if value["regex"]:
                return any(re.search(value["text"], z) for z in lines)
            return any(value["text"] in z for z in lines)
        self._wait(matches, timeout_s, f"text {value['text']!r}")

    def _step_wait_memory(self, value, timeout_s):
        expected = bytes.fromhex(value["hex"])
        mask = bytes.fromhex(value["mask"]) if value["mask"] else b"\xFF" * len(expected)

        def matches():
            info = self.box.mem_read_info(value["address"], len(expected))
            if info.get("missing"):
                return False  # missing pages read as filler bytes: not yet
            actual = bytes.fromhex(info["hex"])
            return all((a & m) == (e & m) for a, e, m in zip(actual, expected, mask))
        self._wait(matches, timeout_s, f"memory {value['address']} = {value['hex']}")

    def _step_wait_frames(self, value, timeout_s):
        self._status_running()
        end = time.monotonic() + timeout_s  # applies to the whole step, not to each piece
        remaining = value
        while remaining > 0:
            time_left = end - time.monotonic()
            if time_left <= 0:
                self.box.resume()  # stopped at the frame end of the last piece: let it run on
                raise _StepError("timeout", f"{value} frames not reached after {timeout_s:g} s")
            piece = min(remaining, 10000)
            status = self.box.frame_step(piece, timeout_s=time_left)
            if status["stop_reason"] != "frame":
                raise _StepError("paused", f"emulator halted during wait_frames "
                                           f"(stop_reason {status['stop_reason']})")
            remaining -= piece
        self.box.resume()

    def _step_wait_frame(self, value, timeout_s):
        self._wait(lambda: self.box.status()["frames"] >= value, timeout_s, f"frame {value}")

    def _step_wait_s(self, value, timeout_s):
        self._status_running()
        time.sleep(value)

    def _step_wait_halt(self, value, timeout_s):
        self._status_running()
        target = self.box.translate(value["address"])
        if "linear" not in target:
            raise _StepError("bad_argument", f"{value['address']} has no linear address")
        linear = int(target["linear"], 16)
        self.box.break_add(linear)

        def at_target():
            """True on a halt at the target; any other halt (watchpoint, REP partial halt) is resumed."""
            status = self.box.status()
            if not status["paused"]:
                return False
            if status["stop_reason"] == "breakpoint" and int(status.get("stop_linear", "0"), 16) == linear:
                return True
            self.box.resume()
            return False
        try:
            end = time.monotonic() + timeout_s
            while not at_target():
                if time.monotonic() > end:
                    if at_target():  # a halt right at the time limit still counts
                        break
                    raise _StepError("timeout",
                                     f"breakpoint {value['address']} not reached after {timeout_s:g} s")
                time.sleep(POLL_S)
        finally:
            try:
                self.box.break_remove(linear)
            except DmcpError:
                pass
        if value["then"] == "continue":
            self.box.resume()

    def _step_continue(self, value, timeout_s):
        self.box.resume()

    def _step_state_load(self, value, timeout_s):
        self.box.state_load(path=value)
        if self.box.status()["paused"]:
            self.box.resume()  # state_load keeps the run state; spec: the emulator runs afterwards

    def _step_state_save(self, value, timeout_s, nr):
        info = self.box.state_save(value)
        self.r["savestates"].append({"nr": nr, "name": value, "path": info["path"]})

    def _step_screenshot(self, value, timeout_s, nr):
        info = self.box.screenshot(value)
        self.r["screenshots"].append({"nr": nr, "name": value, "path": info["path"]})

    def _step_recording_start(self, value, timeout_s, nr):
        kind = value["kind"]
        options = {k: v for k, v in value.items() if k != "kind"}
        name = options.pop("name", None) if kind in ("coverage", "flow") else None
        if kind in self.running:
            # The first file stays in the result, so end it properly first (on an error the entry stays in
            # `running` and _cleanup tries once more).
            old = self.running[kind]
            old["files"] = self._stop(kind)
            old["hint"] = "replaced"
            del self.running[kind]
        getattr(self.box, f"{kind}_start")(**options)
        entry = {"kind": kind, "nr_start": nr, "nr_stop": None, "self_stopped": False, "files": []}
        self.running[kind] = entry
        self._save_name[kind] = name
        self.r["recordings"].append(entry)

    def _stop(self, kind):
        """End a recording and return its files."""
        if kind == "calls":
            info = self.box.calls_stop()
            return [info["file"], info["json_path"]]
        if kind == "events":
            return [self.box.events_stop()["file"]]
        name = self._save_name.get(kind)
        if kind == "coverage":
            save, stop, default = self.box.coverage_save, self.box.coverage_stop, "coverage"
        else:
            save, stop, default = self.box.flow_save, self.box.flow_stop, "indirect"
        error = None
        try:
            info = save(name or default)
        except Exception as exc:  # stop anyway (it slows the emulator down otherwise), then report the error
            error = exc
        try:
            stop()
        except Exception:
            if error is None:
                raise
        if error is not None:
            raise error
        return [info[k] for k in ("path", "json_path", "hits_path") if info.get(k)]

    def _step_recording_stop(self, value, timeout_s, nr):
        kind = value["kind"]
        if kind not in self.running:
            raise _StepError("bad_argument", f"no running recording {kind}")
        entry = self.running.pop(kind)
        entry["files"] = self._stop(kind)
        entry["nr_stop"] = nr

    # --- Flow ---
    def _diagnosis(self):
        """Screen text and screenshot after an error; (None, None) if the emulator is no longer alive."""
        name = self.sz["name"]
        text_path = frame_path = None
        try:
            try:
                lines = self.box.screen_text()["lines"]
            except DmcpError as exc:
                if exc.code == "instance_dead":
                    raise
                lines = []
            text_path = Path(self.box.inst.outdir) / f"{name}.error.txt"
            try:
                text_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
            except OSError:
                text_path = None
            frame_path = self.box.screenshot(f"{name}.error")["path"]
        except DmcpError:
            if not self._alive():
                return None, None
        return (str(text_path) if text_path else None), frame_path

    def _alive(self):
        try:
            return bool(self.box.inst.alive())
        except Exception:
            return False

    def play(self):
        start = time.monotonic()
        for nr, step in enumerate(self.sz["steps"]):
            t0 = time.monotonic()
            method = getattr(self, f"_step_{step['kind']}")
            try:
                if step["kind"] in ("state_save", "screenshot", "recording_start", "recording_stop"):
                    method(step["value"], step["timeout_s"], nr)
                else:
                    method(step["value"], step["timeout_s"])
            except (_StepError, DmcpError, ValueError, TypeError) as exc:
                if isinstance(exc, _StepError):
                    reason, message = exc.reason, exc.message
                elif isinstance(exc, DmcpError):
                    reason, message = exc.code, exc.message
                else:  # wrong option value (api.py: int("many") and the like): a step error, not an exception
                    reason, message = "bad_argument", f"{type(exc).__name__}: {exc}"
                if reason != "instance_dead" and not self._alive():
                    reason = "instance_dead"
                screen, frame = (None, None) if reason == "instance_dead" else self._diagnosis()
                self.r["status"] = "error"
                self.r["error"] = {"nr": nr, "kind": step["kind"], "reason": reason, "message": message,
                                   "screen": screen, "screenshot": frame}
                break
            self.r["steps"].append({"nr": nr, "kind": step["kind"], "duration_s": round(time.monotonic() - t0, 3),
                                    "note": step["note"]})
            self.r["steps_done"] = nr + 1
        self._cleanup()
        self.r["duration_s"] = round(time.monotonic() - start, 3)
        try:
            self.r["frames"] = self.box.status()["frames"]
        except DmcpError:
            self.r["frames"] = None
        self._write_result()
        return self.r

    def _cleanup(self):
        """Stop running recordings (self_stopped); if the emulator is gone, files stay empty."""
        for kind, entry in list(self.running.items()):
            entry["self_stopped"] = True
            try:
                entry["files"] = self._stop(kind)
            except Exception:  # per recording: one error aborts neither the cleanup nor play()
                entry["files"] = []
        self.running.clear()

    def _write_result(self):
        try:
            path = Path(self.box.inst.outdir) / f"{self.sz['name']}.scenario.json"
            self.r["path"] = str(path)
            path.write_text(json.dumps(self.r, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError:
            self.r["path"] = None


def play_scenario(source, variables=None, headless=True, stop_at_end=False, output_dir=None):
    """Load a scenario and play it in a new instance. Returns (box, result); box is None with stop_at_end or
    if the emulator is no longer alive. Step errors are in the result (status "error")."""
    sz = load_scenario(source, variables)
    start = sz["start"]
    box = Dosbox.start(mounts=start["mounts"], autoexec=start["autoexec"], headless=headless, exe=start["exe"],
                       conf=start["conf"], outdir=output_dir)
    try:
        result = _Player(sz, box).play()
    except BaseException:
        try:
            box.stop()  # unexpected error (not a step error): do not leave an orphaned instance behind
        except Exception:
            pass
        raise
    if stop_at_end or not box.inst.alive():
        box.stop()
        return None, result
    return box, result
