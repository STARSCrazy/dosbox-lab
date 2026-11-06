# MCP tools

The MCP server (`dosbox-lab-mcp`) exposes these tools. Almost all take an optional `instance` argument to pick one
of several running instances. Addresses can be given in several forms: `SSSS:OOOOOOOO` (selector), `r:SSSS:OOOO`
(real mode), `l:<hex>` (linear), `p:<hex>` (physical), `va:MODULE:<hex>` / `rel:...` (module relative) or a symbol
name once a symbol file is loaded.

## Instances
- `dosbox_start` - start an emulator instance (C: mount, autoexec lines, optional CD drive, headless).
- `dosbox_stop` - stop an instance.
- `dosbox_list` - list known instances.
- `run_batch` - run one DOS command in a throwaway headless instance and return its captured output and status.
  The Python function also takes `watch` (called with the stdout so far while the command runs; True stops the
  instance, status `stopped`) and `snapshot=False` (no listing of the mounted files before and after).

## Execution control
- `emu_status` - paused or running, why stopped, CS:EIP, mode.
- `emu_pause` - pause (optionally until the first protected-mode instruction).
- `emu_continue` - resume.
- `emu_step` - execute N instructions while paused.
- `emu_step_over` - single step that runs through CALL/INT/REP.
- `emu_speed` - normal, turbo or fixed cycles.
- `emu_run_until` - run until all given conditions hold (address, mode, CS, CPL, expression).
- `emu_frame_step` - run N frames, then pause.
- `emu_eval` - evaluate an expression over registers, flags and memory.

## Breakpoints and watchpoints
- `break_add` / `break_remove` / `break_list` - code breakpoints, with condition, one-shot, hit counts.
- `break_int_add` / `break_int_remove` - breakpoints before INT instructions (number, AH, AL).
- `watch_add` / `watch_remove` / `watch_list` - memory watchpoints (read, write or both; stop or log).
- `watch_log` - write the watch access log to a file.
- `watch_clear_log` - clear the access log or resize it.

## CPU and memory
- `cpu_registers` - all registers, segment bases, control registers, flags, mode, CPL, paging.
- `cpu_set_register` - set a general register, ESP, EIP or EFLAGS.
- `cpu_descriptors` - GDT, LDT or IDT entries.
- `cpu_translate` - convert an address between all address forms.
- `cpu_paging` - page table entries for a linear range.
- `cpu_disassemble` - disassembly with symbols and branch targets.
- `mem_read` - read up to 4096 bytes as hex.
- `mem_write` - write bytes.
- `mem_dump` - dump a range to a file with checksum and missing pages.
- `mem_search` - search text or bytes in a range.

## Recordings
- `coverage_start` / `coverage_stop` / `coverage_save` / `coverage_status` - code coverage (optionally with block hit counts).
- `flow_start` / `flow_stop` / `flow_save` / `flow_recent` / `flow_status` - indirect jumps and calls, recent control transfers.
- `calls_start` / `calls_stop` / `calls_status` - call log for chosen functions: arguments on entry, registers on return, optional save states per call.
- `events_start` / `events_stop` / `events_status` / `events_read` - event stream (exec, exit, INT, mode switch, file access).
- `snapshot` - memory, registers, descriptors, paging and screen into one folder.
- `state_save` / `state_load` - full emulator state to and from a save file.

## Functions and symbols
- `func_run` - run a recorded call again from its save state, with changed registers, stack or memory.
- `modules_list` - known modules with load segment and address mapping.
- `modules_set_segments` - set the module address mapping (for example LE objects of DOS extenders).
- `symbols_load` - load a symbol file (format `decomp-symbols/1`); names then work as addresses.
- `symbol_lookup` - name to address and address to symbol.

## DOS state
- `dos_programs` - PSP chain with names and command lines.
- `dos_mcb` - DOS memory block chain.
- `dos_files` - open files and handles of the current program.
- `dos_target` - target program: PSP, load segment, entry point, stack, relocation.

## Input and screen
- `key` - press a key or combination.
- `type_text` - type text.
- `mouse_move` / `mouse_click` / `mouse_button` / `mouse_wheel` - injected mouse input.
- `mouse_move_program` - move the mouse until the program's own position variables match.
- `mouse_state` - INT 33h position, buttons and range.
- `input_own` - ignore or allow the real mouse and keyboard in the emulator window.
- `screenshot` - PNG screenshot, optionally a region.
- `screen_text` - text mode screen as lines.
- `video_state` - video mode and resolution.
