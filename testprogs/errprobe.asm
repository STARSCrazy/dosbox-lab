; ERRPROBE.COM - prueft, dass nach der Rueckkehr aus EXEC (Emulator liest den LE-Kopf) kein erweiterter Fehlercode (INT 21h 59h) im Gast zurueckbleibt; DOS setzt ihn beim Kindende auf 0 (src/dos/dos_execute.cpp:163).
;  1. INT 21h 3Dh oeffnet "NIXDA.XYZ" (fehlt) -> bekannter Fehler 2; Block verkleinern (4Ah)
;  2. EXEC (4B00h) von "LE.EXE" (LE-Kopf wird vom Emulator gelesen); danach INT 21h AX=5900h BX=0: AX -> PSP:00F0 (Wort)
;  3. wieder 3Dh auf "NIXDA.XYZ" (Fehler 2), EXEC von "EXITCODE.COM" (kein LE), 5900h: AX -> PSP:00F2 (Wort)
;  4. Marke 5Ah an PSP:00F4, Ausgabe "ERRPROBE fertig", Ende mit Errorlevel 0
; Erwartung (mit den Testprogrammen beobachtet): beide Woerter gleich; Wert siehe Test (test_31_le.py).
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

    call fehler_erzeugen
    mov dx, le_name
    call kind
    mov ax, 0x5900
    xor bx, bx
    int 0x21
    mov [0xF0], ax

    call fehler_erzeugen
    mov dx, com_name
    call kind
    mov ax, 0x5900
    xor bx, bx
    int 0x21
    mov [0xF2], ax

    mov byte [0xF4], 0x5A
    mov dx, text
    mov ah, 0x09
    int 0x21
    mov ax, 0x4C00
    int 0x21

fehler_erzeugen:
    mov ax, 0x3D00
    mov dx, fehlt_name
    int 0x21
    ret

kind:                           ; DX = Programmname
    mov [alter_sp], sp
    push cs
    pop es
    mov ax, 0x4B00
    mov bx, parameter
    int 0x21
    cli
    mov ax, cs
    mov ss, ax
    mov sp, [cs:alter_sp]
    sti
    push cs
    pop ds
    ret

fehlt_name   db 'NIXDA.XYZ', 0
le_name      db 'LE.EXE', 0
com_name     db 'EXITCODE.COM', 0
befehlszeile db 0, 13
fcb          times 16 db 0
parameter    dw 0
             dw befehlszeile, 0
             dw fcb, 0
             dw fcb, 0
text         db 'ERRPROBE fertig', 13, 10, '$'
alter_sp     dw 0
align 2
stapel       times 256 db 0
stapel_ende:
