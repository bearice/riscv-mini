/* AE350 P2 DDR test payload: linked into and executed from DDR.
 *
 * Prints one `T <name> PASS|FAIL ...` line per test plus raw cycle counts; the
 * host operation (scripts/mini_ops/ae350.py) parses those lines, converts
 * cycles to rates with the *configured* clock and stores the log as evidence.
 * Parameters arrive in the first 64 bytes of the DDR window (CPU 0x2000),
 * written with the ROM monitor's `p` command.
 */
#include <stdint.h>
#include "ae350_address_map.h"

#define UART2_BASE 0xf0300000u
#define UART_DATA 0x20u
#define UART_LSR  0x34u
#define UART_REG(offset) (*(volatile uint32_t *)(UART2_BASE + (offset)))

#define CPU_ADDR(dram_off) ((uint32_t)(dram_off))   /* zero-based DDR */
#define PARAMS ((volatile uint32_t *)0x2000u)
#define P_STRESS_SECONDS 0u
#define P_SEED           1u
#define P_REGION_KB      2u
#define P_FLAGS          3u
#define P_TRAV_MB        4u

#define WORK_BASE  CPU_ADDR(0x100000u)   /* 1 MiB into DRAM: above the payload */
#define WORK_SIZE  (1u << 20)

extern char __text_start[], __text_end[], __data_start[], __data_end[], __bss_start[], __bss_end[];

static uint32_t failures;
static uint32_t checks;

/* Results are mirrored into the always-mapped 8 KiB SRAM so the CPU JTAG can
 * read them when no console exists.  Layout: [0]=0xd0000001 started,
 * [1..N]=per-test ok flags in call order, [0x80]=checks, [0x81]=failures,
 * [0x82]=0xd0000002 done. */
#define RES ((volatile uint32_t *)AE350_SRAM_RESULTS)
static uint32_t res_index;
static void res_begin(void) { RES[0] = 0xd0000001u; }
static void res_test(int ok) { RES[1 + (res_index++)] = ok ? 1u : 0u; if (res_index >= 0x7f) res_index = 0x7f; }

#ifdef MINI_NO_UART
static void putc_uart(char c) { (void)c; }
#else
static void putc_uart(char c) {
    while (!(UART_REG(UART_LSR) & 0x20u)) {}
    UART_REG(UART_DATA) = (uint8_t)c;
}
#endif
static void puts_uart(const char *s) { while (*s) putc_uart(*s++); }
static void hex32(uint32_t value) {
    for (int i = 28; i >= 0; i -= 4) putc_uart("0123456789abcdef"[(value >> i) & 15u]);
}
void trap_report(uint32_t cause, uint32_t pc, uint32_t value, uint32_t ra, uint32_t sp) {
    RES[0xA0] = 0x80000000u | cause;
    RES[0xA1] = pc;
    RES[0xA2] = value;
    RES[0xA3] = ra;
    puts_uart("\r\nPTRAP mcause="); hex32(cause);
    puts_uart(" mepc="); hex32(pc);
    puts_uart(" mtval="); hex32(value);
    puts_uart(" ra="); hex32(ra);
    puts_uart(" sp="); hex32(sp);
    puts_uart("\r\nPTRAP HALTED\r\n");
}

static uint32_t mcycle(void) { uint32_t v; __asm__ volatile("csrr %0, mcycle" : "=r"(v)); return v; }
static void fence_i(void) { __asm__ volatile("fence.i" ::: "memory"); }
static void fence(void) { __asm__ volatile("fence" ::: "memory"); }

static uint32_t rng_state;
static uint32_t rng(void) {
    uint32_t x = rng_state;
    x ^= x << 13; x ^= x >> 17; x ^= x << 5;
    rng_state = x;
    return x;
}

static void test_begin(const char *name) {
    /* Progress marker for the JTAG-only flow: which test is running now. */
    static uint32_t seq;
    RES[0x41] = ++seq;
    RES[0x42] = (uint32_t)(uint8_t)name[0] | ((uint32_t)(uint8_t)name[1] << 8) |
                ((uint32_t)(uint8_t)name[2] << 16) | ((uint32_t)(uint8_t)name[3] << 24);
    puts_uart("T "); puts_uart(name); puts_uart(" ");
}
static void test_result(int ok) {
    res_test(ok);
    if (ok) { puts_uart("PASS\r\n"); checks++; }
    else    { puts_uart("FAIL\r\n"); failures++; checks++; }
}
static void kv(const char *key, uint32_t value) { puts_uart(key); puts_uart("="); hex32(value); }

