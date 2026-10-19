// AE350 subsystem configuration for this project (P2 DDR stage).
//
// The Gowin RiscV_AE350_SOC IP is delivered as an encrypted netlist whose
// feature set is selected by these macros at synthesis time, exactly like the
// generated project in the vendor example.  The values here give the fabric
// the whole low data window, bitstream-initialized vendor ITCM at the reset
// vector, and a read-only platform APB status bank. Hard ILM/DLM supply local
// execution/data/stacks; no fabric work SRAM or optional fabric DTCM exists.

`define AE350_EXT_D_MEM_EN
`define AE350_EMB_ITCM_EN
`define AE350_EXT_APB_SLAVE_EN
`define AE350_APB_UART2_EN
`define AE350_APB_GPIO_EN
`define AE350_APB_RTC_EN
