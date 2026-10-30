; LOADONLY.COM - laedt EXITCODE.COM ohne Start (INT 21h AX=4B01h), danach Ende mit Errorlevel 0.
; Vorher verkleinert INT 21h 4Ah den eigenen Block auf 1000h Paragraphen. Das Kind wird nie ausgefuehrt
; (kein Ausgabetext von EXITCODE); LOADONLY selbst gibt nichts aus.
bits 16
org 0x100
start:
    mov sp, stapel_ende
    mov bx, 0x1000
    mov ah, 0x4A                ; ES = PSP seit Programmstart
    int 0x21
    mov [parameter + 4], cs
    mov [parameter + 8], cs
    mov [parameter + 12], cs
    push cs
    pop es
    mov ax, 0x4B01
    mov dx, name
    mov bx, parameter
    int 0x21
    mov ax, 0x4C00
    int 0x21
name         db 'EXITCODE.COM', 0
befehlszeile db 0, 13
fcb          times 16 db 0
parameter    dw 0               ; Umgebung des Elternprogramms
             dw befehlszeile, 0
             dw fcb, 0
             dw fcb, 0
             dw 0, 0, 0, 0      ; Rueckgabe SS:SP, CS:IP (4B01h)
stapel       times 128 db 0
stapel_ende:
