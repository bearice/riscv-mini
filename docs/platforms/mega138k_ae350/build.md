# AE350 构建与增量 PnR

138K/AE350 使用 `--machine mega138k_ae350`：`bootloader` 构建Flash固件区中的独立XIP启动镜像，初始化硬核 ILM/DLM 并进入 UART loader；`gateware` 集成原生 AHB 与 DDR3。`payload` 独立构建应用或内存诊断，`bios` 构建 [AE350 BIOS 与 GW-BASIC](runtime.md)。AE350 machine-description 只从仓库内的 `board.json`、`machine.json` 和构建实现生成；`ae350_customized_demo` 仅供开发时查阅，不由构建器打开、扫描或纳入输入闭包。Gowin IP 与 AE350 wrapper 的实际构建输入是仓库内固定并校验哈希的 vendor 文件。构建与板测使用准确产物身份，不消费 20K current。恢复核对所选硬件关联的启动镜像。位流不包含 bootrom 字节；首次使用前须通过独立安装操作写入 U4 官方 0x600000 固件分区，见 [bootloader](bootloader.md)。

## 138K/AE350 增量 PnR

隔离官方 Flash 时序时，可临时在机器 JSON 中设置 `rtl.timing_probe: true`，再用相同的 `mini.py build gateware --machine mega138k_ae350 --synthesize` 入口和原 workspace 构建。探测仅实例化 AE350、官方 Flash、FR APB 回接、PLL/复位和 UART/JTAG 引脚，不实例化 DDR、SD、音频或视频；不提供可运行的 bootloader 环境，板端入口拒绝该产物。结束后移除该字段，恢复完整系统；频率仅修改 `clocks.sys`。失败构建的完整时序报告也保存在各自 run 中，便于后续频率复验。

Gowin 增量 PnR 支持 Arora V/III，AE350 的 GW5AST-138 可用，20K 的 GW2A 不适用。AE350 `gateware --synthesize` 默认 `--pnr-mode full`。稳定工作区、对象缓存和成功产物复用默认生效；厂商增量 PnR 显式选择。`place` 只复用布局 `.p`；`pnr` 复用布局和布线 `.pr`，这两种模式要求已有兼容成功基线，否则在运行 Gowin 前报错。可选 `auto` 在同一命名工作区存在兼容成功实现时使用 `.pr`，否则全量实现。完整 `.pr` 的读取和匹配在实际工程上可能比全量实现慢，不能把它等同于必然加速。

```text
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --pnr-mode full
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --pnr-mode pnr
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --workspace experiment --pnr-mode place
```

基线从最近一次成功的不可变产物读取并校验；器件、工具身份、DDR 物理参数/几何和关键时钟必须兼容，setup/hold 及最小脉宽检查必须通过。种子复制到稳定工作区再通过显式 Tcl 路径传给 Gowin，不使用可能来自失败运行的临时 `.pr`。模式和基线身份/哈希记录在 lock 中，并将基线保留为依赖。相同源码请求先查成功结果缓存，不因成功基线更新而不断重建；实现输入索引可从 store manifest 重建。

IP 配方摘要变化单独记录；软件/CLI 输入造成的配方变化不等同于 DDR 物理配置变化，仍由 Gowin 对新综合网表检查布局匹配率。匹配率不足时工具可能改跑全量，回执记录实际模式。

增量失败保留失败日志，不静默退回全量。可显式选择 `--pnr-mode full` 重新实现；若完全相同的全量请求已有归档，则直接复用。`auto` 对时钟或工具不兼容的基线自动执行全量。每次实现仍做原有时序检查，不代表板测通过。只运行生成、不加 `--synthesize` 时不会归档工作区残留的旧 PnR 输出。

参考：[SUG100 第 42–43 页](https://cdn.gowinsemi.com.cn/SUG100E.pdf#page=54)、[SUG1220 第 44 页](https://cdn.gowinsemi.com.cn/SUG1220E.pdf#page=50)。
