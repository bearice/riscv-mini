# M10：基础系统 review、整理与资源报告

日期：2026-10-01。M9 前置提交 `98a23af00cd2186a9bd9ae17893df350819dd94e`。
本轮保留 VexRiscv lite、CPU/sys 60 MHz、DDR CK 120 MHz，以及单一基础系统。
按用户本轮补充要求，板上测试改为 DDR monitor 的 `test ...` 命令；
bootloader 不加入 SD、LCD、音频、Ethernet、USB 或测试代码。

## Review 范围与处理

| 模块 | 核查重点 | 结果 |
| --- | --- | --- |
| SoC / DDR / Wishbone | 地址布局、串行事务、应答所有权、显示优先和复位 | 保留当前实现；真实总线、DDR 调度与音频延迟应答仿真通过 |
| RGB LCD / audio | 帧结束标记、欠载恢复、停止后的旧应答、ring producer/fetched 所有权 | 保留双帧与有限 FIFO；缓冲由调用者保持到 stop 成功，无 D-cache |
| SD / vendor | CRC 错误传递、读描述符背压、块计数、超时和 abort | 修正补丁说明漏记 M9 FIFO/连续 block_length/valid & ready 计数的问题 |
| USB Host | ULPI 状态、OHCI DMA/IRQ、停止/重启、HID 队列与异步 LED 数据生命周期 | 连续停止复现 ready/ID 残留；USB-only stop 保持 ULPI PLL 运行，enable 复位 FSM，F10 仍复位 PLL |
| Ethernet / board IO | UID 派生 MAC、packet slot 的复制/释放、IRQ 延后工作、共用 F10 | 保留现有 HAL；两个 RX 槽的突发丢包限制仍存在 |
| ROM / app | 分区、镜像 ABI/CRC、栈预留、恢复边界、初始化职责 | boot 二进制与 M9 相同；测试模块只加入 DDR app，未扩大 ROM/SRAM |
| 构建 / 验收脚本 / 文档 | 实际 PnR 证据完整性、资源报告、旧阶段数字、独立验收范围 | 补全资源及 ELF section 大小，缺失报告字段时报错，统一当前文档入口 |

保留现有模块划分，不增加统一设备框架或包装层。修复了一个真实 USB 停止
缺陷：先前单次探针正常，连续重启第三次后出现 initialized=0、phy_ready=1、
ID=00060424，再次停止/等待仍不清零。根因路径为 ULPI PLL 先停钟，而 FSM
使用同步状态复位；修复保证 USB-only stop 有时钟完成清零。
回归命令 `test usb stop/restart` 对旧硬件实际失败，对修复硬件检查停止状态和重新枚举。

板上验收统一为 `test ...` 命令，清单与参数见 [固件测试](firmware-tests.md)。
网络 parser 的验收向量抽到共用 helper，monitor 与独立示例共用。
新整帧 CRC 检查曾因为主循环服务太稀疏而耗尽音频 ring；实板把 SD/LCD
分段定位到 LCD 校验后，增加有界服务频率与补给，减少 CRC 的 DDR 栈临时访问。
没有扩大硬件 FIFO 或改变设备频率。旧 stage 构建条件没有恢复。
临时诊断程序与日志留在忽略提交的 `build/`，固定版本依赖和 vendor 补丁保留。

新增 `sim/test_build_report.py` 覆盖实际资源行、setup/hold 违反端点、
缺失时钟/时序字段和非法资源用量。`sim/README.md` 给出全部本地检查的统一运行方式。
`validation.json` 新增 `firmware_sizes`，保留原有上传工具依赖的字段。
软件构建不会提供新的 PnR 证据；综合报告缺少必要字段时不再静默省略。

## 实际资源

来源：`build/m10-release/gateware/impl/pnr/project.rpt.txt` 和 timing HTML，
Gowin V1.9.12.04，`GW2A-LV18PG256C8/I7` / GW2A-18C，默认原生 SD。
以下为完成布局布线后的数据，不是模块估算。

