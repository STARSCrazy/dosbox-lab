# SPDX-License-Identifier: GPL-2.0-or-later
"""MCP server (stdio) for dosbox-lab. Start: python -m dosbox_lab.mcp_server"""
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


def _box(instance: Optional[str]) -> Dosbox:
    inst_id = instance or newest_instance()
    if inst_id is None:
        raise DmcpError("bad_argument", "no running instance - call dosbox_start first")
    if inst_id not in _boxes:
        _boxes[inst_id] = Dosbox.attach(inst_id)
    return _boxes[inst_id]


@mcp.tool()
def dosbox_start(drive_c: str, commands: Optional[list[str]] = None, cdrom_d: Optional[str] = None,
                 headless: bool = False) -> dict:
    """Start an emulator instance. drive_c: host directory for C:. commands: autoexec lines
    (e.g. ["GAME.EXE"]). cdrom_d: optional host directory mounted as CD drive D:. headless: no window.
    Returns the instance id and the output directory."""
    mounts = [Mount("C", drive_c)] + ([Mount("D", cdrom_d, cdrom=True)] if cdrom_d else [])
    box = Dosbox.start(mounts=mounts, autoexec=commands or [], headless=headless)
    _boxes[box.id] = box
    return {"instance": box.id, "output_dir": str(box.inst.outdir)}


@mcp.tool()
def dosbox_stop(instance: Optional[str] = None) -> dict:
    """Stop an instance."""
    box = _box(instance)
    box.stop()
    _boxes.pop(box.id, None)
    return {"stopped": box.id}


@mcp.tool()
def dosbox_list() -> dict:
    """List the known instances (running and exited)."""
    from .cli import _instances
    return {"instances": [{k: v for k, v in i.items() if not k.startswith("_")} for i in _instances()]}


@mcp.tool()
def run_batch(drive_c: str, command: str, timeout_s: float = 60.0, drive_d: Optional[str] = None,
              cdrom_d: bool = False, max_output: int = 16 * 1024 * 1024) -> dict:
    """Run one DOS command (program, .BAT file or shell command) in its own windowless instance and stop the
    instance afterwards. drive_c: host directory for C: (writable, current drive). drive_d: optional directory
    for D: (cdrom_d=True: read-only). Returns: status (done/timeout/shell_exited/crashed; shell_exited = the
    command contained EXIT), exit_code (errorlevel), stdout/stderr as file paths plus the beginning as text,
    screen (screen.txt or screen.png), files (new/changed/deleted in C:/D:), runtime_s. Programs that wait for a
    key run into the time limit."""
    mounts = [Mount("C", drive_c)] + ([Mount("D", drive_d, cdrom=cdrom_d)] if drive_d else [])
    return _run_batch(mounts, command, timeout_s=timeout_s, max_output=max_output)


@mcp.tool()
def emu_status(instance: Optional[str] = None) -> dict:
    """Is the emulator paused, and why? Also CS:EIP, turbo mode and protected mode."""
    return _box(instance).status()


@mcp.tool()
def emu_pause(until: Optional[str] = None, timeout_s: float = 10.0, instance: Optional[str] = None) -> dict:
    """Pause the emulation. until="pmode": pause at the first instruction in protected mode (waits up to
    timeout_s); needed for selector addresses with DOS extenders. Use emu_run_until for other conditions."""
    return _box(instance).pause(until=until, timeout_s=timeout_s)


@mcp.tool()
def emu_continue(instance: Optional[str] = None) -> dict:
    """Let the emulation run on."""
    return _box(instance).resume()


@mcp.tool()
def emu_step(count: int = 1, instance: Optional[str] = None) -> dict:
    """While paused, execute exactly `count` instructions."""
    return _box(instance).step(count)


@mcp.tool()
def emu_speed(mode: str, cycles: Optional[int] = None, instance: Optional[str] = None) -> dict:
    """Set the speed: "normal", "turbo" or "cycles" (then cycles=100..2000000, a fixed number of cycles)."""
    return _box(instance).speed(mode, cycles=cycles)


