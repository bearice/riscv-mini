# AE350 / A25 cache-control contract and sources

> 适用机器：`mega138k_ae350`。配置以机器 JSON 和所选硬件产物的导出为准。

Updated: 2026-10-19. Scope: Gowin `RiscV_AE350_SOC` hard macro (AndeStar V5 32-bit A25, rv32imafdc_zicsr_zifencei) under bare-metal firmware.

Current platform observation: D-cache is write-back. An ILM-resident measurement with a DLM stack observes one UI read on refill, no read on a hit, one read after invalidate, no UI write on a cached store, and one UI write for the subsequent 32-byte line clean. See [final evidence](../../../reports/ae350-p2.md) and [performance baseline](../../../reports/performance/ae350-memory-baseline.md). This validates the selected control sequences; it does not promote sibling-core-only register semantics to verified A25 behavior.

Legend: **[V]** = stated in the cited source; **[V*]** = verified for a sibling/spec source, not the A25 itself; **[I]** = inference; **[?]** = no source found.

## 0. Sources

| Tag | Source |
| --- | --- |
| `MUG` | Gowin RiscV_AE350_SOC Software Programming Manual MUG1029-1.3E; §6 Cache (PDF p.51–56) |
| `AX45` | AndesCore AX45MP-1C UM248 V1.2 — AndeStar V5 sibling core (64-bit, 64 B lines); only structural/CSR-semantics cross-check, **not** the A25's numbers |
| `OSBI` | OpenSBI source `platform/generic/include/andes/andes.h`, `platform/generic/andes/ae350.c` (OpenSBI 6ad246a1, Andes copyright) |
| `OSBI20` | Andes' original AE350 cache SBI: <https://git.minres.com/Firmware/opensbi/raw/commit/47a47654e8d3997b059d11bb8845ed0037e88c8e/platform/andes/ae350/cache.c> |
| `UB` | U-Boot source `arch/riscv/include/asm/arch-andes/csr.h`, `arch/riscv/cpu/andes/cache.c`, `arch/riscv/cpu/andes/cpu.c` |

## 1. The names in the request vs. what actually exists

**There is no `mcache_info` CSR, no `cctl_command` CSR and no `cctl_memory` CSR.** The AndeStar V5 machine CSR list (`AX45` Table 42, p.87) is: `milmb 0x7c0`, `mdlmb 0x7c1`, `mecc_code 0x7c2`, `mnvec 0x7c3`, `mxstatus 0x7c4`, `mpft_ctl 0x7c5`, `mhsp_ctl 0x7c6`, `msp_bound 0x7c7`, `msp_base 0x7c8`, `mdcause 0x7c9`, **`mcache_ctl 0x7ca`**, **`mcctlbeginaddr 0x7cb`**, **`mcctlcommand 0x7cc`**, **`mcctldata 0x7cd`**, `mcounterwen 0x7ce`, `mcounterinten 0x7cf`, `mmisc_ctl 0x7d0`. S/U versions: `ucctlbeginaddr 0x80b`, `ucctlcommand 0x80c`, `scctldata 0x9cd`. Geometry registers: `micm_cfg 0xfc0`, `mdcm_cfg 0xfc1`, `mmsc_cfg 0xfc2`. No source in this repo, `C:/Gowin`, or the web search used the strings `mcache_info`, `cctl_command`, `cctl_memory` **[?]**.

Requested → real register mapping:

| Requested | Real | Number | Notes |
| --- | --- | --- | --- |
| `mcache_ctl` | `mcache_ctl` | **0x7ca** | `MUG` Table 6-3 calls it "Cache Control Register" and gives **no CSR number**; 0x7ca from `OSBI:14`, `UB:13`, `AX45` — **[V]**
| `mcache_info` | does not exist | — | runtime geometry = `micm_cfg`/`mdcm_cfg` (§4) — **[V]** for absence in `MUG`/`AX45`/vendor trees
| `cctl_command` | `mcctlcommand` | **0x7cc** | `OSBI:15`, `UB:15` (`CSR_UCCTLCOMMAND 0x80c` = S/U twin) — **[V]**
| `cctl_memory` | `mcctlbeginaddr` | **0x7cb** | holds **VA** for VA-type commands, **way/index(/dword)** for IX-type commands — **[V]** `AX45` §7.6 Table 12
| — | `mcctldata` | 0x7cd | data/status of lock, tag, RAM access commands — **[V]**

Not named in the request but required: `mmsc_cfg 0xfc2` bit 16 = **CCTLCSR** presence gate (`OSBI:47-48`); `mcctlcommand` "is only present when (micm_cfg.ISZ!=0 or mdcm_cfg.DSZ!=0) and (mmsc_cfg.CCTLCSR==1)" (`AX45` §16.12.10, p.232) — **reading it on a core without CCTL traps as an illegal instruction [V*]**, so probe `mmsc_cfg` (guarded, trap-safe) before first use.

