# SPDX-License-Identifier: GPL-2.0-or-later
"""Legacy (German) names that are still accepted when reading old files.

All names in dosbox-lab are English. Earlier versions used German names; files written by other programs (symbol
files, scenario scripts) may still carry them. The readers translate them with the tables below, so old files keep
working. This module is the only place with German names; see docs/dosbox-lab/MIGRATION.md for the full mapping.
"""
import copy

# --- Symbol files (written by Decomp) -------------------------------------------------------------------------
SYMBOL_FORMAT = "decomp-symbols/1"
LEGACY_SYMBOL_FORMATS = ("decomp-symbole/1",)
_SYMBOL_FILE_KEYS = {"modul": "module", "adressraum": "address_space", "symbole": "symbols",
                     "segmente": "segments", "basis_linear": "base_linear"}
_SYMBOL_SEGMENT_KEYS = {"va_ende": "va_end", "basis_linear": "base_linear"}
_SYMBOL_ENTRY_KEYS = {"adr": "addr", "groesse": "size", "art": "kind"}


def _rename(obj, names):
    return {names.get(key, key): value for key, value in obj.items()}


def normalize_symbol_file(data):
    """Return `data` (a parsed symbol file) with legacy German keys replaced by the current ones."""
    data = _rename(data, _SYMBOL_FILE_KEYS)
    if isinstance(data.get("segments"), list):
        data["segments"] = [_rename(s, _SYMBOL_SEGMENT_KEYS) if isinstance(s, dict) else s
                            for s in data["segments"]]
    if isinstance(data.get("symbols"), list):
        data["symbols"] = [_rename(s, _SYMBOL_ENTRY_KEYS) if isinstance(s, dict) else s for s in data["symbols"]]
    return data


# --- Scenario scripts -----------------------------------------------------------------------------------------
LEGACY_SCENARIO_FORMATS = ("dosbox-lab-szenario/1", "dosbox-mcp-szenario/1")
_SCENARIO_TOP = {"variablen": "variables", "schritte": "steps"}
_SCENARIO_MOUNT = {"laufwerk": "drive", "pfad": "path"}
_SCENARIO_STEP_COMMON = {"notiz": "note"}
_SCENARIO_KINDS = {
    "tippe": "type", "taste": "key", "klick": "click", "maus_programm": "mouse_program",
    "warte_text": "wait_text", "warte_speicher": "wait_memory", "warte_halt": "wait_halt", "weiter": "continue",
    "warte_bilder": "wait_frames", "warte_bild": "wait_frame", "warte_s": "wait_s",
    "zustand_laden": "state_load", "speicherstand": "state_save", "aufzeichnung_start": "recording_start",
    "aufzeichnung_stop": "recording_stop",
}
_SCENARIO_VALUE_KEYS = {
    "knopf": "button", "x_adresse": "x_address", "y_adresse": "y_address", "breite": "width",
    "toleranz": "tolerance", "versuche": "attempts", "warte_s": "wait_s", "zeile": "line",
    "adresse": "address", "maske": "mask", "danach": "then", "art": "kind",
}
_SCENARIO_BUTTONS = {"links": "left", "rechts": "right", "mitte": "middle"}
_SCENARIO_THEN = {"anhalten": "halt", "weiter": "continue"}
# options of recording_start (parameters of calls_start, events_start, coverage_start, flow_start)
_RECORDING_OPTIONS = {
    "ziele": "targets", "bereiche": "ranges", "module": "modules", "max_je_ziel": "max_per_target",
    "zustaende": "states", "max_zustaende": "max_states", "typen": "types", "int_modus": "int_mode",
    "mit_rueckgabe": "with_return", "datei_vorschau": "file_preview", "max_bloecke": "max_blocks",
    "fortsetzen": "resume", "indirekt": "indirect", "ring_groesse": "ring_size", "mit_returns": "with_returns",
    "max_paare": "max_pairs",
}
_RANGE_KEYS = {"von": "from", "bis": "to"}
_INT_RULE_KEYS = {"nummer": "number"}
_EVENT_TYPES = {"datei": "file"}
_INT_MODES = {"alle": "all"}


def _normalize_recording(value):
    value = _rename(value, {**_SCENARIO_VALUE_KEYS, **_RECORDING_OPTIONS})
    if isinstance(value.get("ranges"), list):
        value["ranges"] = [_rename(r, _RANGE_KEYS) if isinstance(r, dict) else r for r in value["ranges"]]
    for key in ("ints", "int_filter"):
        if isinstance(value.get(key), list):
            value[key] = [_rename(r, _INT_RULE_KEYS) if isinstance(r, dict) else r for r in value[key]]
    if isinstance(value.get("types"), list):
        value["types"] = [_EVENT_TYPES.get(t, t) for t in value["types"]]
    if value.get("int_mode") in _INT_MODES:
        value["int_mode"] = _INT_MODES[value["int_mode"]]
    return value


def _normalize_step_value(kind, value):
    if not isinstance(value, dict):
        return value
    if kind in ("recording_start", "recording_stop"):
        return _normalize_recording(value)
    value = _rename(value, _SCENARIO_VALUE_KEYS)
    if kind == "click" and value.get("button") in _SCENARIO_BUTTONS:
        value["button"] = _SCENARIO_BUTTONS[value["button"]]
    if kind == "wait_halt" and value.get("then") in _SCENARIO_THEN:
        value["then"] = _SCENARIO_THEN[value["then"]]
    return value


def normalize_scenario(raw):
    """Return a copy of a scenario with legacy German keys and step kinds replaced by the current ones."""
    raw = _rename(copy.deepcopy(raw), _SCENARIO_TOP)
    start = raw.get("start")
    if isinstance(start, dict) and isinstance(start.get("mounts"), list):
        start["mounts"] = [_rename(m, _SCENARIO_MOUNT) if isinstance(m, dict) else m for m in start["mounts"]]
    if isinstance(raw.get("steps"), list):
        steps = []
        for step in raw["steps"]:
            if not isinstance(step, dict):
                steps.append(step)
                continue
            new = {}
            for key, value in step.items():
                if key in _SCENARIO_STEP_COMMON:
                    new[_SCENARIO_STEP_COMMON[key]] = value
                    continue
                kind = _SCENARIO_KINDS.get(key, key)
                new[kind] = _normalize_step_value(kind, value)
            steps.append(new)
        raw["steps"] = steps
    return raw
