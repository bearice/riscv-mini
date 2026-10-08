# RAM 零基址、动态 VRAM 与无 C Flash XIP 启动

生成：2026-10-08T02:51:37+00:00；模式：staged review。

## 改动

- 将 RAM 迁移到零基址，CSR/Ethernet/USB/XIP 放入高地址设备区域；CPU 地址属性同步更新，不增加布局 flag。
- 添加 Flash 只读 XIP 与 enable/busy CSR，从物理 1 MiB 启动，保留 DDR BIOS 和 UART 恢复；VRAM 改为 CSR 指定、按帧锁存的 DDR 缓冲区。
- 迁移 OpenSBI/U-Boot 地址和运行时 CSR 描述，关闭 U-Boot C 指令，修复 PIE 搬移与 4 KiB SD DMA 请求边界和缓存同步；SD 硬件 buffer 未扩大。
- 启动页、L2、SD DMA、XIP、VRAM、CPU fence 回归通过；新 captured PnR 0/0 与匹配镜像完整板测、60 秒 soak、三轮 all/cache 通过。独立 OpenSBI/U-Boot 15 项验收及原始镜像哈希保留。cache 压力测试期间 LCD underflow 26，旧基线 27；普通 soak 为 0。

## 验证

对应源码的检查：18 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/zero-ram-xip-vs-v0.7.1.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
