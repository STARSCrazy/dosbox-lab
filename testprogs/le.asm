; AUSGABE=EXE
; LE.EXE - Testprogramm fuer die LE-Basis-Ableitung (D27).
; MZ-Programm mit angehaengtem LE-Kopf (Doppelwort 0x3C): 2 Objekte
;   obj1 va 0x10000 Groesse 0x3000, obj2 va 0x20000 Groesse 0x0A10; EIP-Objekt 1, EIP 0x100 (einsprung_va 0x10100).
; Setzt einen eigenen Real-Mode-Handler fuer INT 31h: AX=0501h liefert der Reihe nach feste lineare Adressen
; in BX:CX mit CF=0. Angefordert wird (Groesse -> Adresse):
;   ohne Argument: 0x100 -> 0x00300000, 0x3000 -> 0x00400000, 0x0A10 -> 0x00420000
;   Argument E:    0x4000 -> 0x00500000            (ein Block fuer alle Objekte)
;   Argument N:    0x100 -> 0x00300000, 0x200 -> 0x00310000   (nichts passt)
; Stellt den alten INT-31h-Vektor wieder her, gibt "LE fertig" aus, Ende mit Errorlevel 0.
; Varianten ueber %define vor dem %include (Wrapper-Dateien LEEIP, LEVIELE, LEEXEC):
;   LE_OBJEKTE n   Objektzahl im LE-Kopf (Standard 2)
;   LE_EIP wert    EIP im LE-Kopf (Standard 0x100; 0x3000 liegt ausserhalb von Objekt 1)
;   LE_VIELE       ohne Argument 257 Anforderungen (je 0x100 Byte, nie passend) statt der normalen Folge
;   LE_EXEC        vor den Anforderungen startet das Programm EXITCODE.COM (INT 21h 4B00, vorher 4Ah)
%ifndef LE_OBJEKTE
%define LE_OBJEKTE 2
%endif
%ifndef LE_EIP
%define LE_EIP 0x100
%endif
; Dateigroesse aus Labeldifferenzen je Sektion (NASM kann Sektionsgrenzen nicht teilen): MZ-Kopf 0x40 + Code + LE-Teil.
%define DATEI_GROESSE (0x40 + (code_ende - start) + (le_ende - le_kopf))
bits 16

section mz start=0 vstart=0
    db 'MZ'
    dw DATEI_GROESSE % 512              ; Bytes auf der letzten Seite
    dw (DATEI_GROESSE + 511) / 512      ; Seiten
    dw 0                                ; Relokationen
    dw 4                                ; Kopfgroesse in Paragraphen (0x40 Byte)
    dw 0x0040                           ; min. Zusatzspeicher
    dw 0xFFFF                           ; max. Zusatzspeicher
    dw 0                                ; SS relativ (= CS)
    dw stapel_ende                      ; SP
    dw 0                                ; Pruefsumme
    dw start                            ; IP
    dw 0                                ; CS relativ
    dw 0x40                             ; Relokationstabelle (leer)
    dw 0                                ; Overlay
    times 0x3C-($-$$) db 0
    dd section.le.start                 ; Versatz des LE-Kopfs in der Datei

section code follows=mz vstart=0
start:
    xor ah, ah
    cmp byte [0x80], 0                  ; DS = PSP: Laenge des Befehlsrests
    je .ohne_argument
    mov ah, [0x82]                      ; erstes Zeichen nach dem Leerzeichen
.ohne_argument:
    push cs
    pop ds
    mov [modus], ah
    mov word [groessen], folge_normal
    mov word [adressen], adr_normal
    cmp ah, 'E'
    jne .nicht_e
    mov word [groessen], folge_e
    mov word [adressen], adr_e
.nicht_e:
    cmp ah, 'N'
    jne .vektor
    mov word [groessen], folge_n
    mov word [adressen], adr_n
.vektor:
    mov ax, 0x3531                      ; alten Vektor merken (ES:BX)
    int 0x21
    mov [alt_off], bx
    mov [alt_seg], es
    mov ax, 0x2531                      ; eigenen Handler setzen (DS:DX)
    mov dx, handler
    int 0x21
