// SPDX-License-Identifier: GPL-2.0+
/*
 * riscv-mini Tang Mega 138K Pro AE350. OpenSBI enters U-Boot in S-mode with
 * the device tree of the machine; memory and devices come from that tree.
 */

#include <fdtdec.h>
#include <init.h>

int board_init(void)
{
	return 0;
}

int dram_init(void)
{
	return fdtdec_setup_mem_size_base();
}

int dram_init_banksize(void)
{
	return fdtdec_setup_memory_banksize();
}
