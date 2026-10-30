; TEXTCP.COM - schreibt direkt in den Textbildschirm (B800:0640 = Zeile 10, Spalte 0) die Zeichencodes
; C9 CD 84 01 7F 00 (je Attribut 07) und wartet dann auf eine Taste. Fuer screen.text (CP437 -> Unicode);
; mem.write erreicht den VGA-Speicher nicht (schreibt Systemspeicher).
bits 16
org 0x100
start:
    mov ax, 0xB800
    mov es, ax
    mov di, 0x0640
    mov si, zeichen
    mov cx, zeichen_ende - zeichen
.schleife:
    lodsb
    mov ah, 0x07
    stosw
    loop .schleife
    xor ax, ax
    int 0x16
    mov ax, 0x4C00
    int 0x21
zeichen db 0xC9, 0xCD, 0x84, 0x01, 0x7F, 0x00
zeichen_ende:
