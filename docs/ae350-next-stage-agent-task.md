# AE350 下一阶段实现任务：统一地址与自主 UART loader

更新：2026-10-18。可直接将本文件交给实现 agent 执行。本文汇总用户最新要求，优先于旧任务书中冲突的频率、布局和启动顺序要求。

## 1. 起点、范围与交付方式

在当前工作树及已有 P2 证据上继续，不重置回早期提交，不覆盖其他人的未提交修改。先核对 Git 状态、候选源码身份、当前板卡和已在运行的任务。共享板卡同一时刻只允许一个执行者使用。

仓库：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini`。`TangPrimer-138K/docs` 和 `example` 是只读参考，项目文档写在本仓库。

本轮必须实现：统一内存布局、必要地址合同、AE350 UART恢复、自主短POST和UART payload loader，以及受影响的P2回归。未来DMA/外设/XIP的地址要规划清楚，但不要求本轮同时实现全部外设或XIP。LCD暂不移植，视频采用HDMI。

构建和板端操作统一走 `scripts/mini.py`；必要时扩展现有后端，避免新增零散公开入口。保留旧P1恢复候选、20K current/baseline及原始失败证据。不写Flash、不修改FTDI驱动、不占用摄像头接口、不增加UART调试电路、不执行未经授权的断电冷启动。不主动push/tag/发布。

## 2. 本轮已更新的文档与阅读顺序

以下是最近这轮规划中实际新增/更新的文档，不是实现完成清单：

| 文档 | 本轮变更与用途 |
| --- | --- |
| [本文件](ae350-next-stage-agent-task.md) | 新增完整交接提示，纳入“参考20K ROM、UART加载payload”的最新要求及验收 |
| [统一地址规划](ae350-address-map.md) | 新增DDR/SRAM/ROM布局、硬核保留区、DMA/CSR/FIFO地址、buffer/ABI合同；补充XIP和Flash管理地址。本轮进一步明确UART loader优先、XIP实现后置 |
| [P2任务书](ae350-p2-task.md) | 新增400MHz唯一DDR交付点、零基址DDR、高地址SRAM、复位向量说明；调整G10和实施顺序；本轮补充自主POST与UART loader要求并链接本交接文件 |
| [阶段计划](ae350-migration-stages.md) | 加入单一400MHz DDR验收决定、链接P3地址合同；本轮同步UART loader优先和XIP后置顺序 |
| [P2 bring-up说明](ae350-p2-bringup.md) | 将低地址SRAM布局明确标为现有旧候选，添加待实现的新布局；本轮说明现有JTAG launcher与目标UART loader的区别 |

另读此前已存在的证据文档：[ROM/JTAG启动器调查](../reports/ae350-jtag-launcher-investigation.md)、[P2阶段报告](../reports/ae350-p2.md)及配套JSON。它们记录各自源码版本的实测，不能用规划修改倒推其已在新布局通过。

## 3. 频率要求

DDR CK固定400MHz，是唯一拟交付及强制验收频点。100/200/300MHz保留历史诊断，不再补测、不要求通过。必要时可为定位具体故障临时降频，但不重新开启完整扫档任务。

CPU core、AE350数据口、AHB/APB、DDR UI、DDR CK分别登记。CPU实际频率与mcycle计时来源仍须核实；DDR只验400MHz不等于性能可按未经验证的800MHz换算。压力保存主机单调时钟起止及调试暂停影响。

## 4. 下一版静态内存布局

| 区域 | 地址范围（含端点） | 初始大小 |
| --- | --- | --- |
| DDR | 0x00000000–0x3fffffff | 预期1GiB，可用容量按实测发布 |
| fabric SRAM | 0x40000000–0x40001fff | 8KiB |
| fabric ROM | 0x80000000–0x80001fff | 8KiB |
| XIP预留 | 0x88000000–0x8fffffff | 128MiB地址预留，不代表Flash实际容量 |

DDR CPU物理字节地址直接对应DRAM偏移，去掉减0x2000、低地址SRAM覆盖及运行时内存重映射。ROM保留已验证的默认入口。SMU HART0_RESET_VECTOR在0xf0100050，手册定义R/W、默认0x80000000；本轮不依赖改变它，也不混淆DPC、mtvec与复位向量。

集中维护地址合同，并供RTL、BSP、linker、loader、载荷、runner、元数据及未来设备树消费。显式分配启动栈、data/bss、接收控制、mailbox、结果和保留区，加入重叠/溢出/越界检查，不把旧地址简单逐处加偏移。DDR故障时ROM/SRAM/JTAG恢复仍可用。

保留ROM已修复的“锁存地址→同步读取→寄存响应→ready”时序。互连以同一个全局地址接受沿驱动请求与响应选择；正确处理AHB地址/数据相位、背压及跨从设备连续事务，不恢复已被否定的pending守卫或额外HOLD协议。

## 5. 自主 ROM、POST 与 UART loader：本轮启动主线

先检查20K当前ROM loader、上传协议、镜像格式和主机 `board run` 实现。优先复用协议、镜像封装、校验和主机工具，通过AE350 BSP/平台适配，不创建另一套不兼容的138K专用上传协议。

目标流程：

```text
FPGA配置完成
→ ROM自主启动，使用高地址SRAM
→ 检查DDR自动校准结果
→ 短内存POST
→ UART输出平台/构建身份和loader就绪
→ UART接收、校验并装载payload到DDR
→ 同步cache/指令视图、建立执行环境
→ 跳到payload入口
```

DDR硬件训练/校准本来就是IP自动完成，不依赖JTAG。当前ROM是等待SRAM magic的JTAG launcher；本轮将正常启动改为自主POST和UART loader，JTAG入口保留为可选恢复，不再是成功路径的前提。

先解决AE350正常UART静默，验证真实时钟、引脚、初始化和收发。用现有JTAG定位，不加额外TX发送器或调试连线。只读写UART寄存器不能代替实际收发验证。

loader检查装载地址、长度、溢出、入口、栈及保留区，拒绝覆盖ROM/SRAM/loader自身或不可用DDR；校验失败、截断、串口超时或中断上传不得执行不完整镜像，应回到可重试状态。20K协议复用不代表沿用20K二进制、CSR、ISA或物理地址；声明AE350镜像ABI与能力。

区分启动短POST与完整诊断：启动POST自行执行关键地址/地址线/少量模式，不能依赖主机触发；全容量、cache、执行、30分钟压力放在显式诊断载荷，不要求每次开机都运行。

先通过SRAM下载的bitstream验证CPU自主ROM/POST/UART loader。该结果不等于断电后持久自举；FPGA配置Flash安装、Flash固件装载与XIP后续独立推进，本轮不因“自举”一词擅自写Flash。

## 6. DMA、CSR 与 XIP：保留后续合同

完整表见[统一地址规划](ae350-address-map.md)。本轮落实统一描述和扩展规则，必要时实现SYSINFO/诊断基础；不一次性接入全部外设。

- 硬DMAC寄存器0xf0c00000，硬核SPI0xf0f00000，其他硬核外设保持厂商原址。
- 自定义APB CSR：0xf8000000平台基础；0xf8010000 audio、0xf8020000 mic、0xf8030000 HDMI、0xf8040000 SD、0xf8050000 Ethernet、0xf8060000 USB、0xf8070000 WS2812、0xf8080000额外I/O、0xf8090000 Flash/XIP管理。
- AHB数据端点：0xe8000000 audio TX、0xe8010000 mic RX、0xe8020000 SD、0xe8030000 Ethernet可选、0xe8040000 USB可选、0xe8050000 loopback/独立DDR观察。每家族预留64KiB，具体实现按能力表发布。
- DMA buffer与描述符使用DDR物理字节地址，不能把UI字地址或CPU虚拟指针当作DMA地址。共享池是可配置软件分配策略，不是新的MMIO别名；硬DMAC可达性、描述符格式、cache所有权在P3验证。
- HDMI优先专用DDR reader。硬DMAC通道动态分配，不预设全部通道/握手已经可用。不把Extended AHB Master当通用DDR接口。
- XIP预留0x88000000；Flash偏移=CPU地址−XIP基址。容量外报错，不允许24位回绕；物理芯片、片选、容量和分区尚需确认，不继承20K分区，也不覆盖FPGA配置区。
- 保留小ROM时，XIP初步走Customized Instruction Memory的fabric分流；厂商内置Flash模式不是可无条件同时开启的第二窗口。XIP实际实现后置于UART loader，不阻塞本轮启动闭环。
- 地址与能力表生成驱动信息；major用于破坏性地址/语义改变，minor用于兼容追加，构建ID与ABI版本分开。硬件预留不等于功能可用。

## 7. 本轮实施顺序

1. 盘点当前实现与回执，冻结地址描述及受影响的ABI变化，修正旧文档冲突。
2. 完成零基址DDR、高地址SRAM和全部消费者更新，主机/联合仿真验证请求配对、边界、背压和恢复。
3. 400MHz候选达到PnR 0/0，经JTAG验证ROM、SRAM、DDR基本读写及DDR驻留代码交错访问。
4. 恢复正常UART收发；复用20Kloader协议，完成自主短POST、UART接收与payload启动。
5. 在最终布局及启动路径上执行受影响的P2回归，整理准确的通过/失败/未测记录。
6. Flash只读/XIP和P3硬DMAC按后续阶段推进，不扩张成本轮前置要求。

## 8. 验收与证据要求

### 本轮必须完成

- 新地址布局统一、无重叠；DDR零地址、末尾、越界、SRAM/DDR切换有明确结果。
- ROM全固件身份比对、非顺序读回、字节地址、错误响应回归通过。
- 无JTAG写mailbox时，CPU自动完成ROM初始化、短POST并进入UART loader。
- 正常上传/执行成功路径不设置JTAG DPC、不写magic、不依赖JTAG读完成位。
- 用统一入口上传匹配AE350 payload，在DDR执行并经UART返回可核对结果；支持重复上传/重新进入loader。
- 错误校验、非法地址、传输中断、超时后可重试，DDR校准失败仍能进入ROM/SRAM诊断恢复。
- 受影响候选PnR 0/0，保存完整构建/载荷身份、命令、ROM/程序结果与最终驻留状态。

### 继续修正既有P2验收缺口

- 普通load/add/store不记作原子操作通过。对照厂商ae350_atomic及内存属性调查LR/SC/AMO，保留真实失败。
- CPU JTAG abstract-memory不自动等于cache独立外存观察；证明旁路或使用fabric观察器后验收cache。
- 每32B只验证首尾两字不能称全部字节覆盖；报告实际覆盖字节及排除区，按区间发布RAM。
- 修复runner成功状态未保存等问题；不手改历史passed:false回执，生成新的正确结果。
- 30分钟压力保留主机实际起止、错误、覆盖和暂停影响；计时频率未确认前保留原始cycle，不伪造MB/s/ns。
- 旧候选与新布局分别取证；P1恢复和UART状态分别报告，不将历史结果混用。

交付：代码、统一入口、操作文档、更新后的G1–G11报告及JSON、构建/板测原始回执。明确区分已实现且验证、已实现未验证、仅规划和失败项。自主POST/UART loader成功不等于完整P2、XIP或持久冷启动完成。
