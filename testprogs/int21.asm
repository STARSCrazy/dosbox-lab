; INT21.COM - Testprogramm fuer Ereignisse (exec/exit/int/DPMI-Dekodierung).
;  1. eigener INT-31h-Handler (Real Mode); INT 31h AX=0000 CX=1 (Antwort AX=00A7, CF=0),
;     INT 31h AX=0007 BX=00A7 CX:DX=0012:3456 (Antwort CF=0)
;  2. INT 21h 3Dh oeffnet "INT21.COM", 3Eh schliesst
;  3. INT 21h 4Ah verkleinert den eigenen Block, 4B00h startet "EXITCODE.COM" mit Befehlszeile " ARG1"
;  4. INT 21h 4Dh holt den Exit-Code des Kindes (2Ah), Ausgabe "INT21 fertig", Ende mit Kind-Code + 1 (2Bh)
bits 16
org 0x100
start:
    mov sp, stapel_ende
    mov ax, 0x2531              ; INT 31h -> int31 (DS = CS bei .COM)
    mov dx, int31
    int 0x21
    mov ax, 0x0000
    mov cx, 1
    int 0x31
    mov bx, ax                  ; 00A7
    mov ax, 0x0007
    mov cx, 0x0012
    mov dx, 0x3456
    int 0x31

    mov ax, 0x3D00
    mov dx, eigener_name
    int 0x21
    jc .ohne_datei
    mov bx, ax
    mov ah, 0x3E
    int 0x21
.ohne_datei:

    mov bx, (ende - start + 0x100 + 15) / 16
    mov ah, 0x4A                ; ES = PSP seit Programmstart
    int 0x21

    mov [parameter + 4], cs     ; Segmente im EXEC-Parameterblock
    mov [parameter + 8], cs
    mov [parameter + 12], cs
    mov [alter_sp], sp
    push cs
    pop es
    mov ax, 0x4B00
    mov dx, kind_name
    mov bx, parameter
    int 0x21
    cli
    mov ax, cs
    mov ss, ax
    mov sp, [cs:alter_sp]
    sti
    push cs
    pop ds

    mov ah, 0x4D
    int 0x21
    mov [ergebnis], al
    mov dx, text_fertig
    mov ah, 0x09
    int 0x21
    mov al, [ergebnis]
    inc al
    mov ah, 0x4C
    int 0x21

int31:                          ; minimaler "DPMI"-Handler: AX=0000 -> AX=00A7, immer CF=0
    cmp ax, 0x0000
    jne .weiter
    mov ax, 0x00A7
.weiter:
    push bp
    mov bp, sp
    and word [bp + 6], 0xFFFE   ; CF im gesicherten FLAGS loeschen
    pop bp
    iret

eigener_name db 'INT21.COM', 0
kind_name    db 'EXITCODE.COM', 0
befehlszeile db 5, ' ARG1', 13
fcb          times 16 db 0
parameter    dw 0               ; Umgebung des Elternprogramms
             dw befehlszeile, 0
             dw fcb, 0
             dw fcb, 0
text_fertig  db 'INT21 fertig', 13, 10, '$'
ergebnis     db 0
alter_sp     dw 0
align 2
stapel       times 256 db 0
stapel_ende:
ende:
