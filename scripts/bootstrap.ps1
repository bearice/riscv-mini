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
$MiniPythonVersion = (Get-Content -LiteralPath (Join-Path $MiniRoot '.python-version') -Raw).Trim()

function Invoke-MiniChecked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed (exit $LASTEXITCODE)." }
}

if (Test-Path -LiteralPath $MiniPython) {
    $MiniCurrentPythonVersion = (& $MiniPython -c 'import platform; print(platform.python_version())' 2>$null | Select-Object -First 1).Trim()
} else {
    $MiniCurrentPythonVersion = ''
}
if ($MiniCurrentPythonVersion -ne $MiniPythonVersion) {
    Invoke-MiniChecked -Executable $MiniUv -Arguments @('venv', '--clear', '--python', $MiniPythonVersion, $MiniVenv)
}
# uv clones git dependencies with nested submodules; deep checkout paths exceed
# MAX_PATH unless git core.longpaths is enabled. Warn instead of failing obscurely.
$MiniLongPaths = (& git config --global --get core.longpaths) 2>$null
if ($LASTEXITCODE -ne 0 -or $MiniLongPaths -notin @('true', '1')) {
    Write-Warning 'git core.longpaths is not enabled; uv git dependency clones can fail with "Filename too long". Run: git config --global core.longpaths true'
}
Invoke-MiniChecked -Executable $MiniUv -Arguments @('pip', 'sync', '--python', $MiniPython, (Join-Path $MiniRoot 'requirements.lock'))
$MiniConfigureArgs = @((Join-Path $PSScriptRoot 'tools/configure_tools.py'))
if ($GowinBin) { $MiniConfigureArgs += @('--gowin-bin', $GowinBin) }
if ($RiscvBin) { $MiniConfigureArgs += @('--riscv-bin', $RiscvBin) }
Invoke-MiniChecked -Executable $MiniPython -Arguments $MiniConfigureArgs
. (Join-Path $PSScriptRoot 'env.ps1')
Invoke-MiniChecked -Executable $MiniPython -Arguments @((Join-Path $PSScriptRoot 'tools/doctor.py'))
if (-not $SkipSmoke) {
    # Smoke build uses the minimal profile: the lite core ships inside
    # pythondata-cpu-vexriscv, so a fresh checkout needs no generated CPU RTL.
    # The full profile uses the production MMU CPU in gateware/vendor/vexriscv.
    Invoke-MiniChecked -Executable $MiniPython -Arguments @(
        (Join-Path $PSScriptRoot 'mini.py'), 'build', 'system', '--profile', 'minimal',
        '--purpose', 'bootstrap-smoke')
}
