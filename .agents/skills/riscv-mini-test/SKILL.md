---
name: riscv-mini-test
description: 在 riscv-mini 选择并执行主机、RTL 或指定机器的实板测试，核对镜像身份和受影响功能。
---

# 测试与实板验收

按用户请求与改动选择主机、RTL、内存接口或实板层级，运行能验证受影响行为的检查；已有有效结果无需重复。此流程不自动扩展为完整发布验收。

主机/RTL 回归按 `tests/common/`、`tests/<machine>/` 选择，先查脚本参数及所需工具。仿真、编译、PnR 和板测分别记录，不能互相替代。

板端操作先读 [公共入口](../../../scripts/README.md) 和对应机器的操作页，确认位流、软件、ABI、PnR 与准确产物身份：

- [20K](../../../docs/platforms/primer20k_vexriscv/operations.md)：BIOS/monitor 的功能命令见 [固件测试](../../../docs/platforms/primer20k_vexriscv/firmware-tests.md)。综合 soak、麦克风等按功能与实际连接选择。测试 BIOS 二级程序时读 [载荷移植](../../../docs/platforms/primer20k_vexriscv/bios-payload-porting.md)。
- [AE350](../../../docs/platforms/mega138k_ae350/operations.md)：显式选择 gateware 与 payload；UART 交接、应用执行和 ddr/dma/io/audio/video 套件分别判定，CPU JTAG 按该操作要求配置。

日常测试用 FPGA SRAM 和 UART loader，Flash 写入仅在明确请求时执行；冷启动也须用户明确要求。复用公共 `mini.py board` 工作流，不临时拼接编程器或 UART 探针。

完成条件：结果和日志保存在本次 `build/runs/`，关联准确镜像；说明通过、失败、未执行与外部限制。物理显示、听音、真实输入与数字回执分开记录。测试不自动 pin current 或更新 baseline。
