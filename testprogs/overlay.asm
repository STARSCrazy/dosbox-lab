; OVERLAY.COM - loads EXITCODE.COM as an overlay (INT 21h AX=4B03h) and exits with errorlevel 0.
; Parameter block: load segment 3000h (in free memory, not the own block), relocation factor 3000h.
; The overlay is not executed. Output "OVERLAY done". Expected module: EXITCODE.COM,
; kind "overlay", load_seg and rel_base 3000, paragraphs = (file size + 15) / 16.
bits 16
org 0x100
start:
    mov ax, 0x4B03
    mov dx, name
    mov bx, parameter
    int 0x21
    mov dx, text
    mov ah, 0x09
    int 0x21
    mov ax, 0x4C00
    int 0x21
name      db 'EXITCODE.COM', 0
parameter dw 0x3000, 0x3000
text      db 'OVERLAY done', 13, 10, '$'
