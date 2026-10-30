# Testprogramme

Kleine DOS-Programme mit genau bekanntem Verhalten für die End-to-End-Tests in `python/tests/`.
Bauen: `.\build.ps1` (braucht NASM unter `D:\Dev-Tools\nasm`). Die `.COM`- und `.EXE`-Dateien sind eingecheckt.
Standard ist eine `.COM`-Datei; eine Quelle mit der Zeile `; AUSGABE=EXE` wird zu `<NAME>.EXE` (das MZ baut die Quelle selbst).
Die festen Adressen und Inhalte stehen jeweils im Kopfkommentar der `.asm`-Datei.

- `BATCH.COM`: Testprogramm für `run_batch` (Stufe 4): schreibt `ABTTY` + CRLF über INT 21h 02h/06h und INT 10h 0Eh nach stdout, `ERR` + CRLF nach stderr, legt `BATCH.OUT` an und endet mit Errorlevel 7.
- `LE.EXE` (`le.asm`): Testprogramm für die LE-Basis-Ableitung (D27): von Hand gebautes MZ mit LE-Kopf (2 Objekte: va `0x10000` Größe `0x3000`, va `0x20000` Größe `0x0A10`; Einsprung va `0x10100`). Ein eigener Real-Mode-Handler für INT 31h liefert bei 0501h feste lineare Adressen (ohne Argument `0x300000`/`0x400000`/`0x420000`; `E`: ein Block `0x500000` für alle; `N`: nichts passt). Gibt `LE fertig` aus. Die MZ-Dateigröße wird aus Labeldifferenzen berechnet; das `align 4` vor `code_ende` ist nötig (bin-Sektionen sind auf 4 ausgerichtet), sonst stimmt der MZ-Kopf nicht.
- `LEBAD.EXE` (`lebad.asm`): wie `LE.EXE`, aber Objektzahl 0 im LE-Kopf (`zuordnung "kein_le"`).
- `LEEIP.EXE`, `LEVIELE.EXE`, `LEEXEC.EXE` (je eine Wrapper-Datei um `le.asm` mit `%define`, Beschreibung im Kopf von `le.asm`): Varianten für die Grenzfälle der LE-Ableitung (EIP außerhalb des Objekts, 257 Anforderungen, EXEC eines anderen Programms).
- `OVERLAY.COM`: lädt `EXITCODE.COM` als Overlay (INT 21h 4B03h) nach Segment `3000h`. `LOADONLY.COM`: lädt `EXITCODE.COM` ohne Start (INT 21h 4B01h).
- `TEXTCP.COM`: schreibt die Zeichencodes C9 CD 84 01 7F 00 direkt in den Textbildschirm (B800:0640 = Zeile 10) und wartet auf eine Taste; Prüfung von `screen.text` (CP437 als Unicode).
- `ERRPROBE.COM`: erzeugt Fehler 2 (Öffnen einer fehlenden Datei), startet `LE.EXE` und dann `EXITCODE.COM` und legt jeweils INT 21h AX=5900h nach der Rückkehr bei `PSP:00F0` / `PSP:00F2` ab (Marke `PSP:00F4` = 5Ah); Prüfung, dass nach der Rückkehr aus EXEC (LE-Kopf-Lesen) kein Fehlercode zurückbleibt; DOS setzt ihn beim Kindende auf 0 (`src/dos/dos_execute.cpp:163`).
- `CALLSEXC.COM`: Ausnahme-Kontexte ohne eigenes IRET für das Aufrufprotokoll (D29): verschachtelte INT-0-Fehler, IRET zum äußeren Rahmen, Aufruf von `PSP:0200`, Programmende im INT-0-Handler; Ablauf im Kopf von `callsexc.asm`.
- `CALLS32.COM`: ein 32-Bit-Aufruf im Protected Mode für das Aufrufprotokoll (D29): wartet auf `PSP:03F1` != 0, schaltet in 32-Bit-PM (Basis 0, ohne Paging), ruft `add32` (`PSP:0280`, `ret 4`) und schreibt danach `C32!` nach linear `0x00500000`.
- `FUNCPM.COM`: Stackwechsel für `func_run` (D31): wie `CALLS32.COM` in 32-Bit-PM, dann per IRETD nach Ring 3; `ring3fn` (`PSP:02C0`) ruft INT 30h, der Ring-0-Handler läuft auf dem TSS-Stack (ESP0 `0x00070000`) und schreibt `0x11223344` nach linear `0x00500010`; danach `R3OK` nach linear `0x00500000`.
- `FILEIO.COM`: bekannte Dateizugriffe für das Ereignis `datei` (D34): wartet auf `PSP:03F1` != 0, legt `FIO.DAT` an (64 Byte 00..3F), liest ab Position 10h 16 Byte über `lesen` (`PSP:0280`) nach `PSP:0400` und 8 Byte nach `PSP:0410`, schreibt `FIO` an CON (kein `datei`-Ereignis) und setzt `PSP:03F0` = 1. Im Test mit einem temporären Verzeichnis als Laufwerk C starten.
- `SZENE.COM`: Testprogramm für Szenarien (D35): gibt `BEREIT` aus, wartet auf eine Taste, legt das Zeichen nach `PSP:0300`, gibt `TASTE <zeichen>` aus, ruft `fertig_fn` (`PSP:0200`, setzt `PSP:0301` = 5Ah, gibt `FERTIG` aus), dann HLT-Schleife.
