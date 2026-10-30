; FLOW.COM - bekannte indirekte Spruenge/Aufrufe fuer Coverage- und Kontrollfluss-Tests.
; Offsets relativ zum PSP-Segment (linear = PSP*16 + Offset):
;   0100 start          0107 call [bx] -> 0160      0109 call [bx+2] -> 0168
;   010F call ax -> 0160                            0111 jmp [sprung] -> 0140
;   0115..012F, 0130 nie ausgefuehrt
;   0140 call far [fernziel] -> CS:0170             0144 mov byte [fertig], 1
;   014E int 21h (AH=09h "FLOW fertig")             0150 sti, 0151 hlt, 0152 jmp 0151
;   0160 ziel_a: ret    0168 ziel_b: ret    0170 ziel_far: retf
;   0180 tabelle (Daten)                            01F0 fertig (Byte, 1 nach allen Spruengen)
bits 16
org 0x100
start:
    mov [fernziel + 2], cs      ; 0100 (4 Byte)
    mov bx, tabelle             ; 0104 (3 Byte)
    call [bx]                   ; 0107 (2 Byte)
    call [bx + 2]               ; 0109 (3 Byte)
    mov ax, ziel_a              ; 010C (3 Byte)
    call ax                     ; 010F (2 Byte)
    jmp [sprung]                ; 0111 (4 Byte)
    times 0x30 - ($ - $$) db 0xCC
nie:
    mov byte [fertig], 0xEE     ; 0130: darf nie laufen
    times 0x40 - ($ - $$) db 0xCC
weiter:
    call far [fernziel]         ; 0140 (4 Byte)
    mov byte [fertig], 1        ; 0144 (5 Byte)
    mov dx, text_fertig         ; 0149 (3 Byte)
    mov ah, 0x09                ; 014C (2 Byte)
    int 0x21                    ; 014E (2 Byte)
    sti                         ; 0150
.warte:
    hlt                         ; 0151
    jmp .warte                  ; 0152
    times 0x60 - ($ - $$) db 0xCC
ziel_a:
    ret                         ; 0160
    times 0x68 - ($ - $$) db 0xCC
ziel_b:
    ret                         ; 0168
    times 0x70 - ($ - $$) db 0xCC
ziel_far:
    retf                        ; 0170
    times 0x80 - ($ - $$) db 0
tabelle  dw ziel_a, ziel_b      ; 0180
sprung   dw weiter              ; 0184
fernziel dw ziel_far, 0         ; 0186
text_fertig db 'FLOW fertig', 13, 10, '$'
    times 0xF0 - ($ - $$) db 0
fertig   db 0                   ; 01F0
