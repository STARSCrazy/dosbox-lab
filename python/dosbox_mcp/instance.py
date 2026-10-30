"""Emulator-Instanzen starten, wiederfinden und beenden."""
import ctypes
import json
import os
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

from .errors import DmcpError
from .pipe import PipeClient

REPO = Path(__file__).resolve().parents[2]
DEFAULT_EXE = REPO / "bin" / "x64" / "Release SDL2" / "dosbox-x.exe"
RUNS = REPO / ".runs"

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
SW_SHOWNOACTIVATE = 4


@dataclass
class Mount:
    drive: str          # Laufwerksbuchstabe, z. B. "C"
    host: str           # absoluter Ordner auf dem Host
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
        "transport=named_pipe",
        f"endpoint={endpoint}",
        f"dosbox_executable={exe}",
        f"dosbox_workdir={rundir}",
        "profile=production",
        "request_timeout_ms=10000",
        "max_message_bytes=8388608",
        "max_memory_read_bytes=65536",
        "max_trace_events=10000",
    ]) + "\n"


def _image_name(handle):
    """Voller Image-Pfad des Prozesses oder None."""
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_ulong(len(buf))
    if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
        return None
    return buf.value


def _pid_alive(pid):
    """Laeuft der Prozess noch und ist es dosbox-x.exe? (PIDs werden wiederverwendet.)"""
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
        return bool(name) and name.lower().endswith("dosbox-x.exe")
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
        """C:\\DIR\\DATEI.EXE -> Host-Pfad ueber die Mounts dieser Instanz; None, wenn kein Mount passt."""
        if len(dos_path) < 3 or dos_path[1:3] != ":\\":
            return None
        for mount in self.mounts:
            if mount.drive.upper() == dos_path[0].upper():
                return Path(mount.host).joinpath(*[part for part in dos_path[3:].split("\\") if part])
        return None

    @classmethod
    def start(cls, mounts=(), autoexec=(), headless=False, exe=None, inst_id=None, conf=None, timeout_s=30, outdir=None):
        exe = Path(exe or DEFAULT_EXE)
        if not exe.exists():
            raise DmcpError("bad_argument", f"Emulator nicht gefunden: {exe} (erst build.ps1 laufen lassen)")
        # DOSBox loest relative Mount-Pfade gegen den Laufordner auf: gegen das cwd des Aufrufers festlegen
        mounts = [Mount(m.drive, str(Path(m.host).resolve()), m.cdrom) for m in mounts]
        inst_id = inst_id or uuid.uuid4().hex[:8]
        rundir = RUNS / inst_id
        endpoint = rf"\\.\pipe\dosbox-mcp-{inst_id}"
        out = Path(outdir).resolve() if outdir else rundir / "out"
        try:
            rundir.mkdir(parents=True, exist_ok=True)
            out.mkdir(parents=True, exist_ok=True)
            (rundir / "dosbox.conf").write_text(_conf_text(mounts, autoexec, conf), encoding="mbcs")
            (rundir / "agent.env").write_text(_agent_text(endpoint, exe, rundir), encoding="mbcs")
            env = dict(os.environ)
            env["DMCP_OUTDIR"] = str(out)
            if headless:
                env["SDL_VIDEODRIVER"] = "dummy"
                env["SDL_AUDIODRIVER"] = "dummy"
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = SW_SHOWNOACTIVATE
            # Kein stdout/stderr umleiten (DOSBox-X-Debugger braucht echte Konsolen-Handles).
            proc = subprocess.Popen(
                [str(exe), "-conf", str(rundir / "dosbox.conf"), "-agent-config", str(rundir / "agent.env"),
                 "-fastlaunch", "-nomenu"],
                cwd=str(rundir), env=env, startupinfo=startupinfo,
                creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, close_fds=True)
        except OSError as exc:  # z. B. WinError 32, solange die exe neu gebaut wird
            raise DmcpError("internal", f"emulator could not be started: {exc}") from exc
        inst = cls(inst_id, rundir, endpoint, proc.pid, outdir=out)
        inst._proc = proc
        (rundir / "instance.json").write_text(json.dumps({
            "id": inst_id, "pid": proc.pid, "endpoint": endpoint, "headless": headless,
            "mounts": [m.__dict__ for m in mounts], "autoexec": list(autoexec),
            "outdir": str(out)}, indent=2), encoding="utf-8")
        try:
            inst.client.connect(timeout_s, alive=inst.alive)
            inst.client.call("dmcp.ping")
        except DmcpError:
            inst.stop()
            raise
        return inst

    @classmethod
    def attach(cls, inst_id, timeout_s=10):
        info_path = RUNS / inst_id / "instance.json"
        if not info_path.exists():
            raise DmcpError("bad_argument", f"Instanz {inst_id} unbekannt")
        info = json.loads(info_path.read_text(encoding="utf-8"))
        inst = cls(inst_id, RUNS / inst_id, info["endpoint"], info["pid"], outdir=info.get("outdir"))
        if not inst.alive():
            raise DmcpError("instance_dead", f"Instanz {inst_id} laeuft nicht mehr")
        inst.client.connect(timeout_s, alive=inst.alive)
        return inst

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
            subprocess.run(["taskkill", "/PID", str(self.pid), "/F"], capture_output=True)

    def log_tail(self, lines=40):
        log = self.rundir / "dosbox.log"
        if not log.exists():
            return ""
        return "\n".join(log.read_text(encoding="mbcs", errors="replace").splitlines()[-lines:])