/* ------------------------------------------------------------------ geometry */
static uint32_t label_for(uint32_t dram_off) { return 0xc0de0000u ^ (dram_off >> 6) ^ (dram_off * 2654435761u); }

static void test_geometry(void) {
    static const uint32_t offs[] = {
        0x100000u,           /* 1 MiB */
        0x08000000u,         /* 128 MiB */
        0x0ffffffcu,         /* 128 MiB boundary - 4 */
        0x10000000u,         /* 256 MiB */
        0x20000000u,         /* 512 MiB */
        0x3fffffc0u,         /* final 64-byte region */
        0x3ffffffcu,         /* last word */
    };
    const uint32_t n = sizeof(offs) / sizeof(offs[0]);
    uint32_t errs = 0, distinct = 0;
    for (uint32_t i = 0; i < n; i++) *(volatile uint32_t *)CPU_ADDR(offs[i]) = label_for(offs[i]);
    fence();
    /* Read back in reverse order so no write is immediately followed by its read. */
    for (uint32_t i = n; i-- > 0;) {
        uint32_t got = *(volatile uint32_t *)CPU_ADDR(offs[i]);
        if (got != label_for(offs[i])) {
            if (errs < 4) { test_begin("geometry.error"); kv(" off", offs[i]); kv(" want", label_for(offs[i])); kv(" got", got); puts_uart("\r\n"); }
            errs++;
        }
    }
    /* Keep a second copy of the labels and prove they are pairwise distinct. */
    for (uint32_t i = 0; i < n; i++) {
        uint32_t a = *(volatile uint32_t *)CPU_ADDR(offs[i]);
        for (uint32_t j = i + 1; j < n; j++) {
            if (a == *(volatile uint32_t *)CPU_ADDR(offs[j])) errs++;
        }
        distinct++;
    }
    test_begin("geometry"); test_result(errs == 0);
    kv(" points", n); kv(" distinct", distinct); kv(" errors", errs); puts_uart("\r\n");
}

/* ------------------------------------------------------------------ patterns */
static void fill_block(volatile uint32_t *base, uint32_t words, uint32_t seed) {
    rng_state = seed ? seed : 1;
    for (uint32_t i = 0; i < words; i++) base[i] = rng();
}
static uint32_t verify_block(volatile uint32_t *base, uint32_t words, uint32_t seed) {
    rng_state = seed ? seed : 1;
    uint32_t errs = 0;
    for (uint32_t i = 0; i < words; i++) {
        uint32_t want = rng();
        uint32_t got = base[i];
        if (got != want) {
            if (errs < 4) { test_begin("pattern.error"); kv(" idx", i); kv(" want", want); kv(" got", got); puts_uart("\r\n"); }
            errs++;
        }
    }
    return errs;
}

static void test_patterns(uint32_t seed) {
    volatile uint32_t *base = (volatile uint32_t *)WORK_BASE;
    const uint32_t words = 65536;   /* 256 KiB */
    uint32_t errs = 0, i;

    for (i = 0; i < words; i++) base[i] = 0x00000000u;
    fence();
    for (i = 0; i < words; i++) if (base[i] != 0) errs++;
    for (i = 0; i < words; i++) base[i] = 0xffffffffu;
    fence();
    for (i = 0; i < words; i++) if (base[i] != 0xffffffffu) errs++;

    for (uint32_t bit = 0; bit < 32; bit++) {
        for (i = 0; i < 64; i++) base[i] = 1u << bit;
        fence();
        for (i = 0; i < 64; i++) if (base[i] != (1u << bit)) errs++;
        for (i = 0; i < 64; i++) base[i] = ~(1u << bit);
        fence();
        for (i = 0; i < 64; i++) if (base[i] != ~(1u << bit)) errs++;
    }
    for (i = 0; i < words; i++) base[i] = (i & 1u) ? 0x55555555u : 0xaaaaaaaau;
    fence();
    for (i = 0; i < words; i++) if (base[i] != ((i & 1u) ? 0x55555555u : 0xaaaaaaaau)) errs++;
    for (i = 0; i < words; i++) base[i] = (uint32_t)(WORK_BASE + 4 * i);
    fence();
    for (i = 0; i < words; i++) if (base[i] != (uint32_t)(WORK_BASE + 4 * i)) errs++;
    for (i = 0; i < words; i++) base[i] = ~(uint32_t)(WORK_BASE + 4 * i);
    fence();
    for (i = 0; i < words; i++) if (base[i] != ~(uint32_t)(WORK_BASE + 4 * i)) errs++;

    fill_block(base, words, seed);
    fence();
    errs += verify_block(base, words, seed);

    /* Address stride across cache lines / banks / rows: one word per 4 KiB. */
    for (i = 0; i < 256; i++) *(volatile uint32_t *)(WORK_BASE + 4096u * i) = 0x5a000000u | i;
    fence();
    for (i = 0; i < 256; i++) if (*(volatile uint32_t *)(WORK_BASE + 4096u * i) != (0x5a000000u | i)) errs++;

    test_begin("patterns"); test_result(errs == 0);
    kv(" bytes", words * 4); kv(" seed", seed); kv(" errors", errs); puts_uart("\r\n");
}

