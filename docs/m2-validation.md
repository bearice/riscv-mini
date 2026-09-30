# M2 SPI LCD / SD 上板验证

日期：2026-09-30。Tang Primer 20K + Dock 3713，H5TQ1G63EFR-PBC / 128 MiB DDR，examples/SPI_lcd 对应的 240×135 LCD。
M2 已完成综合、布局布线、SRAM 下载和上板功能验收。

## 实现

CPU/sys 48 MHz，DDR CK 96 MHz；延续 M1 的 DLL-off PHY 与 DDR 自检。
LCD、SD 各使用独立 LiteX SPI 主控制器，可配置分频、软件保持 CS，mode 0，轮询传输。
LCD 为 16-bit 传输、MOSI 输出，DC/RESET/低有效背光使用 GPIO；SD 为 8-bit 全双工。
当前没有 SPI FIFO/DMA、其他 CPOL/CPHA 模式或中断驱动。

LCD 初始化命令和显示窗口来自本地 examples/SPI_lcd，保留实际毫秒级 reset/sleep-out 延时。
CPU 将 RGB565 测试帧绘制到 DDR 0x40300000，占 64,800 字节；窗口偏移 x=40、y=53，像素高字节先行。
工作 SPI 6 MHz；纯像素线速下限 86.4 ms，当前逐像素轮询还包含 CPU 开销，没有承诺连续刷新率。

SD 初始化 400 kHz，工作 6 MHz，512-byte CMD17/CMD24 单块读写。
包含 SD v1/v2 初始化路径、SDSC/SDHC 地址转换、CSD 容量识别、CMD59 CRC 开启、数据 CRC16、响应/忙等待超时及错误返回。
通过成功初始化时的 detect 电平识别插卡状态；本卡插入时为 0。
FatFs R0.16 配置 FAT12/16/32、exFAT 和静态长文件名，无格式化命令、无 RTC，时间戳固定为 2025-01-01。
来源和配置见 firmware/vendor/fatfs/README.md；实际验收仅覆盖当前 FAT32 SDHC 卡。

## 实测结果

| 验收项 | 结果 |
| --- | --- |
| DDR 回归 | 地址/数据位、跨 128 MiB 地址范围的抽样模式，以及 DDR C 代码/栈全部通过 |
| SPI 事务仿真 | 8/16-bit 位序、mode-0 时钟、手动 CS 保持、非法长度处理通过 |
| SPI 硬件内部回环 | LCD/SD SPI CSR 控制及回环通过 |
| LCD 物理显示 | 用户确认白框、红绿蓝色带、文字方向和画面范围全部正常 |
| SD 容量 / 挂载 | 0x0747b000 个 512-byte 扇区，SDHC，FatFs type 3（FAT32），根目录列举成功 |
| SD 文件写回 | 新建 RVTEST00.BIN，4096 字节；关闭后重新打开，每字节及 CRC32=08040e1e 校验通过，线上 CRC16 检查通过 |
| 拔卡 | 用户拔卡后执行 sdinfo，CMD0 R1=ff，报告 SD unavailable / FatFs=3，返回提示符，串口回显正常 |
| 插卡恢复 | 用户插回后执行 sdinfo，无需复位即可重新挂载，目录中仍存在测试文件 |
| 最终版本重新下载 / 软件复位 | 启动及一次 CPU 软件复位均通过 DDR、SPI、SD 挂载和 LCD 传输 |
| 持久文件复验 | 最终版本的软件复位后执行 sdcheck RVTEST00.BIN，只读检查 4096 字节，CRC32 仍为 08040e1e |

写入使用 FA_CREATE_NEW，选择未占用的 RVTEST00–99.BIN；已有同名文件不会覆盖。
本次只新建 RVTEST00.BIN，没有格式化、裸扇区写入或修改既有文件内容；创建文件会更新文件系统分配和目录元数据。
重新下载与复位验证不再次创建文件。文件保留在卡上用于后续回归。

显示、写入和拔插测试在前一版 M2 bitstream 上完成；最终版新增只读 sdcheck 命令，LCD/SPI/SD 写入驱动未变。
最终版重新上板，并完成启动、软件复位和上述持久文件复验。

## 最终构建与证据

- 固件 30,432 字节 / 32 KiB ROM，剩余 2,336 字节；后续应用应考虑从 SD 加载到 DDR。
- Logic 6,037 / 20,736（约 29.1%），Register 3,213 / 16,173，BSRAM 28 / 46（约 60.9%）。
- Gowin setup / hold violated endpoints 均为 0。
- 最终 bitstream SHA256：`e639b94ce09ef59ae4189ccf2673ba2d209f8ca6d5470e9030c3ee9807ea2f39`。
- USB location 107569，JTAG 2 MHz，COM4 / 115200 / 8N1；仅 SRAM Program，未写配置 Flash。

生成证据保存在 build/m2/：validation.json、hardware-validation.json、programmer.log、uart.log、
write-validation.json、removal-validation.json、reinsert-validation.json、persistence-validation.json 和 m2-acceptance.json。
这些文件是本机测试产物，生成目录不作为源代码维护。

添加 M2 外设改变了 Migen 层级前缀。PHY 初始化控制寄存器使用稳定名称并保留，
constraints.py 解析生成 RTL 中 pause 的第一级同步器，约束仅豁免初始化停钟复位路径和该首级 CDC；
CPU/DDR 数据路径及第二级同步器继续参与时序检查。

## 当前边界

HDMI framebuffer/DMA/TMDS 属于 M3，尚未实现。实体复位按键和断电冷启动未验收。
拔插验收为文件关闭、系统空闲后的拔卡；没有测试写入中拔卡或断电，不能据此承诺文件系统掉电一致性。
未实测 SDSC、其他卡型、exFAT；SD SPI 当前适合资源加载，未作吞吐基准测试。
目前 LCD 更新和 SD 访问为轮询，会阻塞 monitor，所有协议等待有超时；没有后台自动热插拔任务，插回后用 sdinfo 重新挂载。

下一阶段规格已改为 480×272 / 约 59.94 Hz，先接入并行 RGB LCD 的 RGB565 DDR framebuffer，HDMI 暂缓（见 [视频规格](video-spec.md)），加入显示 DMA、跨时钟 FIFO、帧切换和 underflow 计数，
在显示运行时回归 DDR/SD/LCD 并测量内存带宽。
