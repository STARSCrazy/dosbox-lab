# SPDX-License-Identifier: GPL-2.0-or-later
"""JSON-RPC 2.0 over the emulator's control channel (JSON Lines): a named pipe on Windows,
a Unix domain socket elsewhere."""
import json
import socket
import threading
import time

from .errors import DmcpError


class _SocketFile:
    """The two calls PipeClient needs, on a connected socket (write = sendall, no partial writes)."""

    def __init__(self, sock):
        self._sock = sock

    def write(self, data):
        self._sock.sendall(data)

    def read(self, size):
        return self._sock.recv(size)

    def close(self):
        self._sock.close()


def _open(endpoint):
    if endpoint.startswith("\\\\.\\pipe\\"):
        return open(endpoint, "r+b", buffering=0)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.connect(endpoint)
    except OSError:
        sock.close()
        raise
    return _SocketFile(sock)


class PipeClient:
    def __init__(self, endpoint):
        self.endpoint = endpoint
        self._file = None
        self._buffer = b""
        self._next_id = 0
        self._lock = threading.Lock()
        self._alive = None

    def connect(self, timeout_s=30.0, alive=None):
        if alive is not None:
            self._alive = alive  # also used for the reconnect attempt in call()
        alive = alive or self._alive
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                self._file = _open(self.endpoint)
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
        self._buffer = b""  # a half-read reply must not be taken for the answer to the next request

    def call(self, method, params=None):
        with self._lock:  # writing and reading the reply belong together (several threads, one pipe)
            if self._file is None:
                # The connection was dropped (e.g. after an oversized message) while the emulator may
                # still run: one quick reconnect attempt before giving up.
                if self._alive is not None and not self._alive():
                    raise DmcpError("instance_dead", "no connection to the emulator (process ended)")
                try:
                    self.connect(timeout_s=2.0)
                except DmcpError:
                    raise DmcpError("instance_dead", "no connection to the emulator") from None
            self._next_id += 1
            request_id = self._next_id
            request = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
            try:
                self._file.write((json.dumps(request) + "\n").encode("utf-8"))
                line = self._read_line()
            except OSError as exc:
                self.close()
                raise DmcpError("instance_dead", f"connection to the emulator lost: {exc}") from exc
            except BaseException:
                # Interrupted between request and reply (Ctrl+C, timeout in a caller thread): the reply
                # would arrive later and be taken for the next call's result, so drop the connection.
                self.close()
                raise
            try:
                response = json.loads(line)
            except ValueError as exc:  # also UnicodeDecodeError
                self.close()
                raise DmcpError("internal", f"unreadable reply from the emulator: {exc}") from exc
            if not isinstance(response, dict) or response.get("id") != request_id:
                self.close()
                raise DmcpError("internal", f"reply does not belong to request {request_id}: {line[:200]!r}")
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