static void test_masks(void) {
    volatile uint8_t *b = (volatile uint8_t *)WORK_BASE;
    volatile uint16_t *h = (volatile uint16_t *)WORK_BASE;
    volatile uint32_t *w = (volatile uint32_t *)WORK_BASE;
    uint32_t errs = 0;
    for (uint32_t i = 0; i < 256; i++) b[i] = 0x11;
    fence();
    for (uint32_t i = 0; i < 256; i += 3) b[i] = (uint8_t)(0x80 + i);
    fence();
    for (uint32_t i = 0; i < 256; i++) if (b[i] != ((i % 3 == 0) ? (uint8_t)(0x80 + i) : 0x11)) errs++;
    for (uint32_t i = 0; i < 256; i++) b[i] = 0x22;
    fence();
    for (uint32_t i = 0; i < 128; i++) h[i] = (uint16_t)(0x8000u | i);
    fence();
    for (uint32_t i = 0; i < 256; i++) if (b[i] != (uint8_t)((0x8000u | (i >> 1)) >> (8 * (i & 1)))) errs++;
    for (uint32_t i = 0; i < 64; i++) w[i] = 0x01020304u * (i + 1);
    fence();
    for (uint32_t i = 0; i < 64; i++) if (w[i] != 0x01020304u * (i + 1)) errs++;
    /* Guard bytes: write the middle byte of a word, neighbours must survive. */
    w[0] = 0xa1b2c3d4u;
    fence();
    b[1] = 0x5au;
    fence();
    if (w[0] != 0xa1b25ad4u) errs++;
    test_begin("masks"); test_result(errs == 0);
    kv(" bytes", 1024); kv(" errors", errs); puts_uart("\r\n");
}

/* ------------------------------------------------------------ DDR execution */
static uint32_t add_one(uint32_t x) { return x + 1u; }

static void test_exec(void) {
    uint32_t errs = 0;
    uint32_t text_len = (uint32_t)(__text_end - __text_start);
    uint32_t sum = 0;
    for (uint32_t i = 0; i < text_len; i++) sum = (sum << 1) | (sum >> 31), sum += (uint8_t)__text_start[i];
    uint32_t sp; __asm__ volatile("mv %0, sp" : "=r"(sp));
    if ((uint32_t)add_one < 0x2000u || (uint32_t)add_one >= 0x40000000u) errs++;
    if ((uint32_t)WORK_BASE < 0x2000u) errs++;
    if (sp < 0x2000u || sp >= 0x40000000u) errs++;
    if (text_len < 1024u) errs++;
    test_begin("exec"); test_result(errs == 0);
    kv(" fn", (uint32_t)add_one); kv(" pc_main", (uint32_t)test_exec);
    kv(" sp", sp); kv(" text_bytes", text_len); kv(" text_sum", sum); puts_uart("\r\n");

    /* Same-address code replacement: assemble two stubs at one fixed address. */
    volatile uint32_t *code = (volatile uint32_t *)0x2100u;
    uint32_t (*fn)(uint32_t) = (uint32_t (*)(uint32_t))0x2100u;
    uint32_t r1 = 0, r2 = 0;
    RES[0x43] = 1;
    code[0] = 0x00150513u;  /* addi a0, a0, 1 */
    code[1] = 0x00008067u;  /* ret */
    RES[0x43] = 2;
    fence_i(); fence();
    RES[0x43] = 3;
    r1 = fn(41);
    RES[0x43] = 4;
    code[0] = 0x00250513u;  /* addi a0, a0, 2 */
    code[1] = 0x00008067u;
    fence_i(); fence();
    RES[0x43] = 5;
    r2 = fn(41);
    RES[0x43] = 6;
    test_begin("exec.replace"); test_result(r1 == 42 && r2 == 43);
    kv(" first", r1); kv(" second", r2); puts_uart("\r\n");
}