@mcp.tool()
def break_add(target: Optional[str] = None, condition: Optional[str] = None, once: bool = False,
              linear: Optional[str] = None, instance: Optional[str] = None) -> dict:
    """Add a code breakpoint. target: any address form ("0043:000406F1", "r:1234:0100", "l:010406F1",
    "va:PROGRAM.EXE:406F1", "rel:...") or a symbol name. condition: expression such as "ax==5 && zf".
    once: remove the breakpoint after the first halt. linear (hex) is only kept for old callers."""
    box = _box(instance)
    if target is None and linear is not None:
        return box.break_add(int(linear, 16), condition=condition, once=once)
    return box.break_add(target, condition=condition, once=once)


@mcp.tool()
def break_remove(target: Optional[str] = None, all: bool = False, linear: Optional[str] = None,
                 instance: Optional[str] = None) -> dict:
    """Remove one breakpoint (target as in break_add, or linear as hex) or all of them."""
    box = _box(instance)
    if all:
        return box.break_remove(all=True)
    return box.break_remove(int(linear, 16) if target is None and linear else target)


@mcp.tool()
def break_list(instance: Optional[str] = None) -> dict:
    """List all breakpoints with address, condition and hit count, plus the interrupt breakpoints."""
    return _box(instance).break_list()


@mcp.tool()
def break_int_add(number: str, ah: Optional[str] = None, al: Optional[str] = None,
                  instance: Optional[str] = None) -> dict:
    """Halt before an INT instruction (number/ah/al as hex strings, e.g. "21", "4C")."""
    return _box(instance).break_int_add(number, ah=ah, al=al)


@mcp.tool()
def break_int_remove(number: Optional[str] = None, ah: Optional[str] = None, al: Optional[str] = None,
                     all: bool = False, instance: Optional[str] = None) -> dict:
    """Remove one interrupt breakpoint or all of them."""
    return _box(instance).break_int_remove(number, ah=ah, al=al, all=all)


@mcp.tool()
def emu_run_until(address: Optional[str] = None, pmode: Optional[bool] = None, paging: Optional[bool] = None,
                  cs: Optional[str] = None, cpl: Optional[int] = None, expression: Optional[str] = None,
                  timeout_s: float = 10.0, instance: Optional[str] = None) -> dict:
    """Run until all given conditions hold at the same time (at least one instruction). Example:
    cs="0043", cpl=3. expression: registers/flags/memory, e.g. "eax==5 && byte[l:01000004]>3"."""
    return _box(instance).run_until(address=address, pmode=pmode, paging=paging, cs=cs, cpl=cpl,
                                    expression=expression, timeout_s=timeout_s)


@mcp.tool()
def emu_step_over(timeout_s: float = 10.0, instance: Optional[str] = None) -> dict:
    """Single step; CALL/INT/REP run through to the following instruction (the emulator must be paused)."""
    return _box(instance).step_over(timeout_s=timeout_s)


@mcp.tool()
def emu_frame_step(count: int = 1, timeout_s: float = 10.0, instance: Optional[str] = None) -> dict:
    """Run on for `count` emulated frames, then pause."""
    return _box(instance).frame_step(count, timeout_s=timeout_s)


@mcp.tool()
def emu_eval(expression: str, instance: Optional[str] = None) -> dict:
    """Evaluate an expression (registers, flags, byte/word/dword[ADDR]) in the current state."""
    return _box(instance).eval(expression)


@mcp.tool()
def cpu_disassemble(address: Optional[str] = None, count: int = 10, bits: Optional[int] = None,
                    name: Optional[str] = None, instance: Optional[str] = None) -> dict:
    """Disassemble from address (default: the current instruction) with symbols and jump targets. Up to 200
    lines in the reply; more only with name (then written to <name>.jsonl, reply contains path)."""
    return _box(instance).disassemble(address, count=count, bits=bits, name=name)


