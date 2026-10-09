# Exclusive native Ethernet rings, U-Boot integration and no-FPU defaults

生成：2026-10-09T08:55:13+00:00；模式：staged review。

## 改动

- RTL buffers native memory clients and gives autonomous Ethernet RX/TX rings exclusive MAC queue ownership; full defaults to MMU without FPU.
- HAL and BIOS use DDR descriptor ownership and borrowing, with PIO excluded from DMA builds and autonomous-ring diagnostics.
- OpenSBI describes runtime ring CSR addresses, actual CPU ISA and USB IRQ; U-Boot uses the exclusive native DDR ring backend and matching early UART address.

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`build/ring-uboot-performance/comparison.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
