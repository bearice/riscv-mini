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
