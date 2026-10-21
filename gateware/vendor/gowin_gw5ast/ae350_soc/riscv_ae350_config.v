// AE350 subsystem configuration.
//
// The Gowin RiscV_AE350_SOC IP is delivered as an encrypted netlist whose
// feature set is selected by these macros at synthesis time. P2 keeps the
// external DDR window, vendor ITCM and the extended APB. P3a additionally
// exports the 32-bit extended AHB slave, the 16 fabric interrupts and the 8
// DMAC request/ack pairs (MUG1031 8.2.12-8.2.14). Embedded DDR3 stays off so
// it cannot collide with the standalone DDR3 controller.

`define AE350_EXT_D_MEM_EN
`define AE350_EMB_ITCM_EN
`define AE350_EXT_APB_SLAVE_EN
`define AE350_EXT_AHB_SLAVE_EN
`define AE350_EXT_INT_EN
`define AE350_EXT_DMA_EN
`define AE350_APB_UART2_EN
`define AE350_APB_GPIO_EN
`define AE350_APB_RTC_EN
