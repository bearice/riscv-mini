# Dot-source this file. Changes affect only this PowerShell process.
$MiniRoot = Split-Path -Parent $PSScriptRoot
$MiniPython = Join-Path $MiniRoot '.venv\Scripts\python.exe'
$MiniToolsFile = Join-Path $MiniRoot '.tools.local.json'
if (-not (Test-Path -LiteralPath $MiniToolsFile)) {
    throw 'Local tools are not configured. Run scripts\bootstrap.ps1 first.'
}
$MiniTools = Get-Content -LiteralPath $MiniToolsFile -Raw | ConvertFrom-Json
$MiniBins = @((Join-Path $MiniRoot '.venv\Scripts'))
foreach ($MiniToolName in @('gcc', 'gowin', 'programmer', 'make', 'openfpgaloader')) {
    $MiniToolPath = $MiniTools.$MiniToolName
    if ($MiniToolPath) { $MiniBins += Split-Path -Parent $MiniToolPath }
}
$env:PATH = ($MiniBins -join ';') + ';' + $env:PATH
$env:PYTHONUTF8 = '1'
$env:PYTHONUNBUFFERED = '1'
$env:LITEX_ENV_CC_TRIPLE = 'riscv-none-elf'