@mcp.tool()
def watch_add(address: str, length: int = 1, kind: str = "write", action: str = "halt",
              instance: Optional[str] = None) -> dict:
    """Add a memory watchpoint on a linear range. kind: read|write|both. action: halt (halt after the access,
    status stop_watch) or log (access log, see watch_log)."""
    return _box(instance).watch_add(address, length=length, kind=kind, action=action)


@mcp.tool()
def watch_remove(id: Optional[int] = None, all: bool = False, instance: Optional[str] = None) -> dict:
    """Remove one watchpoint or all of them."""
    return _box(instance).watch_remove(id, all=all)


@mcp.tool()
def watch_list(instance: Optional[str] = None) -> dict:
    """List the watchpoints with hit counts and the state of the access log."""
    return _box(instance).watch_list()


@mcp.tool()
def watch_log(name: str = "accesses", clear: bool = False, instance: Optional[str] = None) -> dict:
    """Write the access log to <name>.jsonl (reply: path, count). clear: empty the log afterwards."""
    return _box(instance).watch_log(name, clear=clear)


@mcp.tool()
def watch_clear_log(max_entries: Optional[int] = None, instance: Optional[str] = None) -> dict:
    """Empty the access log; optionally set a new size (1000..1000000 entries)."""
    return _box(instance).watch_clear_log(max_entries)


@mcp.tool()
def state_save(name: str = "state", instance: Optional[str] = None) -> dict:
    """Save the complete emulator state (<name>.sav + <name>.json in the output directory)."""
    return _box(instance).state_save(name)


@mcp.tool()
def state_load(name: Optional[str] = None, path: Optional[str] = None, skip_check: bool = False,
               instance: Optional[str] = None) -> dict:
    """Load a savestate (breakpoints, watchpoints and recordings are left as they are). name: file in the
    output directory; path: any file. skip_check: do not verify the savestate and its sidecar file first."""
    return _box(instance).state_load(name, path=path, skip_check=skip_check)


@mcp.tool()
def dos_programs(instance: Optional[str] = None) -> dict:
    """PSP chain, current program first, with name, command line and load segment."""
    return _box(instance).dos_programs()


@mcp.tool()
def dos_mcb(instance: Optional[str] = None) -> dict:
    """DOS memory block chain (MCB)."""
    return _box(instance).dos_mcb()


@mcp.tool()
def dos_files(instance: Optional[str] = None) -> dict:
    """Open files (system file table) and the handles of the current program."""
    return _box(instance).dos_files()


@mcp.tool()
def dos_target(instance: Optional[str] = None) -> dict:
    """Target program (started last with EXEC 4B00): PSP, load segment, entry point, stack, relocation."""
    return _box(instance).dos_target()


@mcp.tool()
def mouse_move_program(x: int, y: int, x_address: str, y_address: str, width: int = 2, tolerance: int = 0,
                       instance: Optional[str] = None) -> dict:
    """Move the mouse until the program's own position variables (x_address/y_address, `width` bytes each)
    show x/y. For programs with their own mouse handler."""
    return _box(instance).mouse_move_program(x, y, x_address, y_address, width=width, tolerance=tolerance)


@mcp.tool()
def cpu_registers(instance: Optional[str] = None) -> dict:
    """All registers including segment bases, CR0-CR4, flags, mode (real/pm/v86), bitness, CPL and paging."""
    return _box(instance).registers()


@mcp.tool()
def cpu_set_register(name: str, value: str, instance: Optional[str] = None) -> dict:
    """Set eax..edi, ebp, esp, eip or eflags (value as a hex string)."""
    return _box(instance).set_register(name, int(value, 16))


@mcp.tool()
def cpu_descriptors(table: str = "gdt", selector: Optional[str] = None, instance: Optional[str] = None) -> dict:
    """List the GDT, LDT or IDT (protected mode); selector (hex) restricts the list to one entry."""
    return _box(instance).descriptors(table, int(selector, 16) if selector else None)


