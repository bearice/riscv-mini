/*
 * Userspace UP atomic runtime for AE350 (RV32IMFD without A extension).
 *
 * External DDR3 on AE350 lacks exclusive/bus-lock support for AMO/LR/SC on
 * non-read-allocated cache lines. All user synchronization primitives route
 * 32-bit compare-and-exchange through Linux syscall 257 (sys_riscv_up_cmpxchg)
 * while normal aligned 1/2/4-byte loads/stores execute in user mode.
 */
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifndef __NR_riscv_up_cmpxchg
#define __NR_riscv_up_cmpxchg 257u
#endif
#ifndef __NR_sched_yield
#define __NR_sched_yield 124u
#endif

static inline uint32_t ae350_cas32(volatile uint32_t *ptr, uint32_t expected,
				   uint32_t desired)
{
	register uint32_t a0 __asm__("a0") = (uint32_t)(uintptr_t)ptr;
	register uint32_t a1 __asm__("a1") = expected;
	register uint32_t a2 __asm__("a2") = desired;
	register uint32_t a7 __asm__("a7") = __NR_riscv_up_cmpxchg;

	__asm__ volatile("ecall"
			 : "+r"(a0)
			 : "r"(a1), "r"(a2), "r"(a7)
			 : "memory");
	return a0;
}

static inline void ae350_sched_yield(void)
{
	register uint32_t a0 __asm__("a0") = 0u;
	register uint32_t a7 __asm__("a7") = __NR_sched_yield;

	__asm__ volatile("ecall" : "+r"(a0) : "r"(a7) : "memory");
}

static inline void ae350_fence(void)
{
	__asm__ volatile("fence rw,rw" ::: "memory");
}

/* Hashed lock table for 8-byte / fallback generic atomics (in .bss). */
#define LOCK8_COUNT 64u
static volatile uint32_t g_lock8[LOCK8_COUNT];

static volatile uint32_t *lock8_for(const volatile void *ptr)
{
	uintptr_t v = (uintptr_t)ptr;
	return &g_lock8[(v >> 3) & (LOCK8_COUNT - 1u)];
}

static void lock8_acquire(volatile uint32_t *lk)
{
	unsigned spins = 0;

	while (ae350_cas32(lk, 0u, 1u) != 0u) {
		if (++spins >= 64u) {
			spins = 0;
			ae350_sched_yield();
		}
	}
	ae350_fence();
}

static void lock8_release(volatile uint32_t *lk)
{
	ae350_fence();
	*lk = 0u;
	ae350_fence();
}

/* Sub-word CAS on the enclosing 4-byte aligned word. */
static uint32_t rmw_subword(volatile void *ptr, unsigned bytes, int is_cas,
			    uint32_t expected, uint32_t newval,
			    uint32_t (*fn)(uint32_t cur, uint32_t arg))
{
	uintptr_t addr = (uintptr_t)ptr;
	volatile uint32_t *wordp = (volatile uint32_t *)(addr & ~(uintptr_t)3u);
	unsigned shift = (unsigned)((addr & 3u) * 8u);
	uint32_t mask = (bytes == 1u) ? 0xFFu : 0xFFFFu;

	for (;;) {
		uint32_t word_old = *wordp;
		uint32_t sub_old = (word_old >> shift) & mask;
		if (is_cas && sub_old != (expected & mask))
			return sub_old;
		uint32_t sub_new = fn ? fn(sub_old, newval) : newval;
		uint32_t word_new =
			(word_old & ~(mask << shift)) | ((sub_new & mask) << shift);
		if (ae350_cas32(wordp, word_old, word_new) == word_old)
			return sub_old;
	}
}

void __sync_synchronize(void)
{
	ae350_fence();
}

bool __atomic_is_lock_free(size_t size, const volatile void *ptr)
{
	if (size == 0)
		return true;
	if (size <= 4)
		return (((uintptr_t)ptr) & (size - 1u)) == 0;
	return false;
}

