; BATCH.COM - test program for run_batch.
; stdout: "A" (INT 21h 02h), "B" (INT 21h 06h), "TTY" (INT 10h 0Eh), CR LF (INT 21h 02h) -> "ABTTY\r\n".
; Creates BATCH.OUT with the content "DMCP" (4 bytes) and writes "ERR\r\n" to handle 2 (stderr).
; Ends with errorlevel 07h; if BATCH.OUT cannot be created: errorlevel 01h.
bits 16
org 0x100
start:
    mov ah, 0x02
    mov dl, 'A'
    int 0x21
    mov ah, 0x06
    mov dl, 'B'
    int 0x21
    mov si, tty
.tty:
    lodsb
    or al, al
    jz .tty_end
    mov ah, 0x0E
    mov bx, 0x0007
    int 0x10
    jmp .tty
.tty_end:
    mov ah, 0x02
    mov dl, 13
    int 0x21
    mov ah, 0x02
    mov dl, 10
    int 0x21
    mov ah, 0x3C            ; create BATCH.OUT
    xor cx, cx
    mov dx, filename
    int 0x21
    jc .error
    mov bx, ax
    mov ah, 0x40
    mov cx, 4
    mov dx, content
    int 0x21
    mov ah, 0x3E
    int 0x21
    mov ah, 0x40            ; stderr
    mov bx, 2
    mov cx, err_end - err
    mov dx, err
    int 0x21
    mov ax, 0x4C07
    int 0x21
.error:
    mov ax, 0x4C01
    int 0x21
tty db 'TTY', 0
filename db 'BATCH.OUT', 0
content db 'DMCP'
err db 'ERR', 13, 10
err_end:
