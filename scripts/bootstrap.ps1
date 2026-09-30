[CmdletBinding()]
param(
    [string]$GowinBin,
    [string]$RiscvBin,
    [switch]$SkipSmoke
)
$ErrorActionPreference = 'Stop'
$MiniRoot = Split-Path -Parent $PSScriptRoot
$MiniUv = (Get-Command uv -ErrorAction Stop).Source
$MiniVenv = Join-Path $MiniRoot '.venv'
$MiniPython = Join-Path $MiniVenv 'Scripts\python.exe'

function Invoke-MiniChecked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed (exit $LASTEXITCODE)." }
}

if (-not (Test-Path -LiteralPath $MiniPython)) {
    Invoke-MiniChecked -Executable $MiniUv -Arguments @('venv', '--python', '3.10.21', $MiniVenv)
}
Invoke-MiniChecked -Executable $MiniUv -Arguments @('pip', 'sync', '--python', $MiniPython, (Join-Path $MiniRoot 'requirements.lock'))
$MiniConfigureArgs = @((Join-Path $PSScriptRoot 'configure_tools.py'))
if ($GowinBin) { $MiniConfigureArgs += @('--gowin-bin', $GowinBin) }
if ($RiscvBin) { $MiniConfigureArgs += @('--riscv-bin', $RiscvBin) }
Invoke-MiniChecked -Executable $MiniPython -Arguments $MiniConfigureArgs
. (Join-Path $PSScriptRoot 'env.ps1')
Invoke-MiniChecked -Executable $MiniPython -Arguments @((Join-Path $PSScriptRoot 'doctor.py'))
if (-not $SkipSmoke) {
    Invoke-MiniChecked -Executable $MiniPython -Arguments @((Join-Path $PSScriptRoot 'build.py'))
}
