; SPDX-License-Identifier: GPL-2.0-or-later
; OUTPUT=EXE
; LEVIELE.EXE - like LE.EXE, but without argument 257 requests INT 31h AX=0501h (0x100 bytes each, never fitting): assignment "abandoned".
%define LE_VIELE
%include "le.asm"
