# 板载用户 Flash 与 TWI mux

适用机器：`mega138k_ae350`。SPI 使用 AE350 硬核 ATCSPI200（`0xf0f00000`），I2C 使用硬核 ATCIIC100（`0xf0a00000`）。两者启用硬核数字端口，由 fabric 提供 SPI 三态缓冲与 I2C 开漏缓冲；I2C 只驱动低电平或释放，同时将物理 SCL/SDA 回接硬核输入，保留时钟伸展/仲裁所需的电平观察。

这里只接入用户 Flash U7 和管理总线。配置 Flash U4 保留，启动仍为 ITCM/UART loader；未启用嵌入式 SPI/XIP，也没有 Flash program/erase API。

## 物理连接

引脚来自核心板 `TANG_MEGA-138K-Pro_4071_Schematics.pdf` 的 U7 和 Dock `TANG_MEGA-138K_Pro-Dock-4071f_Schematics.pdf` 的 TWI_MUX/FPGA_STORAGE 页，唯一构建来源是 Board/Machine JSON。

| 设备信号 | FPGA 引脚 | 说明 |
| --- | --- | --- |
| U7 CSn / CLK | L25 / P20 | 3.3 V，SPI mode 0 |
| U7 DQ0/MOSI / DQ1/MISO | R21 / T22 | 首版使用单线 SPI |
| U7 DQ2/WPn / DQ3/HOLDn | R22 / R20 | 绑定硬核数字端口，板上上拉 |
| 公共 SYS_TWI SCL / SDA | K25 / K26 | 3.3 V、外部 2.2 kΩ 上拉，开漏 |
| mux 选择位 0 / 1 / 2 | N19 / P19 / P26 | 分别来自硬 GPIO 位 20 / 21 / 22 |

mux 是两颗 74HC4051，共同切换 SCL/SDA，没有 I2C 控制地址。选择值按上述命名引脚的位序解释：bit0=N19、bit1=P19、bit2=P26；不要将图纸表头元组直接当作 GPIO 位序。两颗开关的引脚顺序不同，板上走线已补偿，使同一选择值的 SCL/SDA 进入同一分支。

GPIO 选择输出始终由 DATAOUT 驱动，复位选择 0；BSP 会配置方向、保存旧选择与旧方向，结束时恢复。K25/K26/N19/P19/P26 从保留列表转为明确的管理总线资源，其他摄像头/调试保留引脚不变。

## 接口与操作

SYSINFO 追加 SPI/I2C 设备项，硬核寄存器保留原布局，设备表 ABI 字段为 0；逻辑设备、物理引脚和 mux 位序参与 image ABI。mux 使用现有硬 GPIO 寄存器，没有第二套 GPIO 或 I2C 软件控制器。

`board_management.h` 提供：

- `board_flash_init`：配置 1 MHz、8-bit mode 0，读取 JEDEC、状态和 SFDP；支持本次三字节地址范围。
- `board_flash_read`：只读 `0x03`，最多 512-byte 分块，调用者 buffer 可不对齐，越界在 MMIO 前拒绝。
- `board_i2c_init`：7-bit、轮询模式，配置保守标准速度时序，初选 EEPROM 分支 2；不是精确测得的 100 kHz 声明。
- `board_twi_mux_select/get`、`board_i2c_probe`、`board_i2c_read_register`：选择支路、地址 ACK/NACK 和一字节寄存器地址的 repeated-START 读取。
- `board_management_stop`：等待总线空闲、禁用 I2C 并恢复选择/方向。

SPI/I2C 轮询和接收循环放在 ILM，避免默认关闭 cache 时的 DDR 取指延迟妨碍 FIFO 服务。接口单线程、单所有者使用；错误和超时显式返回。未验收 SPI DMA/Quad、I2C 10-bit/中断、多主冲突或外部故障注入。

```sh
nix develop --command python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --purpose management
nix develop --command python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_bus_suite.c --purpose management-readonly
nix develop --command python scripts/mini.py board bus-suite --build <gateware-result.json> --payload <payload-result.json> --port <UART> --cpu-jtag-location <CPU-JTAG>
```

始终复用原 workspace（本轮为 default）。诊断通过 SRAM/UART 装载，UART 完成标记前不暂停 CPU。验证 JEDEC/SFDP、重复读取 CRC、guard、越界拒绝、EEPROM repeated-START 读取、未命中地址 NACK、八种选择读回及支路地址响应。EEPROM 只发送读指针，不写数据；不配置支路上的时钟芯片或其他设备。

下游地址响应只说明该选择值下有设备 ACK，不代表已枚举/验收该分支所有功能。RX HDMI 的 EDID slave/HPD、TX DDC、时钟配置和摄像头控制仍需各自实现。

## 寄存器依据

- [Linux ATCSPI200 驱动](https://github.com/torvalds/linux/blob/master/drivers/spi/spi-atcspi200.c)：SPI 命令/地址/读计数、FIFO 状态和分频。
- [Andes ATCIIC100 驱动](https://github.com/andestech/linux/blob/ast-v5_4_0-branch/drivers/i2c/busses/i2c-atciic100.c)：原生模块 ID 与寄存器。
- [Zephyr ATCIIC100 定义](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/i2c/i2c_andes_atciic100.h)：阶段控制、状态、ACK 和时序字段。

具体通过范围、准确镜像和原始回执见 [管理总线验收](../../../reports/ae350-management.md)。