uint8_t __atomic_load_1(const volatile void *ptr, int memorder)
{
	(void)memorder;
	uint8_t v = *(const volatile uint8_t *)ptr;
	ae350_fence();
	return v;
}

uint16_t __atomic_load_2(const volatile void *ptr, int memorder)
{
	(void)memorder;
	uint16_t v = *(const volatile uint16_t *)ptr;
	ae350_fence();
	return v;
}

uint32_t __atomic_load_4(const volatile void *ptr, int memorder)
{
	(void)memorder;
	uint32_t v = *(const volatile uint32_t *)ptr;
	ae350_fence();
	return v;
}

uint64_t __atomic_load_8(const volatile void *ptr, int memorder)
{
	(void)memorder;
	volatile uint32_t *lk = lock8_for(ptr);
	const volatile uint32_t *w = (const volatile uint32_t *)ptr;
	uint32_t lo, hi;

	lock8_acquire(lk);
	lo = w[0];
	hi = w[1];
	lock8_release(lk);
	return ((uint64_t)hi << 32) | lo;
}

void __atomic_store_1(volatile void *ptr, uint8_t val, int memorder)
{
	(void)memorder;
	ae350_fence();
	*(volatile uint8_t *)ptr = val;
	ae350_fence();
}

void __atomic_store_2(volatile void *ptr, uint16_t val, int memorder)
{
	(void)memorder;
	ae350_fence();
	*(volatile uint16_t *)ptr = val;
	ae350_fence();
}

void __atomic_store_4(volatile void *ptr, uint32_t val, int memorder)
{
	(void)memorder;
	ae350_fence();
	*(volatile uint32_t *)ptr = val;
	ae350_fence();
}

void __atomic_store_8(volatile void *ptr, uint64_t val, int memorder)
{
	(void)memorder;
	volatile uint32_t *lk = lock8_for(ptr);
	volatile uint32_t *w = (volatile uint32_t *)ptr;

	lock8_acquire(lk);
	w[0] = (uint32_t)val;
	w[1] = (uint32_t)(val >> 32);
	lock8_release(lk);
}

uint8_t __atomic_exchange_1(volatile void *ptr, uint8_t val, int memorder)
{
	(void)memorder;
	return (uint8_t)rmw_subword(ptr, 1u, 0, 0u, val, NULL);
}

uint16_t __atomic_exchange_2(volatile void *ptr, uint16_t val, int memorder)
{
	(void)memorder;
	return (uint16_t)rmw_subword(ptr, 2u, 0, 0u, val, NULL);
}

uint32_t __atomic_exchange_4(volatile void *ptr, uint32_t val, int memorder)
{
	(void)memorder;
	volatile uint32_t *p = (volatile uint32_t *)ptr;

	for (;;) {
		uint32_t cur = *p;
		if (ae350_cas32(p, cur, val) == cur)
			return cur;
	}
}

uint64_t __atomic_exchange_8(volatile void *ptr, uint64_t val, int memorder)
{
	(void)memorder;
	volatile uint32_t *lk = lock8_for(ptr);
	volatile uint32_t *w = (volatile uint32_t *)ptr;
	uint32_t lo, hi;

	lock8_acquire(lk);
	lo = w[0];
	hi = w[1];
	w[0] = (uint32_t)val;
	w[1] = (uint32_t)(val >> 32);
	lock8_release(lk);
	return ((uint64_t)hi << 32) | lo;
}

bool __atomic_compare_exchange_1(volatile void *ptr, void *expected,
				 uint8_t desired, bool weak, int sm, int fm)
{
	(void)weak; (void)sm; (void)fm;
	uint8_t exp = *(uint8_t *)expected;
	uint8_t old = (uint8_t)rmw_subword(ptr, 1u, 1, exp, desired, NULL);
	if (old == exp)
		return true;
	*(uint8_t *)expected = old;
	return false;
}

