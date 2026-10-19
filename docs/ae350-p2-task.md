# AE350 P2 实现任务

更新：2026-10-19。P2 的唯一当前验收合同是 [内存系统交付合同](ae350-p2-memory-acceptance.md)，M1–M9 替代此前 G1–G11 作为新内存架构的完成门槛。旧 G 编号仅用于解释已有回执，不能用旧候选的通过项验收新结构。

实现范围为：厂商初始化 ITCM 启动、硬核 ILM/DLM、本地数据与栈、零基址 DDR、burst 宽窄转换、独立数据口时钟、正确的协议/CDC、唯一 UART loader、完整性及有计量依据的性能。删除 fabric 工作 SRAM、自定义启动 ROM AHB 桥、JTAG probe/mailbox/launcher。DDR 训练由厂商 IP 硬件完成；CPU POST 和完整诊断分别负责不同层次的检查。

源码回退点为 snapshot `cf020fe9b890163da0728ccd447fb66238e6b79d`。此前已验证 gateware `842482a17fb7` 只作功能/性能基线。当前实现和准确候选见 [内存结构整改进度](../reports/ae350-p2-memory-redesign.md)，操作见 [上板说明](ae350-p2-bringup.md)，地址及外设规划见 [地址合同](ae350-address-map.md)。

只验 DDR CK 400 MHz，不要求低频扫档或 30 分钟长跑；短压力须保存主机真实计时。核心频率没有独立验证前只发布原始 cycles。ILM/DLM 与 fabric ITCM 不混同；ECC 未验证不能宣称纠错通过。原子指令在当前 fabric DDR 路径不受支持，能力表明确限制。XIP、DMAC、多主仲裁及 HDMI 留到后续阶段。

所有构建、操作经 mini 入口，真实 FIFO/寄存级互连和故障路径先仿真，再做完整 STA 与匹配身份板测。不得把仿真、PnR、UART handoff、JTAG 可读或局部遍历互相替代。只写 FPGA SRAM，不写 Flash，不动 20K current/baseline；不默认物理断电。源码、准确依赖 ROM/载荷、操作回执、覆盖区间及未验收项一起交付。
继续实现或交接给其他 agent 时，先读 [当前实现交接](ae350-next-stage-agent-task.md)，其中记录未提交接手点、候选、剩余任务及复现命令。
