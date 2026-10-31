; FUNC.COM - functions for func_run (D31). Offsets relative to the PSP segment (linear = PSP*16 + offset).
; Waits until the test sets go (03F1) to 1, then calls each function once and stops in an HLT loop.
;   0200 sum:  ax = ax + bx, [result] = ax, local variables on the stack, ret
;   0220 calls:  inc [counter], 2x sum (ax = ax + 2*bx), ret
;   0240 with_int: INT 21h AH=30h (DOS version), ret
;   0280 tick:   waits for the next timer tick (0040:006C), ret
;   02A0 child:  starts EXITCODE.COM (INT 21h 4B00h), ax = 1234h, ret
;   02E0 hma:    A20 on, writes BEEFh to FFFF:FF00 (linear 0x10FEF0), ret
;   0300 result (dw), 0302 counter (dw), 0304 old_sp (dw), 0310 EXEC parameter block, 0320 child name,
;   0330 command_line, 0340 FCB area, 03F0 done (1 after all calls), 03F1 go
bits 16
org 0x100
start:
    mov bx, 0x1000           ; shrink own block to 64 KB (room for the child)
    mov ah, 0x4A             ; ES = PSP since program start
    int 0x21
    mov [parameter + 4], cs  ; segments in the EXEC parameter block
    mov [parameter + 8], cs
    mov [parameter + 12], cs
.wait:
    cmp byte [go], 0
    je .wait
    mov ax, 3
    mov bx, 4
    call sum               ; ax = 7, result = 7
    mov ax, 1
    mov bx, 2
    call calls               ; ax = 5, result = 5, counter = 1
    call with_int
    call tick
    call child
    call hma
    mov byte [done], 1
.h:
    sti
    hlt
    jmp .h
    times 0x100 - ($ - $$) db 0xCC
sum:                       ; 0200
    push bp
    mov bp, sp
    sub sp, 8
    mov [bp - 2], ax
    mov [bp - 4], bx
    add ax, bx
    mov [result], ax
    mov sp, bp
    pop bp
    ret
    times 0x120 - ($ - $$) db 0xCC
calls:                       ; 0220
    inc word [counter]
    call sum
    call sum
    ret
    times 0x140 - ($ - $$) db 0xCC
with_int:                    ; 0240
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
child:                       ; 02A0
    mov [old_sp], sp
    push cs
    pop es
    mov bx, parameter
    mov dx, child_name
    mov ax, 0x4B00
    int 0x21
    cli
    mov ax, cs
    mov ss, ax
    mov sp, [cs:old_sp]
    sti
    push cs
    pop ds
    mov ax, 0x1234
    ret
    times 0x1E0 - ($ - $$) db 0xCC
hma:                         ; 02E0
    in al, 0x92
    or al, 2
    out 0x92, al             ; A20 on
    push ds
    mov ax, 0xFFFF
    mov ds, ax
    mov word [0xFF00], 0xBEEF ; linear 0x10FEF0 (HMA)
    pop ds
    ret
    times 0x200 - ($ - $$) db 0
result dw 0                ; 0300
counter  dw 0                ; 0302
old_sp   dw 0                ; 0304
    times 0x210 - ($ - $$) db 0
parameter dw 0               ; 0310 environment of the parent program
          dw command_line, 0 ; command line (start sets the segment)
          dw fcb, 0          ; FCB 1
          dw fcb, 0          ; FCB 2
    times 0x220 - ($ - $$) db 0
child_name db 'EXITCODE.COM', 0 ; 0320
    times 0x230 - ($ - $$) db 0
command_line db 0, 0x0D      ; 0330
    times 0x240 - ($ - $$) db 0
fcb times 37 db 0            ; 0340
    times 0x2F0 - ($ - $$) db 0
done   db 0                ; 03F0
go       db 0                ; 03F1
