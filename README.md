# dosbox-lab

dosbox-lab is a fork of [DOSBox-X](https://github.com/joncampbell123/dosbox-x) for automated analysis of DOS programs. It adds a control channel to the emulator, so that AI agents (through MCP) and Python scripts can run a program, stop it, inspect it and record what it does.

The extension talks to a Python package (`dosbox_lab`) that offers an MCP server with about 70 tools, a Python API and a command line. (`dmcp` in the C++ sources stands for "DOSBox machine control protocol".)

Typical uses: reverse engineering and decompiling DOS games and programs, recording what code actually runs, re-running single functions to compare an original with a rebuilt version, and driving old DOS compilers from modern scripts.

## Capabilities

- Breakpoints (code, conditional, one-shot, INT breakpoints) and memory watchpoints, with an access log
- Memory and register access in real mode and protected mode, GDT/LDT/IDT, paging, address translation, disassembly with symbols
- Code coverage (bitmap per instruction start, optional hit counters per basic block)
- Control flow recording (indirect jumps and calls, ring buffer of recent transfers)
- Call log: registers, flags and stack on entry and registers on return for chosen functions
- File access log and event stream (exec, exit, INT, mode switches, file operations)
- Run a single function again from a save state, with changed registers, stack or memory
- Module bases for LE executables (DOS extenders): load a symbol file and use names as addresses
- Scenarios: scripted, repeatable runs described in JSON, with recordings
- Headless batch runs, for example to drive old DOS compilers, with captured console output
- Several parallel instances, each with its own run folder
- Screenshots, screen text, keyboard and mouse input injected into the emulator (the host mouse and keyboard are never touched)

## Requirements

- Windows x64. The extension is built with Visual Studio only so far; the other DOSBox-X build paths (Linux, macOS, MinGW) have not been tried with it.
- Microsoft Visual C++ (MSBuild), from Visual Studio 2019 or 2022 – the free Community edition or the Build Tools are enough, with the "Desktop development with C++" workload. The project uses the VS 2019 toolset (v142) by default, as DOSBox-X does. With VS 2022 either add the "MSVC v142" component in the installer or build with `-Toolset v143` (not tested yet).
- Python 3.10 or newer
- Optional: [NASM](https://www.nasm.us/) (the Netwide Assembler, a free x86 assembler), only to rebuild the small DOS test programs in `testprogs/`. The built programs are already included.

## Build

```powershell
.\build.ps1          # Release SDL2 | x64 -> bin\x64\Release SDL2\dosbox-lab.exe
.\build.ps1 -Clean   # full rebuild
.\build.ps1 -Toolset v143   # use the VS 2022 toolset instead of v142
```

`build.ps1` finds MSBuild through the `MSBUILD` environment variable, `vswhere` or `PATH`, and enables the extension with `/DC_DOSBOX_AGENT=1 /DC_DMCP=1`. The test programs in `testprogs/` are already checked in; to rebuild them run `testprogs\build.ps1` (NASM from the `NASM` environment variable or from `PATH`).

## Install the Python package

```powershell
pip install -e ./python
```

This provides the commands `dosbox-lab` (command line) and `dosbox-lab-mcp` (MCP server over stdio), and `python -m dosbox_lab`.

The emulator is looked up in `bin\x64\Release SDL2\dosbox-lab.exe` inside this checkout, which is why the editable install (`-e`) is recommended. To use another file, set `DOSBOX_LAB_EXE`. Run folders (logs, screenshots, recordings) go to `.runs/` in the checkout; override with `DOSBOX_LAB_RUNS`.

## Use with an MCP client

```powershell
claude mcp add dosbox-lab -- dosbox-lab-mcp
```

A configuration example for other clients is in [python/mcp.example.json](python/mcp.example.json). All tools are listed in [docs/dosbox-lab/tools.md](docs/dosbox-lab/tools.md), the recording formats in [docs/dosbox-lab/formats.md](docs/dosbox-lab/formats.md).

## Command line

```powershell
dosbox-lab start --dir C:\games\demo --cmd GAME.EXE --headless
dosbox-lab pause
dosbox-lab regs
dosbox-lab read 0043:000406F1 16
dosbox-lab shot
dosbox-lab stop

# run a DOS command headless and return its output and exit code, e.g. an old compiler
dosbox-lab batch --dir C:\work --d C:\compilers\tc --cmd "TCC HELLO.C"
```

`dosbox-lab --help` and `dosbox-lab <command> --help` list all commands and options.

## Python example

```python
from dosbox_lab.api import Dosbox
from dosbox_lab.instance import Mount

box = Dosbox.start(mounts=[Mount("C", r"C:\games\demo")], autoexec=["GAME.EXE"], headless=True)
try:
    box.break_add(target="0043:000406F1")      # breakpoint, any address form
    box.pause()                                # or box.run_until(...)
    print(box.registers())
    print(box.mem_read("l:010406F1", 16).hex())
    box.screenshot("title")
finally:
    box.stop()
```

## Tests

```powershell
cd python
python -m unittest discover -s tests -t .
```

The tests start real emulator instances (build first). One test checks that the host mouse cursor is never moved, so do not move the mouse while the tests run.

## Status

Version 0.1.0. Windows only. The interface was renamed from an earlier internal version; old names are listed in [docs/dosbox-lab/MIGRATION.md](docs/dosbox-lab/MIGRATION.md), and old scenario and symbol files are still read.

## Relationship to DOSBox-X

dosbox-lab is a fork of DOSBox-X and keeps its complete upstream history. Our own changes live in `src/agent/ext/`, `include/agent/dmcp.h`, `python/` and `testprogs/`, plus small hooks in a few DOSBox-X source files, each marked with `// DOSBOX-LAB hook`. Upstream changes are taken over by merging from [joncampbell123/dosbox-x](https://github.com/joncampbell123/dosbox-x). The original DOSBox-X readme is kept as [README.dosbox-x.md](README.dosbox-x.md).

## License

GPL-2.0, like DOSBox-X (see `COPYING`). Our own files carry `SPDX-License-Identifier: GPL-2.0-or-later`, matching the DOSBox-X file headers ("version 2 or, at your option, any later version").
