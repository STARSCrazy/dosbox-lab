# SPDX-License-Identifier: GPL-2.0-or-later
class DmcpError(Exception):
    """Fehler aus dem Emulator oder der Python-Schicht. `code` ist maschinenlesbar."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
