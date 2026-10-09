# 关闭 FPU 的 Ethernet native DMA 与 buffered memory ports 实验

2026-10-09：关闭 FPU 后，Ethernet native DMA 和 buffered memory ports 可以与音频输出、双麦克风、SD、RGB LCD、USB 同时容纳。XIP 和 ROM8K 配置均完成 Gowin PnR，setup/hold 违反端点均为 0/0；ROM8K 配置另通过匹配镜像实板验收和外部 Ethernet 收发。XIP 候选仅完成 PnR，未进行该候选的实板/Flash 启动验收。

## 分支与配置

实验分支 `codex/eth-dma-no-fpu` 从已保存的 native Ethernet 实验派生，先 rebase 到 `main` 的 `e24a460`，再进行实验。rebase 后提交为 `5865db46eabe5e344648454a1228f6a87cddf5d4`。原参考分支 `codex/buffered-memory-ports` (`e7586b0`) 和 `codex/eth-dma-no-audio` (`974da25`) 保持原样。

跟随当前主线的无 C CPU 配置：RV32IMA、Sv32 MMU、I/D cache、4 KiB shared writeback L2；FPU 关闭，CPU/L2 60 MHz、DDR 120 MHz。音频 DDS 48 kHz、双麦克风、SD lite/native4、LCD、Ethernet 和 USB ultra 均开启，Gowin place2/route2。重新生成的 MMU 无 FPU CPU RTL SHA256 为 `80b9ad665be1c30dfd0180671d5c4138b423e3df47dc76e67c450d99664bbcd9`。

SD 和 Ethernet 的 DDR 侧使用 32-bit native MemoryPort，LCD 使用 16-bit MemoryPort。每个流式端口在内存控制器中拥有 16-byte burst 缓冲，控制器负责与 128-bit DDR burst 的转换。Ethernet 的包 SRAM 侧仍使用 Wishbone；CPU 设置包地址、长度和方向并等待 DMA 完成。当前实现保留 MAC packet SRAM，不是 descriptor ring，也不是 MAC 直接流入 DDR。驱动对至少 64 字节、满足地址条件的帧使用 DMA，其他帧保留 PIO 路径。

## 容量和时序

| 配置 | Logic / 20736 | Register / 16173 | CLS / 10368 | BSRAM / 46 | setup / hold |
| --- | ---: | ---: | ---: | ---: | ---: |
| XIP，ROM 0 | 17614 | 10253 | 9807 | 38 | 0 / 0 |
| ROM 8 KiB | 17534 | 10168 | 9688 | 42 | 0 / 0 |

XIP 还剩 3122 Logic、561 CLS、8 个 BSRAM；ROM8K 还剩 3202 Logic、680 CLS、4 个 BSRAM。两者使用 3 个 rPLL，PRIMARY/LW 时钟资源均为 8/8。不能仅按剩余 Logic 推断其他新功能一定能够布线。

4 KiB ROM 尝试在链接阶段失败：无压缩指令的 ROM bootloader 为 5320 字节，超出 1224 字节。8 KiB ROM 用于 SRAM gateware / UART BIOS 临时板测，避免为了实验修改 Flash。Gowin 命令行曾在综合启动前无输出退出；单独工程诊断和正常构建重试最终均完成 PnR，最终使用正常流程生成、校验和登记的镜像。

完整配置、CPU 输入、工具参数、源码指纹和复现 recipe 见 [XIP 构建身份](../build/no-fpu-xip/build-info.json) 与 [ROM8K 构建身份](../build/no-fpu-rom8k/build-info.json)。

## 实板结果

ROM8K bitstream SHA256：`793c9cb9fef133a5a905bb41cdda99fbcf138dffe91f52f39e70fd076e5b1919`。

BIOS app.img SHA256：`633c96f990524a2ecf15d8df9dc063cb5ce768f53f1ada9eb08b73dc594099b9`。

- 主机/RTL 共 11 项检查通过：配置、shared L2、buffered ports、native DMA、SD emitted RTL、LCD emitted RTL、内存控制器 emitted RTL、Ethernet DMA emitted RTL、软件 DDR 初始化、DDR 队列与结构检查。
- 匹配上述 FS/app 的实板验收共 50 项检查通过，包括 FPU disabled 的预期拒绝、MMU、L2、DDR、SD 块读取、文件系统、LCD、音频、双麦克风、Ethernet PHY/parser、USB 枚举和三次重启。
- SD native4 DMA 初始化、块读取及 DMA 检查通过，旧参考分支的 SD 初始化失败在本候选上未复现；这不是对旧故障根因的证明。
- 60 秒并发测试完成 24 轮，音频 underrun/error 为零，LCD underflow 为零。USB stop 后的 `TEST usb FAIL` 是验收脚本要求的负向检查，随后重启通过。
- 外部 Ethernet：ARP 和 Windows ICMP 通过；32.53 秒内 90 个 UDP echo，长度为 0、1、31、32、63、64、255、511、1024、1472 字节，payload 共 31077 字节，零超时，最大 RTT 8.29 ms。RX/TX DMA 计数最终为 79/76，实际完成 DMA 拷贝。并发运行音频、LCD、SD、USB 检查，音频和 LCD 错误计数为零。

原始板测、下载和恢复证据已复制到 [本次实验证据目录](../build/no-fpu-evidence/)，外部网络证据见 [external-verification.json](../build/no-fpu-rom8k/external-verification.json)。没有进行屏幕画面、USB 物理输入或听音验收，也没有写 Flash 或做掉电启动测试。

## 性能与结束状态

all/cache 各三轮通过，原始 UART、镜像身份与数据 hash 保存在 [性能报告](../build/no-fpu-performance/comparison.md) 及同名 JSON。以正式 v0.8.0 的已有三轮测量为参考，两者均为 CPU60/DDR120 MHz。CPU/L2/SD 指标总体接近：L2 read 为 54.953 vs 54.816 cycles/access，SD 4 KiB read 为 1.778 vs 1.772 MiB/s，CPU mul-add 为 40.016 vs 39.982 cycles/iteration。

候选和参考同时存在 FPU、端口结构、Ethernet DMA、启动模式、BIOS 及 placement 的差异，不能将变化单独归因于 FPU。cache 压力测试会造成 LCD 欠载：候选和正式基线都累计 24 次；普通并发及外部网络测试均无欠载。结束后的 BIOS 检查通过。

板上已用 SRAM gateware / UART BIOS 恢复正式 `current` v0.8.0，恢复操作及启动自检通过。`current`/`baseline` 引用不变，本次实验未合并、未推送、未升级 Flash，也未创建新发布版本。当前分支保留 rebase 提交和本报告，完整构建产物保存在 worktree 的 `build/` 下。
