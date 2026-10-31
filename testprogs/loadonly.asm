; LOADONLY.COM - loads EXITCODE.COM without starting it (INT 21h AX=4B01h), then exits with errorlevel 0.
; Before that, INT 21h 4Ah shrinks the own block to 1000h paragraphs. The child is never executed
; (no output text from EXITCODE); LOADONLY itself prints nothing.
bits 16
org 0x100
start:
    mov sp, stack_end
    mov bx, 0x1000
    mov ah, 0x4A                ; ES = PSP since program start
    int 0x21
    mov [parameter + 4], cs
    mov [parameter + 8], cs
    mov [parameter + 12], cs
    push cs
    pop es
    mov ax, 0x4B01
    mov dx, name
    mov bx, parameter
    int 0x21
    mov ax, 0x4C00
    int 0x21
name         db 'EXITCODE.COM', 0
command_line db 0, 13
fcb          times 16 db 0
parameter    dw 0               ; environment of the parent program
             dw command_line, 0
             dw fcb, 0
             dw fcb, 0
             dw 0, 0, 0, 0      ; return SS:SP, CS:IP (4B01h)
stack        times 128 db 0
stack_end:
