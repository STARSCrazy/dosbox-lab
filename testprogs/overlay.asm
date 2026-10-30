; OVERLAY.COM - laedt EXITCODE.COM als Overlay (INT 21h AX=4B03h) und beendet sich mit Errorlevel 0.
; Parameterblock: Ladesegment 3000h (im freien Speicher, nicht der eigene Block), Relokationsfaktor 3000h.
; Das Overlay wird nicht ausgefuehrt. Ausgabe "OVERLAY fertig". Erwartetes Modul: EXITCODE.COM,
; art "overlay", load_seg und rel_basis 3000, paragraphen = (Dateigroesse + 15) / 16.
bits 16
org 0x100
start:
    mov ax, 0x4B03
    mov dx, name
    mov bx, parameter
    int 0x21
    mov dx, text
    mov ah, 0x09
    int 0x21
    mov ax, 0x4C00
    int 0x21
name      db 'EXITCODE.COM', 0
parameter dw 0x3000, 0x3000
text      db 'OVERLAY fertig', 13, 10, '$'
