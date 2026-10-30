#pragma once
#include <stdint.h>
uint64_t ethernet_cycles(void);
void ethernet_delay_us(unsigned us);
int ethernet_mdio_read(unsigned phy,unsigned reg,uint16_t *value);
int ethernet_mdio_write(unsigned phy,unsigned reg,uint16_t value);
int ethernet_phy_init(unsigned *address,uint32_t *identity);
int ethernet_phy_gigabit(unsigned address);
