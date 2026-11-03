# AE350 U-Boot port inputs

`firmware/uboot/install.py --machine mega138k_ae350` installs the port into the
pinned U-Boot v2026.07 tree, following the vendor-source rules in
`docs/common/component-builds.md` (shared helpers: `firmware/vendor_install.py`).
Board files and native drivers are tracked under `firmware/uboot/` in the U-Boot
tree layout and copied in:

| Tracked file | Installed as |
| --- | --- |
| `board/riscv-mini/mega138k_ae350/*` | board directory; `arch/riscv/Kconfig` gets the target and its Kconfig source |
| `configs/mega138k_ae350_defconfig`, `include/configs/mega138k_ae350.h` | board configuration |
| `drivers/mmc/ae350_sd.c` | `CONFIG_MMC_AE350_SD`: LiteSDCard core, block data through one ATCDMAC300 channel; at most 8 blocks per command (the core's block count register is 4 bits) |
| `drivers/net/ae350_eth.c` | `CONFIG_AE350_ETH`: descriptor-ring MAC and RTL8211F over bit-banged MDIO |

The installer adds the Kconfig/Makefile entries from its machine table. Edits to
upstream code live in `patches/`, one purpose per file, applied by the installer
in file-name order (applied patches are skipped; a conflicting patch aborts):

| Patch | Purpose |
| --- | --- |
| `10-single-hart-no-atomics.patch` | Without Zaamo and SMP the boot hart enters U-Boot directly instead of through the `amoswap` hart lottery. AMO/LR/SC on DDR never complete on the A25 (see `firmware/linux/ae350/README.md`); the build rejects any atomic instruction in the image. |

`source.json` pins the tarball, its SHA256 and the reconstructed tree hash. After
changing any tracked input, replay the archive with the installer and record the
new `tree_sha256`; `build/vendor/u-boot-ae350` is disposable.

Addresses are not configured here. The build writes the address contract to
`<obj>/include/ae350_dts.h` (used by the board header and the environment) and
sets `TEXT_BASE` (os.uboot), the initial stack (top of os.uboot) and
`SYS_LOAD_ADDR` (os.kernel) in `.config`. U-Boot runs in S-mode on the FDT that
OpenSBI passes (`OF_HAS_PRIOR_STAGE`): `firmware/dts/mega138k_ae350-u-boot.dts`,
embedded into `fw_payload.bin` and copied to os.firmware-fdt. Linux boots with
the SD/TFTP `mega138k_ae350.dtb`. The environment's `sdboot` (the autoboot
command) and `netboot` print a `crc32` after each load as boot evidence; both
use `&&`/`if`, so the defconfig enables the hush parser. `sdboot` reads the
volume the BIOS FatFs writes: a FAT boot sector at LBA 0 (`mmc 0:0`) wins over
the partition table, as in FatFs.

Cache maintenance around SD/Ethernet DMA uses the Andes `ucctl` CSRs from
S-mode, which OpenSBI enables (`firmware/opensbi/ae350/patches/40-ae350-smode-cctl.patch`).
