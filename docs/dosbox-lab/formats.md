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
| `dosbox-lab-scenario/1` | Scenario input: a JSON description of a scripted run (variables, start settings, steps). |
| `dosbox-lab-scenario-result/1` | Scenario result: per-step outcome and the recordings produced. |
| Event stream (`<name>.jsonl`) | Events: exec, exit, INT, mode switches, new code selectors, file operations. |
| Flow records (`<name>.jsonl`) | Indirect jump/call pairs and the most recent control transfers. |
| Watch log (`<name>.jsonl`) | Memory accesses seen by watchpoints. |
| Symbol file (format `decomp-symbols/1`) | Input for `symbols_load`: `module`, `address_space`, `symbols` (`name`, `addr`, `size`, `kind`), optional `segments` and `base_linear`. |

JSON Lines files are read with `dosbox_lab.files.read_jsonl`.

## Legacy names

All names are English. Older files with German names are still read: scenarios in the formats
`dosbox-lab-szenario/1` and `dosbox-mcp-szenario/1` (German keys and step kinds) and symbol files in the format
`decomp-symbole/1` (German keys such as `modul`, `symbole`, `adr`) are translated on load; see
`python/dosbox_lab/legacy.py`. Files that dosbox-lab writes always use the English names.
