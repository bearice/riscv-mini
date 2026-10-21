# AE350 板级 I/O 与中断

当前交付点为 sys 80 MHz、mem/UI 100 MHz、DDR CK 400 MHz，gateware `44405c059cf8`。板级 I/O、中断、共享核心和受影响的 DDR/DMA 回归通过；性能和精确身份见[验收与性能报告](../reports/performance/ae350-board-io.md)。风扇调速、停转和硬件改线按用户要求暂缓，不属于本次签收。

## 板级绑定与软件接口

`gateware/boards/mega138k_pro/board.json` 的 `user_io` 是 LED、按键、WS2812 的映射来源，数量、GPIO 位号和极性进入生成头文件和镜像 ABI。底板有六盏低有效 LED（J14、R26、L20、M25、N21、N23）、四个低有效按键（K16、F15、G15、G16，5 ms 去抖）和 H16 上的 WS2812B-2020，没有拨码开关。复位 LED 高阻、灯灭。摄像头、两组 JTAG、UART、DDR、配置脚和核心板复位不发布为用户 GPIO。

LED 由板级页 `0xf8080028` 的逻辑寄存器控制，1 为亮，读回为寄存器值；按键及诊断注入位接硬核 GPIO 输入 `0xf0700000`。当前加密网表不能启用 `AE350_GPIO_IOPORT_EN`，因此不冒称 LED 是硬 GPIO 输出。软件通过 `board_led_*`、`board_button_get`、`board_events_take` 和 `board_ws2812_*` 使用逻辑设备。硬 GPIO 驱动仅接受发布的输入位；非法位、方向和未识别设备在写寄存器之前拒绝。调用者串行使用板级输出接口；不提供多调用者仲裁。

ATCGPIO100 中断使能为 `+0x50`，模式从 `+0x54` 开始每通道占四位、每组八通道，上升沿编码 6，状态为 `+0x64`。GPIO 的 PLIC source 为 7，实板套件核对此号。20K HAL 保持旧接口，同时提供有返回状态的查询接口；AE350 的板级 API 不伪造不存在的开关读数。

## 共享核心与中断合同

`gateware/devices/gpio/core.py` 和 `gateware/devices/ws2812/core.py` 不含总线地址或封装引脚。20K CSR Adapter 与 AE350 APB Adapter 复用同一份状态机。AE350 的 WS2812 页为 `0xf8070000`，板级事件页为 `0xf8080000`，使用公共设备头。

80 MHz 下 WS2812 位槽 100 周期，0/1 高电平 32/64 周期，reset 间隔 24000 周期。输入颜色为 0xRRGGBB，输出为 GRB、MSB first。忙时提交不启动第二帧，LAST_ERROR=1；非法写产生 PSLVERR 且无副作用。H16 为 3.3 V、灯供电为 5 V，目视显示通过不等于该电平组合的全条件电气保证。

Fabric I/O 接 `EXT_INT[7]`，loopback 保持 `EXT_INT[0]`；本候选实测 I/O PLIC source 为 17。按键、诊断和 WS2812 完成可同时 pending；W1C 与新事件同拍时保留新事件，读取不清除。pending 位允许合并，不作为边沿计数器。

Payload BSP 提供 PLIC 注册、屏蔽、claim/complete 与 trap 分派，进入 main 前关闭中断。仅机器外部中断走 PLIC；软件、定时器和未知原因单独分派。发现 source 时先打开 fabric 门、保持 CPU 全局中断关闭，并有界等待及排空 gateway。正常 ISR 后 mcause 保留 `0x8000000b`；同步异常写独立 trap 记录，不通过清零 mcause 伪造成功。

## 可复用诊断

`firmware/examples/ae350_io_suite.c` 输出 32 字结果合同，完成字 `0xC4100004`、schema `0x50340001`。`board io-suite` 分别判定上传、执行和数字功能，包括身份/拒绝、LED 读回、1000 次事件、合并/屏蔽/重入、硬 GPIO claim、WS2812 完成/忙拒绝，以及 DMA 忙时的 ISR 与独立 CPU DDR 访问。物理观察独立记录，套件 physical 字保持 0。

```powershell
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --source firmware/examples/ae350_io_suite.c --extra-source firmware/ae350/dma/ae350_dma.c --include-dir firmware/ae350/dma
.venv/Scripts/python.exe scripts/mini.py board io-suite --build <gateware-result.json> --payload <payload-result.json> --port COM9 --location <FPGA位置> --cpu-jtag-location <CPU位置>
```

`firmware/examples/ae350_led_rgb_demo.c` 用 KEY0/KEY1 选择 LED、KEY2/KEY3 选择 RGB，独立打印温度，不写风扇占空比。历史目视确认记录为 `build/runs/20261020T170013748621Z-board-ae350-upload`，当前数字重测不伪造为再次目视。

## 温度与暂缓能力

`0xf8007000` 的温度采样使用右下 ADCLRC 和片内振荡器。CONTROL 写 1 开始，STATUS bit0=有效、bit1=忙，约 100 ms 未完成设置错误；`+0x28` 为毫摄氏度。14 位有符号采样乘 250，FSCAL=730、OFFSET=-1180 是厂商典型值，不是逐片标定。仿真固定返回 25 ℃，实板读数与绝对温度精度分别声明。

T17 经 R266（15 Ω）接 Q9 P-MOS 栅极，T18 是测速输入；用户已确认 R265/R266 实装与图纸一致。现有开漏栅极命令、测速寄存器和驱动接口保留为实验能力：默认持续拉低栅极，保持供电，0% 仅释放，不保证停转或调速；禁止将它们发布为已验收风扇控制。不得直接在仍连接 FPGA 的栅极节点加 5 V 上拉。现有能力字段描述数字电路存在，不等于物理风扇功能通过。后续硬件改动及对应 RTL 单独验收。

仅写 FPGA SRAM，不改 Flash、current 或原内存 baseline。硬 DMAC 忙通道保护及硬件错误传播仍是正式实时 DMA 外设接入前的前置项。
