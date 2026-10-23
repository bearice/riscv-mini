---
name: riscv-mini-benchmark
description: 在 riscv-mini 测量或比较指定机器的性能，关联准确候选、基线和工作负载并生成报告。
---

# 性能测量与对比

先读 [产物身份](../../../docs/common/build-artifacts.md)，明确机器、候选与基线、时钟、ISA、工作负载及外部条件。通过 [平台索引](../../../docs/README.md) 选择实际支持的套件，不将20K BIOS命令用于AE350。

- 20K：[BIOS benchmark](../../../docs/platforms/primer20k_vexriscv/benchmark.md)，运行前核对 [实板条件](../../../docs/platforms/primer20k_vexriscv/lab-environment.md)。完整对比保存 all/cache 各三轮，按报告工具组织双方结果。
- AE350：[内存验证合同](../../../docs/platforms/mega138k_ae350/memory-validation.md) 与 [操作口径](../../../docs/platforms/mega138k_ae350/operations.md)。隔离测试使用 ILM 代码、DLM 栈/结果；保存原始 cycles、内存流量和计时边界，未标定频率不声称已验证吞吐。

按测量顺序加载准确镜像；20K 的 `benchmark.py --build-dir` 只关联选定身份，不自动编程或读回 FPGA。每次测量保存独立结果，不能覆盖上一轮；测量目录与稳定构建 workspace 是不同用途。

对比须采用一致工作量与计时口径，原始数据、配置、产物 hash 和结果可互相追溯。失败、零字节、未执行或旧身份无法关联的数据说明原因并排除有效吞吐比较。存储默认只读，网络/物理输入按请求范围选择。

完成条件：适用轮次的原始数据与报告可查，说明比较结果、限制和结束后的板卡状态；不自动改变 baseline/current。
