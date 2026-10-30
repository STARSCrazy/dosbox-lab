; DEBUG.COM - bekannter Ablauf fuer die Debug-Tests (Stufe 3).
; Offsets relativ zum PSP-Segment (linear = PSP*16 + Offset):
;   0100 mov dx,meldung   0103 mov ah,09   0105 int 21h (Text ausgeben)
;   0107 schleife: call unter (-> 0140)
;   010A mov ax,[wert]    010D inc ax      010E mov [wert],ax
;   0111 mov ah,01        0113 int 16h     0115 jz schleife
;   0117 mov ah,00        0119 int 16h     011B cmp al,27     011D jne schleife
;   011F mov ax,4C07      0122 int 21h (Ende, Errorlevel 7)
;   0140 unter: inc word [aufrufe]          0144 ret
;   0180 wert (Wort, +1 pro Runde)          0182 aufrufe (Wort, +1 pro Runde)
bits 16
org 0x100
start:
    mov dx, meldung             ; 0100
    mov ah, 0x09                ; 0103
    int 0x21                    ; 0105
schleife:
    call unter                  ; 0107
    mov ax, [wert]              ; 010A
    inc ax                      ; 010D
    mov [wert], ax              ; 010E
    mov ah, 0x01                ; 0111
    int 0x16                    ; 0113
    jz schleife                 ; 0115
    mov ah, 0x00                ; 0117
    int 0x16                    ; 0119
    cmp al, 27                  ; 011B
    jne schleife                ; 011D
    mov ax, 0x4C07              ; 011F
    int 0x21                    ; 0122
    times 0x40 - ($ - $$) db 0xCC
unter:
    inc word [aufrufe]          ; 0140
    ret                         ; 0144
    times 0x80 - ($ - $$) db 0
wert     dw 0                   ; 0180
aufrufe  dw 0                   ; 0182
meldung  db 'DEBUG laeuft (ESC beendet)', 13, 10, '$'
