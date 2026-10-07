param([string]$SocDir="build/opensbi/soc", [string]$OutputDir="build/opensbi/firmware", [string]$Source="build/vendor/opensbi")
$ErrorActionPreference="Stop"
$MiniRoot=Split-Path -Parent $PSScriptRoot
Push-Location $MiniRoot
try {
    & .venv/Scripts/python.exe -X utf8 scripts/opensbi_build.py --soc-dir $SocDir --output-dir $OutputDir --source $Source
    if($LASTEXITCODE){throw "OpenSBI preparation failed"}
    $MiniBuildPath=(Resolve-Path (Join-Path $OutputDir 'build.sh')).Path.Replace('\','/')
    $MiniLinuxPath='/mnt/'+$MiniBuildPath.Substring(0,1).ToLower()+$MiniBuildPath.Substring(2)
    & wsl -d archlinux -- sh $MiniLinuxPath
    if($LASTEXITCODE){throw "OpenSBI build failed; inspect build logs"}
    & .venv/Scripts/python.exe -X utf8 scripts/opensbi_build.py --soc-dir $SocDir --output-dir $OutputDir --source $Source --pack-only
    if($LASTEXITCODE){throw "OpenSBI packing failed"}
} finally {Pop-Location}
