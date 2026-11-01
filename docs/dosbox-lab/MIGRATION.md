# Migration guide: German names to English names

All names in dosbox-lab are English now. Earlier versions used German identifiers, German JSON keys and German
enum values. This guide lists every change from old to new, so that programs that talk to dosbox-lab
(for example Decomp) can be migrated mechanically.

What did **not** change:

- JSON-RPC method names (`emu.break_remove`, `calls.start`, ...), they were already English.
- MCP tool names (`emu_status`, `break_add`, ...) and Python class names (`Dosbox`, `Instance`, `Mount`, `DmcpError`).
- Python module names, except for the new module `dosbox_lab.legacy`.
- Environment variables: `DOSBOX_LAB_EXE` (with `DOSBOX_MCP_EXE` as old fallback), `DOSBOX_LAB_RUNS`, `DMCP_OUTDIR`.
- File extensions and binary formats (`.dmcv`, `.sav`, call logs as JSONL, PNG).

What changed: parameter names, JSON keys (requests, results and every file written by the emulator or by the
Python package), enum values, CLI commands and options, default file names, format names, test program names.

## 1. The five most important points

1. **Every JSON key and parameter is renamed** with one consistent word mapping (section 7). Rename in both
   directions: parameters you send (`adresse` -> `address`) and fields you read (`laenge` -> `length`,
   `datei` -> `file`, `pfad` -> `path`, `fehler` -> `error`).
2. **Enum values changed**: watch kind `schreiben/lesen/beides` -> `write/read/both`, watch action
   `anhalten/protokollieren` -> `halt/log`, mouse button `links/rechts/mitte` -> `left/right/middle`,
   event type `datei` -> `file`, `int_mode` value `alle` -> `all`.
3. **Ambiguous words**: German `bis` is `to` in ranges (`calls_start(ranges=[{"from","to"}])`) but `until`
   in `pause`/`snapshot`/`run_until`; `von` -> `from`; `nach` -> `to`; `art` -> `kind`;
   `module` (list) -> `modules`, `modul` (single) -> `module`.
4. **Format names**: `decomp-symbole/1` -> `decomp-symbols/1`, `dosbox-lab-szenario/1` ->
   `dosbox-lab-scenario/1`, result `dosbox-lab-szenario-ergebnis/1` -> `dosbox-lab-scenario-result/1`.
   Readers still accept the old symbol and scenario formats (section 2). Recordings written by older
   versions are **not** converted; read them with the old key names or re-record.
5. **Renamed files and CLI options**: `szene.asm/SZENE.COM` -> `scene.asm/SCENE.COM`, `test_36_szenario.py` ->
   `test_36_scenario.py`, CLI options such as `--max-je-ziel` -> `--max-per-target`, `--ausgabe` ->
   `--output-dir`. CLI errors are `{"error", "message"}` instead of `{"fehler", ...}`.

## 2. Backward compatibility (what still reads old files)

`python/dosbox_lab/legacy.py` is the only place that keeps German names on purpose.

| Reader | Accepts old input | Behaviour |
|---|---|---|
| `Dosbox.symbols_load(path)` | format `decomp-symbole/1`; German file keys (`modul`, `adressraum`, `symbole`, `segmente`, `basis_linear`), segment keys (`va_ende`), symbol keys (`adr`, `groesse`, `art`) | translated to the current keys; error messages are English |
| `load_scenario`, `play_scenario`, CLI `scenario` | formats `dosbox-lab-szenario/1`, `dosbox-mcp-szenario/1`; German top-level keys (`variablen`, `schritte`), mount keys (`laufwerk`, `pfad`), step kinds, step values (`knopf`, `adresse`, `danach`, ...), recording options, range keys (`von`, `bis`) | translated by `legacy.normalize_scenario` before validation |
| `DOSBOX_MCP_EXE` | old environment variable | used when `DOSBOX_LAB_EXE` is not set |

Writers always produce the new names. The wire protocol (RPC keys) has **no** compatibility layer: the
emulator and the Python package must come from the same version.

## 3. Python package (`dosbox_lab`)

Package and module names are unchanged. New: `dosbox_lab.legacy` (`normalize_symbol_file`,
`normalize_scenario`, `SYMBOL_FORMAT`, `LEGACY_SYMBOL_FORMATS`, `LEGACY_SCENARIO_FORMATS`).

Function and method names did not change; only parameters did. The tables list those that changed
(old -> new). Methods of `Dosbox` that are not listed kept all their parameters.

### 3.1 `Dosbox` methods and module functions