/* ------------------------------------------------------------------ atomics */
static void test_nonatomic_rmw(void) {
    /* The AE350 hard macro's AHB data port has a single-bit HRESP and no
     * exclusive/atomic attribute, and every aligned AMO/LR.SC through it hangs
     * the hart: reproduced with a minimal DDR-resident program on both SRAM
     * (0x1940) and DRAM (0x102004), PC stuck on the AMO instruction, no bus
     * transaction issued, no trap.  The tests below therefore only exercise
     * plain RMW sequences and record the limitation explicitly. */
    volatile uint32_t *p = (volatile uint32_t *)0x102008u;
    uint32_t ok = 1;
    *p = 100u;
    uint32_t tmp = *p;
    *p = tmp + 7u;
    if (*p != 107u) ok = 0;
    test_begin("nonatomic.rmw"); test_result(ok);
    kv(" amo_supported", 0); kv(" rmw_value", *p); puts_uart("\r\n");
}

/* --------------------------------------------------- bandwidth and latency */
static void test_perf(void) {
    volatile uint32_t *base = (volatile uint32_t *)WORK_BASE;
    const uint32_t bytes = 65536;
    const uint32_t words = bytes / 4;
    uint32_t t0, t1, sum = 0;

    for (int rep = 0; rep < 3; rep++) {
        t0 = mcycle();
        for (uint32_t i = 0; i < words; i++) base[i] = i ^ 0x5a5a5a5au;
        fence();
        t1 = mcycle();
        RES[0x60 + rep * 3 + 0] = t1 - t0;
        test_begin("perf.write"); puts_uart("MEASURED\r\n");
        kv(" rep", rep); kv(" bytes", bytes); kv(" cycles", t1 - t0); puts_uart("\r\n");

        t0 = mcycle();
        for (uint32_t i = 0; i < words; i++) sum += base[i];
        fence();
        t1 = mcycle();
        RES[0x60 + rep * 3 + 1] = t1 - t0;
        test_begin("perf.read"); puts_uart("MEASURED\r\n");
        kv(" rep", rep); kv(" bytes", bytes); kv(" cycles", t1 - t0); kv(" sum", sum); puts_uart("\r\n");

        t0 = mcycle();
        for (uint32_t i = 0; i < words; i++) ((volatile uint32_t *)(WORK_BASE + 0x40000))[i] = base[i];
        fence();
        t1 = mcycle();
        RES[0x60 + rep * 3 + 2] = t1 - t0;
        test_begin("perf.copy"); puts_uart("MEASURED\r\n");
        kv(" rep", rep); kv(" bytes", bytes); kv(" cycles", t1 - t0); puts_uart("\r\n");
    }

    /* Dependent-load latency: pointer chase over 64 KiB, 1024 hops. */
    {
        volatile uint32_t *chase = (volatile uint32_t *)(WORK_BASE + 0x80000);
        const uint32_t hops = 1024, stride = 16;
        for (uint32_t i = 0; i < hops; i++) chase[i * (stride / 4)] = (uint32_t)(chase + ((i + 1) % hops) * (stride / 4));
        fence();
        volatile uint32_t *p = chase;
        t0 = mcycle();
        for (uint32_t i = 0; i < hops; i++) p = (volatile uint32_t *)*p;
        t1 = mcycle();
        RES[0x69] = t1 - t0;
    test_begin("perf.latency"); puts_uart("MEASURED\r\n");
        kv(" hops", hops); kv(" cycles", t1 - t0); kv(" last", (uint32_t)p); puts_uart("\r\n");
    }
}

/* ------------------------------------------------------------ cache (G7)
 *
 * A25 (MUG1031 / AX45MP): 32 KiB L1D, 4-way, 32-byte lines, I/D disabled at
 * reset.  Control: mcache_ctl=0x7ca (bit0 IC_EN, bit1 DC_EN), CCTL address
 * window 0x7cb, CCTL command 0x7cc (write triggers), CCTL data 0x7cd.
 * Commands used here: 23=L1D_INVAL_ALL, 7=L1D_WB_ALL, 6=L1D_WBINVAL_ALL,
 * 2=L1D_VA_WBINVAL (by address).  fence.i flushes the whole D-cache and
 * invalidates the whole I-cache on this core; plain fence does nothing to
 * either cache, so it is never used as a maintenance operation here.
 */
