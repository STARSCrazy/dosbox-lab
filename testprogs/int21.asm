; INT21.COM - test program for events (exec/exit/int/DPMI decoding).
;  1. own INT 31h handler (real mode); INT 31h AX=0000 CX=1 (reply AX=00A7, CF=0),
;     INT 31h AX=0007 BX=00A7 CX:DX=0012:3456 (reply CF=0)
;  2. INT 21h 3Dh opens "INT21.COM", 3Eh closes it
;  3. INT 21h 4Ah shrinks the own block, 4B00h starts "EXITCODE.COM" with the command line " ARG1"
;  4. INT 21h 4Dh gets the exit code of the child (2Ah), output "INT21 done", exit with child code + 1 (2Bh)
bits 16
org 0x100
start:
    mov sp, stack_end
    mov ax, 0x2531              ; INT 31h -> int31 (DS = CS in a .COM)
    mov dx, int31
    int 0x21
    mov ax, 0x0000
    mov cx, 1
    int 0x31
    mov bx, ax                  ; 00A7
    mov ax, 0x0007
    mov cx, 0x0012
    mov dx, 0x3456
    int 0x31

    mov ax, 0x3D00
    mov dx, own_name
    int 0x21
    jc .without_file
    mov bx, ax
    mov ah, 0x3E
    int 0x21
.without_file:

    mov bx, (image_end - start + 0x100 + 15) / 16
    mov ah, 0x4A                ; ES = PSP since program start
    int 0x21

    mov [parameter + 4], cs     ; segments in the EXEC parameter block
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

    mov ah, 0x4D
    int 0x21
    mov [result], al
    mov dx, text_done
    mov ah, 0x09
    int 0x21
    mov al, [result]
    inc al
    mov ah, 0x4C
    int 0x21

int31:                          ; minimal "DPMI" handler: AX=0000 -> AX=00A7, always CF=0
    cmp ax, 0x0000
    jne .done
    mov ax, 0x00A7
.done:
    push bp
    mov bp, sp
    and word [bp + 6], 0xFFFE   ; clear CF in the saved FLAGS
    pop bp
    iret

own_name     db 'INT21.COM', 0
child_name   db 'EXITCODE.COM', 0
command_line db 5, ' ARG1', 13
fcb          times 16 db 0
parameter    dw 0               ; environment of the parent program
             dw command_line, 0
             dw fcb, 0
             dw fcb, 0
text_done  db 'INT21 done', 13, 10, '$'
result     db 0
old_sp       dw 0
align 2
stack        times 256 db 0
stack_end:
image_end:
