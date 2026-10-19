// AE350 subsystem configuration for this project (P2 DDR stage).
//
// The Gowin RiscV_AE350_SOC IP is delivered as an encrypted netlist whose
// feature set is selected by these macros at synthesis time, exactly like the
// generated project in the vendor example.  The values here give the fabric
// the whole low data window (AE350_EXT_D_MEM_EN), keep instructions in the
// fabric ROM (AE350_EXT_I_MEM_EN) and enable only the peripherals the mini
// monitor uses.  No TCM is instantiated: the boot SRAM stays fabric memory on
// the DDR/SRAM AHB port so its ownership and handoff are visible and testable.

`define AE350_EXT_D_MEM_EN
`define AE350_EXT_I_MEM_EN
`define AE350_APB_UART2_EN
`define AE350_APB_GPIO_EN
`define AE350_APB_RTC_EN
