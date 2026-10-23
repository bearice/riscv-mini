# 平台配置前端与 LiteX 集成边界

本项目以前端校验和机器适配补充 LiteX；生成、编译、PnR 与实板资格分别记录。

## 基础机制的所有权

| 能力 | 使用现有实现 | 项目补充范围 |
| --- | --- | --- |
| 引脚、IO 标准、连接器、资源申请 | LiteX `GenericPlatform` / `ConstraintManager` / `GowinPlatform` | 严格 JSON 到原生资源声明的薄转换、板卡保留引脚检查 |
| 总线、地址区域、CSR、IRQ | LiteX `SoC` / `SoCCore` handlers | AE350 原生 AHB/APB、硬核中断/DMA 及项目设备适配 |
| 标准硬件和软件导出 | LiteX CSR JSON、export helpers、Builder 生成目录 | 现有 ABI 所需的 packed CSR 别名、AE350/cache/DMA 等项目字段 |
| RTL、ROM 初始化、工具调用 | LiteX `Builder` / 原生平台 build | 项目启动流程、独立固件组件及厂商 IP 生成的缺失动作 |
| IP 生成 | 适用的现有 CPU/厂商生成器 | 当前单核 VexRiscv、Gowin IPC 参数映射与内容身份复用 |
| 输入归档 | 先使用/比较 LiteX `build_bundle` | 组件身份、依赖、current/baseline、实板证据等实际缺口 |
| 动作调度 | Ninja 与已有 action worker | 将现有动作写成依赖边，不建立第二个执行器或资源管理器 |