bool __atomic_compare_exchange_2(volatile void *ptr, void *expected,
				 uint16_t desired, bool weak, int sm, int fm)
{
	(void)weak; (void)sm; (void)fm;
	uint16_t exp = *(uint16_t *)expected;
	uint16_t old = (uint16_t)rmw_subword(ptr, 2u, 1, exp, desired, NULL);
	if (old == exp)
		return true;
	*(uint16_t *)expected = old;
	return false;
}

bool __atomic_compare_exchange_4(volatile void *ptr, void *expected,
				 uint32_t desired, bool weak, int sm, int fm)
{
	(void)weak; (void)sm; (void)fm;
	uint32_t exp = *(uint32_t *)expected;
	uint32_t old = ae350_cas32((volatile uint32_t *)ptr, exp, desired);
	if (old == exp)
		return true;
	*(uint32_t *)expected = old;
	return false;
}

bool __atomic_compare_exchange_8(volatile void *ptr, void *expected,
				 uint64_t desired, bool weak, int sm, int fm)
{
	(void)weak; (void)sm; (void)fm;
	volatile uint32_t *lk = lock8_for(ptr);
	volatile uint32_t *w = (volatile uint32_t *)ptr;
	uint64_t exp = *(uint64_t *)expected;
	uint64_t old;
	bool ok;

	lock8_acquire(lk);
	old = ((uint64_t)w[1] << 32) | w[0];
	if (old == exp) {
		w[0] = (uint32_t)desired;
		w[1] = (uint32_t)(desired >> 32);
		ok = true;
	} else {
		ok = false;
	}
	lock8_release(lk);
	if (!ok)
		*(uint64_t *)expected = old;
	return ok;
}

#define DEF_OP_FN(name, expr) \
	static uint32_t op32_##name(uint32_t cur, uint32_t arg) { return (expr); } \
	static uint64_t op64_##name(uint64_t cur, uint64_t arg) { \
		return (uint64_t)(expr); \
	}

static uint32_t op32_add(uint32_t cur, uint32_t arg) { return cur + arg; }
static uint32_t op32_sub(uint32_t cur, uint32_t arg) { return cur - arg; }
static uint32_t op32_and(uint32_t cur, uint32_t arg) { return cur & arg; }
static uint32_t op32_or(uint32_t cur, uint32_t arg)  { return cur | arg; }
static uint32_t op32_xor(uint32_t cur, uint32_t arg) { return cur ^ arg; }
static uint32_t op32_nand(uint32_t cur, uint32_t arg) { return ~(cur & arg); }

static uint64_t op64_add(uint64_t cur, uint64_t arg) { return cur + arg; }
static uint64_t op64_sub(uint64_t cur, uint64_t arg) { return cur - arg; }
static uint64_t op64_and(uint64_t cur, uint64_t arg) { return cur & arg; }
static uint64_t op64_or(uint64_t cur, uint64_t arg)  { return cur | arg; }
static uint64_t op64_xor(uint64_t cur, uint64_t arg) { return cur ^ arg; }
static uint64_t op64_nand(uint64_t cur, uint64_t arg) { return ~(cur & arg); }

static uint32_t rmw32(volatile void *ptr, uint32_t arg,
		      uint32_t (*fn)(uint32_t, uint32_t))
{
	volatile uint32_t *p = (volatile uint32_t *)ptr;

	for (;;) {
		uint32_t cur = *p;
		uint32_t nxt = fn(cur, arg);
		if (ae350_cas32(p, cur, nxt) == cur)
			return cur;
	}
}

static uint64_t rmw64(volatile void *ptr, uint64_t arg,
		      uint64_t (*fn)(uint64_t, uint64_t))
{
	volatile uint32_t *lk = lock8_for(ptr);
	volatile uint32_t *w = (volatile uint32_t *)ptr;
	uint64_t cur, nxt;

	lock8_acquire(lk);
	cur = ((uint64_t)w[1] << 32) | w[0];
	nxt = fn(cur, arg);
	w[0] = (uint32_t)nxt;
	w[1] = (uint32_t)(nxt >> 32);
	lock8_release(lk);
	return cur;
}

