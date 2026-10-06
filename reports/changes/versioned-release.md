# 以代码发布版本跟踪产物，限制 changelog 为产品发布

生成：2026-10-06T03:17:22+00:00；模式：staged review。

## 改动

- 依据完整 Git 功能里程碑确立当前 0.7.0；历史阶段仅作追溯映射，正式 VERSION 和 Git tag 绑定最终代码发布。
- CHANGELOG 仅保存产品发布变化，文档/流程提交保存独立范围记录；显式 release-version 才能写入 changelog。
- 正式包采用 releases/v版本/配置，非发布代码快照采用 archives/v版本-Git hash/配置；VERSION 纳入输入指纹和 recipe，保存版本、tag 与完整 Git hash。
- 补充版本合法性、普通提交 changelog 禁止写入、VERSION 复现和构建别名清理保护；13 项主机回归通过。

## 验证

对应源码的检查：13 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/6880dee-vs-f0892da.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
