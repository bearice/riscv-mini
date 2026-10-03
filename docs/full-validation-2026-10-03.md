# 默认 full 配置最终验收（2026-10-03）

默认启用独立 MMU/FPU、全部外设、原生 SD lite、USB ultra、48k DDS；
sys/CPU/Wishbone 60 MHz，DDR 120 MHz，LCD 9 MHz。音频和双麦克风共用
DDS clock-enable，不消耗额外 PLL。生成 35 个实际 SoC Verilog 模块，
模块内部实现内联；Gowin netlist_hierarchy=0，place=3，route=2。

候选目录 `build/rtl-diagnosis/blocks-route2/`，完整独立构建，未复用旧 PnR。
Gowin V1.9.12.04 / GW2A-LV18PG256C8/I7，Setup/Hold 违例均为 0。

| 最终 PnR 资源 | 使用 / 可用 |
| --- | ---: |
| Logic | 18,740 / 20,736 |
| Register | 10,775 / 16,173 |
| CLS | 10,057 / 10,368 |
| BSRAM | 43 / 46 |
| rPLL | 3 / 4 |
| PRIMARY / LW | 各 8 / 8 |
| I/O / IOLOGIC | 139 / 207；62 / 207 |

FS SHA256 `ba3ab08ad51c2883d5af20b2e10a528b978f7663e88dac30c64b3a4490bedbce`。
CPU SHA256 `45333b754ab68726f6eb23ede586af40cb5f2d7990569a60fcc8b6f9cd45fc97`。
boot 6,584 bytes，应用镜像 70,172 bytes，ABI `adf18467`。
资源和哈希证据为 `validation.json` 及 `gateware/impl/pnr/` 报告。

## 实板结果

FPGA SRAM 下载、UART 应用加载后，36 种固件命令检查全部通过；
SD 新建并读回 `RVT00008.BIN`。含 FPU addition、Sv32 translated load、
DDR、IRQ、Flash 只读、native SD、两块显示、板级 IO、双麦快照、USB
枚举/重启、共享 PHY reset 和 Ethernet 状态/解析器等。固件测试入口
保留为 `test ...` 命令，主机工具调用这些命令。

真实 Ethernet 使用 USB 网卡 Ethernet 8（测试时 ifIndex=80），host IP
169.254.25.153，board IP 169.254.20.20:1234；MAC 8E:6F:09:E9:70:28
来自 Flash UID 41503446333236000000000200280050。PHY 为 0x001cc816，
100 Mbps full duplex。ARP MAC、4 次 ping、UDP 0/1/31/32/63/64/255/511/
1024/1472 bytes 都通过。

五分钟并发持续 301.969 秒（完成最后一轮），52 轮，520 个 UDP 包、
179,556 bytes payload、零超时/数据不匹配，最大观测回环 RTT 32 ms。
每轮调用 USB、SD 文件 CRC、LCD 换帧和 Ethernet 命令，期间音频 DMA
静音运行。最终 SD/USB/audio errors、audio underruns、LCD underflows
均为 0。该负载是间歇网络突发加其他测试，不是最大网络吞吐率测量。

Windows Line In（Realtek，device 2）采集 48 kHz / stereo / 16-bit：
左声道峰值 750.0 Hz，右声道 500.0 Hz；声道分离 32.82/32.90 dB，
自动静音测试音抑制 61.15/54.03 dB，没有削波样本。没有将 Line In 的
底噪、DC offset 等同于 DAC 本身性能。用户确认两块 LCD 均正常稳定。

证据：该目录的 `firmware-verification.json`、`external-verification.json`、
对应 UART 日志和 `audio-line-in.wav`。完整转换故障和失败候选记录见
[RTL 修正](rtl-defaults.md)，RV32GCB 综合实验见 [资源实验](rv32gcb-resources.md)。

## 验收边界与发布

MMU 测试覆盖 MPRV 下 Sv32 load；硬件具有 M/S/U，不等于用户进程、内核、
隔离测试或 OS ABI 已完成。FPU 本轮覆盖 addition，不等于完整 IEEE-754
或 RTOS 上下文已验收。本轮未重新要求键鼠物理输入、SD/网线拔插、整板断电。

该候选用于最终 Flash 更新：FPGA 配置写偏移 0，匹配应用写偏移 0x200000。
沿用 erase/program，不做写后读回校验；正常启动仍执行镜像 CRC/ABI 检查。
发布日志留在候选目录，不用生成测试核替代已验收 CPU。

