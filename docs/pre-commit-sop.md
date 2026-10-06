# 提交与代码发布 SOP

每次提交保存真实范围和适用检查。只有产品代码发布更新 CHANGELOG.md 和发布版本；范围记录 `reports/changes/` 只在代码变更时生成，文档、报告与构建/提交管理设施的改动不生成范围记录，也不创建 changelog 条目。代码变更先提交，再生成最终产物。发布包按版本号组织，提交快照按版本号加 Git hash 组织。

## 1. 确认范围与是否发布

读取 git status、diff 和新增文件，明确本次行为变化。暂存实现、必要测试和文档。git diff --cached --check 必须通过；每个暂存文件都需审阅。

产品代码发布阅读 [版本追溯](version-history.md)，维护 VERSION，选择未使用的 vMAJOR.MINOR.PATCH。新功能/架构变化递增 MINOR，产品修复递增 PATCH；文档/流程提交不递增。CHANGELOG 只描述该发布的功能、修复和验收结果，不收录提交流水、文档整理或流程脚本变化。

## 2. 选择候选与性能基线

按 [构建目录流程](build-artifacts.md) 创建新 run，记录 purpose、全部参数、Git hash、dirty 状态和源码输入指纹。dirty 候选保留 patch、未跟踪代码和 CPU RTL，不能仅用 HEAD 作为身份。历史登记标 historical-association，不把登记时间当构建时间。

基线由用户指定或与前一已验收功能版本关联。本次 v0.7.0 基线是 f0892da 的无 C/8 KiB ROM 共享 writeback 系统（追溯版本 0.6.0）；更早的 ff84bfb write-through 不替代它。双方时钟、工作量和身份必须一致可查。补测旧产物后恢复当前版本。

## 3. 完成适用验证

| 范围 | 验证要求 |
| --- | --- |
| 文档、提交/构建管理脚本 | 相应主机回归、CLI smoke、范围/报告校验；历史性能只作关联 |
| CPU/L2/DDR/总线/外设 gateware | 针对性 RTL/DFI 回归、实际配置 PnR setup/hold 0/0、匹配镜像实板检查、受影响并发及性能 |
| firmware/HAL/编译参数 | 编译、ABI/CRC、相关主机/实板测试及受影响热点 benchmark |

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

报告只保存人工审阅的改动说明、验证结果、性能依据和必要版本引用，不重复 Git 的文件清单或 diffstat。最终 Git index 的 patch 指纹只保存在本地 build/reports/commit-checks，--verify 用它检测暂存内容是否变化；本地记录缺失时重新 prepare。CHANGELOG 和 reports/changes 自身排除以免自引用。检查输入指纹必须匹配。硬件/固件提交额外使用新 captured/board-qualified 构建及性能报告。

仅产品代码发布额外传 --release-version <VERSION> --write-changelog，change 只写发布变化；普通提交不传这两个选项。历史功能版本补记可使用 --revision <产品代码提交>，明确是事后记录，不伪造当时的检查。完整提交范围直接查 Git，不再复制到报告或 changelog。

## 5. 复核并提交；发布创建版本 tag

```powershell
git add <明确文件；代码变更同时加入范围记录>
git diff --cached --check
.venv/Scripts/python.exe scripts/prepare_commit.py --title verified --change verified `
  --output reports/changes/pending.md --verify
git diff --cached --stat
git commit
git status --short
git tag -a v0.7.0 -m 'riscv-mini 0.7.0'  # 仅本次代码发布；使用实际 VERSION
```

普通提交不创建发布 tag。发布顺序：bump `VERSION` 并在 `CHANGELOG.md` 写本版条目，与功能代码同一个提交；提交得到 git hash 后，从该提交运行构建（干净工作树，产物身份不带 `.dirty`）并完成实板测试；再归档范围记录（`reports/changes/<commit7>.md` 与同名 `.json`）、打发布 tag，tag 指向最终干净提交，然后进入第 6 节生成最终包。不可覆盖已发布版本。实现或普通报告改动后重新验证并生成范围。此流程不自动推送。

## 6. 提交后生成并校验最终产物

```powershell
.venv/Scripts/python.exe scripts/release.py --version 0.7.0 --build current `
  --app firmware/bios/main.c --board-check --baseline baseline `
  --baseline-results <基线all> --baseline-results <基线cache>
.venv/Scripts/python.exe scripts/release.py --verify build/releases/v0.7.0/<配置>
```

正式发布必须 VERSION、tag、HEAD 一致，输出 `build/releases/v<版本>/<配置>/`。涉及代码变更但不发布新产品版本，执行同一命令加 `--snapshot`，输出 `build/archives/v<版本>-<Git hash>/<配置>/`；不增加 changelog，不覆盖正式包。纯文档提交不生成范围记录，构建产物也不适用。

最终包从提交后的干净源码重建，包含 boot/app bin/img/ELF/map、FS、PnR/板测/性能报告、构建参数、CPU 输入/生成信息、工具身份、VERSION、完整 Git hash 和文件 SHA256。失败状态为 failed；只有完成并 verify 通过才标 complete，匹配板测通过才更新 current。

完成条件：正式版本或提交快照已生成且验证通过。中间 run 可清理，但 recipe、源码/参数/CPU 输入和 catalog 保留；正式发布、提交快照和固定版本受到清理保护。复现需要相同工具/依赖版本。
