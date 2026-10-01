# HAL 与板级外围设备

基线为单一 CPU/sys 60 MHz、DDR CK 120 MHz、LCD 像素 9 MHz 配置。bootloader 仍只负责 DDR 初始化和 Flash/UART 装载；HAL、trap/IRQ 和设备驱动全部链接在 DDR 应用中。Flash 安装只擦除/编程，未恢复写后校验。

## 接口与运行方式

应用只需 `#include <hal/hal.h>`，公共接口支持 C 和 C++ `extern "C"`。CSR 头由 HAL/backend 使用，基础应用不再直接读写 CSR。已有 `firmware/drivers/` 作为 backend 复用，没有复制一份 SD/LCD 驱动。

| API 组 | 当前能力 |
| --- | --- |
| time | `hal_time_ms()`：64 位 sys uptime 转为 32 位毫秒；`hal_ticks()`：原 60 MHz 32 位 ticks；delay / 回绕安全 deadline 比较 |
| irq / trap | 注册和屏蔽 32 个 CPU IRQ 源；临界区保存/恢复 MIE；完整整数寄存器/CSR trap frame；默认同步异常报告后停止，可覆盖异常 handler |
| uart | IRQ RX + 128 字节环形缓冲；非阻塞 getc；带超时和已写长度的 write；puts/hex 为阻塞便利函数 |
| board IO | 六灯逻辑掩码、四按键稳定状态/按下释放事件、四 DIP、单颗 WS2812B、共用 PHY reset |
| spi | 已有 SD / LCD mode-0 主机的事务接口，SPI 回退下位宽分别 8 / 16；原生 SD 构建中 SD SPI 请求返回 HAL_UNSUPPORTED；不是任意模式/引脚的通用 SPI 控制器 |
| sd | FatFs mount/list、512 字节 block read/write；默认原生四位 SD + Wishbone DMA，SPI 作为回退；get_info 返回模式、时钟、容量和统计 |
| flash | JEDEC/容量、read、受 `[2,4)` MiB 边界保护的 program/erase；无写后读回 |
| display | SPI LCD 状态页，DDR RGB565 双帧的 init/frame/present/stop/status |
| audio | PT8211 PCM16 stereo，46,875 Hz；PIO FIFO / DDR ring DMA、start/pause/stop/mute、进度及欠载统计；见 [音频](audio.md) |
| ethernet | RTL8201F MDIO、100M full-duplex link、LiteEth原始帧、packet slots、IRQ/错误统计、Flash UID派生MAC；见 [Ethernet](ethernet.md) |
| usb | OHCI Host、DDR 描述符/DMA、FS HID 枚举；Boot 键盘 usage/修饰键按下释放、Boot 鼠标相对运动、raw HID reports、异步键盘 LED；见 [USB](usb.md) |

`hal_init()` 在应用启动时调用一次。当前是裸机单主循环：IRQ 只做 RX 搬运、计数和按键事件确认，SD/FatFs、SPI、显示和 Flash 传输在主循环执行。`hal_poll()` 每250 ms有界轮询Ethernet PHY链路，并处理 TinyUSB 枚举及 HID 回调；应用需频繁调用。它不消费网络包，应用需要调用 `hal_eth_receive()` 及时取走帧，并取走 USB raw/键盘/鼠标事件以免队列溢出。SPI/文件系统及 USB API 不支持多个调用者或 ISR 并发操作。

timer0 保留原来的自由运行计数，为既有驱动提供 32 位 tick 超时，同时增加 64 位 uptime；timer1 每 60000 sys 周期产生一次 IRQ，约 1 ms。毫秒 deadline 使用 `(int32_t)(now-deadline)`，适用于间隔小于 `2^31` 毫秒；原 ticks 的耗时比较使用无符号减法，不用普通大小比较。

## 引脚与 IO 语义

| 设备 | 位序 / 引脚 | 语义 |
| --- | --- | --- |
| `hal_leds_set(mask)` | bit0..5：C13、A13、N16、N14、L14、L16 | 按原理图 Orange_LED[0..5]；1 为亮，硬件反相为低有效，复位默认全灭 |
| 按键 | bit0..3：T3、T2、D7、C7，即 Key2..5 | 两级同步，5 ms 连续稳定后更新；1 为按下；按下/释放只在稳定状态变化时记录 |
| DIP | bit0..3：E9、E8、T4、T5 | 两级同步；1 为 ON；不是 B10/RECFG |
| WS2812B | T9，LVCMOS33 | CPU 色值 `0xRRGGBB`，硬件发 GRB / MSB-first，busy 时提交返回 HAL_BUSY |
| 公共 PHY reset | F10，LVCMOS33 | 唯一 GPIO output；同时复位 Ethernet/USB PHY，禁止为两个设备重复申请引脚 |

四按键和四 DIP 采用 LVCMOS15；LED/WS2812B/公共 reset 为 LVCMOS33。C13/A13 使用现有平台的 DONE/READY 转 GPIO 选项。Key1/T10 保留系统复位，B10/RECFG 保留 FPGA 配置用途。