#define CSR_MCACHE_CTL   0x7ca
#define CSR_CCTL_ADDR    0x7cb
#define CSR_CCTL_CMD     0x7cc
#define CCTL_INVAL_ALL   23
#define CCTL_CLEAN_ALL   7
#define CCTL_WBINVAL_ALL 6
#define CCTL_VA_WBINVAL  2

static inline __attribute__((always_inline)) uint32_t mcache_ctl(void) { uint32_t v; __asm__ volatile("csrr %0, 0x7ca" : "=r"(v)); return v; }
static inline __attribute__((always_inline)) void mcache_csrs(uint32_t bits) { __asm__ volatile("csrs 0x7ca, %0" :: "r"(bits) : "memory"); }
static inline __attribute__((always_inline)) void mcache_csrc(uint32_t bits) { __asm__ volatile("csrc 0x7ca, %0" :: "r"(bits) : "memory"); }
static inline __attribute__((always_inline)) void cctl_cmd(uint32_t op) { __asm__ volatile("csrw 0x7cc, %0" :: "r"(op) : "memory"); }
static inline __attribute__((always_inline)) void cctl_addr(uint32_t a) { __asm__ volatile("csrw 0x7cb, %0" :: "r"(a) : "memory"); }

static uint32_t ddr_read_off(uint32_t off) { return *(volatile uint32_t *)CPU_ADDR(off); }
static void ddr_write_off(uint32_t off, uint32_t v) { *(volatile uint32_t *)CPU_ADDR(off) = v; }

static void test_cache(void) {
    const uint32_t off = 0x200000u;      /* 2 MiB into DRAM, outside the payload */
    uint32_t errs = 0, steps = 0, write_through = 0;
    uint32_t v_stale, v_after_inval, v_before_clean, v_after_clean, ctl0, ctl1;

    ctl0 = mcache_ctl();
    {
        uint32_t mmsc_cfg; __asm__ volatile("csrr %0, 0xfc2" : "=r"(mmsc_cfg));
        uint32_t mdcm_cfg; __asm__ volatile("csrr %0, 0xfc1" : "=r"(mdcm_cfg));
        test_begin("cache.info"); puts_uart("INFO\r\n");
        kv(" mmsc_cfg", mmsc_cfg); kv(" cctlcsr", (mmsc_cfg >> 16) & 1u);
        kv(" mdcm_cfg", mdcm_cfg); puts_uart("\r\n");
    }
    mcache_csrc(0x3u);                    /* start with both caches disabled */
    ddr_write_off(off, 0xaaaa0001u);
    fence();

    /* 1. Enable the D-cache and fill the line from DRAM. */
    mcache_csrs(0x2u);
    ctl1 = mcache_ctl();
    if ((ctl1 & 0x2u) == 0) errs++;       /* write-back did not stick: report, do not fake */
    if (ddr_read_off(off) != 0xaaaa0001u) errs++;
    steps++;

    /* 2. Bypass the cache and change DRAM behind it. */
    mcache_csrc(0x2u);
    ddr_write_off(off, 0xbbbb0002u);
    fence();

    /* 3. Re-enable: the old line is expected to be served from the cache. */
    mcache_csrs(0x2u);
    v_stale = ddr_read_off(off);

    /* 4. Invalidate the whole D-cache: DRAM value must appear. */
    cctl_cmd(CCTL_INVAL_ALL);
    v_after_inval = ddr_read_off(off);
    if (v_after_inval != 0xbbbb0002u) errs++;
    steps++;

    /* 5. Dirty a line with the cache on, then clean: DRAM must change. */
    ddr_write_off(off, 0xcccc0003u);
    fence();
    mcache_csrc(0x2u);
    v_before_clean = ddr_read_off(off);   /* bypass -> DRAM, still the old value */
    mcache_csrs(0x2u);
    (void)ddr_read_off(off);              /* refill, then dirty it again */
    ddr_write_off(off, 0xcccc0003u);
    cctl_cmd(CCTL_CLEAN_ALL);
    mcache_csrc(0x2u);
    v_after_clean = ddr_read_off(off);
    if (v_after_clean != 0xcccc0003u) errs++;
    /* This core's L1D writes through: a store with the cache enabled is already
     * visible in DRAM, so a deferred dirty write-back cannot be observed.  The
     * measured mode is recorded instead of failing the step. */
    if (v_before_clean != 0xbbbb0002u) {
        if (v_before_clean == 0xcccc0003u) write_through = 1;
        else errs++;
    }
    steps++;

    /* 6. By-address clean+invalidate over a 96-byte range that starts and ends
     *    mid-line, with guard words on both sides that must stay in DRAM. */
    {
        volatile uint8_t *p = (volatile uint8_t *)CPU_ADDR(off + 0x1000);
        mcache_csrc(0x3u);
        for (uint32_t i = 0; i < 256; i++) p[i] = (uint8_t)(i ^ 0x5a);
        fence();
        mcache_csrs(0x2u);
        for (uint32_t i = 0; i < 256; i++) (void)p[i];     /* fill lines */
        for (uint32_t i = 32; i < 96; i += 4) *(volatile uint32_t *)(p + i) = 0xdead0000u | i;
        for (uint32_t a = (uint32_t)(p + 32) & ~31u; a < (uint32_t)(p + 96); a += 32) {
            cctl_addr(a);
            cctl_cmd(CCTL_VA_WBINVAL);
        }
        mcache_csrc(0x2u);
        for (uint32_t i = 0; i < 32; i++) if (p[i] != (uint8_t)(i ^ 0x5a)) errs++;
        for (uint32_t i = 96; i < 128; i++) if (p[i] != (uint8_t)(i ^ 0x5a)) errs++;
        for (uint32_t i = 32; i < 96; i += 4) if (*(volatile uint32_t *)(p + i) != (0xdead0000u | i)) errs++;
        steps++;
    }

    mcache_csrc(0x3u);                    /* leave both caches off for the monitor */
    RES[0x50] = ctl0; RES[0x51] = ctl1;
    RES[0x52] = v_stale; RES[0x53] = v_after_inval;
    RES[0x54] = v_before_clean; RES[0x55] = v_after_clean;
    RES[0x56] = steps; RES[0x57] = errs; RES[0x5B] = write_through;
    {
        uint32_t mmsc_cfg; __asm__ volatile("csrr %0, 0xfc2" : "=r"(mmsc_cfg));
        uint32_t mdcm_cfg; __asm__ volatile("csrr %0, 0xfc1" : "=r"(mdcm_cfg));
        RES[0x58] = mmsc_cfg; RES[0x59] = mdcm_cfg;
        RES[0x5A] = (mmsc_cfg >> 16) & 1u;
    }
    test_begin("cache"); test_result(errs == 0);
    kv(" ctl_reset", ctl0); kv(" ctl_en", ctl1);
    kv(" stale", v_stale); kv(" inval", v_after_inval);
    kv(" pre_clean", v_before_clean); kv(" post_clean", v_after_clean);
    kv(" steps", steps); kv(" errors", errs); puts_uart("\r\n");
}

