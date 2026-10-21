#pragma once
#include <stdint.h>
typedef void (*irq_handler_t)(void *context);
int irq_attach(uint32_t source, irq_handler_t handler, void *context);
int irq_attach_local(uint32_t code, irq_handler_t handler, void *context);
int irq_mask(uint32_t source, unsigned enabled);
void irq_unmask_all(void);
uint32_t plic_claim(void);
void plic_complete(uint32_t source);
uint32_t plic_pending(unsigned word);
void plic_priority(uint32_t source, uint32_t priority);
void plic_threshold(uint32_t threshold);
void irq_global(unsigned enabled);
uint32_t irq_unhandled(void);
uint32_t irq_last_cause(void);
void irq_dispatch(uint32_t cause);
