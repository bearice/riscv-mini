"""Build U-Boot v2026.07 as the riscv-mini S-mode payload (Windows toolchain).

Host tools come from MSYS2 (make/gcc/python/dtc); the target compiler is the
xPack riscv-none-elf GCC. Board files and the LiteUART32 serial driver live
in firmware/uboot/ and are copied into the pinned source tree, mirroring
scripts/opensbi_build.py's platform-copy pattern.
"""
import argparse, os, shutil, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
REV = "v2026.07"
MSYS = Path(r"C:\msys64")
def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "build/vendor/uboot")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "build/uboot/firmware")
    parser.add_argument("--toolchain-prefix", default="C:/xpack-riscv-none-elf-gcc-15.2.0-1/bin/riscv-none-elf-")
    a = parser.parse_args()
    src = a.source.resolve(); out = a.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    if not src.exists():
        subprocess.run(["git", "clone", "--depth", "1", "--branch", REV,
                        "https://github.com/u-boot/u-boot.git", str(src)], check=True)
    tag = subprocess.check_output(["git", "-C", str(src), "describe", "--tags", "--abbrev=0"],
                                  text=True).strip()
    if tag != REV:
        raise ValueError(f"U-Boot source tag {tag} differs from pinned {REV}")
    port = ROOT / "firmware/uboot"
    shutil.copytree(port / "board/riscv-mini", src / "board/riscv-mini", dirs_exist_ok=True)
    maintainers = src / "MAINTAINERS"
    entry = "RISCV-MINI\nM:\tbearice\nS:\tgithub.com/bearice/riscv-mini\nF:\tboard/riscv-mini/\n\n"
    text = maintainers.read_text()
    if "RISCV-MINI" not in text:
        maintainers.write_text(text + entry)
    shutil.copy(port / "drivers/serial/serial_liteuart32.c", src / "drivers/serial/serial_liteuart32.c")
    mk = src / "drivers/serial/Makefile"
    if "LITEUART32" not in mk.read_text():
        mk.write_text(mk.read_text().replace(
            "obj-$(CONFIG_BCM6345_SERIAL) += serial_bcm6345.o",
            "obj-$(CONFIG_BCM6345_SERIAL) += serial_bcm6345.o\nobj-$(CONFIG_LITEUART32_SERIAL) += serial_liteuart32.o"))
    kc = src / "drivers/serial/Kconfig"
    if "LITEUART32_SERIAL" not in kc.read_text():
        block = ('config LITEUART32_SERIAL\n\tbool "LiteX CSR LiteUART (32-bit registers)"\n'
                 '\tdepends on SERIAL\n\thelp\n\t  Select this to enable the LiteX CSR LiteUART variant with\n'
                 '\t  32-bit-wide registers used by the riscv-mini TangPrimer 20K SoC.\n\n')
        kc.write_text(kc.read_text().replace("if SERIAL\n", "if SERIAL\n\n" + block, 1))
    # bash -l rebuilds PATH from scratch, so export the toolchain paths
    # inside the command in MSYS POSIX form.
    def posix(p):
        s = Path(p).as_posix()
        return "/" + s[0].lower() + s[2:]
    # U-Boot's Makefile errors out when abs_srctree contains a colon (every
    # Windows drive path does). Run make inside the MSYS2 POSIX layer, where
    # paths are /c/... and colon-free.
    toolchain_bin = str(Path(a.toolchain_prefix).parent)
    msys_path = ":".join([posix(MSYS / "ucrt64" / "bin"), posix(MSYS / "usr" / "bin"), posix(toolchain_bin)])
    out_rel = os.path.relpath(out, src).replace("\\", "/")
    prefix = Path(a.toolchain_prefix).name
    bash = str(MSYS / "usr" / "bin" / "bash.exe")
    # Native gcc takes its temp dir from Windows TMP/TEMP. MSYS bash -l
    # resets TMP to /tmp (C:\msys64\tmp), which is not writable from
    # Python child processes; re-export Windows-style TMP/TEMP inside the
    # command so gcc keeps temps inside the workspace.
    gcc_tmp = out / "tmp"
    gcc_tmp.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    tmp_exports = f"export TMP='{gcc_tmp}' TEMP='{gcc_tmp}' && "
    # MSYS gcc writes Windows-style paths (C:/...) into .d files; the
    # MSYS-built fixdep cannot open them ("fixdep: read: No error").
    # Build host tools with the native ucrt64 gcc so fixdep is a native
    # binary that reads those paths fine.
    hostcc = posix(MSYS / "ucrt64" / "bin" / "gcc.exe")
    def run_make(target):
        subprocess.run([bash, "-lc",
                        f"cd {src_posix} && export PATH={msys_path}:$PATH && {tmp_exports}"
                        f"make O={out_rel} HOSTCC={hostcc} {target}"],
                       env=env, check=True)
    # Python's cwd is not honored by MSYS bash (it falls back to $HOME), so
    # cd inside the command using the POSIX form of the source path.
    src_posix = posix(src)
    run_make("riscv_mini_defconfig")
    run_make("-j8 CROSS_COMPILE=" + prefix +
             " PLATFORM_CFLAGS='-march=rv32ima_zicsr_zifencei -mabi=ilp32'")
    binary = out / "u-boot.bin"
    print("U-Boot payload:", binary, binary.stat().st_size, "bytes")
if __name__ == "__main__":
    main()
