"""Build U-Boot v2026.07 as the riscv-mini S-mode payload.

The build runs inside the WSL archlinux toolchain (riscv64-elf-gcc) on a
native ext4 tree, which is the verified environment; building on a
Windows-mounted path is far too slow. The port is fully described under
firmware/uboot/ and applied to the pinned source tree automatically,
mirroring scripts/opensbi_build.py's platform-copy pattern:

  * Added files (board dir, top-level defconfig, include/configs header,
    build-time fallback dts, and the own serial/Ethernet/MMC/USB drivers)
    are copied into the tree from firmware/uboot/, which mirrors the U-Boot
    layout one-for-one.
  * Edits to existing upstream files (arch/riscv Kconfig/Makefile/config.mk/
    dts Makefile/cache.c, common/board_r.c initcall order, drivers/*/Kconfig
    and Makefile wiring, tools/prelink-riscv.inc) live in
    firmware/uboot/uboot-port.patch and are applied with `git apply`.

The result is reproducible from a clean clone: no manual tree edits are
required. The Ethernet driver reuses upstream's LITEETH Kconfig/Makefile
entry and only replaces drivers/net/liteeth.c with our 32-bit-CSR version.
The produced u-boot.bin is copied back to the Windows build tree for
scripts/opensbi_build.py to pack.
"""
import argparse, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
REV = "v2026.07"
DISTRO = "archlinux"
# WSL-native (ext4) build tree; never build on a /mnt/c path.
WSL_SRC = "$HOME/uboot-port"
WSL_OBJ = "$HOME/uboot-port-obj"
CROSS = "riscv64-elf-"
LIBGCC = "-L /usr/lib/gcc/riscv64-elf/16.2.0/rv32im/ilp32 -lgcc"
# LLD supports PIE; runtime relocation must also move command/data pointers.
ARCH_FLAGS = "-march=rv32ima_zicsr_zifencei -mabi=ilp32"

# (path under firmware/uboot/, destination under the U-Boot tree)
COPIES = [
    ("board/riscv-mini", "board/riscv-mini"),
    ("configs/riscv_mini_defconfig", "configs/riscv_mini_defconfig"),
    ("include/configs/riscv_mini.h", "include/configs/riscv_mini.h"),
    ("arch/riscv/dts/riscv-mini.dts", "arch/riscv/dts/riscv-mini.dts"),
    ("drivers/serial/serial_liteuart32.c", "drivers/serial/serial_liteuart32.c"),
    ("drivers/net/liteeth.c", "drivers/net/liteeth.c"),
    ("drivers/mmc/litesd.c", "drivers/mmc/litesd.c"),
    ("drivers/usb/host/liteusb.c", "drivers/usb/host/liteusb.c"),
]

def to_wsl(p):
    """Windows path -> WSL /mnt/<drive>/... path."""
    s = str(Path(p).resolve()).replace("\\", "/")
    return "/mnt/" + s[0].lower() + s[2:]

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", default=WSL_SRC,
                        help="WSL-native source path (default $HOME/uboot-port)")
    parser.add_argument("--obj", default=WSL_OBJ,
                        help="WSL-native build output path (default $HOME/uboot-port-obj)")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/uboot/firmware",
                        help="Windows dir to receive u-boot.bin")
    parser.add_argument("--prepare-only", action="store_true", help="Write build.sh for direct WSL execution")
    a = parser.parse_args()
    out = a.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    port_wsl = to_wsl(ROOT / "firmware/uboot")
    out_wsl = to_wsl(out)
    patch = out / "uboot-port.patch"
    patch.write_bytes((ROOT / "firmware/uboot/uboot-port.patch").read_bytes().replace(b'\r\n', b'\n'))

    copy_cmds = []
    for rel_src, rel_dst in COPIES:
        s = f"{port_wsl}/{rel_src}"; d = rel_dst
        if (ROOT / "firmware/uboot" / rel_src).is_dir():
            copy_cmds.append(f'mkdir -p "$(dirname {d})"; cp -a {s}/. {d}/')
        else:
            copy_cmds.append(f'mkdir -p "$(dirname {d})"; cp -a {s} {d}')

    script = f"""
set -euo pipefail
SRC="{a.source}"; OBJ="{a.obj}"; PORT="{port_wsl}"; OUT="{out_wsl}"
PATCH="$OUT/uboot-port.patch"
if [ ! -d "$SRC" ]; then
  git clone --depth 1 --branch {REV} https://github.com/u-boot/u-boot.git "$SRC"
fi
cd "$SRC"
tag=$(git describe --tags --abbrev=0)
[ "$tag" = "{REV}" ] || {{ echo "U-Boot source tag $tag differs from pinned {REV}" >&2; exit 1; }}
{chr(10).join(copy_cmds)}
if git apply --reverse --check "$PATCH" 2>/dev/null; then
  echo "uboot-port.patch already applied"
else
  git apply --reverse "$PATCH" 2>/dev/null || true
  git apply "$PATCH"
  echo "uboot-port.patch applied"
fi
if ! grep -q RISCV-MINI MAINTAINERS; then
  printf 'RISCV-MINI\\nM:\\tbearice\\nS:\\tgithub.com/bearice/riscv-mini\\nF:\\tboard/riscv-mini/\\n\\n' >> MAINTAINERS
fi
make O="$OBJ" riscv_mini_defconfig
make -j8 O="$OBJ" CROSS_COMPILE={CROSS} LD=ld.lld LDFLAGS_u-boot='--gc-sections -static -pie -Ttext=$(CONFIG_TEXT_BASE)' PLATFORM_LIBGCC="{LIBGCC}" PLATFORM_CFLAGS='{ARCH_FLAGS}'
mkdir -p "$OUT"
cp "$OBJ/u-boot.bin" "$OUT/u-boot.bin"
cp "$OBJ/u-boot" "$OUT/u-boot.elf"
cp "$OBJ/.config" "$OUT/config"
echo "U-Boot payload: $OUT/u-boot.bin $(stat -c%s "$OUT/u-boot.bin") bytes"
"""
    (out / "build.sh").write_text(script, newline="\n")
    if a.prepare_only:
        print("Prepared", out / "build.sh")
        return
    subprocess.run(["wsl", "-d", DISTRO, "-e", "bash", "-lc", script], check=True)

if __name__ == "__main__":
    main()
