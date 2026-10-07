// SPDX-License-Identifier: GPL-2.0+
/*
 * riscv-mini (TangPrimer 20K) runs as an S-mode payload of the pinned
 * OpenSBI platform (firmware/opensbi). OpenSBI enters with a0=hartid,
 * a1=DTB; start.S stores a1 into gd->arch.firmware_fdt_addr and the
 * same DTB is handed to the kernel by booti. No SPL: the payload is
 * entered directly at CONFIG_TEXT_BASE (0x41100000 = FW_PAYLOAD_OFFSET).
 */
#include <init.h>
#include <asm/global_data.h>
#include <fdtdec.h>
#include <fdt_support.h>

DECLARE_GLOBAL_DATA_PTR;

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

int board_late_init(void)
{
	return 0;
}
