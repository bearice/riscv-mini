param(
    [string]$SocDir="build/releases/system/v0.9.1/full-rv32ima-rom0k-l24k",
    [string]$OutputDir="build/linux",
    [switch]$WithBootstrap
)
$ErrorActionPreference="Stop"
$MiniRoot=Split-Path -Parent $PSScriptRoot
function Invoke-MiniPython {
    & .venv/Scripts/python.exe -X utf8 @args
    if($LASTEXITCODE){throw "Python build stage failed ($LASTEXITCODE)"}
}
function Invoke-MiniWsl([string]$Script, [string]$Log) {
    $MiniPath=(Resolve-Path $Script).Path.Replace('\','/')
    $MiniLinux='/mnt/'+$MiniPath.Substring(0,1).ToLower()+$MiniPath.Substring(2)
    & wsl -d archlinux -e bash $MiniLinux *> $Log
    if($LASTEXITCODE){throw "WSL build failed ($LASTEXITCODE); inspect $Log"}
}
Push-Location $MiniRoot
try {
    Invoke-MiniPython scripts/linux_build.py --output-dir "$OutputDir/minimal"
    Invoke-MiniWsl "$OutputDir/minimal/build.sh" "$OutputDir/minimal/build.log"
    Invoke-MiniPython scripts/linux_build.py --output-dir "$OutputDir/minimal" --record
    if($WithBootstrap) {
        Invoke-MiniPython scripts/uboot_build.py --prepare-only --without-video --soc-dir $SocDir `
            --source '$HOME/riscv-mini-linux/uboot' --obj '$HOME/riscv-mini-linux/uboot-obj' `
            --output-dir "$OutputDir/uboot"
        Invoke-MiniWsl "$OutputDir/uboot/build.sh" "$OutputDir/uboot/build.log"
        Invoke-MiniPython scripts/opensbi_build.py --soc-dir $SocDir --output-dir "$OutputDir/sbi" `
            --payload-path "$OutputDir/uboot/u-boot.bin" --osb-name UBOOT.OSB
        Invoke-MiniWsl "$OutputDir/sbi/build.sh" "$OutputDir/sbi/build.log"
        Invoke-MiniPython scripts/opensbi_build.py --soc-dir $SocDir --output-dir "$OutputDir/sbi" `
            --pack-only --osb-name UBOOT.OSB
    }
} finally {Pop-Location}
