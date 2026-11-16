#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Assembles all test programs (Linux/macOS counterpart of build.ps1): NAME.COM, or NAME.EXE for a source with
# the line "; OUTPUT=EXE". NASM from $NASM or PATH.
set -e
cd "$(dirname "$0")"
nasm=${NASM:-nasm}
command -v "$nasm" >/dev/null 2>&1 || { echo "NASM not found (set NASM or add it to PATH): $nasm" >&2; exit 1; }
for src in *.asm; do
    name=$(basename "$src" .asm | tr '[:lower:]' '[:upper:]')
    if grep -q '^; OUTPUT=EXE' "$src"; then ext=EXE; else ext=COM; fi
    "$nasm" -f bin -i ./ "$src" -o "$name.$ext" -l "$name.lst"
    printf '%-12s %6s bytes\n' "$name.$ext" "$(wc -c < "$name.$ext" | tr -d ' ')"
done
