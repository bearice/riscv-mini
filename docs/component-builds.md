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

138K/AE350 使用 `--machine mega138k_ae350`：`bootloader` 构建厂商 ITCM 启动镜像，初始化硬核 ILM/DLM 并进入 UART loader；`gateware` 集成原生 AHB 与 DDR3。`payload` 独立构建应用或内存诊断。AE350 machine-description 只从仓库内的 `board.json`、`machine.json` 和构建实现生成；`ae350_customized_demo` 仅供开发时查阅，不由构建器打开、扫描或纳入输入闭包。Gowin IP 与 AE350 wrapper 的实际构建输入是仓库内固定并校验哈希的 vendor 文件。构建与板测使用准确产物身份，不消费 20K current。恢复在当前平台执行，不再构建或切换到已退役的 P1 ROM/SRAM 镜像。

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
python scripts/mini.py build system --profile minimal --feature video=on --source firmware/examples/primer20k_vexriscv/monitor.c
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

日常构建、bootstrap 和构建验收不传 `--output-dir`，统一使用下表的受管理目录，不在 `build/` 顶层创建按任务命名的目录。实验使用 `--workspace <名称>` 选择工作缓存，使用 `--purpose <用途>` 标注收据；只有明确需要额外交付文件或兼容工具视图时才指定 `--output-dir`。

## 目录所有权

| 位置 | 内容 |
| --- | --- |
| `build/store/<component>/<digest>` | 不可变模块产物、完整输入/flags lock、源码快照、manifest |
| `build/store/system/<digest>/bundle.lock.json` | 组件引用和完整配置，不复制依赖文件 |
| `build/work/<machine>/<target>/<workspace>` | 稳定工作区；默认 workspace 为 default，保留 Ninja、对象、depfile、厂商工具缓存 |
| `build/work/replay/<run-id>` | 明确要求冷编译的独立复现工作区 |
| `build/runs/<run-id>` | kind、请求、计划、结果、日志和测试结果 |
| `build/index/build-keys` | 输入 key 到产物的可重建索引 |
| `build/refs` | 现役、基线、候选引用 |
| `build/releases` | 导出的自包含产物包 |

完成产物按内容清单计算 SHA256，文件或 manifest 被修改后复用/导出/恢复均拒绝。同 key 生成不同产物会保留冲突并失败，不覆盖旧结果。输入源码在构建期间变化也会拒绝发布。

所有 machine 的构建输入哈希统一使用 `sha256-text-eol-lf-v1`：公共层按内容识别 UTF-8（可带 BOM）及带 BOM 的 UTF-16/32 文本，将 CRLF/CR 转为 LF；不改变其它空白、编码、BOM 或末尾换行。规则不依赖扩展名。包含二进制控制字符或无法按上述编码解码的输入保留原字节。源码快照同步归一化，使纯 EOL 变化对应相同的输入 key 和快照。编译器/工具、下载压缩包、二进制产物及 store/导出包完整性清单仍使用原始字节 SHA256；已保存的历史锁与归档不改写。

硬件出厂物（厂商加密 IP 与代码生成的 CPU 核）不放在 `build/` 里当“输入”，而是入库到 `gateware/vendor/<vendor>/` 并附参数与来源记录：`generator.json`/`PROVENANCE.json` 记录生成命令、固定参数、生成器与上游文件哈希、工具链身份，同一目录保存许可证。声明式闭包必须列出这些文件，构建前校验哈希；文件缺失或被改动即报错，并按记录重新生成入库（`mini.py build cpu --generate --vendor-dir gateware/vendor/vexriscv`）。参数调整后的新出厂物随代码提交，历史产物只通过 `builds reproduce` 复现。

**铁律：同一 machine/target 的重复构建必须使用相同 workspace。** 修改源码、调整参数、时序收敛、失败重试和复验都沿用原 `--workspace`；未指定时持续使用 `default`。严禁按尝试、阶段、时间戳、用途或参数组合另起 workspace，也不得为并行重试另开工作区。保留原工作区及编译器、Ninja、Gowin 缓存，用 `--purpose` 与自动 run 收据区分各次尝试。

