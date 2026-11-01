# SPDX-License-Identifier: GPL-2.0-or-later
"""Start, find again and stop emulator instances."""
import ctypes
import json
import os
import signal
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import DmcpError
from .pipe import PipeClient

WINDOWS = os.name == "nt"
REPO = Path(__file__).resolve().parents[2]
_EXE_ENV = os.environ.get("DOSBOX_LAB_EXE") or os.environ.get("DOSBOX_MCP_EXE")  # old name as fallback
# Windows: build.ps1 (MSBuild); Linux/macOS: build-lab (autotools, binary in src/).
_BUILT_EXE = REPO / "bin" / "x64" / "Release SDL2" / "dosbox-lab.exe" if WINDOWS else REPO / "src" / "dosbox-x"
DEFAULT_EXE = Path(_EXE_ENV) if _EXE_ENV else _BUILT_EXE
RUNS = Path(os.environ.get("DOSBOX_LAB_RUNS") or REPO / ".runs")
# dosbox.conf and agent.env are read with the C runtime: ANSI code page on Windows, UTF-8 elsewhere.
TEXT_ENCODING = "mbcs" if WINDOWS else "utf-8"
# Executable names that count as an emulator of ours when a PID is checked (PIDs get reused).
EXE_NAMES = ("dosbox-lab.exe",) if WINDOWS else ("dosbox-lab", "dosbox-x")

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
SW_SHOWNOACTIVATE = 4


@dataclass
class Mount:
    drive: str          # drive letter, e.g. "C"
    host: str           # absolute folder on the host
    cdrom: bool = False


def _conf_text(mounts, autoexec, extra):
    sections = {
        "sdl": {"output": "surface", "autolock": "false", "fullscreen": "false", "mouse_wheel_key": "0"},
        "dosbox": {"memsize": "16"},
        "cpu": {"core": "normal", "cputype": "pentium", "cycles": "auto"},
        "dos": {"ems": "false"},
        "log": {"logfile": "dosbox.log"},
    }
    for section, values in (extra or {}).items():
        sections.setdefault(section, {}).update(values)
    lines = []
    for section, values in sections.items():
        lines.append(f"[{section}]")
        lines += [f"{key}={value}" for key, value in values.items()]
        lines.append("")
    lines.append("[autoexec]")
    for mount in mounts:
        kind = " -t cdrom" if mount.cdrom else ""
        lines.append(f'mount {mount.drive} "{mount.host}"{kind}')
    if mounts:
        lines.append(f"{mounts[0].drive}:")
    lines += list(autoexec)
    return "\n".join(lines) + "\n"


def _agent_text(endpoint, exe, rundir):
    return "\n".join([
        f"transport={'named_pipe' if WINDOWS else 'unix_socket'}",
        f"endpoint={endpoint}",
        f"dosbox_executable={exe}",
        f"dosbox_workdir={rundir}",
        "profile=production",
        "request_timeout_ms=10000",
        "max_message_bytes=8388608",
        "max_memory_read_bytes=65536",
        "max_trace_events=10000",
    ]) + "\n"


def _endpoint(inst_id, rundir):
    """Named pipe on Windows; elsewhere a Unix socket in the (private) run folder, or in a private temp
    folder if the path would exceed the socket path limit (104 bytes on macOS, 108 on Linux)."""
    if WINDOWS:
        return rf"\\.\pipe\dosbox-lab-{inst_id}"
    path = rundir / "agent.sock"
    if len(os.fsencode(path)) < 100:
        return str(path)
    return str(Path(tempfile.mkdtemp(prefix=f"dosbox-lab-{inst_id}-")) / "agent.sock")


def _image_name(handle):
    """Full image path of the process, or None."""
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_ulong(len(buf))
    if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return None
    return buf.value


def _posix_image_name(pid):
    """Executable name of a running process (Linux: /proc, else ps), or None."""
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        pass
    try:
        out = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() or None


