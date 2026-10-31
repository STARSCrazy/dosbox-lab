; CALLS32.COM - a 32-bit call in protected mode. Waits for go (03F1), then switches to 32-bit PM
; without paging (code selector 08, base 0) and calls add32 with one stack parameter.
;   0200 pm32 (32-bit code)   0280 add32: eax = [esp+4] + 1, ret 4
;   linear 0x00500000 = 'C32!' after the call, then an endless loop (IF=0)
bits 16
org 0x100
start:
.wait:
    cmp byte [go], 0
    je .wait
    cli
    in al, 0x92
    or al, 2
    out 0x92, al            ; A20 on
    xor eax, eax
    mov ax, cs
    shl eax, 4
    mov ebx, eax
    add eax, gdt
    mov [gdtr + 2], eax
    mov eax, ebx
    add eax, pm32
    mov [jump_target], eax
    lgdt [gdtr]
    mov eax, cr0
    or al, 1
    mov cr0, eax
    o32 jmp far [jump_target]
jump_target:
    dd 0
    dw 0x08
align 8
gdt:
    dq 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x9A,0xCF,0x00   ; 08 code32, base 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x92,0xCF,0x00   ; 10 data32, base 0
gdt_end:
gdtr:
    dw gdt_end - gdt - 1
    dd 0
    times 0x100 - ($ - $$) db 0xCC
bits 32
pm32:                        ; 0200 (no absolute references to own labels)
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov esp, 0x00090000
    push dword 0x11223344
    call add32               ; relative
    mov dword [0x00500000], 0x21323343   ; 'C32!'
.loop:
    jmp .loop
    times 0x180 - ($ - $$) db 0xCC
add32:                       ; 0280
    mov eax, [esp + 4]
    inc eax
    ret 4
    times 0x2F1 - ($ - $$) db 0
go db 0                      ; 03F1
