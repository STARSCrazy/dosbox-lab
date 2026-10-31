; SPDX-License-Identifier: GPL-2.0-or-later
; OUTPUT=EXE
; LEEXEC.EXE - like LE.EXE, but starts EXITCODE.COM (INT 21h 4B00) before the requests: assignment "abandoned" (another program).
%define LE_EXEC
%include "le.asm"