| 资源 | 使用 / 总量 | 占用 | 余量 |
| --- | ---: | ---: | ---: |
| Logic | 15,445 / 20,736 | 74.5% | 5,291 |
| Register | 8,431 / 16,173 | 52.1% | 7,742 |
| CLS | 9,182 / 10,368 | 88.6% | 1,186 |
| BSRAM | 34 / 46 | 73.9% | 12 块 |
| I/O Port | 131 / 207 | 63.3% | 76 |
| IOLOGIC | 62 / 207 | 30.0% | 145 |
| rPLL | 4 / 4 | **100%** | **0** |
| PRIMARY | 8 / 8 | **100%** | **0** |
| LW | 8 / 8 | **100%** | **0** |
| GCLK_PIN | 6 / 8 | 75.0% | 2 |
| CLKDIV | 1 / 8 | 12.5% | 7 |
| DHCEN | 1 / 16 | 6.3% | 15 |
| DLL | 1 / 4 | 25.0% | 3 |
| DQS | 2 / 9 | 22.2% | 7 |

Gowin 报告的 Logic 是工具口径，不能直接称为“15,445 个 LUT”；细分为
12,510 LUT、2,179 ALU、0 ROM16，以及 126 个 SSRAM(RAM16) 实例。
Register 总量包含逻辑和 I/O 寄存器。BSRAM 推断类型为 SP 4、SDPB 24、
SDPX9B 2、pROM 4。余下 12 块 BSRAM 的标称容量为 216 Kbit / 27 KiB，
实际能用多少取决于端口、字宽与推断打包。

**setup 0、hold 0 违反端点**。核对现有约束：异步状态只豁免首级同步器，
跨域载荷的例外有保持/握手协议，第二级及正常 CPU/DDR/控制路径仍检查。
ULPI 使用外部 IO 时序预算与相移 PLL；这是当前约束下的 PnR 结果，
不等于板级电气时序、温度或最高频率已经充分覆盖。

与 M9 比较 Logic -83、Register +17、CLS -31，BSRAM/PLL 不变；
这是本次实际工具结果，不据此推导一般性的优化收益。
当前主要扩展限制是 **CLS 接近 89%、PLL 与 PRIMARY/LW 已满**。
不能根据剩余约 25% Logic 承诺更大的 CPU、更多高速外设或独立时钟域可直接加入；
后续扩展需同时评估打包、现有时钟复用和重新 PnR。

## 固件与内存

| 项目 | 当前基础构建 |
| --- | ---: |
| boot ROM / 工作 SRAM | 8,192 / 8,192 字节 |
| boot text / data / BSS | 6,564 / 0 / 4 字节 |
| boot.bin | **6,568 字节**，ROM 映像余量 1,624 字节 |
| 当前 app text / data / BSS | 65,984 / 52 / 154,488 字节 |
| app.bin / app.img | 66,036 / **66,084 字节**（48 字节镜像头） |
| 应用执行区 | `0x40800000` 起，linker 窗口 4 MiB，预留 64 KiB 栈 |
| RGB565 一帧 / 两帧像素 | 261,120 / 522,240 字节 |
| 帧缓冲地址与保留空间 | `0x47E00000`、`0x47E40000`，两个 256 KiB 槽 |
| 显示 FIFO | 8 KiB，片上 |
| 音频 FIFO / 测试 ring | 512 stereo frames；DDR 静态保留 128 KiB ring，仅显式命令启用 DMA |
| 独立并发示例 app.img / BSS | 55,416 / 150,376 字节，含 128 KiB PCM ring |

SD DMA 的 4 KiB bounce buffer 和 USB 描述符/缓冲均在应用 DDR/BSS 中，
已包含在上述 section 大小内；OHCI HCCA 要求 256 字节对齐。
Ethernet 仍为片上两个 RX / 两个 TX packet slots，未改为 DDR ring。
DDR 测试的 scratch、SD 非对齐测试缓冲、128 KiB PCM ring 和协议向量都在 app BSS，
不占 boot SRAM 或新增 BSRAM。相比 M9，app.img 增加 19,608 字节，BSS 增加
143,376 字节。应用窗口、栈预留和缓冲槽容量不等于实际运行时全部写满，
也没有测量栈高水位。

固定时钟保持：CPU/Wishbone/CSR/DDR 控制 60 MHz，DDR CK 120 MHz，
RGB LCD 9 MHz，OHCI 48 MHz，ULPI 初始化 60 MHz/225°，RMII 50 MHz；
Flash SPI 10 MHz、SPI LCD 6 MHz、SD 初始化 400 kHz/工作 7.5 MHz、
音频 BCK 1.5 MHz/46,875 stereo frame/s。来源、分频与复位关系见 [时钟树](clocks.md)。

