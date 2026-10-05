# ROM 软件 DDR 初始化与 L2 启动 RAM

CPU 从 8 KiB ROM 启动。复位时共享 L2 把现有数据 RAM 的 4 KiB 固定映射到 `0x407ff000..0x407fffff`，启动栈顶为 `0x40800000`；data/BSS 同样位于此窗口。没有独立的集成 SRAM。DDR 未就绪时只有该窗口的 CPU 访问能完成，其他地址与 LCD/SD streaming 请求被阻止，L2 不进行 DDR refill、替换或回写。

每个启动缓存行初始为零、valid、dirty。ROM 初始化 DDR 后，解除固定，继续使用同一个栈地址与 tag/data；第一次替换或维护会把脏行写回对应 DDR 地址，因此不需要搬运正在使用的 C 栈。复位会重新清零启动窗口，丢弃未写回数据，不能用作持久化操作。软件初始化依赖至少 4 KiB L2，构建不再提供 `--l2-size 0`。

## 初始化与训练

[`firmware/bootloader/ddr.c`](../firmware/bootloader/ddr.c) 执行 reset/release、CKE、模式寄存器、ZQ 校准和读训练。当前硬件为 H5TQ1G63EFR，sys 60 MHz / DDR CK 120 MHz，DLL-off，CL6/CWL6。reset 与 release 各保持至少 1 ms；单条命令及调节后使用有界的软件等待。

对两个 byte lane 分别遍历四种 bitslip、256 个 delay setting，每个 setting 检查三个训练种子 42/84/36。读数据必须同时满足两相 DFI valid、该 lane 的数据比较和 burst detect。选取最大连续通过窗口的中点，要求窗口至少四个 setting，并复验。失败最多重新初始化三次；串口输出 lane 的 tap/bitslip/window、最新捕获状态，最终失败后保持控制器隔离，不访问未训练 DDR。

训练 scratch 为 bank0/row0/column0，与启动栈窗口分离。PHY DLL、时钟停止与复位逻辑仍由硬件实现；[`gateware/ddr_boot.py`](../gateware/ddr_boot.py) 只提供单周期命令、调节脉冲、固定测试 pattern 和读结果捕获，不含训练扫描 FSM。LiteDRAM controller/crossbar 在 handover 前保持复位。ROM 在开始串口输出前等待 2 ms，让 PHY 启动的短暂 sys 时钟停止/复位结束。

## CSR 与诊断

`control` bits0/1/2/3 分别为 reset_n、CKE、handover/ready、failed。
`command` 包含 address[15:0]、bank[18:16]、RAS/CAS/WE[22:20]、write/read enable[24:23]；写入触发一个 DFI 命令。
`tuning` 包含 lane mask[1:0]、delay reset/increment/direction[4:2]、bitslip reset/increment[6:5]；调节动作仅在写入时产生脉冲，lane mask 保持到软件释放。
`status` bit0 ready、bit1 failed、bit2 captured、bits5:4 两个 lane 的比较结果、bits7:6 burst detect。每次 READ 清除捕获及比较结果，缺少新数据不会沿用上一次通过结果。
`lane0/lane1` 保存 tap[7:0]、bitslip[9:8]、window[24:16]，由软件写入，可通过 BIOS `test ddr` 读取。

## ROM 和验证边界

ROM 链接启动汇编、UART、timer、DDR 初始化、紧凑 Flash 驱动和镜像装载器，使用 `-Os -flto`。不含 SD、LCD、文件系统、音频、网络或 USB。8 KiB 上限由 [`boot.ld`](../firmware/bootloader/boot.ld) 和 [`scripts/build.py`](../scripts/build.py) 检查；函数空间可用 `scripts/boot_size.py` 检查，LTO 内联空间计入调用者。当前 full ROM 为 5292 B；boot 编译同时生成 `.su` 栈使用报告。当前 main 固定栈帧 272 B，train 48 B、probe 64 B；沿已链接静态调用路径训练峰值约 384 B，没有递归或动态栈分配。4 KiB 窗口中至少保留 2 KiB 栈。

`tests/boot_ram_test.py` 检查训练前隔离、4/8 KiB 缓存字节写、脏栈交接/替换和复位。`tests/shared_l2_verilog_test.py` 另执行实际 Verilog 的启动 RAM 路径。`tests/ddr_boot_test.py` 检查命令、调节脉冲、新数据捕获和所有权交接，不模拟电气 PHY，也不代替真实 ROM 训练。`scripts/boot_repeat_verify.py --output-dir BUILD` 检查重复 SRAM 下载和软件复位后的实际训练与 BIOS 启动，不写 Flash。

训练和 `test ddr` 并非完整 128 MiB 内存测试，后者覆盖 8 KiB scratch。重复启动通过不代表已验证所有电源状态、温度或板型；Flash 和整板断电启动需要匹配配置单独验证。镜像格式与更新操作见 [bootloader](bootloader.md)。
