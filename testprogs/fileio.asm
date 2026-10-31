; SPDX-License-Identifier: GPL-2.0-or-later
; FILEIO.COM - known file accesses for the file event. Waits for go (03F1). Offsets relative to the PSP segment.
;   creates FIO.DAT and writes 64 bytes 00..3F, closes; opens it, sets position 10h, reads 16 bytes via
;   read (0280) into buffer (0400), reads 8 bytes directly into 0410, closes, writes "FIO" to CON (handle 1,
;   no file event), done=1 (03F0), then an hlt loop.
bits 16
org 0x100
start:
.wait:
    cmp byte [go], 0
    je .wait
    mov di, pattern
    xor al, al
    mov cx, 64
.m:
    stosb
    inc al
    loop .m
    mov ah, 0x3C
    xor cx, cx
    mov dx, name
    int 0x21                 ; create
    mov bx, ax
    mov ah, 0x40
    mov cx, 64
    mov dx, pattern
    int 0x21                 ; write 64 bytes from offset 0
    mov ah, 0x3E
    int 0x21
    mov ax, 0x3D00
    mov dx, name
    int 0x21                 ; open for reading
    mov bx, ax
    mov ax, 0x4200
    xor cx, cx
    mov dx, 0x10
    int 0x21                 ; position 10h
    mov cx, 16
    mov dx, buffer
    call read               ; 10h..1Fh
    mov ah, 0x3F
    mov cx, 8
    mov dx, buffer + 16
    int 0x21                 ; 20h..27h
    mov ah, 0x3E
    int 0x21
    mov ah, 0x40
    mov bx, 1
    mov cx, 3
    mov dx, text
    int 0x21                 ; to CON
    mov byte [done], 1
    sti
.w:
    hlt
    jmp .w
    times 0x180 - ($ - $$) db 0xCC
read:                       ; 0280
    mov ah, 0x3F
    int 0x21
    ret
    times 0x200 - ($ - $$) db 0
name   db 'FIO.DAT', 0       ; 0300
text   db 'FIO'
    times 0x2F0 - ($ - $$) db 0
done db 0                  ; 03F0
go     db 0                  ; 03F1
    times 0x300 - ($ - $$) db 0
buffer:                      ; 0400
    times 32 db 0
pattern:                     ; 0420
    times 64 db 0
