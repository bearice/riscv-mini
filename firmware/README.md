# 固件目录

默认构建 `bios/main.c`：ROM 从 Flash/UART 装载 BIOS 到 DDR，BIOS 再从 SD/TFTP 引导裸机程序。ROM 不链接显示、文件系统、USB、音频或网络驱动。

| 目录 | 职责 |
| --- | --- |
| `bios/` | 默认常驻 BIOS：setup/POST、TTY、图形与 IO 服务、设置、SD/TFTP 引导；`include/bios.h` 为二级程序的公开 ABI |
| `opensbi/` | 单 hart M-mode SBI 平台、兼容补丁和 S/U-mode 硬件探针；使用 OSB1 镜像由 BIOS 一次性交接 |
| `bootloader/` | 小型 ROM loader、ROM Flash 驱动和镜像协议；`boot.ld` 为 ROM，`app.ld` 为 DDR 固件链接布局 |
| `boot/` | ROM 与 DDR 固件共用的启动汇编，按构建宏选择启动路径 |
| `hal/` | 公共设备 API、IRQ/trap、板级 IO，以及音频/麦克风/网络/USB 实现 |
| `drivers/` | UART、timer、SPI、Flash、SD/FatFs、LCD 和 freestanding 字符串后端 |
| `diagnostics/` | BIOS 与 monitor 共用的 `test ...` 检查与输入事件观察；不属于 ROM |
| `common/` | BIOS、诊断与示例共用的 RGB 绘图/font 和网络报文辅助函数 |
| `examples/` | 可选独立示例；旧串口 monitor 为 `monitor.c`，不是默认固件 |
| `apps/` | 独立二级程序应用；`gwbasic/` 是可被 BIOS 加载的 GW-BASIC 解释器 |
| `vendor/` | FatFs、TinyUSB 固定版本源代码及许可证 |

构建入口：

```powershell
& $MiniPython scripts/build.py                            # BIOS
& $MiniPython scripts/build.py --app firmware/examples/monitor.c
& $MiniPython scripts/build.py --app firmware/examples/usb_input_demo.c
& $MiniPython scripts/bios_payload.py --source firmware/examples/bios_demo.c
& $MiniPython scripts/gwbasic_image.py                    # GW-BASIC 解释器 RPB1
```

前三种生成由 ROM 直接装载的 DDR 固件。`bios_demo.c` 和 `nyancat_demo.c` 使用独立裸机 SDK，生成由 BIOS 装载的 RPB1 程序，不能作为 `build.py --app` 入口。[Nyan Cat 示例](../docs/nyancat-demo.md) 包含 GIF 帧转换、动态背景和循环 BGM。ABI、内存所有权和命令见 [BIOS](../docs/bios.md)，硬件检查见 [固件测试命令](../docs/firmware-tests.md)。生产固件和诊断不引用 examples 内的实现。

[GW-BASIC 解释器](../docs/gwbasic.md) 是 `apps/` 下的完整应用，同样由 BIOS 从 SD/TFTP 装载；它只用 BIOS 的 ecall 服务，所以主机侧可以用 `scripts/gwbasic_sim.py` 在 QEMU 上跑真实产物，验收见 `tests/gwbasic_test.py`。

OpenSBI 的构建工具、镜像布局、CSR timer 和验收边界见 [OpenSBI 平台](../docs/opensbi-port.md)。它不链接 BIOS 的设备 HAL，普通 RPB1 程序仍使用原来的可返回 BIOS ABI。
