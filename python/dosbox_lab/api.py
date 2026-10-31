# SPDX-License-Identifier: GPL-2.0-or-later
"""High-level API: one method per emulator function."""
import hashlib
import json
import time
from pathlib import Path
from .errors import DmcpError
from .instance import Instance
from .legacy import LEGACY_SYMBOL_FORMATS, SYMBOL_FORMAT, normalize_symbol_file


def _hex2(value):
    """int -> hex string ("21"); strings are passed through unchanged (the emulator validates them)."""
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

    # --- Execution control ---
    def status(self):
        return self.call("emu.status")

    def pause(self, until=None, timeout_s=10.0):
        """Pause the emulation. until="pmode": pause only at the first instruction in protected mode (waits up
        to timeout_s). If the emulator was already paused, the condition applies from the next resume()."""
        if until is None:
            return self.call("emu.pause")
        return self._wait_armed(self.call("emu.pause", until=until), timeout_s, until)

    def _wait_armed(self, status, timeout_s, label):
        """Wait until an armed stop condition fires. On timeout: disarm the condition, let the emulator run on
        and report a timeout - unless some halt (condition, breakpoint, watchpoint) hit in the abort window:
        then the emulator stays stopped and the status is returned."""
        if status["paused"]:
            return status
        try:
            return self.wait_paused(timeout_s)
        except DmcpError as exc:
            if exc.code != "timeout":
                raise
            status = self.call("emu.pause")  # disarms; if already paused, stop_reason is kept
            if status["stop_reason"] != "pause":
                return status  # a real halt (hit counted, one-shot removed): do not throw it away
            self.call("emu.continue")
            raise DmcpError("timeout", f"emulator did not pause in time; the condition "
                                       f"\"{label}\" was aborted, the emulator keeps running") from None

    def run_until(self, address=None, pmode=None, paging=None, cs=None, cpl=None, expression=None, timeout_s=10.0):
        """Run until all given conditions hold at the same time (at least one instruction) and return the
        status. address: any address form; cs: selector/segment as hex string; expression: e.g. "ax==5 && zf"."""
        params = {key: value for key, value in (("address", address), ("pmode", pmode), ("paging", paging),
                                                ("cs", cs), ("cpl", cpl), ("expression", expression))
                  if value is not None}
        return self._wait_armed(self.call("emu.run_until", **params), timeout_s, "run_until")

    def eval(self, expression):
        """Evaluate a condition expression on the current state: {"expression", "value"} (hex)."""
        return self.call("emu.eval", expression=expression)

    def wait_paused(self, timeout_s=10.0):
        end = time.monotonic() + timeout_s
        while True:
            status = self.status()
            if status["paused"]:
                return status
            if time.monotonic() > end:
                raise DmcpError("timeout", "emulator did not pause in time (condition not reached)")
            time.sleep(0.02)

    def resume(self):
        return self.call("emu.continue")

    def step(self, count=1):
        return self.call("emu.step", count=count)

    def step_over(self, timeout_s=10.0):
        """Like step, but CALL/INT/REP string instructions run through to the following instruction (stack
        pointer back to at least its start value). If the call does not return: timeout, the emulator keeps
        running."""
        status = self.call("emu.step_over")
        if status.get("kind") == "step":
            return status
        return self._wait_armed(status, timeout_s, "step_over")

    def frame_step(self, count=1, timeout_s=10.0):
        """Run on for `count` finished frames and then pause (stop_reason "frame")."""
        return self._wait_armed(self.call("emu.frame_step", count=count), timeout_s, "frame")

    def speed(self, mode, cycles=None):
        """mode: "normal", "turbo" or "cycles" (then cycles=100..2000000, fixed cycles per ms)."""
        if mode == "cycles":
            return self.call("emu.speed", mode=mode, cycles=cycles)
        return self.call("emu.speed", mode=mode)

    def break_add(self, target=None, condition=None, once=False, linear=None, address=None):
        """Code breakpoint. target: int = linear address, str = any address form or symbol name.
        condition: expression as for run_until ("ax==5"); once: remove after the first halt."""
        if isinstance(target, int):
            linear = target
        elif target is not None:
            address = target
        params = {"once": bool(once)}
        if address is not None:
            params["address"] = address
        if linear is not None:
            params["linear"] = linear
        if condition is not None:
            params["condition"] = condition
        return self.call("emu.break_add", **params)

    def break_remove(self, target=None, all=False, linear=None):
        """target as for break_add (int = linear, str = address form); linear= as for break_add."""
        if all:
            return self.call("emu.break_remove", all=True)
        if isinstance(target, int):
            linear = target
        if linear is not None:
            return self.call("emu.break_remove", linear=linear)
        return self.call("emu.break_remove", address=target)

    def break_int_add(self, number, ah=None, al=None):
        """Halt before INT number (optionally only for a matching AH/AL). Values as int or hex string."""
        params = {"number": _hex2(number)}
        if ah is not None:
            params["ah"] = _hex2(ah)
        if al is not None:
            params["al"] = _hex2(al)
        return self.call("emu.break_int_add", **params)

    def break_int_remove(self, number=None, ah=None, al=None, all=False):
        if all:
            return self.call("emu.break_int_remove", all=True)
        params = {"number": _hex2(number)}
        if ah is not None:
            params["ah"] = _hex2(ah)
        if al is not None:
            params["al"] = _hex2(al)
        return self.call("emu.break_int_remove", **params)

    def break_list(self):
        return self.call("emu.break_list")

    # --- Watchpoints ---
    def watch_add(self, address, length=1, kind="write", action="halt"):
        """Memory watchpoint on a linear range. kind: read|write|both;
        action: halt (halt after the access, status stop_watch) or log (access log)."""
        return self.call("watch.add", address=address, length=length, kind=kind, action=action)

    def watch_remove(self, id=None, all=False):
        if all:
            return self.call("watch.remove", all=True)
        return self.call("watch.remove", id=id)

    def watch_list(self):
        return self.call("watch.list")

    def watch_log(self, name="accesses", clear=False):
        """Write the access log to <name>.jsonl (reply: path, count, full)."""
        info = self.call("watch.log", name=name, clear=bool(clear))
        info["sha256"] = hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest()
        return info

    def watch_clear_log(self, max_entries=None):
        if max_entries is None:
            return self.call("watch.clear_log")
        return self.call("watch.clear_log", max_entries=int(max_entries))

    # --- CPU ---
    def registers(self):
        return self.call("cpu.registers")

    def set_register(self, name, value):
        """value: int or hex string as in the replies ("00000050", "0x50")."""
        if isinstance(value, str):
            try:
                value = int(value, 16)
            except ValueError:
                raise DmcpError("bad_argument", f"register value is not a hex number: {value!r}") from None
        return self.call("cpu.set_register", name=name, value=value)

    # --- Memory ---
    def mem_read_info(self, addr, length):
        return self.call("mem.read", address=addr, length=length)

    def mem_read(self, addr, length):
        return bytes.fromhex(self.mem_read_info(addr, length)["hex"])

    def mem_write(self, addr, data):
        return self.call("mem.write", address=addr, hex=bytes(data).hex().upper())

    def mem_dump(self, addr, length, name):
        info = self.call("mem.dump", address=addr, length=length, name=name)
        info["sha256"] = hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest()
        return info

    def mem_search(self, addr, length, pattern, max_hits=100):
        if isinstance(pattern, str):
            result = self.call("mem.search", address=addr, length=length, text=pattern, max_hits=max_hits)
        else:
            result = self.call("mem.search", address=addr, length=length, hex=bytes(pattern).hex().upper(),
                               max_hits=max_hits)
        return result["hits"]

    def descriptors(self, table="gdt", selector=None):
        params = {"table": table}
        if selector is not None:
            params["selector"] = selector
        return self.call("cpu.descriptors", **params)

    def translate(self, addr):
        return self.call("cpu.translate", address=addr)

    def paging(self, addr, length=0x1000):
        return self.call("cpu.paging", address=addr, length=length)

    def disassemble(self, address=None, count=10, bits=None, name=None):
        """Disassemble from address (default: current instruction). Up to 200 lines in the reply; with name up
        to 100 000 lines as <name>.jsonl (reply: path). Lines: addr, bytes, length, text, target (direct
        jumps)."""
        params = {"count": count}
        if address is not None:
            params["address"] = address
        if bits is not None:
            params["bits"] = bits
        if name is not None:
            params["name"] = name
        return self.call("cpu.disassemble", **params)

    # --- Modules ---
    def modules(self):
        return self.call("modules.list")["modules"]

    def set_segments(self, module, segments):
        """segments: [{"name", "va_start", "va_end", "base_linear"}] as hex strings; linear = base_linear + va."""
        return self.call("modules.set_segments", module=module, segments=list(segments))

    def clear_modules(self, module=None):
        if module is None:
            return self.call("modules.clear", all=True)
        return self.call("modules.clear", module=module)

    # --- Input ---
    def key(self, name, ms=60):
        return self.call("input.key", key=name, duration_ms=ms)

    def type_text(self, text, layout="de", gap_ms=40):
        return self.call("input.type", text=text, layout=layout, gap_ms=gap_ms)

    def input_pending(self):
        return self.call("input.pending")

    def wait_input(self, timeout_s=10.0):
        end = time.monotonic() + timeout_s
        while self.input_pending()["pending"] > 0:
            if time.monotonic() > end:
                raise DmcpError("timeout", "input was not processed in time (emulator paused?)")
            time.sleep(0.02)

    def release_all(self):
        return self.call("input.release_all")

    def own_input(self, on):
        return self.call("input.own", on=bool(on))

    def mouse_move(self, x, y, coords="game"):
        return self.call("input.mouse_move", x=x, y=y, coords=coords)

    def mouse_move_rel(self, dx, dy):
        return self.call("input.mouse_move_rel", dx=dx, dy=dy)

    def mouse_button(self, button, down):
        return self.call("input.mouse_button", button=button, pressed=bool(down))

    def mouse_click(self, x=None, y=None, button="left", hold_ms=80):
        if x is not None and y is not None:
            self.mouse_move(x, y)
        self.mouse_button(button, True)
        time.sleep(hold_ms / 1000.0)
        return self.mouse_button(button, False)

    def mouse_wheel(self, steps):
        return self.call("input.mouse_wheel", steps=steps)

    def mouse_state(self):
        return self.call("input.mouse_state")

    def mouse_move_program(self, x, y, x_address, y_address, width=2, attempts=12, tolerance=0, wait_s=0.06):
        """Move the mouse until the program's own position variables (x_address/y_address, `width` bytes each,
        little endian, unsigned) show x/y. For programs with their own mouse handler whose cursor does not
        follow the INT 33h position (e.g. DOS extender games). Corrects with mouse_move_rel and learns the
        factor (program units per movement step) from the observed effect."""
        def read():
            px = int.from_bytes(self.mem_read(x_address, width), "little")
            py = int.from_bytes(self.mem_read(y_address, width), "little")
            return px, py

        def steps(delta, factor):
            if delta == 0:
                return 0
            step = round(delta / factor)
            return step if step != 0 else (1 if delta > 0 else -1)

        def close(delta, factor):
            # A remainder up to half a step cannot be reached more precisely with this program
            return abs(delta) <= tolerance or abs(delta) <= abs(factor) / 2

        def result(px, py, attempts, fx, fy):
            res = {"x": px, "y": py, "attempts": attempts, "factor": [fx, fy]}
            if (px, py) != (x, y) and (abs(x - px) > tolerance or abs(y - py) > tolerance):
                res["hint"] = (f"target not exactly reachable with step size {max(abs(fx), abs(fy)):g} "
                               f"(remainder {x - px},{y - py})")
            return res

        def probe(delta, factor, done):
            # A remainder below half the learned step size is only unreachable if a single step also jumps
            # that far: programs with mouse acceleration learn too large a factor from big movements. So try
            # one single step per axis (afterwards the factor is learned anew).
            return not done and delta != 0 and abs(delta) > tolerance and abs(factor) > 1 and close(delta, factor)

        fx = fy = 1.0
        probed_x = probed_y = False
        px, py = read()
        for attempt in range(attempts):
            dx, dy = x - px, y - py
            px_probe, py_probe = probe(dx, fx, probed_x), probe(dy, fy, probed_y)
            if close(dx, fx) and close(dy, fy) and not (px_probe or py_probe):
                return result(px, py, attempt, fx, fy)
            sx = (1 if dx > 0 else -1) if px_probe else (0 if close(dx, fx) else steps(dx, fx))
            sy = (1 if dy > 0 else -1) if py_probe else (0 if close(dy, fy) else steps(dy, fy))
            probed_x, probed_y = probed_x or px_probe, probed_y or py_probe
            self.mouse_move_rel(sx, sy)
            time.sleep(wait_s)
            nx, ny = read()
            if sx and nx != px:
                fx = min(10.0, max(0.1, (nx - px) / sx))
            if sy and ny != py:
                fy = min(10.0, max(0.1, (ny - py) / sy))
            px, py = nx, ny
        if close(x - px, fx) and close(y - py, fy):
            return result(px, py, attempts, fx, fy)
        raise DmcpError("timeout", f"program position {px},{py} instead of {x},{y} after {attempts} attempts "
                                   f"(factor {fx:.2f}/{fy:.2f}; at the edge or wrong addresses?)")

    # --- Screen ---
    def screenshot(self, name="screen", rect=None):
        params = {"name": name}
        if rect is not None:
            x, y, w, h = rect
            params.update(x=x, y=y, width=w, height=h)
        info = self.call("screen.capture", **params)
        info["sha256"] = hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest()
        return info

    def screen_text(self):
        return self.call("screen.text")

    def video_state(self):
        return self.call("screen.video_state")

    # --- Events ---
    def events_start(self, types=None, int_filter=None, int_mode=None, with_return=False, max_bytes=None,
                     name="events", file_preview=None):
        """Start the event stream to <output dir>/<name>.jsonl.
        int_filter: [{"number": "21", "ah": "3D"}, ...] (hex strings); default 21h, 2Fh, 31h, 33h.
        file_preview: number of transferred data bytes shown in the file event (default 16, 0 = off)."""
        params = {"name": name, "with_return": bool(with_return)}
        if types is not None:
            params["types"] = list(types)
        if int_filter is not None:
            params["int_filter"] = list(int_filter)
        if int_mode is not None:
            params["int_mode"] = int_mode
        if max_bytes is not None:
            params["max_bytes"] = int(max_bytes)
        if file_preview is not None:
            params["file_preview"] = int(file_preview)
        return self.call("events.start", **params)

    def events_stop(self):
        return self.call("events.stop")

    def events_status(self):
        return self.call("events.status")

    def events_read(self, since_nr=0, max=50):
        return self.call("events.read", since_nr=since_nr, max=max)

    def wait_event(self, predicate, timeout_s=15.0, since_nr=0):
        """Read new events until predicate(event) is true and return that event."""
        end = time.monotonic() + timeout_s
        nr = since_nr
        while True:
            batch = self.events_read(since_nr=nr, max=500)
            for event in batch["events"]:
                if predicate(event):
                    return event
            if batch["events"]:
                nr = batch["events"][-1]["nr"]
            elif time.monotonic() > end:
                raise DmcpError("timeout", "the expected event did not arrive in time")
            else:
                time.sleep(0.05)

    # --- Coverage ---
    def coverage_start(self, max_blocks=None, resume=False, hits=False):
        params = {"resume": bool(resume), "hits": bool(hits)}
        if max_blocks is not None:
            params["max_blocks"] = int(max_blocks)
        return self.call("coverage.start", **params)

    def coverage_stop(self):
        return self.call("coverage.stop")

    def coverage_status(self):
        return self.call("coverage.status")

    def coverage_save(self, name="coverage"):
        info = self.call("coverage.save", name=name)
        info["sha256"] = hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest()
        return info

    # --- Control flow ---
    def flow_start(self, indirect=True, ring=True, ring_size=4096, with_returns=False, max_pairs=None):
        params = {"indirect": bool(indirect), "ring": bool(ring), "ring_size": int(ring_size),
                  "with_returns": bool(with_returns)}
        if max_pairs is not None:
            params["max_pairs"] = int(max_pairs)
        return self.call("flow.start", **params)

    def flow_stop(self):
        return self.call("flow.stop")

    def flow_status(self):
        return self.call("flow.status")

    def flow_save(self, name="indirect"):
        return self.call("flow.save", name=name)

    def flow_recent(self, count=100, name="recent"):
        """Write the last `count` control flow transfers (oldest first) to <name>.jsonl."""
        return self.call("flow.recent", count=count, name=name)

    # --- Call log (D29) ---
    def calls_start(self, targets=None, ranges=None, modules=None, ints=None, stack_bytes=None, max_per_target=1000,
                    max_bytes=None, name="calls", states=0, max_states=1000):
        """Record every call to the filtered targets in <name>.jsonl (one entry per call, written at the
        return). targets: address texts/symbols; ranges: [{"from": ..., "to": ...}] (to is inclusive);
        modules: module names; ints: [{"number": "21", "ah": "3F"}]. At least one filter is required.
        states=N: savestate at the entry of the first N calls per target (field state, for func_run), at most
        max_states per run."""
        params = {"name": name, "max_per_target": int(max_per_target)}
        if states != 0:
            params["states"] = int(states)
        if max_states != 1000:
            params["max_states"] = int(max_states)
        if targets is not None:
            params["targets"] = list(targets)
        if ranges is not None:
            params["ranges"] = list(ranges)
        if modules is not None:
            params["modules"] = list(modules)
        if ints is not None:
            params["ints"] = list(ints)
        if stack_bytes is not None:
            params["stack_bytes"] = int(stack_bytes)
        if max_bytes is not None:
            params["max_bytes"] = int(max_bytes)
        return self.call("calls.start", **params)

    def calls_stop(self):
        """End the recording: open calls get open=true, the sidecar file is <name>.json (json_path)."""
        return self.call("calls.stop")

    def calls_status(self):
        return self.call("calls.status")

    # --- Run a single function (D31) ---
    def func_run(self, state, write=None, regs=None, stack=None, irqs=False, max_instructions=None, name="func",
                 timeout_s=60.0):
        """Load a savestate from calls_start(states=N) (field state), optionally set registers (regs
        {"eax": "0x10"}), stack bytes from SS:ESP (stack, hex) and memory bytes (write [{"address", "hex"}],
        e.g. rebuilt code), run until the return and report registers, changed memory and INT calls.
        irqs=False holds back hardware interrupts during the run. On timeout: end "aborted" + hint.
        The emulator stays paused afterwards."""
        params = {"state": str(state), "irqs": bool(irqs), "name": name, "timeout_ms": 60000}
        if write is not None:
            params["write"] = [{"address": s["address"],
                                "hex": s["hex"] if isinstance(s["hex"], str) else bytes(s["hex"]).hex().upper()}
                               for s in write]
        if regs is not None:
            params["regs"] = {k: (v if isinstance(v, str) else f"0x{v:X}") for k, v in regs.items()}
        if stack is not None:
            params["stack"] = stack if isinstance(stack, str) else bytes(stack).hex().upper()
        if max_instructions is not None:
            params["max_instructions"] = int(max_instructions)
        self._call_when_ready("func.start", params)
        end = time.monotonic() + timeout_s
        while True:
            result = self.call("func.result")
            if not result.get("running"):
                return result
            if time.monotonic() > end:
                self.call("emu.pause")
                result = self.call("func.result")
                # Only a halt caused by our emu.pause is the timeout; if the return came just before, it stays.
                if result.get("end") == "interrupted" and result.get("stop_reason") == "pause":
                    result["end"] = "aborted"
                    result["stop_reason"] = None
                    result["hint"] = "timeout_s reached"
                return result
            time.sleep(0.02)

    # --- Symbols ---
    def symbols_load(self, path, base_linear=None):
        """Load a symbol file (format decomp-symbols/1; the legacy format decomp-symbole/1 with German keys is
        accepted as well). base_linear: mapping to use if the file brings none (linear = base_linear + va)."""
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DmcpError("bad_argument", f"symbol file not readable: {exc}") from exc
        if not isinstance(data, dict) or data.get("format") not in (SYMBOL_FORMAT,) + LEGACY_SYMBOL_FORMATS:
            raise DmcpError("bad_argument", f"symbol file does not have format={SYMBOL_FORMAT}")
        data = normalize_symbol_file(data)
        params = {key: data[key] for key in ("module", "address_space", "symbols", "segments", "base_linear")
                  if key in data}
        if base_linear is not None:
            params["base_linear_loaded"] = base_linear if isinstance(base_linear, str) else f"0x{base_linear:X}"
        if data.get("sha256") and not isinstance(data["sha256"], str):
            raise DmcpError("bad_argument", "sha256 of the symbol file must be a string")
        result = self.call("symbols.set", **params)
        if data.get("sha256"):
            result["hints"] = list(result["hints"]) + self._check_sha256(data["module"], data["sha256"])
        return result

    def _check_sha256(self, module_name, expected):
        module = next((m for m in self.modules() if m["module"] == module_name.upper()), None)
        if module is None or "file" not in module:
            return [f"sha256 cannot be checked: {module_name} has not been started in this instance yet"]
        host = self.inst.host_path(module["file"])
        if host is None or not host.exists():
            return [f"sha256 cannot be checked: {module['file']} is not in any known mount"]
        actual = hashlib.sha256(host.read_bytes()).hexdigest()
        if actual.lower() != expected.lower():
            return [f"sha256 does not match: symbol file {expected[:12]}..., loaded program {actual[:12]}..."]
        return []

    def symbol_lookup(self, name=None, address=None):
        if name is not None:
            return self.call("symbols.lookup", name=name)
        return self.call("symbols.lookup", address=address)

    def symbols_clear(self, module):
        return self.call("symbols.clear", module=module)

    # --- Snapshot ---
    def snapshot(self, label, with_extension=False, until=None):
        """Briefly pause (if needed), save memory/registers/descriptors/paging/screen, and run on.
        until="pmode": pause only once in protected mode (DOS extenders often sit in real mode, where the
        linear ranges above the RAM are missing; the result then carries `hints`).
        If the emulator is already paused, the snapshot is taken at once and `until` does not apply."""
        from . import snapshot
        return snapshot.take(self, label, with_extension=with_extension, until=until)

    # --- Savestates ---
    def state_save(self, name="state"):
        """Complete emulator state as <name>.sav (+ sidecar <name>.json) in the output directory.
        Breakpoints, watchpoints and recordings are not part of it."""
        info = self.call("state.save", name=name, timeout_ms=60000)
        info["sha256"] = hashlib.sha256(Path(info["path"]).read_bytes()).hexdigest()
        return info

    def state_load(self, name=None, path=None, skip_check=False):
        """Load a savestate (name in the output directory, or path). Checks the file and its sidecar first."""
        params = {"skip_check": bool(skip_check), "timeout_ms": 60000}
        if path is not None:
            params["path"] = str(path)
        else:
            params["name"] = name or "state"
        return self._call_when_ready("state.load", params)

    def _call_when_ready(self, method, params, timeout_s=30.0):
        """Loading before the DOS shell is up crashes the emulator; it then refuses with not_ready.
        If the emulator is running, wait; if it is paused, the error comes at once (it will never be ready)."""
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
        """PSP chain from the current program (current first) with names, command line and load segment."""
        return self.call("dos.programs")

    def dos_mcb(self):
        """Memory block chain (MCB) from the first block."""
        return self.call("dos.mcb")

    def dos_files(self):
        """Open files (system file table) and the handles of the current program."""
        return self.call("dos.files")

    def dos_target(self):
        """Target program (last started with EXEC 4B00): PSP, load segment, entry, stack, relocation."""
        return self.call("dos.target")