每个稳定工作区持有 OS 文件锁，直到验证和成功归档完成；重复构建在同一工作区串行执行，目标内部 Ninja 仍并行编译。独立实验首次选择 workspace 后，后续重复构建必须沿用。工作区名只选择可变缓存位置，不替代输入摘要或产物身份。编译环境过滤隐含的 CFLAGS/GCC_EXEC_PREFIX 等变量，并记录实际工具和库。源码路径和 map 文件中不稳定的本机根目录作规范化处理，以支持不同 checkout 路径下的同平台重放。

`workspace.json` 记录 running/succeeded/failed、本次 run/input_key 以及最近一次成功产物。失败保留现场与上次成功引用，不发布新产物。成功结果仍复制进 `build/store`，清理工作区不影响已归档或固定版本。旧的按 key/run 划分的目录不自动迁移或删除。

硬件重新生成时去掉过期的生成源文件，保留厂商综合缓存；执行 PnR 前使旧位流和报告失效，再检查本次退出码、位流和时序。归档保留生成输入、位流、时序/资源报告及 `.p/.pr`，不再复制 `impl/gwsynthesis` 编译缓存。固件保留对象与 Ninja depfile；静态库重建时先删除旧库，避免已经移除的源文件仍残留在库中。

上游后端也使用相同的稳定目录和锁；成功后归档顶层交付文件到 `build/store/backend/<target>/...`，缓存子目录继续留在 work。它是输出快照，不宣称具有核心模块同等的源码闭包及 replay 能力。显式 `--output-dir` 仍选择其工作/输出位置。上游工具自身使用的外部缓存位置维持各后端现有约定。

构建身份头仅由显示身份的 main.c 消费；普通硬件功能头保持稳定。修改某个 BIOS 实现文件后，Ninja 可保留其他对象，仅重编该文件与身份显示文件，再链接和打包。

## 138K/AE350 增量 PnR

Gowin 增量 PnR 支持 Arora V/III，AE350 的 GW5AST-138 可用，20K 的 GW2A 不适用。AE350 `gateware --synthesize` 默认 `--pnr-mode full`。稳定工作区、对象缓存和成功产物复用默认生效；厂商增量 PnR 显式选择。`place` 只复用布局 `.p`；`pnr` 复用布局和布线 `.pr`，这两种模式要求已有兼容成功基线，否则在运行 Gowin 前报错。可选 `auto` 在同一命名工作区存在兼容成功实现时使用 `.pr`，否则全量实现。完整 `.pr` 的读取和匹配在实际工程上可能比全量实现慢，不能把它等同于必然加速。

```text
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --pnr-mode full
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --pnr-mode pnr
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --workspace experiment --pnr-mode place
```

基线从最近一次成功的不可变产物读取并校验；器件、工具身份、DDR 配置和关键时钟必须兼容，setup/hold 及最小脉宽检查必须通过。种子复制到稳定工作区再通过显式 Tcl 路径传给 Gowin，不使用可能来自失败运行的临时 `.pr`。模式和基线身份/哈希记录在 lock 中，并将基线保留为依赖。相同源码请求先查成功结果缓存，不因成功基线更新而不断重建；实现输入索引可从 store manifest 重建。

增量失败保留失败日志，不静默退回全量。可显式选择 `--pnr-mode full` 重新实现；若完全相同的全量请求已有归档，则直接复用。`auto` 对时钟或工具不兼容的基线自动执行全量。每次实现仍做原有时序检查，不代表板测通过。只运行生成、不加 `--synthesize` 时不会归档工作区残留的旧 PnR 输出。