@mcp.tool()
def cpu_translate(address: str, instance: Optional[str] = None) -> dict:
    """Convert an address into all forms. Input forms: "SSSS:OOOOOOOO", "r:SSSS:OOOO", "l:<hex>", "p:<hex>"."""
    return _box(instance).translate(address)


@mcp.tool()
def cpu_paging(address: str, length: int = 4096, instance: Optional[str] = None) -> dict:
    """Page table entries for a linear range (CR3, present, physical address)."""
    return _box(instance).paging(address, length)


@mcp.tool()
def mem_read(address: str, length: int, instance: Optional[str] = None) -> dict:
    """Read up to 4096 bytes as hex. Use mem_dump for more."""
    return _box(instance).mem_read_info(address, length)


@mcp.tool()
def mem_write(address: str, hex: str, instance: Optional[str] = None) -> dict:
    """Write bytes (hex string)."""
    return _box(instance).mem_write(address, bytes.fromhex(hex))


@mcp.tool()
def mem_dump(address: str, length: int, name: str, instance: Optional[str] = None) -> dict:
    """Write a memory range to <name>.bin (+ .json); returns the path, SHA-256 and missing pages."""
    return _box(instance).mem_dump(address, length, name)


@mcp.tool()
def mem_search(address: str, length: int, text: Optional[str] = None, hex: Optional[str] = None,
               max_hits: int = 100, instance: Optional[str] = None) -> dict:
    """Search a range for text or hex bytes."""
    pattern = text if text is not None else bytes.fromhex(hex or "")
    return {"hits": _box(instance).mem_search(address, length, pattern, max_hits)}


@mcp.tool()
def key(key: str, duration_ms: int = 60, instance: Optional[str] = None) -> dict:
    """Press a key or key combination in the emulator, e.g. "ENTER", "ESC", "F1", "ALT+X"."""
    return _box(instance).key(key, duration_ms)


@mcp.tool()
def type_text(text: str, layout: str = "de", instance: Optional[str] = None) -> dict:
    """Type text in the emulator (layout "de" for keyb gr, otherwise "us")."""
    return _box(instance).type_text(text, layout)


@mcp.tool()
def mouse_move(x: float, y: float, coords: str = "game", instance: Optional[str] = None) -> dict:
    """Move the emulator mouse to the game pixel (x, y). The real mouse is left alone."""
    return _box(instance).mouse_move(x, y, coords)


@mcp.tool()
def mouse_click(x: Optional[float] = None, y: Optional[float] = None, button: str = "left",
                instance: Optional[str] = None) -> dict:
    """Click (optionally move there first). button: left, right, middle."""
    return _box(instance).mouse_click(x, y, button)


@mcp.tool()
def mouse_button(button: str, pressed: bool, instance: Optional[str] = None) -> dict:
    """Press or release a mouse button (for dragging)."""
    return _box(instance).mouse_button(button, pressed)


@mcp.tool()
def mouse_wheel(steps: int, instance: Optional[str] = None) -> dict:
    """Turn the mouse wheel (negative = up)."""
    return _box(instance).mouse_wheel(steps)


@mcp.tool()
def mouse_state(instance: Optional[str] = None) -> dict:
    """INT 33h position, buttons and range."""
    return _box(instance).mouse_state()


@mcp.tool()
def input_own(on: bool, instance: Optional[str] = None) -> dict:
    """on=True: ignore the real mouse/keyboard in the emulator window (default). on=False: a human may play
    along."""
    return _box(instance).own_input(on)


@mcp.tool()
def screenshot(name: str = "screen", x: Optional[int] = None, y: Optional[int] = None,
               width: Optional[int] = None, height: Optional[int] = None, show: bool = True,
               instance: Optional[str] = None):
    """Screenshot as PNG (optionally a cropped area x/y/width/height). show=True also returns the image
    itself."""
    rect = (x, y, width, height) if width is not None else None
    info = _box(instance).screenshot(name, rect)
    if show:
        return [json.dumps(info, ensure_ascii=False), Image(path=info["path"])]
    return info


