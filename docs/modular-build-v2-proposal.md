# 模块化构建、接口契约与可复现发布提案

日期：2026-10-16。状态：用户已批准，按阶段实施；可用命令与当前迁移边界见 [组件构建操作](component-builds.md)。

本轮调查开始于 `84ff17c`，结束时工作树 HEAD 为 `1b7cc00`；结束时复核了新接入的 Nix 环境。以下区分当前源码、历史归档和拟议行为。未运行完整构建、PnR、板测或 Flash 更新。

## 1. 目标与结论

保留单仓库，按实际依赖组织独立构建目标和版本。Python 负责配置解析、输入锁定、契约检查、产物登记；Ninja 负责构建依赖调度；Jinja 按需生成文本；上游工程继续使用自身 Make/Kbuild/sbt。

Release 定义为不可变的构建组合：源码引用、完整展开的构建 flags、依赖产物、工具链与执行环境、输出校验和、验证记录。代码 tag 只标识其中的源码。仅有 tag 或六个组件版本号，不足以发布一个可复现系统。

需要解决用户明确提出的六类问题：跨机器生成了不同参数的 CPU；新增 CSR 不破坏旧软件却改变整体 ABI tag；构建组件捆绑；build 目录混乱；hostcc/Java/WSL 隐含；release 没有完整表达所有构建 flags。

## 2. 当前证据

| 范围 | 已有机制 | 缺口与影响 |
| --- | --- | --- |
| 版本 | `VERSIONS.yaml` 独立维护 rtl/bootloader/hal/bios/opensbi/uboot，系统包在 `RELEASES.yaml` 固定组合 | `mini.py release create` 仍从同一 HEAD 构建并要求当前版本等于组合；Linux、rootfs、SDK 和应用未纳入统一组件登记 |
| 构建 | `mini.py` 已提供操作入口；`build.py` 支持功能选择、CSR/RTL 生成与综合 | 主流程一次生成硬件描述，编译 boot/app，再生成最终 gateware；没有独立、统一的组件输入输出契约 |
| CPU | `mini.py build cpu --generate` 单独生成 RTL，并记录参数和输出 SHA；消费方会在存在匹配元数据时验证 SHA | 生成参数由 CLI/脚本分支组合；相同文件名不代表相同 CPU，消费方也没有强制完整、可重放的生成 lock |
| ABI | `boot_image.py::abi_tag()` 覆盖寄存器、内存、IRQ、功能等；LCD wrap 有精确兼容例外 | 整体指纹混合接口、能力和配置；新增不使用的 CSR 也改变 tag；布局相同不能证明 DMA/fence 等行为一致 |
| 载荷 | BIOS ecall ABI v1、features bits、RPB1/OSB1、静态 Newlib 输入记录 | RPB1/OSB1 头没有 required ISA/服务集；通用 payload 默认硬浮点，与当前归档的无 FPU 硬件不匹配；镜像格式版本与 BIOS ABI 宏有耦合 |
| 目录 | runs/catalog/refs/recipes/archives/releases/operations 已存在，部分清理有保护 | 本地 `build/` 一级目录共 109 个，含大量专项输出；新旧入口遵循不同目录和元数据约定 |
| 环境 | requirements lock、本机工具路径 JSON、doctor；新 flake 提供 JDK8、固定 sbt、xPack GCC、uv | WSL 发行版、用户目录、hostcc、LLVM、目标工具和 libgcc 选择仍散落在脚本；doctor 检查不覆盖完整目标依赖闭包 |
| 发布 | 最终包保存 build-parameters、replay、CPU 输入和部分工具信息 | Git 中的 release 声明不足以直接取得完整输入；本地忽略目录不能作为跨机器分发机制；验证范围存在由 external 补集推断的代码 |

当前 `build/refs/current.json` 指向 `ed50260` 的 ROM 启动归档：RV32IMA/ILP32、L2 8 KiB，组件 rtl 0.10.0、bootloader 0.9.1、hal 0.10.0、bios 0.11.0、opensbi 0.10.0、uboot 0.4.0。它与 HEAD 代码版本、系统包 0.10.0 是不同身份。这里核对的是归档和引用，未读取当前板上运行状态。

当前 CPU 归档记录 `single_cycle_shift=true`、`single_cycle_muldiv=true`、`two_cycle_icache=true`、`injector_register=true`、`relaxed_pc_calculation=false`。RTL SHA256 为 `9f35e57b1cb182bb43d4d8a9ff408d09a290186d5df02a86d484af5a8fbe5d9d`。`s1m1` 文件名未表达 fetch pipeline，重新运行生成器缺少 `--pipelined-fetch` 会选到不同配置。

