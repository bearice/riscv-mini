# 集中管理 SD/LCD 内存端口缓冲的实验分支

生成：2026-10-07T09:46:09+00:00；模式：staged review。

## 改动

- SD 读写端口收窄到 32 bit、LCD 到 16 bit；每端口 16 B 缓冲由 SharedMemoryController 管理，保留内部 128-bit SharedL2/native 路径。
- 写 completion、取消排空和缓存维护屏障通过 RTL 回归；匹配候选 PnR 0/0、实板 49 检查、60 秒 soak 与三轮 all/cache 通过。
- 按用户要求仅提交参考分支，暂不合并或发布；已有 dirty 候选与捕获 recipe 保留，不将其重新标为提交后发布构建。

## 验证

对应源码的检查：24 项，PASS；命令和输入身份见同名 JSON。

性能依据：`reports/performance/buffered-memory-ports.md`。

完整改动以 Git 记录为准；本记录只保留改动说明和验证依据。
