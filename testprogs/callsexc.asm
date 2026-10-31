; CALLSEXC.COM - exception contexts without their own IRET for the call log.
; Offsets relative to the PSP segment (linear = PSP*16 + offset). Waits until the test sets go (03F1) to 1.
;   1. div 0 -> handler (context 1); it raises div 0 again (context 2); the second entry resets SP to
;      the first frame and returns by IRET to the first fault (context 2 ends without its own IRET).
;   2. call target (0200) in the main context.
;   3. div 0 -> handler restores the old INT 0 vector and exits via INT 21h 4C00h (no IRET).
;   0200 target (inc ax), 0300 old vector (dd), 0304 saved_sp (dw), 0306 counter (db), 03F1 go
bits 16
org 0x100
start:
    mov ax, 0x3500
    int 0x21                 ; ES:BX = old INT 0 vector
    mov [old_vector], bx
    mov [old_vector + 2], es
    mov ax, 0x2500
    mov dx, handler
    int 0x21
.wait:
    cmp byte [go], 0
    je .wait
    xor bl, bl
    mov ax, 10
    div bl                   ; fault 1, the handler returns here with bl = 1
    call target
    xor bl, bl
    mov ax, 10
    div bl                   ; fault 3: program end in the handler
    mov ax, 0x4C01           ; never reached
    int 0x21
handler:
    inc byte [counter]
    cmp byte [counter], 1
    je .first
    cmp byte [counter], 2
    je .second
    push ds
    lds dx, [old_vector]
    mov ax, 0x2500
    int 0x21
    pop ds
    mov ax, 0x4C00
    int 0x21
.first:
    mov [saved_sp], sp
    mov ax, 10
    div bl                   ; bl still 0: fault 2
.second:
    mov sp, [saved_sp]       ; discard the frame of fault 2
    mov bl, 1
    mov ax, 10
    iret                     ; to the frame of fault 1
    times 0x100 - ($ - $$) db 0xCC
target:                        ; 0200
    inc ax
    ret
    times 0x200 - ($ - $$) db 0
old_vector dd 0              ; 0300
saved_sp   dw 0              ; 0304
counter    db 0              ; 0306
    times 0x2F1 - ($ - $$) db 0
go         db 0              ; 03F1