#define DEFINE_RMW_OP(op) \
uint8_t __atomic_fetch_##op##_1(volatile void *ptr, uint8_t val, int m) { \
	(void)m; return (uint8_t)rmw_subword(ptr, 1u, 0, 0u, val, op32_##op); \
} \
uint16_t __atomic_fetch_##op##_2(volatile void *ptr, uint16_t val, int m) { \
	(void)m; return (uint16_t)rmw_subword(ptr, 2u, 0, 0u, val, op32_##op); \
} \
uint32_t __atomic_fetch_##op##_4(volatile void *ptr, uint32_t val, int m) { \
	(void)m; return rmw32(ptr, val, op32_##op); \
} \
uint64_t __atomic_fetch_##op##_8(volatile void *ptr, uint64_t val, int m) { \
	(void)m; return rmw64(ptr, val, op64_##op); \
} \
uint8_t __atomic_##op##_fetch_1(volatile void *ptr, uint8_t val, int m) { \
	(void)m; return (uint8_t)op32_##op(rmw_subword(ptr, 1u, 0, 0u, val, op32_##op), val); \
} \
uint16_t __atomic_##op##_fetch_2(volatile void *ptr, uint16_t val, int m) { \
	(void)m; return (uint16_t)op32_##op(rmw_subword(ptr, 2u, 0, 0u, val, op32_##op), val); \
} \
uint32_t __atomic_##op##_fetch_4(volatile void *ptr, uint32_t val, int m) { \
	(void)m; return op32_##op(rmw32(ptr, val, op32_##op), val); \
} \
uint64_t __atomic_##op##_fetch_8(volatile void *ptr, uint64_t val, int m) { \
	(void)m; return op64_##op(rmw64(ptr, val, op64_##op), val); \
} \
uint8_t __sync_fetch_and_##op##_1(volatile void *ptr, uint8_t val) { \
	return __atomic_fetch_##op##_1(ptr, val, 5); \
} \
uint16_t __sync_fetch_and_##op##_2(volatile void *ptr, uint16_t val) { \
	return __atomic_fetch_##op##_2(ptr, val, 5); \
} \
uint32_t __sync_fetch_and_##op##_4(volatile void *ptr, uint32_t val) { \
	return __atomic_fetch_##op##_4(ptr, val, 5); \
} \
uint64_t __sync_fetch_and_##op##_8(volatile void *ptr, uint64_t val) { \
	return __atomic_fetch_##op##_8(ptr, val, 5); \
} \
uint8_t __sync_##op##_and_fetch_1(volatile void *ptr, uint8_t val) { \
	return __atomic_##op##_fetch_1(ptr, val, 5); \
} \
uint16_t __sync_##op##_and_fetch_2(volatile void *ptr, uint16_t val) { \
	return __atomic_##op##_fetch_2(ptr, val, 5); \
} \
uint32_t __sync_##op##_and_fetch_4(volatile void *ptr, uint32_t val) { \
	return __atomic_##op##_fetch_4(ptr, val, 5); \
} \
uint64_t __sync_##op##_and_fetch_8(volatile void *ptr, uint64_t val) { \
	return __atomic_##op##_fetch_8(ptr, val, 5); \
}

DEFINE_RMW_OP(add)
DEFINE_RMW_OP(sub)
DEFINE_RMW_OP(and)
DEFINE_RMW_OP(or)
DEFINE_RMW_OP(xor)
DEFINE_RMW_OP(nand)

uint8_t __sync_val_compare_and_swap_1(volatile void *ptr, uint8_t oldv, uint8_t newv)
{
	return (uint8_t)rmw_subword(ptr, 1u, 1, oldv, newv, NULL);
}

uint16_t __sync_val_compare_and_swap_2(volatile void *ptr, uint16_t oldv, uint16_t newv)
{
	return (uint16_t)rmw_subword(ptr, 2u, 1, oldv, newv, NULL);
}

uint32_t __sync_val_compare_and_swap_4(volatile void *ptr, uint32_t oldv, uint32_t newv)
{
	return ae350_cas32((volatile uint32_t *)ptr, oldv, newv);
}

uint64_t __sync_val_compare_and_swap_8(volatile void *ptr, uint64_t oldv, uint64_t newv)
{
	uint64_t cur = oldv;
	__atomic_compare_exchange_8(ptr, &cur, newv, false, 5, 5);
	return cur;
}

bool __sync_bool_compare_and_swap_1(volatile void *ptr, uint8_t oldv, uint8_t newv)
{
	return __sync_val_compare_and_swap_1(ptr, oldv, newv) == oldv;
}

bool __sync_bool_compare_and_swap_2(volatile void *ptr, uint16_t oldv, uint16_t newv)
{
	return __sync_val_compare_and_swap_2(ptr, oldv, newv) == oldv;
}

bool __sync_bool_compare_and_swap_4(volatile void *ptr, uint32_t oldv, uint32_t newv)
{
	return ae350_cas32((volatile uint32_t *)ptr, oldv, newv) == oldv;
}

bool __sync_bool_compare_and_swap_8(volatile void *ptr, uint64_t oldv, uint64_t newv)
{
	uint64_t cur = oldv;
	return __atomic_compare_exchange_8(ptr, &cur, newv, false, 5, 5);
}

uint8_t __sync_lock_test_and_set_1(volatile void *ptr, uint8_t val)
{
	return __atomic_exchange_1(ptr, val, 5);
}

uint16_t __sync_lock_test_and_set_2(volatile void *ptr, uint16_t val)
{
	return __atomic_exchange_2(ptr, val, 5);
}

uint32_t __sync_lock_test_and_set_4(volatile void *ptr, uint32_t val)
{
	return __atomic_exchange_4(ptr, val, 5);
}

uint64_t __sync_lock_test_and_set_8(volatile void *ptr, uint64_t val)
{
	return __atomic_exchange_8(ptr, val, 5);
}

void __sync_lock_release_1(volatile void *ptr) { __atomic_store_1(ptr, 0u, 3); }
void __sync_lock_release_2(volatile void *ptr) { __atomic_store_2(ptr, 0u, 3); }
void __sync_lock_release_4(volatile void *ptr) { __atomic_store_4(ptr, 0u, 3); }
void __sync_lock_release_8(volatile void *ptr) { __atomic_store_8(ptr, 0u, 3); }

bool __atomic_test_and_set(volatile void *ptr, int memorder)
{
	return __atomic_exchange_1(ptr, 1u, memorder) != 0u;
}

void __atomic_clear(volatile void *ptr, int memorder)
{
	__atomic_store_1(ptr, 0u, memorder);
}

void __atomic_load(size_t size, const volatile void *src, void *dest, int m)
{
	(void)m;
	volatile uint32_t *lk = lock8_for(src);
	lock8_acquire(lk);
	memcpy(dest, (const void *)src, size);
	lock8_release(lk);
}

void __atomic_store(size_t size, volatile void *dest, void *src, int m)
{
	(void)m;
	volatile uint32_t *lk = lock8_for(dest);
	lock8_acquire(lk);
	memcpy((void *)dest, src, size);
	lock8_release(lk);
}

void __atomic_exchange(size_t size, volatile void *ptr, void *val, void *ret, int m)
{
	(void)m;
	volatile uint32_t *lk = lock8_for(ptr);
	lock8_acquire(lk);
	memcpy(ret, (const void *)ptr, size);
	memcpy((void *)ptr, val, size);
	lock8_release(lk);
}

bool __atomic_compare_exchange(size_t size, volatile void *ptr, void *expected,
			       void *desired, int sm, int fm)
{
	(void)sm; (void)fm;
	volatile uint32_t *lk = lock8_for(ptr);
	bool ok;

	lock8_acquire(lk);
	if (memcmp((const void *)ptr, expected, size) == 0) {
		memcpy((void *)ptr, desired, size);
		ok = true;
	} else {
		memcpy(expected, (const void *)ptr, size);
		ok = false;
	}
	lock8_release(lk);
	return ok;
}
