/* Single-hart LiteX/VexRiscv platform. Device drivers belong to S-mode. */
#include <sbi/sbi_console.h>
#include <sbi/sbi_platform.h>
#include <sbi/sbi_timer.h>
#include <sbi/sbi_system.h>
#include <sbi/sbi_ecall_interface.h>
#include <sbi/riscv_asm.h>
#include "mini_csr.h"
static void mini_putc(char c) {
    while (uart_txfull_read()) { }
    uart_rxtx_write((unsigned char)c);
}
static int mini_getc(void) {
    if (uart_rxempty_read()) return -1;
    int c=uart_rxtx_read(); uart_ev_pending_write(2); return c;
}
static struct sbi_console_device console={.name="LiteX UART",.console_putc=mini_putc,.console_getc=mini_getc};
static u64 mini_time(void) {cpu_timer_latch_write(1);return cpu_timer_time_read();}
static void mini_timer_start(u64 deadline) {cpu_timer_time_cmp_write(deadline);cpu_timer_latch_write(1);}
static void mini_timer_stop(void) {mini_timer_start(~(u64)0);}
static struct sbi_timer_device timer={.name="VexRiscv MTIP",.timer_freq=60000000,
    .timer_value=mini_time,.timer_event_start=mini_timer_start,.timer_event_stop=mini_timer_stop};
static int reset_check(u32 type,u32 reason) {(void)reason;return type==SBI_SRST_RESET_TYPE_WARM_REBOOT?1:0;}
static void reset_do(u32 type,u32 reason) {
    (void)type;(void)reason;
    /* Drain queued console bytes, then allow the last character to leave TX. */
    while(!uart_txempty_read()) { }
    u64 end=mini_time()+12000; /* 200 us at the fixed 60 MHz timebase */
    while(mini_time()<end) { }
    ctrl_reset_write(1);for(;;)asm volatile("wfi");
}
static struct sbi_system_reset_device reset={.name="LiteX reset",.system_reset_check=reset_check,.system_reset=reset_do};
static int early(bool cold) {
    csr_write(0xbc0,0);csr_write(0x9c0,0);
    if(cold) {sbi_console_set_device(&console);sbi_system_reset_add_device(&reset);}
    return 0;
}
static int timer_init(void) {mini_timer_stop();sbi_timer_set_device(&timer);return 0;}
static const struct sbi_platform_operations ops={.early_init=early,.timer_init=timer_init};
const struct sbi_platform platform={.opensbi_version=OPENSBI_VERSION,
    .platform_version=SBI_PLATFORM_VERSION(1,0),.name="riscv-mini TangPrimer20K",.features=SBI_PLATFORM_DEFAULT_FEATURES,
    .hart_count=1,.hart_stack_size=SBI_PLATFORM_DEFAULT_HART_STACK_SIZE,
    .heap_size=SBI_PLATFORM_DEFAULT_HEAP_SIZE(1),.platform_ops_addr=(unsigned long)&ops};