按键事件为两个 **合并位掩码**，`hal_buttons_take()` 取走按下和释放标志；同一按键在主循环处理前多次变化会合并，不承诺计数队列。硬件 W1C 清除事件，同周期的新边沿优先保留。长按不反复产生 pressed；没有自动 repeat。

WS2812B 所有时序由 sys 状态机完成：每 bit 75 周期 = 1.25 µs，0 高 24 周期 = 400 ns，1 高 48 周期 = 800 ns，发送前初始低和发送后 latch 均至少 18000 周期 = 300 µs。颜色在提交时锁存，之后修改颜色寄存器不会改变正在发送的帧。无需新 PLL。

`hal_phys_reset(hold_ms)` 先停止Ethernet IRQ/MAC和USB Host/DMA，取消旧packet-slot和USB队列所有权，把F10拉低至少10 ms，释放50 ms后重新初始化Ethernet与USB。HAL_OK表示公共复位动作完成，设备初始化结果由 `hal_eth_get_info()` / `hal_usb_get_info()` 查询。它同时复位Ethernet/USB PHY，不控制VBUS。`hal_usb_init()` 的单独重启用STP退出PHY串行模式，保持F10释放，不中断Ethernet。

## 中断与异常

生成的SPI 回退的 IRQ 编号是 UART=0、timer0=1、board_io=2、timer1=3、ethmac=4、usb_host=5；原生 SD 构建为 UART=0、timer0=1、sdcard=2、board_io=3、timer1=4、ethmac=5、usb_host=6。runtime启用UART RX、board IO、timer1及成功初始化后的Ethernet/USB；timer0 保留轮询。SD DMA 当前也使用有界轮询；SD event IRQ 定义在硬件中，未启用到 CPU runtime。USB IRQ 向 TinyUSB 主循环队列送完成事件。使用 VexRiscv 的 `0xbc0` mask / `0xfc0` pending 和 machine external IRQ，初始化 `mtvec`、`mie.MEIE`、`mstatus.MIE`。

trap 汇编保留 x1..x31、原 sp、mepc/mstatus/mcause/mtval，栈保持 16 字节对齐。默认异常输出 cause/pc/value 后停止；自定义 handler 若要恢复执行，需要正确更新 frame。尚无独立异常栈、嵌套 IRQ、调度器或 RTOS runtime。

基础 monitor 保留 help/status/ls/reboot，并增加正常板级控制命令：`io`、`led HH`、`rgb RRGGBB`。如 `led 01` 亮原理图第零灯，`rgb 200000` 显示低亮度红色。按键事件异步输出 `BUTTON pressed=... released=...`；`status` 显示 IRQ、RX 丢字节及视频欠载计数。

M10 按用户要求增加统一 `test ...` 固件命令，覆盖设备自检与有界并发。
`test irq` 只为主动触发的 ECALL 放行异常并返回，其他异常仍报告 FAULT。
验收逻辑在 DDR app，不进入 HAL 驱动或 boot ROM；命令和外部验收边界见
[固件测试](firmware-tests.md)。

## 示例与验证

`firmware/examples/hal_demo.c` 是独立 UART 加载示例，不加入基础 monitor。它覆盖 ECALL 处理并返回、timer IRQ、在真实 DDR 栈上运行的确定性计算、deadline 回绕和 UART IRQ echo。

```powershell
& $MiniPython scripts/build.py --app firmware/examples/hal_demo.c --output-dir build/m5a-demo
& $MiniPython scripts/boot_upload.py --reset --mode uart --image build/m5a-demo/firmware/app.img
# 示例输入 Q 返回 UART IRQ ECHO=00000051；! 返回 bootloader。
& $MiniPython scripts/boot_upload.py --reset --mode uart
```

先构建/下载当前 FPGA 配置，示例才能使用匹配的 CSR ABI。模拟检查运行 `sim/test_board_io.py`，包括去抖、长按不重复、释放、并发按键、GRB/pulse/latch 和 busy 提交忽略。实际验收状态见 [M5a 验证](m5a-validation.md)。

原生 SD 的块缓冲、错误取消和运行频率见 [原生 SD](native-sd.md)。M5a 验证页记录当时的 SPI 构建，最新基础版见 [M6 验证](m6-validation.md)。

`hal_flash_uid()` 读取工厂唯一序列号。SPI LCD提供 `hal_spi_lcd_network(mac,ip)` 与 `hal_spi_lcd_link(up)`，支持冒号/句点字形和局部更新。基础monitor默认显示实际MAC和IP UNCONFIGURED；显式 `test eth start` 启用测试用 ARP/ICMP/UDP 子集并显示测试 IP，`test eth stop` 恢复未配置状态。独立Ethernet示例也拥有自己的测试地址，HAL本身不提供完整IP栈。见 [Ethernet](ethernet.md) / [M8验证](m8-validation.md)。
