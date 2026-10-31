; CALLS.COM - known calls for the call log. Offsets relative to the PSP segment
; (linear = PSP*16 + offset). Waits until the test sets go (03F1) to 1.
;   0200 inner (inc ax)        0210 outer (2x inner)      0220 recurse (recursion via cx)
;   0230 far_fn (retf, ax=1234)  0240 with_param (ret 4)    0250 often (called 5x)
;   0260 escape_outer         0270 escape_inner (reset the stack, ret past the frame)
;   0280 hang (hlt loop, never returns)
;   0300 far_target (dd), 0304 saved_sp (dw), 03F0 done (1 before hang), 03F1 go
bits 16
org 0x100
start:
    mov [far_target + 2], cs
.wait:
    cmp byte [go], 0
    je .wait
    mov ax, 5
    call outer              ; inner 2x: ax 5 -> 6 -> 7
    mov cx, 3
    call recurse             ; recursion, depth 3
    call far [far_target]      ; ax = 1234h
    push word 0x00AA
    push word 0x00BB
    call with_param          ; ax = 0165h, ret 4
    mov cx, 5
.often:
    push cx
    call often
    pop cx
    loop .often
    call escape_outer        ; escape_inner leaves by resetting the stack
    mov ah, 0x30
    int 0x21                 ; DOS version
    mov byte [done], 1
    call hang
    times 0x100 - ($ - $$) db 0xCC
inner:                       ; 0200
    inc ax
    ret
    times 0x110 - ($ - $$) db 0xCC
outer:                       ; 0210
    call inner
    call inner
    ret
    times 0x120 - ($ - $$) db 0xCC
recurse:                     ; 0220
    dec cx
    jz .end
    call recurse
.end:
    ret
    times 0x130 - ($ - $$) db 0xCC
far_fn:                      ; 0230
    mov ax, 0x1234
    retf
    times 0x140 - ($ - $$) db 0xCC
with_param:                  ; 0240
    push bp
    mov bp, sp
    mov ax, [bp + 4]
    add ax, [bp + 6]
    pop bp
    ret 4
    times 0x150 - ($ - $$) db 0xCC
often:                       ; 0250
    ret
    times 0x160 - ($ - $$) db 0xCC
escape_outer:                ; 0260
    mov [saved_sp], sp
    call escape_inner
    mov byte [done], 0xEE  ; never reached
    ret
    times 0x170 - ($ - $$) db 0xCC
escape_inner:                ; 0270
    mov sp, [saved_sp]
    ret                      ; returns directly to the caller of escape_outer
    times 0x180 - ($ - $$) db 0xCC
hang:                        ; 0280
    sti
.w:
    hlt
    jmp .w
    times 0x200 - ($ - $$) db 0
far_target dw far_fn, 0          ; 0300
saved_sp  dw 0                ; 0304
    times 0x2F0 - ($ - $$) db 0
done   db 0                ; 03F0
go       db 0                ; 03F1
