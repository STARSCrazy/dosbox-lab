; SPDX-License-Identifier: GPL-2.0-or-later
; OUTPUT=EXE
; LEBAD.EXE - like LE.EXE, but object count 0 in the LE header (no usable LE).
%define LE_OBJECTS 0
%include "le.asm"
