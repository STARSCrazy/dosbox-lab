; FLOW.COM - known indirect jumps/calls for coverage and control-flow tests.
; Offsets relative to the PSP segment (linear = PSP*16 + offset):
;   0100 start          0107 call [bx] -> 0160      0109 call [bx+2] -> 0168
;   010F call ax -> 0160                            0111 jmp [jump_ptr] -> 0140
;   0115..012F, 0130 never executed
;   0140 call far [far_ptr] -> CS:0170              0144 mov byte [done], 1
;   014E int 21h (AH=09h "FLOW done")               0150 sti, 0151 hlt, 0152 jmp 0151
;   0160 target_a: ret    0168 target_b: ret    0170 target_far: retf
;   0180 table (data)                            01F0 done (byte, 1 after all jumps)
bits 16
org 0x100
start:
    mov [far_ptr + 2], cs       ; 0100 (4 bytes)
    mov bx, table               ; 0104 (3 bytes)
    call [bx]                   ; 0107 (2 bytes)
    call [bx + 2]               ; 0109 (3 bytes)
    mov ax, target_a            ; 010C (3 bytes)
    call ax                     ; 010F (2 bytes)
    jmp [jump_ptr]              ; 0111 (4 bytes)
    times 0x30 - ($ - $$) db 0xCC
never:
    mov byte [done], 0xEE       ; 0130: must never run
    times 0x40 - ($ - $$) db 0xCC
resume:
    call far [far_ptr]          ; 0140 (4 bytes)
    mov byte [done], 1          ; 0144 (5 bytes)
    mov dx, text_done           ; 0149 (3 bytes)
    mov ah, 0x09                ; 014C (2 bytes)
    int 0x21                    ; 014E (2 bytes)
    sti                         ; 0150
.wait:
    hlt                         ; 0151
    jmp .wait                   ; 0152
    times 0x60 - ($ - $$) db 0xCC
target_a:
    ret                         ; 0160
    times 0x68 - ($ - $$) db 0xCC
target_b:
    ret                         ; 0168
    times 0x70 - ($ - $$) db 0xCC
target_far:
    retf                        ; 0170
    times 0x80 - ($ - $$) db 0
table  dw target_a, target_b    ; 0180
jump_ptr dw resume              ; 0184
far_ptr  dw target_far, 0       ; 0186
text_done db 'FLOW done', 13, 10, '$'
    times 0xF0 - ($ - $$) db 0
done   db 0                     ; 01F0
