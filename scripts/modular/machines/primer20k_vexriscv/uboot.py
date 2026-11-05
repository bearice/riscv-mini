"""Build U-Boot v2026.07 as the riscv-mini S-mode payload.

The source tree is reconstructed on the Windows host from the pinned archive
under firmware/uboot/vexriscv/source.json and the shared port installer, then
synchronized into a WSL-native (ext4) tree by content hash, because building
U-Boot on a /mnt/c path is far too slow. --source and --obj name that build
location only; the tree contents always come from the source manifest. Edits
to upstream files live in the machine patch series next to the manifest.

The produced u-boot.bin is copied back to the Windows build tree for
mini build opensbi to pack.
"""
import subprocess, sys, json
import hashlib
from pathlib import Path
from scripts.tools.versions import component_version
from scripts.modular.machines.primer20k_vexriscv import sourceprep
from scripts.modular.storage import Store
ROOT = Path(__file__).resolve().parents[4]
CROSS = "riscv64-elf-"
LIBGCC = "$(riscv64-elf-gcc -march=rv32im -mabi=ilp32 -print-libgcc-file-name)"
# LLD supports PIE; runtime relocation must also move command/data pointers.
ARCH_FLAGS = "-march=rv32ima_zicsr_zifencei -mabi=ilp32"

def to_wsl(p):
    """Windows path -> WSL /mnt/<drive>/... path."""
    s = str(Path(p).resolve()).replace("\\", "/")
    if sys.platform != 'win32':
        return s
    return "/mnt/" + s[0].lower() + s[2:]


def build(a, run=None):
    out = a.output_dir.resolve(); out.mkdir(parents=True, exist_ok=True)
    csr = json.loads((a.soc_dir/'csr.json').read_text()) if a.soc_dir else None
    ring = bool(csr and csr['constants'].get('mini_feature_eth_dma', 0))
    if ring != bool(csr and csr['constants'].get('config_eth_ring_dma', 0)):
        raise ValueError('U-Boot DMA requires the exclusive ring ABI')
    backend = 'ring' if ring else 'pio'
    uart_base = csr['csr_bases']['uart'] if csr else 0xf000a800
    version = component_version('uboot')
    out_wsl = to_wsl(out)
    store = Store()
    tree = sourceprep.prepare(store, run, 'uboot', store.build/'vendor/u-boot-v2026.07-vexriscv')
    tree_sha = run.result['upstream_source']['tree_sha256']

    script = f"""
set -euo pipefail
SRC="{a.source}"; OBJ="{a.obj}"; OUT="{out_wsl}"
VENDOR="{to_wsl(tree)}"; TREE_SHA="{tree_sha}"
# The ext4 tree is disposable build state: replace it wholesale whenever its
# marker does not match the reconstructed source, never patch it in place.
if [ "$(cat "$SRC/.mini-source.json" 2>/dev/null || true)" != "$TREE_SHA" ]; then
  rm -rf "$SRC.mini-tmp"
  cp -a "$VENDOR" "$SRC.mini-tmp"
  rm -rf "$SRC"
  mv "$SRC.mini-tmp" "$SRC"
  echo "$TREE_SHA" > "$SRC/.mini-source.json"
fi
cd "$SRC"
make O="$OBJ" riscv_mini_defconfig
scripts/config --file "$OBJ/.config" --{'enable' if ring else 'disable'} RISCV_MINI_ETH_RING_DMA
scripts/config --file "$OBJ/.config" --set-val DEBUG_UART_BASE 0x{uart_base:x}
{'scripts/config --file "$OBJ/.config" --disable VIDEO' if a.without_video else ''}
make O="$OBJ" olddefconfig
make -j8 O="$OBJ" CROSS_COMPILE={CROSS} LD=ld.lld LDFLAGS_u-boot='--gc-sections -static -pie -Ttext=$(CONFIG_TEXT_BASE)' PLATFORM_LIBGCC="{LIBGCC}" PLATFORM_CFLAGS='{ARCH_FLAGS}' LOCALVERSION='-riscv-mini-{version}'
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
    from scripts.modular.executor import execute
    execute(out / 'build.sh', required=('cc','cxx','make','dtc','lld','riscv64-elf-gcc'),run=run)
    record = dict(component='uboot', version=version, ethernet_backend=backend,
        soc_dir=str(a.soc_dir.resolve()) if a.soc_dir else None,
        binary_sha256=hashlib.sha256((out/'u-boot.bin').read_bytes()).hexdigest())
    (out/'validation.json').write_text(json.dumps(record, indent=2)+'\n')
