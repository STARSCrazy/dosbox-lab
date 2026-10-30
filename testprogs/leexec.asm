; AUSGABE=EXE
; LEEXEC.EXE - wie LE.EXE, startet aber vor den Anforderungen EXITCODE.COM (INT 21h 4B00): zuordnung "aufgegeben" (anderes Programm).
%define LE_EXEC
%include "le.asm"
