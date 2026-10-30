; AUSGABE=EXE
; LEEIP.EXE - wie LE.EXE, aber EIP 0x3000 im LE-Kopf (liegt ausserhalb von Objekt 1, Groesse 0x3000): zuordnung "kein_le".
%define LE_EIP 0x3000
%include "le.asm"