## Flash 更新后的追加检查

提交 `b0e64d381d4187ede9a53bfe908e55766150a11c` 后，已完成 FPGA
配置 erase/program 和匹配应用安装。配置写偏移 0，应用写偏移
0x200000；正常 Flash 启动输出 `BOOT FLASH entry=40800000`、
`SYSTEM READY`，镜像 ABI `adf18467`、CRC32 `4a45a1ec`。
未做 Flash 写后读回验证或整板断电测试。

第一次 Flash 启动后的追加 `test usb` 失败：PHY ready=1、ID=00060424、
port/lines=1，SOF/IRQ 持续计数，但 connected=0、HID=0；驱动 errors=0
不能代表枚举成功。`test usb restart` 立即恢复为 046d:c52b、三个 HID
接口，没有拔插接收器。该单次异常的根因尚未确认，不能宣称已修复。

随后保持同一镜像与接收器连接，三次 Flash 软件复位启动全部通过；
六次从 Flash 重新加载 FPGA 后检查全部通过，其中三次先停在 bootloader
十秒；另外三次重新加载后让正常主循环运行五秒再检查，也全部通过。
没有为无法复现的异常加入重试或延长超时。这些结果缩小了复现范围，
不证明偶发启动问题已消失，最终验收仍保留这个限制。

证据位于 `build/rtl-diagnosis/`：`flash-runtime-console.log`（首次失败）、
`flash-usb-restart.log`、`usb-boot-probe.log`、`usb-reload-probe.json`、
`usb-idle-probe.json`。Flash 编程和正常启动证据位于候选目录：
`configuration-programmer.log`、`configuration-reload.log`、
`boot-upload-uart.log`、`flash-release.json`。上述追加检查未改变硬件或固件。

## MiniSoC 模块重排后的新 ABI 与 Flash（2026-10-03）

用户允许 IRQ ABI 改变后，`MiniSoC.__init__` 改按配置/CPU、平台/时钟、
DDR、系统定时器/板级 IO、Flash/SD、显示、音频/麦克风、Ethernet/USB、
feature 常量分组。定时器先注册，不再为了旧 ABI 维持 SD 在前的顺序。
CSR 地址及内存映射保持不变；默认 full IRQ 为 UART=0、timer0=1、
timer1=2、board_io=3、sdcard=4、ethmac=5、usb_host=6。

**当前板上 Flash 使用此版本**，新 ABI 为 `997bd228`，应用 CRC32 为
`49d015c2`；此前 `adf18467` 应用不能与新配置混用。bootloader 和应用
均重新编译，配置及应用分别写入偏移 0 / 0x200000。

新候选位于 `build/soc-layout/release/`。独立完整构建通过 Setup/Hold
0/0；PnR Logic=18785、Register=10776、CLS=10081、BSRAM=43、PLL=3。
CPU/sys 60 MHz、DDR 120 MHz、SD lite、MMU/FPU、USB ultra、DDS 48k
保持原配置。FPGA FS SHA256 为
`0eeee9249394c4149543f205011ec898f42d43be292dfbd07cad4ff4d1324536`，
应用镜像 SHA256 为
`361894e4c99298c6a8c2b8f15fce51027b29e9fd201854c48a7d5ab990e270fd`。

36 种固件命令检查通过，SD 新建并读回 `RVT00009.BIN`；音频 Line In
实测左 750 Hz、右 500 Hz，通过声道分离及静音检查、无削波。五分钟
网络/SD/USB/音频/显示并发实际 300.953 秒、55 轮、550 个 UDP 包，
189915 bytes payload、零超时/数据不匹配，最大 RTT 32 ms。

写入 Flash 后三次启动通过：每次让主循环先运行五秒，再执行 IRQ、USB、
Ethernet、SD、FPU、MMU 检查。证据为该候选的 `validation.json`、
`firmware-verification.json`、`external-verification.json`、
`configuration-programmer.log`、`configuration-reload.log`、
`boot-upload-uart.log`、`flash-check.json` 及对应 UART 日志。
本轮未做写后读回验证、整板断电或新的屏幕/键鼠物理观察。
此前单次 USB 枚举异常未复现，根因仍未知，不宣称此重排修复了它。
