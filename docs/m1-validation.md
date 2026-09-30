# M1 DDR 上板验证

日期：2026-09-30。开发板 Tang Primer 20K + Dock 3713；用户实物照片确认完整颗粒型号 H5TQ1G63EFR-PBC。
M1 已完成实际综合、PnR、SRAM 下载、串口自检和 DDR 程序执行。

## 配置

- RV32IM / ilp32，CPU/sys 48 MHz，DDR CK 96 MHz（192 MT/s），x16。
- DDR 8 banks × 8192 rows × 1024 columns，128 MiB，映射 0x40000000–0x47FFFFFF。
- DLL-off，CL6/CWL6，MR1=0x003（DLL disable、输出阻抗配置），MR2=0x008；静态与动态 ODT 禁用。
- 保守 RP/RCD/WR 15 ns，RAS 37.5 ns，FAW 50 ns，RFC 160 ns；REFI 3.90625 us。
- ROM 32 KiB、SRAM 16 KiB、上游 CPU 2 KiB I-cache，无 D-cache/L2 cache。
- 当前固件 3376 字节；测试使用裸机固件和生成的初始化序列，未使用完整 LiteX BIOS。

用户随后提供实物照片，丝印为 H5TQ1G63EFR / PBC：PB 是 DDR3-1600（11-11-11）速度档，C 为正常功耗的商业温度档。当前保守低频配置继续保留；RP/RCD 15 ns 与 RAS 37.5 ns 比 PB 档的 13.75 ns / 35 ns 更宽松。速度档额定 1600 MT/s 对应 CK 800 MHz，不是当前 FPGA 的运行频率，也不是必须采用的频率。
几何依据为本地 docs/07_Chip_manual/sk_hynix.pdf 第 4/9 页；速度档参考第 26–31 页。
DLL-off 的 CL6/CWL6、CL-1 读突发与 ODT 要求参考 SK hynix 的
[DDR3 Device Operation，第 16/25 页](https://www.bulcomp-eng.com/datasheet/H5TQ2G63FFR-PBC%20%28BGA%29%20-%20Datasheet%202.pdf)。
该文档为厂商通用操作说明，实际颗粒的可用性由本次上板测试确认；不把正常 DLL-on 的 tCK 表用于 96 MHz。

## 上板结果

使用 USB Debugger A / location 107569 / JTAG 2 MHz，通过 Gowin CLI SRAM Program 下载。
串口 COM4 / 115200 / 8N1。最后验收包含一次下载启动和连续两次 CPU 软件复位：三次均通过。
重新下载的其他迭代也通过，但验收以最终 bitstream 和最后保存日志为准。

两字节通道的有效窗口为 87–88 taps，bitslip=2，采样中心为 43–44；最终训练逐通道扫描
4 bitslips × 256 taps，以三组数据模式、完整 16-byte DFII 突发和 burst-detect 标志筛选。
仅接受三组模式全部匹配的候选，采用最大连续窗口的中心并再次复验。

CPU 侧验收：

1. 32 个数据位 walking-one / walking-zero。
2. 同时写入各 word-address 位的独立签名，检测 128 MiB 范围的地址别名。
3. 每 MiB 的前 64 KiB，共 8 MiB 抽样；先跨所有区域写入，再读回，分别验证全 0、全 1、地址相关模式及其反码。
4. 0x47FFFFFC 最后一个 word 读写正确。
5. 从 ROM 拷贝 C 程序到 0x40100000，执行指令同步后跳转；栈设为 0x40200000，局部 volatile 数组在 DDR 栈上读写；返回预期 0x13579BDF。
6. 自检后 UART 字符回显正确。软件复位 `!` 后重新完成 DDR 初始化、训练和同样的验收。

抽样模式覆盖范围为 8 MiB；并非对 128 MiB 每个字节做全容量 March 测试。
实体复位键、断电冷启动、长时间运行、温度范围、DMA 并发及带宽尚未验收。
最初训练失败的版本仍能报告错误并进入 ROM UART 回显，验证了训练失败恢复路径；
任意 DDR 总线故障/程序异常的恢复尚未验证。

## PHY 修正和时序

原 GW2DDRPHY 的 DLL-off 路径在本板上无法训练：READ 窗口未覆盖提前到来的前导，
burst-detect 为 0；只提前窗口起点且同步提前终点会丢失 BL8 尾部。
本地 vendor PHY 提前起点并保留原终点，捕获延迟提前一个 sys 周期，剩余对齐由 bitslip 训练完成。
软件还修正了延迟调节期间 DQS HOLD 的使用，实际读写时必须解除 byte-lane 选择。
保留上游文件许可和来源，详见 gateware/vendor/README.md。

最终资源：Logic 5674/20736，Register 2863/16173，BSRAM 14/46。
最终 Gowin 时序报告 setup/hold violated endpoints 均为 0。
约束对初始化停钟后释放的 reset、DHCEN stop 控制以及 pause 的第一级同步器使用具体路径例外；
没有屏蔽整个 sys/sys2x 时钟域，CPU、控制器和同步器第二级继续参与分析。
这些例外与固定上游 RTL 名称绑定，升级 PHY 后必须复核。
时序通过不代替 DDR IO 电气和外部时序的温度/长期验证。

## 复现与证据

在 riscv-mini 目录运行 README 中的 M1 构建和 board_test 命令。
生成报告位于 build/m1/validation.json、hardware-validation.json、uart.log、programmer.log；
PnR 报告位于 build/m1/gateware/impl/pnr/。
验证报告包含 bitstream SHA256，并在下载前核对，避免测试到旧文件。
M1 验收时板上运行最终 M1，自检通过后等待串口字符，未写配置 Flash 或 SD 卡。
后续 M2 已替换板上 SRAM 配置并新建 SD 测试文件；当前状态见 m2-validation.md。
