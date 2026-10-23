# 文档目录

先选择机器，再查其启动、地址、设备和验收合同。`common/` 保存所有机器共同遵循的规则，`platforms/<machine>/` 保存机器专属设计与操作。测试结论与准确镜像身份保存在 `reports/` 和本地 run 收据中。

## 通用规则

| 文档 | 内容 |
| --- | --- |
| [开发环境](common/development.md) | Linux/NixOS、Windows 与工具发现 |
| [平台构建配置](common/platform-descriptions.md) | Board/Machine 严格 JSON、参数消费者与 IP 生成 |
| [配置与 LiteX 集成](common/platform-configuration-architecture.md) | 公共配置前端、原生资源与机器适配的边界 |
| [组件构建](common/component-builds.md) | build/plan、增量复用、稳定 workspace、导出与兼容性 |
| [构建产物](common/build-artifacts.md) | store/work/runs、身份、引用与保护规则 |
| [操作入口](../scripts/README.md) | 公共 CLI 与按机器选择板端流程 |
| [提交与发布](common/pre-commit-sop.md) | 普通提交、明确 release 与归档的边界 |
| [版本追溯](common/version-history.md) | 版本标签与组件组合；各平台历史另列 |

## 平台

| machine | 设计与操作入口 | 配置源 |
| --- | --- | --- |
| `primer20k_vexriscv` | [Tang Primer 20K / VexRiscv](platforms/primer20k_vexriscv/README.md) | [Board](../gateware/boards/primer20k_dock3713/board.json)、[Machine](../gateware/machines/primer20k_vexriscv/machine.json) |
| `mega138k_ae350` | [Tang Mega 138K Pro / AE350](platforms/mega138k_ae350/README.md) | [Board](../gateware/boards/mega138k_pro/board.json)、[Machine](../gateware/machines/mega138k_ae350/machine.json) |

公共文档中的平台差异应通过表格或链接说明；具体引脚、时钟、容量、启动存储、ABI、Flash 分区、默认串口和测试命令放在对应机器目录。协议核心即使复用，机器地址、中断和 DMA/cache 所有权仍分别说明。

## 维护范围

文档维护现行规则、机器设计与仍有效的能力边界。已经完成的迁移提案、阶段验收过程、调试流水账和临时候选清单不再保留为操作文档；历史实现从 Git 追溯，具体测试证据见 `reports/`。

修改配置后以 JSON 与实际产物导出为准，不用旧 full/minimal、候选目录、哈希或资源数字推断当前默认值。新增文档按职责归位并更新平台索引；迁移或删除时同步修复入口和交叉链接。
