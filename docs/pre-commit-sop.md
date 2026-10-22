# 提交与正式发布 SOP

普通 Git 提交用于保存开发进度；只有用户明确要求正式 release 时，才执行发布验收、版本管理和最终归档。代码、RTL、固件、文档和流程脚本的普通提交采用同一套轻量流程。

## 普通提交

1. 查看 `git status`、diff 和新增文件，确认本次内容；明确暂存要提交的文件。
2. 审阅 `git diff --cached`，执行 `git diff --cached --check`。
3. 写清提交说明并提交，检查提交后状态。推送按用户要求执行。

```sh
git status --short
git diff
git add <本次文件>
git diff --cached
git diff --cached --check
git commit -m '<改动与原因>'
git status --short
```

普通提交不强制运行测试、构建、PnR、实板、性能基准，不要求 `prepare_commit.py`、检查报告、`reports/changes/`、暂存指纹、版本递增、CHANGELOG、tag 或提交后快照。开发中可按问题需要做针对性检查，用户明确要求的测试仍须完成；已有验证结果无需为了提交再重复执行。未验证的内容如实说明。

完成条件是本次 Git 提交已创建；不自动进入 release，也不在提交后追加构建或归档任务。

## 正式 release

以下要求只适用于用户明确要求的正式发布。检查范围按上一正式版本到本次发布源码的全部变化确定，不能只看最后一个版本号提交。

1. **确定版本与范围。** 阅读 [版本历史](version-history.md)，只递增本次发布涉及的组件版本；`VERSIONS.yaml` 与 CHANGELOG 仅在 release 更新。系统包在 `RELEASES.yaml` 固定组件组合及不在本包验证的 `external` 组件。
2. **选择候选与基线。** 使用 [组件构建](component-builds.md) 的统一入口和默认 work/store/runs 目录，记录准确产物身份、参数和源码；性能基线选择明确的已验收版本，不硬编码历史实验目录。
3. **完成适用验收。** 按下表覆盖本次发布涉及的功能，保存实际命令、结果及限制。开发候选结果可帮助判断，但最终发布镜像必须来自已提交的干净源码，并匹配实际验收的二进制。

| 发布涉及的变化 | 验收要求 |
| --- | --- |
| 构建工具或流程 | 相关主机回归与 CLI 检查 |
| firmware/HAL/编译参数 | 编译、ABI/CRC、相关主机回归及功能实板验收；RTL 不变时沿用匹配硬件证据，不重复硬件验收 |
| CPU/L2/DDR/总线/外设 RTL | 相关 RTL/DFI 回归、实际配置 PnR setup/hold 0/0、匹配镜像的实板检查、受影响并发与性能验收 |

PnR 通过和实板通过分别记录。板测必须匹配实际 bitstream 与应用哈希；不能把旧候选的通过标记套给新镜像。需要 all/cache 性能对比时各跑三轮，双方时钟与工作量一致，保留原始数据和明确的候选/基线身份。失败与未验证项如实记录。默认不做断电冷启动；常规验收使用 SRAM/UART，Flash 只在用户明确要求时写入。

4. **生成并验证发布包。** 提交发布源码与版本信息，从干净提交构建和验收，生成含完整 Git hash、版本、参数、工具/CPU 输入、二进制、哈希和验收记录的最终包，再执行 `release verify`。版本与 tag 不可覆盖；推送、GitHub Release 和 `current` 更新按相应发布任务执行。

```sh
python scripts/mini.py release create --version <系统包版本> --hardware <配置模板> \
  --source firmware/bios/main.c --board-check --baseline <已验收基线> \
  --baseline-results <基线all目录> --baseline-results <基线cache目录>
python scripts/mini.py release verify <生成的发布包目录>
```

完整操作见 [发布 skill](../.agents/skills/riscv-mini-release/SKILL.md)。release 的完成条件是发布包 complete、verify 通过，并完成该发布承诺的验收；声明的版本、tag、源码与二进制必须一致。

## 可选记录与快照

`prepare_commit.py` 仅为可选记录工具，默认轻量模式不要求验证材料；显式 `--release` 或 `--release-version` 才启用发布检查。若主动提供检查/构建/性能记录，工具仍会校验它们是否匹配，不能把失败或过期结果写成通过。`--verify` 只用于主动保存过的本地审阅记录，不是普通提交的门槛；轻量记录不能被冒充为 release 验证。

`reports/changes/` 可在需要时汇总发布行为和验收依据，不复制 Git 文件清单。只有显式 release 的 `--component <组件> --release-version <版本> --write-changelog` 才写 CHANGELOG；组件版本必须与清单一致。

保存已有开发构建可使用 `release export` / `release verify`，不自动补齐正式发布验收。提交快照只在用户明确要求时生成，使用 `release create --snapshot`；普通提交不自动建快照，也不自动发布。已有 store、runs、recipes、正式包和固定版本继续按 [产物保护规则](build-artifacts.md) 管理。
