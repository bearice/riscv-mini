# Peripheral CSR packing

The peripheral CSR interface is distinct from CPU instruction CSRs such as
mstatus and satp. Narrow, read-only peripheral values now share bus words.
Controls, commands, DMA addresses and full-width counters retain their existing
semantics. All field widths remain unchanged, including 16-bit microphone counts.

`gateware/csr_layout.py` is the shared layout definition. Gateware packs live
state Signals into CSRStatus words; `scripts/build.py` emits the original C
read accessor names as field extraction functions. Existing HAL and application
sources therefore retain their interfaces. Optional features emit aliases only
when the corresponding packed word exists.

| Bank / word | Fields (bit positions) | Previous words | New words |
|---|---|---:|---:|
| board_io / inputs | buttons 3:0; switches 7:4; pressed 11:8; released 15:12 | 4 | 1 |
| mic / counts | level 15:0; captured 31:16 | 2 | 1 |
| mic / state | busy 0; done 1; activity 2; activity_right 3 | 4 | 1 |
| audio / state | level 15:0; errors 18:16; busy 19; amplifier 20 | 4 | 1 |
| rgb_lcd / state | active 0; busy 1 | 2 | 1 |
| Total | | 16 | 5 |

A single packed word read captures its fields together. Separate legacy read
accessor calls still perform separate bus reads and do not promise a common
snapshot. Sticky button events and their independent write-to-clear command,
FIFO pop/capture commands, and all clock-domain crossings are preserved.

## ABI

打包布局、CSR/IRQ 与 USB/SD backend 属于镜像 ABI。应用必须使用同一构建生成的头文件与 app.img，不能把旧应用用于新 FPGA 配置。CSR bus array 的 LUT/FF 与 CPU 指令 CSR 文件是不同资源来源，计数应查看层级综合报告，而不是把名称中含 CSR 的所有条目相加。
