# SPDX-License-Identifier: GPL-2.0-or-later
"""run_batch: run a DOS command headless, collect exit code, output and files."""
import json
import os
import time
from pathlib import Path

from .errors import DmcpError
from .instance import Instance

# quit warning=false: closing the window never asks (no dialog that would block the emulation thread).
STANDARD_CONF = {"cpu": {"cycles": "max"}, "dosbox": {"quit warning": "false"}}
STANDARD_MAX_OUTPUT = 16 * 1024 * 1024
MAX_FILES = 20000
SHORT_BYTES = 4096
END_WAIT_S = 3.0  # how long the process may live on after a lost connection (shutdown after EXIT)


def _snapshot_files(mounts):
    """Files of the writable mounts: {(drive, relative path): (size, mtime_ns, host path)}, complete?"""
    state = {}
    for mount in mounts:
        if mount.cdrom:
            continue
        root = Path(mount.host)
        for dirpath, _dirs, files in os.walk(root):
            for name in files:
                if len(state) >= MAX_FILES:
                    return state, False
                host = Path(dirpath) / name
                try:
                    st = host.stat()
                except OSError:
                    continue
                state[(mount.drive.upper(), str(host.relative_to(root)))] = (st.st_size, st.st_mtime_ns, str(host))
    return state, True


def _compare(before, after):
    files = []
    for key, (size, mtime, host) in sorted(after.items()):
        old = before.get(key)
        if old is not None and old[:2] == (size, mtime):
            continue
        files.append({"drive": key[0], "path": key[1], "host": host, "size": size,
                        "kind": "new" if old is None else "changed"})
    for key, (size, _mtime, host) in sorted(before.items()):
        if key not in after:
            files.append({"drive": key[0], "path": key[1], "host": host, "size": size,
                            "kind": "deleted"})
    return files


def _short(path):
    if path is None or not path.exists():
        return ""
    with open(path, "rb") as f:
        return f.read(SHORT_BYTES).decode("cp437")


def _wait_end(inst, seconds):
    """True if the process ends within `seconds` (or has already exited)."""
    end = time.monotonic() + seconds
    while inst.alive():
        if time.monotonic() >= end:
            return False
        time.sleep(0.05)
    return True


def _exited_normally(inst):
    """Process exited with exit code 0 (normal shutdown); cannot be checked without an own process handle."""
    proc = getattr(inst, "_proc", None)
    return proc is None or proc.poll() == 0


def _batch_file(inst):
    """Last state from <outdir>/batch.json (written by DMCPBAT at START, program start/end and END)."""
    try:
        return json.loads((inst.outdir / "batch.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _screen(inst):
    """Text mode -> screen.txt, otherwise screen.png; None if no frame can be obtained."""
    try:
        text = inst.client.call("screen.text", {})
    except DmcpError as exc:
        if exc.code != "not_text_mode":
            return None
        try:
            return inst.client.call("screen.capture", {"name": "screen"})["path"]
        except DmcpError:
            return None
    path = inst.outdir / "screen.txt"
    path.write_text("\n".join(line.rstrip() for line in text["lines"]) + "\n", encoding="utf-8")
    return str(path)


def _read_text(path):
    try:
        return path.read_bytes().decode("cp437")
    except OSError:
        return ""


def run_batch(mounts, command, timeout_s=60.0, max_output=STANDARD_MAX_OUTPUT, conf=None, exe=None, watch=None,
              snapshot=True):
    """Run `command` (program, batch file or shell command) in its own headless instance.
    mounts: list of Mount (the first one is the current drive).
    watch: optional callable, called while the command runs (every poll, about 50 ms) with the captured stdout so far
    (text, code page 437); when it returns True the instance is stopped and the status is "stopped".
    snapshot=False skips listing the files of the writable mounts before and after ("files" is then empty and
    "files_complete" False), for callers that track their files themselves.
    Result: dict, status done/timeout/stopped/shell_exited/crashed."""
    if not isinstance(command, str) or not command.strip() or "\n" in command or "\r" in command:
        raise DmcpError("bad_argument", "command must be a single, non-empty command line")
    if not command.isascii():
        raise DmcpError("bad_argument", "command may only contain ASCII characters (config file and guest code page)")
    if not mounts:
        raise DmcpError("bad_argument", "at least one mount is required (e.g. drive C:)")
    if not timeout_s or timeout_s <= 0:
        raise DmcpError("bad_argument", "timeout_s must be greater than 0")
    if not isinstance(max_output, int) or max_output < 1:
        raise DmcpError("bad_argument", "max_output must be an integer >= 1")
    merged = {section: dict(values) for section, values in STANDARD_CONF.items()}
    for section, values in (conf or {}).items():
        merged.setdefault(section, {}).update(values)
    if watch is not None and not callable(watch):
        raise DmcpError("bad_argument", "watch must be callable")
    before, full_before = _snapshot_files(mounts) if snapshot else ({}, False)
    autoexec = [f"@Z:\\DMCPBAT.COM START {max_output}", f"@CALL {command}", "@Z:\\DMCPBAT.COM END"]
    started = time.monotonic()
    inst = Instance.start(mounts=list(mounts), autoexec=autoexec, headless=True, exe=exe, conf=merged)
    result = {"instance": inst.id, "command": command}
    status, batch = "timeout", None
    try:
        deadline = started + timeout_s
        while True:
            try:
                batch = inst.client.call("batch.status", {})
            except DmcpError as exc:
                # Connection gone: the process may still be shutting down (EXIT in the command), so wait briefly for its end
                if not inst.alive() or (exc.code != "timeout" and _wait_end(inst, END_WAIT_S)):
                    file = _batch_file(inst)
                    if file is not None and file.get("active") and _exited_normally(inst):
                        status, batch = "shell_exited", file  # shell exited (EXIT), DMCPBAT END never ran
                    else:
                        status = "crashed"
                else:
                    result["rpc_error"] = f"{exc.code}: {exc.message}"
                break
            if batch["done"]:
                status = "done"
                break
            if watch is not None and watch(_read_text(inst.outdir / "stdout.txt")):
                status = "stopped"
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        result["runtime_s"] = round(time.monotonic() - started, 3)
        if status == "crashed":
            result["screen"] = None
            result["log_end"] = inst.log_tail()
        elif status == "shell_exited" or "rpc_error" in result:
            result["screen"] = None  # process exited or emulator hangs: no further RPCs
        else:
            result["screen"] = _screen(inst)
            try:
                batch = inst.client.call("batch.status", {})  # byte counters after the last state
            except DmcpError:
                pass
    finally:
        inst.stop()
    out, err = inst.outdir / "stdout.txt", inst.outdir / "stderr.txt"
    after, full_after = _snapshot_files(mounts) if snapshot else ({}, False)
    result.update({
        "status": status,
        "exit_code": (batch["exit_code"] if status == "done"
                      else batch.get("last_exit_code") if status == "shell_exited" else None),
        "programs": batch["programs"] if batch else 0,
        "truncated": bool(batch and batch["truncated"]),
        "stdout": str(out) if out.exists() else None,
        "stderr": str(err) if err.exists() else None,
        "stdout_text": _short(out),
        "stderr_text": _short(err),
        "log": str(inst.rundir / "dosbox.log"),
        "files": _compare(before, after),
        "files_complete": full_before and full_after,
    })
    if batch and batch.get("error"):
        result["error"] = batch["error"]
    return result