对当前归档 CSR 的只读计算：

| 变化 | 旧 tag | 新 tag |
| --- | --- | --- |
| 只切换 filesystem 常量 | 28890b7e | e47fe6d4 |
| 只追加一个未使用的 RW CSR | 28890b7e | bdad1355 |
| 只把 L2 容量改为 16 KiB | 28890b7e | ee1891d2 |

这些计算证明 hash 对上述差异敏感，不证明对应软件一定兼容。是否兼容还需要判断调用方实际依赖的地址、初始化、内存所有权和行为。

## 3. 独立身份与版本

每个组件产物分别记录：

- `version`：对人沟通的组件版本，沿用命名空间 tag。
- `source`：完整 commit、组件源码闭包 digest、必要补丁和上游 source lock。
- `build_key`：规范化配置、输入闭包、工具链、执行环境及构建实现的摘要，用于重建和复用。
- `artifact_digest`：实际产物包的 SHA256；同一 build key 的不同输出不能互相覆盖。
- `provides/requires`：提供或要求的接口契约及能力。
- `test_runs`：针对具体产物与组合的测试 run 引用，不属于版本号或 ABI；测试结果和日志统一存放在 runs 中。

源码、物理 RTL、ABI 和验收使用不同标识。CPU pipeline 改动可以保持 ISA 接口相同，但必须产生新的构建身份，并重新做所需时序/硬件验证。软件 ABI 兼容也不等于原有性能或板测结论可继承。

组件版本继续放在单仓库内。独立版本不要求立即拆 Git 仓库。系统 lock 可以引用不同提交构建的不可变组件产物；需要重建时，在对应提交 checkout 中执行其固定版本的构建实现，不能把多个提交的目录临时拼到当前源码里。

HAL 保持静态链接库：先明确源码和构建依赖，再提供可复用 SDK；HAL 版本不代表板上存在可独立升级的 HAL 服务。

## 4. 构建目标与依赖

| 目标 | 关键输入 | 独立输出 |
| --- | --- | --- |
| cpu | 完整 CPU 配置、生成器及补丁、上游源码、Java/sbt 依赖 lock | RTL、元数据、生成 lock、校验和 |
| soc-description | 板型、CPU 产物、外设/地址/IRQ/时钟配置 | CSR、内存图、硬件契约、生成 SDK、可追溯硬件描述 |
| bootloader | soc 契约、DDR 初始化接口、boot mode/layout、编译工具链 | ELF/BIN、镜像协议能力与依赖 manifest |
| gateware | soc-description、完整 RTL/IP/约束/init、Gowin 参数；ROM 模式增加 bootloader 内容依赖 | FS、PnR 报告、部署元数据 |
| hal-sdk | 所选硬件契约、驱动、目标 ISA/C ABI、libc 策略 | 公共头、生成头、库或固定源码清单、链接布局 |
| bios | HAL SDK、BIOS 源码、runtime、链接配置 | ELF/BIN/IMG、提供的 BIOS 服务契约 |
| payload/<name> | BIOS SDK、所需服务、目标 ISA/ABI、资源和 libc | RPB、资源 manifest、运行需求 |
| opensbi / uboot | 各自 source/config/toolchain lock、目标平台描述 | ELF/BIN、DTB、启动接口 manifest |
| linux / rootfs | kernel/rootfs 配置、目标契约、UAPI 或运行依赖 | Image、initrd、元数据 |
| boot-bundle / system | 已锁定的组件产物和兼容性检查结果 | OSB、系统组合 lock、发布包 |

ROM 模式下 bootloader 内容实际嵌进 gateware，修改它必须重做 gateware。XIP 模式下 bootloader 内容可独立更新，但 reset vector、Flash 映射、保留容量和交接协议仍是硬件依赖。

先生成 soc-description/SDK，再编译 bootloader，最后生成含 ROM 的 gateware，可避免循环依赖。bootloader/BIOS 横幅不能要求预先知道尚未生成的最终 bitstream SHA；显示自身 build ID 和目标契约，最终组合身份由 bundle 和操作记录保存。

统一 CLI 作为用户入口，底层目标可独立选择。以下为拟议命令，当前尚未实现：

```text
mini configure --profile dock-default
mini build cpu --locked <cpu.lock.json>
mini build bios --hardware <artifact-id>
mini build payload/doom --sdk <artifact-id>
mini build system --locked <system.lock.json>
mini build plan --locked <system.lock.json>
mini compat check --hardware <artifact-id> --software <artifact-id>
mini release verify --lock <release.lock.json>
mini reproduce --lock <release.lock.json>
```

执行环境默认自动探测，日常命令无需传入 `--environment`；手动指定只作为可选覆盖。`plan` 输出每个目标的输入、自动选择的执行环境及选择依据、复用状态和重建原因。独立目标只检查自己所需的工具：使用已发布 CPU RTL 编 BIOS 不应强制要求本机 Java。

### 4.1 跨 build 的模块复用

一次 build/run 是请求和结果记录，不拥有独占的一套组件副本。所有 run 通过不可变 artifact 引用共享 store；新 run 可以组合旧 gateware、旧 bootloader、旧 SDK 和新 BIOS。

复用分两层：

1. 组件产物复用：根据 build key 找到已完成 artifact，校验其 manifest、输入和文件 digest 后直接引用，整个组件不再执行。
2. 组件内部增量：组件 key 已变化、不能整包命中时，在隔离且加写锁的 work 中复用仍有效的对象文件；Ninja 按源文件、头文件、命令和工具依赖重编受影响部分，再链接和打包。若改动的是所有对象都依赖的头或编译 flags，仍可能需要重编全部对象。

每个目标的 key 只覆盖自己的完整输入闭包：

```text
build_key(target) = SHA256(canonical(
  target kind + rule/schema version,
  target source files and imported build implementation,
  resolved flags and generated inputs,
  exact dependency artifact/input digests,
  tools and execution-environment requirements used by this target
))
```

复用判断不采用整个仓库 HEAD、整个 VERSIONS.yaml、全局 source hash 或本次 run ID。完整 commit 保留在 provenance；组件自己的版本/commit 字符串若嵌入二进制，仍是该组件真实输入。BIOS 版本增加不应因为公共生成头带着全部版本号而使 bootloader 失效；版本头、硬件头和组合身份必须分离。修改 Java 不应使只消费固定 CPU RTL 的 BIOS 失效；修改 C 编译器则会影响真正使用它的目标。

初期依赖闭包可保守地包含组件所属源码子树及共享构建实现，宁可多重建，也不能漏依赖；后续才细化共享 Python 模块和动态生成输入的依赖发现。不能只 hash 一条 CLI 或一个顶层脚本。

复用流程：解析目标及依赖 → 计算逐目标 key → 查本地已完成产物 → 校验 digest → 命中即引用，未命中才执行并原子发布到 store。失败或尚未完成的工作区不作为可复用产物；并行相同 key 的构建协调写入。相同 key 若产生不同输出必须保留并报告冲突，不能覆盖旧产物。跨机器首先支持按 release lock 导入/下载精确 artifact；远程 cache 可后续接入，但同样逐文件校验并遵守受信来源规则。

只修改 BIOS 实现、其他输入和目标配置不变时：

| 目标 | 处理 |
| --- | --- |
| CPU、soc-description | key 不变，直接引用原产物，不运行 Java/硬件生成 |
| bootloader | 输入闭包不含 BIOS 实现，直接复用 |
| gateware | CPU/硬件/约束/PnR flags/ROM 内容不变，直接复用已有 FS 与对应 PnR 证据，不运行 Gowin |
| hal-sdk / BIOS SDK | 公共接口、生成头和库均未变时复用；实现和公开服务描述分别判定 |
| BIOS | 新 key；增量编译受影响对象、链接，生成新的 app.img 与 manifest |
| 已有 payload/OpenSBI/U-Boot/Linux/rootfs | 所消费的 SDK/契约/输入均未变时复用；运行兼容性另行检查 |
| system bundle | 新建 lock，引用旧 artifact digests 与新 BIOS digest；发布包按需要自包含导出 |

BIOS 是 DDR 应用，不能因为它与 bootloader 同在旧 build.py 流程中就把二者判定为物理绑定。ROM 模式中 gateware 依赖 bootloader 的真实内容，不依赖 BIOS 内容。若 BIOS 改动实际连带共享头、SDK、硬件需求或 bootloader，则按真实依赖传播重建，不能承诺“文件路径在 BIOS 下就必然只重编 BIOS”。

更新现役配置时，拟议命令 `mini build bios --hardware current` 固定原硬件契约并构建当前 BIOS，自动解析配套 SDK；要求无法由该硬件满足时立即报错，不悄悄升级 gateware。另行生成候选 bundle 将旧硬件与新 BIOS 组合；构建不自动改变 current。

SDK 依赖使用它实际导出的头、链接布局和库的 digest，不依赖提供方完整 BIOS 二进制 digest。仅改 BIOS 内部命令实现，不使所有 payload 重新构建。公开 SDK 改变后，按其影响重新计算消费方 key；已存在旧 payload 可以保留原产物，经兼容检查和必要运行验证后组合使用。

ABI 相容只允许组装候选，不能制造精确构建命中。相同 ABI 的另一份 HAL 实现不能冒充原静态链接库；消费者重新链接后才是使用新 HAL 的新产物。

验证也按对象复用：同一 FS 的 PnR 报告仍有效，已有硬件专项证据可引用原结论和范围；新 BIOS 的主机/功能回归及“旧硬件 + 新 BIOS”组合测试需要新记录。新 bundle 不继承旧 BIOS 的通过状态，也不为证明软件变更而机械重跑同一 bitstream 的 PnR。

`plan` 应给出类似结果：`cpu REUSE`、`gateware REUSE`、`hal-sdk REUSE`、`bios BUILD (console.c changed)`、`system REASSEMBLE (BIOS digest changed)`，并显示每项引用的 digest 和重建原因。

## 5. 工具选择

| 选择 | 适合用途 | 本提案选择 |
| --- | --- | --- |
| Python 顺序执行 subprocess | 配置解析、平台适配、语义检查、专有工具封装 | 保留这些职责；不继续自行扩展完整调度器 |
| GNU Make 顶层 | 文件目标、依赖、成熟工具生态 | 可实现，但本项目已有复杂 Python 语义层；手写 Make 会再维护一套配置和跨平台转义 |
| Python 生成 Ninja | 显式 DAG、增量、并行、命令变化追踪 | 推荐为顶层执行方式，按目标逐步迁移 |
| Jinja | 稳定模板生成 DTS/Tcl/链接脚本/长配置文本 | 按需引入，不能代替依赖调度或输入 lock |

Ninja 官方定义是由更高层构建系统生成其输入；它支持多输出、命令变化追踪、depfile、资源池和 response file。选择它主要是为了清楚表达依赖，而不是承诺缩短 PnR 时间。[Ninja 手册](https://ninja-build.org/manual.html)

执行约定：

- Python 生成确定性的 action 描述和 Ninja DAG。跨 Windows/WSL 的动作由显式 runner 启动，runner 接收结构化 argv/env/cwd，避免在 Ninja/Jinja 中层叠拼 shell。
- C 编译按对象文件建目标，使用 depfile；可复用的增量工作目录按配置/工具链隔离。run 是一次调用记录，不要求每次从空目录编所有对象。
- 所有导入的 Python 构建实现、模板、生成头、linker script、上游补丁、RTL/IP/SDC/init 文件都必须进入相关 action 的依赖闭包；不能只 hash 顶层 Verilog。
- 内容相同的生成文件不改 mtime，配合 restat；时间戳只是调度提示，复用和发布仍校验 digest。
- 为 Gowin/Java/WSL 配资源池，限制上游 make 的 jobs，避免外层和内层各自全核并行。
- Gowin 等长任务保存实时日志和进度；Ninja 默认缓冲输出，进度呈现需由 runner/console pool 设计，不能牺牲可观察性。
- Jinja 使用 StrictUndefined。模板只排版已校验的数据；默认值、依赖和兼容判断留在 Python。JSON 用序列化器，Ninja 用专用 writer，不用 Jinja 模拟转义规则。

参考：[GNU Make 规则](https://www.gnu.org/software/make/manual/html_node/Rules.html)、[Jinja 简介](https://jinja.palletsprojects.com/en/stable/intro/)、[StrictUndefined](https://jinja.palletsprojects.com/en/stable/api/#jinja2.StrictUndefined)。

## 6. 自动探测环境与显式工具链记录

分成三个层次：

1. Git 中的工具链要求与环境选择规则：工具角色、版本或包来源、目标三元组、支持的执行平台和约束；用户无需逐次选择环境 profile。
2. 自动探测得到的本机 bindings：安装目录、可用执行器、WSL 发行版实例、Linux 工作根、Gowin 许可位置；允许手动覆盖。沿用本机配置不入 Git 的原则。
3. 每次构建生成的 resolved environment lock：真正使用的可执行文件、版本、指纹、库、环境变量和 runner 身份；随 release 发布。

必须逐项描述的角色：Python 与 Python 包、Ninja、host CC/CXX/AR/LD、目标 CC/binutils、LLVM/LLD、Java、sbt launcher 与 JVM/Scala 依赖、dtc、make、Gowin 综合/PnR/器件库、打包工具，以及对应执行环境。

尤其区分：`host.cc` 编译在构建环境运行的工具；`target.cc` 编译 RV32 程序。组件需要 hostcc 时必须声明，并向上游传递明确的 HOSTCC/HOSTCXX 等变量；不额外引入无关的宿主 C 单测。Windows 构建中的 host 工具若在 WSL 运行，host 就是该 Linux 环境，而非 Windows。

Linux 原生执行器和 Windows→WSL 执行器实现相同动作接口。系统自动识别当前 OS/架构、已有 Nix/uv 环境和可用工具；原生环境满足目标要求时直接使用。Windows 下目标需要 Linux 工具时，自动发现并检查可用 WSL 发行版，按稳定的选择规则选用满足要求的实例。多个可用实例优先沿用仍满足要求的本机绑定，并在 plan 中报告选择；只有无法自动解决的歧义才要求覆盖配置。WSL 不是所有平台的必需依赖。

选定后自动记录发行版、架构、shell、文件系统/工作根和工具集；不能在脚本中写死 archlinux、用户 HOME 或 libgcc 16.2.0 路径。显式化针对构建系统的依赖声明与解析结果，不要求用户手工填写全部环境信息。

沿用现有 flake/flake.lock 实现 Linux 环境，沿用 uv 管 Python；不改系统级 NixOS 配置。flake 声明与工具链要求要有一致性检查，不能长期维护两套互相矛盾的版本。现有 shell 提供了部分工具，不等于已经锁住上游 WSL 工作树或所有 host 工具。

配置阶段自动探测、选择并校验目标闭包，打印完整工具解析表：版本、target triple、RV32 multilib、实际 libc/libm/libgcc、ELF 格式、Java/sbt 和所需 shell/runner。普通构建直接使用通过校验的探测结果，生成 resolved environment lock。正式 locked build 同样自动定位本机工具和执行器，但必须满足已有 lock 的工具与环境约束；本机路径或 WSL 实例名可以不同。缺失或版本不符即失败，不能静默从 PATH 选另一个版本，也不能因本机探测结果不同而重写 release lock。

只传递声明过的环境变量，记录 PATH/JAVA_HOME/编译相关 flags 等对输出有影响的取值。许可密钥不写入 lock，只记录满足许可条件和工具版本。锁住工具不等于锁住整套系统；未固定的系统依赖必须明确记录为复现限制。

不同平台工具 binary hash 可以不同。输入复现、软件/RTL 字节复现、PnR 功能及时序复验分别报告；不承诺 Windows/Linux 生成相同 FS。

## 7. CPU 输入锁定

CPU lock 至少包含：上游完整 commit 与 dirty patch、pythondata 模板内容、生成器源码及补丁摘要、完整展开的 generator 参数、Java/sbt/Scala/Spinal 依赖、环境与输出 SHA。

把 MMU/FPU/C/B、I/D cache 大小、singleCycleShift/MulDiv、twoCycleCache、injectorStage、relaxedPcCalculation、reset/trap/MMIO 配置分别保存。参数缺失报错，不把缺失解释成 false，不根据文件名或生成器当前默认值重建。

发布 CPU artifact 自带 `.v`、必要辅助文件与 lock；固定产物消费不需要重新运行 Java。要求重新生成时，严格使用对应生成器和 lock。

字节 SHA 与“逻辑可能等价”分开：生成器注释中的 Git hash、换行也会影响 SHA。先保留原始 bytes 校验；需要稳定输出时明确固定或剥离非语义 provenance，并让该规则本身版本化。不得无说明地修改注释来伪装命中旧产物。

跨机器缺失 lock、参数不完整或输出不匹配时，在 SoC/PnR 前停止并输出字段级差异。CPU 配置正确和 PnR 参数正确都需要检查；place0/route2 不能被当作 place2/route2 的精确复现。

## 8. ABI、能力与兼容 flags

拆开四层接口：镜像封装协议、硬件平台/设备接口、BIOS 服务接口、编译器调用约定/运行环境。RPB 格式版本不再使用 BIOS ecall ABI 版本的宏来判定。

硬件端提供：platform ABI major、memory/IRQ/设备布局、设备契约 major/minor、capabilities、行为约定。软件声明自身实际依赖的契约、required/optional capabilities、ISA、特权模式和必要的运行状态（例如 FPU 状态初始化）。

基础兼容条件：要求的 ABI major 匹配；提供方满足要求的能力和契约；所有实际使用的地址/位宽/语义符合要求；ISA 与内存布局可用。需要特定修订版时声明单独的命名约束，不能把版本号比较当作全部判据。

flags 分成两个域：

- 功能能力：提供方的 provides、消费方的 required 与 optional；required 必须满足，optional 缺失必须走已实现且测试过的回退。
- 镜像解释要求：mandatory format flags；加载器不理解的必需 flags 必须拒绝。未知可选扩展只有在格式明确允许忽略时才能跳过。

能力定义使用固定数值 ID/命名注册表，不复用旧 bit，不用一个无边界的全局布尔集合描述所有层次。CPU ISA、硬件 video 与 BIOS video 服务分别登记；有硬件不等于某份 BIOS 已提供相应服务。

| 变更 | 判断规则 |
| --- | --- |
| 新增末尾、复位禁用的可选 CSR | 老地址/位域/复位与行为不变时保持旧契约；新增 capability，新软件可要求它 |
| 新 CSR 挤动旧 offset，或旧寄存器语义改变 | 破坏所涉旧接口，升级契约 major；旧软件缺适配则拒绝 |
| 相同 CSR、改变 DMA 完成或 fence 语义 | 属于行为契约变化，不能因为 hash 相同而放行 |
| 切换 CPU pipeline 或 L2 容量 | 构建身份改变；是否 ABI 变化按 boot RAM、缓存维护和行为契约判断，不能一律宣布无影响 |
| 关闭 FatFs | 不自动改变硬件 ABI；影响 BIOS/SDK 文件服务能力，要求 FS 的软件应被拒绝或使用回退 |
| 新 BIOS 服务 | 保持旧调用号/结构语义，追加能力；旧软件可继续用，新软件声明需求 |
| 新应用需要 F 扩展 | 目标 ISA 与 FPU 运行环境均需满足；ilp32/ilp32f 另行记录 |

当前 LCD wrap 的精确例外保留为 legacy 规则，后续追加设备契约，避免在 hash 函数里继续堆特判。内容 hash 用于身份与快速一致性判断；兼容性由契约检查与验证支持。

新增版本化能力查询，带调用方 buffer size；保留旧 BIOS_INFO 的原有大小和写入规则，不能扩展结构后向旧程序缓冲区写更多字节。FS、INDEX8、audio 等能力明确登记。未知调用与设备/文件错误的错误码语义需要版本化迁移，不能静默改变 ABI v1 既有 -1 行为。

主机打包和运行前均检查需求，板端在执行入口跳转前复核嵌入镜像的关键约束。仅存在主机 sidecar 不足以保护 SD/TFTP 独立装载。

## 9. 镜像和旧系统迁移

现有 app.img v1 flags/reserved 要求全零，不能直接借用这些字段。新契约使用显式 v2 header/扩展格式，并为 RPB/OSB 分别规定能力和交接要求。

先冻结现有 v1 hash 算法及已发布镜像行为；增加主机侧 contracts/lock/解释工具，再发布能识别新格式和旧格式的 loader/BIOS。旧 loader 继续只接收其支持的 v1，不能假定新 header 可在旧 Flash loader 上运行。

向下构建 v1 镜像只允许目标约束确实可表达的情况，不能重写 tag 把不兼容镜像伪装成兼容。旧格式缺需求信息时标记 legacy/unknown，不能默认所有新服务均可用。

ROM loader 升级会连带 gateware；XIP loader 升级可独立于位流内容，但要满足其平台契约。迁移的板端试验先用 SRAM/UART；Flash 更新仍按用户明确授权执行。

## 10. 所有构建 flags 进入 release lock

tracked profile 负责描述意图；resolved lock 负责固定事实。生成 release lock 时必须把所有默认值、推导值、preset 展开为显式配置，保留原始 invocation 只为审计，不能用它代替 resolved flags。

lock 必须覆盖整个依赖图：

- CPU 的完整 generator/plugin 参数。
- SoC 板型、器件/封装/速度等级、时钟、boot mode、地址图、ROM/L2、所有外设、后端与 RTL 层级。
- 综合与 PnR：全部实际设置，包括 place/route、hierarchy、retiming/pipeline、timing-driven、约束、seed（工具提供时）与相关工程选项。厂商隐含默认要保存实际生成工程/报告，并固定工具版本；未知项不能声称已完整锁定。
- 每个软件目标的 march/mabi、优化/LTO、defines、includes、编译/汇编/链接 flags、链接脚本、libc/multilib、实际配置文件。
- Java/JVM/sbt、host tools、目标工具、runner、工作目录语义、影响输出的环境变量、全部 source 与 artifact digests。
- packaging：镜像格式、装载/入口地址、Flash 分区与组合，及本次运行/部署选择。

执行计划与 lock 分开保存机器路径。release lock 用逻辑引用和内容地址；本机 resolved bindings 记录其实际路径，解包到另一目录也能解析相同输入。

软件中的可见版本字符串/commit 若参与编译，也属于真实输入：不能一方面在所有组件横幅里嵌入全仓 HEAD，一方面宣称 BIOS 改动绝不重编 bootloader。默认使用组件自身身份，整仓构建/发布身份放在外部 provenance。

每个 release 包含 lock、组件 manifests、checksum、CPU/必要输入、工具解析报告、生成工程及验证证据。远程 release 必须实际附带输入或有可取回且校验固定的地址，不能引用发布者电脑上的 build 路径。离线复现所需的上游下载/依赖也必须打包或声明不支持离线。

release verifier 从空目录校验完整闭包，缺 flag、缺输入或 checksum 不符即失败。修改任一 resolved flag 都产生新的构建身份，禁止继续使用同一不可变 release 覆盖旧产物。

## 11. 目录与产物所有权

```text
components/                  # 组件描述及构建依赖，逐步接入现有源码目录
configs/profiles/            # 用户可读的系统配置
configs/toolchains/          # 工具角色与版本要求
configs/environments/        # linux-native / windows-wsl 等执行环境
contracts/                   # 设备/BIOS/镜像契约与 capability 注册
releases/<system-version>/   # Git 内 release 描述与可移植 lock
build/
  work/<config-key>/         # 可增量工作区；串行锁定写入
  runs/<run-id>/             # 构建、测试或板端操作的请求、日志和结果
  store/<component>/<digest>/# 完成后只读的组件产物及 manifest
  cache/                    # 可重新获取/计算的下载及编译缓存
  releases/<system-version>/# 导出的系统包
  refs/                     # current / baseline / 候选引用
  index/                    # 可从产物 manifest 重建的本地检索索引
```

`work` 的配置 key 隔离工具链与 flags；对象文件还由 DAG 的输入/命令依赖管理。`store` 使用实际产物包 digest，build key 到产物的映射单独记录，以容纳非字节确定性的输出并检测冲突。

### 11.1 两次构建的具体布局

以下短 ID 仅用于说明，实际路径使用完整 digest。`run-001` 构建原系统，`run-002` 只改 BIOS；两次都引用同一套硬件产物。

```text
build/
  store/
    cpu/<cpu-A>/                 # manifest.json、outputs/CPU.v、生成 lock
    soc-description/<soc-A>/     # manifest.json、CSR、内存图、硬件契约
    bootloader/<boot-A>/         # manifest.json、outputs/boot.elf、boot.bin
    gateware/<gw-A>/             # manifest.json、outputs/system.fs、PnR 报告
    hal-sdk/<sdk-A>/             # manifest.json、include/、lib/、链接布局
    bios/
      <bios-A>/                 # 原 BIOS：manifest.json、outputs/app.elf/bin/img
      <bios-B>/                 # 新 BIOS：相同结构，保留原产物
    system/
      <system-A>/bundle.lock.json  # 引用 cpu-A/soc-A/boot-A/gw-A/sdk-A/bios-A
      <system-B>/bundle.lock.json  # 引用同一组依赖，仅 BIOS 改为 bios-B
  work/
    bios/<workspace-key>/       # build.ninja、obj/、depfiles、临时链接输出
  runs/
    <run-001>/                  # kind=build；请求、计划、结果、日志
    <run-002>/                  # kind=build；REUSE/BUILD 结果与 system-B 引用
    <hardware-check>/           # kind=test；原 gw-A 的硬件测试结果
    <bios-B-check>/             # kind=test；新组合的 BIOS/功能测试结果
  refs/
    current.json               # 原 system-A；构建不自动修改现役
    candidates/<run-002>.json   # 新 system-B，使用唯一名称避免并行覆盖
  index/
    build-keys/                # component + build key → artifact digest
  releases/
    <version>/                 # 发布时才导出自包含包
  cache/                       # 下载缓存等，可重新获取的内容
```

每个组件 `manifest.json` 声明组件类型/版本、构建 key、输出文件与 checksum、依赖引用和契约；构建请求、探测到的本机路径和执行日志在 run 中。artifact digest 按版本化的规范化内容清单计算，排除 digest 字段本身，避免自引用；不得把目录打包时间等偶然值作为内容身份。

系统 bundle 本身也是 store 内的不可变产物，lock 固定各组件 digest 和完整构建配置；依赖文件不复制到每个 bundle。run 的 result 引用 bundle/组件，`current`/候选 ref 通过 JSON 引用 system digest，不依赖 Windows symlink/junction。实际文件由解析器按 store 布局定位，索引仅加速查询，不能作为唯一事实来源。

构建、测试和板端操作统一使用 run，以 `kind` 区分。测试 run 保存测试对象的组件/组合 digest、测试范围、环境、结果和日志；发布记录引用相关测试 run，导出发布包时一并收录所需结果。无需另设 evidence 目录或独立的证据存储机制。

未完成或失败的编译只保留在 work/run，不发布成成功 artifact。开发中重复构建可继续使用受锁保护的增量 work；清空 work 后仍能从源码和 lock 重建。所有完成产物发布前先在暂存区校验，再原子移入 store。

第二次构建完成后，新增 bios-B、system-B、run-002 及其相关记录；CPU/SoC/bootloader/gateware/SDK 不增加副本。正式发布才在 releases 中展开完整依赖闭包，打包文件和 lock 均自包含；本地开发目录则通过引用共享。

### 11.2 入口、历史兼容与清理

每个脚本通过 workspace/runner 接口申请工作区和 run，禁止自行创造 build/doom-final2 等输出目录。Linux/WSL ext4 工作区是同一逻辑结构在该执行器上的映射，不能用未登记的 HOME 全局目录共享可变输出。

现有 archives/catalog/operations/recipes 先保持原地，通过 legacy adapter 读入新索引；验证 checksum 后逐步导入 store，不批量移动或删除历史目录。`current`/`baseline` 改为指向明确 bundle；兼容层继续解析旧 catalog ID。

清理按照 refs、release lock 和保留策略做可达性检查，先输出计划。work/cache 可清理；被现役、基线或正式 release 引用的产物、必要复现输入及测试 run 不可被普通 clean 删除。新测试产生新的 run，通过外部发布/选择记录引用，不能为补板测修改已发布 artifact manifest。

## 12. 发布与验证范围

system lock 同时固定组件版本、build key 和 artifact digest。允许不包含 Linux/rootfs 的 BIOS 系统，也允许 Linux 系统组合；组件集合由具体 bundle 描述，不能硬编码必须有六项。

明确区分 shipped、declared-compatible、contract-checked、tested。验收必须关联参与组合的全部产物 digest 和测试环境；不能由 `all components - external` 得出 verified，也不能自动把旧位流上的 BIOS 板测转移到新位流。

现有 host/RTL/PnR/board/performance 记录分别保留；新组合需要相关回归。输入相同并不保证物理 PnR 相同，更不等于屏幕观感或物理断电验收。默认不额外加入冷启动测试。

## 13. 迁移顺序与验收

1. 输入与环境：导出现役 CPU/SoC/toolchain 全量 lock；实现环境自动探测及只读 plan/doctor；在另一台机器自动定位工具并比对锁定输入，禁止 fallback 默认值。复用现有 flake 与工具配置。
2. 构建目标：拆出 soc-description、bootloader、bios、gateware；接 Ninja，保持旧 CLI adapter。验证 BIOS 改动不启动 Gowin，CPU/约束改变必重建相应目标。
3. 目录：新构建全部进入受管理结构；旧目录只索引，先保护 current/baseline/release，再设计导入和清理。
4. 契约：上线主机兼容检查、设备 capability 注册及正反向 fixture；再迁移镜像/BIOS 能力查询，不先放宽旧 loader 检查。
5. 发布：用完整 lock 组合组件产物，从空目录/另一台机器重放；下载发布包即可获得必要输入，发布范围从真实证据推导。

| 用户 case | 必须通过的验收 |
| --- | --- |
| CPU 跨机器参数错误 | lock 缺 injectorStage 等字段时失败；正确配置可追溯；输出不同能解释注释/工具/逻辑差异；错误输入在 PnR 前拒绝 |
| 新增 CSR 实际兼容 | 保持旧字段及行为、只追加可选 CSR 的 fixture 对旧消费者通过；旧字段移动或行为破坏必须失败；新消费者缺所需 capability 失败 |
| 组件捆绑 | 跨 run 只改 BIOS 时，CPU/SDK/bootloader/FS 直接命中已完成产物，不启动 Java、硬件生成或 Gowin；新 bundle 引用旧 digest 和新 BIOS；全仓 commit/BIOS 版本变化不污染其他目标 key；ROM bootloader 变化会重做 gateware |
| build 目录混乱 | 新目标无自定义顶层输出；并行构建不共享可变工作区；清理计划无法删除现役/基线/发布依赖；旧引用仍可用 |
| 隐含环境 | 不传 environment 即能自动选用满足要求的原生/WSL 环境，并记录选择依据；缺 hostcc/Java/所需 Linux 执行器或版本不匹配时，在目标开始前报告；纯 BIOS 固定输入构建不无故要求 Java；locked build 自动定位但不改变发布约束 |
| release 只有代码 tag | 从发布包和 lock 得到所有展开 flags；更改默认值不影响旧 release；缺闭包输入拒绝；跨机器输出/时序验证按层次记录 |

回滚采用旧构建/目录 adapter 和已保存的现役 bundle。新体系先产候选，不自动改变 current，不重标旧 release。主机契约与构建管理先上线，板端 wire format 按第 9 节单独迁移和验证。
