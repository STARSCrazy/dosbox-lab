; MEMTEST.COM - known memory content for dosbox-lab tests.
; The marker "DMCP-MEMTEST-MARK" is followed by: counter (dword, counting up),
; mirror (dword, continuously receives the value from input), input (dword).
bits 16
org 0x100
start:
    mov dx, message
    mov ah, 0x09
    int 0x21
.loop:
    inc dword [counter]
    mov eax, [input]
    mov [mirror], eax
    mov ah, 0x01            ; key present?
    int 0x16
    jz .loop
    mov ah, 0x00
    int 0x16
    cmp al, 27              ; ESC exits
    jne .loop
    mov ax, 0x4C00
    int 0x21
message db 'memtest running (ESC exits)', 13, 10, '$'
marker  db 'DMCP-MEMTEST-MARK'
counter dd 0
mirror  dd 0
input dd 0
