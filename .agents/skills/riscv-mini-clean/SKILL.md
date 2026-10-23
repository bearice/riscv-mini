---
name: riscv-mini-clean
description: 清理 riscv-mini 的受管理构建缓存和无引用中间产物，先检查计划并保留固定版本及证据。
---

# 清理构建产物

先读 [产物与保护规则](../../../docs/common/build-artifacts.md)，确认本次范围；不并行运行构建或板端操作。

```sh
python scripts/mini.py builds clean work
python scripts/mini.py builds clean obsolete
```

只查看与请求范围对应的计划，检查目标、固定引用、文档/报告证据及依赖保护；确认计划符合已授权范围后，对同一命令加 `--execute` 执行。单个已登记旧构建使用 `builds clean <catalog-id>`，不用手工删除其 catalog 或 recipe。

work 清理保留稳定目录约定；同一目标后续重建继续使用原 workspace。obsolete 可回收无引用 store、run、候选引用与索引，不能把所有 store 都当作永久保留，也不能删除固定引用及其递归依赖。

正式包、源码、vendor、recipe、Git 工作树与有引用证据按清理器规则保留。未知目录或未覆盖的文件先核对用途，不扩大为整棵 build 删除。

完成条件：检查执行收据与退出状态，报告删除范围、保留项及失败原因；失败保留记录，修复后重试原命令。
