---
name: riscv-mini-release
description: 在用户明确要求 riscv-mini 正式发布时管理版本、构建验收并生成可核验的发布包。
---

# 正式发布

仅在用户明确要求正式 release 时执行。读取 [发布 SOP](../../../docs/common/pre-commit-sop.md#正式-release)、[版本规则](../../../docs/common/version-history.md) 与 [产物规则](../../../docs/common/build-artifacts.md)，再按 [平台索引](../../../docs/README.md) 确认可用打包与验收流程。

1. 确认从上一正式版本到本次发布的功能范围、目标机器与组件组合。只更新变化组件的版本和功能 CHANGELOG，在 `RELEASES.yaml` 声明实际组合及未在本包构建/验证的 external 组件。
2. 提交待发布源码、版本和清单，取得干净 Git hash；从该提交构建最终镜像。版本和 tag 不可覆盖，最终包与验收必须关联同一准确 binary。
3. 按发布涉及的功能完成适用主机/RTL、PnR、实板及性能检查，保存真实结果和限制。20K 的 BIOS/all/cache 规则不套用 AE350；没有支持的打包路径时先说明具体缺口，不生成伪造的通过记录。
4. 使用 `mini.py release create` / `release verify` 生成并核验最终包。包内版本、源码、参数、二进制哈希和验收证据必须一致。

20K 命令见 [系统包发布示例](../../../docs/platforms/primer20k_vexriscv/release.md)。构建复用同一 workspace；实板流程见相应机器操作页。发布不自动授权 Flash 写入，cold boot 仅按明确请求执行。

推送或发布到 GitHub 等远端按用户授权范围执行，选择本次明确 tag 和已核验产物，不批量推送无关 tags。未执行的远端发布和 current 选择如实报告。

仅要求导出开发构建时使用 `release export` / `release verify`；提交快照只按明确请求处理，不追加正式发布验收、版本或远端发布。

完成条件：正式包 complete、verify 通过，已完成承诺的验收与获授权的发布动作，版本、tag、完整 Git hash 和产物身份一致。
