; OUTPUT=EXE
; LEEIP.EXE - like LE.EXE, but EIP 0x3000 in the LE header (outside object 1, size 0x3000): assignment "not_le".
%define LE_EIP 0x3000
%include "le.asm"
