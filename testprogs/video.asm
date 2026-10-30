; VIDEO.COM - Modus 13h, Palette 1=rot 2=gruen 3=blau, drei senkrechte Streifen.
bits 16
org 0x100
start:
    mov ax, 0x0013
    int 0x10
    mov dx, 0x3C8
    mov al, 1
    out dx, al
    inc dx                  ; 0x3C9: R,G,B je Eintrag, ab Farbe 1
    mov al, 63
    out dx, al              ; 1: R
    xor al, al
    out dx, al              ; 1: G
    out dx, al              ; 1: B
    out dx, al              ; 2: R
    mov al, 63
    out dx, al              ; 2: G
    xor al, al
    out dx, al              ; 2: B
    out dx, al              ; 3: R
    out dx, al              ; 3: G
    mov al, 63
    out dx, al              ; 3: B
    mov ax, 0xA000
    mov es, ax
    xor di, di
    mov bx, 200
.zeile:
    mov al, 1
    mov cx, 106
    rep stosb
    mov al, 2
    mov cx, 107
    rep stosb
    mov al, 3
    mov cx, 107
    rep stosb
    dec bx
    jnz .zeile
.halt:
    jmp .halt
