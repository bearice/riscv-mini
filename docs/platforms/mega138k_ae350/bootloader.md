# AE350 ROM bootloader

> 适用机器：`mega138k_ae350`。配置以机器 JSON 和所选硬件产物的导出为准。

这是 mega138k_ae350 平台的常驻启动固件，供后续 DDR、DMA、视频等阶段共用，不属于某一验收阶段。

复位入口 `0x80000000–0x80001fff` 使用官方 SPI Flash Instruction Memory 后端，对应 U4 物理 `0x600000–0x601fff` 的独立 8 KiB bootrom；不实例化 fabric ITCM。16 MiB U4 的 `[0,0x600000)` 留给配置位流，bootrom 后的空间保留。CPU 链接地址不会改成 `0x80600000`。用户 Flash U7 保持独立硬核 SPI 通路。

start.S 在使用栈前检查并初始化硬核 ILM/DLM，然后把 loader 代码和常量从 XIP 复制到已有的硬核 ILM。main.c 执行自主 POST，随后提供 UART 镜像上传、ABI/范围/CRC 校验与应用跳转。私有数据和栈位于 DLM。Flash 时钟与读模式由官方控制器决定；sys50 最小系统已通过 STA，但复位时读命令、实际 SCLK 和自主启动仍待实板确认，不套用之前自定义 SPI 后端的 25 MHz 结论。

独立启动合同在 `boot_contract.py`。SYSINFO 固定提供：

| 偏移 | 内容 |
| --- | --- |
| `0x0c` | 最小启动合同版本，当前为 1 |
| `0x10` | 实际 sys/APB 时钟 Hz，用于 UART 分频 |
| `0x14` | 当前硬件完整 image ABI，用于拒绝不匹配的 payload |

bootrom 构建输入只包括启动源码、启动合同和编译配方/工具，不依赖 CPU artifact、外设 RTL 或整份机器 JSON。修改频率、外设或去抖参数可复用同一份 bootrom；启动依赖的寄存器或内存布局不匹配时构建失败，必须显式更新启动合同。硬件完整 ABI 仍由每个 gateware 产物导出，host 上传流程从所选硬件读取，不能用 bootrom 身份代替硬件兼容性。

构建由 `scripts/mini.py build bootloader --machine mega138k_ae350` 或 `build gateware` 编排，产物类别为 bootloader，身份为 AE350-ROM-bootloader，大小由链接和构建共同限制在 8 KiB。应用和诊断载荷独立构建，不影响 bootloader 复用。构建不会写 Flash。

`mini.py board bootrom --build <gateware-result.json>` 仅准备 8 KiB 安装镜像和收据；显式追加 `--install --cable-serial <FPGA调试器序列号> --port <UART>` 才执行安装。安装前核对 FPGA IDCODE 与 JEDEC/容量，备份 `[0x5f0000,0x620000)` 的三个64KiB块。以读改写方式保存整个 `[0x600000,0x610000)` 擦除块，仅替换前8KiB启动槽，再读回完整邻域。不解除块保护，也不写配置位流；记录保存在 `build/runs/`。硬件必须先通过完整 STA。Flash及后续SRAM验证使用同一libusb工具，避免FTDI驱动自动回绑与D2XX交接冲突。

首次使用 XIP gateware 前需要先安装匹配的 bootrom；SRAM 编程本身不会安装或修复 Flash。后续仅在 bootrom 发生变化时重新安装。持久配置位流必须严格小于 6 MiB 并保留固件分区。

用户明确要求持久更新后，使用 `mini.py board update --machine mega138k_ae350 --build <gateware-result.json> --bootrom-install <成功安装回执/result.json> --cable-serial <FPGA序列号> --port <UART>`。它检查相同bootloader的成功安装回执，并重新比对实际启动槽，因此无关gateware变化不要求重刷bootrom。配置字节从不可变FS逐行解码，保存最后擦除块的原有尾部及下一保护块；写入后独立读回整个范围并核对启动邻域完全不变。最后显式JTAG RELOAD，从配置Flash启动，不加载SRAM位流，不冒充断电冷启动。

sys50镜像已完成bootrom安装、JTAG ROM/硬件ABI校验及配置Flash逐字节读回与重载启动，见[实板安装记录](../../../reports/ae350-sys50-board-install.md)。

应用运行时位于 firmware/bsp/mega138k_ae350；可复用内存诊断位于 firmware/diagnostics/mega138k_ae350。二者不作为 bootloader 的源文件或链接依赖。旧 fabric ROM/SRAM 监视器已退役；历史实现仅通过 snapshot commit 回溯。

当前 sys 配置已按用户决定降为 50 MHz；启用 FR 的官方 Flash 最小系统在 50 MHz 通过 STA，100/80 MHz 未通过，见[最小系统探测](../../../reports/ae350-minimal-flash-timing.md)。保留 sys/mem 独立 PLL 输出；音频回到 sys50，移除独立 audio80 域及其 CDC。APB 简化为捕获命令、一次访问提交和最终注册响应，SD/Flash 等慢设备仍等待真实完成。额外的 AHB 响应中继及候选预采样已删除；DSP 推断不再被强制禁止。完整系统 STA 与实板结果须分别判定。

官方 FR 管理页为 `0xf8090000`，独立 APB 适配器保存事务，完成原生 FR 访问后才响应，并拒绝未知/只读寄存器写入和非法字节选通。SYSINFO 地址不变。
