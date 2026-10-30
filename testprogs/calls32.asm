; CALLS32.COM - ein 32-Bit-Aufruf im Protected Mode (D29). Wartet auf los (03F1), schaltet dann in 32-Bit-PM
; ohne Paging (Code-Selektor 08, Basis 0) und ruft add32 mit einem Stack-Parameter.
;   0200 pm32 (32-Bit-Code)   0280 add32: eax = [esp+4] + 1, ret 4
;   linear 0x00500000 = 'C32!' nach dem Aufruf, danach Endlosschleife (IF=0)
bits 16
org 0x100
start:
.warte:
    cmp byte [los], 0
    je .warte
    cli
    in al, 0x92
    or al, 2
    out 0x92, al            ; A20 an
    xor eax, eax
    mov ax, cs
    shl eax, 4
    mov ebx, eax
    add eax, gdt
    mov [gdtr + 2], eax
    mov eax, ebx
    add eax, pm32
    mov [sprungziel], eax
    lgdt [gdtr]
    mov eax, cr0
    or al, 1
    mov cr0, eax
    o32 jmp far [sprungziel]
sprungziel:
    dd 0
    dw 0x08
align 8
gdt:
    dq 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x9A,0xCF,0x00   ; 08 Code32, Basis 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x92,0xCF,0x00   ; 10 Daten32, Basis 0
gdt_ende:
gdtr:
    dw gdt_ende - gdt - 1
    dd 0
    times 0x100 - ($ - $$) db 0xCC
bits 32
pm32:                        ; 0200 (keine absoluten Verweise auf eigene Marken)
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov esp, 0x00090000
    push dword 0x11223344
    call add32               ; relativ
    mov dword [0x00500000], 0x21323343   ; 'C32!'
.schleife:
    jmp .schleife
    times 0x180 - ($ - $$) db 0xCC
add32:                       ; 0280
    mov eax, [esp + 4]
    inc eax
    ret 4
    times 0x2F1 - ($ - $$) db 0
los db 0                     ; 03F1
