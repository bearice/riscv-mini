# Stable task entry point; always use this repository's interpreter and cwd.
$MiniRoot = $PSScriptRoot
$MiniInterpreter = Join-Path $MiniRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $MiniInterpreter)) {
    Write-Error 'Repository Python is missing. Run scripts\bootstrap.ps1 first.'
    exit 1
}
Push-Location -LiteralPath $MiniRoot
try {
    & $MiniInterpreter (Join-Path $MiniRoot 'scripts\mini.py') @args
    $MiniResult = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $MiniResult