/* --------------------------------------------------------------- stress */
/* ---------------------------------------------------- capacity / aliasing (G4)
 *
 * Five label slots spread over the whole window: start of DRAM, 128 MiB,
 * 256 MiB, 512 MiB and the last 32-byte block.  Each slot gets a label derived
 * from its offset; the check is that every slot returns its own label (no
 * mirror onto a lower address) and that all five labels differ.
 */
static void test_capacity(void) {
    static const uint32_t offs[5] = {0x00000000u, 0x08000000u, 0x10000000u,
                                     0x20000000u, 0x3FFFDFE0u};
    uint32_t want[5], got[5], errs = 0, i, j;
    for (i = 0; i < 5; i++) want[i] = 0xca9a0000u ^ (offs[i] >> 6) ^ (offs[i] * 2654435761u);
    for (i = 0; i < 5; i++) *(volatile uint32_t *)CPU_ADDR(offs[i]) = want[i];
    fence();
    for (i = 5; i-- > 0;) got[i] = *(volatile uint32_t *)CPU_ADDR(offs[i]);
    for (i = 0; i < 5; i++) if (got[i] != want[i]) errs++;
    for (i = 0; i < 5; i++) for (j = i + 1; j < 5; j++) if (want[i] == want[j]) errs++;
    test_begin("capacity"); test_result(errs == 0);
    for (i = 0; i < 5; i++) { kv(" slot", offs[i]); kv(" got", got[i]); }
    kv(" errors", errs); puts_uart("\r\n");
}

/* ------------------------------------------------------ full traversal (G6)
 *
 * Walks every 32-byte block of the published window, writing a seeded value in
 * the first and last word of the block, then reads them back.  Ranges that hold
 * the running code, the parameter block and the stack are excluded and the
 * exclusion is reported.
 */
#define TRAV_SKIP(addr) (((addr) < AE350_DDR_PARAMS + 0x40u && (addr) + 32u > AE350_DDR_PARAMS) || \
                         ((addr) < (uint32_t)__bss_end && (addr) + 32u > (uint32_t)__text_start) || \
                         ((addr) >= 0x70000u && (addr) < 0x80000u))
