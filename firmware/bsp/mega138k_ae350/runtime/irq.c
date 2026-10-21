#include "irq.h"
#include "ae350_address_map.h"

#define SOURCES 64u
#define CAUSE_SOFTWARE 3u
#define CAUSE_TIMER 7u
#define CAUSE_EXTERNAL 11u
static irq_handler_t handlers[SOURCES];
static void *contexts[SOURCES];
static irq_handler_t local_handler[12];
static void *local_context[12];
static uint32_t unhandled;
static uint32_t last_cause;

static volatile uint32_t *reg(uint32_t addr) {
    return (volatile uint32_t *)(uintptr_t)addr;
}

static int known(uint32_t source) {
    return source > 0u && source < SOURCES;
}

int irq_attach(uint32_t source, irq_handler_t handler, void *context) {
    if (!known(source) || !handler) return 1;
    handlers[source] = handler;
    contexts[source] = context;
    return 0;
}

int irq_attach_local(uint32_t code, irq_handler_t handler, void *context) {
    if ((code != CAUSE_SOFTWARE && code != CAUSE_TIMER) || !handler) return 1;
    local_handler[code] = handler;
    local_context[code] = context;
    return 0;
}

void irq_unmask_all(void) {
    reg(AE350_PLIC_ENABLE)[0] = 0;
    reg(AE350_PLIC_ENABLE)[1] = 0;
}

int irq_mask(uint32_t source, unsigned enabled) {
    if (!known(source)) return 1;
    volatile uint32_t *word = &reg(AE350_PLIC_ENABLE)[source >> 5];
    uint32_t bit = 1u << (source & 31u);
    if (enabled) *word |= bit;
    else *word &= ~bit;
    return 0;
}

uint32_t plic_claim(void) { return *reg(AE350_PLIC_CLAIM); }
void plic_complete(uint32_t source) { *reg(AE350_PLIC_CLAIM) = source; }
uint32_t plic_pending(unsigned word) { return reg(AE350_PLIC_PENDING)[word]; }
void plic_priority(uint32_t source, uint32_t priority) {
    if (known(source)) reg(AE350_PLIC_BASE)[source] = priority;
}
void plic_threshold(uint32_t threshold) { *reg(AE350_PLIC_THRESHOLD) = threshold; }

void irq_global(unsigned enabled) {
    if (enabled) {
        __asm__ volatile("csrs mie, %0" :: "r"(1u << 11) : "memory");
        __asm__ volatile("csrs mstatus, %0" :: "r"(1u << 3) : "memory");
    } else {
        __asm__ volatile("csrc mstatus, %0" :: "r"(1u << 3) : "memory");
        __asm__ volatile("csrc mie, %0" :: "r"(1u << 11) : "memory");
    }
}

uint32_t irq_unhandled(void) { return unhandled; }
uint32_t irq_last_cause(void) { return last_cause; }

void irq_dispatch(uint32_t cause) {
    uint32_t code = cause & 0x7fffffffu;
    last_cause = cause;
    if ((cause & 0x80000000u) == 0u) {
        unhandled++;
        return;
    }
    if (code == CAUSE_SOFTWARE || code == CAUSE_TIMER) {
        if (!local_handler[code]) {
            unhandled++;
            return;
        }
        local_handler[code](local_context[code]);
        return;
    }
    if (code != CAUSE_EXTERNAL) {
        unhandled++;
        return;
    }
    uint32_t source = plic_claim();
    if (!known(source) || !handlers[source]) {
        unhandled++;
        if (source) plic_complete(source);
        return;
    }
    handlers[source](contexts[source]);
    plic_complete(source);
}
