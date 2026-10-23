# riscv-mini

多平台 RISC-V FPGA 工程。板卡物理资源由 Board JSON 描述，CPU、启动、内存、设备绑定和 IP 参数由 Machine JSON 描述；各机器通过同一构建与产物管理入口开发。

| machine | 板卡 / CPU | 文档入口 |
| --- | --- | --- |
| `primer20k_vexriscv` | Tang Primer 20K + Dock 3713 / VexRiscv 软核 | [20K 平台](docs/platforms/primer20k_vexriscv/README.md) |
| `mega138k_ae350` | Tang Mega 138K Pro / AE350 硬核 | [138K / AE350 平台](docs/platforms/mega138k_ae350/README.md) |

启动流程、时钟、地址、外设、固件 ABI 和验收资格分别由各机器定义。共同的配置格式不表示两台机器的镜像或驱动兼容。

## 开始开发

Linux/NixOS 使用仓库 Nix flake；其他环境的工具路径由 `.tools.local.json` 发现。

```sh
nix develop
python scripts/mini.py --help
python scripts/mini.py doctor
python scripts/mini.py plan gateware --machine primer20k_vexriscv
python scripts/mini.py plan gateware --machine mega138k_ae350
```

构建使用 `mini.py build <target> --machine <machine>`。先编辑对应机器 JSON，再运行相同命令；CLI 不提供硬件覆盖。重复构建保留同一 workspace 和缓存，默认 `default`。准确产物和收据记录在 `build/store` 与 `build/runs`，候选不自动成为 current。

完整入口见 [文档目录](docs/README.md)、[开发环境](docs/common/development.md)、[操作指南](scripts/README.md) 和 [组件构建](docs/common/component-builds.md)。

## 源码目录

| 路径 | 职责 |
| --- | --- |
| `gateware/boards/` | 物理板卡 JSON |
| `gateware/machines/<machine>/` | 机器配置、SoC、总线、内存和设备适配 |
| `gateware/devices/`、`gateware/vendor/` | 可复用设备核心与固定厂商输入 |
| `firmware/bsp/<machine>/`、`firmware/bootloader/<machine>/` | 机器运行时与启动固件 |
| `firmware/` | HAL、BIOS、诊断、应用与依赖，见 [固件目录](firmware/README.md) |
| `scripts/`、`tests/` | 共享工具与按机器划分的操作、回归 |
| `docs/` | 通用规则、各机器设计、历史提案的独立入口 |
| `reports/` | 带准确候选身份的改动、验收和性能记录 |

普通提交遵循 [轻量 SOP](docs/common/pre-commit-sop.md)。版本、CHANGELOG、完整发布验收与源码归档仅在明确要求正式 release 时处理；发布记录见 [版本追溯](docs/common/version-history.md)。
