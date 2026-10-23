# 操作入口

所有机器使用 `python scripts/mini.py`；Windows PowerShell 可用 `./mini.ps1`。命令支持情况和执行语义由目标机器决定。

```sh
python scripts/mini.py --help
python scripts/mini.py guide
python scripts/mini.py doctor
python scripts/mini.py plan <target> --machine <machine>
python scripts/mini.py build <target> --machine <machine> --purpose candidate
```

硬件配置来自 Board/Machine JSON，CLI 不覆盖硬件参数。相同 machine/target 的重复构建始终沿用原 workspace（默认 `default`），用 purpose 和 run 收据区分尝试。构建不自动编程板子，不修改 current。

| 任务 | 公共入口 / 规则 |
| --- | --- |
| 环境发现 | `configure`、`doctor`；见 [开发环境](../docs/common/development.md) |
| 生成、编译、PnR | `plan <target>` / `build <target>`；见 [组件构建](../docs/common/component-builds.md) |
| 产物查询与身份 | `builds list` / `builds show <ref>`；见 [产物组织](../docs/common/build-artifacts.md) |
| 运行、恢复、验收 | `board`；从下表选择机器操作页和准确产物身份 |
| 导出与验证 | `release export` / `release verify`；导出不等于正式发布 |
| 历史复现 | `builds reproduce`，使用历史 checkout 的配方 |
| 清理 | `clean` / `builds clean`，先审阅计划，再显式执行 |
| 提交与正式发布 | [SOP](../docs/common/pre-commit-sop.md)；普通提交不触发 release |

## 按机器操作

| machine | 操作与验收 | 关键区别 |
| --- | --- | --- |
| `primer20k_vexriscv` | [20K 操作与更新](../docs/platforms/primer20k_vexriscv/operations.md) | 软件 DDR 初始化、ROM/XIP、BIOS、Flash 分区与固件套件 |
| `mega138k_ae350` | [AE350 操作与验收](../docs/platforms/mega138k_ae350/operations.md) | ITCM 启动、ILM/DLM、UART payload、CPU JTAG 与结果合同 |

选择准确的 build/artifact 或 run `result.json`；已有 current 必须核对所属机器。串口、FPGA location、CPU JTAG、启动存储和可用测试套件都按机器与实际连接选择，不从另一机器继承默认值。

日常板测使用 FPGA SRAM 和 UART 临时装载；只有用户明确要求时才写 Flash。写入地址、更新方法与读回要求见支持该操作的机器文档。PnR、上传交接、应用执行与套件通过分别记录。

新构建、测试与板端操作的请求、结果和日志统一放在 `build/runs/<run-id>/`，历史 `build/operations/` 保留。内部 `mini_ops/` 和各机器脚本负责具体实现；扩展操作能力时修改现有入口，不临时拼接 UART/编程器探针。
