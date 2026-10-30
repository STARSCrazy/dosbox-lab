; MEMTEST.COM - bekannter Speicherinhalt fuer dosbox-lab-Tests.
; Nach dem Marker "DMCP-MEMTEST-MARK" folgen: zaehler (dword, laeuft hoch),
; spiegel (dword, bekommt laufend den Wert von eingabe), eingabe (dword).
bits 16
org 0x100
start:
    mov dx, meldung
    mov ah, 0x09
    int 0x21
.schleife:
    inc dword [zaehler]
    mov eax, [eingabe]
    mov [spiegel], eax
    mov ah, 0x01            ; Taste vorhanden?
    int 0x16
    jz .schleife
    mov ah, 0x00
    int 0x16
    cmp al, 27              ; ESC beendet
    jne .schleife
    mov ax, 0x4C00
    int 0x21
meldung db 'memtest laeuft (ESC beendet)', 13, 10, '$'
marker  db 'DMCP-MEMTEST-MARK'
zaehler dd 0
spiegel dd 0
eingabe dd 0