## 2. `mcache_ctl` (0x7ca) bit map

| Bits | Field | A25 (`MUG` T6-3) | AX45 (`AX45` §16.12.6) |
| --- | --- | --- | --- |
| 0 | `IC_EN` I-cache enable | yes, reset 0 | same |
| 1 | `DC_EN` D-cache enable | yes, reset 0 | same |
| 3:2 / 5:4 | `IC_ECCEN`/`DC_ECCEN` (0 off, 2 uncorrectable-only, 3 any) | yes, reset 0 | same |
| 6 / 7 | `IC_RWECC`/`DC_RWECC` (ECC RAM diagnostics) | yes | same |
| 8 | `CCTL_SUEN` (S/U access to `ucctlbeginaddr`/`ucctlcommand`) | yes, reset 0 | same |
| 9 / 10 | `IPREF_EN`/`DPREF_EN` | **not documented** | yes |
| 11 | `IC_FIRST_WORD` (RO, miss fill order) | yes | yes |
| 14:13 | `DC_WAROUND` | not documented | yes |
| 19 / 20 | `DC_COHEN`/`DC_COHSTA` | **not documented** | yes |

**There is no single "cache enable" master bit.** Enabling the caches = set `IC_EN|DC_EN` (`UB:62-88` uses exactly `csrsi/csrci 0x7ca` masks `0x1`/`0x2`). Bit 19/20 exist only when a coherence manager is present; `OSBI:145-151` and `UB:49-57` both guard with "if DC_COHEN reads back 1, then poll DC_COHSTA" because the bit is hardwired 0 without a CM. This macro is single-core with no documented L2 → never poll `DC_COHSTA` unguarded **[V]**. Read-modify-write only the `MUG`-documented bits: setting bits 9/10/13/15/19 (as `UB:36-39` does) on a core that does not implement them is at best ignored, at worst changes undocumented behaviour **[I]**.

## 3. Reset state

`MUG` Table 6-3 gives **Initial Value 0 for `IC_EN` and `DC_EN`** → **both L1 caches are disabled out of reset** and firmware must enable them explicitly **[V]**. `AX45` matches (reset 0 for both). Consequence: at reset every access goes to memory uncached, which is why the cold-boot path works with no maintenance at all.

## 4. Geometry and how to discover it at runtime

`MUG` Table 6-1/6-2: I-cache and D-cache each **32 KB = 256 lines/way × 4-way × 32-byte line**, LRU, ECC; `Cache size = lines per way × ways × line size`. **Line size = 32 bytes [V]** for this macro (AX45MP uses 64 B lines — do not copy its numbers).

Runtime decode uses `micm_cfg` (0xfc0) / `mdcm_cfg` (0xfc1) (`AX45` §16.5.1/§16.5.2, `AX45:5580-5660`): `ISET/DSET[2:0]` = lines-per-way (0→64, 1→128, **2→256**, …), `IWAY/DWAY[5:3]` = associativity-minus-1 (**3 → 4-way**), `ISZ/DSZ[8:6]` = line size (0 none, 1 8 B, 2 16 B, **3 32 B**, 4 64 B). Worked check on this very part **[V]**: `MUG` §27.18.3 prints `MICM_CFG = 0x439ada`; decode `(0x439ada>>0)&7 = 2` (256 sets), `(>>3)&7 = 3` (4-way), `(>>6)&7 = 3` (32 B) — which is exactly the demo's own `sets = 256 / ways = 4 / line size = 32` output. So the decode above is validated on the A25, and the same value can be read at boot instead of trusting constants.

## 5. `mcctlcommand` (0x7cc) opcode values

Identical in `MUG` Table 6-4 (A25, p.54 / printed 20) and `AX45` Table 58 (p.232) — two independent documents **[V]**:

| Val | Command | Addr type | Val | Command | Addr type |
| --- | --- | --- | --- | --- | --- |
| 0 | `L1D_VA_INVAL` | VA | 17 | `L1D_IX_WB` | index |
| 1 | `L1D_VA_WB` | VA | 18 | `L1D_IX_WBINVAL` | index |
| 2 | `L1D_VA_WBINVAL` | VA | 19–22 | `L1D_IX_RTAG/RDATA/WTAG/WDATA` | index (diag) |
| 3 / 4 | `L1D_VA_LOCK` / `_UNLOCK` | VA | **23** | **`L1D_INVAL_ALL`** | — |
| **6** | **`L1D_WBINVAL_ALL`** | — | 24 | `L1I_IX_INVAL` | index |
| **7** | **`L1D_WB_ALL`** | — | 27–30 | `L1I_IX_RTAG/RDATA/WTAG/WDATA` | index (diag) |
| 8 | `L1I_VA_INVAL` | VA | | | |
| 11 / 12 | `L1I_VA_LOCK` / `_UNLOCK` | VA | | | |
| 16 | `L1D_IX_INVAL` | index | | | |