| Name | Name (new) | Parameter renames |
|---|---|---|
| `run_until` | `run_until` | `adresse` -> `address`; `ausdruck` -> `expression` |
| `eval` | `eval` | `ausdruck` -> `expression` |
| `frame_step` | `frame_step` | `anzahl` -> `count` |
| `break_add` | `break_add` | `ziel` -> `target`; `bedingung` -> `condition`; `einmalig` -> `once`; `adresse` -> `address` |
| `break_remove` | `break_remove` | `ziel` -> `target` |
| `break_int_add` | `break_int_add` | `nummer` -> `number` |
| `break_int_remove` | `break_int_remove` | `nummer` -> `number` |
| `watch_add` | `watch_add` | `adresse` -> `address`; `laenge` -> `length`; `art` -> `kind`; `aktion` -> `action` |
| `watch_log` | `watch_log` | `leeren` -> `clear` |
| `watch_clear_log` | `watch_clear_log` | `max_eintraege` -> `max_entries` |
| `disassemble` | `disassemble` | `adresse` -> `address`; `anzahl` -> `count` |
| `set_segments` | `set_segments` | `modul` -> `module`; `segmente` -> `segments` |
| `clear_modules` | `clear_modules` | `modul` -> `module` |
| `mouse_move_program` | `mouse_move_program` | `x_adresse` -> `x_address`; `y_adresse` -> `y_address`; `breite` -> `width`; `versuche` -> `attempts`; `toleranz` -> `tolerance`; `warte_s` -> `wait_s` |
| `events_start` | `events_start` | `typen` -> `types`; `int_modus` -> `int_mode`; `mit_rueckgabe` -> `with_return`; `datei_vorschau` -> `file_preview` |
| `events_read` | `events_read` | `seit_nr` -> `since_nr` |
| `wait_event` | `wait_event` | `seit_nr` -> `since_nr` |
| `coverage_start` | `coverage_start` | `max_bloecke` -> `max_blocks`; `fortsetzen` -> `resume` |
| `flow_start` | `flow_start` | `indirekt` -> `indirect`; `ring_groesse` -> `ring_size`; `mit_returns` -> `with_returns`; `max_paare` -> `max_pairs` |
| `flow_recent` | `flow_recent` | `anzahl` -> `count` |
| `calls_start` | `calls_start` | `ziele` -> `targets`; `bereiche` -> `ranges`; `module` -> `modules`; `max_je_ziel` -> `max_per_target`; `zustaende` -> `states`; `max_zustaende` -> `max_states` |
| `func_run` | `func_run` | `zustand` -> `state`; `schreiben` -> `write`; `max_befehle` -> `max_instructions` |
| `symbols_load` | `symbols_load` | `basis_linear` -> `base_linear` |
| `symbol_lookup` | `symbol_lookup` | `adresse` -> `address` |
| `symbols_clear` | `symbols_clear` | `modul` -> `module` |
| `snapshot` | `snapshot` | `mit_erweiterung` -> `with_extension`; `bis` -> `until` |
| `state_load` | `state_load` | `pfad` -> `path`; `ohne_pruefung` -> `skip_check` |
| `load_scenario` | `load_scenario` | `quelle` -> `source`; `variablen` -> `variables` |
| `play_scenario` | `play_scenario` | `quelle` -> `source`; `variablen` -> `variables`; `stop_am_ende` -> `stop_at_end`; `ausgabe` -> `output_dir` |
| `snapshot.take` | `snapshot.take` | `mit_erweiterung` -> `with_extension`; `bis` -> `until` |
| `batch.run_batch` | `batch.run_batch` | `befehl` -> `command`; `max_ausgabe` -> `max_output` |

`Dosbox.break_add` and `break_remove` also kept the older `linear=` parameter (an integer linear address) for
old callers; `address=` of `break_add` is the new name for `adresse=`.

Other changes in the Python API:

| Old | New |
|---|---|
| `play_scenario(...)` returns `(box, ergebnis)` | returns `(box, result)`; result dictionary keys: see section 6 |
| `Dosbox.start(...)` and `Instance.start(...)` | unchanged |
| error texts of `DmcpError` | English; code that matched German texts must be updated |
| `snapshot.take(...)` result keys `pfad`, `dateien`, `bereiche`, `hinweise` | `path`, `files`, `ranges`, `hints`; range entries `{"start", "end", "module", "name"}` (was `ende`, `modul`) |
| `meta.json` of a snapshot: `zeit_ms`, `pausiert_vorher`, `status`, `module`, `bereiche`, `hinweise` | `time_ms`, `paused_before`, `status`, `modules`, `ranges`, `hints` |

## 4. MCP server (tools)

Tool names are unchanged. **Every tool that took `instanz` now takes `instance`** (this applies to all tools
below except `dosbox_start` and `run_batch`). Parameter renames per tool:

| Tool | Tool (new) | Parameter renames |
|---|---|---|
| `dosbox_start` | `dosbox_start` | `laufwerk_c` -> `drive_c`; `befehle` -> `commands` |
| `dosbox_stop` | `dosbox_stop` | `instanz` -> `instance` |
| `run_batch` | `run_batch` | `laufwerk_c` -> `drive_c`; `befehl` -> `command`; `laufwerk_d` -> `drive_d`; `max_ausgabe` -> `max_output` |
| `emu_status` | `emu_status` | `instanz` -> `instance` |
| `emu_pause` | `emu_pause` | `bis` -> `until`; `instanz` -> `instance` |
| `emu_continue` | `emu_continue` | `instanz` -> `instance` |
| `emu_step` | `emu_step` | `anzahl` -> `count`; `instanz` -> `instance` |
| `emu_speed` | `emu_speed` | `modus` -> `mode`; `instanz` -> `instance` |
| `break_add` | `break_add` | `ziel` -> `target`; `bedingung` -> `condition`; `einmalig` -> `once`; `instanz` -> `instance` |
| `break_remove` | `break_remove` | `ziel` -> `target`; `alle` -> `all`; `instanz` -> `instance` |
| `break_list` | `break_list` | `instanz` -> `instance` |
| `break_int_add` | `break_int_add` | `nummer` -> `number`; `instanz` -> `instance` |
| `break_int_remove` | `break_int_remove` | `nummer` -> `number`; `alle` -> `all`; `instanz` -> `instance` |
| `emu_run_until` | `emu_run_until` | `adresse` -> `address`; `ausdruck` -> `expression`; `instanz` -> `instance` |
| `emu_step_over` | `emu_step_over` | `instanz` -> `instance` |
| `emu_frame_step` | `emu_frame_step` | `anzahl` -> `count`; `instanz` -> `instance` |
| `emu_eval` | `emu_eval` | `ausdruck` -> `expression`; `instanz` -> `instance` |
| `cpu_disassemble` | `cpu_disassemble` | `adresse` -> `address`; `anzahl` -> `count`; `instanz` -> `instance` |
| `watch_add` | `watch_add` | `adresse` -> `address`; `laenge` -> `length`; `art` -> `kind`; `aktion` -> `action`; `instanz` -> `instance` |
| `watch_remove` | `watch_remove` | `alle` -> `all`; `instanz` -> `instance` |
| `watch_list` | `watch_list` | `instanz` -> `instance` |
| `watch_log` | `watch_log` | `leeren` -> `clear`; `instanz` -> `instance` |
| `watch_clear_log` | `watch_clear_log` | `max_eintraege` -> `max_entries`; `instanz` -> `instance` |
| `state_save` | `state_save` | `instanz` -> `instance` |
| `state_load` | `state_load` | `pfad` -> `path`; `ohne_pruefung` -> `skip_check`; `instanz` -> `instance` |
| `dos_programs` | `dos_programs` | `instanz` -> `instance` |
| `dos_mcb` | `dos_mcb` | `instanz` -> `instance` |
| `dos_files` | `dos_files` | `instanz` -> `instance` |
| `dos_target` | `dos_target` | `instanz` -> `instance` |
| `mouse_move_program` | `mouse_move_program` | `x_adresse` -> `x_address`; `y_adresse` -> `y_address`; `breite` -> `width`; `toleranz` -> `tolerance`; `instanz` -> `instance` |
| `cpu_registers` | `cpu_registers` | `instanz` -> `instance` |
| `cpu_set_register` | `cpu_set_register` | `wert` -> `value`; `instanz` -> `instance` |
| `cpu_descriptors` | `cpu_descriptors` | `tabelle` -> `table`; `selektor` -> `selector`; `instanz` -> `instance` |
| `cpu_translate` | `cpu_translate` | `adresse` -> `address`; `instanz` -> `instance` |
| `cpu_paging` | `cpu_paging` | `adresse` -> `address`; `laenge` -> `length`; `instanz` -> `instance` |
| `mem_read` | `mem_read` | `adresse` -> `address`; `laenge` -> `length`; `instanz` -> `instance` |
| `mem_write` | `mem_write` | `adresse` -> `address`; `instanz` -> `instance` |
| `mem_dump` | `mem_dump` | `adresse` -> `address`; `laenge` -> `length`; `instanz` -> `instance` |
| `mem_search` | `mem_search` | `adresse` -> `address`; `laenge` -> `length`; `max_treffer` -> `max_hits`; `instanz` -> `instance` |
| `key` | `key` | `taste` -> `key`; `dauer_ms` -> `duration_ms`; `instanz` -> `instance` |
| `type_text` | `type_text` | `instanz` -> `instance` |
| `mouse_move` | `mouse_move` | `koordinaten` -> `coords`; `instanz` -> `instance` |
| `mouse_click` | `mouse_click` | `knopf` -> `button`; `instanz` -> `instance` |
| `mouse_button` | `mouse_button` | `knopf` -> `button`; `gedrueckt` -> `pressed`; `instanz` -> `instance` |
| `mouse_wheel` | `mouse_wheel` | `schritte` -> `steps`; `instanz` -> `instance` |
| `mouse_state` | `mouse_state` | `instanz` -> `instance` |
| `input_own` | `input_own` | `an` -> `on`; `instanz` -> `instance` |
| `screenshot` | `screenshot` | `anzeigen` -> `show`; `breite` -> `width`; `hoehe` -> `height`; `instanz` -> `instance` |
| `screen_text` | `screen_text` | `instanz` -> `instance` |
| `video_state` | `video_state` | `instanz` -> `instance` |
| `events_start` | `events_start` | `typen` -> `types`; `int_modus` -> `int_mode`; `mit_rueckgabe` -> `with_return`; `datei_vorschau` -> `file_preview`; `instanz` -> `instance` |
| `events_stop` | `events_stop` | `instanz` -> `instance` |
| `events_status` | `events_status` | `instanz` -> `instance` |
| `events_read` | `events_read` | `seit_nr` -> `since_nr`; `instanz` -> `instance` |
| `coverage_start` | `coverage_start` | `max_bloecke` -> `max_blocks`; `fortsetzen` -> `resume`; `instanz` -> `instance` |
| `coverage_stop` | `coverage_stop` | `instanz` -> `instance` |
| `coverage_save` | `coverage_save` | `instanz` -> `instance` |
| `coverage_status` | `coverage_status` | `instanz` -> `instance` |
| `flow_start` | `flow_start` | `indirekt` -> `indirect`; `ring_groesse` -> `ring_size`; `mit_returns` -> `with_returns`; `instanz` -> `instance` |
| `flow_stop` | `flow_stop` | `instanz` -> `instance` |
| `flow_save` | `flow_save` | `instanz` -> `instance` |
| `flow_recent` | `flow_recent` | `anzahl` -> `count`; `instanz` -> `instance` |
| `flow_status` | `flow_status` | `instanz` -> `instance` |
| `calls_start` | `calls_start` | `ziele` -> `targets`; `bereiche` -> `ranges`; `module` -> `modules`; `max_je_ziel` -> `max_per_target`; `zustaende` -> `states`; `max_zustaende` -> `max_states`; `instanz` -> `instance` |
| `func_run` | `func_run` | `zustand` -> `state`; `schreiben` -> `write`; `max_befehle` -> `max_instructions`; `instanz` -> `instance` |
| `calls_stop` | `calls_stop` | `instanz` -> `instance` |
| `calls_status` | `calls_status` | `instanz` -> `instance` |
| `modules_list` | `modules_list` | `instanz` -> `instance` |
| `modules_set_segments` | `modules_set_segments` | `modul` -> `module`; `segmente` -> `segments`; `instanz` -> `instance` |
| `symbols_load` | `symbols_load` | `pfad` -> `path`; `basis_linear` -> `base_linear`; `instanz` -> `instance` |
| `symbol_lookup` | `symbol_lookup` | `adresse` -> `address`; `instanz` -> `instance` |
| `snapshot` | `snapshot` | `mit_erweiterung` -> `with_extension`; `bis` -> `until`; `instanz` -> `instance` |

