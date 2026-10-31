; SZENE.COM - Testprogramm fuer Szenarien (D35). Offsets relativ zum PSP.
; Gibt BEREIT aus, wartet auf eine Taste (INT 16h), legt das Zeichen nach 0300, gibt "TASTE <zeichen>" aus,
; ruft fertig_fn und haelt in einer HLT-Schleife.
;   0200 fertig_fn: [0301] = 5Ah, gibt FERTIG aus, ret
;   0300 taste (db), 0301 status (db)
bits 16
org 0x100
start:
    mov dx, t_bereit
    mov ah, 9
    int 0x21
    xor ah, ah
    int 0x16                 ; AL = Zeichen
    mov [taste], al
    mov [t_taste + 6], al
    mov dx, t_taste
    mov ah, 9
    int 0x21
    call fertig_fn
.h:
    sti
    hlt
    jmp .h
t_bereit db 'BEREIT', 13, 10, '$'
t_taste  db 'TASTE ?', 13, 10, '$'
    times 0x100 - ($ - $$) db 0xCC
fertig_fn:                   ; 0200
    mov byte [status], 0x5A
    mov dx, t_fertig
    mov ah, 9
    int 0x21
    ret
t_fertig db 'FERTIG', 13, 10, '$'
    times 0x200 - ($ - $$) db 0
taste  db 0                  ; 0300
status db 0                  ; 0301