**There is no `L1I_INVAL_ALL`** (values 5, 9, 10, 13–15, 25, 26 are unlisted/absent) **[V]** — whole-I-cache invalidation is either `fence.i` (§7) or a 256×4 loop of `L1I_IX_INVAL` (cmd 24).

VA-type ops apply **only if the addressed line is present** in that cache (`AX45` §7.6 Table 12, p.27); IX-type ops bypass translation. Whole-cache ops 6/7/23 take no address. `L1D_WB_ALL` leaves lines resident and locked lines locked; `L1D_WBINVAL_ALL` also invalidates **locked** lines; `L1D_INVAL_ALL` discards dirty data **[V]** (`AX45` §7.6.12–7.6.14).

## 6. Sequences (M-mode bare metal; VA = PA, no MMU)

```c
#define CSR_MCACHE_CTL 0x7ca
#define CSR_MCCTLBEGIN 0x7cb
#define CSR_MCCTLCMD   0x7cc
#define L1D_VA_INVAL 0
#define L1D_VA_WB 1
#define L1D_VA_WBINVAL 2
#define L1D_WBINVAL_ALL 6
#define L1D_WB_ALL 7
#define L1I_VA_INVAL 8
#define L1D_INVAL_ALL 23
#define LINE 32
```

- **Enable D-cache** (cache off at reset): `csr_set(0x7ca, 0x2)` (`UB:76`). I-cache: `csr_set(0x7ca, 0x1)`. Both at once: `csr_set(0x7ca, 0x3)`; `OSBI:153` does exactly this. Do it after the memory the code/data lives in is coherent (i.e. at reset, before any stale line can exist). **[V]**
- **Disable D-cache**: write back first, then clear the bit — `csr_write(0x7cc, L1D_WBINVAL_ALL); csr_clear(0x7ca, 0x2);` `OSBI20` does the reverse order (`csr_clear` then `L1D_WBINVAL_ALL`) and `OSBI:126-128` clears `IC_EN|DC_EN` then flushes; both orders appear in Andes' own code, so neither is falsified, but **flush-then-disable is the one that cannot lose data** **[I]**. Note the D-cache write buffers are flushed when `DC_EN` is cleared (`AX45` §7.3 bullet list, p.42) — so the *write buffer*, not the cache, is what that clear guarantees. **[V*]**
- **Clean (write back) a range**: loop lines, `csr_write(0x7cb, a); csr_write(0x7cc, 1);` with `a` walking line-aligned addresses.
- **Invalidate a range**: same loop with cmd `0` (D) or `8` (I).
- **Clean+invalidate a range**: same loop with cmd `2`.
- **Whole D-cache**: cmd `6` (clean+invalidate), `7` (clean only), `23` (invalidate only). No address needed.
- **Whole I-cache**: `fence.i` (cmd 24 loop is the only CSR-only option). `OSBI20` clears `IC_EN` and then executes `fence.i` as its I-cache disable — i.e. Andes relies on `fence.i` invalidating the I-cache **[V]**.

Range helper (end exclusive; start rounded down — the VA ops act on the *containing line*, so a partial line must still be covered by a whole-line op):

```c
static void cctl_range(uintptr_t start, uintptr_t end, unsigned cmd) {
    for (uintptr_t a = start & ~(uintptr_t)(LINE - 1); a < end; a += LINE) {
        csr_write(0x7cb, a);      /* VA type: byte address */
        csr_write(0x7cc, cmd);    /* write = execute */
    }
}
```

**Completion**: there is **no busy/done bit and no documented polling**; the `mcctlcommand` write is the operation, with no status register to poll (`MUG` §6.3 has no status field; `AX45` §16.12.10/p.232 lists none). Status registers exist only for *lock* (via `mcctldata`, `AX45` §7.6.4) — and reading a lock result immediately after the write only works if the command is complete, which is the strongest available evidence that the operation retires with the CSR write **[I]**. A `fence`/`fence.i` afterwards is cheap and is what Andes' own code uses around the cache ops (`OSBI20`), but no source states it is required for CCTL visibility **[I]**. **Verify on the board** with a write→WBINVAL_ALL→non-cached readback test.

**Interrupt safety [V]**: CCTL takes parameters from three CSRs and "they are inherently not atomic"; higher-privilege software must save `mcctlbeginaddr`/`mcctlcommand`/`mcctldata` with interrupts disabled (`AX45` p.233 note). In M-mode bare metal: do not let a trap handler issue CCTL between setting `mcctlbeginaddr` and writing `mcctlcommand`, or disable interrupts around multi-line loops if any handler touches these CSRs.

