# Assembles all test programs to .COM files (a source with the line "; OUTPUT=EXE" becomes .EXE).
$ErrorActionPreference = 'Stop'
$nasm = if ($env:NASM) { $env:NASM } else { 'nasm' }
if (-not (Get-Command $nasm -ErrorAction SilentlyContinue)) { throw "NASM not found (set NASM or add it to PATH): $nasm" }
Get-ChildItem "$PSScriptRoot\*.asm" | ForEach-Object {
    $ext = if (Select-String -Path $_.FullName -Pattern '^; OUTPUT=EXE' -Quiet) { '.EXE' } else { '.COM' }
    $out = Join-Path $PSScriptRoot ($_.BaseName.ToUpper() + $ext)
    & $nasm -f bin -i "$PSScriptRoot/" $_.FullName -o $out -l ([IO.Path]::ChangeExtension($out, '.lst'))
    if ($LASTEXITCODE -ne 0) { throw "NASM error in $($_.Name)" }
    "{0,-12} {1,6} bytes" -f (Split-Path $out -Leaf), (Get-Item $out).Length
}
