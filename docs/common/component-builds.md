# 组件构建与产物复用

使用 `python scripts/mini.py`；Windows 使用 `.venv/Scripts/python.exe`，Linux 在项目 Nix shell 中使用 Python。执行环境自动发现，无需指定 environment。

## 常用操作

```sh
python scripts/mini.py plan gateware --machine <machine>
python scripts/mini.py build gateware --machine <machine>
python scripts/mini.py build gateware --machine <machine> --synthesize
```

`build` 和 `plan` 都必须明确指定目标。机器的可用目标和软件装载方式见平台页；[20K BIOS-only 构建](../platforms/primer20k_vexriscv/build.md) 与 [AE350 构建](../platforms/mega138k_ae350/build.md) 分别说明。修改源码只影响相关组件的输入闭包，不因全仓 HEAD 改变强制重建所有组件。`plan` 显示复用或重建及其原因。

构建完成在控制台打印 `build/runs/<id>/result.json`。其中 `system.digest` 是完整组合身份，可用于后续命令；`build/refs/candidates/<run-id>.json` 也引用它。`current` 始终由明确的选择操作修改。

## 独立目标与硬件生成

机器支持的目标、启动镜像与板端流程分别见 [20K](../platforms/primer20k_vexriscv/README.md) 和 [138K/AE350](../platforms/mega138k_ae350/README.md)。目标注册表不代表每台机器都支持全部目标。



`cpu` 根据机器 JSON 自动生成或复用 IP，生成身份包括参数、生成器/SDK 源码和工具内容；不再有独立 CPU 特性覆盖参数。

`soc-description` 从当前硬件源码生成 CSR、契约和 RTL；`gateware` 还独立构建 bootloader 并生成最终 RTL，只有 `--synthesize` 才运行 Gowin。嵌入位流的启动镜像是位流依赖；外部装载的应用内容不是。具体启动存储由机器定义。未综合的候选不能通过板端入口的 PnR 检查。

构建只有一个公开入口：`mini.py build <target>`，Windows 包装器是 `mini.ps1`。旧独立命令和阶段包装器已删除，不提供旧参数转发。所有目标由同一个 argparse 树和 target registry 注册，内部后端统一为 `build(request, run)`；准备、执行和打包由调度层完成，不能再用 `--prepare-only/--record/--pack-only` 分散调用。



硬件配置的唯一来源是所选机器的 `board.json` / `machine.json`，格式与消费者见 [平台构建配置](platform-descriptions.md)。直接编辑 JSON 后执行 build；CLI 不再提供硬件覆盖参数，plan 不修改配置。固件目标消费选定硬件的导出。

目标包括 system、bios、bootloader、hal-sdk、cpu、soc-description、gateware、cpu-gcb、cpu-qualification、matrix、linux、busybox、uboot、opensbi、payload、doom、gwbasic、nyancat。每个目标都使用 `plan <target>` / `build <target>`、`--hardware`、`--output-dir` 和 `--purpose`。未指定输出目录时，核心组件进入 store，上游及应用后端使用受管理的 work；run 统一保存请求、计划/结果和日志。上游完整构建仍调用各项目的 Make/Kbuild/sbt，当前并不因此宣称所有上游产物已有核心组件同等的不可变 store/replay 能力。

`--output-dir` 可为系统创建含 validation/catalog 的输出视图，为独立组件导出其产物树，为上游或 payload 指定工作/输出目录。日常操作使用 run 中的 system 或 artifact 身份；目录名不承担兼容性判断。构建不会修改 current 或编程板子。

日常构建、bootstrap 和构建验收不传 `--output-dir`，统一使用下表的受管理目录，不在 `build/` 顶层创建按任务命名的目录。实验使用 `--workspace <名称>` 选择工作缓存，使用 `--purpose <用途>` 标注收据；只有明确需要额外交付文件或兼容工具视图时才指定 `--output-dir`。

