; CALLS.COM - bekannte Aufrufe fuer das Aufrufprotokoll (D29). Offsets relativ zum PSP-Segment
; (linear = PSP*16 + Offset). Wartet, bis der Test los (03F1) auf 1 setzt.
;   0200 innen (inc ax)        0210 aussen (2x innen)     0220 rek (Rekursion ueber cx)
;   0230 fern (retf, ax=1234)  0240 mitparam (ret 4)      0250 oft (5x gerufen)
;   0260 flucht_aussen         0270 flucht_innen (Stack zuruecksetzen, ret am Rahmen vorbei)
;   0280 haengt (hlt-Schleife, kehrt nie zurueck)
;   0300 fernziel (dd), 0304 merk_sp (dw), 03F0 fertig (1 vor haengt), 03F1 los
bits 16
org 0x100
start:
    mov [fernziel + 2], cs
.warte:
    cmp byte [los], 0
    je .warte
    mov ax, 5
    call aussen              ; innen 2x: ax 5 -> 6 -> 7
    mov cx, 3
    call rek                 ; Rekursion, Tiefe 3
    call far [fernziel]      ; ax = 1234h
    push word 0x00AA
    push word 0x00BB
    call mitparam            ; ax = 0165h, ret 4
    mov cx, 5
.oft:
    push cx
    call oft
    pop cx
    loop .oft
    call flucht_aussen       ; flucht_innen springt per Stack-Ruecksetzen heraus
    mov ah, 0x30
    int 0x21                 ; DOS-Version
    mov byte [fertig], 1
    call haengt
    times 0x100 - ($ - $$) db 0xCC
innen:                       ; 0200
    inc ax
    ret
    times 0x110 - ($ - $$) db 0xCC
aussen:                      ; 0210
    call innen
    call innen
    ret
    times 0x120 - ($ - $$) db 0xCC
rek:                         ; 0220
    dec cx
    jz .ende
    call rek
.ende:
    ret
    times 0x130 - ($ - $$) db 0xCC
fern:                        ; 0230
    mov ax, 0x1234
    retf
    times 0x140 - ($ - $$) db 0xCC
mitparam:                    ; 0240
    push bp
    mov bp, sp
    mov ax, [bp + 4]
    add ax, [bp + 6]
    pop bp
    ret 4
    times 0x150 - ($ - $$) db 0xCC
oft:                         ; 0250
    ret
    times 0x160 - ($ - $$) db 0xCC
flucht_aussen:               ; 0260
    mov [merk_sp], sp
    call flucht_innen
    mov byte [fertig], 0xEE  ; nie erreicht
    ret
    times 0x170 - ($ - $$) db 0xCC
flucht_innen:                ; 0270
    mov sp, [merk_sp]
    ret                      ; kehrt direkt zum Aufrufer von flucht_aussen zurueck
    times 0x180 - ($ - $$) db 0xCC
haengt:                      ; 0280
    sti
.w:
    hlt
    jmp .w
    times 0x200 - ($ - $$) db 0
fernziel dw fern, 0          ; 0300
merk_sp  dw 0                ; 0304
    times 0x2F0 - ($ - $$) db 0
fertig   db 0                ; 03F0
los      db 0                ; 03F1
