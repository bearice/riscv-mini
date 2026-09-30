#include <hal/hal.h>
#include <generated/csr.h>
#include "io.h"
static struct {hal_irq_handler_t callback;void *context;} handlers[32];
_Static_assert(offsetof(hal_trap_frame_t,pc)==128 && sizeof(hal_trap_frame_t)==144,"trap.S frame layout");
volatile hal_stats_t hal_stats;
extern void hal_trap_entry(void);
uint32_t hal_irq_save(void) {uint32_t old;__asm__ volatile("csrrci %0,mstatus,8":"=r"(old)::"memory");return old;}
void hal_irq_restore(uint32_t state) {if(state&8u)__asm__ volatile("csrsi mstatus,8":::"memory");}
static unsigned mask_read(void) {unsigned mask;__asm__ volatile("csrr %0,0xbc0":"=r"(mask));return mask;}
static void mask_write(unsigned mask) {__asm__ volatile("csrw 0xbc0,%0"::"r"(mask):"memory");}
hal_result_t hal_irq_attach(unsigned source,hal_irq_handler_t callback,void *context) {
    if(source>=32)return HAL_INVALID;
    unsigned state=hal_irq_save();handlers[source].context=context;handlers[source].callback=callback;hal_irq_restore(state);return HAL_OK;
}
hal_result_t hal_irq_enable(unsigned source,unsigned enabled) {
    if(source>=32 || (enabled && !handlers[source].callback))return HAL_INVALID;
    unsigned state=hal_irq_save(),mask=mask_read();
    mask_write(enabled?mask|(1u<<source):mask&~(1u<<source));hal_irq_restore(state);return HAL_OK;
}
void hal_get_stats(hal_stats_t *stats) {
    if(!stats)return;
    unsigned state=hal_irq_save();*stats=hal_stats;hal_irq_restore(state);
}
__attribute__((weak)) void hal_exception_handler(hal_trap_frame_t *frame) {
    puts_uart("\r\nFAULT cause=");io_hex(frame->cause);puts_uart(" pc=");io_hex(frame->pc);
    puts_uart(" value=");io_hex(frame->value);puts_uart("\r\n");
    for(;;)__asm__ volatile("nop");
}
void hal_trap_dispatch(hal_trap_frame_t *frame) {
    if(frame->cause!=0x8000000bu) {hal_exception_handler(frame);return;}
    unsigned pending;__asm__ volatile("csrr %0,0xfc0":"=r"(pending));pending&=mask_read();
    for(unsigned source=0;pending;++source,pending>>=1) if(pending&1u) {
        if(handlers[source].callback)handlers[source].callback(handlers[source].context);
        else {mask_write(mask_read()&~(1u<<source));++hal_stats.unhandled_irqs;}
    }
}
void hal_irq_runtime_init(void) {
    __asm__ volatile("csrci mstatus,8\ncsrw 0xbc0,zero\ncsrw mtvec,%0\ncsrw mie,%1"::"r"(hal_trap_entry),"r"(0x800u):"memory");
}
void hal_irq_runtime_start(void) {__asm__ volatile("csrsi mstatus,8":::"memory");}
