#include <stdint.h>
extern void probe_puts(const char *s),probe_enter_user(void),probe_user_return(void);
extern unsigned char probe_user_code[],probe_user_end[];
static void hex(unsigned v) {
    const char *h = "0123456789ABCDEF";
    for (int n = 7; n >= 0; --n) {
        char c = h[(v >> (4 * n)) & 15];
        /* SBI putchar */
        register unsigned r0 asm("a0") = (unsigned char)c;
        register unsigned r1 asm("a1") = 0;
        register unsigned r7 asm("a7") = 1;
        asm volatile("ecall" : "+r"(r0) : "r"(r1), "r"(r7) : "memory");
    }
}
static uint32_t root[1024] __attribute__((aligned(4096)));
static uint32_t user_pages[1024] __attribute__((aligned(4096)));
static unsigned active,faults,s_fault;
static unsigned last_cause,last_pc,last_val,last_st;
static void fail(unsigned where, unsigned cause, unsigned pc, unsigned value, unsigned status) {
    probe_puts("USER MMU FAIL where=");
    hex(where);
    probe_puts(" cause=");
    hex(cause);
    probe_puts(" pc=");
    hex(pc);
    probe_puts(" val=");
    hex(value);
    probe_puts(" st=");
    hex(status);
    probe_puts(" f=");
    hex(faults);
    probe_puts("\r\n");
    /* A previous accepted trap was recorded; show it so the failing context
       is not lost when this trap is only its consequence. */
    if(s_fault){
        probe_puts("RECORDED cause=");
        hex(last_cause);
        probe_puts(" pc=");
        hex(last_pc);
        probe_puts(" val=");
        hex(last_val);
        probe_puts(" st=");
        hex(last_st);
        probe_puts(" faulted=");
        hex(faults);
        probe_puts("\r\n");
    }
    for(;;){}
}
int probe_mmu_trap(unsigned cause) {
    if(!active)return 0;
    unsigned pc,value,status;
    asm volatile("csrr %0,sepc; csrr %1,stval; csrr %2,sstatus":"=r"(pc),"=r"(value),"=r"(status));
    if(status&256) {
        last_cause = cause; last_pc = pc; last_val = value; last_st = status;
        s_fault = 1;
        asm volatile("csrw sepc,%0"::"r"(pc+4));
        return 1;
    }
    if((faults==0 && cause==13 && value==0x01100000) ||
       (faults==1 && cause==13 && value==0xf0000000) ||
       (faults==2 && cause==15 && value==0x00400000)) {
        ++faults;asm volatile("csrw sepc,%0"::"r"(pc+4));return 1;
    }
    if(cause==8 && faults==3) {
        asm volatile("csrw sepc,%0; csrs sstatus,%1"::"r"(probe_user_return),"r"(256));return 1;
    }
    fail(2, cause, pc, value, status);return 0;
}
static void cctl_wb(unsigned long start, unsigned long size) {
    unsigned long a;
    for (a = start & ~31u; a < start + size; a += 32) {
        asm volatile("csrw 0x80b, %0; csrw 0x80c, %1" :: "r"(a), "r"(2) : "memory");
    }
}
void probe_mmu(void) {
    // Kernel direct map: DDR and MMIO are supervisor-only superpages.
    for(unsigned i=0;i<0x20;++i)root[i]=(i<<20)|0xcf;
    root[0x3c0]=(0xf0000000u>>2)|0xc7;
    root[1]=((uint32_t)user_pages>>2)|1;
    user_pages[0]=(0x00070000u>>2)|0xdb; // user RX, accessed/dirty
    user_pages[2]=(0x00072000u>>2)|0xd7; // user RW, accessed/dirty
    for(unsigned i=0;i<(unsigned)(probe_user_end-probe_user_code);++i)
        ((unsigned char *)0x00070000)[i]=probe_user_code[i];
    ((volatile unsigned *)0x00072000)[0]=0;((volatile unsigned *)0x00072000)[1]=0;
    cctl_wb((unsigned long)root, 4096);
    cctl_wb((unsigned long)user_pages, 4096);
    cctl_wb(0x00070000, 4096);
    cctl_wb(0x00072000, 4096);
    asm volatile("fence rw,rw; fence.i":::"memory");

    /* Test DDR writeback: invalidate user_pages line and read back */
    asm volatile("csrw 0x80b, %0; csrw 0x80c, %1; fence rw,rw" :: "r"((unsigned long)user_pages), "r"(0) : "memory");
    probe_puts("after inv up[0]="); hex(user_pages[0]); probe_puts("\r\n");

    probe_puts("root[1]="); hex(root[1]);
    probe_puts(" up[0]="); hex(user_pages[0]);
    probe_puts(" satp="); hex(0x80000000u|((uint32_t)root>>12));
    probe_puts("\r\n");

    /* Test 1: root[1] as superpage */
    root[1] = (1 << 20) | 0xcf;
    cctl_wb((unsigned long)root, 4096);
    asm volatile("fence rw,rw; fence.i":::"memory");
    active=1;asm volatile("csrw satp,%0; sfence.vma"::"r"(0x80000000u|((uint32_t)root>>12)):"memory");
    probe_puts("superpage read 0x400000="); hex(*(volatile uint32_t *)0x00400000); probe_puts("\r\n");
    active=0;asm volatile("csrw satp,zero; sfence.vma":::"memory");

#define TEST_PAGING(name, r1_val, u0_val, sum_val) do { \
        root[1] = (r1_val); \
        user_pages[0] = (u0_val); \
        cctl_wb((unsigned long)root, 4096); \
        cctl_wb((unsigned long)user_pages, 4096); \
        asm volatile("csrw 0x80c, %0; fence rw,rw; fence.i" :: "r"(6) : "memory"); \
        s_fault = 0; \
        active = 1; \
        if (sum_val) asm volatile("csrs sstatus, %0" :: "r"(0x40000u) : "memory"); \
        else asm volatile("csrc sstatus, %0" :: "r"(0x40000u) : "memory"); \
        asm volatile("csrw satp, %0; sfence.vma" :: "r"(0x80000000u | ((uint32_t)root >> 12)) : "memory"); \
        uint32_t val = *(volatile uint32_t *)0x00400000; \
        active = 0; \
        asm volatile("csrw satp, zero; sfence.vma" ::: "memory"); \
        probe_puts(name); \
        if (s_fault) { \
            probe_puts(" FAULT cause="); hex(last_cause); \
            probe_puts(" pc="); hex(last_pc); \
            probe_puts(" val="); hex(last_val); \
        } else { \
            probe_puts(" OK val="); hex(val); \
        } \
        probe_puts("\r\n"); \
    } while (0)

    TEST_PAGING("T1: r1=|1 u0=0xcf sum=0", ((uint32_t)user_pages >> 2) | 1, (0x00070000u >> 2) | 0xcf, 0);
    TEST_PAGING("T2: r1=|1 u0=0xcf sum=1", ((uint32_t)user_pages >> 2) | 1, (0x00070000u >> 2) | 0xcf, 1);
    TEST_PAGING("T3: r1=|0xc1 u0=0xcf sum=0", ((uint32_t)user_pages >> 2) | 0xc1, (0x00070000u >> 2) | 0xcf, 0);
    TEST_PAGING("T4: r1=|1 u0=0xef (G=1) sum=0", ((uint32_t)user_pages >> 2) | 1, (0x00070000u >> 2) | 0xef, 0);
    TEST_PAGING("T5: r1=|1 u0=0xdb (U=1) sum=1", ((uint32_t)user_pages >> 2) | 1, (0x00070000u >> 2) | 0xdb, 1);
    TEST_PAGING("T6: r1=|0x11 u0=0xdb (U=1) sum=1", ((uint32_t)user_pages >> 2) | 0x11, (0x00070000u >> 2) | 0xdb, 1);

    uint32_t *up_fix = (uint32_t *)0x00078000;
    up_fix[0] = (0x00070000u >> 2) | 0xcf;
    cctl_wb(0x00078000, 4096);
    root[1] = (0x00078000u >> 2) | 1;
    cctl_wb((unsigned long)root, 4096);
    asm volatile("csrw 0x80c, %0; fence rw,rw; fence.i" :: "r"(6) : "memory");
    s_fault = 0; active = 1;
    asm volatile("csrw satp, %0; sfence.vma" :: "r"(0x80000000u | ((uint32_t)root >> 12)) : "memory");
    uint32_t val_fix = *(volatile uint32_t *)0x00400000;
    active = 0; asm volatile("csrw satp, zero; sfence.vma" ::: "memory");
    probe_puts("T7: fixed 0x78000");
    if (s_fault) { probe_puts(" FAULT cause="); hex(last_cause); probe_puts(" val="); hex(last_val); }
    else { probe_puts(" OK val="); hex(val_fix); }
    probe_puts("\r\n");

    /* Also test reading through satp identity map: can we read user_pages[0] directly under satp? */
    root[1] = ((uint32_t)user_pages >> 2) | 1;
    user_pages[0] = 0x11223344;
    cctl_wb((unsigned long)user_pages, 4096);
    asm volatile("csrw 0x80c, %0; fence rw,rw; fence.i" :: "r"(6) : "memory");
    s_fault = 0; active = 1;
    asm volatile("csrw satp, %0; sfence.vma" :: "r"(0x80000000u | ((uint32_t)root >> 12)) : "memory");
    uint32_t id_read = *(volatile uint32_t *)user_pages;
    active = 0; asm volatile("csrw satp, zero; sfence.vma" ::: "memory");
    probe_puts("T8: satp id map user_pages read="); hex(id_read); probe_puts("\r\n");

    probe_enter_user();
    asm volatile("csrw satp,zero; sfence.vma":::"memory");active=0;
    if(faults!=3 || ((volatile unsigned *)0x00072000)[0]!=0x12345678 || !((volatile unsigned *)0x00072000)[1]) {
        fail(3, 0, 0, ((volatile unsigned *)0x00072000)[0], ((volatile unsigned *)0x00072000)[1]);
    }
    probe_puts("U ECALL / TIME PASS\r\nUSER MMU kernel/MMIO/RX PASS\r\n");
}