参考：[SUG100 第 42–43 页](https://cdn.gowinsemi.com.cn/SUG100E.pdf#page=54)、[SUG1220 第 44 页](https://cdn.gowinsemi.com.cn/SUG1220E.pdf#page=50)。

## Gowin 许可证连接重试

20K、AE350 综合构建及 CPU qualification（包括 synthesis-only）遇到明确的 license server 连接故障时自动重试：总计最多 4 次尝试，等待间隔为 5、15、30 秒。识别 `License verification failed` 附近的 `Connection timeout`、`Server not responding`、无法连接许可证服务器等信息；空日志、许可证过期/无效、语法、布局布线和时序错误不会触发这种重试。即便工具错误地以退出码 0 报告许可证连接失败，也不会当作成功。

重试继续使用原工作区及其综合缓存，每次尝试先使旧位流和报告失效。AE350 收据保留 `gowin-attempt-N.log` 与 `gowin-attempts.json`，20K 对应 `gateware-attempt-N.log` 与 `gateware-attempts.json`；其中记录退出码、故障证据及等待时间。`gowin.log`/`gateware.log` 保留最后一次尝试。达到上限后构建失败，不发布新产物；成功后仍执行原有位流与时序验收。等待期间取消会停止后续重试。

## 导出与复现

以下 `<system-digest>` 取自构建结果，`<package>` 为尚不存在的目标目录：

```text
python scripts/mini.py release export <system-digest> --output-dir <package>
python scripts/mini.py release verify <package>
python scripts/mini.py reproduce <package>
python scripts/mini.py reproduce <package> --rebuild
python scripts/mini.py build system --locked <package>/release.lock.json
```

目录重组（2026-10-21 的多平台搬迁）之后，重组前导出的发布包不再能直接 `build --locked`：包里的 `inputs/source` 记录的是搬迁前的脚本路径，新树在 source 校验阶段会报 `Source differs from lock`，这是预期的单向兼容边界。历史复现一律用 `mini.py builds reproduce`（在保留 checkout 里自回放），不要为旧包在树上保留第二套路径。

包内 `release.lock.json` 固定完整组件闭包及逐文件校验和，`artifacts/` 保存全部依赖、参数和必要输入。默认 reproduce 是校验并恢复精确产物；`--rebuild` 从每个固件组件保存的源码和构建实现创建独立 checkout，自动绑定本机工具，校验锁定身份后重新编译 HAL/BIOS，并要求产物 digest 完全匹配。CPU/位流仍使用固定产物；不会把此操作称为重新综合或重新上板。

当前导出类型为 artifact-bundle，可用于开发组合归档和跨机器传递，不自动创建 Git tag、GitHub Release 或板测通过标记。旧导入产物中未记录的生成/厂商默认参数明确保留为 legacy 限制，不用当前默认值补造历史事实。软件/RTL 字节重放与不同 OS/Gowin 下的 PnR 复验分别记录。

## 兼容性和测试

```text
python scripts/mini.py compat --hardware <system-digest> --software <bios-manifest.json>
python scripts/mini.py test --system <system-digest> -- python tests/common/modular_build_test.py
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

硬件 ABI major 和接口行为修订由 provider 在 `gateware/machines/primer20k_vexriscv/config.py` 定义。追加寄存器不改已有接口修订；改变访问语义、IRQ 分发或缓存一致性规则必须改对应修订，即使地址没变也会拒绝。配置同时记录 SD/USB 后端、音频时钟等已有语义约束。源码审查仍须维护这些声明，哈希无法自动证明硬件行为相同。

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


正式 release 的可选记录工具 `prepare_commit.py --release --build` 也可接收内容寻址平台的成功板测 `result.json`。它核对实际 bitstream/payload、PnR、执行结果与当前源码闭包，不生成伪造的旧 catalog 身份。性能报告必须指向同一组 artifact。普通提交不要求这些材料，也不自动构建或生成提交快照。

`mini.py builds clean work` 先显示可重建工作缓存的清理计划，`--execute` 才执行。它保留 store、原始 runs/证据、归档、recipes、refs、vendor、已登记工作目录和 Git worktree；单个旧式中间 run 仍用 `builds clean <catalog-id>`。未知历史顶层目录不自动删除。
