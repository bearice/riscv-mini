# AE350 OpenSBI port inputs

`source.json` pins upstream commit 7a7a60dc87a41c79fb63d5972943e9d8fe4abf77,
the official commit tarball and its checksum. `firmware/opensbi/install.py
--machine mega138k_ae350` installs the port (vendor-source rules:
`docs/common/component-builds.md`). AE350 uses upstream's generic platform with
the Andes AE350 support, so the port adds no files; the upstream edits are split
by purpose under `patches/` and applied in name order:

| Patch | Purpose | Status |
| --- | --- | --- |
| `10-rv32-no-mstatush.patch` | Skip the RV32 `mstatush` MDT clear at entry. The A25 implements privileged spec 1.11, has no `mstatush`, and traps before any trap handler exists; Smdbltrp needs 1.12, so there is nothing to clear. | Required (hardware) |
| `20-single-hart-no-atomics.patch` | Allow building without Zaamo/Zalrsc: plain boot lottery, atomics, ticket locks and the `__atomic_*_4` library calls the compiler emits; the illegal-atomic emulation reports a truly illegal instruction. Only correct on one hart (OpenSBI runs with M-mode interrupts off); cold boot hangs with a message if the DT lists more harts. | Required (hardware workaround) |
| `30-ae350-enable-l1-cache.patch` | The Andes AE350 platform enables L1 I/D caches on cold boot (L1D write-back/invalidate first). The BIOS hands over with caches off. | Required |
| `40-ae350-smode-cctl.patch` | Set `mcache_ctl.CCTL_SUEN` on cold boot so S-mode U-Boot can write back/invalidate L1D around SD and Ethernet DMA through `ucctlcommand`. Linux does not use it (`CONFIG_ERRATA_ANDES` off). | Required for U-Boot; board-verified by U-Boot SD `fatload` and TFTP with L1D on (`build/runs/20261102T191120089044Z-board-ae350-linux`, `…184015359623Z-board-ae350-linux`) |

Why no atomics: on DDR an A25 AMO/LR/SC only completes when its line is already
read-allocated in L1D; otherwise the core issues a bus atomic that the hard
macro's DDR port cannot acknowledge and retries forever (evidence in
`docs/platforms/mega138k_ae350/p8-handoff.md`). The build therefore uses
`PLATFORM_RISCV_ISA=rv32imfd_zicsr_zifencei` and rejects any AMO/LR/SC found in
the disassembled firmware. The `opensbi` target builds `fw_jump` (straight
to the Image); the `uboot` target builds `fw_payload` with U-Boot at os.uboot
and the U-Boot control FDT embedded (`FW_DYNAMIC=n`; upstream's test payload
needs atomics).

Load address, jump address and FDT address are taken from the AE350 address
contract (`os.sbi`, `os.kernel`, `os.dtb`); the build checks that `_fw_end`
stays inside `os.sbi`, and `board linux` checks the printed banner against it.
The root domain uses upstream's split firmware regions (M-only RX text, M-only
RW data). OpenSBI does not touch ILM/DLM: the A25 local memories cannot be
disabled, and Linux keeps their virtual window unmapped instead (see the
address map, section 7).

The unified build entry verifies or reconstructs the disposable vendor source
from those inputs. The banner version is the pinned upstream commit
(`OPENSBI_VERSION_GIT`), not the enclosing repository. When the native make
command changes, the build cleans the workspace objects first, because OpenSBI
tracks neither compiler flags nor a moved source tree. Successful firmware and
DTB are archived to the immutable store.
