# SPDX-License-Identifier: GPL-2.0-or-later
class DmcpError(Exception):
    """Error from the emulator or the Python layer. `code` is machine-readable."""

    def __init__(self, code, message):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