@mcp.tool()
def screen_text(instance: Optional[str] = None) -> dict:
    """Text mode screen as lines."""
    return _box(instance).screen_text()


@mcp.tool()
def video_state(instance: Optional[str] = None) -> dict:
    """Video mode, type and resolution."""
    return _box(instance).video_state()


@mcp.tool()
def events_start(types: Optional[list[str]] = None, int_filter: Optional[list[dict]] = None,
                 int_mode: Optional[str] = None, with_return: bool = False, max_bytes: Optional[int] = None,
                 name: str = "events", file_preview: Optional[int] = None, instance: Optional[str] = None) -> dict:
    """Start the event stream to <output_dir>/<name>.jsonl. types: exec, exit, int, int_ret, mode,
    new_code_selector, file (default: all). file_preview: number of data bytes shown in a file event.
    int_filter: [{"number": "21", "ah": "3D"}] (default 21h, 2Fh, 31h, 33h). int_mode: all|pm|real.
    with_return: also log int_ret for every logged interrupt (INT 31h always)."""
    return _box(instance).events_start(types, int_filter, int_mode, with_return, max_bytes, name, file_preview)


@mcp.tool()
def events_stop(instance: Optional[str] = None) -> dict:
    """Stop the event stream; the file is complete afterwards."""
    return _box(instance).events_stop()


@mcp.tool()
def events_status(instance: Optional[str] = None) -> dict:
    """Is the stream active? Event count, bytes, limit, file."""
    return _box(instance).events_status()


@mcp.tool()
def events_read(since_nr: int = 0, max: int = 50, instance: Optional[str] = None) -> dict:
    """New events after since_nr (at most 500, taken from the last 10000). The file is authoritative."""
    return _box(instance).events_read(since_nr, max)


@mcp.tool()
def coverage_start(max_blocks: Optional[int] = None, resume: bool = False, hits: bool = False,
                   instance: Optional[str] = None) -> dict:
    """Start code coverage (one bit per instruction start, linear). hits: hit counter per basic block.
    resume: keep the data of an earlier run."""
    return _box(instance).coverage_start(max_blocks, resume, hits)


@mcp.tool()
def coverage_stop(instance: Optional[str] = None) -> dict:
    """Stop code coverage (the data is kept)."""
    return _box(instance).coverage_stop()


@mcp.tool()
def coverage_save(name: str = "coverage", instance: Optional[str] = None) -> dict:
    """Write <name>.bin (DMCV) + <name>.json (+ <name>_hits.jsonl). Returns the paths."""
    return _box(instance).coverage_save(name)


@mcp.tool()
def coverage_status(instance: Optional[str] = None) -> dict:
    """Is coverage active? Number of blocks, limit."""
    return _box(instance).coverage_status()


@mcp.tool()
def flow_start(indirect: bool = True, ring: bool = True, ring_size: int = 4096, with_returns: bool = False,
               instance: Optional[str] = None) -> dict:
    """Start control flow recording: indirect jumps/calls and a ring buffer of the most recent transfers."""
    return _box(instance).flow_start(indirect, ring, ring_size, with_returns)


@mcp.tool()
def flow_stop(instance: Optional[str] = None) -> dict:
    """Stop control flow recording (the data is kept)."""
    return _box(instance).flow_stop()


@mcp.tool()
def flow_save(name: str = "indirect", instance: Optional[str] = None) -> dict:
    """Write the indirect source/target pairs to <name>.jsonl."""
    return _box(instance).flow_save(name)


@mcp.tool()
def flow_recent(count: int = 100, name: str = "recent", instance: Optional[str] = None) -> dict:
    """Write the most recent control flow transfers (oldest first) to <name>.jsonl, e.g. after a breakpoint."""
    return _box(instance).flow_recent(count, name)


@mcp.tool()
def flow_status(instance: Optional[str] = None) -> dict:
    """Is recording active? Pairs, ring size, instruction counter."""
    return _box(instance).flow_status()


