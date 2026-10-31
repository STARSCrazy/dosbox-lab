# Contributing to dosbox-lab

Thanks for your interest. dosbox-lab is a fork of [DOSBox-X](https://github.com/joncampbell123/dosbox-x) that adds automation for analysing DOS programs.

## Where does my change belong?

- **Automation, recording, MCP tools, Python API and CLI** (`src/agent/ext/`, `include/agent/dmcp.h`, `python/`, `testprogs/`): here. Please open an [issue](https://github.com/STARSCrazy/dosbox-lab/issues) or a pull request.
- **Emulation itself** (CPU, DOS, hardware, video, sound, general DOSBox-X behaviour): please contribute to [DOSBox-X](https://github.com/joncampbell123/dosbox-x) directly. dosbox-lab merges upstream changes from there.

## Guidelines

- Keep changes to DOSBox-X files as small as possible and mark each hook with `// DOSBOX-LAB hook`, so upstream merges stay easy.
- Add or update a test in `python/tests/` for new behaviour. Tests start real emulator instances; see the README for how to build and run them.
- Code, comments, tool names and documentation are in English.
- New source files carry `SPDX-License-Identifier: GPL-2.0-or-later` in their first comment line. By contributing you agree that your work is licensed under the GPL, version 2 or later.

## Code of conduct

Please follow the [code of conduct](CODE_OF_CONDUCT.md).
