param(
    [string]$OutputDir = 'build/linux/busybox',
    [switch]$WithBootstrap,
    [switch]$RootfsOnly,
    [string]$SocDir = 'build/releases/system/v0.9.1/full-rv32ima-rom0k-l24k'
)
$ErrorActionPreference = 'Stop'
$repo = Split-Path $PSScriptRoot -Parent
Push-Location $repo
try {
    $prepareArgs = @('--output-dir', $OutputDir, '--prepare-only')
    if ($RootfsOnly) { $prepareArgs += '--rootfs-only' }
    & .venv/Scripts/python.exe -X utf8 scripts/busybox_build.py @prepareArgs
    if ($LASTEXITCODE -ne 0) { throw 'BusyBox preparation failed' }
    $absolute = (Resolve-Path $OutputDir).Path.Replace('\','/')
    $linuxPath = '/mnt/' + $absolute.Substring(0,1).ToLower() + $absolute.Substring(2)
    if ($RootfsOnly) {
        & wsl -d archlinux -- bash "$linuxPath/build.sh" --package-only *> "$OutputDir/rootfs-build.log"
    } else {
        & wsl -d archlinux -- bash "$linuxPath/build.sh" *> "$OutputDir/build.log"
    }
    if ($LASTEXITCODE -ne 0) { throw "BusyBox compilation failed; see $OutputDir/build.log" }
    & .venv/Scripts/python.exe -X utf8 scripts/busybox_build.py --output-dir $OutputDir --record
    if ($LASTEXITCODE -ne 0) { throw 'BusyBox packaging failed' }
    if ($WithBootstrap) {
        & .venv/Scripts/python.exe -X utf8 scripts/uboot_build.py --prepare-only --without-video --soc-dir $SocDir `
            --source '$HOME/riscv-mini-linux/uboot' --obj '$HOME/riscv-mini-linux/uboot-obj' --output-dir "$OutputDir/uboot"
        if ($LASTEXITCODE -ne 0) { throw 'U-Boot preparation failed' }
        & wsl -d archlinux -- bash "$linuxPath/uboot/build.sh" *> "$OutputDir/uboot/build.log"
        if ($LASTEXITCODE -ne 0) { throw 'U-Boot compilation failed' }
        & .venv/Scripts/python.exe -X utf8 scripts/opensbi_build.py --soc-dir $SocDir --output-dir "$OutputDir/sbi" `
            --payload-path "$OutputDir/uboot/u-boot.bin" --osb-name UBOOT.OSB
        if ($LASTEXITCODE -ne 0) { throw 'OpenSBI preparation failed' }
        & wsl -d archlinux -- bash "$linuxPath/sbi/build.sh" *> "$OutputDir/sbi/build.log"
        if ($LASTEXITCODE -ne 0) { throw 'OpenSBI compilation failed' }
        & .venv/Scripts/python.exe -X utf8 scripts/opensbi_build.py --soc-dir $SocDir --output-dir "$OutputDir/sbi" --pack-only --osb-name UBOOT.OSB
        if ($LASTEXITCODE -ne 0) { throw 'Bootstrap packaging failed' }
    }
} finally { Pop-Location }
