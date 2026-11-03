# AE350 Linux port inputs

Both machines use `firmware/linux/install.py`. The default machine remains
`primer20k_vexriscv`; AE350 passes `--machine mega138k_ae350`. Common drivers and
Kconfig/Makefile entries are installed once by that shared installer. Upstream
edits beyond those common overlays live in `patches/`, one purpose per file,
applied by the same installer in file-name order through the shared
`firmware/vendor_install.py` (a repeated installation leaves the tree unchanged;
a conflicting patch restores the tree and aborts). Vendor-source rules:
`docs/common/component-builds.md`.

| Patch | Purpose | Status |
| --- | --- | --- |
| `10-reserved-kernel-va.patch` | `CONFIG_RISCV_RESERVED_VA_START/SIZE`: keep a kernel VA range permanently busy in vmalloc. The A25 matches ILM/DLM on the virtual address and they cannot be disabled; the range comes from the address contract (`OS_RESERVED_VA`) and the build checks the config against it. | Fix, board-verified (PLIC/UART IRQ) |
| `20-clang-unused-argument.patch` | Do not turn clang's unused-argument warning into an error. | Build |
| `30-no-ddr-atomics.patch` | UP kernel built without AMO/LR/SC (`rv32im`, IRQ-off atomics, futex, `entry.S`). The A25 implements A, but on DDR an AMO/LR/SC only completes when its line was read-allocated in L1D; otherwise the core issues a bus atomic that the hard macro's DDR port cannot acknowledge (AMO retries forever, SC always fails). Board evidence: `docs/platforms/mega138k_ae350/p8-handoff.md`. | Hardware workaround (single hart); permanent |
| `85-tlbflush-full.patch` | Full `sfence.vma` instead of the per-page (`sfence.vma addr`, `sfence.vma addr, asid`) flushes. Still required after the ILM/DLM VA fix: without it `/init` fails to exec (`-EINVAL`) and the kernel panics (receipt `20261102T112419022654Z-board-ae350-linux`). Why ranged flushes fail on the A25 is not established. | Required workaround; root cause open |

`source.json` pins the Linux 6.12.112 tarball, its SHA256 and the reconstructed
source tree. `linux.config` is the complete configuration, with the initramfs
path substituted by the build adapter. Nothing unique may live only in
`build/vendor` or `build/work`; both are disposable and reconstructible.

Hardware capability and accepted software are separate. The A25 implements
A, F, D and C (`rv32imafdc`, reported by OpenSBI and `/proc/cpuinfo`). AMOs are
off because of the measured DDR limitation above, not because the CPU lacks A.
This is accepted as a hardware limit: user programs are built without A
(`init.c` uses `rv32im`); programs that rely on A (musl/glibc CAS and AMOs) hang on DDR.
`CONFIG_FPU=y` (V explicitly off; the A25 has no vector unit). F/D were verified in two
steps: M-mode execution (`firmware/examples/mega138k_ae350/fpu_probe.c`: IEEE results,
fcsr flags, all 32 registers) and Linux context save/restore (`init.c` FP test, built with
`MINI_LINUX_FPU` for AE350 only: fork inheritance plus four processes yielding/sleeping
with distinct F registers and fcsr). The `rv32im` base needs the `-march` F/D/V filter in
`arch/riscv/Makefile` widened, which `30-no-ddr-atomics.patch` does, so kernel C code stays
free of FP instructions. C is not required.
