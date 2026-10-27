# 板载用户 Flash 与 TWI mux

适用机器：`mega138k_ae350`。用户 Flash U7 使用独立 fabric QSPI 控制器（`0xf8050000`），运行于 sys50，SCK 默认 25 MHz。I2C 使用硬核 ATCIIC100（`0xf0a00000`），SCL/SDA 由 fabric 提供开漏缓冲并回接硬核输入。

U4 使用官方 SPI Flash Instruction Memory 和 FR 接口，启动固件从物理 `0x600000` 延伸至 Flash 末尾；安装见 [bootloader](bootloader.md)。U7 容量由 Board JSON 的 `user_flash.size_bytes` 导出，BSP 以 JEDEC 密度核对实物。

## 物理连接

引脚来自核心板 `TANG_MEGA-138K-Pro_4071_Schematics.pdf` 的 U7 和 Dock `TANG_MEGA-138K_Pro-Dock-4071f_Schematics.pdf` 的 TWI_MUX/FPGA_STORAGE 页，唯一构建来源是 Board/Machine JSON。

| 设备信号 | FPGA 引脚 | 说明 |
| --- | --- | --- |
| U7 CSn / CLK | L25 / P20 | 3.3 V，SPI mode 0 |
| U7 DQ0/MOSI / DQ1/MISO | R21 / T22 | QSPI IO0 / IO1 |
| U7 DQ2/WPn / DQ3/HOLDn | R22 / R20 | QSPI IO2 / IO3，板上上拉 |
| 公共 SYS_TWI SCL / SDA | K25 / K26 | 3.3 V、外部 2.2 kΩ 上拉，开漏 |
| mux 选择位 0 / 1 / 2 | N19 / P19 / P26 | 分别来自硬 GPIO 位 20 / 21 / 22 |

mux 是两颗 74HC4051，共同切换 SCL/SDA，没有 I2C 控制地址。选择值按上述命名引脚的位序解释：bit0=N19、bit1=P19、bit2=P26；不要将图纸表头元组直接当作 GPIO 位序。两颗开关的引脚顺序不同，板上走线已补偿，使同一选择值的 SCL/SDA 进入同一分支。

GPIO 选择输出始终由 DATAOUT 驱动，复位选择 0；BSP 会配置方向、保存旧选择与旧方向，结束时恢复。K25/K26/N19/P19/P26 从保留列表转为明确的管理总线资源，其他摄像头/调试保留引脚不变。

## 接口与操作

SYSINFO 的 QSPI 设备项发布控制页、范围和寄存器 ABI 1；引脚、容量、控制器类型和设备表参与硬件 image ABI。I2C 保持原生寄存器布局，mux 使用硬 GPIO 位 20–22。

`board_management.h` 提供：

- `board_flash_init`：读取 JEDEC、SR1/SR2 和 SFDP；对已支持的 Winbond `EF4018` 启用四线读取。QE 采用 `50h` 后接 `01h` 的易失状态寄存器写法，保持 SR1 和 SR2 其余位。
- `board_flash_read`：`6Bh` 的 1-1-4 读取；命令、24-bit 地址和八个 dummy 时钟使用单线，数据使用四线。支持非对齐缓冲，越界在 MMIO 前拒绝。
- `board_flash_read_single`：`03h` 单线读取，用于同地址比对。
- `board_i2c_init`、`board_twi_mux_select/get`、`board_i2c_probe`、`board_i2c_read_register`：管理总线初始化、支路选择、ACK/NACK 和 repeated-START 读取。
- `board_management_stop`：等待 QSPI 完成、恢复原始 QE 状态，禁用 I2C 并恢复 mux 选择和 GPIO 方向。失败时返回错误。

QSPI 以轮询方式使用，单线程、单所有者；寄存器/接收热循环位于 ILM。阵列 program/erase API 由后续明确的存储用途定义。I2C 时序使用保守标准速度配置。

| QSPI 偏移 | 寄存器 |
| --- | --- |
| `0x00 / 0x04 / 0x08` | ID=`0xae350b01`、ABI=1、能力=3（单线/四线） |
| `0x0c` | CONTROL：bit0 保持 CS，bit1 四线，bit2 接收，bits9:8 为字节数减一 |
| `0x10` | STATUS：bit0 busy |
| `0x14` | SCK 半周期分频减一，0–255；SCK=sys/(2×(divider+1)) |
| `0x18 / 0x1c` | TX DATA / RX DATA，1–4 字节，线上 MSB first；发送数据左对齐 |
| `0x20 / 0x24` | sys 源时钟 / Board Flash 容量 |

写 TX DATA 提交一次传输，CS 可在多次传输之间保持有效。busy 时修改 CONTROL、divider 或 DATA 返回 APB ERROR；部分写和未知偏移也被拒绝。控制器与总线均在 sys 域。

```sh
nix develop --command python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --purpose management
nix develop --command python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_bus_suite.c --purpose management-readonly
nix develop --command python scripts/mini.py board bus-suite --build <gateware-result.json> --payload <payload-result.json> --port <UART> --cpu-jtag-location <CPU-JTAG>
```

始终复用原 workspace（本轮为 default）。诊断通过 SRAM/UART 装载，UART 完成标记前不暂停 CPU。验证 JEDEC/SFDP、重复读取 CRC、guard、越界拒绝、EEPROM repeated-START 读取、未命中地址 NACK、八种选择读回及支路地址响应。EEPROM 只发送读指针，不写数据；不配置支路上的时钟芯片或其他设备。

下游地址响应只说明该选择值下有设备 ACK，不代表已枚举/验收该分支所有功能。RX HDMI 的 EDID slave/HPD、TX DDC、时钟配置和摄像头控制仍需各自实现。

## 接口依据与验收

- [Winbond W25Q128JV 数据手册](https://www.winbond-electronics.com/pdf-7d/w25q128jvsjq.pdf)：QE、易失状态更新、`03h` 与 `6Bh` 时序。
- [Andes ATCIIC100 驱动](https://github.com/andestech/linux/blob/ast-v5_4_0-branch/drivers/i2c/busses/i2c-atciic100.c)和 [Zephyr 寄存器定义](https://github.com/zephyrproject-rtos/zephyr/blob/main/drivers/i2c/i2c_andes_atciic100.h)。

本轮结果见 [P7 验收记录](../../../reports/ae350-p7.md)，历史管理总线记录见 [先前验收](../../../reports/ae350-management.md)。