%ifdef LE_EXEC
    mov ah, 0x62                        ; PSP -> ES, Block verkleinern (sonst fehlt der Speicher fuer das Kind)
    int 0x21
    mov es, bx
    mov bx, 0x100
    mov ah, 0x4A
    int 0x21
    mov [parameter + 4], cs
    mov [parameter + 8], cs
    mov [parameter + 12], cs
    mov [alter_sp], sp
    push cs
    pop es
    mov ax, 0x4B00
    mov dx, kind_name
    mov bx, parameter
    int 0x21
    cli
    mov ax, cs
    mov ss, ax
    mov sp, [cs:alter_sp]
    sti
    push cs
    pop ds
%endif
    mov si, [groessen]
.anfordern:                             ; Liste von Doppelworten, 0 = Ende
    mov cx, [si]
    mov bx, [si+2]
    mov ax, cx
    or ax, bx
    jz .zurueck
    mov ax, 0x0501
    int 0x31
    add si, 4
    jmp .anfordern
.zurueck:
    push ds
    mov dx, [alt_off]
    mov ds, [alt_seg]
    mov ax, 0x2531
    int 0x21
    pop ds
    mov ah, 0x09
    mov dx, text
    int 0x21
    mov ax, 0x4C00
    int 0x21

handler:                                ; INT 31h, nur AX=0501h benutzt: naechste Adresse nach BX:CX, CF=0
    push si
    mov si, [cs:adressen]
    add si, [cs:index]
    mov cx, [cs:si]
    mov bx, [cs:si+2]
    add word [cs:index], 4
    pop si
    clc
    sti
    retf 2

modus    db 0
index    dw 0
groessen dw 0
adressen dw 0
alt_off  dw 0
alt_seg  dw 0
%ifdef LE_VIELE
folge_normal times 257 dd 0x100
             dd 0
adr_normal   times 257 dd 0x00300000
%else
folge_normal dd 0x100, 0x3000, 0x0A10, 0
adr_normal   dd 0x00300000, 0x00400000, 0x00420000
%endif
folge_e      dd 0x4000, 0
adr_e        dd 0x00500000
folge_n      dd 0x100, 0x200, 0
adr_n        dd 0x00300000, 0x00310000
text db 'LE fertig', 13, 10, '$'
%ifdef LE_EXEC
kind_name    db 'EXITCODE.COM', 0
befehlszeile db 0, 13
fcb          times 16 db 0
parameter    dw 0                       ; Umgebung des Elternprogramms
             dw befehlszeile, 0
             dw fcb, 0
             dw fcb, 0
alter_sp     dw 0
%endif
stapel times 256 db 0
stapel_ende:
    align 4, db 0                       ; Sektionen sind 4-Byte-ausgerichtet: ohne dies waere die Groesse um 1 zu klein
code_ende:

section le follows=code vstart=0
le_kopf:
    db 'LE', 0, 0                       ; Signatur, Byte- und Wortordnung
    dd 0                                ; +0x04 Format-Stufe
    dw 2                                ; +0x08 CPU 386
    dw 1                                ; +0x0A OS
    dd 0                                ; +0x0C Modulversion
    dd 0                                ; +0x10 Modulflags
    dd 0                                ; +0x14 Seitenzahl
    dd 1                                ; +0x18 EIP-Objekt (1-basiert)
    dd LE_EIP                           ; +0x1C EIP (Versatz im Objekt)
    times 0x40-($-le_kopf) db 0
    dd objekttabelle - le_kopf          ; +0x40 Versatz der Objekttabelle (relativ zum Kopf)
    dd LE_OBJEKTE                       ; +0x44 Objektzahl
    times 0xB0-($-le_kopf) db 0
objekttabelle:                          ; je 24 Byte: Groesse, Basis, Flags, Seitenindex, Seitenzahl, reserviert
    dd 0x3000, 0x10000, 0x2045, 1, 0, 0
    dd 0x0A10, 0x20000, 0x2043, 1, 0, 0
le_ende:

