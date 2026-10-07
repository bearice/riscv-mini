# 构建目录与产物身份

入口是本地 **build/README.md**，由 `scripts/builds.py refresh` 生成。Git 里的报告保存关键摘要和哈希，忽略的 build/ 保留完整 RTL、工具日志、镜像和测量。

```text
build/
  README.md             当前版本、性能基线、构建登记表
  legacy-index.md       旧实验/工具链目录分类
  runs/
    <UTC时间>-<Git版本[-dirty]>-<profile>-<ISA>-rom<N>k-l2<N>k-<用途>/
      build-info.json   构建开始捕获的源码/参数/CPU RTL hash
      validation.json  实际配置、ABI、镜像 hash、PnR 结果
      gateware/        完整 RTL、Gowin 工程与 bitstream
      firmware/        boot/app ELF、bin、img、map
  catalog/<ID>.json     身份、原路径、用途、状态、板测证据
  refs/current.json    明确选择的已验收实板版本
  refs/baseline.json   明确选择的性能基线
  reports/             本地完整检查/性能数据
  recipes/<run身份>/   参数、Git patch、未跟踪源码、CPU RTL输入
  releases/system/v<版本>/<配置>/
    release.json       complete/failed、版本、tag、完整Git hash、归档文件哈希
    build-parameters.json
    toolchain.json     编译器/工具hash、Python依赖版本
    firmware/          boot/app bin、img、ELF/map
    gateware/          最终bitstream
    reports/           PnR、板测、性能、提交记录
    replay/            自包含复现输入
  archives/v<版本>-<Git版本>/<配置>/  普通代码提交快照，不占用新发布版本
reports/
  changes/             改动说明与验证依据，不重复 Git 文件清单
  performance/         可提交的性能报告及摘要 JSON
```

新默认构建不再覆盖 build/base。显式 `--output-dir` 继续支持矩阵和旧命令，但重用其目录可能使旧 catalog 身份过期；查询时会校验产物 hash。可保留完整参数在 build-info.json，目录名只包含最重要的辨识项。

```powershell
# 每次独立创建 run，不编程板卡。
.venv/Scripts/python.exe scripts/build.py --purpose full-candidate --synthesize
.venv/Scripts/python.exe scripts/builds.py list
$Candidate = & .venv/Scripts/python.exe scripts/builds.py path latest-pnr
.venv/Scripts/python.exe scripts/firmware_verify.py --output-dir $Candidate --program --soak-seconds 60
# 只有匹配 hash 且 passed=true 的板测才能选为 current。
.venv/Scripts/python.exe scripts/builds.py pin current <完整ID> --evidence "$Candidate/firmware-verification.json"
$Current = & .venv/Scripts/python.exe scripts/builds.py path current
$Baseline = & .venv/Scripts/python.exe scripts/builds.py path baseline
```

三种概念不同：`latest-generated` 是新流程最新完成生成/编译的构建（可含 PnR 失败）；`latest-pnr` 是新流程最新 setup/hold 0/0 的构建；`current` 是明确 pin 的实板验收版本。它们分别回答“最新做了什么”“最新可下载候选是什么”“当前应该用哪个已验收版本”。`baseline` 单独 pin，生成报告时必须再次显式指定。

历史构建通过 register 登记，不改名、不移动原目录、不重写 validation.json。Gowin 工程、CPU RTL 路径和日志中有绝对路径，直接搬动会破坏复现。historical-association 的 commit 来自人确认的版本关联，不表示脚本捕获过该源码状态；不能拿它验收新的硬件改动。历史目录登记时间不参与 latest-* 选择。

```powershell
.venv/Scripts/python.exe scripts/builds.py register --path build/cache-boot/release `
  --purpose baseline-noc-rom8k --commit f0892da --label 'C改动前非C/8KiB基线'
.venv/Scripts/python.exe scripts/builds.py refresh
.venv/Scripts/python.exe scripts/builds.py show baseline
```

性能数据放到独立的 build/reports/performance/<日期>-<双方ID>/all、cache 子目录。每次使用新目录，避免覆盖旧测量。使用 `benchmark.py --build-dir $Current` 保存双镜像身份；该参数关联操作者选择的已加载版本，并不对 FPGA 做 bitstream 读回。

```powershell
.venv/Scripts/python.exe scripts/benchmark.py --build-dir $Current --suite all --rounds 3 `
  --output-dir build/reports/performance/<日期>-<候选ID>/all
.venv/Scripts/python.exe scripts/benchmark.py --build-dir $Current --suite cache --rounds 3 `
  --output-dir build/reports/performance/<日期>-<候选ID>/cache
.venv/Scripts/python.exe scripts/performance_report.py --candidate current --baseline baseline `
  --candidate-results <候选all目录> --candidate-results <候选cache目录> `
  --baseline-results <基线all目录> --baseline-results <基线cache目录> `
  --output reports/performance/<候选版本>-vs-<基线版本>.md
```

## 最终归档与中间目录清理

代码变更先提交。系统包发布的 RELEASES.yaml 条目、system/v<版本> tag 和 HEAD 必须一致，release.py 输出 releases/system/v<版本>/<配置>/。不发布新系统包的代码提交使用 --snapshot，输出 archives/v<版本>-<Git hash>/<配置>/；文档提交只保存范围记录。两种最终产物均保留二进制、报告、参数和复现输入，不可覆盖；每个文件有 SHA256，release.json 只有完整生成后才标 complete。组件版本与系统包见 [版本追溯](version-history.md)。

```powershell
.venv/Scripts/python.exe scripts/builds.py clean <中间构建ID>          # 先列出清理目标
.venv/Scripts/python.exe scripts/builds.py clean <中间构建ID> --execute
.venv/Scripts/python.exe scripts/builds.py reproduce <中间构建ID> --output-dir build/runs/<新的复现目录>
```

clean 只删除已登记、未被 refs 固定、位于 build/runs 的中间目录，保留 recipes 和 catalog。recipes 保存 Git hash、VERSIONS.yaml、dirty patch、未跟踪代码、参数及 CPU RTL/元数据，复现命令会在独立 detached checkout 中恢复源码后重新构建。旧未知实验、工具链、正式发布、提交快照和固定版本不在自动清理范围。复现需相同工具/依赖版本；工具路径由当前机器 .tools.local.json 配置。

脚本不会推断正确基线，不会把空传输/失败用例算成吞吐，也不会将登记构建自动升级为板测 PASS。工具链/历史实验的物理迁移属于另一次可复现性工作：先盘点绝对路径、更新调用方、验证迁移后生成和编程，再清理原路径。
