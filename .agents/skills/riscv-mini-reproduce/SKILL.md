---
name: riscv-mini-reproduce
description: 使用 riscv-mini 保存的构建身份与配方复现历史产物，在独立 checkout 恢复对应源码和参数。
---

# 复现历史构建

读取 [历史复现与产物身份](../../../docs/common/build-artifacts.md)。确认用户选择的机器、catalog ID、recipe 和工具/依赖可用；普通增量重建使用构建流程，普通产物不承诺已保存完整源码归档。

```sh
python scripts/mini.py builds show <catalog-id>
python scripts/mini.py builds reproduce <catalog-id> --output-dir <复现输出目录>
```

由保存的配方在独立 checkout 恢复源码、参数与 CPU/IP 输入，并运行该历史提交自己的入口。不要用当前 CLI、机器 JSON 或默认值补造旧输入；没有配方或必要源码时明确报告缺失。

输出与原始身份和证据分开保存。重复尝试沿用已选择的复现工作区/输出位置，不以时间戳持续创建新缓存目录；具体目录规则由历史配方决定。

完成条件：新产物和输入可对应原配方，说明字节校验或重新构建的实际范围。PnR、板测与性能仅报告本次实际执行结果，不继承原构建的通过标记。