static void test_traversal(void) {
    uint32_t blocks = 0, errs = 0, addr, seed = PARAMS[P_SEED] | 1u;
    uint32_t limit = 0x40000000u;
    if (PARAMS[P_TRAV_MB] && PARAMS[P_TRAV_MB] < 1024u) limit = PARAMS[P_TRAV_MB] * 0x100000u;
    if (limit > 0x40000000u) limit = 0x40000000u;
    uint32_t full = (PARAMS[P_FLAGS] & 8u) ? 8u : 2u;
    uint32_t lanes = full;                     /* words written per 32-byte block */
    /* One offset table shared by the write and verify passes: two-word mode
     * uses the first and last word of the block, full mode all eight. */
    static const uint32_t off_tab8[8] = {0u, 4u, 8u, 12u, 16u, 20u, 24u, 28u};
    static const uint32_t off_tab2[2] = {0u, 28u};
    /* pass 1: write */
    for (addr = AE350_DDR_BASE; addr < limit; addr += 32u) {
        if (TRAV_SKIP(addr)) continue;
        if (full == 8u) {
            for (uint32_t w = 0; w < 8u; w++)
                *(volatile uint32_t *)(addr + off_tab8[w]) = seed ^ (addr >> 5) ^ (w * 0x01010101u);
        } else {
            for (uint32_t w = 0; w < 2u; w++)
                *(volatile uint32_t *)(addr + off_tab2[w]) = (w ? (0x5a5a0000u ^ (addr >> 3)) : (seed ^ (addr >> 5)));
        }
        blocks++;
    }
    fence();
    /* pass 2: verify */
    for (addr = AE350_DDR_BASE; addr < limit; addr += 32u) {
        if (TRAV_SKIP(addr)) continue;
        for (uint32_t w = 0; w < lanes; w++) {
            uint32_t want = (full == 8u) ? (seed ^ (addr >> 5) ^ (w * 0x01010101u))
                                         : (w ? (0x5a5a0000u ^ (addr >> 3)) : (seed ^ (addr >> 5)));
            uint32_t off = (full == 8u) ? off_tab8[w] : off_tab2[w];
            uint32_t got = *(volatile uint32_t *)(addr + off);
            if (got != want) {
                if (errs < 4) { kv(" trav_bad", addr); kv(" lane", w); kv(" want", want); kv(" got", got); puts_uart("\r\n"); }
                errs++;
            }
        }
    }
    test_begin("traversal"); test_result(errs == 0);
    RES[0x110] = blocks; RES[0x111] = blocks * 4u * lanes; RES[0x112] = errs; RES[0x113] = limit;
    kv(" blocks", blocks); kv(" words_per_block", lanes);
    kv(" covered_bytes", blocks * 4u * lanes); kv(" errors", errs);
    kv(" limit", limit);
    kv(" skip_params", 0x2000u); kv(" skip_code", 0x3000u);
    kv(" skip_stack", 0x70000u); puts_uart("\r\n");
}

/* Cache acceptance through the fabric observer (flag bit 4).
 *
 * The DDR adapter records every AHB transaction it performs in the ROM status
 * window (rec_w_n/rec_r_n counters).  The CPU JTAG is therefore not needed to
 * tell whether a read actually reached DRAM: a cache hit leaves the read
 * counter unchanged, a bypassed/refilled read increments it, and a store with
 * the cache enabled increments the write counter (write-through).
 */
#define OBS_R (*(volatile uint32_t *)0x80001fccu)   /* fabric read transactions */
#define OBS_W (*(volatile uint32_t *)0x80001fc8u)   /* fabric write transactions */

static inline __attribute__((always_inline)) void dc_inval_all_cmd(void) {
    __asm__ volatile("csrw 0x7cc, %0" :: "r"(23u) : "memory");
}

/* The ROM exposes a coherent asynchronous snapshot, not live DDR wires.
 * Leave enough time for request/acknowledge round trips before observing it. */
