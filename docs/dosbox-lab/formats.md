# File formats

Recordings are written into the instance's output folder. Format names carry a version (`/1`). Files written by
earlier versions used the prefix `dosbox-mcp-` instead of `dosbox-lab-`; readers accept both.

| Format / file | Purpose |
|---|---|
| `dosbox-lab-coverage/1` (`<name>.bin` DMCV + `<name>.json`, optional `<name>_hits.jsonl`) | Code coverage: one bit per instruction start in 64 KB blocks, plus metadata and basic block hit counts. Reader: `dosbox_lab.files.read_coverage`, `covered`. |
| `dosbox-lab-calls/1` (`<name>.jsonl` + `<name>.json`) | Call log: one line per call with registers, flags, stack on entry and registers on return; the sidecar holds the metadata. |
| `dosbox-lab-state/1` (`<name>.sav` + `<name>.json`) | Full emulator state and its sidecar (memory pages, machine type, labels). |
| `dosbox-lab-func/1` | Result of `func_run`: registers, memory writes and side effects of one re-run function call. |
| `dosbox-lab-dump/1` (`<name>.bin` + `.json`) | Memory dump with checksum and missing pages. |
| `dosbox-lab-snapshot/1` (`meta.json` in a snapshot folder) | Snapshot of memory, registers, descriptors, paging and screen. |
| `dosbox-lab-szenario/1` | Scenario input: a JSON description of a scripted run (variables, start settings, steps). |
| `dosbox-lab-szenario-ergebnis/1` | Scenario result: per-step outcome and the recordings produced. |
| Event stream (`<name>.jsonl`) | Events: exec, exit, INT, mode switches, new code selectors, file operations. |
| Flow records (`<name>.jsonl`) | Indirect jump/call pairs and the most recent control transfers. |
| Watch log (`<name>.jsonl`) | Memory accesses seen by watchpoints. |
| Symbol file (format `decomp-symbole/1`) | Input for `symbols_load`: module name, address space and a list of named addresses. |

JSON Lines files are read with `dosbox_lab.files.read_jsonl`.