## 7. Does `fence` / `fence.i` do cache maintenance?

For the AndeStar V5 core the answer is **not "nothing"** (`AX45` §7.5 Table 11, p.44) **[V*]**:

| Cache | `FENCE` | `FENCE.I` |
| --- | --- | --- |
| I-Cache | none | **invalidate all lines** |
| D-Cache | none | **write back all lines** |

with the gate: `FENCE.I` does nothing to the I-cache when `IC_EN=0`, nothing to the D-cache when `DC_EN=0` (`AX45` §7.5). So: **`fence` = no cache maintenance at all** — it orders, it does not flush; **`fence.i` = whole-I-cache invalidate + whole-D-cache write-back**, but it never *invalidates* dirty D-lines (use cmd 6 for that). Since the caches are **off at reset**, `fence.i` in early boot is a no-op on both. Whether the A25 implements the same table is **[I]**, corroborated by `OSBI20` depending on `fence.i` to invalidate I-cache after clearing `IC_EN`, and by `MUG` §27.16's self-modifying demo relying on `fence.i` after touching a cached variable.

## 8. Alignment, ranges, errata

- **Line alignment [I]**: no source requires VA-type addresses to be line-aligned; they select the *line containing* the address and are conditioned on presence in the cache (`AX45` Table 12). Aligning down is still correct and keeps range loops total. Bits `[4:0]` of a VA are simply ignored for line selection.
- **IX-type index format on this part [I, computed from `AX45` Tables 13/14]**: D-cache `mcctlbeginaddr = {WAY[14:13], INDEX[12:5], OFFSET[4:3]}`, bits `[2:0]` zero (A = log2(4 dwords)+3 = 5, B = log2(32768/4) = 13, C = 15); I-cache uses `OFFSET[4:2]` (word granularity, 8 words/line). Use VA commands and this never matters.
- **Range end is exclusive in any sane implementation [I]**; the documents define only the per-line operation, never an inclusive end, because there is no range register.
- **Known erratum / caution [V]** (`OSBI:109-115`): "On Andes 4X-series CPUs, disabling the L1 data cache causes the CPU to fetch data directly from RAM. However, L1 cache flushes write data back to the Last Level Cache (LLC). This discrepancy can lead to return address corruption on the stack. To prevent this, the following functions must be inlined." Practical rule for this firmware: **never call a function across a D-cache disable/enable boundary** — inline `ae350_disable_coherency`-style helpers, or keep the stack/return path in a region that is not being flushed.
- **Diagnostics caution [V]** (`AX45` §7.6.9 note): writing tag data with `L1D_IX_WTAG`/`L1I_IX_WTAG` when the cache is enabled is UNPREDICTABLE; recommended to clear TAG SRAM before enabling. Irrelevant unless the ECC-injection path is used.
- **No published A25/AE350 cache erratum found** in `MUG`, `AX45`, u-boot, OpenSBI or the searched web material **[?]**.

## 9. Verified vs unverified

**Verified against Gowin's own AE350 manual**: line size 32 B, 32 KB / 256 lines-per-way / 4-way, ECC+LRU; `IC_EN`/`DC_EN` reset = 0 (caches off at reset); `mcache_ctl` documented bits 0–11 with semantics; the full `mcctlcommand` opcode table incl. 6/7/23 and the absence of an I-cache "invalidate all" command; `micm_cfg`/`mdcm_cfg` exist and their decode reproduces the demo's printed 256/4/32 on this exact part.

**Verified against Andes' upstream code** (Andes copyright, targets AE350/A25-class parts): CSR numbers 0x7ca/0x7cb/0x7cc/0x80c; `csrsi/csrci 0x7ca, bit0/bit1` as the enable/disable idiom; cmd 6 as the D-cache flush-all; `fence.i` as the I-cache invalidator; the DC_COHEN/DC_COHSTA guard.

**Unverified / inference**: FENCE/FENCE.I effects on the **A25** (table proven only for AX45MP); command completion being synchronous with no polling; VA alignment requirement; exact range-boundary semantics; whether bits 9/10/13/15/19 do anything on this macro; whether `mmsc_cfg.CCTLCSR` is set (needs an on-board `csrr 0xfc2`); the original Andes BSP `bsp\ae350\cache.c` text (not on this machine — `MUG` §6.3 points at it but does not quote it).

**Not found anywhere**: `mcache_info`, `cctl_command`, `cctl_memory` as named CSRs; any AE350 BSP headers inside `C:/Gowin`; any C firmware in the 138K reference tree; an A25-core datasheet PDF (andestech.com product page has no public datasheet link).
