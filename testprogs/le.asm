; SPDX-License-Identifier: GPL-2.0-or-later
; OUTPUT=EXE
; LE.EXE - test program for the LE base derivation.
; MZ program with an appended LE header (dword at 0x3C): 2 objects
;   obj1 va 0x10000 size 0x3000, obj2 va 0x20000 size 0x0A10; EIP object 1, EIP 0x100 (entry_va 0x10100).
; Installs its own real-mode handler for INT 31h: AX=0501h returns fixed linear addresses in order
; in BX:CX with CF=0. The requests are (size -> address):
;   without argument: 0x100 -> 0x00300000, 0x3000 -> 0x00400000, 0x0A10 -> 0x00420000
;   argument E:    0x4000 -> 0x00500000            (one block for all objects)
;   argument N:    0x100 -> 0x00300000, 0x200 -> 0x00310000   (nothing fits)
; Restores the old INT 31h vector, prints "LE done", exits with errorlevel 0.
; Variants via %define before the %include (wrapper files LEEIP, LEVIELE, LEEXEC):
;   LE_OBJECTS n   object count in the LE header (default 2)
;   LE_EIP value   EIP in the LE header (default 0x100; 0x3000 lies outside object 1)
;   LE_VIELE       without argument 257 requests (0x100 bytes each, never fitting) instead of the normal sequence
;   LE_EXEC        before the requests the program starts EXITCODE.COM (INT 21h 4B00, after 4Ah)
%ifndef LE_OBJECTS
%define LE_OBJECTS 2
%endif
%ifndef LE_EIP
%define LE_EIP 0x100
%endif
; File size from label differences per section (NASM cannot share section boundaries): MZ header 0x40 + code + LE part.
%define FILE_SIZE (0x40 + (code_end - start) + (le_end - le_header))
bits 16

section mz start=0 vstart=0
    db 'MZ'
    dw FILE_SIZE % 512              ; bytes on the last page
    dw (FILE_SIZE + 511) / 512      ; pages
    dw 0                                ; relocations
    dw 4                                ; header size in paragraphs (0x40 bytes)
    dw 0x0040                           ; min. extra memory
    dw 0xFFFF                           ; max. extra memory
    dw 0                                ; SS relative (= CS)
    dw stack_end                        ; SP
    dw 0                                ; checksum
    dw start                            ; IP
    dw 0                                ; CS relative
    dw 0x40                             ; relocation table (empty)
    dw 0                                ; overlay
    times 0x3C-($-$$) db 0
    dd section.le.start                 ; offset of the LE header in the file

section code follows=mz vstart=0
start:
    xor ah, ah
    cmp byte [0x80], 0                  ; DS = PSP: length of the command tail
    je .without_argument
    mov ah, [0x82]                      ; first char after the space
.without_argument:
    push cs
    pop ds
    mov [mode], ah
    mov word [sizes], seq_normal
    mov word [addresses], addr_normal
    cmp ah, 'E'
    jne .not_e
    mov word [sizes], seq_e
    mov word [addresses], addr_e
.not_e:
    cmp ah, 'N'
    jne .vector
    mov word [sizes], seq_n
    mov word [addresses], addr_n
.vector:
    mov ax, 0x3531                      ; remember the old vector (ES:BX)
    int 0x21
    mov [old_off], bx
    mov [old_seg], es
    mov ax, 0x2531                      ; set the own handler (DS:DX)
    mov dx, handler
    int 0x21
%ifdef LE_EXEC
    mov ah, 0x62                        ; PSP -> ES, shrink the block (otherwise there is no memory for the child)
    int 0x21
    mov es, bx
    mov bx, 0x100
    mov ah, 0x4A
    int 0x21
    mov [parameter + 4], cs
    mov [parameter + 8], cs
    mov [parameter + 12], cs
    mov [old_sp], sp
    push cs
    pop es
    mov ax, 0x4B00
    mov dx, child_name
    mov bx, parameter
    int 0x21
    cli
    mov ax, cs
    mov ss, ax
    mov sp, [cs:old_sp]
    sti
    push cs
    pop ds
%endif
    mov si, [sizes]
.request:                               ; list of dwords, 0 = end
    mov cx, [si]
    mov bx, [si+2]
    mov ax, cx
    or ax, bx
    jz .restore
    mov ax, 0x0501
    int 0x31
    add si, 4
    jmp .request
.restore:
    push ds
    mov dx, [old_off]
    mov ds, [old_seg]
    mov ax, 0x2531
    int 0x21
    pop ds
    mov ah, 0x09
    mov dx, text
    int 0x21
    mov ax, 0x4C00
    int 0x21

handler:                                ; INT 31h, only AX=0501h used: next address to BX:CX, CF=0
    push si
    mov si, [cs:addresses]
    add si, [cs:index]
    mov cx, [cs:si]
    mov bx, [cs:si+2]
    add word [cs:index], 4
    pop si
    clc
    sti
    retf 2

mode    db 0
index    dw 0
sizes    dw 0
addresses dw 0
old_off  dw 0
old_seg  dw 0
%ifdef LE_VIELE
seq_normal   times 257 dd 0x100
             dd 0
addr_normal  times 257 dd 0x00300000
%else
seq_normal   dd 0x100, 0x3000, 0x0A10, 0
addr_normal  dd 0x00300000, 0x00400000, 0x00420000
%endif
seq_e        dd 0x4000, 0
addr_e       dd 0x00500000
seq_n        dd 0x100, 0x200, 0
addr_n       dd 0x00300000, 0x00310000
text db 'LE done', 13, 10, '$'
%ifdef LE_EXEC
child_name   db 'EXITCODE.COM', 0
command_line db 0, 13
fcb          times 16 db 0
parameter    dw 0                       ; environment of the parent program
             dw command_line, 0
             dw fcb, 0
             dw fcb, 0
old_sp       dw 0
%endif
stack times 256 db 0
stack_end:
    align 4, db 0                       ; sections are 4-byte aligned: without this the size would be 1 too small
code_end:

section le follows=code vstart=0
le_header:
    db 'LE', 0, 0                       ; signature, byte and word order
    dd 0                                ; +0x04 format level
    dw 2                                ; +0x08 CPU 386
    dw 1                                ; +0x0A OS
    dd 0                                ; +0x0C module version
    dd 0                                ; +0x10 module flags
    dd 0                                ; +0x14 page count
    dd 1                                ; +0x18 EIP object (1-based)
    dd LE_EIP                           ; +0x1C EIP (offset in the object)
    times 0x40-($-le_header) db 0
    dd object_table - le_header         ; +0x40 offset of the object table (relative to the header)
    dd LE_OBJECTS                       ; +0x44 object count
    times 0xB0-($-le_header) db 0
object_table:                           ; 24 bytes each: size, base, flags, page index, page count, reserved
    dd 0x3000, 0x10000, 0x2045, 1, 0, 0
    dd 0x0A10, 0x20000, 0x2043, 1, 0, 0
le_end:
