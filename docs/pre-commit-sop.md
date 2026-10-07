# 提交与代码发布 SOP

每次提交保存真实范围和适用检查。只有产品代码发布更新 CHANGELOG.md 和发布版本；范围记录 `reports/changes/` 只在代码变更时生成，文档、报告与构建/提交管理设施的改动不生成范围记录，也不创建 changelog 条目。代码变更先提交，再生成最终产物。发布包按版本号组织，提交快照按版本号加 Git hash 组织。

## 1. 确认范围与是否发布

读取 git status、diff 和新增文件，明确本次行为变化。暂存实现、必要测试和文档。git diff --cached --check 必须通过；每个暂存文件都需审阅。

产品代码发布阅读 [版本追溯](version-history.md)。版本按组件独立递增：只改 `VERSIONS.yaml` 里真正改动的那个组件（`rtl`/`bootloader`/`hal`/`bios`/`opensbi`/`uboot`），未改的组件不动。新功能/架构变化递增该组件的 MINOR，产品修复递增 PATCH；文档/流程提交不递增。CHANGELOG.md 用 `## <组件> v<版本>` 分节，只描述该组件该版的功能、修复和验收结果，不收录提流水、文档整理或流程脚本变化。

## 2. 选择候选与性能基线

按 [构建目录流程](build-artifacts.md) 创建新 run，记录 purpose、全部参数、Git hash、dirty 状态和源码输入指纹。dirty 候选保留 patch、未跟踪代码和 CPU RTL，不能仅用 HEAD 作为身份。历史登记标 historical-association，不把登记时间当构建时间。

基线由用户指定或与前一已验收功能版本关联。本次 v0.7.0 基线是 f0892da 的无 C/8 KiB ROM 共享 writeback 系统（追溯版本 0.6.0）；更早的 ff84bfb write-through 不替代它。双方时钟、工作量和身份必须一致可查。补测旧产物后恢复当前版本。

## 3. 完成适用验证

| 范围 | 验证要求 |
| --- | --- |
| 文档、提交/构建管理脚本 | 相应主机回归、CLI smoke、范围/报告校验；历史性能只作关联 |
| CPU/L2/DDR/总线/外设 gateware | 针对性 RTL/DFI 回归、实际配置 PnR setup/hold 0/0、匹配镜像实板检查、受影响并发及性能 |
| firmware/HAL/编译参数（bootloader、BIOS、驱动等软件） | 编译、ABI/CRC、相关主机回归与功能实板测试；RTL 不变时跳过全部硬件相关测试 |

RTL（gateware）不变时，先前硬件验收仍然成立：纯软件改动（bootloader、BIOS、驱动、HAL、编译参数）跳过所有硬件相关测试——不重跑 PnR/时序、不要求 captured board-qualified 构建、不做性能基准，只做主机回归与功能实板验证。只有 RTL 改变才重新做硬件验收（captured board-qualified 构建 + PnR + 性能基线），因为 RTL 影响时序与资源。

记录测试命令、源码输入指纹和实际报告路径。PnR 与实板验收分开，板测必须同时匹配 FS 和 app image hash。新增硬件/固件不能继承旧板测。失败、未跑项和物理观察限制保留。默认 storage benchmark 只读，常规程序只写 FPGA SRAM/UART。

all/cache 各三轮，benchmark --build-dir 保存加载身份，在新目录测量避免覆盖。performance_report.py 显式指定双方结果；不同工作量/时钟、失败或零字节不计算降幅。旧无身份测量仅在有下载/UART 证据时显式 --allow-legacy-results。保存 Markdown/JSON、原始数据 hash、样本/范围/中位数、LCD/音频状态和限制；结束后 BIOS/音频恢复检查。

## 4. 保存提交范围；仅代码变更生成范围记录

纯文档、报告整理与流程变更不生成 `reports/changes/` 记录，直接按第 1、3 节确认范围并完成适用检查。代码变更生成范围记录：

