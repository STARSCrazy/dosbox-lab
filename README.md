# dosbox-lab

dosbox-lab is a fork of [DOSBox-X](https://github.com/joncampbell123/dosbox-x) for automated analysis of DOS
programs. It adds a control channel to the emulator, so that AI agents (through MCP) and Python scripts can run a
program, stop it, inspect it and record what it does.

The extension talks to a Python package (`dosbox_lab`) that offers an MCP server with about 70 tools, a Python API
and a command line. (`dmcp` in the C++ sources stands for "DOSBox machine control protocol".)

## Capabilities

- Breakpoints (code, conditional, one-shot, INT breakpoints) and memory watchpoints, with an access log
- Memory and register access in real mode and protected mode, GDT/LDT/IDT, paging, address translation,
  disassembly with symbols
- Code coverage (bitmap per instruction start, optional hit counters per basic block)
- Control flow recording (indirect jumps and calls, ring buffer of recent transfers)
- Call log: registers, flags and stack on entry and registers on return for chosen functions
- File access log and event stream (exec, exit, INT, mode switches, file operations)
- Run a single function again from a save state, with changed registers, stack or memory
- Module bases for LE executables (DOS extenders): load a symbol file and use names as addresses
- Scenarios: scripted, repeatable runs described in JSON, with recordings
- Headless batch runs, for example to drive old DOS compilers, with captured console output
- Several parallel instances, each with its own run folder
- Screenshots, screen text, keyboard and mouse input injected by the agent

## Build

Windows only for now (Visual Studio 2019 Build Tools, x64):

```powershell
.\build.ps1          # Release SDL2 | x64 -> bin\x64\Release SDL2\dosbox-lab.exe
.\build.ps1 -Clean   # full rebuild
```

`build.ps1` finds MSBuild through `MSBUILD`, `vswhere` or `PATH`, and enables the agent code with
`/DC_DOSBOX_AGENT=1 /DC_DMCP=1`. Test programs (`testprogs/`) are assembled with NASM (`NASM` environment variable
or `nasm` on `PATH`): `testprogs\build.ps1`.

## Install the Python package

```
pip install ./python
```

This provides the commands `dosbox-lab` (command line) and `dosbox-lab-mcp` (MCP server over stdio), and
`python -m dosbox_lab`. By default the emulator is looked up in `bin\x64\Release SDL2\dosbox-lab.exe` inside the
checkout; set `DOSBOX_LAB_EXE` to use another file. Run folders go to `.runs/` (override: `DOSBOX_LAB_RUNS`).

## Use with an MCP client

```
claude mcp add dosbox-lab -- dosbox-lab-mcp
```

A configuration example for other clients is in `python/mcp.example.json`. The tools are listed in
[docs/dosbox-lab/tools.md](docs/dosbox-lab/tools.md), the recording formats in
[docs/dosbox-lab/formats.md](docs/dosbox-lab/formats.md).

## Python example

```python
from dosbox_lab.api import Dosbox
from dosbox_lab.instance import Mount

box = Dosbox.start(mounts=[Mount("C", r"C:\games\demo")], autoexec=["GAME.EXE"], headless=True)
try:
    box.break_add(ziel="0043:000406F1")        # breakpoint, any address form
    box.pause()                                # or box.run_until(...)
    print(box.registers())
    print(box.mem_read("l:010406F1", 16).hex())
    box.screenshot("title")
finally:
    box.stop()
```

## Status

Windows only. A Linux build of the extension has not been tried. The tool and parameter names and the doc strings
are partly German (the project grew out of a German-language workflow); they are stable but may be translated.

## Relationship to DOSBox-X

dosbox-lab is a fork of DOSBox-X and keeps its complete upstream history. Our own changes live in
`src/agent/ext/`, `include/agent/dmcp.h`, `python/` and `testprogs/`, plus small hooks in a few DOSBox-X source
files, each marked with `// DOSBOX-LAB hook`. Upstream changes are taken over by merging from
[joncampbell123/dosbox-x](https://github.com/joncampbell123/dosbox-x). The original DOSBox-X readme is kept as
[README.dosbox-x.md](README.dosbox-x.md).

## License

GPL-2.0, like DOSBox-X (see `COPYING`). Our own files carry `SPDX-License-Identifier: GPL-2.0-or-later`, matching
the DOSBox-X file headers ("version 2 or, at your option, any later version").
