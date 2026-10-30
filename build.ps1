# Baut den DOSBOX-MCP-Fork (Release SDL2, x64) mit Agent- und DMCP-Erweiterung.
param([switch]$Clean)
$ErrorActionPreference = 'Stop'
$msbuild = 'C:\Program Files (x86)\Microsoft Visual Studio\2019\BuildTools\MSBuild\Current\Bin\MSBuild.exe'
if (-not (Test-Path $msbuild)) { throw "MSBuild nicht gefunden: $msbuild" }
# CL wird von cl.exe an jede Kommandozeile angehängt; so bleibt vs/dosbox-x.vcxproj unverändert.
$env:CL = '/DC_DEBUG=1 /DC_DOSBOX_AGENT=1 /DC_DMCP=1'
$target = if ($Clean) { 'Rebuild' } else { 'Build' }
& $msbuild "$PSScriptRoot\vs\dosbox-x.sln" /m /t:$target "/p:Configuration=Release SDL2" /p:Platform=x64 /v:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw "Build fehlgeschlagen (Exit $LASTEXITCODE)" }
Get-Item "$PSScriptRoot\bin\x64\Release SDL2\dosbox-x.exe"
