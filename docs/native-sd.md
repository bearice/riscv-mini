# 原生 SD 与 SPI 回退

原生 SD 使用锁定版 LiteSDCard 的 SDPHY、SDCore 和双向 Wishbone DMA。协议是 SD memory，一位初始化后协商四位 SDR；不支持 SDIO I/O function 卡。现有 FatFs 和公共 `hal_sd_*` 接口复用，bootloader 不链接任何 SD 驱动。

## 频率与引脚

sys 为 60 MHz。初始化 divider=150 得到 400 kHz；正常 divider=8 得到 **7.5 MHz**。15 MHz 的读取曾上板通过，但首笔 CMD24 写数据响应失败；单独降低时钟后写入/读回通过。当前选用稳定的 7.5 MHz，15 MHz 写采样裕量属于后续优化，不能把读通过当作写通过。

| 信号 | FPGA 引脚 |
| --- | --- |
| CLK | N10 |
| CMD | R14 |
| DAT0..3 | M8、M7、M10、N11 |
| Card Detect | D15，低表示插卡，两级同步 |

引脚使用 LVCMOS33。同一构建只能由原生或 SPI 控制器之一使用这些脚。无需新 PLL；SD 使用 sys 分频和 SDR IO。

卡进入 SPI 模式后需要重新上电才能返回 SD 模式；仅 FPGA 软件复位、SRAM 下载或 CMD0 不能完成这一转换。本板 SD 供电直连，没有 CPU 可控的卡电源开关。切换时先写入原生 FPGA 配置和匹配应用，再拔插 SD 卡或给整板重新上电；否则原来的 SPI 启动应用会再次将卡置为 SPI。[Espressif 官方说明](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32c61/api-reference/peripherals/sdspi_share.html)记录了这一模式限制。

## 传输与错误处理

初始化发送初始时钟、CMD0/CMD8、CMD55/ACMD41、CID/RCA/CSD、选择卡、读取 SCR 和协商四位。SDHC 使用块地址，SDSC 使用字节地址并设置 512 字节块；容量来自 CSD，超过当前 32 位 LBA 能力的卡拒绝初始化。实卡验收使用 SDHC，SDSC 分支尚无对应实卡验证。

DMA 通过现有 sys Wishbone 仲裁和 DDR 桥访问内存，DDR 物理端口和视频优先调度保持原设计。传输经 **4 KiB、四字节对齐的 DDR bounce buffer**，因此上层缓冲不必对齐。每次最多八块；大请求分段，单块用 CMD17/24、多块用 CMD18/25，随后 CMD12 停止。读操作只有在数据 CRC 和 DMA 完成都成功后才复制到调用者；写操作先复制到 DMA 缓冲。默认构建启用 4 KiB 共享读缓存，只缓存读、写直达 DDR，所有权切换仍使用 `fence rw,rw`。

命令与数据使用硬件 CRC7/CRC16；软件检查 R1/R6 和 event 错误。锁定版 SDCore 未把命令 CRC 错误传给 CSR，本地 `gateware/vendor/sdcore.py` 只修正该状态连接，保留原版权。仿真能复现原版坏 CRC 返回成功，本地返回 done/error/crc。上游功能依据见 [LiteSDCard](https://github.com/enjoy-digital/litesdcard)。

命令 PHY timeout 为 250 ms，数据读 timeout 为 500 ms；软件 event / DMA 等待各最多一秒，ACMD41 初始化循环最多两秒。写响应/busy 超时也由软件等待覆盖。失败禁用 DMA、复位 SD 控制器及 FIFO，并标记未初始化，后续 `hal_sd_mount()` 重新初始化。CMD13 检查写后状态，`CTRL_SYNC` 不添加额外的数据读回。

CD 在每次等待与块操作中检查。检测无卡时标记介质不可用，重新插卡后需要重新 mount；该路径仍需独立实物拔插验证。控制器 event IRQ 已定义，但当前 DMA 使用有界轮询；应用 timer/UART/GPIO IRQ 继续工作。

`hal_sd_get_info()` 返回 backend、宽度、当前时钟、容量、介质/初始化状态，以及原生读块/写块/错误累计值。SPI 回退仍复用原驱动，其三个累计统计目前为 0，表示未实现统计；模式/时钟/容量/状态仍有效。原生构建的 `hal_spi_transfer(HAL_SPI_SD,...)` 与 `hal_spi_select(HAL_SPI_SD,...)` 返回 `HAL_UNSUPPORTED`，SPI LCD 不受影响。调用模型仍为一个裸机主循环，不支持 ISR/多个任务并发访问 SD/FatFs。

## 构建、下载与验收

```powershell
. .\scripts\env.ps1
# 默认基础应用 + 原生 SD。
& $MiniPython scripts/build.py --synthesize

# 独立 SPI 回退；需要其自己的 FPGA 配置、boot ROM 和 app.img。
& $MiniPython scripts/build.py --sd-backend spi --output-dir build/spi --synthesize

# 独立测试应用，保持基础 monitor 无写测/压力命令。
& $MiniPython scripts/build.py --sd-backend native --app firmware/examples/sd_demo.c --output-dir build/m6-demo
& $MiniPython scripts/sd_verify.py --output-dir build/base --reset --write-test --soak-seconds 300

# 恢复基础应用。
& $MiniPython scripts/boot_upload.py --reset --mode uart
```

独立示例中 `r` 读取现有 `RVTEST00.BIN` 并计算 CRC；`b` 比较 16 次单块和一次 16 块读取，使用未对齐缓冲；`w` 用 `FA_CREATE_NEW` 选择未占用的 `RV6Txxxx.BIN`，写入 64 KiB、关闭、重新打开并逐字节/CRC 验证。它不会格式化或覆盖既有文件。`f` 换帧，`s` 状态，`!` 复位。测试文件保留在卡上。

`sd_verify.py` 默认只读，`--write-test` 才新建一个文件；所有后续持续轮次只读原有测试文件和换帧。构建脚本检查 backend 与生成的硬件常数匹配，镜像 ABI 包含 CSR/IRQ，不能将 SPI 应用混用于原生 FPGA 配置。当前配置与限制见 [系统设计](system-design.md)。
