; BATCH.COM - Testprogramm fuer run_batch (Stufe 4).
; stdout: "A" (INT 21h 02h), "B" (INT 21h 06h), "TTY" (INT 10h 0Eh), CR LF (INT 21h 02h) -> "ABTTY\r\n".
; Legt BATCH.OUT mit dem Inhalt "DMCP" (4 Byte) an, schreibt "ERR\r\n" auf Handle 2 (stderr).
; Ende mit Errorlevel 07h; kann BATCH.OUT nicht angelegt werden: Errorlevel 01h.
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
    jz .tty_ende
    mov ah, 0x0E
    mov bx, 0x0007
    int 0x10
    jmp .tty
.tty_ende:
    mov ah, 0x02
    mov dl, 13
    int 0x21
    mov ah, 0x02
    mov dl, 10
    int 0x21
    mov ah, 0x3C            ; BATCH.OUT anlegen
    xor cx, cx
    mov dx, dateiname
    int 0x21
    jc .fehler
    mov bx, ax
    mov ah, 0x40
    mov cx, 4
    mov dx, inhalt
    int 0x21
    mov ah, 0x3E
    int 0x21
    mov ah, 0x40            ; stderr
    mov bx, 2
    mov cx, err_ende - err
    mov dx, err
    int 0x21
    mov ax, 0x4C07
    int 0x21
.fehler:
    mov ax, 0x4C01
    int 0x21
tty db 'TTY', 0
dateiname db 'BATCH.OUT', 0
inhalt db 'DMCP'
err db 'ERR', 13, 10
err_ende:
