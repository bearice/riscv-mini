# 硬件 DDR 启动

`gateware/ddr_boot.py` 完成 DDR JEDEC 初始化和逐 byte lane 读训练；片上没有集成 SRAM。4 KiB ROM 先以无栈汇编轮询 ready/failed，成功后才在 `0x407fe000..0x407fffff` 建立 data/BSS 和启动栈，SP=`0x40800000`。应用入口同为 `0x40800000`，应用栈顶为 `0x40c00000`。

硬件执行 reset/release、模式寄存器（DLL-off，CL6/CWL6）、ZQ 校准，再对两个 lane 遍历四种 bitslip 与 256 个 delay setting。检查三个训练种子 42/84/36、read-data-valid 和 burst detect，选择最宽通过窗口的中点并再次验证。scratch 使用 bank0/row0/column0；控制器和 crossbar 在训练期间保持复位，master 在 handover 前被阻止。失败后停止命令，ROM 不访问 DDR 栈就输出失败或超时。

完整 SoC 软件复位同时复位 DMA/外设并重新训练 DDR，PHY 初始化/DLL 所需时钟保持运行。训练状态和 lane 结果可从生成 CSR 及 monitor 状态读取。

## ROM 内容和边界

ROM 只链接启动汇编、UART、计时、紧凑 Flash 驱动与镜像装载器，使用 `-Os -flto`。不含 SD、LCD、文件系统、音频、网络、USB 或 Flash UID 逻辑。保留地址/长度/ABI/CRC 检查、有界超时、UART framing、header-last 安装；没有 Flash 写后读回验证。

4 KiB 链接上限由 [`boot.ld`](../firmware/bootloader/boot.ld) 和 [`scripts/build.py`](../scripts/build.py) 强制检查，实际大小随工具和 LTO 构建而变。函数空间可用 `scripts/boot_size.py ELF --json REPORT` 检查，LTO 内联函数空间会计入调用者。

`sim/test_ddr_boot.py` 检查 JEDEC 命令、模拟窗口选择、handover 和失败路径，不能替代电气训练。硬件训练不是完整 128 MiB 内存测试；`test ddr` 只覆盖 8 KiB。镜像格式、Flash 分区和更新操作见 [bootloader](bootloader.md)。
