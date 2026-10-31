; SPDX-License-Identifier: GPL-2.0-or-later
; DEBUG.COM - known flow for the debug tests.
; Offsets relative to the PSP segment (linear = PSP*16 + offset):
;   0100 mov dx,message   0103 mov ah,09   0105 int 21h (print text)
;   0107 main_loop: call under (-> 0140)
;   010A mov ax,[value]    010D inc ax      010E mov [value],ax
;   0111 mov ah,01        0113 int 16h     0115 jz main_loop
;   0117 mov ah,00        0119 int 16h     011B cmp al,27     011D jne main_loop
;   011F mov ax,4C07      0122 int 21h (exit, errorlevel 7)
;   0140 under: inc word [calls]          0144 ret
;   0180 value (word, +1 per round)          0182 calls (word, +1 per round)
bits 16
org 0x100
start:
    mov dx, message             ; 0100
    mov ah, 0x09                ; 0103
    int 0x21                    ; 0105
main_loop:
    call under                  ; 0107
    mov ax, [value]             ; 010A
    inc ax                      ; 010D
    mov [value], ax             ; 010E
    mov ah, 0x01                ; 0111
    int 0x16                    ; 0113
    jz main_loop                ; 0115
    mov ah, 0x00                ; 0117
    int 0x16                    ; 0119
    cmp al, 27                  ; 011B
    jne main_loop               ; 011D
    mov ax, 0x4C07              ; 011F
    int 0x21                    ; 0122
    times 0x40 - ($ - $$) db 0xCC
under:
    inc word [calls]            ; 0140
    ret                         ; 0144
    times 0x80 - ($ - $$) db 0
value     dw 0                  ; 0180
calls     dw 0                  ; 0182
message   db 'DEBUG running (ESC exits)', 13, 10, '$'
