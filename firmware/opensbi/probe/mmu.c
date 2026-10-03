#include <stdint.h>
extern void probe_puts(const char *s),probe_enter_user(void),probe_user_return(void);
extern unsigned char probe_user_code[],probe_user_end[];
static uint32_t root[1024] __attribute__((aligned(4096)));
static uint32_t user_pages[1024] __attribute__((aligned(4096)));
static unsigned active,faults;
static void fail(void) {probe_puts("USER MMU FAIL\r\n");for(;;){}}
int probe_mmu_trap(unsigned cause) {
    if(!active)return 0;
    unsigned pc,value,status;
    asm volatile("csrr %0,sepc; csrr %1,stval; csrr %2,sstatus":"=r"(pc),"=r"(value),"=r"(status));
    if(status&256)fail();
    if((faults==0 && cause==13 && value==0x41100000) ||
       (faults==1 && cause==13 && value==0xf0000000) ||
       (faults==2 && cause==15 && value==0x00400000)) {
        ++faults;asm volatile("csrw sepc,%0"::"r"(pc+4));return 1;
    }
    if(cause==8 && faults==3) {
        asm volatile("csrw sepc,%0; csrs sstatus,%1"::"r"(probe_user_return),"r"(256));return 1;
    }
    fail();return 0;
}
void probe_mmu(void) {
    // Kernel direct map: DDR and MMIO are supervisor-only superpages.
    for(unsigned i=0x100;i<0x120;++i)root[i]=(i<<20)|0xcf;
    root[0x3c0]=(0xf0000000u>>2)|0xc7;
    root[1]=((uint32_t)user_pages>>2)|1;
    user_pages[0]=(0x41400000u>>2)|0x5b; // user RX, accessed
    user_pages[2]=(0x41402000u>>2)|0xd7; // user RW, accessed/dirty
    for(unsigned i=0;i<(unsigned)(probe_user_end-probe_user_code);++i)
        ((unsigned char *)0x41400000)[i]=probe_user_code[i];
    ((volatile unsigned *)0x41402000)[0]=0;((volatile unsigned *)0x41402000)[1]=0;
    asm volatile("fence rw,rw; .word 0x0000500f; fence.i":::"memory");
    active=1;asm volatile("csrw satp,%0; sfence.vma"::"r"(0x80000000u|((uint32_t)root>>12)):"memory");
    probe_enter_user();
    asm volatile("csrw satp,zero; sfence.vma":::"memory");active=0;
    if(faults!=3 || ((volatile unsigned *)0x41402000)[0]!=0x12345678 || !((volatile unsigned *)0x41402000)[1])fail();
    probe_puts("U ECALL / TIME PASS\r\nUSER MMU kernel/MMIO/RX PASS\r\n");
}
