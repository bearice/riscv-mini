# AE350 bootloader

适用机器：`mega138k_ae350`。配置以机器 JSON 和所选产物的导出为准。

平台常驻启动固件通过官方 SPI Flash Instruction Memory 从 U4 执行，CPU 复位入口为 `0x80000000`。配置位流使用物理 `[0,0x600000)`；固件区从 `0x600000` 延伸至 Flash 末尾。容量来源为Board JSON的 `resources.config_flash.size_bytes`，链接边界与安装元数据由此派生。当前U4为16 MiB，固件可用10 MiB，对应CPU地址 `0x80000000–0x809fffff`。

普通代码与常量直接放在 XIP 区，显式 `.ilm.text` / `.ilm.rodata` 热段在启动时复制到硬核 ILM；私有数据与栈使用硬核 DLM。start.S 初始化本地内存和段内容，main.c 完成 POST，随后提供 UART 镜像上传、ABI/范围/CRC 校验和 DDR 应用交接。ILM/DLM 的分区和实际容量见[地址合同](address-map.md)。

独立启动合同在 `boot_contract.py`。SYSINFO 固定提供：

| 偏移 | 内容 |
| --- | --- |
| `0x0c` | 启动合同版本，当前为1 |
| `0x10` | 实际 sys/APB 时钟 Hz，用于 UART 分频 |
| `0x14` | 当前硬件 image ABI，用于核对 payload |

bootloader 的构建输入为启动源码、启动合同和编译配方/工具。外设与频率参数从稳定的运行时接口读取；启动依赖的寄存器或内存合同变化时显式更新合同。应用 BSP 与诊断载荷分别构建。

## 构建

使用 `mini.py build bootloader --machine mega138k_ae350`；`build gateware` 也会解析所需启动产物。链接器和构建器共同检查固件区末尾，元数据分别记录可用容量和实际镜像长度。普通代码/常量可使用整个 Flash 固件区，热段和可写数据分别受其 ILM/DLM 分区约束。

当前系统为 sys/AHB/APB 50 MHz、mem/DDR UI 100 MHz、CPU 800 MHz。Flash串行时钟和读模式由SDK控制器决定；完整构建时序和实板状态以匹配产物回执为准。

## 安装与更新

`mini.py board bootrom --build <gateware-result.json>` 准备实际长度的启动镜像；追加 `--install --cable-serial <FPGA序列号> --port <UART>` 执行安装。安装前校验目标FPGA、JEDEC与容量，按实际镜像长度备份涉及的擦除块及可用邻接保护块，读改写保存首尾块中的原有数据，再独立读回比较。镜像接近Flash末尾时，读写范围截止于器件容量。安装完成后通过SRAM候选确认自主启动。

后续仅在启动镜像变化时重新安装。位流更新使用 `mini.py board update --machine mega138k_ae350 --build <gateware-result.json> --bootrom-install <成功安装回执/result.json> --cable-serial <FPGA序列号> --port <UART>`。相同bootloader的成功回执可复用；每次写配置前重新读取实际启动镜像，按其长度保护固件区域，并在更新后再次比较。最后通过JTAG RELOAD从配置Flash启动。

当前FR控制页位于 `0xf8090000`，APB适配器保持事务直至FR接口完成，校验寄存器偏移、方向和写选通。U7用户Flash使用独立硬SPI通路。

已有镜像的安装、ROM读回与重载启动证据见[实板记录](../../../reports/ae350-sys50-board-install.md)。本文的容量和链接规则由当前源码定义，大镜像实板验收按具体产物记录。