static uint32_t observer_read(volatile uint32_t *reg) {
    for (volatile uint32_t i = 0; i < 512u; ++i) __asm__ volatile("nop");
    return *reg;
}
static void test_observer(void) {
    volatile uint32_t *p = (volatile uint32_t *)CPU_ADDR(0x206000u);
    uint32_t c0, c1, c2, c3, w0, w1, w2, errs = 0;
    mcache_csrc(0x3u);
    dc_inval_all_cmd();
    c0 = observer_read(&OBS_R);
    mcache_csrs(0x2u);                 /* D-cache on */
    (void)*p;                          /* refill from DRAM */
    c1 = observer_read(&OBS_R);
    (void)*p;                          /* must hit the L1D */
    c2 = observer_read(&OBS_R);
    dc_inval_all_cmd();                /* invalidate -> next read hits DRAM again */
    (void)*p;
    c3 = observer_read(&OBS_R);
    w0 = observer_read(&OBS_W);
    *p = 0x0badf00du;                  /* store with the cache on */
    w1 = observer_read(&OBS_W);
    cctl_cmd(7u);                      /* L1D_WB_ALL: clean the dirty line */
    __asm__ volatile("fence" ::: "memory");
    w2 = observer_read(&OBS_W);
    mcache_csrc(0x3u);
    /* A hit produces no fabric traffic; a bypassed read does; a write-back cache
     * keeps the store off the bus until the clean. */
    if (c1 == c0) errs |= 1u;
    if (c2 != c1) errs |= 2u;
    if (c3 == c2) errs |= 4u;
    if (w1 != w0) errs |= 8u;
    if (w2 == w1) errs |= 16u;
    RES[0x100] = c0; RES[0x101] = c1; RES[0x102] = c2; RES[0x103] = c3;
    RES[0x104] = w0; RES[0x105] = w1; RES[0x106] = w2; RES[0x107] = errs;
    test_begin("cache.observer"); test_result(errs == 0);
    kv(" r0", c0); kv(" r1", c1); kv(" r2", c2); kv(" r3", c3);
    kv(" w0", w0); kv(" w1", w1); kv(" w2", w2); kv(" errors", errs); puts_uart("\r\n");
}

static void test_stress(void) {
    uint32_t seconds = PARAMS[P_STRESS_SECONDS];
    uint32_t seed = PARAMS[P_SEED];
    uint32_t kb = PARAMS[P_REGION_KB];
    if (!seconds || seconds > 3600) seconds = 5;
    if (!kb || kb > 1024) kb = 256;
    volatile uint32_t *base = (volatile uint32_t *)WORK_BASE;
    uint32_t words = (kb * 1024u) / 4u;
    uint64_t deadline = (uint64_t)seconds * 800000000ull;
    uint64_t elapsed = 0;
    uint32_t last = mcycle(), started = last, iterations = 0, errs = 0;

    while (elapsed < deadline) {
        fill_block(base, words, seed + iterations);
        fence();
        errs += verify_block(base, words, seed + iterations);
        iterations++;
        uint32_t now = mcycle();
        elapsed += (uint32_t)(now - last);
        last = now;
    }
    RES[0x6C] = iterations; RES[0x6D] = errs;
    RES[0x6E] = started; RES[0x6F] = mcycle();
    test_begin("stress"); test_result(errs == 0);
    kv(" seconds", seconds); kv(" iterations", iterations);
    kv(" bytes_per_iter", words * 4); kv(" errors", errs);
    kv(" start_mcycle", started); kv(" end_mcycle", mcycle()); puts_uart("\r\n");
}

void main(void) {
    res_begin();
    puts_uart("\r\nP2TESTS start\r\n");
    kv("params", PARAMS[0]); puts_uart("\r\n");
    test_begin("payload"); puts_uart("ENTERED\r\n");
    kv(" text_start", (uint32_t)__text_start); kv(" text_end", (uint32_t)__text_end);
    kv(" data_start", (uint32_t)__data_start); kv(" bss_end", (uint32_t)__bss_end); puts_uart("\r\n");

    uint32_t seed = PARAMS[P_SEED] ? PARAMS[P_SEED] : 0x12345678u;
    test_geometry();
    test_patterns(seed);
    test_masks();
    test_exec();
    test_nonatomic_rmw();
    test_perf();
    test_cache();
    if (PARAMS[P_FLAGS] & 4u) test_observer();
    if (PARAMS[P_FLAGS] & 1u) test_capacity();
    if (PARAMS[P_FLAGS] & 2u) test_traversal();
    test_stress();

    test_begin("summary"); test_result(failures == 0);
    RES[0x80] = checks; RES[0x81] = failures;
    kv(" checks", checks); kv(" failures", failures); puts_uart("\r\n");
    puts_uart(failures ? "P2TESTS FAIL\r\n" : "P2TESTS PASS\r\n");
    RES[0x82] = 0xd0000002u;
    for (;;) { __asm__ volatile("wfi"); }
}

/* decoupling probe: payload-only change */
