; FUNCPM.COM - Ring-3-Funktion mit INT ueber ein Gate nach Ring 0 (Stackwechsel ueber das TSS) fuer func_run (D31).
; Wartet auf los (03F1), schaltet in 32-Bit-PM ohne Paging (Basis 0), springt per IRETD nach Ring 3 und ruft ring3fn.
;   0200 pm32 (Ring 0)   0280 ring3 (Ring 3, Stack 0x00080000)
;   02C0 ring3fn: push ebp, int 30h, pop ebp, ret
;   02E0 handler (Ring 0, Stack ab TSS-ESP0 0x00070000): pusht 3 Register, schreibt 0x11223344 nach linear
;        0x00500010, iretd
;   linear 0x00500000 = 'R3OK' nach dem Aufruf, danach Endlosschleife in Ring 3 (IF=0)
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
    xor ebx, ebx
    mov bx, cs
    shl ebx, 4              ; ebx = Basis des Programms (bleibt im PM erhalten)
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
    mov [sprungziel], eax
    lgdt [gdtr]
    lidt [idtr]
    mov eax, cr0
    or al, 1
    mov cr0, eax
    o32 jmp far [sprungziel]
sprungziel:
    dd 0
    dw 0x08
gdtr:
    dw gdt_ende - gdt - 1
    dd 0
idtr:
    dw idt_ende - idt - 1
    dd 0
    times 0x100 - ($ - $$) db 0xCC
bits 32
pm32:                        ; 0200 (keine absoluten Verweise auf eigene Marken)
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov esp, 0x00090000
    mov ax, 0x28
    ltr ax
    push dword 0x00000002
    popfd                    ; NT=0, sonst wird IRETD ein Taskwechsel
    push dword 0x23         ; SS Ring 3
    push dword 0x00080000    ; ESP Ring 3
    push dword 0x00000002    ; EFLAGS (IF=0)
    push dword 0x1B          ; CS Ring 3
    lea eax, [ebx + ring3]
    push eax
    iretd
    times 0x180 - ($ - $$) db 0xCC
ring3:                       ; 0280
    mov ax, 0x23
    mov ds, ax
    mov es, ax
    mov eax, 5
    call ring3fn             ; relativ
    mov dword [0x00500000], 'R3OK'
.schleife:
    jmp .schleife
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
los db 0                     ; 03F1
align 8
gdt:
    dq 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x9A,0xCF,0x00   ; 08 Code32 Ring 0, Basis 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x92,0xCF,0x00   ; 10 Daten32 Ring 0
    db 0xFF,0xFF,0x00,0x00,0x00,0xFA,0xCF,0x00   ; 18 Code32 Ring 3 (Selektor 1B)
    db 0xFF,0xFF,0x00,0x00,0x00,0xF2,0xCF,0x00   ; 20 Daten32 Ring 3 (Selektor 23)
gdt_tss:
    db 0x67,0x00,0x00,0x00,0x00,0x89,0x00,0x00   ; 28 TSS (32 Bit, frei), Basis setzt start
gdt_ende:
align 8
idt:
    times 0x30 dq 0
idt30:
    dw 0, 0x08               ; Offset 15..0 (setzt start), Selektor
    db 0, 0xEE               ; 32-Bit-Interrupt-Gate, DPL 3
    dw 0                     ; Offset 31..16
idt_ende:
tss:
    dd 0                     ; Rueckverweis
    dd 0x00070000            ; ESP0
    dd 0x10                  ; SS0
    times 104 - 12 db 0
