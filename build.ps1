# Builds dosbox-lab (Release SDL2, x64) with the agent and DMCP extension.
# MSBuild is taken from $env:MSBUILD, else found via vswhere, else from PATH.
param([switch]$Clean)
$ErrorActionPreference = 'Stop'
$msbuild = $env:MSBUILD
if (-not $msbuild) {
    $vswhere = Join-Path ([Environment]::GetFolderPath('ProgramFilesX86')) 'Microsoft Visual Studio\Installer\vswhere.exe'
    if (Test-Path $vswhere) {
        $msbuild = & $vswhere -latest -products * -requires Microsoft.Component.MSBuild -find 'MSBuild\**\Bin\MSBuild.exe' | Select-Object -First 1
    }
}
if (-not $msbuild) { $cmd = Get-Command MSBuild.exe -ErrorAction SilentlyContinue; if ($cmd) { $msbuild = $cmd.Source } }
if (-not $msbuild -or -not (Test-Path $msbuild)) { throw "MSBuild not found (set the MSBUILD environment variable): $msbuild" }
# cl.exe appends CL to every command line; this switches on the agent code without touching the project defines.
$env:CL = '/DC_DEBUG=1 /DC_DOSBOX_AGENT=1 /DC_DMCP=1'
$target = if ($Clean) { 'Rebuild' } else { 'Build' }
& $msbuild "$PSScriptRoot\vs\dosbox-x.sln" /m /t:$target "/p:Configuration=Release SDL2" /p:Platform=x64 /v:minimal /nologo
if ($LASTEXITCODE -ne 0) { throw "Build failed (exit $LASTEXITCODE)" }
Get-Item "$PSScriptRoot\bin\x64\Release SDL2\dosbox-lab.exe"
