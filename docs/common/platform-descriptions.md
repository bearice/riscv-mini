# 平台构建配置

20K 与 138K 共享严格的 Board/Machine schema，当前版本为 2。
分层边界见 [平台配置与 LiteX 集成](platform-configuration-architecture.md)。

## 唯一配置来源

- `gateware/boards/<board>/board.json`：FPGA、晶振、物理资源、引脚、电气约束和保留引脚。
- `gateware/machines/<machine>/machine.json`：机器所用资源、时钟、内存窗口、启动、GPIO、RTL/PnR 参数和 IP 生成参数。

SPI NOR的物理容量由Board资源的 `size_bytes` 定义。当前20K的 `resources.flash.size_bytes` 为8388608；138K的 `resources.config_flash.size_bytes` 和 `resources.user_flash.size_bytes` 为16777216。类型化描述、硬件导出和安装检查消费同一板级值。地址孔径、镜像分区和启动偏移属于平台布局：AE350固件容量按配置Flash容量减去0x600000计算，20K的XIP/镜像窗口保持平台定义的4MiB。

未知字段、重复键、错误类型、资源冲突及不支持的参数组合会在构建前被拒绝。
JSON 不保存自由文本、推测或验收状态。

硬件覆盖参数已经从 CLI 删除，包括 profile、feature、l2-size、cpu-verilog 和 CPU
生成特性。修改机器 JSON 后执行同一个构建命令即可。`plan` 不修改配置文件。
工具路径由环境发现，软件源文件和操作选项仍由公共 CLI 指定。

```sh
python scripts/mini.py plan soc-description --machine primer20k_vexriscv
python scripts/mini.py build system --machine primer20k_vexriscv --purpose candidate
python scripts/mini.py build soc-description --machine mega138k_ae350
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize
```

## 参数到实际消费者

| 输入 | 消费者 |
| --- | --- |
| FPGA、晶振、引脚与电气约束 | LiteX 原生 GowinPlatform/ConstraintManager、PLL、CST/SDC |
| 机器时钟 | CRG、设备时基及所选硬件的软件导出 |
| 内存窗口 | 机器地址布局、总线解码、内存保护及链接输入 |
| CPU IP 特性与缓存/流水线 | VexRiscv 生成器、生成 RTL、软件 ISA/ABI |
| AE350 IP 特性 | SDK wrapper/加密子系统的配置宏及 TCM 输入 |
| DDR/PLL IP 属性 | 厂商 Tcl 属性接口和实际生成的 RTL/IPC |
| ROM、L2、功能组合 | SoC 存储器/设备实例、CSR 和软件编译 |
| GPIO/设备绑定 | 原生资源申请、极性/去抖/位分配和镜像 ABI |
| 层次、布局和布线 | LiteX Verilog 转换与 Gowin 实现选项 |

前端只解析一次源配置并传递同一快照。Board 不依赖机器 ABI；两台机器均通过
薄 `IOBindings` 转换到原生 LiteX 资源，由 ConstraintManager 负责申请和最终约束。
AE350 fabric、总线及 packed CSR 兼容访问器属于项目适配。
标准 CSR/内存/常量导出使用 LiteX 的生成器。

## IP 生成与依赖

`mini.py build cpu` 自动解析并生成 JSON 所需的 IP；没有独立的 `--generate` 分支。
硬件目标也先解析 IP，再捕获最终 RTL 输入。VexRiscv 参数由单核生成器消费。
AE350 SDK 当前没有可用的 Tcl IPFlow 实现，适配器消费 SDK 随附的模板并生成
配置宏和 TCM 文件。DDR/PLL 使用原生 `create_ipc`、`set_property`、
`report_property`、`generate_target`，JSON 使用属性的语义值；厂商 IPC 内部枚举
不是另一套用户参数。

Ninja 调度 IP 动作。参数、实际生成源码/SDK 模板和工具内容身份发生变化时重新生成；
缺失或损坏的输出也会失效。生成失败不会退回旧配置的 IP。既有 store 保存不可变
产物及依赖；工作区保存 Ninja、生成器和编译缓存。

重复构建始终复用同一个 `--workspace`，默认 `default`。matrix 逐例写入并校验源 JSON，
串行复用同一工作区，结束时恢复原文件；用例配置和结果保存在报告中。

软件构建使用选定硬件产物的 CSR、内存、ABI 和配置快照。为历史硬件重编软件时，
不导入当前源机器 JSON 重新推导硬件参数。归档重放保留其历史 checkout 与命令。
构建输出和候选不会自动成为 `current`，生成或编译通过也不表示 PnR/实板通过。

## 普通构建不归档源码

store 保留实际交付产物及增量元数据，不复制 inputs/source、Python 包、payload
或导出器源码。普通输出视图也不创建 source.patch/untracked 的重放副本。
需要源码归档时使用明确的 release/归档流程；普通构建不要求可重复构建。