@mcp.tool()
def calls_start(targets: Optional[list[str]] = None, ranges: Optional[list[dict]] = None,
                modules: Optional[list[str]] = None, ints: Optional[list[dict]] = None,
                stack_bytes: Optional[int] = None, max_per_target: int = 1000, max_bytes: Optional[int] = None,
                name: str = "calls", states: int = 0, max_states: int = 1000,
                instance: Optional[str] = None) -> dict:
    """Call log to <output_dir>/<name>.jsonl: one entry per call with registers/flags/stack on entry and
    registers on return. At least one filter is required: targets (addresses/symbols), ranges
    [{"from","to"}], modules (module names), ints [{"number": "21", "ah": "3F"}]. stack_bytes defaults to
    32 (16 bit) / 64 (32 bit). states=N: save the state on entry of the first N calls per target (field
    "state", for func_run), at most max_states per run. Slows the emulator down (instruction hook); call
    calls_stop after the analysis."""
    return _box(instance).calls_start(targets, ranges, modules, ints, stack_bytes, max_per_target, max_bytes, name,
                                      states, max_states)


@mcp.tool()
def func_run(state: str, write: Optional[list[dict]] = None, regs: Optional[dict] = None,
             stack: Optional[str] = None, irqs: bool = False, max_instructions: Optional[int] = None,
             name: str = "func", timeout_s: float = 60.0, instance: Optional[str] = None) -> dict:
    """Re-run a recorded call: loads state (field "state" from calls.jsonl, created by
    calls_start(states=N)), optionally sets regs {"eax": "0x10"}, stack (hex, from SS:ESP) and write
    [{"address": "l:...", "hex": "..."}] (e.g. reconstructed code bytes), and runs until the return.
    Returns: end, regs_out, changed (memory ranges with before/after), ints. Hardware IRQs are held back unless
    irqs=true. The emulator stays paused afterwards."""
    return _box(instance).func_run(state, write, regs, stack, irqs, max_instructions, name, timeout_s)


@mcp.tool()
def calls_stop(instance: Optional[str] = None) -> dict:
    """Stop the call log; unfinished calls get open=true; the sidecar file is <name>.json (json_path)."""
    return _box(instance).calls_stop()


@mcp.tool()
def calls_status(instance: Optional[str] = None) -> dict:
    """Is the call log active? Entries written, open, omitted, bytes, file."""
    return _box(instance).calls_status()


@mcp.tool()
def modules_list(instance: Optional[str] = None) -> dict:
    """Known modules: EXEC data (PSP, load segment, rel_base), va mapping with its origin, symbol count."""
    return {"modules": _box(instance).modules()}


@mcp.tool()
def modules_set_segments(module: str, segments: list[dict], instance: Optional[str] = None) -> dict:
    """Set the va mapping: [{"name","va_start","va_end","base_linear"}] (hex strings), linear = base_linear + va."""
    return _box(instance).set_segments(module, segments)


@mcp.tool()
def symbols_load(path: str, base_linear: Optional[str] = None, instance: Optional[str] = None) -> dict:
    """Load a symbol file (format decomp-symbols/1). Afterwards names work as addresses (also MODULE!name)."""
    return _box(instance).symbols_load(path, base_linear)


@mcp.tool()
def symbol_lookup(name: Optional[str] = None, address: Optional[str] = None, instance: Optional[str] = None) -> dict:
    """Name -> address, or address -> symbol (name+offset)."""
    return _box(instance).symbol_lookup(name, address)


@mcp.tool()
def snapshot(label: str, with_extension: bool = False, until: Optional[str] = None,
             instance: Optional[str] = None) -> dict:
    """Snapshot into a directory of its own: memory (1 MB + module ranges), registers, descriptors, paging,
    frame, meta.json. with_extension: additionally all memory above 1 MB.
    until="pmode": halt in protected mode first (for DOS extenders, otherwise linear ranges are missing)."""
    return _box(instance).snapshot(label, with_extension, until=until)


def main():
    """Entry point of the console command dosbox-lab-mcp (stdio)."""
    mcp.run()


if __name__ == "__main__":
    main()
