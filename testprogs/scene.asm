; SCENE.COM - test program for scenarios. Offsets relative to the PSP.
; Prints READY, waits for a key (INT 16h), stores the char at 0300, prints "KEY <char>",
; calls done_fn and stops in an HLT loop.
;   0200 done_fn: [0301] = 5Ah, prints DONE, ret
;   0300 key (db), 0301 status (db)
bits 16
org 0x100
start:
    mov dx, t_ready
    mov ah, 9
    int 0x21
    xor ah, ah
    int 0x16                 ; AL = char
    mov [key], al
    mov [t_key + 4], al
    mov dx, t_key
    mov ah, 9
    int 0x21
    call done_fn
.h:
    sti
    hlt
    jmp .h
t_ready db 'READY', 13, 10, '$'
t_key  db 'KEY ?', 13, 10, '$'
    times 0x100 - ($ - $$) db 0xCC
done_fn:                   ; 0200
    mov byte [status], 0x5A
    mov dx, t_done
    mov ah, 9
    int 0x21
    ret
t_done db 'DONE', 13, 10, '$'
    times 0x200 - ($ - $$) db 0
key  db 0                  ; 0300
status db 0                  ; 0301
