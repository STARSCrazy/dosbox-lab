; SPDX-License-Identifier: GPL-2.0-or-later
; KEYS.COM - for each key: AX from INT 16h/00h to 0000:04F0, counter (word) in 0000:04F2.
bits 16
org 0x100
start:
    xor ax, ax
    mov es, ax
    mov word [es:0x4F0], 0
    mov word [es:0x4F2], 0
.loop:
    mov ah, 0x00
    int 0x16
    mov [es:0x4F0], ax
    inc word [es:0x4F2]
    mov dl, al
    mov ah, 0x02
    int 0x21
    jmp .loop
