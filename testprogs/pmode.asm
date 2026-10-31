; SPDX-License-Identifier: GPL-2.0-or-later
; PMODE.COM - switches to 32-bit protected mode with paging and stays there (IF=0).
;   GDT: 08 code32 flat, 10 data32 flat, 18 data32 with base 0x01000000
;   Paging: 0..4 MB identity; linear 0x01000000..0x013FFFFF -> physical 0x00200000..
;   page directory at physical 0x00300000, tables at 0x00301000 and 0x00302000
;   linear 0x01000000 = 'DMCP', 0x01000004 = counter (counting up)
bits 16
org 0x100
start:
    cli
    in al, 0x92
    or al, 2
    out 0x92, al            ; A20 on
    xor eax, eax
    mov ax, cs
    shl eax, 4              ; linear base of this program
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
    db 0xFF,0xFF,0x00,0x00,0x00,0x9A,0xCF,0x00   ; 08 code32, base 0, 4 GB
    db 0xFF,0xFF,0x00,0x00,0x00,0x92,0xCF,0x00   ; 10 data32, base 0, 4 GB
    db 0xFF,0xFF,0x00,0x00,0x00,0x92,0xCF,0x01   ; 18 data32, base 0x01000000
gdt_end:
gdtr:
    dw gdt_end - gdt - 1
    dd 0

; From here on the code runs with CS=08 (base 0) at linear address base+pm32.
; It must therefore not contain absolute references to its own labels.
bits 32
pm32:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov esp, 0x00090000
    cld
    mov edi, 0x00300000     ; zero the directory + two tables
    xor eax, eax
    mov ecx, 3 * 1024
    rep stosd
    mov edi, 0x00301000     ; table 0: 0..4 MB identity
    mov eax, 0x00000003
    mov ecx, 1024
.t0:
    stosd
    add eax, 0x1000
    loop .t0
    mov edi, 0x00302000     ; table 1: linear 0x01000000.. -> physical 0x00200000..
    mov eax, 0x00200003
    mov ecx, 1024
.t1:
    stosd
    add eax, 0x1000
    loop .t1
    mov dword [0x00300000], 0x00301003         ; PDE 0
    mov dword [0x00300000 + 4 * 4], 0x00302003 ; PDE 4 (linear 0x01000000)
    mov eax, 0x00300000
    mov cr3, eax
    mov eax, cr0
    or eax, 0x80000000
    mov cr0, eax
    jmp .paging_on
.paging_on:
    mov dword [0x01000000], 0x50434D44         ; 'DMCP'
    mov dword [0x01000004], 0
.loop:
    inc dword [0x01000004]
    jmp .loop
