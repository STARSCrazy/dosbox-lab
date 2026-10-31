; MOUSE.COM - mode 13h, INT 33h state written continuously to 0000:04F4..04FD:
;   04F4 x, 04F6 y (AX=3), 04F8 buttons (OR since start), 04FA/04FC mickey sums (AX=0Bh)
bits 16
org 0x100
start:
    mov ax, 0x0013
    int 0x10
    xor ax, ax
    int 0x33                ; reset the driver
    xor ax, ax
    mov es, ax
    mov di, 0x4F4
    mov cx, 6
    rep stosw               ; zero 04F4..04FF
.loop:
    mov ax, 0x0003
    int 0x33
    mov [es:0x4F4], cx
    mov [es:0x4F6], dx
    or [es:0x4F8], bx
    mov ax, 0x000B
    int 0x33
    add [es:0x4FA], cx
    add [es:0x4FC], dx
    jmp .loop