Further MCP changes: `modules_list` returns `{"modules": [...]}` (was `{"module": [...]}`); `input_own`
takes `on` (was `an`); error responses contain
English messages.

## 5. CLI (`python -m dosbox_lab`)

Command names are unchanged. Renamed options and arguments (old -> new):

| Command | Old | New |
|---|---|---|
| (global) | `--id` | `--id` (help text only) |
| `batch` | `--max-ausgabe` | `--max-output` |
| `scenario` | `--datei`, `--fenster`, `--offen`, `--ausgabe` | `--file`, `--window`, `--open`, `--output-dir` |
| `pause`, `snapshot` | `--bis pmode` | `--until pmode` |
| `events-start` | `--typ`, `--rueckgabe`, `--vorschau` | `--type`, `--with-return`, `--preview` |
| `events-read` | `--seit` | `--since` |
| `calls-start` | `--ziel`, `--bereich VON BIS`, `--modul`, `--max-je-ziel`, `--zustaende`, `--max-zustaende` | `--target`, `--range FROM TO`, `--module`, `--max-per-target`, `--states`, `--max-states` |
| `func-run` | `--zustand`, `--schreiben`, `--max-befehle` | `--state`, `--write`, `--max-instructions` |
| `cov-start` | `--max-bloecke` | `--max-blocks` |
| `flow-save` | default name `indirekt` | `indirect` |
| `flow-recent` | argument `anzahl` | `count` |
| `segments` | arguments `modul`, `segmente` | `module`, `segments` |
| `symbols` | argument `pfad`, `--basis` | `path`, `--base` |
| `translate`, `read`, `write`, `dump`, `search`, `dis`, `watch` | argument `adresse`, `laenge` | `address`, `length` |
| `dis` | `--anzahl` | `--count` |
| `frame` | argument `anzahl` | `count` |
| `key` | argument `taste` | `key` |
| `click` | `--button links` (default) | `--button left` |
| `shot` | `--rect x,y,breite,hoehe` | `--rect x,y,width,height` |
| `until` | `--adresse`, `--ausdruck` | `--address`, `--expression` |
| `eval` | argument `ausdruck` | `expression` |
| `speed` | argument `modus` | `mode` |
| `break` | arguments `ziel`, `--bedingung`, `--einmalig` | `target`, `--condition`, `--once` |
| `unbreak`, `unwatch` | `--alle` | `--all` |
| `break-int` | argument `nummer` | `number` |
| `watch` | `--laenge`, `--art schreiben/lesen/beides`, `--aktion anhalten/protokollieren` | `--length`, `--kind write/read/both`, `--action halt/log` |
| `watch-log` | default name `zugriffe`, `--leeren` | `accesses`, `--clear` |
| `state-load` | `--pfad`, `--ohne-pruefung` | `--path`, `--skip-check` |
| `dos` | argument `art` | `kind` |
| `call` | argument `methode` | `method` |

CLI output: errors are `{"error": ..., "message": ...}` (was `fehler`); `start` prints `{"id", "rundir"}`
(unchanged).

## 6. Scenario scripts and results

Format `dosbox-lab-scenario/1`. Old files load unchanged (section 2); new files must use the new names.

| Old | New |
|---|---|
| `variablen`, `schritte` | `variables`, `steps` |
| mounts: `laufwerk`, `pfad` | `drive`, `path` |
| step note `notiz` | `note` |
| `tippe` | `type` |
| `taste` | `key` |
| `klick` | `click` |
| `maus_programm` | `mouse_program` |
| `warte_text`, `warte_speicher`, `warte_halt` | `wait_text`, `wait_memory`, `wait_halt` |
| `weiter` | `continue` |
| `warte_bilder`, `warte_bild`, `warte_s` | `wait_frames`, `wait_frame`, `wait_s` |
| `zustand_laden`, `speicherstand` | `state_load`, `state_save` |
| `aufzeichnung_start`, `aufzeichnung_stop` | `recording_start`, `recording_stop` |
| step values: `knopf`, `x_adresse`, `y_adresse`, `breite`, `toleranz`, `versuche`, `warte_s`, `zeile`, `adresse`, `maske`, `danach`, `art` | `button`, `x_address`, `y_address`, `width`, `tolerance`, `attempts`, `wait_s`, `line`, `address`, `mask`, `then`, `kind` |
| values `links`, `rechts`, `mitte` | `left`, `right`, `middle` |
| `danach: anhalten/weiter` | `then: halt/continue` |
| recording options `ziele`, `bereiche` (`von`/`bis`), `module`, `max_je_ziel`, `zustaende`, `max_zustaende`, `typen`, `int_modus`, `mit_rueckgabe`, `datei_vorschau`, `max_bloecke`, `fortsetzen`, `indirekt`, `ring_groesse`, `mit_returns`, `max_paare` | `targets`, `ranges` (`from`/`to`), `modules`, `max_per_target`, `states`, `max_states`, `types`, `int_mode`, `with_return`, `file_preview`, `max_blocks`, `resume`, `indirect`, `ring_size`, `with_returns`, `max_pairs` |
| int rule `nummer` | `number` |

Result file `<name>.szenario.json` -> `<name>.scenario.json`, format `dosbox-lab-szenario-ergebnis/1` ->
`dosbox-lab-scenario-result/1`. Error artifacts: `<name>.fehler.txt` -> `<name>.error.txt`, screenshot
`<name>.fehler` -> `<name>.error`.

| Old result key | New |
|---|---|
| `quelle`, `variablen`, `instanz` | `source`, `variables`, `instance` |
| `schritte_fertig`, `schritte_gesamt`, `schritte` | `steps_done`, `steps_total`, `steps` |
| `dauer_s`, `bilder` | `duration_s`, `frames` |
| `speicherstaende`, `aufzeichnungen` | `savestates`, `recordings` |
| `fehler`, `pfad` | `error`, `path` |
| step entry: `kind` (was `art`), `note` (was `notiz`) | |
| recording entry: `art`, `selbst_gestoppt`, `dateien`, `hinweis` | `kind`, `self_stopped`, `files`, `hint` |

## 7. JSON keys of requests, results and written files

The keys below changed everywhere they occur: in RPC requests and responses, in the Python result
dictionaries, in the files the emulator writes (call logs `*.jsonl` and `*.calls.json`, event logs, coverage and
flow reports, memory dumps, function-run results, savestate sidecars `*.json`, snapshot `meta.json`) and in
the CLI output. The right column names the areas (C++ source files `dmcp_<area>.cpp`) where the key occurs.
Where one old word has two new words, both rows are given and the area decides.

Other value changes: the batch helper `DMCPBAT.COM` gets the argument `END` instead of `ENDE`; disassembly
`end` value `nicht lesbar` -> `unreadable`; function-run result `commands` (executed instructions) ->
`instructions`; default file names `zugriffe` -> `accesses`, `indirekt` -> `indirect`.

