; FUNCPM.COM - ring 3 function with INT through a gate to ring 0 (stack switch via the TSS) for func_run.
; Waits for go (03F1), switches to 32-bit PM without paging (base 0), jumps by IRETD to ring 3 and calls ring3fn.
;   0200 pm32 (ring 0)   0280 ring3 (ring 3, stack 0x00080000)
;   02C0 ring3fn: push ebp, int 30h, pop ebp, ret
;   02E0 handler (ring 0, stack from TSS ESP0 0x00070000): pushes 3 registers, writes 0x11223344 to linear
;        0x00500010, iretd
;   linear 0x00500000 = 'R3OK' after the call, then an endless loop in ring 3 (IF=0)
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
    xor ebx, ebx
    mov bx, cs
    shl ebx, 4              ; ebx = base of the program (preserved in PM)
    mov eax, ebx
    add eax, gdt
    mov [gdtr + 2], eax
    mov eax, ebx
    add eax, idt
    mov [idtr + 2], eax
    mov eax, ebx
    add eax, tss
    mov [gdt_tss + 2], ax
    shr eax, 16
    mov [gdt_tss + 4], al
    mov [gdt_tss + 7], ah
    mov eax, ebx
    add eax, handler
    mov [idt30], ax
    shr eax, 16
    mov [idt30 + 6], ax
    mov eax, ebx
    add eax, pm32
    mov [jump_target], eax
    lgdt [gdtr]
    lidt [idtr]
    mov eax, cr0
    or al, 1
    mov cr0, eax
    o32 jmp far [jump_target]
jump_target:
    dd 0
    dw 0x08
gdtr:
    dw gdt_end - gdt - 1
    dd 0
idtr:
    dw idt_end - idt - 1
    dd 0
    times 0x100 - ($ - $$) db 0xCC
bits 32
pm32:                        ; 0200 (no absolute references to own labels)
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov esp, 0x00090000
    mov ax, 0x28
    ltr ax
    push dword 0x00000002
    popfd                    ; NT=0, otherwise IRETD becomes a task switch
    push dword 0x23         ; SS ring 3
    push dword 0x00080000    ; ESP ring 3
    push dword 0x00000002    ; EFLAGS (IF=0)
    push dword 0x1B          ; CS ring 3
    lea eax, [ebx + ring3]
    push eax
    iretd
    times 0x180 - ($ - $$) db 0xCC
ring3:                       ; 0280
    mov ax, 0x23
    mov ds, ax
    mov es, ax
    mov eax, 5
    call ring3fn             ; relative
    mov dword [0x00500000], 'R3OK'
.loop:
    jmp .loop
    times 0x1C0 - ($ - $$) db 0xCC
ring3fn:                     ; 02C0
    push ebp
    mov ebp, 0x1234
    int 0x30
    pop ebp
    ret
    times 0x1E0 - ($ - $$) db 0xCC
handler:                     ; 02E0
    push eax
    push ebx
    push ecx
    mov dword [0x00500010], 0x11223344
    pop ecx
    pop ebx
    pop eax
    iretd
    times 0x2F1 - ($ - $$) db 0
go db 0                      ; 03F1
align 8
gdt:
    dq 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x9A,0xCF,0x00   ; 08 code32 ring 0, base 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x92,0xCF,0x00   ; 10 data32 ring 0
    db 0xFF,0xFF,0x00,0x00,0x00,0xFA,0xCF,0x00   ; 18 code32 ring 3 (selector 1B)
    db 0xFF,0xFF,0x00,0x00,0x00,0xF2,0xCF,0x00   ; 20 data32 ring 3 (selector 23)
gdt_tss:
    db 0x67,0x00,0x00,0x00,0x00,0x89,0x00,0x00   ; 28 TSS (32 bit, free), start sets the base
gdt_end:
align 8
idt:
    times 0x30 dq 0
idt30:
    dw 0, 0x08               ; offset 15..0 (start sets it), selector
    db 0, 0xEE               ; 32-bit interrupt gate, DPL 3
    dw 0                     ; offset 31..16
idt_end:
tss:
    dd 0                     ; back link
    dd 0x00070000            ; ESP0
    dd 0x10                  ; SS0
    times 104 - 12 db 0
