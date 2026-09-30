#include "io.h"
#include <generated/csr.h>
void io_timer_init(void) {timer0_en_write(0);timer0_load_write(~0u);timer0_reload_write(~0u);timer0_en_write(1);}
uint32_t io_ticks(void) {timer0_update_value_write(1);return ~timer0_value_read();}
void io_delay_ms(unsigned ms) {uint32_t start=io_ticks();while((uint32_t)(io_ticks()-start)<ms*(CONFIG_CLOCK_FREQUENCY/1000u)) {}}