## 目录所有权

| 位置 | 内容 |
| --- | --- |
| `build/store/<component>/<digest>` | 不可变交付产物、增量所需哈希/参数/依赖、manifest（普通构建不保存源码） |
| `build/store/system/<digest>/bundle.lock.json` | 组件引用和完整配置，不复制依赖文件 |
| `build/work/<machine>/<target>/<workspace>` | 稳定工作区；默认 workspace 为 default，保留 Ninja、对象、depfile、厂商工具缓存 |
| `build/work/replay/<run-id>` | 明确要求冷编译的独立复现工作区 |
| `build/runs/<run-id>` | kind、请求、计划、结果、日志和测试结果 |
| `build/index/build-keys` | 输入 key 到产物的可重建索引 |
| `build/refs` | 现役、基线、候选引用 |
| `build/releases` | 导出的自包含产物包 |

完成产物按内容清单计算 SHA256，文件或 manifest 被修改后复用/导出/恢复均拒绝。同 key 生成不同产物会保留冲突并失败，不覆盖旧结果。输入源码在构建期间变化也会拒绝发布。

所有 machine 的构建输入哈希统一使用 `sha256-text-eol-lf-v1`：公共层按内容识别 UTF-8（可带 BOM）及带 BOM 的 UTF-16/32 文本，将 CRLF/CR 转为 LF；不改变其它空白、编码、BOM 或末尾换行。规则不依赖扩展名。包含二进制控制字符或无法按上述编码解码的输入保留原字节。普通构建只计算这些哈希，不复制源码快照。编译器/工具、下载压缩包、二进制产物及 store/导出包完整性清单仍使用原始字节 SHA256；已保存的历史锁与归档不改写。

IP 生成参数保存在机器 JSON；生成的 CPU/厂商 IP 通过不可变 store 依赖进入硬件构建。工作区保存生成缓存，SDK 模板、源码和工具身份参与失效判断。历史产物继续通过其保存的 checkout 和 recipe 重放。

**铁律：同一 machine/target 的重复构建必须使用相同 workspace。** 修改源码、调整参数、时序收敛、失败重试和复验都沿用原 `--workspace`；未指定时持续使用 `default`。严禁按尝试、阶段、时间戳、用途或参数组合另起 workspace，也不得为并行重试另开工作区。保留原工作区及编译器、Ninja、Gowin 缓存，用 `--purpose` 与自动 run 收据区分各次尝试。

每个稳定工作区持有 OS 文件锁，直到验证和成功归档完成；重复构建在同一工作区串行执行，目标内部 Ninja 仍并行编译。独立实验首次选择 workspace 后，后续重复构建必须沿用。工作区名只选择可变缓存位置，不替代输入摘要或产物身份。编译环境过滤隐含的 CFLAGS/GCC_EXEC_PREFIX 等变量，并记录实际工具和库。源码路径和 map 文件中不稳定的本机根目录作规范化处理，以支持不同 checkout 路径下的同平台重放。

`workspace.json` 记录 running/succeeded/failed、本次 run/input_key 以及最近一次成功产物。失败保留现场与上次成功引用，不发布新产物。成功结果仍复制进 `build/store`，清理工作区不影响已归档或固定版本。旧的按 key/run 划分的目录不自动迁移或删除。

硬件重新生成时去掉过期的生成源文件，保留厂商综合缓存；执行 PnR 前使旧位流和报告失效，再检查本次退出码、位流和时序。归档保留生成输入、位流、时序/资源报告及 `.p/.pr`，不再复制 `impl/gwsynthesis` 编译缓存。固件保留对象与 Ninja depfile；静态库重建时先删除旧库，避免已经移除的源文件仍残留在库中。

上游后端也使用相同的稳定目录和锁；成功后归档顶层交付文件到 `build/store/backend/<target>/...`，缓存子目录继续留在 work。它是输出快照，不宣称具有核心模块同等的源码闭包及 replay 能力。显式 `--output-dir` 仍选择其工作/输出位置。上游工具自身使用的外部缓存位置维持各后端现有约定。

