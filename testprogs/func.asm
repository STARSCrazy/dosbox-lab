; FUNC.COM - Funktionen fuer func_run (D31). Offsets relativ zum PSP-Segment (linear = PSP*16 + Offset).
; Wartet, bis der Test los (03F1) auf 1 setzt, ruft dann jede Funktion einmal und haelt in einer HLT-Schleife.
;   0200 summe:  ax = ax + bx, [ergebnis] = ax, lokale Variablen auf dem Stack, ret
;   0220 ruft:   inc [zaehler], 2x summe (ax = ax + 2*bx), ret
;   0240 mitint: INT 21h AH=30h (DOS-Version), ret
;   0280 tick:   wartet auf den naechsten Timer-Tick (0040:006C), ret
;   02A0 kind:   startet EXITCODE.COM (INT 21h 4B00h), ax = 1234h, ret
;   02E0 hma:    A20 an, schreibt BEEFh nach FFFF:FF00 (linear 0x10FEF0), ret
;   0300 ergebnis (dw), 0302 zaehler (dw), 0304 alter_sp (dw), 0310 EXEC-Parameterblock, 0320 Kindname,
;   0330 Befehlszeile, 0340 FCB-Flaeche, 03F0 fertig (1 nach allen Aufrufen), 03F1 los
bits 16
org 0x100
start:
    mov bx, 0x1000           ; eigenen Block auf 64 KB verkleinern (Platz fuer das Kind)
    mov ah, 0x4A             ; ES = PSP seit Programmstart
    int 0x21
    mov [parameter + 4], cs  ; Segmente im EXEC-Parameterblock
    mov [parameter + 8], cs
    mov [parameter + 12], cs
.warte:
    cmp byte [los], 0
    je .warte
    mov ax, 3
    mov bx, 4
    call summe               ; ax = 7, ergebnis = 7
    mov ax, 1
    mov bx, 2
    call ruft                ; ax = 5, ergebnis = 5, zaehler = 1
    call mitint
    call tick
    call kind
    call hma
    mov byte [fertig], 1
.h:
    sti
    hlt
    jmp .h
    times 0x100 - ($ - $$) db 0xCC
summe:                       ; 0200
    push bp
    mov bp, sp
    sub sp, 8
    mov [bp - 2], ax
    mov [bp - 4], bx
    add ax, bx
    mov [ergebnis], ax
    mov sp, bp
    pop bp
    ret
    times 0x120 - ($ - $$) db 0xCC
ruft:                        ; 0220
    inc word [zaehler]
    call summe
    call summe
    ret
    times 0x140 - ($ - $$) db 0xCC
mitint:                      ; 0240
    mov ah, 0x30
    int 0x21
    ret
    times 0x180 - ($ - $$) db 0xCC
tick:                        ; 0280
    push ds
    mov dx, 0x40
    mov ds, dx
    mov dx, [0x6C]
.t:
    cmp dx, [0x6C]
    je .t
    pop ds
    ret
    times 0x1A0 - ($ - $$) db 0xCC
kind:                        ; 02A0
    mov [alter_sp], sp
    push cs
    pop es
    mov bx, parameter
    mov dx, kind_name
    mov ax, 0x4B00
    int 0x21
    cli
    mov ax, cs
    mov ss, ax
    mov sp, [cs:alter_sp]
    sti
    push cs
    pop ds
    mov ax, 0x1234
    ret
    times 0x1E0 - ($ - $$) db 0xCC
hma:                         ; 02E0
    in al, 0x92
    or al, 2
    out 0x92, al             ; A20 an
    push ds
    mov ax, 0xFFFF
    mov ds, ax
    mov word [0xFF00], 0xBEEF ; linear 0x10FEF0 (HMA)
    pop ds
    ret
    times 0x200 - ($ - $$) db 0
ergebnis dw 0                ; 0300
zaehler  dw 0                ; 0302
alter_sp dw 0                ; 0304
    times 0x210 - ($ - $$) db 0
parameter dw 0               ; 0310 Umgebung des Elternprogramms
          dw befehlszeile, 0 ; Befehlszeile (Segment setzt start)
          dw fcb, 0          ; FCB 1
          dw fcb, 0          ; FCB 2
    times 0x220 - ($ - $$) db 0
kind_name db 'EXITCODE.COM', 0 ; 0320
    times 0x230 - ($ - $$) db 0
befehlszeile db 0, 0x0D      ; 0330
    times 0x240 - ($ - $$) db 0
fcb times 37 db 0            ; 0340
    times 0x2F0 - ($ - $$) db 0
fertig   db 0                ; 03F0
los      db 0                ; 03F1
