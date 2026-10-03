#include <stdint.h>
#include "mini_csr.h"
extern void probe_trap(void);
static volatile unsigned ticks, external_ticks;
static unsigned phase;
static inline long sbi(unsigned ext,unsigned fn,unsigned a,unsigned b,unsigned c) {
    register unsigned r0 asm("a0")=a,r1 asm("a1")=b,r2 asm("a2")=c;
    register unsigned r6 asm("a6")=fn,r7 asm("a7")=ext;
    asm volatile("ecall":"+r"(r0),"+r"(r1),"+r"(r2):"r"(r6),"r"(r7):"a3","a4","a5","memory");return (long)r0;
}
void probe_puts(const char *s) {while(*s) {while(uart_txfull_read()){}uart_rxtx_write(*s++);}}
static void hex(unsigned v) {const char *h="0123456789ABCDEF";for(int n=7;n>=0;--n){while(uart_txfull_read()){}uart_rxtx_write(h[(v>>(4*n))&15]);}}
static uint64_t now(void) {
    unsigned h,l,h2;do{asm volatile("rdtimeh %0; rdtime %1; rdtimeh %2":"=r"(h),"=r"(l),"=r"(h2));}while(h!=h2);
    return ((uint64_t)h<<32)|l;
}
static void arm(void) {uint64_t t=now()+600000;sbi(0x54494d45,0,(unsigned)t,t>>32,0);}
int probe_mmu_trap(unsigned cause);
void probe_mmu(void);
void probe_dispatch(unsigned *frame) {
    (void)frame;unsigned cause;asm volatile("csrr %0,scause":"=r"(cause));
    if(cause==0x80000005u) {++ticks;arm();return;}
    if(cause==0x80000009u && phase==1) {timer1_ev_pending_write(1);++external_ticks;return;}
    if(probe_mmu_trap(cause))return;
    probe_puts("PROBE FAULT cause=");hex(cause);unsigned pc,value;asm volatile("csrr %0,sepc; csrr %1,stval":"=r"(pc),"=r"(value));probe_puts(" pc=");hex(pc);probe_puts(" value=");hex(value);probe_puts("\r\n");for(;;){}
}
void probe_main(unsigned hart,void *dtb) {
    (void)dtb;probe_puts("\r\nS PROBE START hart=");hex(hart);probe_puts("\r\n");
    asm volatile("csrw stvec,%0"::"r"(probe_trap));
    long base=sbi(0x10,0,0,0,0);probe_puts(base==0?"SBI BASE PASS\r\n":"SBI BASE FAIL\r\n");
    uint64_t t=now();while(now()==t){}probe_puts("TIME CSR PASS\r\n");
    volatile unsigned atomic=1;unsigned previous;
    asm volatile("amoadd.w %0,%2,(%1)":"=r"(previous):"r"(&atomic),"r"(2):"memory");
    if(previous!=1 || atomic!=3){probe_puts("AMO FAIL\r\n");for(;;){}}
    unsigned loaded,status;do{asm volatile("lr.w %0,(%2); sc.w %1,%3,(%2)":"=&r"(loaded),"=&r"(status):"r"(&atomic),"r"(7):"memory");}while(status);
    if(loaded!=3 || atomic!=7){probe_puts("LRSC FAIL\r\n");for(;;){}}probe_puts("ATOMIC PASS\r\n");
    arm();asm volatile("csrs sie,%0; csrsi sstatus,2"::"r"(32));
    while(ticks<10)asm volatile("wfi");
    asm volatile("csrci sstatus,2; csrc sie,%0"::"r"(32));sbi(0x54494d45,0,~0u,~0u,0);
    probe_puts("S TIMER PASS ticks=");hex(ticks);probe_puts("\r\n");
    phase=1;timer1_en_write(0);timer1_load_write(60000);timer1_reload_write(60000);timer1_ev_pending_write(1);timer1_ev_enable_write(1);
    asm volatile("csrw 0x9c0,%0; csrs sie,%1; csrsi sstatus,2"::"r"(1u<<TIMER1_INTERRUPT),"r"(512));
    timer1_en_write(1);while(external_ticks<10)asm volatile("wfi");
    asm volatile("csrci sstatus,2; csrw 0x9c0,zero");timer1_en_write(0);timer1_ev_enable_write(0);
    probe_puts("S EXTERNAL PASS ticks=");hex(external_ticks);probe_puts("\r\n");
    probe_mmu();probe_puts("OPENSBI PROBE PASS\r\n");
    /* Validation endpoint: request warm reset without changing Flash. */
    for(;;)if(!uart_rxempty_read()) {
        unsigned c=uart_rxtx_read();uart_ev_pending_write(2);
        if(c=='r') {probe_puts("SBI RESET REQUEST\r\n");sbi(0x53525354,0,2,0,0);probe_puts("SBI RESET FAILED\r\n");}
    }
}