```powershell
.venv/Scripts/python.exe tests/workflow_test.py --report build/reports/checks/workflow.json
.venv/Scripts/python.exe scripts/prepare_commit.py `
  --title '本次行为变化' --change '人工审阅的具体变化和原因' `
  --checks build/reports/checks/workflow.json --output reports/changes/pending.md
```

报告只保存人工审阅的改动说明、验证结果、性能依据和必要版本引用，不重复 Git 的文件清单或 diffstat。最终 Git index 的 patch 指纹只保存在本地 build/reports/commit-checks，--verify 用它检测暂存内容是否变化；本地记录缺失时重新 prepare。CHANGELOG 和 reports/changes 自身排除以免自引用。检查输入指纹必须匹配。RTL（gateware）提交额外使用新 captured/board-qualified 构建及性能报告；纯软件（firmware/bootloader/BIOS/驱动）提交在 RTL 不变时跳过这些硬件验收，只保留主机回归与功能实板检查。

仅产品代码发布额外传 --component <组件> --release-version <版本> --write-changelog，change 只写该组件该版的发布变化；普通提交不传这些选项。`<组件>` 是六个组件之一或 `system`（系统包）。脚本会校验版本与 `VERSIONS.yaml`（组件）或 `RELEASES.yaml`（system）一致。历史功能版本补记可使用 --revision <产品代码提交>，明确是事后记录，不伪造当时的检查。完整提交范围直接查 Git，不再复制到报告或 changelog。

## 5. 复核并提交；发布创建版本 tag

```powershell
git add <明确文件；代码变更同时加入范围记录>
git diff --cached --check
.venv/Scripts/python.exe scripts/prepare_commit.py --title verified --change verified `
  --output reports/changes/pending.md --verify
git diff --cached --stat
git commit
git status --short
git tag -a bios/v0.7.2 -m 'riscv-mini bios 0.7.2'  # 组件发布；system 包用 system/v<版本>
```

普通提交不创建发布 tag。组件发布顺序：bump `VERSIONS.yaml` 里那个组件并在 `CHANGELOG.md` 写 `## <组件> v<版本>` 条目，与功能代码同一个提交；提交得到 git hash 后，从该提交运行构建（干净工作树，产物身份不带 `.dirty`）并完成实板测试；再归档范围记录（`reports/changes/<commit7>.md` 与同名 `.json`）、打 `<组件>/v<版本>` tag，tag 指向最终干净提交。系统包发布另在 `RELEASES.yaml` 钉住组件组合，打 `system/v<版本>` tag，然后进入第 6 节生成整 SoC 最终包。不可覆盖已发布版本。实现或普通报告改动后重新验证并生成范围。此流程不自动推送。

## 6. 提交后生成并校验最终产物

```powershell
.venv/Scripts/python.exe scripts/release.py --version 0.7.2 --build current `
  --app firmware/bios/main.c --board-check --baseline baseline `
  --baseline-results <基线all> --baseline-results <基线cache>
.venv/Scripts/python.exe scripts/release.py --verify build/releases/system/v0.7.2/<配置>
```

系统包发布必须 `RELEASES.yaml` 的包版本、`system/v<版本>` tag、HEAD 一致，输出 `build/releases/system/v<版本>/<配置>/`。涉及代码变更但不发布新系统包，执行同一命令加 `--snapshot`，输出 `build/archives/v<版本>-<Git hash>/<配置>/`；不增加 changelog，不覆盖正式包。纯文档提交不生成范围记录，构建产物也不适用。

最终包从提交后的干净源码重建，包含 boot/app bin/img/ELF/map、FS、PnR/板测/性能报告、构建参数、CPU 输入/生成信息、工具身份、VERSIONS.yaml、RELEASES.yaml、完整 Git hash 和文件 SHA256。失败状态为 failed；只有完成并 verify 通过才标 complete，匹配板测通过才更新 current。

完成条件：正式版本或提交快照已生成且验证通过。中间 run 可清理，但 recipe、源码/参数/CPU 输入和 catalog 保留；正式发布、提交快照和固定版本受到清理保护。复现需要相同工具/依赖版本。
