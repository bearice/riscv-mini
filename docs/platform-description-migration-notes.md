# 平台描述迁移备注

以下是从旧平台 JSON 移出的说明及历史状态，保留原文供追溯。它们不是当前能力声明，也不参与构建；当前验收结果以对应构建回执为准。旧 JSON 中未被消费的运行参数和重复 IP 参数已删除，原始值可从 Git 历史查阅。

## primer20k_dock3713.ddr.status

Fixed60/120MHz DLL-off CL6/CWL6; hardware initialization/training, reserved DDR loader stack, optional shared L2. See docs/hardware-ddr-boot.md and docs/l2-cache.md.

## primer20k_dock3713.video.status

Linker-owned DDR buffers programmed through base0/base1 CSR; addresses latch at frame start. See docs/xip-experiment.md.

## primer20k_dock3713.video.rgb_lcd.reference

../examples/RGB_lcd/480x272_4.3inch_lcd

## primer20k_dock3713.board_io.status

Implemented; input/lighting physical acceptance pending. See docs/hal.md.

## primer20k_dock3713.hal.irq_note

Numbers follow LiteX peripheral registration order in gateware/machines/primer20k_vexriscv/soc.py; a build without the sdcard interrupt (SPI SD backend) shifts every later source down by one.

## primer20k_dock3713.sd.status

Native 15 MHz reads pass; stable native 7.5 MHz write/read. Hotplug acceptance skipped per user. See docs/native-sd.md.

## primer20k_dock3713.microphone.status

Stereo firmware checks and user-confirmed independent acoustic response/LCD stability passed on P11/R11/M15/J16 and T6/R8/T8/P9. T9 remains WS2812 only. See docs/microphone.md.

## primer20k_dock3713.audio.status

Implemented; 48 kHz DDS clock, DDR ring DMA and acceptance in docs/audio.md.

## mega138k_pro.physical_identity

{
  "part": null,
  "core_revision": null,
  "baseboard_revision": null,
  "ddr_marking": "2 x SK hynix H5TQ4G63EFR; second line RDC 152V",
  "ddr_identification": "user-photo-2026-10-17",
  "status": "partially-confirmed"
}

## mega138k_pro.resources.camera.status

reserved-do-not-allocate

## mega138k_pro.resources.camera.routing_note

Reserve actual Dock camera connections; core MIPI_EXIO aliases routed to J23 SDRAM are not camera connector pins

## mega138k_pro.resources.camera.evidence

Dock schematic sheets 5, 11, 12, 16

## mega138k_pro.resources.microphone.wiring_evidence

user-confirmed 2026-10-22: DA CK LR WS

## mega138k_pro.ddr_candidates

[
  {
    "part": "H5TQ4G63EFR",
    "chips": 2,
    "data_width": 32,
    "row_bits": 15,
    "bank_bits": 3,
    "column_bits": 10,
    "size_bytes": 1073741824
  },
  {
    "part": "Micron-custom-reference",
    "chips": 2,
    "data_width": 32,
    "row_bits": 14,
    "bank_bits": 3,
    "column_bits": 10,
    "size_bytes": 536870912
  }
]

## mega138k_pro.conflicts

[
  {
    "resource": "MIC-ARRAY",
    "owners": [
      "microphone",
      "PMOD0",
      "SSPI"
    ],
    "resolution": "At most one active owner"
  },
  {
    "resource": "part",
    "board_reference": "Confirm silicon marking/speed grade",
    "resolution": "Build target follows board.json; physical silicon marking is not confirmed"
  }
]

## mega138k_pro.evidence

[
  "docs/images/mega138k-pro-ddr-20261017.png",
  "TangPrimer-138K/docs/02_Schematic",
  "docs/ae350-migration-stages.md"
]

## mega138k_pro.user_io.status

published-digital-binding; physical observation is a separate gate

## mega138k_pro.user_io.ws2812.note

Dock schematic places WS2812B-2020 on the peripheral sheet and the published pin is H16. The FPGA pad is 3.3 V; the LED supply in the schematic is 5 V. Waveform acceptance is separate from looking at the LED.

## mega138k_pro.user_io.not_published

[
  "U4 is the core reset and is also labeled USR_KEY on the SOM sheet; it is not a button",
  "N22 is a SOM LED net and is not one of the six dock user LEDs",
  "core T7/T8 button nets are not the four dock user buttons"
]

## mega138k_pro.user_io.evidence

[
  "Dock 4071f peripheral sheet: USR_LED[0..5], USR_KEY[0..3], WS2812B-2020",
  "LiteX sipeed_tang_mega_138k_pro led_n and TangMega138KProDock btn_n/led_ws2812",
  "J3 connector map: button 0..3 = K16 F15 G15 G16, matching the dock CST KEY pins"
]

## mega138k_ae350.cpu.software_qualification

integer ABI; floating-point, MMU and interrupt software outside memory qualification

## mega138k_ae350.memory.physical_geometry.status

identified-from-photo-and-reference-not-memory-tested

## mega138k_ae350.clocks.rtc.status

reference-only

## mega138k_ae350.runtime_abi

rv32ima_zicsr_zifencei / ilp32; generated address ABI

## mega138k_ae350.loader_format

ae350-20k-frame-v1; 48-byte header, 128-byte packets, ABI/range/CRC checks

## mega138k_ae350.pending

[
  "floating-point-and-MMU-software",
  "timer-IRQ-software",
  "DMAC-and-shared-DDR-arbitration",
  "XIP-and-HDMI"
]

## mega138k_ae350.ddr3.cpu_window.note

DDR is zero based: CPU byte address == DRAM byte address; fabric SRAM owns 0x40000000-0x40001FFF
