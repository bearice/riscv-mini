"""Prepare a pinned RV32 Linux + freestanding initramfs build in WSL ext4.

Run the emitted build.sh through PowerShell's wsl entrypoint, then --record.
No FPGA programming or SD/Flash writes are performed by this builder.
"""
import hashlib
import json
import shlex
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERSION = '6.12.112'
SHA256 = '164dc9d1f6c93c61a15e1f071c48379b467f2b17c469cce7223471968208ed03'


def wsl_path(path):
    path = Path(path).resolve().as_posix()
    if os.name != 'nt':
        return path
    return '/mnt/' + path[0].lower() + path[2:]


def build(args, run=None):
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if args.record:
        files = ('Image', 'vmlinux', 'config', 'init', 'System.map')
        record = dict(version=VERSION, source_sha256=SHA256,
                      isa='rv32ima_zicsr_zifencei', abi='ilp32',
                      load_address='0x02000000', console='liteuart0 (native ttyLXU0; early SBI DBCN)',
                      artifacts={name: hashlib.sha256((out / name).read_bytes()).hexdigest()
                                 for name in files}, board_verified=False, board_devices_verified=False)
        inputs = sorted((ROOT / 'firmware/linux').rglob('*')) + [Path(__file__)]
        record['port_inputs'] = {str(path.relative_to(ROOT)).replace('\\', '/'): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in inputs if path.is_file()}
        if args.board_evidence:
            evidence = json.loads(args.board_evidence.read_text())
            inputs = next((step for step in evidence['steps'] if step['name'] == 'linux_inputs'), {})
            acceptance = next((step for step in evidence['steps'] if step['name'] in ('linux_userspace','linux_busybox')), {})
            if evidence.get('passed') is not True or acceptance.get('passed') is not True or inputs.get('kernel_sha256') != record['artifacts']['Image']:
                raise ValueError('Board receipt does not qualify this kernel Image')
            record.update(board_verified=True, board_evidence=str(args.board_evidence.resolve()),
                          userspace_profile=acceptance['name'],
                          board_evidence_sha256=hashlib.sha256(args.board_evidence.read_bytes()).hexdigest(),
                          selected_build=evidence['selected_build'], os_image_sha256=inputs['os_image_sha256'])
            devices = next((step for step in evidence['steps'] if step['name'] == 'linux_devices'), {})
            record['board_devices_verified'] = devices.get('passed') is True
        (out / 'validation.json').write_text(json.dumps(record, indent=2) + '\n')
        return
    port = shlex.quote(wsl_path(ROOT / 'firmware/linux'))
    output = shlex.quote(wsl_path(out))
    script = f'''#!/bin/bash
set -euo pipefail
BASE="$HOME/riscv-mini-linux"
SRC="$BASE/linux-{VERSION}"
OBJ="$BASE/obj-minimal"
PORT={port}
OUT={output}
mkdir -p "$BASE" "$OUT"
cd "$BASE"
if [ ! -f linux-{VERSION}.tar.xz ]; then
    curl -fL --retry 3 -o linux-{VERSION}.tar.xz https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-{VERSION}.tar.xz
fi
echo '{SHA256}  linux-{VERSION}.tar.xz' | sha256sum -c -
if [ ! -d "$SRC" ]; then tar -xf linux-{VERSION}.tar.xz; fi
mkdir -p "$OBJ"
python "$PORT/install.py" "$SRC"
cp "$PORT/riscv_mini_fb.c" "$SRC/drivers/video/fbdev/riscv_mini_fb.c"
if ! grep -q '^config FB_RISCV_MINI$' "$SRC/drivers/video/fbdev/Kconfig"; then
cat >> "$SRC/drivers/video/fbdev/Kconfig" <<'EOF'

config FB_RISCV_MINI
    bool "riscv-mini coherent RGB LCD framebuffer"
    depends on FB && OF && RISCV
    select FB_DMAMEM_HELPERS
EOF
echo 'obj-$(CONFIG_FB_RISCV_MINI) += riscv_mini_fb.o' >> "$SRC/drivers/video/fbdev/Makefile"
fi
make -C "$SRC" ARCH=riscv O="$OBJ" headers
mkdir -p "$OBJ/uapi/include"
cp -a "$OBJ/usr/include/." "$OBJ/uapi/include/"
clang --target=riscv32-unknown-linux-gnu -march=rv32ima_zicsr_zifencei -mabi=ilp32 -Os -Wall -Wextra -Werror -fno-builtin -fno-stack-protector -fno-pic -ffreestanding -nostdlib -static -fuse-ld=lld -I "$PORT/uapi-shim" -isystem "$OBJ/uapi/include" -Wl,-e,_start -Wl,--build-id=none "$PORT/init.c" -o "$OBJ/mini-init"
cat > "$OBJ/initramfs.list" <<EOF
dir /dev 755 0 0
nod /dev/console 600 0 0 c 5 1
nod /dev/fb0 600 0 0 c 29 0
dir /proc 755 0 0
dir /sys 755 0 0
dir /mnt 755 0 0
file /init $OBJ/mini-init 755 0 0
EOF
cd "$SRC"
make ARCH=riscv LLVM=1 O="$OBJ" KCONFIG_ALLCONFIG="$PORT/mini.config" allnoconfig
scripts/config --file "$OBJ/.config" --set-str INITRAMFS_SOURCE "$OBJ/initramfs.list"
make ARCH=riscv LLVM=1 O="$OBJ" olddefconfig
grep -q '^CONFIG_ARCH_RV32I=y$' "$OBJ/.config"
grep -q '^CONFIG_MMU=y$' "$OBJ/.config"
grep -q '^CONFIG_HVC_RISCV_SBI=y$' "$OBJ/.config"
grep -q '^CONFIG_FB_RISCV_MINI=y$' "$OBJ/.config"
for symbol in MINI_IRQ SERIAL_LITEUART_CONSOLE MMC_RISCV_MINI NET_RISCV_MINI BOARD_RISCV_MINI SPI_RISCV_MINI USB_RISCV_MINI USB_HID HID_GENERIC USB_STORAGE BLK_DEV_SD INPUT_EVDEV GPIO_CDEV SND_RISCV_MINI SND_PCM MTD_RISCV_MINI HIGH_RES_TIMERS PROC_SYSCTL UNIX98_PTYS; do
    grep -q "^CONFIG_$symbol=y$" "$OBJ/.config"
done
! grep -Eq '^CONFIG_(FPU|RISCV_ISA_C|SMP|FB_PANEL_RISCV_MINI)=y$' "$OBJ/.config"
make -j8 ARCH=riscv LLVM=1 O="$OBJ" Image
llvm-nm "$OBJ/vmlinux" > "$OBJ/mini-symbols.txt"
for symbol in mini_usb_probe mini_net_probe mini_sd_probe mini_audio_probe mini_flash_probe liteuart_probe; do
    grep -q " $symbol$" "$OBJ/mini-symbols.txt"
done
! grep -q " panel_probe$" "$OBJ/mini-symbols.txt"
cp "$OBJ/arch/riscv/boot/Image" "$OBJ/vmlinux" "$OBJ/System.map" "$OUT/"
cp "$OBJ/mini-init" "$OUT/init"
cp "$OBJ/.config" "$OUT/config"
clang --version > "$OUT/toolchain.txt"
llvm-readelf -h -A "$OBJ/mini-init" > "$OUT/init-elf.txt"
sha256sum "$OUT/Image"
'''
    (out / 'build.sh').write_text(script, newline='\n')
    print('Prepared', out / 'build.sh')
