; SPDX-License-Identifier: GPL-2.0-or-later
; ERRPROBE.COM - checks that after returning from EXEC (the emulator reads the LE header) no extended error code (INT 21h 59h) stays behind in the guest; DOS resets it to 0 when the child ends (src/dos/dos_execute.cpp:163).
;  1. INT 21h 3Dh opens "NIXDA.XYZ" (missing) -> known error 2; shrink the memory block (4Ah)
;  2. EXEC (4B00h) of "LE.EXE" (the emulator reads the LE header); then INT 21h AX=5900h BX=0: AX -> PSP:00F0 (word)
;  3. again 3Dh on "NIXDA.XYZ" (error 2), EXEC of "EXITCODE.COM" (no LE), 5900h: AX -> PSP:00F2 (word)
;  4. marker 5Ah at PSP:00F4, output "ERRPROBE done", exit with errorlevel 0
; Expectation (observed with the test programs): both words equal; value see test (test_31_le.py).
bits 16
org 0x100
start:
    mov sp, stack_end
    mov bx, 0x1000
    mov ah, 0x4A                ; ES = PSP since program start
    int 0x21
    mov [parameter + 4], cs
    mov [parameter + 8], cs
    mov [parameter + 12], cs

    call make_error
    mov dx, le_name
    call child
    mov ax, 0x5900
    xor bx, bx
    int 0x21
    mov [0xF0], ax

    call make_error
    mov dx, com_name
    call child
    mov ax, 0x5900
    xor bx, bx
    int 0x21
    mov [0xF2], ax

    mov byte [0xF4], 0x5A
    mov dx, text
    mov ah, 0x09
    int 0x21
    mov ax, 0x4C00
    int 0x21

make_error:
    mov ax, 0x3D00
    mov dx, missing_name
    int 0x21
    ret

child:                          ; DX = program name
    mov [old_sp], sp
    push cs
    pop es
    mov ax, 0x4B00
    mov bx, parameter
    int 0x21
    cli
    mov ax, cs
    mov ss, ax
    mov sp, [cs:old_sp]
    sti
    push cs
    pop ds
    ret

missing_name db 'NIXDA.XYZ', 0
le_name      db 'LE.EXE', 0
com_name     db 'EXITCODE.COM', 0
command_line db 0, 13
fcb          times 16 db 0
parameter    dw 0
             dw command_line, 0
             dw fcb, 0
             dw fcb, 0
text         db 'ERRPROBE done', 13, 10, '$'
old_sp       dw 0
align 2
stack        times 256 db 0
stack_end:
