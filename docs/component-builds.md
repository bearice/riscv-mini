# 组件构建与产物复用

使用 `python scripts/mini.py`；Windows 使用 `.venv/Scripts/python.exe`，Linux 在项目 Nix shell 中使用 Python。执行环境自动发现，无需指定 environment。

## 常用操作

```text
python scripts/mini.py configure --target bios
python scripts/mini.py plan bios --hardware current
python scripts/mini.py build bios --hardware current
python scripts/mini.py build system --hardware current
```

`build bios` 固定所选硬件，构建当前源码的 HAL SDK 和 BIOS，创建候选系统。CPU、SoC 描述、bootloader、位流从选定组合复用，不运行 Java 或 Gowin。`build` 和 `plan` 都必须明确指定目标。修改源码或版本只影响相关组件的输入闭包；不会因为全仓 HEAD 改变而强制重建所有组件。

`plan` 打印 REUSE/BUILD 及原因。首次使用旧 catalog/归档时会校验并导入 store，不移动或删除原目录，不改变 current。初次 HAL SDK 需要编译，因为旧系统中的 HAL 是静态链接进 BIOS 的，不能从旧 ELF 假装取得一份可复用库。

构建完成在控制台打印 `build/runs/<id>/result.json`。其中 `system.digest` 是完整组合身份，可用于后续命令；`build/refs/candidates/<run-id>.json` 也引用它。`current` 始终由明确的选择操作修改。

## 独立目标与硬件生成

```text
python scripts/mini.py build hal-sdk --hardware current
python scripts/mini.py build bootloader --hardware current
python scripts/mini.py build cpu --hardware current
python scripts/mini.py build soc-description --hardware current
python scripts/mini.py build gateware --hardware current
python scripts/mini.py build gateware --hardware current --synthesize
python scripts/mini.py build system --hardware current --refresh-hardware --synthesize
```

`cpu` 校验并复用所选的固定 RTL/生成元数据；重新生成 CPU 使用 `mini.py build cpu --generate`，其输出包括完整 generator.lock.json，重放时传 `--locked-config`。Java、sbt 和上游源码自动发现，参数可覆盖；lock 模式要求所有 generator flags 和源码/工具身份显式匹配。旧 CPU 缺完整生成源/工具记录时，只能校验固定产物，不宣称其生成闭包完整。

`soc-description` 从当前硬件源码生成 CSR、契约和 RTL；`gateware` 还独立构建 bootloader 并生成最终 RTL，只有 `--synthesize` 才运行 Gowin。ROM 内容是位流依赖，BIOS 内容不是。未综合的候选不能通过板端入口的 PnR 检查。

构建只有一个公开入口：`mini.py build <target>`，Windows 包装器是 `mini.ps1`。旧独立命令和阶段包装器已删除，不提供旧参数转发。所有目标由同一个 argparse 树和 target registry 注册，内部后端统一为 `build(request, run)`；准备、执行和打包由调度层完成，不能再用 `--prepare-only/--record/--pack-only` 分散调用。

```text
python scripts/mini.py build system --profile minimal
python scripts/mini.py build system --hardware current --l2-size 16384 --synthesize
python scripts/mini.py build system --profile minimal --feature video=on --source firmware/examples/monitor.c
python scripts/mini.py build cpu --generate --pipelined-fetch --arithmetic-matrix --output-dir build/work/cpu-generator
python scripts/mini.py build payload --soft-float
python scripts/mini.py build linux
python scripts/mini.py build uboot --hardware current --feature video=off
python scripts/mini.py build opensbi --hardware current --payload-path <u-boot.bin>
python scripts/mini.py build busybox
```

硬件覆盖参数只属于 system、soc-description 和 gateware；固件目标固定所选硬件。`--profile minimal` 不依赖现有 CPU 产物；full profile 需要通过 `--cpu-verilog` 明确选择有完整元数据的 CPU。没有 `--profile` 时，从 `--hardware` 选择完整配置，覆盖项明确记录。功能统一写成 `--feature NAME=on|off`。

目标包括 system、bios、bootloader、hal-sdk、cpu、soc-description、gateware、cpu-gcb、cpu-qualification、matrix、linux、busybox、uboot、opensbi、payload、doom、gwbasic、nyancat。每个目标都使用 `plan <target>` / `build <target>`、`--hardware`、`--output-dir` 和 `--purpose`。未指定输出目录时，核心组件进入 store，上游及应用后端使用受管理的 work；run 统一保存请求、计划/结果和日志。上游完整构建仍调用各项目的 Make/Kbuild/sbt，当前并不因此宣称所有上游产物已有核心组件同等的不可变 store/replay 能力。

`--output-dir` 可为系统创建含 validation/catalog 的输出视图，为独立组件导出其产物树，为上游或 payload 指定工作/输出目录。日常操作使用 run 中的 system 或 artifact 身份；目录名不承担兼容性判断。构建不会修改 current 或编程板子。

## 目录所有权

| 位置 | 内容 |
| --- | --- |
| `build/store/<component>/<digest>` | 不可变模块产物、完整输入/flags lock、源码快照、manifest |
| `build/store/system/<digest>/bundle.lock.json` | 组件引用和完整配置，不复制依赖文件 |
| `build/work/<component>/<key>` | Ninja、对象文件、depfile、临时输出；可增量或清理 |
| `build/runs/<run-id>` | kind、请求、计划、结果、日志和测试结果 |
| `build/index/build-keys` | 输入 key 到产物的可重建索引 |
| `build/refs` | 现役、基线、候选引用 |
| `build/releases` | 导出的自包含产物包 |

完成产物按内容清单计算 SHA256，文件或 manifest 被修改后复用/导出/恢复均拒绝。同 key 生成不同产物会保留冲突并失败，不覆盖旧结果。输入源码在构建期间变化也会拒绝发布。

当前同一仓库的模块操作使用会话锁，避免构建与清理互相破坏；目标内部 Ninja 并行编译。编译环境过滤隐含的 CFLAGS/GCC_EXEC_PREFIX 等变量，并记录实际工具和库。源码路径和 map 文件中不稳定的本机根目录作规范化处理，以支持不同 checkout 路径下的同平台重放。

构建身份头仅由显示身份的 main.c 消费；普通硬件功能头保持稳定。修改某个 BIOS 实现文件后，Ninja 可保留其他对象，仅重编该文件与身份显示文件，再链接和打包。

## 导出与复现

以下 `<system-digest>` 取自构建结果，`<package>` 为尚不存在的目标目录：

```text
python scripts/mini.py release export <system-digest> --output-dir <package>
python scripts/mini.py release verify <package>
python scripts/mini.py reproduce <package>
python scripts/mini.py reproduce <package> --rebuild
python scripts/mini.py build system --locked <package>/release.lock.json
```

包内 `release.lock.json` 固定完整组件闭包及逐文件校验和，`artifacts/` 保存全部依赖、参数和必要输入。默认 reproduce 是校验并恢复精确产物；`--rebuild` 从每个固件组件保存的源码和构建实现创建独立 checkout，自动绑定本机工具，校验锁定身份后重新编译 HAL/BIOS，并要求产物 digest 完全匹配。CPU/位流仍使用固定产物；不会把此操作称为重新综合或重新上板。

当前导出类型为 artifact-bundle，可用于开发组合归档和跨机器传递，不自动创建 Git tag、GitHub Release 或板测通过标记。旧导入产物中未记录的生成/厂商默认参数明确保留为 legacy 限制，不用当前默认值补造历史事实。软件/RTL 字节重放与不同 OS/Gowin 下的 PnR 复验分别记录。

## 兼容性和测试

```text
python scripts/mini.py compat --hardware <system-digest> --software <bios-manifest.json>
python scripts/mini.py test --system <system-digest> -- python tests/modular_build_test.py
python scripts/mini.py board run --build <system-digest> --dry-run
python scripts/mini.py board verify --build <system-digest> --suite firmware
```

版本号是发布标签，不参与兼容性判定。产物 digest 标识准确内容；build key 标识准确输入，决定缓存复用。兼容性是有方向的关系：provider 提供的接口是否满足 consumer 构建时保存的 requires。

| 依赖边 | 组合时的规则 |
| --- | --- |
| CPU → SoC 描述 | 必须是生成描述时锁定的 CPU digest；禁止按文件名或版本猜参数 |
| SoC 描述、ROM → gateware | 必须是生成位流时锁定的描述及 ROM digest；XIP gateware 不嵌入 bootloader |
| SoC → bootloader / HAL SDK / BIOS | 同平台、ABI major、C ABI、时钟；CPU ISA 覆盖 consumer 指令集；所需 CSR 地址/宽度/类型、内存、IRQ 相同；约定的后端和行为修订相同；必需能力齐全 |
| HAL SDK → BIOS | 必须是链接时的库 digest；当前为静态链接，没有可在运行时替换的共享库 ABI |
| bootloader → BIOS 镜像 | v1 镜像格式、范围和 CRC 合法，tag 与该 bootloader 构建时的 tag 相等；BIOS 记录的 bootloader 引用也必须一致 |

上述检查在 `contracts.assembly` 集中执行，覆盖系统组合、已存在系统的解析、导出/导入校验、locked build 和板端视图生成。检查结果写入 bundle lock 和 run。对没有 requires 的历史软件，只允许原来精确锁定的 SoC，不能据此宣称跨硬件兼容。

硬件 ABI major 和接口行为修订由 provider 在 `gateware/config.py` 定义。追加寄存器不改已有接口修订；改变访问语义、IRQ 分发或缓存一致性规则必须改对应修订，即使地址没变也会拒绝。配置同时记录 SD/USB 后端、音频时钟等已有语义约束。源码审查仍须维护这些声明，哈希无法自动证明硬件行为相同。

当前 `scope=configured-sdk-snapshot` 保守保存所选硬件的完整 CSR/内存/IRQ 快照，**尚未自动分析每个消费者最小访问集合**；不能把它描述为“实际使用的寄存器”。因此追加未在快照中的 CSR 可以通过，快照内接口变化会拒绝。`requirements` 支持显式声明子集，但现有 HAL/BIOS 默认没有缩小范围。ABI 相容用于检查不同产物的组合，不能冒充精确缓存命中。

构建契约 capabilities 的位号固定在 `contracts.CAPABILITIES`：0=mmu，1=fpu，2=flash，3=spi_lcd，4=sd，5=filesystem，6=video，7=board_io，8=ws2812，9=audio，10=mic，11=mic_stereo，12=eth，13=eth_dma，14=usb。`required_capability_flags` 必须全部提供，未知必需位拒绝；缺少的 `optional_capability_flags` 只返回诊断。`mandatory_flags` 属于契约格式扩展，目前所有位保留，非零拒绝。可选位不自动生成降级代码，消费者仍须实现相应行为。

BIOS → payload 另有既存运行时 ABI：`firmware/bios/include/bios.h` 定义 ILP32 M-mode ecall、magic、函数号、结构和 `BIOS_ABI_VERSION=1`；`payload.h` 校验载荷头/版本/地址范围/CRC。`bios_info.features` 的位号是 0=SD、1=video、2=USB、3=Ethernet、4=audio、5=INDEX8，与构建契约位号不同。payload、OpenSBI/U-Boot/Linux 的完整依赖尚未接入上述 store 组合检查；不能声称版本相同就已验证兼容。

现有 v1 镜像和旧 loader 的校验算法保持不变。主机契约通过不能让旧 loader 自动理解新格式，也不绕过其 ABI tag；新 wire format 与板端能力协商需按提案后续迁移并验证。本次构建管理迁移不扩大已安装 loader 的接受范围。

测试与板端操作统一写到 runs，kind 分别为 test/board。测试 run 关联准确的 system/产物身份。导出包可以用 `--test-run <run-id>` 加入已通过且针对同一组合的测试结果。新 BIOS 需新功能测试；复用同一位流可以继续引用对应的 PnR 结果。

板端入口按需在 `work/board-view/<system-digest>` 创建兼容旧工具的视图，原文件归 store 所有。SRAM/UART 试验使用正常 board 工作流；Flash 更新仍只在用户明确要求时执行。

## 清理

```text
python scripts/mini.py clean
python scripts/mini.py clean --execute
```

默认仅输出计划；执行时只移除受管理的 work/cache 子目录，验证绝对路径位于允许范围内，并用会话锁排除并发模块构建。store、runs、refs、release 和旧目录均保留。旧 runs 的清理仍使用原 catalog 清理流程，其保护规则不变。

## 2026-10-17 统一入口验收

128 项主机回归、Python 编译和 diff 检查通过。新增入口回归验证：build/plan 共用完整目标注册表；旧文件已删除；内部后端不含独立 CLI；旧参数和阶段开关被拒绝；上游一次命令完整执行准备、runner 和打包，plan 不执行工具或下载源码。

端到端结果：`build/runs/20261016T154609225204Z-single-build-entrypoint-131d39/result.json`。验证了 BIOS 构建及无改动全部 REUSE、最小系统 RTL/ROM/固件构建及 plan 全复用、普通 payload 和 GW-BASIC 编译、所有构建目标的帮助、上游 plan、release export/verify，以及从包内保存源码执行 reproduce --rebuild。隔离源码的 BIOS-only 验收在 `build/runs/20261016T154630262263Z-modular-reuse-f00232/result.json`，只编译变更文件及身份 main.c 两个对象，其余组件复用。

候选 system 为 `a85beb451b43fa4ce9af59df391fd6b419212d08b2ed132a218b4de96824130f`；最小系统为 `0132b59e30d169de5836a3a6b929a0f4d59fe55e4970e2dfb280d3916f32ddbf`。未访问板子、未执行新 PnR、未修改 current，未提交或创建正式代码 release。CPU 生成和上游 OS 的完整重编不在本轮真实执行范围内；它们的入口注册、plan/调度协议和现有功能回归通过，不等同于完成了一次全量 OS 构建或板测。