def _pid_alive(pid):
    """Is the process still running and is it our emulator? (PIDs get reused.)"""
    if not WINDOWS:
        try:
            os.kill(pid, 0)
        except PermissionError:
            return False  # someone else's process
        except OSError:
            return False
        name = _posix_image_name(pid)
        if name and name.endswith(" (deleted)"):  # binary rebuilt while running
            name = name[:-len(" (deleted)")]
        return bool(name) and os.path.basename(name).lower() in EXE_NAMES
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code)):
            return False
        if code.value != 259:  # STILL_ACTIVE
            return False
        try:
            name = _image_name(ctypes.c_void_p(handle))
        except (OSError, ValueError, AttributeError):
            return False
        return bool(name) and name.lower().endswith(EXE_NAMES)
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


class Instance:
    def __init__(self, inst_id, rundir, endpoint, pid, outdir=None):
        self.id = inst_id
        self.rundir = Path(rundir)
        self.endpoint = endpoint
        self.pid = pid
        self.client = PipeClient(endpoint)
        self._proc = None
        self._outdir = Path(outdir) if outdir else None

    @property
    def outdir(self):
        return self._outdir or self.rundir / "out"

    @property
    def mounts(self):
        info = json.loads((self.rundir / "instance.json").read_text(encoding="utf-8"))
        return [Mount(**m) for m in info.get("mounts", [])]

    def host_path(self, dos_path):
        """C:\\DIR\\FILE.EXE -> host path via the mounts of this instance; None if no mount matches."""
        if len(dos_path) < 3 or dos_path[1:3] != ":\\":
            return None
        for mount in self.mounts:
            if mount.drive.upper() == dos_path[0].upper():
                return Path(mount.host).joinpath(*[part for part in dos_path[3:].split("\\") if part])
        return None

    @classmethod
    def start(cls, mounts=(), autoexec=(), headless=False, exe=None, inst_id=None, conf=None, timeout_s=30, outdir=None,
              early_exit_ok=False):
        """early_exit_ok: an emulator that ends before the control channel was reached (a batch with EXIT)
        is returned instead of raising instance_dead; the caller inspects its output files."""
        exe = Path(exe or DEFAULT_EXE)
        if not exe.exists():
            raise DmcpError("bad_argument",
                            f"emulator not found: {exe} (run {'build.ps1' if WINDOWS else './build-lab'} first)")
        # DOSBox resolves relative mount paths against the run folder: pin them to the caller's cwd
        mounts = [Mount(m.drive, str(Path(m.host).resolve()), m.cdrom) for m in mounts]
        inst_id = inst_id or uuid.uuid4().hex[:8]
        rundir = RUNS / inst_id
        out = Path(outdir).resolve() if outdir else rundir / "out"
        try:
            rundir.mkdir(parents=True, exist_ok=True)
            if not WINDOWS:
                rundir.chmod(0o700)  # the control socket lives here
            endpoint = _endpoint(inst_id, rundir)
            out.mkdir(parents=True, exist_ok=True)
            (rundir / "dosbox.conf").write_text(_conf_text(mounts, autoexec, conf), encoding=TEXT_ENCODING)
            (rundir / "agent.env").write_text(_agent_text(endpoint, exe, rundir), encoding=TEXT_ENCODING)
            env = dict(os.environ)
            env["DMCP_OUTDIR"] = str(out)
            if headless:
                env["SDL_VIDEODRIVER"] = "dummy"
                env["SDL_AUDIODRIVER"] = "dummy"
            args = [str(exe), "-conf", str(rundir / "dosbox.conf"), "-agent-config", str(rundir / "agent.env"),
                    "-fastlaunch", "-nomenu"]
            if WINDOWS:
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startupinfo.wShowWindow = SW_SHOWNOACTIVATE
                # Do not redirect stdout/stderr (the DOSBox-X debugger needs real console handles).
                proc = subprocess.Popen(args, cwd=str(rundir), env=env, startupinfo=startupinfo,
                                        creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True)
            else:
                # Own session, no terminal: the debugger UI (curses) must not write into the caller's
                # stdout, which is the MCP channel when the MCP server starts the emulator.
                with open(rundir / "console.log", "wb") as console:
                    proc = subprocess.Popen(args, cwd=str(rundir), env=env, stdin=subprocess.DEVNULL,
                                            stdout=console, stderr=subprocess.STDOUT,
                                            start_new_session=True, close_fds=True)
        except UnicodeEncodeError as exc:  # a host path outside the ANSI code page (Windows)
            raise DmcpError("bad_argument", f"path cannot be passed to the emulator: {exc}") from exc
        except OSError as exc:  # e.g. WinError 32 while the exe is being rebuilt
            raise DmcpError("internal", f"emulator could not be started: {exc}") from exc
        inst = cls(inst_id, rundir, endpoint, proc.pid, outdir=out)
        inst._proc = proc
        try:
            (rundir / "instance.json").write_text(json.dumps({
                "id": inst_id, "pid": proc.pid, "endpoint": endpoint, "headless": headless,
                "mounts": [m.__dict__ for m in mounts], "autoexec": list(autoexec),
                "outdir": str(out)}, indent=2), encoding="utf-8")
            inst.client.connect(timeout_s, alive=inst.alive)
            inst.client.call("dmcp.ping")
        except DmcpError as exc:
            if early_exit_ok and exc.code == "instance_dead" and not inst.alive():
                inst.client.close()
                return inst
            inst.stop()
            raise
        except BaseException:  # also Ctrl+C while waiting: never leave a detached emulator behind
            inst.stop()
            raise
        return inst

    @classmethod
    def attach(cls, inst_id, timeout_s=10):
        info_path = RUNS / inst_id / "instance.json"
        if not info_path.exists():
            raise DmcpError("bad_argument", f"instance {inst_id} unknown")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        inst = cls(inst_id, RUNS / inst_id, info["endpoint"], info["pid"], outdir=info.get("outdir"))
        if not inst.alive():
            raise DmcpError("instance_dead", f"instance {inst_id} is no longer running")
        inst.client.connect(timeout_s, alive=inst.alive)
        return inst

    @classmethod
    def stop_by_id(cls, inst_id):
        """Stop an instance by its PID without connecting first (also works when the pipe hangs).
        Returns False if the instance was not running."""
        info_path = RUNS / inst_id / "instance.json"
        if not info_path.exists():
            raise DmcpError("bad_argument", f"instance {inst_id} unknown")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        inst = cls(inst_id, RUNS / inst_id, info["endpoint"], info["pid"], outdir=info.get("outdir"))
        was_alive = inst.alive()
        inst.stop()
        return was_alive

    def alive(self):
        if self._proc is not None:
            return self._proc.poll() is None
        return _pid_alive(self.pid)

    def stop(self):
        self.client.close()
        if self._proc is not None:
            if self._proc.poll() is None:
                self._proc.terminate()
                try:
                    self._proc.wait(5)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
        elif _pid_alive(self.pid):
            if WINDOWS:
                subprocess.run(["taskkill", "/PID", str(self.pid), "/F"], capture_output=True)
            else:
                os.kill(self.pid, signal.SIGTERM)
                end = time.monotonic() + 5
                while time.monotonic() < end and _pid_alive(self.pid):
                    time.sleep(0.05)
                if _pid_alive(self.pid):
                    os.kill(self.pid, signal.SIGKILL)
        if not WINDOWS and self.endpoint.endswith("agent.sock"):
            try:  # the emulator removes it on a clean exit, not after SIGKILL
                os.unlink(self.endpoint)
            except OSError:
                pass

    def log_tail(self, lines=40):
        log = self.rundir / "dosbox.log"
        if not log.exists():
            return ""
        return "\n".join(log.read_text(encoding=TEXT_ENCODING, errors="replace").splitlines()[-lines:])
