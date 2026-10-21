# 稳定构建工作区验收（2026-10-21）

构建改为持续使用 `build/work/<machine>/<target>/<workspace>`，输入变化不再创建新的工作目录。成功结果按原有内容身份发布到 `build/store`；失败保留现场，但不替换成功基线。上游后端归档顶层交付文件，源码和对象子目录保留在工作区。此次没有清理旧目录、修改 P4 硬件/固件实现、改变 current、提交或操作板子。

## 已验证的行为

- 74 项构建测试通过，包含 14 项工作区相关测试；另一个模块测试集合 42 项通过（与前者部分重叠，不相加）。覆盖变更输入继续使用同一目录、失败保留成功引用、命名工作区隔离、锁冲突、清理拒绝活跃工作区、只读位流失效、旧输出不作为验收结果、种子完整性和兼容性、输入索引恢复、归档成功输出、静态库移除旧成员、Gowin 自动降级的识别。
- 20K minimal 系统的 RTL、bootloader、HAL SDK、BIOS 构建成功；产物导出和 verify 成功。此项未运行 Gowin，不能作为 20K PnR 或板测证据。
- 20K 测试应用首次编译 2 个单元，修改同一路径的应用后仅编译 1 个单元，启动对象复用。再次执行相同请求直接复用成功产物。
- AE350 测试应用首次编译 7 个单元，修改应用后仅编译 1 个单元，6 个运行时对象复用。构建身份宏通过生成头传递给实际使用者，避免输入摘要变化使所有编译命令变化。
- 普通 payload 后端实际编译并归档成功，工作区对象子目录不会进入顶层交付快照。
- `compileall` 和 `git diff --check` 通过。

## Gowin 的实测边界

Gowin V1.9.12.04，GW5AST-138B，place/route option 2/2。在同一个 `pnr-validation` 工作区执行：

| 试验 | 结果 |
| --- | --- |
| 全量对照 | PnR 188 秒；setup/hold 0/0；最小脉宽 slack +0.142 ns |
| 完整 `.pr` 复用 | 工具明确读取 baseline.pr；运行 392.1 秒后仍在读取/匹配阶段，人工停止本次性能试验；没有成功位流或时序结论 |
| 只复用 `.p` | 工具报告匹配率 58.68%，低于 80% 阈值，PA2136 警告后自动改用全量布局；最终 PnR 242 秒，setup/hold 0/0，最小脉宽 slack +0.142 ns |
| 相同布局请求重跑 | 约 2 秒复用归档产物，没有启动 Gowin |

全量和布局试验之间构建驱动有修正，ROM 的构建身份也变化；它们不是相同综合网表的严格性能 A/B，不能把耗时差推广为一般性能结论。现有证据没有证明厂商增量 PnR 加速，默认保持 `--pnr-mode full`，`place`、`pnr`、`auto` 显式选择。新的运行记录会把厂商自动降级写入 `implementation_flow` 和归档的 `pnr-flow.json`，不能将“传了增量参数”当作增量实际生效。

工作区成功引用经历过 Windows 只读 `.fs` 清除失败及主动停止 `.pr` 试验，仍保留之前的完整成功产物；后续 `.p` 试验从不可变归档恢复种子并通过时序检查。Gowin 综合缓存保留在工作区，没有复制进成功 gateware 归档（本次缓存约 6.2 MB）。`.p/.pr` 仍需要归档，以便在工作区清理或失败后恢复可追溯基线。

## 验收收据

收据位于仓库本地 `build/runs`，成功/失败状态与引用以各自 `result.json` 为准：

| 行为 | Run ID |
| --- | --- |
| AE350 全量 | `20261021T032348343491Z-build-stable-workspace-full-71e169` |
| `.pr` 试验及停止时间 | `20261021T032848985494Z-build-stable-workspace-incremental-7ab70f`，另有 `experiment-stop.json` |
| `.p` 及厂商降级日志 | `20261021T033606996446Z-build-stable-workspace-place-692531` |
| gateware 产物复用 | `20261021T034142146252Z-build-stable-workspace-place-reuse-329f7a` |
| 20K 系统 | `20261021T033112328918Z-build-stable-workspace-20k-smoke-745404` |
| 20K 应用初次/变更/复用 | `20261021T033207614161Z-build-stable-firmware-first-626a57`、`20261021T033225146179Z-build-stable-firmware-changed-6a087c`、`20261021T033231995379Z-build-stable-firmware-reuse-1c8dd4` |
| AE350 应用初次/变更 | `20261021T033705057242Z-build-stable-ae350-payload-first-7b1cff`、`20261021T033710246878Z-build-stable-ae350-payload-changed-aef7e3` |
| 普通后端归档 | `20261021T033309685576Z-build-stable-backend-smoke-8ed9f8` |
| 导出/校验 | `20261021T033823060156Z-export-c0cb0d`、`20261021T033833361845Z-verify-f4c177` |

Gowin 流程降级的结构化解析是在上述真实日志生成后补充的，使用该警告格式增加了回归测试；没有为元数据解析再次运行完整 PnR。

## 许可证连接重试补充验收

20K、AE350 和 CPU qualification 的 Gowin 调用接入统一的许可证连接重试。首次失败后最多再尝试 3 次，间隔 5/15/30 秒，每次保留独立日志及退出码；每次重新使旧位流/报告失效，达到上限失败，不发布新成功产物。

`tests/common/gowin_retry_test.py` 的 10 项测试通过，包含真实子进程模拟首次许可证连接失败、第二次恢复，也覆盖持续故障达到上限、退出码 0 却报告许可证连接失败、许可证过期、普通设计错误、混合报错、正常成功和退避期间取消。故障注入只针对测试子进程，没有更改真实许可证服务。另有 74 项构建测试、compileall 和差异检查通过。

实际 `mini.py build system --profile minimal --workspace retry-smoke` 构建通过，收据 `20261021T035000810976Z-build-license-retry-wrapper-smoke-2f5d55`；其中 `gateware-attempts.json` 记录一次正常成功尝试。这是 RTL/固件及日志归档的接线检查，未运行 Gowin 综合，不宣称已复现真实许可证服务故障。

## 提交前清理验收

先审阅 `builds clean obsolete` 和 `builds clean work` 的计划，再执行对应 `--execute`。删除 134 项过期输出/引用/索引和 12 个工作缓存目标，没有保留失败项。清理后 `build` 从约 12.00 GiB 降至 7.50 GiB，释放约 4.49 GiB；提交后快照会新增必要产物。

正式发布、提交快照、固定版本、catalog 和 recipe 保留。本轮 15 份验收收据及关联产物引用另存 `build/archives/workspace-validation-20261021`，受归档依赖保护。旧清理器已支持 `backend/<target>` 组件、保留工作区的成功实现基线，并使用维护锁拒绝与活跃构建并发；`builds clean work` 也使用工作区锁。

清理专项 9 项、工作流 20 项、许可证重试 10 项、构建 74 项及另一个模块集合 42 项通过（集合部分重叠，不相加）。工作流检查保存于 `build/reports/checks/stable-workspace-workflow.json`。此提交属于构建管理设施变更，不增加组件版本、产品 CHANGELOG 或发布 tag。