| Old key / value | New | Areas |
|---|---|---|
| `ENDE` | `end` | batch |
| `abbildung` | `mapping` | symbols |
| `abgebrochen` | `aborted` | dos, func |
| `abgeleitet` | `derived` | modules |
| `abgeschnitten` | `truncated` | batch, cpu, dos, mem |
| `abstand_ms` | `gap_ms` | input |
| `adr` | `addr` | disasm, events, fileio, func, symbols, watch |
| `adresse` | `address` | cpu, disasm, exec, func, mem, symbols, watch |
| `adressraum` | `address_space` | modules, symbols |
| `aelteste_im_puffer` | `oldest_in_buffer` | events |
| `aktion` | `action` | watch |
| `aktiv` | `active` | batch, calls, coverage, events, flow |
| `aktuell` | `current` | dos |
| `alias_deskriptor` | `alias_descriptor` | ints |
| `alle` | `all` | events, modules |
| `alles` | `all` | symbols |
| `alt` | `old` | watch |
| `an` | `on` | input |
| `angewendet` | `applied` | modules |
| `anhalten` | `halt` | watch |
| `anzahl` | `count` | calls, disasm, events, exec, flow, ints, modules, symbols, watch |
| `art` | `kind` | calls, cpu, dos, events, exec, fileio, flow, func, modules, symbols, watch |
| `aufgegeben` | `abandoned` | le |
| `aufruf` | `call` | func, state |
| `aufzeichnung` | `recording` | events |
| `ausdruck` | `expression` | exec |
| `ausserhalb` | `outside` | dos |
| `ausstehend` | `pending` | input |
| `ax_vorher` | `ax_before` | ints |
| `basis` | `base` | addr, cpu, ints, mode |
| `basis_linear` | `base_linear` | coverage, le, modules, symbols |
| `basis_linear_geladen` | `base_linear_loaded` | symbols |
| `bedingung` | `condition` | exec |
| `befehle` | `commands` | calls, coverage, flow, func |
| `befehls_hook` | `instruction_hook` | exec |
| `befehlszeile` | `command_line` | dos, events |
| `beides` | `both` | watch |
| `bereich` | `range` | input |
| `bereich_entsperren` | `region_unlock` | ints |
| `bereich_sperren` | `region_lock` | ints |
| `bereiche` | `ranges` | calls |
| `besitzer` | `owner` | dos |
| `bilder` | `frames` | exec |
| `bis` | `to` | calls |
| `bis` | `until` | exec |
| `blockgroesse` | `block_size` | coverage |
| `bloecke` | `blocks` | coverage, dos |
| `breite` | `width` | screen, watch |
| `call_far_direkt` | `call_far_direct` | flow |
| `calls_datei` | `calls_file` | calls |
| `datei` | `file` | calls, dos, events, fileio, modules |
| `datei_vorschau` | `file_preview` | events |
| `dateien` | `files` | dos |
| `dateiname` | `filename` | ints |
| `daten` | `data` | cpu |
| `dauer_ms` | `duration_ms` | input |
| `deskriptor` | `descriptor` | mode |
| `deskriptor_lesen` | `descriptor_get` | ints |
| `deskriptor_setzen` | `descriptor_set` | ints |
| `dos_speicher_anlegen` | `dos_memory_allocate` | ints |
| `dos_speicher_freigeben` | `dos_memory_free` | ints |
| `eingabe` | `input` | ints |
| `einmalig` | `once` | exec |
| `einsprung` | `entry` | dos, events |
| `einsprung_linear` | `entry_linear` | le |
| `einsprung_va` | `entry_va` | le |
| `eintraege` | `entries` | cpu, exec, watch |
| `eltern` | `parent` | dos |
| `ende` | `end` | calls, disasm, dos, func |
| `entfernt` | `removed` | exec, watch |
| `ereignisse` | `events` | events |
| `ergebnis` | `result` | fileio, ints |
| `erreicht` | `reached` | input |
| `erstes_nr` | `first_nr` | flow |
| `esp_ein` | `esp_in` | calls, func |
| `esp_nach` | `esp_after` | calls, func |
| `exec_anzahl` | `exec_count` | dos, modules |
| `faktor` | `factor` | screen |
| `fehlend` | `missing` | mem |
| `fehler` | `error` | batch, cpu, fileio |
| `fertig` | `done` | batch, le |
| `flags_aus` | `flags_out` | calls, func |
| `flags_ein` | `flags_in` | calls, func |
| `fortsetzen` | `resume` | coverage |
| `frei` | `free` | dos |
| `freier_speicher` | `free_memory` | ints |
| `funktion` | `function` | ints |
| `geaendert` | `changed` | func |
| `geaendert_gekuerzt` | `changed_truncated` | func |
| `gedrueckt` | `pressed` | input |
| `gefunden` | `found` | dos |
| `geklemmt` | `clamped` | input |
| `geladen` | `loaded` | state |
| `geschrieben` | `written` | calls, func |
| `gesetzt` | `set` | modules |
| `grafik` | `graphics` | screen |
| `groesse` | `size` | dos, ints, le, state, symbols |
| `gross` | `uppercase` | cpu |
| `halt_adr` | `halt_addr` | func |
| `haupt` | `main` | calls |
| `herkunft` | `origin` | modules |
| `hinweis` | `hint` | le, mem, modules |
| `hinweise` | `hints` | symbols, watch |
| `hits_pfad` | `hits_path` | coverage |
| `hoehe` | `height` | screen |
| `indirekt` | `indirect` | flow |
| `info_pfad` | `info_path` | state |
| `int_haltepunkte` | `int_breakpoints` | exec |
| `int_modus` | `int_mode` | events |
| `ints_weggelassen` | `ints_omitted` | func |
| `jft_fehler` | `jft_error` | dos |
| `jmp_far_direkt` | `jmp_far_direct` | flow |
| `json_pfad` | `json_path` | calls, coverage, mem |
| `kaputt` | `broken` | dos |
| `kein_le` | `not_le` | le |
| `keine` | `no` | symbols |
| `knoepfe` | `buttons` | input |
| `knopf` | `button` | input |
| `kontext` | `context` | calls |
| `kontexte` | `contexts` | coverage |
| `koordinaten` | `coords` | input |
| `laenge` | `length` | cpu, disasm, fileio, func, mem, watch |
| `laeuft` | `running` | exec, func |
| `ldt_deskriptor_freigeben` | `ldt_descriptor_free` | ints |
| `ldt_deskriptoren_anlegen` | `ldt_descriptors_allocate` | ints |
| `leeren` | `clear` | watch |
| `lesen` | `read` | fileio, watch |
| `letzte_nr` | `last_nr` | events |
| `letzter_exit_code` | `last_exit_code` | batch |
| `limit_erreicht` | `limit_reached_flag` | calls, coverage, events, flow |
| `links` | `left` | input |
| `maschine` | `machine` | state |
| `max_befehle` | `max_instructions` | func |
| `max_bloecke` | `max_blocks` | coverage |
| `max_eintraege` | `max_entries` | watch |
| `max_je_ziel` | `max_per_target` | calls |
| `max_paare` | `max_pairs` | flow |
| `max_treffer` | `max_hits` | mem |
| `max_zustaende` | `max_states` | calls |
| `mit_returns` | `with_returns` | flow |
| `mit_rueckgabe` | `with_return` | events |
| `mitte` | `middle` | input |
| `modul` | `module` | coverage, dos, events, modules, symbols |
| `modul_info` | `module_info` | modules |
| `module` | `modules` | calls, modules |
| `modus` | `mode` | addr, calls, coverage, cpu, fileio, ints, mode, screen |
| `nach` | `to` | mode |
| `nachher` | `after` | func |
| `naechste` | `next` | disasm |
| `neuer_name` | `new_name` | ints |
| `nr_ende` | `nr_end` | calls |
| `nummer` | `number` | calls, events, exec, ints |
| `objekte` | `objects` | le |
| `offen` | `open` | calls, le |
| `ohne_pruefung` | `skip_check` | state |
| `paare` | `pairs` | flow |
| `paragraphen` | `paragraphs` | dos, events, ints, modules |
| `pfad` | `path` | coverage, disasm, flow, func, mem, screen, state, watch |
| `physisch_abbilden` | `physical_map` | ints |
| `pm_vektor_lesen` | `pm_vector_get` | ints |
| `pm_vektor_setzen` | `pm_vector_set` | ints |
| `praesent` | `present` | cpu |
| `programm` | `program` | dos |
| `programme` | `programs` | batch, dos |
| `programmende` | `program_end` | func |
| `protokollieren` | `log` | watch |
| `puffer` | `buffer` | fileio |
| `quelle` | `source` | exec, flow, screen, watch |
| `quelle_breite` | `source_width` | screen |
| `quelle_hoehe` | `source_height` | screen |
| `rechte` | `rights` | ints |
| `rechts` | `right` | input |
| `referenzen` | `refs` | dos |
| `regs_aus` | `regs_out` | calls, func |
| `regs_ein` | `regs_in` | calls, func |
| `rel_basis` | `rel_base` | dos, events, modules |
| `ring_eintraege` | `ring_entries` | flow |
| `ring_gesamt` | `ring_total` | flow |
| `ring_groesse` | `ring_size` | flow |
| `rm_interrupt_simulieren` | `rm_interrupt_simulate` | ints |
| `rm_vektor_lesen` | `rm_vector_get` | ints |
| `rm_vektor_setzen` | `rm_vector_set` | ints |
| `roh` | `raw` | cpu |
| `ruecksprung` | `return_address` | calls, func |
| `schreiben` | `write` | fileio, func, watch |
| `schritt` | `step` | exec |
| `schritte` | `steps` | input |
| `segment_zu_deskriptor` | `segment_to_descriptor` | ints |
| `segmentbasis_lesen` | `segment_base_get` | ints |
| `segmentbasis_setzen` | `segment_base_set` | ints |
| `segmente` | `segments` | modules, symbols |
| `segmentlimit_setzen` | `segment_limit_set` | ints |
| `seit_nr` | `since_nr` | events |
| `seiten` | `pages` | cpu, watch |
| `selektor` | `selector` | cpu, ints |
| `selektor_schrittweite` | `selector_increment` | ints |
| `spalte` | `column` | screen |
| `spalten` | `columns` | screen |
| `speicher_anlegen` | `memory_allocate` | ints |
| `speicher_freigeben` | `memory_free` | ints |
| `speicher_groesse_aendern` | `memory_resize` | ints |
| `speicher_seiten` | `memory_pages` | state |
| `spiel` | `game` | input |
| `stack_laenge` | `stack_length` | calls |
| `stderr_pfad` | `stderr_path` | batch |
| `stdout_pfad` | `stdout_path` | batch |
| `symbole` | `symbols` | modules, symbols |
| `symbole_basis` | `symbols_base` | modules |
| `symbole_segmente` | `symbols_segments` | modules |
| `tabelle` | `table` | cpu |
| `taste` | `key` | input |
| `tiefe` | `depth` | calls |
| `treffer` | `hits` | coverage, exec, mem, watch |
| `typ` | `type` | cpu, dos, events, screen |
| `typen` | `types` | events |
| `umgebung` | `environment` | dos |
| `unbekannt` | `unknown` | calls |
| `unterbrochen` | `interrupted` | func |
| `va_ende` | `va_end` | modules |
| `vektor` | `vector` | calls, flow, func, ints |
| `verpasst` | `missed` | events |
| `voll` | `full` | watch |
| `von` | `from` | calls, mode |
| `vorher` | `before` | func |
| `vorschau` | `preview` | fileio |
| `waechter` | `watchpoint` | watch |
| `wartet_auf` | `waiting_for` | exec |
| `weggelassen` | `omitted` | calls |
| `weitere_linear` | `more_linear` | func |
| `weitergereicht_von` | `passed_on_from` | ints |
| `wert` | `value` | exec, watch |
| `zeichen` | `char` | input |
| `zeile` | `line` | screen |
| `zeilen` | `lines` | disasm, screen |
| `zeit_ms` | `time_ms` | events, exec, state, watch |
| `ziel` | `target` | calls, disasm, exec, flow, input, ints, modules, watch |
| `ziele` | `targets` | calls |
| `zu_nr` | `to_nr` | ints |
| `zugriffe` | `accesses` | watch |
| `zugriffsrechte_setzen` | `access_rights_set` | ints |
| `zuordnung` | `assignment` | le |
| `zustaende` | `states` | calls |
| `zustaende_geschrieben` | `states_written` | calls |
| `zustaende_weggelassen` | `states_omitted` | calls |
| `zustand` | `state` | calls, func |
| `zustand_fehler` | `state_error` | calls |

