param(
    [Parameter(Mandatory=$true)][string]$StopFile,
    [Parameter(Mandatory=$true)][string]$StateFile
)
$ErrorActionPreference = 'Stop'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class MiniWakeRequest {
    [DllImport("kernel32.dll", SetLastError=true)]
    public static extern uint SetThreadExecutionState(uint flags);
}
'@
$TaskContinuous = [uint32]2147483648
try {
    $TaskResult = [MiniWakeRequest]::SetThreadExecutionState($TaskContinuous -bor 1)
    if ($TaskResult -eq 0) { throw 'SetThreadExecutionState failed' }
    @{ pid=$PID; active=$true; started=(Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding utf8
    while (-not (Test-Path -LiteralPath $StopFile)) {
        [System.Threading.Thread]::Sleep(1000)
    }
}
finally {
    [void][MiniWakeRequest]::SetThreadExecutionState($TaskContinuous)
    @{ pid=$PID; active=$false; stopped=(Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -LiteralPath $StateFile -Encoding utf8
}
