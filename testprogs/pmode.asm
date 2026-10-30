; PMODE.COM - schaltet in 32-Bit Protected Mode mit Paging und bleibt dort (IF=0).
;   GDT: 08 Code32 flach, 10 Daten32 flach, 18 Daten32 mit Basis 0x01000000
;   Paging: 0..4 MB identisch; linear 0x01000000..0x013FFFFF -> physisch 0x00200000..
;   Seitenverzeichnis physisch 0x00300000, Tabellen 0x00301000 und 0x00302000
;   linear 0x01000000 = 'DMCP', 0x01000004 = Zaehler (laeuft hoch)
bits 16
org 0x100
start:
    cli
    in al, 0x92
    or al, 2
    out 0x92, al            ; A20 an
    xor eax, eax
    mov ax, cs
    shl eax, 4              ; lineare Basis dieses Programms
    mov ebx, eax
    add eax, gdt
    mov [gdtr + 2], eax
    mov eax, ebx
    add eax, pm32
    mov [sprungziel], eax
    lgdt [gdtr]
    mov eax, cr0
    or al, 1
    mov cr0, eax
    o32 jmp far [sprungziel]

sprungziel:
    dd 0
    dw 0x08
align 8
gdt:
    dq 0
    db 0xFF,0xFF,0x00,0x00,0x00,0x9A,0xCF,0x00   ; 08 Code32, Basis 0, 4 GB
    db 0xFF,0xFF,0x00,0x00,0x00,0x92,0xCF,0x00   ; 10 Daten32, Basis 0, 4 GB
    db 0xFF,0xFF,0x00,0x00,0x00,0x92,0xCF,0x01   ; 18 Daten32, Basis 0x01000000
gdt_ende:
gdtr:
    dw gdt_ende - gdt - 1
    dd 0

; Ab hier laeuft der Code mit CS=08 (Basis 0) an linearer Adresse Basis+pm32.
; Er darf deshalb keine absoluten Verweise auf eigene Marken enthalten.
bits 32
pm32:
    mov ax, 0x10
    mov ds, ax
    mov es, ax
    mov ss, ax
    mov esp, 0x00090000
    cld
    mov edi, 0x00300000     ; Verzeichnis + zwei Tabellen nullen
    xor eax, eax
    mov ecx, 3 * 1024
    rep stosd
    mov edi, 0x00301000     ; Tabelle 0: 0..4 MB identisch
    mov eax, 0x00000003
    mov ecx, 1024
.t0:
    stosd
    add eax, 0x1000
    loop .t0
    mov edi, 0x00302000     ; Tabelle 1: linear 0x01000000.. -> physisch 0x00200000..
    mov eax, 0x00200003
    mov ecx, 1024
.t1:
    stosd
    add eax, 0x1000
    loop .t1
    mov dword [0x00300000], 0x00301003         ; PDE 0
    mov dword [0x00300000 + 4 * 4], 0x00302003 ; PDE 4 (linear 0x01000000)
    mov eax, 0x00300000
    mov cr3, eax
    mov eax, cr0
    or eax, 0x80000000
    mov cr0, eax
    jmp .weiter
.weiter:
    mov dword [0x01000000], 0x50434D44         ; 'DMCP'
    mov dword [0x01000004], 0
.schleife:
    inc dword [0x01000004]
    jmp .schleife
