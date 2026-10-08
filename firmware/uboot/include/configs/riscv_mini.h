/* SPDX-License-Identifier: GPL-2.0+ */
/*
 * Board configuration for the TangPrimer-20K riscv-mini (VexRiscv RV32IMA(F),
 * LiteX SoC, OpenSBI FW_PAYLOAD in S-mode).
 *
 * DRAM, console and timer are described by the device tree handed over by
 * OpenSBI (a1); nothing board-specific is hardcoded here beyond the
 * environment layout.
 */

#ifndef _CONFIG_RISCV_MINI_H
#define _CONFIG_RISCV_MINI_H

/* S-mode timer runs from the 60 MHz SoC clock (SBI emulates rdtime). */
#define RISCV_SMODE_TIMER_FREQ		60000000

#define CFG_EXTRA_ENV_SETTINGS \
	"stdin=serial\0" \
	"stdout=serial,vidconsole\0" \
	"stderr=serial,vidconsole\0"

#endif /* _CONFIG_RISCV_MINI_H */
