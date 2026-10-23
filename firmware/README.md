# 固件目录

固件的启动存储、链接布局、IRQ/cache/DMA 合同由机器定义。共用设备核心和镜像传输不表示两台机器的程序可互换。

| 目录 | 职责 |
| --- | --- |
| `bootloader/<machine>/` | 各机器独立的常驻启动固件 |
| `bsp/<machine>/` | 机器启动运行时、链接布局及设备适配 |
| `diagnostics/<machine>/`、`examples/<machine>/` | 按机器划分的诊断和应用 |
| `hal/`、`drivers/` | HAL 和驱动；实际支持范围见机器文档 |
| `bios/` | 20K 常驻 BIOS、服务 ABI 与 SDK |
| `opensbi/` | 20K OS 引导平台和探针 |
| `common/`、`libc/` | 按目标选择的辅助代码和应用运行库 |
| `apps/` | BIOS 二级程序等独立应用 |
| `vendor/` | 固定第三方源码及许可证 |

## 按机器构建

所有构建使用 `mini.py build <target>`。硬件相关参数来自机器 JSON，软件消费所选硬件产物的导出。

```sh
python scripts/mini.py build bios --hardware <20K-system-digest>
python scripts/mini.py build system --machine primer20k_vexriscv --source firmware/examples/primer20k_vexriscv/monitor.c
python scripts/mini.py build bootloader --machine mega138k_ae350
python scripts/mini.py build payload --machine mega138k_ae350
```

20K 的 bootloader 完成 DDR 初始化和 Flash/UART 装载，BIOS 再装载 RPB/OSB；见 [20K 固件与接口](../docs/platforms/primer20k_vexriscv/README.md)、[BIOS](../docs/platforms/primer20k_vexriscv/bios.md) 和 [二级程序移植](../docs/platforms/primer20k_vexriscv/bios-payload-porting.md)。ROM 不链接 BIOS 的全部外设驱动。

AE350 启动固件在 `bootloader/mega138k_ae350/`，负责 ITCM 启动、ILM/DLM 初始化、自主 POST 和 UART loader；应用运行时在 `bsp/mega138k_ae350/`，诊断在 `diagnostics/mega138k_ae350/`。见 [AE350 平台](../docs/platforms/mega138k_ae350/README.md) 与 [启动固件](../docs/platforms/mega138k_ae350/bootloader.md)。

产物复用、ABI 校验与稳定 workspace 规则见 [组件构建](../docs/common/component-builds.md)。
