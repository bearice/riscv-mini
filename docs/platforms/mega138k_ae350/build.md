# AE350 构建与增量 PnR

138K/AE350 使用 `--machine mega138k_ae350`：`bootloader` 构建厂商 ITCM 启动镜像，初始化硬核 ILM/DLM 并进入 UART loader；`gateware` 集成原生 AHB 与 DDR3。`payload` 独立构建应用或内存诊断。AE350 machine-description 只从仓库内的 `board.json`、`machine.json` 和构建实现生成；`ae350_customized_demo` 仅供开发时查阅，不由构建器打开、扫描或纳入输入闭包。Gowin IP 与 AE350 wrapper 的实际构建输入是仓库内固定并校验哈希的 vendor 文件。构建与板测使用准确产物身份，不消费 20K current。恢复使用所选硬件的启动镜像。

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
