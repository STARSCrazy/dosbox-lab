# Security Policy

## Scope

dosbox-lab adds a control channel to DOSBox-X (a local RPC transport, a named pipe on Windows, and a Python package with an MCP server over stdio). This channel can read and write emulator memory, start programs and access host folders that are mounted into the emulator. Run it only on your own machine and connect only clients you trust.

## Supported versions

Only the latest state of the `main` branch is supported.

## Reporting a vulnerability

- **In dosbox-lab's own parts** (`src/agent/ext/`, `include/agent/dmcp.h`, the hooks marked `// DOSBOX-LAB hook`, `python/`): please use [GitHub's private vulnerability reporting](https://github.com/STARSCrazy/dosbox-lab/security/advisories/new) for this repository. Do not open a public issue for security problems.
- **In the emulator itself** (code inherited from DOSBox-X): please report it to the DOSBox-X project as described in their [security policy](https://github.com/joncampbell123/dosbox-x/blob/master/SECURITY.md).
