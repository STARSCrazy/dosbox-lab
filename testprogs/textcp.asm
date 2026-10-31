; SPDX-License-Identifier: GPL-2.0-or-later
; TEXTCP.COM - writes the character codes C9 CD 84 01 7F 00 (attribute 07 each) directly into the text
; screen (B800:0640 = line 10, column 0) and then waits for a key. For screen.text (CP437 -> Unicode);
; mem.write does not reach VGA memory (it writes system memory).
bits 16
org 0x100
start:
    mov ax, 0xB800
    mov es, ax
    mov di, 0x0640
    mov si, char
    mov cx, char_end - char
.loop:
    lodsb
    mov ah, 0x07
    stosw
    loop .loop
    xor ax, ax
    int 0x16
    mov ax, 0x4C00
    int 0x21
char db 0xC9, 0xCD, 0x84, 0x01, 0x7F, 0x00
char_end:
