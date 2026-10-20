# AE350 物理地址、CSR 与 DMA 规划

更新：2026-10-20。当前平台使用厂商 ITCM 启动镜像、硬核 ILM/DLM 与零基址 DDR，删除 fabric 工作 SRAM、JTAG mailbox 和 launcher。唯一实现合同为 `gateware/machines/ae350_map.py`。SYSINFO、IRQ 聚合、DMA 服务页及 0xe8050000 的 loopback 已实现。硬核 DMAC 位于 0xf0c00000，已在 80/100/400 MHz 运行点完成轮询式功能验收；完整 PLIC 中断服务未验收。公共 API、缓存所有权及使用限制见 [DMA 服务](ae350-dma.md)，结果见 [DMA 验收](../reports/ae350-dma.md)。其余外设槽仍为规划。这里的 CSR 指外设 MMIO；RISC-V CPU CSR 不属于此地址表。

最新启动顺序：先参考20K ROM完成自主短POST与UART payload loader，JTAG仅作可选恢复；Flash/XIP实际实现后置，地址预留保留。启动协议见[bootloader](ae350-bootloader.md)。

## 1. 全局窗口

| 地址范围（含端点） | 用途 | 合同 |
| --- | --- | --- |
| 0x00000000–0x3fffffff | DDR，预期 1GiB | CPU/fabric/DMAC 使用相同的物理字节地址；容量按实测发布，无减 0x2000 的转换 |
| 0x40000000–0x7fffffff | 未映射数据窗口 | 有界 ERROR；无 fabric 工作 SRAM，不映射 DDR 镜像 |
| 0x80000000–0x80007fff | 厂商 ITCM，32KiB | BSRAM 初始化启动镜像，替代自定义 ROM AHB 桥 |
| 0x88000000–0x8fffffff | XIP地址窗口，预留128MiB | 只映射所选Flash已确认容量/分区；实际容量之外有界报错，不镜像 |
| 0xa0000000–0xa000ffff | 硬核 ILM | 启动/应用关键代码，显式复制；DMA 可达性待 P3 |
| 0xa0200000–0xa020ffff | 硬核 DLM | 工作数据、独立栈与保留诊断结果；无 JTAG mailbox |
| 0xc0000000、0xe0000000 等 | 厂商 BMC/译码保留区 | 不安排项目外设 |
| 0xe4000000 | 硬核 PLIC | 沿用厂商定义 |
| 0xe6000000 / 0xe6400000 / 0xe6800000 | PLMT / PLIC软件中断 / Debug Module | 沿用厂商定义 |
| 0xe8000000–0xefffffff | Extended AHB Slave，128MiB | fabric FIFO/数据口、诊断访问；不是第二个 DDR 地址空间 |
| 0xf0000000–0xf0ffffff | 硬核 APB 外设窗口 | 保留厂商寄存器，不在这里分配自定义CSR |
| 0xf8000000–0xfbffffff | Extended APB Slave，64MiB | 自定义平台和外设控制CSR |

SMU 0xf0100000、UART1 0xf0200000、UART2 0xf0300000、PIT 0xf0400000、WDT 0xf0500000、RTC 0xf0600000、GPIO 0xf0700000、I2C 0xf0a00000、**DMAC 0xf0c00000**、SPI 0xf0f00000 保持原址。已关闭的硬核外设不等于这些地址可重新分配。

ROM 保持0x80000000是本项目的启动选择，并非硬核复位向量不可改；SMU HART0_RESET_VECTOR在0xf0100050，文档定义R/W、默认0x80000000，当前配置的可写性及复位保持行为另行验证。

## 2. APB CSR 分配

平台块使用4KiB页；每个外设家族预留64KiB，首版通常只实现首个4KiB。保留空间不消耗对应容量的BSRAM，也不表示已经存在外设。

