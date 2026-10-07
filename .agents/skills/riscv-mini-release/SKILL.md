---
name: riscv-mini-release
description: 在 TangPrimer-20K/riscv-mini 生成正式版本发布包、代码提交快照或历史阶段构建包，完成实板与基准验收，并把 tag 与构建包发布到 GitHub Release。
---

# 发布与最终归档

在 riscv-mini 仓库根目录使用 PowerShell 和 `.venv/Scripts/python.exe`。先确认 Git 状态与操作对象；命令中的尖括号为本次实际值，参数查对应脚本 `--help`。项目外调用时解析用户级 skill junction 的 Target，其上三级为仓库根目录。

读取 `docs/pre-commit-sop.md` 与 `docs/build-artifacts.md`；版本与提交的对应关系读取 `docs/version-history.md`；上板与基准前读取 `docs/lab-environment.md`，不要重新探测现场条件。

## 正式发布

核对干净 HEAD 与 `VERSIONS.yaml`；正式发布还需在 `RELEASES.yaml` 写好本次系统包（`system/v<版本>` 的 tag 指向 HEAD），版本不可覆盖。尚未提交时先完成同级 `riscv-mini-commit/SKILL.md`。涉及代码变更：先提交干净源码，再生成最终包。

系统包声明六个组件的版本，但 `release.py` 从当前 HEAD 构建：它要求 `RELEASES.yaml` 的组件组合等于当前 `VERSIONS.yaml`，构建后还会把产物里的 `component_versions` 与组合再核对一次，不一致就拒绝。包只构建并板测 SoC/bootloader/应用；OpenSBI、U-Boot 等由各自流程（`uboot_verify.py` 等）单独构建与验收的组件，在 `RELEASES.yaml` 的 `external:` 里声明为"已声明兼容、不在本包内构建/验证"，避免用六个版本号暗示全部已验收。

顺序不可颠倒：

1. 递增被改动组件在 `VERSIONS.yaml` 的版本，并在 `CHANGELOG.md` 写该组件本版条目，与功能代码同一个提交；提交后打 `<component>/v<版本>` tag。
2. 在 `RELEASES.yaml` 写系统包：`components` 钉住六个组件版本（须等于 `VERSIONS.yaml`），`external` 列出不在本包构建/验证的组件。
3. 提交，得到 git hash，打 `system/v<版本>` tag 指向该提交。
4. 从该提交运行构建（干净工作树，产物身份不带 `.dirty`），并在实板完成测试。
5. 归档 report/change 记录（`reports/changes/<commit7>.md` 与同名 `.json`），然后运行 `release.py` 生成最终包。

被测试与被发布的 binary 必须来自已提交树；dirty 树的构建只能作为候选验证，不能作为发布验收。

```powershell
.venv/Scripts/python.exe scripts/release.py --version <系统包版本> --build <配置模板ID> `
  --app firmware/bios/main.c --board-check --baseline <基线ID> `
  --baseline-results <基线all目录> --baseline-results <基线cache目录>
.venv/Scripts/python.exe scripts/release.py --verify <生成的最终包目录>
```

不发布新版本的代码提交加 `--snapshot --version <版本>`，输出 `build/archives/v<版本>-<Git>/`。纯文档提交无需 FPGA 重建。

## 历史阶段补建 tag 与构建包

为已有功能阶段补 tag 与包时不修改仓库脚本，也不覆盖已发布版本。

```powershell
git tag -a <tag> -m 'riscv-mini <版本> <阶段说明>' <commit>
git worktree add --detach build/worktrees/<tag> <tag>
```

- worktree 必须在仓库内用 `git worktree add` 创建；`git worktree move` 带进来的安全描述符缺少沙箱写权限，手工补 ACE 也不能修复。
- 构建用该提交自己的 `scripts/build.py --synthesize`。HEAD 的 build.py 不能表达集成 SRAM、硬编码 ROM、`--l2-mode` 等旧配置。
- 子进程只能写仓库内：`TMP`/`TEMP` 指到 `<worktree>/build/.tmp`，sbt 的 Coursier 缓存放进 `build/java-cache/coursier`；CPU RTL 用该提交的 `scripts/cpu_generate.py` 生成（Java 8、sbt-launch 1.9.7、VexRiscv 固定 hash），旧版本还会把 sbt 临时目录写进 `--vexriscv-source`，需要把该源码目录也放进 worktree。
- 打包与登记用的 helper 不入库，按下列契约现写，复用 HEAD 的 `scripts/build_records.py`，输出 `build/releases/<tag>/<配置>/`：`release.json` 标记 `release_kind: "code-release"`、不哈希自身、保存逐文件 SHA256；`validation.json` 补写的字段列进 `annotated_fields`；`profile` 含空格或斜杠时用 `--profile-slug` 与 `--config` 生成可作目录名的配置名，catalog 记录 id 同样必须文件系统安全。
- 每个包都要 `scripts/release.py --verify` 通过。

## 实板与基准

按版本顺序逐个上板，用该提交自带的脚本；命令见同级 `riscv-mini-test/SKILL.md` 与 `riscv-mini-benchmark/SKILL.md`，现场条件见 `docs/lab-environment.md`。

- 0.1.0 只有 `scripts/boot_verify.py`，0.2.0 起才有 `scripts/firmware_verify.py`。跑过使用 SPI SD 的版本后必须整机断电重启或重新拔插卡，卡才能回到 native SD。
- 基准只跑该提交自带的 suite：0.4.0 没有 `cache`；`--suite net` 要求主机网卡持有板子配置的 server 地址。失败的 suite 不进包，现象与限制写进 `docs/version-history.md`，不写成已定位的根因。
- 验收结果写回包：`release.json` 的 `board_checked`、`board_test`、`board_checks`、`board_evidence`、`performance`；原始 JSON 与 UART 日志进 `reports/`，基准数据进 `reports/performance/<suite>/`；对应 catalog 记录状态改为 `board-qualified`。
- 全部结束后把当前正式版本的 bitstream 与镜像重新烧回板子，恢复板子状态。

## 发布到 GitHub

```powershell
git push origin HEAD
git push origin --tags
foreach($tag in <本次 tag 列表>){
  gh release create $tag --title "riscv-mini $tag" --notes-file build/release-archives/notes-$tag.md `
    (Get-ChildItem build/release-archives/riscv-mini-$tag-*.zip).FullName
}
```

- zip 与说明文本由现写的 helper 生成，同样不入库。zip 以 `<tag>/<配置>/` 为顶层目录，内容是已 verify 的整个包，命名 `riscv-mini-<tag>-<配置>.zip`，放在 `build/release-archives/`。
- 说明文本从各包 `release.json` 与 `validation.json` 生成，不手写数字：阶段能力、tag 指向的完整 commit、构建方式与工具链、PnR 0/0 与 Logic/BSRAM、实板检查数、基准结果与失败项，并指向 `docs/version-history.md` 与 `docs/lab-environment.md`。
- 历史阶段的包写明它不是正式发布、只有当前 `VERSIONS.yaml` 各组件版本是；不使用回填或 backfill 这类标记。

## 完成条件

最终包 complete 且 verify 通过，含二进制、报告、参数、CPU 与工具输入、版本、完整 Git hash 和文件 SHA256。正式包位于 `build/releases/system/v<版本>/`，提交快照位于 `build/archives/v<版本>-<Git>/`。GitHub 上每个 tag 有对应 Release 与可校验的 archive。
