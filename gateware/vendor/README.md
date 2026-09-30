# GW2 DDR PHY

gw2ddrphy.py 来自 requirements.lock 固定的 LiteDRAM commit
676871a1f50a1ab0073eaadaa75763432f038188，原有版权与 BSD 标识保留。
本项目修改 DLL-off 分支的读捕获延迟及 DQS READ 窗口，不修改正常 DLL-on 读时序路径。
初始化 stop/reset/pause 寄存器另设稳定 RTL 名称并保留，以供精确时序约束引用；
constraints.py 从生成 RTL 解析 pause 的实际第一级同步器，避免添加外设后层级前缀变化导致约束失效。

在 H5TQ1G63EFR / CK 96 MHz / sys 48 MHz 上，原配置的突发检测为 0，
读回突发偏移；只提前 READ 会截断 BL8 尾部。修正为提前一个 sys 周期
开始 READ，同时保留原结束边沿，形成三个 sys 周期窗口；捕获延迟减少
一个 sys 周期，由 bitslip 训练选择剩余的半周期对齐。

上板完整 16-byte DFII 突发、三组模式、突发检测，以及 CPU 内存访问与
DDR 代码/栈测试验证通过。正常 DLL-on、高频率、其他颗粒尚未验证。
这份本地 PHY 是明确的补丁维护点；更新依赖时需核对并重新上板训练。

# SDCore

sdcore.py 来自锁定的 LiteSDCard commit
17718d9258ac2dd62ac23e2eecd7ac613a050986，保留原版权和 BSD-2-Clause 标识。
唯一功能补丁是将 cmd_crc 连接到 cmd_event.crc，并在响应 CRC 不符时置 cmd_error。
原版发现 CRC 不符却把 CRC 状态硬连为零，软件看不到失败。
sim/test_sd.py 在原版返回成功的坏 CRC 响应上失败，本地补丁返回 done/error/crc=1。
不修改命令格式、PHY 或数据 CRC 算法。更新依赖时须核对是否已上游修复。