锁定版本：`8c01073afb71aa0a0709f02f8e24247589e8f5e4`。对应源码：
[GenericPlatform](https://github.com/enjoy-digital/litex/blob/8c01073afb71aa0a0709f02f8e24247589e8f5e4/litex/build/generic_platform.py)、
[Builder](https://github.com/enjoy-digital/litex/blob/8c01073afb71aa0a0709f02f8e24247589e8f5e4/litex/soc/integration/builder.py)、
[VexRiscvSMP](https://github.com/enjoy-digital/litex/blob/8c01073afb71aa0a0709f02f8e24247589e8f5e4/litex/soc/cores/cpu/vexriscv_smp/core.py)。

## 配置输入

两板使用同一个 Board schema，两机使用同一个 Machine schema。
Board JSON 保存完整物理资源、电气约束、FPGA 和晶振；Machine JSON 保存实际机器
选择及参数，包括各 vendor IP 的生成参数。禁止自由文本说明、推测或验收状态。

前端在一次解析中校验配置，并转换成 LiteX 参数、原生 IO 资源声明和 IP 生成请求。
类型化只读对象用于确保本次构建消费同一输入快照，不另外建立一套 SoC 对象模型。
SoC/CRG/设备构造期间不重新打开 JSON；生成结果和软件构建也不混用当前源配置与
历史硬件参数。配置字段须能追溯到实际参数、资源声明或明确的合法性约束。

```text
严格 JSON 前端
  ├─→ IP 参数 → 现有生成器/厂商 Tcl → 准确 IP 输入
  ├─→ LiteX 原生资源声明 → GowinPlatform / ConstraintManager
  └─→ 机器参数 → SoCCore 或 AE350 机器适配 → Builder / platform.build
                                                    ↓
                                      LiteX 导出 + 项目必要扩展
                                                    ↓
                                      选定硬件的 BSP/软件/证据
```

`IOBindings` 只翻译配置并提供现有 GPIO/ABI 所需的绑定投影。
不维护第二套资源占用、端口图或独立约束模型。引脚申请、连接器解析和最终约束由
LiteX 负责。

## 两台机器的一致性

一致的是输入边界、参数来源和已有机制的使用，不强制两台机器内部拥有相同类图。

20K 保留 `SoCCore` 及其 bus/CSR/IRQ handlers，通过 JSON 参数组装现有模块。
138K 保留确有必要的 AE350 原生 AHB/APB、DDR 和设备 fabric；适用处使用 LiteX
机制，不为使用某个 Builder 接口而伪造一套不存在的 LiteX CSR/CPU 总线。

Board 目录保存板卡数据。原 138K Board `io_map` 中的机器位分配、ABI 逻辑归位到
机器适配/导出，通用部分进入薄 JSON 转换。20K 与 138K 都消费 LiteX 原生资源。
AE350 原 `platform.py` 的实际职责是 fabric，应使用 `fabric.py` 名称，
与 LiteX 的物理 Platform 区分。

20K 的 Flash、Ethernet、USB、SPI LCD 和共享复位等绑定也由 JSON 提供，
不能留在上游平台 `_io` 或 SoC 的引脚字符串中成为另一套配置来源。

## 标准导出与项目扩展

以实际 elaboration 生成的 LiteX CSR JSON、内存区域、constants 和生成头文件为基础。
`HardwareDescription` 若保留为概念，仅表示这些现有输出及项目扩展，
不新建与 LiteX 并列的通用硬件描述 schema、地址管理器或 C 头文件生成器。

AE350 的原生 MMIO、IRQ 路由、cache/DMA 规则等 LiteX 输出未覆盖的内容可作为
明确命名的项目扩展。固定设备寄存器语义由设备实现拥有，实例地址/绑定由机器拥有。
已有 packed CSR 软件别名只补充标准导出无法表达的部分。

软件构建消费所选硬件产物的导出。为旧硬件重编软件时，不能通过 import 当前机器
配置重新推导 RAM 或时钟。PLL 的请求频率与实际输出频率分别保留在输入和导出中。

## IP 参数与自动生成

Machine JSON 是期望生成参数的唯一来源。VexRiscv 特性、缓存、流水线，以及
Gowin IPC 参数采用生成器专用的封闭 schema。它们被翻译成现有生成器参数或
厂商 Tcl；生成的 RTL/IPC、生成记录和哈希是输出。

VexRiscvSMP 的参数化生成机制可以参考，但当前机器是单核，不能直接替换成 SMP。
继续适配当前单核生成器。DDR/PLL 使用本机实际提供的 `create_ipc`、`set_property`、
`generate_target` 等入口。JSON 保存原生 Tcl 属性的语义值，不能把 IPC 内部的
枚举索引当成 Tcl 属性。设备专用 IPC 版本由厂商生成器决定。

本机 AE350 SDK 插件的 Tcl 创建/打开方法是返回失败的空实现。AE350 适配器因此
消费该 SDK 随附的公开 wrapper、加密子系统和 TCM 模板，按 JSON 重新生成
`riscv_ae350_config.v` 与 TCM 输入；它不读取项目中旧的已生成 vendor 配置。
SDK 模板及插件内容纳入生成身份，未映射的参数或不支持的 TCM/语言组合直接拒绝。

以参数、生成器/模板/上游源码、目标和工具内容身份判断复用。变化、缺失或损坏
触发对应 IP 重新生成；失败不继续消费旧参数产物。仅使真实下游依赖失效。
生成先暂存和校验，再发布并更新需入库的 vendor 记录，之后锁定最终 RTL 输入。

## Ninja 的边界

Python 配置前端把机器实例和 IP 引用展开成现有动作的输入输出。
Ninja 管理执行顺序、文件依赖、增量和并发池；现有 action worker 调用具体工具。
不在 Python 中再实现一套 Ninja 的循环检测、执行状态机或通用产物缓存。

项目仍需为每个动作投影其实际消费的参数与源码/工具身份，生成内容不变则不改写的
输入锁，补足时间戳不能表达的内容变化。IP 动作负责 IP 输出检查；既有 store 管理
项目组件完整性。Ninja 不替代这些语义，也不另立一套相同的产物目录体系。

## 构建与归档职责

组件 store 管理内容身份、依赖、current/baseline 与实板证据。LiteX 负责 RTL、标准导出与适用的工具调用；Ninja 调度动作。归档只收集明确请求的机器输入闭包，不遍历所有已导入 Python 包或另一台机器源码。

## 普通构建的留存边界

普通构建只保存后续阶段实际需要的产物，以及增量判断所需的输入哈希、参数、
工具身份和依赖。不得默认复制项目源码、Python 包源码、payload 源码或导出器源码，
也不得因指定 output-dir 而自动创建源码重放 recipe。

源码归档属于明确请求的 release/归档操作。旧的完整归档可以沿用其历史重放路径；
新的普通产物不承诺脱离当前源码树复现。缓存完整性与接口/镜像匹配仍然保留。
