# Test programs

Small DOS programs with exactly known behavior for the end-to-end tests in `python/tests/`.
Build: `.\build.ps1` (needs NASM in PATH or in the environment variable `NASM`). The `.COM` and `.EXE` files are checked in.
The default output is a `.COM` file; a source with the line `; OUTPUT=EXE` becomes `<NAME>.EXE` (the source builds the MZ header itself).
The fixed addresses and contents are described in the header comment of each `.asm` file.

- `BATCH.COM`: test program for `run_batch` (stage 4): writes `ABTTY` + CRLF to stdout via INT 21h 02h/06h and INT 10h 0Eh, `ERR` + CRLF to stderr, creates `BATCH.OUT` and ends with errorlevel 7.
- `LE.EXE` (`le.asm`): test program for the LE base derivation (D27): a hand-built MZ with an LE header (2 objects: va `0x10000` size `0x3000`, va `0x20000` size `0x0A10`; entry va `0x10100`). An own real-mode handler for INT 31h returns fixed linear addresses for 0501h (without argument `0x300000`/`0x400000`/`0x420000`; `E`: one block `0x500000` for all; `N`: nothing fits). Prints `LE done`. The MZ file size is computed from label differences; the `align 4` before `code_end` is required (bin sections are 4-byte aligned), otherwise the MZ header is wrong.
- `LEBAD.EXE` (`lebad.asm`): like `LE.EXE`, but object count 0 in the LE header (`assignment "not_le"`).
- `LEEIP.EXE`, `LEVIELE.EXE`, `LEEXEC.EXE` (each a wrapper file around `le.asm` with `%define`, description in the header of `le.asm`): variants for the edge cases of the LE derivation (EIP outside the object, 257 requests, EXEC of another program).
- `OVERLAY.COM`: loads `EXITCODE.COM` as an overlay (INT 21h 4B03h) at segment `3000h`. `LOADONLY.COM`: loads `EXITCODE.COM` without starting it (INT 21h 4B01h).
- `TEXTCP.COM`: writes the character codes C9 CD 84 01 7F 00 directly into the text screen (B800:0640 = line 10) and waits for a key; check for `screen.text` (CP437 as Unicode).
- `ERRPROBE.COM`: raises error 2 (opening a missing file), starts `LE.EXE` and then `EXITCODE.COM` and stores INT 21h AX=5900h after each return at `PSP:00F0` / `PSP:00F2` (marker `PSP:00F4` = 5Ah); checks that no error code stays behind after returning from EXEC (LE header read); DOS resets it to 0 when the child ends (`src/dos/dos_execute.cpp:163`).
- `CALLSEXC.COM`: exception contexts without their own IRET for the call log (D29): nested INT 0 faults, IRET to the outer frame, call from `PSP:0200`, program end in the INT 0 handler; flow in the header of `callsexc.asm`.
- `CALLS32.COM`: a 32-bit call in protected mode for the call log (D29): waits for `PSP:03F1` != 0, switches to 32-bit PM (base 0, without paging), calls `add32` (`PSP:0280`, `ret 4`) and then writes `C32!` to linear `0x00500000`.
- `FUNCPM.COM`: stack switch for `func_run` (D31): like `CALLS32.COM` in 32-bit PM, then IRETD to ring 3; `ring3fn` (`PSP:02C0`) calls INT 30h, the ring 0 handler runs on the TSS stack (ESP0 `0x00070000`) and writes `0x11223344` to linear `0x00500010`; then `R3OK` to linear `0x00500000`.
- `FILEIO.COM`: known file accesses for the `file` event (D34): waits for `PSP:03F1` != 0, creates `FIO.DAT` (64 bytes 00..3F), reads 16 bytes from position 10h via `read` (`PSP:0280`) into `PSP:0400` and 8 bytes into `PSP:0410`, writes `FIO` to CON (no `file` event) and sets `PSP:03F0` = 1. In the test, start it with a temporary directory as drive C.
- `SCENE.COM`: test program for scenarios (D35): prints `READY`, waits for a key, stores the char at `PSP:0300`, prints `KEY <char>`, calls `done_fn` (`PSP:0200`, sets `PSP:0301` = 5Ah, prints `DONE`), then an HLT loop.