| 基址 | 预留大小 | 用途 |
| --- | --- | --- |
| 0xf8000000 | 4KiB | SYSINFO：平台ID、ABI、构建ID、能力、设备表入口 |
| 0xf8001000 | 4KiB | 时钟/复位：各域配置频率、测量值/有效位、允许的软件复位 |
| 0xf8002000 | 4KiB | fabric IRQ聚合：pending、mask、ack、来源诊断 |
| 0xf8003000 | 4KiB | DDR/互连诊断：校准、超时、首错、仲裁与观测 |
| 0xf8004000 | 4KiB | DMA fabric服务：端点能力、握手映射、仲裁QoS/错误；不复制硬DMAC通道寄存器 |
| 0xf8005000 | 4KiB | 预留调试控制窗口；不建立第二套启动/装载协议 |
| 0xf8006000 | 4KiB | 性能计数器与一致性快照 |
| 0xf8007000–0xf800ffff | 36KiB | 平台保留 |
| 0xf8010000 | 64KiB | audio TX：格式、时钟、FIFO状态、静音、DMA握手 |
| 0xf8020000 | 64KiB | mic RX：采样格式、FIFO状态、溢出、DMA握手 |
| 0xf8030000 | 64KiB | HDMI：模式、像素格式、framebuffer、stride、换帧和欠载 |
| 0xf8040000 | 64KiB | SD：软控制器CSR、数据/DMA状态 |
| 0xf8050000 | 64KiB | Ethernet：MAC/MDIO/队列与错误；第三方IP私有寄存器在本槽内定义 |
| 0xf8060000 | 64KiB | USB：host/设备控制、队列与错误，按实际后端定义 |
| 0xf8070000 | 64KiB | WS2812及专用波形输出 |
| 0xf8080000 | 64KiB | 额外fabric I/O/PWM/事件；普通GPIO优先使用硬核0xf0700000 |
| 0xf8090000 | 64KiB | Flash/XIP管理：能力、忙状态、地址模式、写擦命令及错误；不作为程序取指地址 |
| 0xf80a0000–0xfbffffff | 其余空间 | 后续外设保留，不映射为RAM或重复别名 |

HDMI CSR控制帧地址，但像素流由fabric专用DDR reader读取；它不要求像素数据每帧经过APB，也不默认长期占用硬DMAC通道。LCD相关槽不建立。

## 3. AHB 数据端点

| 基址 | 预留大小 | 初始定义 |
| --- | --- | --- |
| 0xe8000000 | 64KiB | audio TX FIFO，+0x0000为固定地址32位DATA写端口 |
| 0xe8010000 | 64KiB | mic RX FIFO，+0x0000为固定地址32位DATA读端口 |
| 0xe8020000 | 64KiB | SD数据端点，TX/RX分别+0x0000/+0x0004，实际能力按实现发布 |
| 0xe8030000 | 64KiB | Ethernet可选PIO/FIFO端点；有原生ring时不强制使用 |
| 0xe8040000 | 64KiB | USB可选数据端点；受实际IP接口约束 |
| 0xe8050000 | 64KiB | 内存校验、DMA loopback和独立DDR观察器 |
| 0xe8060000–0xefffffff | 其余空间 | 后续高速数据口保留 |

FIFO DATA地址固定，DMA传输时外设地址不递增；DDR端地址递增。APB CSR与AHB DATA是不同用途，不建立两个有副作用的数据口别名。AHB槽的其余地址保留，未实现的端点/非法方向/非法宽度应有界报错，不能静默丢弃数据。主机不得用读改写或推测访问操作FIFO。

上述地址首先是CPU可编程端点规划。DDR 与 loopback 数据字已由硬核 DMAC 在当前运行点传过数据；其余规划端点仍要各自用实际传输证明，CPU 可达不等于 DMAC 可达。若硬 DMAC 拓扑不支持，保留逻辑设备 ABI 并调整后端或采用 fabric DMA，不伪造已连通状态。Extended AHB Master面向ILM/DLM，不当作通用DDR主接口。

## 4. DMA寄存器、描述符与缓冲区

硬DMAC控制寄存器固定0xf0c00000，内部布局和硬件链表描述符沿用实际Gowin/Andes控制器规范。软件不假设通道步长、描述符格式或所有8通道均已启用；启动读取能力并用驱动适配。平台不得新增第二套通道控制镜像CSR。

DMA buffer/描述符在DDR中分配，描述符里的地址是物理字节地址，不是CPU虚拟指针或DDR UI字地址。CPU/DMAC/fabric对同一buffer必须使用同一地址语义；只有DDR控制器入口做UI字地址换算。

裸机初期可从已验证DDR末端保留**16MiB可配置共享池**。若整1GiB已验收，示例区间为0x3f000000–0x3fffffff；这属于软件内存分配策略，不是固定MMIO，也不是永久硬件ABI。实际池从可用区间计算，容量未确认时不能直接使用这个高地址。OS接管时用其DMA分配与映射接口，禁止与普通堆或设备树可用RAM重复分配。

池内动态分配HDMI framebuffer、audio/mic环、网络/USB队列及描述符，不按外设永久切割固定DDR地址。双缓冲、环长度、descriptor stride等通过驱动或已版本化CSR传递。硬件描述符不复用成跨平台软件ABI；项目HAL暴露方向、buffer、长度、完成/取消等操作。

分配对齐取cache-line与控制器要求的较大值，metadata与设备数据不共用需独立维护的cache line。公共接口仍按非一致 DMA 使用：提交前对范围做 clean，CPU 读设备数据前对范围做 invalidate。当前 A25 运行点的套件结果是：缓存打开后的 store 在 clean 之前已经能被 DMAC 读到，该窗口的 DDR 写计数差为 0，所以这不是脏行写回通过。DMAC 改写后，CPU 要等这条缓存行按地址范围无效才读到新值；单行、连续三行和不满一行都如此。不把这条结果推广成硬件一致性。

硬DMAC通道动态申请：首轮memcpy/loopback，再audio TX和mic RX。SD/网络/USB按后端能力申请；HDMI首选专用reader。物理DMA request/ack编号由vendor配置及端点表生成，不等于固定通道号，不能凭“8通道”预分配全部资源。

## 5. CSR、发现与兼容性

新项目CSR使用32位小端、4字节对齐访问；非法访问有界报错。64位地址/计数使用明确的lo/hi与提交/快照协议，不能靠两次独立读取假定原子性。FIFO和W1C寄存器明确标注副作用，reserved字段按各块合同处理。

项目自有块首32字节统一头：+0x00 DEVICE_ID、+0x04 ABI_VERSION（major/minor）、+0x08 CAPABILITIES、+0x0c CONTROL、+0x10 STATUS、+0x14 IRQ_STATUS（W1C）、+0x18 IRQ_ENABLE、+0x1c LAST_ERROR。具体control位在设备规范中冻结；第三方硬IP保持自身布局，在设备表中声明ABI类型，不强套公共头。

SYSINFO设备表发布：device ID/instance、是否存在、CSR与DATA物理基址/长度、ABI版本、IRQ、DMA请求/方向/宽度能力。表和字串位于静态只读ROM或固定快照存储，避免重新制造大型组合ROM mux。未实例化的设备不要求通过扫MMIO猜测；驱动以设备表为准。

minor只追加兼容能力/寄存器；移动地址、改变位语义、descriptor/所有权协议改变须提升相应major。构建ID区分二进制身份，ABI版本表达软件合同，二者不可互代。旧20K CSR顺序与LiteX bank编号不继承为新平台ABI。

建议fabric逻辑IRQ分配：0=平台/互连错误，1=HDMI，2=audio，3=mic，4=SD，5=Ethernet，6=USB，7=其他I/O，8–15保留。它们是16路扩展输入的逻辑规划，不是PLIC source ID；真实PLIC映射、优先级、level/edge、ack顺序和硬DMAC中断由BSP/设备表生成并验收。

## 6. XIP与Flash地址合同

CPU XIP基址选择**0x88000000**，预留至0x8fffffff（128MiB），处于厂商0x80000000–0x8fffffff指令窗口内。保留0x80000000–0x80007fff的 ITCM 启动镜像，二者不覆盖。未实现区和Flash容量之外的地址不允许回绕。

地址转换固定为：`Flash物理偏移 = CPU地址 - 0x88000000`。默认窗口基址对应所选Flash芯片偏移0；可执行分区的入口为`0x88000000 + 分区偏移 + 镜像入口偏移`。128MiB是地址预留，不是已确认的Flash容量；芯片容量、片选、引脚、3/4字节地址模式均须实测/资料确认。首16MiB之外的访问不得因仍使用3字节命令而回绕。多芯片不默认拼接为一片。

Flash物理分区表是独立的镜像/存储合同，发布在设备表/镜像清单中。FPGA配置区、CPU固件、资源、元数据的归属待按实物布线确认；不继承20K的Flash偏移，不提前指定覆盖FPGA配置区的CPU镜像。未来可安排固件A/B槽，但当前不凭空发布固定分区大小。

当前启用厂商 ITCM 启动后端，XIP 尚未实例化。上述窗口只表达保留的地址语义，不承诺现有指令接口能够直接路由到该地址。实现 XIP 前须核对厂商 ITCM/Customized/Flash 指令存储模式的互斥关系，选择可实现的后端并验证启动、取指与常量数据访问；不得恢复旧自定义 ROM 桥后宣称与当前 ITCM 集成兼容。

Flash/XIP管理CSR预留0xf8090000，由所选后端驱动适配。硬核普通SPI仍在0xf0f00000；它不自动等于专用启动Flash控制器。若使用厂商Flash Register R/W接口，显式桥接其FR_*端口并验证独占/仲裁，不把寄存器接口当成内存取指口。

XIP初版是只读映射，store必须报错，编程/擦除通过显式命令完成。执行Flash擦写、修改读协议/时钟或退出XIP模式前，将执行代码、所需常量和异常/中断处理迁入可用ROM/SRAM/DDR，停止所有共享该Flash的读取；完成后按平台cache维护合同同步指令/数据视图再恢复执行。不能从正在擦写或切换模式的Flash执行命令处理程序。

本轮默认启动为ROM→DDR检查/短POST→UART loader→DDR payload。后续增加Flash启动模式时，再实现ROM→初始化/校验Flash镜像→XIP或复制到DDR执行；XIP可在DDR失败时使用硬核 DLM 工作。保留JTAG恢复能力，本规划不授权Flash写入。

XIP验收：ROM冷/热启动与恢复、只读拒写、随机/跨字取数、代码与rodata、独立镜像读回、地址最高位与容量边界、3/4字节模式、超时/忙状态、cache与fence.i、新镜像执行；物理写擦仅在单独授权后执行。先使用低速保守Flash协议定位，再按该Flash规格和板级信号验证目标速度，DDR仍固定400MHz CK。

## 7. 实施顺序与验收

1. 当前内存布局已采用零基址 DDR、厂商 ITCM 与硬核 ILM/DLM；RTL/C/linker/runner 共用地址合同并检查重叠。SYSINFO、IRQ、DMA 服务和 loopback 数据字已发布；XIP 与其余外设槽仍为预留，实例化后才发布存在和能力。
2. P3a 已实例化 SYSINFO、DDR/互连诊断、AHB loopback 数据字，并用主机仿真检查 APB/AHB 宽度、错误、请求电平和空洞拒绝。硬核 DMAC 在当前运行点已有 DDR 与 loopback 传输的实板证据；公共接口、脏行写回和停止后安全复用尚未签收。
3. 硬DMAC做DDR memcpy、DDR↔loopback、方向/固定递增地址、边界/取消/超时，再接audio TX与mic RX；每一步记录真实可达性。
4. 加cache维护/独立外存观察、并发与中断测试；确认描述符和buffer所有权，任何失败不得破坏 ITCM/DLM/JTAG 恢复。
5. HDMI验证专用reader与CPU/DMAC共享仲裁、带宽与欠载；后续SD/网络/USB逐个启用。所有新布局和仲裁组合仅在400MHz CK交付点完成所需复验，不重新要求低频扫档。

依据：[Gowin MUG1029 表3-1、第14章DMAC](https://www.gowinsemi.com/upload/database_doc/2688/document/67dd35b63d214.pdf)、[MUG1031 表3-3及扩展接口](https://www.gowinsemi.com/upload/database_doc/2725/document_ja/69811021aaeed.pdf)。厂商窗口为文档事实；本项目细分地址、共享池与IRQ槽为规划，均需按阶段实施和验收。
