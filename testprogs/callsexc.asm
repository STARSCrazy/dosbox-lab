; CALLSEXC.COM - Ausnahme-Kontexte ohne eigenes IRET fuer das Aufrufprotokoll (D29).
; Offsets relativ zum PSP-Segment (linear = PSP*16 + Offset). Wartet, bis der Test los (03F1) auf 1 setzt.
;   1. div 0 -> Handler (Kontext 1); der loest erneut div 0 aus (Kontext 2); der zweite Eintritt setzt SP auf
;      den ersten Rahmen zurueck und kehrt per IRET zum ersten Fehler zurueck (Kontext 2 endet ohne eigenes IRET).
;   2. call ziel (0200) im Hauptkontext.
;   3. div 0 -> Handler stellt den alten INT-0-Vektor wieder her und beendet per INT 21h 4C00h (kein IRET).
;   0200 ziel (inc ax), 0300 alter Vektor (dd), 0304 merk_sp (dw), 0306 zaehler (db), 03F1 los
bits 16
org 0x100
start:
    mov ax, 0x3500
    int 0x21                 ; ES:BX = alter INT-0-Vektor
    mov [alt_vektor], bx
    mov [alt_vektor + 2], es
    mov ax, 0x2500
    mov dx, handler
    int 0x21
.warte:
    cmp byte [los], 0
    je .warte
    xor bl, bl
    mov ax, 10
    div bl                   ; Fehler 1, der Handler kehrt mit bl = 1 hierher zurueck
    call ziel
    xor bl, bl
    mov ax, 10
    div bl                   ; Fehler 3: Programmende im Handler
    mov ax, 0x4C01           ; nie erreicht
    int 0x21
handler:
    inc byte [zaehler]
    cmp byte [zaehler], 1
    je .erster
    cmp byte [zaehler], 2
    je .zweiter
    push ds
    lds dx, [alt_vektor]
    mov ax, 0x2500
    int 0x21
    pop ds
    mov ax, 0x4C00
    int 0x21
.erster:
    mov [merk_sp], sp
    mov ax, 10
    div bl                   ; bl noch 0: Fehler 2
.zweiter:
    mov sp, [merk_sp]        ; Rahmen von Fehler 2 verwerfen
    mov bl, 1
    mov ax, 10
    iret                     ; zum Rahmen von Fehler 1
    times 0x100 - ($ - $$) db 0xCC
ziel:                        ; 0200
    inc ax
    ret
    times 0x200 - ($ - $$) db 0
alt_vektor dd 0              ; 0300
merk_sp    dw 0              ; 0304
zaehler    db 0              ; 0306
    times 0x2F1 - ($ - $$) db 0
los        db 0              ; 03F1
