; FILEIO.COM - bekannte Dateizugriffe fuer D34. Wartet auf los (03F1). Offsets relativ zum PSP-Segment.
;   legt FIO.DAT an und schreibt 64 Byte 00..3F, schliesst; oeffnet, setzt Position 10h, liest 16 Byte ueber
;   lesen (0280) nach puffer (0400), liest 8 Byte direkt nach 0410, schliesst, schreibt "FIO" an CON (Handle 1,
;   kein datei-Ereignis), fertig=1 (03F0), dann hlt-Schleife.
bits 16
org 0x100
start:
.warte:
    cmp byte [los], 0
    je .warte
    mov di, muster
    xor al, al
    mov cx, 64
.m:
    stosb
    inc al
    loop .m
    mov ah, 0x3C
    xor cx, cx
    mov dx, name
    int 0x21                 ; anlegen
    mov bx, ax
    mov ah, 0x40
    mov cx, 64
    mov dx, muster
    int 0x21                 ; 64 Byte schreiben ab Offset 0
    mov ah, 0x3E
    int 0x21
    mov ax, 0x3D00
    mov dx, name
    int 0x21                 ; lesend oeffnen
    mov bx, ax
    mov ax, 0x4200
    xor cx, cx
    mov dx, 0x10
    int 0x21                 ; Position 10h
    mov cx, 16
    mov dx, puffer
    call lesen               ; 10h..1Fh
    mov ah, 0x3F
    mov cx, 8
    mov dx, puffer + 16
    int 0x21                 ; 20h..27h
    mov ah, 0x3E
    int 0x21
    mov ah, 0x40
    mov bx, 1
    mov cx, 3
    mov dx, text
    int 0x21                 ; an CON
    mov byte [fertig], 1
    sti
.w:
    hlt
    jmp .w
    times 0x180 - ($ - $$) db 0xCC
lesen:                       ; 0280
    mov ah, 0x3F
    int 0x21
    ret
    times 0x200 - ($ - $$) db 0
name   db 'FIO.DAT', 0       ; 0300
text   db 'FIO'
    times 0x2F0 - ($ - $$) db 0
fertig db 0                  ; 03F0
los    db 0                  ; 03F1
    times 0x300 - ($ - $$) db 0
puffer:                      ; 0400
    times 32 db 0
muster:                      ; 0420
    times 64 db 0