构建身份头仅由显示身份的 main.c 消费；普通硬件功能头保持稳定。修改某个 BIOS 实现文件后，Ninja 可保留其他对象，仅重编该文件与身份显示文件，再链接和打包。

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

历史源码归档须在其保存的 checkout 与配方中重放，不用当前树的配置补写旧输入。

包内 `release.lock.json` 固定完整组件闭包及逐文件校验和，`artifacts/` 保存全部依赖、参数和必要输入。默认 reproduce 是校验并恢复精确产物；`--rebuild` 从每个固件组件保存的源码和构建实现创建独立 checkout，自动绑定本机工具，校验锁定身份后重新编译 HAL/BIOS，并要求产物 digest 完全匹配。CPU/位流仍使用固定产物；不会把此操作称为重新综合或重新上板。

当前导出类型为 artifact-bundle，可用于开发组合归档和跨机器传递，不自动创建 Git tag、GitHub Release 或板测通过标记。旧导入产物中未记录的生成/厂商默认参数明确保留为 legacy 限制，不用当前默认值补造历史事实。软件/RTL 字节重放与不同 OS/Gowin 下的 PnR 复验分别记录。

## 兼容性和测试

```text
python scripts/mini.py compat --hardware <system-digest> --software <software-manifest.json>
python scripts/mini.py test --system <system-digest> -- python tests/common/modular_build_test.py
```

版本号是发布标签，不参与兼容性判定。产物 digest 标识准确内容；build key 标识准确输入，决定缓存复用。兼容性是有方向的关系：provider 提供的接口是否满足 consumer 构建时保存的 requires。

组合检查在 `contracts.assembly` 集中执行，覆盖系统组合、已存在系统的解析、导出/导入校验、locked build 和板端视图生成。检查结果写入 bundle lock 和 run。对没有 requires 的历史软件，只允许原来精确锁定的 SoC，不能据此宣称跨硬件兼容。

各机器的具体契约分别见 [20K 构建契约与 BIOS ABI](../platforms/primer20k_vexriscv/contracts.md)、[AE350 地址合同](../platforms/mega138k_ae350/address-map.md) 和 [AE350 cache](../platforms/mega138k_ae350/cache.md)。

测试与板端操作统一写到 runs，kind 分别为 test/board。测试 run 关联准确的 system/产物身份。导出包可以用 `--test-run <run-id>` 加入已通过且针对同一组合的测试结果。修改后的软件需要对应功能测试；复用同一位流可以继续引用对应的 PnR 结果。

20K 板端入口使用的兼容旧工具视图见其操作页，原文件归 store 所有。SRAM/UART 试验使用正常 board 工作流；Flash 更新仍只在用户明确要求时执行。

## 清理

```text
python scripts/mini.py clean
python scripts/mini.py clean --execute
```

默认仅输出计划；执行时只移除受管理的 work/cache 子目录，验证绝对路径位于允许范围内，并用会话锁排除并发模块构建。store、runs、refs、release 和旧目录均保留。旧 runs 的清理仍使用原 catalog 清理流程，其保护规则不变。

正式 release 的可选记录工具 `prepare_commit.py --release --build` 也可接收内容寻址平台的成功板测 `result.json`。它核对实际 bitstream/payload、PnR、执行结果与当前源码闭包，不生成伪造的旧 catalog 身份。性能报告必须指向同一组 artifact。普通提交不要求这些材料，也不自动构建或生成提交快照。

`mini.py builds clean work` 先显示可重建工作缓存的清理计划，`--execute` 才执行。它保留 store、原始 runs/证据、归档、recipes、refs、vendor、已登记工作目录和 Git worktree；单个旧式中间 run 仍用 `builds clean <catalog-id>`。未知历史顶层目录不自动删除。
