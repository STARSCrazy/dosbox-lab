# SPDX-License-Identifier: GPL-2.0-or-later
"""JSON-RPC 2.0 over the emulator's named pipe (JSON Lines)."""
import json
import threading
import time

from .errors import DmcpError


class PipeClient:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self._file = None
        self._buffer = b""
        self._next_id = 0
        self._lock = threading.Lock()

    def connect(self, timeout_s=30.0, alive=None):
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                self._file = open(self.endpoint, "r+b", buffering=0)
                return
            except OSError:
                if alive is not None and not alive():
                    raise DmcpError("instance_dead", "emulator process exited before the pipe was ready")
                if time.monotonic() > deadline:
                    raise DmcpError("timeout", f"pipe {self.endpoint} not reachable within {timeout_s}s")
                time.sleep(0.1)

    def close(self):
        if self._file is not None:
            try:
                self._file.close()
            except OSError:
                pass
            self._file = None

    def call(self, method, params=None):
        with self._lock:  # writing and reading the reply belong together (several threads, one pipe)
            if self._file is None:
                raise DmcpError("instance_dead", "no connection to the emulator")
            self._next_id += 1
            request = {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": params or {}}
            try:
                self._file.write((json.dumps(request) + "\n").encode("utf-8"))
                line = self._read_line()
            except OSError as exc:
                self.close()
                raise DmcpError("instance_dead", f"connection to the emulator lost: {exc}") from exc
        response = json.loads(line)
        if "error" in response:
            err = response["error"]
            data = err.get("data") or {}
            raise DmcpError(data.get("code", "rpc_error"), err.get("message", "unknown error"))
        return response.get("result", {})

    def _read_line(self):
        while b"\n" not in self._buffer:
            chunk = self._file.read(65536)
            if not chunk:
                self.close()
                raise DmcpError("instance_dead", "the emulator closed the connection")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return line.decode("utf-8")
