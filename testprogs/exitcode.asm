; EXITCODE.COM - one line on stdout (INT 21h 09h), one on stderr (INT 21h 40h, handle 2),
; exits with errorlevel 2Ah (INT 21h 4Ch).
bits 16
org 0x100
start:
    mov dx, text_out
    mov ah, 0x09
    int 0x21
    mov ah, 0x40
    mov bx, 2
    mov cx, text_err_end - text_err
    mov dx, text_err
    int 0x21
    mov ax, 0x4C2A
    int 0x21
text_out db 'EXITCODE stdout', 13, 10, '$'
text_err db 'EXITCODE stderr', 13, 10
text_err_end:
