# Assembliert alle Testprogramme zu .COM-Dateien (Quelle mit Zeile "; AUSGABE=EXE": .EXE).
$ErrorActionPreference = 'Stop'
$nasm = 'D:\Dev-Tools\nasm\nasm.exe'
Get-ChildItem "$PSScriptRoot\*.asm" | ForEach-Object {
    $ext = if (Select-String -Path $_.FullName -Pattern '^; AUSGABE=EXE' -Quiet) { '.EXE' } else { '.COM' }
    $out = Join-Path $PSScriptRoot ($_.BaseName.ToUpper() + $ext)
    & $nasm -f bin -i "$PSScriptRoot\" $_.FullName -o $out -l ([IO.Path]::ChangeExtension($out, '.lst'))
    if ($LASTEXITCODE -ne 0) { throw "NASM-Fehler in $($_.Name)" }
    "{0,-12} {1,6} Byte" -f (Split-Path $out -Leaf), (Get-Item $out).Length
}