Special cases worth knowing:

- `bis`: `to` in address ranges (`calls.start` ranges, `{"from","to"}`), `until` in `exec` (pause until) and
  `snapshot`. `von` -> `from`, `nach` -> `to` (mode switch).
- `module` (list in `calls.start`, filter echo, `modules.list` result, snapshot meta) -> `modules`;
  `modul` (one name) -> `module`.
- `an` -> `on` (`input.own`), `alt` -> `old` (watch), `adr` -> `addr`, `art` -> `kind`.
- `weiter` -> `continue`, `anhalten` -> `halt`, `protokollieren` -> `log`.
- `spiel` -> `game` (mouse coordinates), `haupt` -> `main` (calls).
- `limit_erreicht` -> `limit_reached_flag` (recording status flag); `ruecksprung` -> `return_address`.

## 8. Glossary

| German | English |
|---|---|
| Haltepunkt | breakpoint |
| Waechter | watchpoint |
| Momentaufnahme | snapshot |
| Speicherstand | savestate |
| Aufzeichnung | recording |
| Aufrufprotokoll | call log |
| Kontrollfluss | control flow |
| Abdeckung | coverage |
| Ereignis | event |
| Instanz | instance |
| Lauf | run |
| Szenario | scenario |
| Ausgabeordner | output directory |
| Zielprogramm | target program |
| Zustand | state |
| Befehl | instruction (CPU) / command (DOS, CLI) |
| Aufruf | call |
| Ruecksprung | return |
| Bereich | range |
| Treffer | hit |
| Fehler | error |
| Hinweis | hint |
| Gast | guest |
| Segment, Selektor, Deskriptor | segment, selector, descriptor |

## 9. Checklist for callers (for example Decomp)

1. Replace all parameter names in calls to `Dosbox`, `run_batch`, `play_scenario` and the CLI (sections 3 to 5).
2. Rename every key you read from results and from recorded files (section 7); watch for `bis`, `module`.
3. Change enum values you send or compare (`write/read/both`, `halt/log`, `left/right/middle`, `file`, `all`).
4. Write symbol files with format `decomp-symbols/1` and the English keys of `legacy.py` (the old format is still read).
5. Re-record or convert old recordings if they must be read with the new key names.
6. Rebuild the emulator (`build.ps1`) so that C++ and Python use the same wire names.