## 构建与验收证据

- 14 个 `sim/test_*.py` 检查全部通过，包括完整 LCD 扫描、音频、SD、USB PHY、
  USB 边沿模型、Ethernet slots、Wishbone、DDR 调度、镜像与构建报告。
- 原生 SD 基础构建完成综合/PnR；SPI-SD 回退构建完成 RTL/固件生成，
  本轮不声称 SPI 回退完成新 PnR 或上板验收。
- HAL 示例通过 RISC-V g++ 的 C++ 语法检查；独立网络/媒体示例构建通过。
- `scripts/firmware_verify.py` 实板 **39 次命令调用通过**，包括停止 USB 后
  `test usb` 的预期 FAIL、非法参数拒绝、三次本地重启和共享 F10 复位。
  `test sd write` 新建 `RVT00001.BIN`，64 KiB 写入及逐字节读回通过。
- 额外 **30 次连续 USB stop/restart** 全通过，逐次确认 stopped ready=0
  和重新枚举三个 HID 接口；旧硬件已实际捕获 stale-ready 失败。
  原始失败和修复结果均保留，回归报告为 `usb-reset-regression.json`。
- 五分钟 `test soak 300` **61 轮、主动窗口 301.788 秒**（UART 命令总耗时
  302.078 秒，包含开始/结束处理；最后一轮完成后结束）。每轮 SD 文件
  4,096 字节 CRC `08040e1e`、整帧 CRC/换帧及 USB/OHCI 检查通过。
  最后 audio played/fetched 为 14,146,413 / 14,146,925；audio underrun/overrun/errors、
  LCD underflows、SD errors、USB errors/队列 drops、UART drops/unhandled IRQ 为 0。
  接收器仍为 `046D:C52B` / 3 HID，HCCA `0x40812300`（256 字节对齐）。
  输出结束后恢复黑色画布、IP UNCONFIGURED、停止音频且默认静音。
  报告为 `build/m10-release/firmware-soak-verification.json`，原始日志为
  `firmware-soak-uart.log`。此轮完成后只补了每轮起始读取 `!` 的中止入口，
  没有改变 DMA、校验或时钟逻辑；不把这轮日志标作最终镜像的五分钟结果。
- 最终镜像重新通过 **38 次命令调用**，包括一轮短并发检查；报告为
  `firmware-verification.json`，其 FPGA/app SHA256 与下列最终产物一致。
  另在 `test soak 30` 运行中发送 `!`，**5.453 秒**进入 ROM 选择菜单，
  没有等待 30 秒完成，证据为 `soak-abort.json` / `soak-abort-uart.log`。
- 最终 FPGA 配置使用 Gowin 普通 exFlash Erase/Program 写入并 reload，
  最终应用写入 `[2,4)` MiB 分区；未执行配置 Verify 或应用写后读回。
  Flash 启动、UART 装载以及两次自动 Flash 软件复位启动均通过，
  每次检查 USB 三个 HID、PHY 初始化、LCD 零欠载及音频停止/静音状态。
  证据为 `configuration-programmer.log`、`boot-upload-uart.log` 和
  `base-startup.json` / `base-startup-uart.log`；启动时的镜像 CRC 检查仍保留。
- 本轮网络物理收发不重做：主机原有直连 USB 网卡不在设备列表中。
  M9 的 ARP/ping/UDP 及五分钟稳定窗口是单独的历史验收证据，见 [M9](m9-validation.md)，
  不冒充本轮结果；立即复位后的突发丢 UDP 限制继续保留。

FPGA bitstream SHA256：
`0ed3c1e349dc8fc52baf545e98ab1d543e265272f949f5bc739fd145b4b84b15`。

当前 app.img SHA256：
`32a893a62b315884ac4b68fa2d2e9c3a9a5a1ecb44a665d494ed4bffaec5e167`。

真实键盘按下/释放、修饰键和键盘 LED、鼠标、LS、Hub 等未验收；
用户在设备旁之前不新增拔插或实物输入要求。RTOS、HDMI、完整网络协议栈、
Ethernet DDR ring 与 15 MHz SD 写时序属于后续工作，不计入本轮交付。
