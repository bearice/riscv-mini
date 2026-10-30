# AE350 物理地址、CSR 与 DMA 规划

> 适用机器：`mega138k_ae350`。配置以机器 JSON 和所选硬件产物的导出为准。

更新：2026-10-26。当前源码使用配置 Flash U4 官方 0x600000 固件分区独立 XIP bootrom、硬核 ILM/DLM 与零基址 DDR。唯一地址合同为 `gateware/machines/mega138k_ae350/address_map.py`，板级引脚与极性来自 `board.json`。硬核 DMAC 位于 0xf0c00000，已在 80/100/400 MHz 完成轮询式验收。硬 GPIO 输入、WS2812 和板级事件页、GPIO/fabric PLIC→CPU ISR 及 DMA/DDR 并发已通过，见[板级 I/O](io.md)。LED 使用板级输出寄存器。风扇调速/停转不在本次交付中。DMA 所有权与限制见[DMA 服务](dma.md)。其余外设槽仍为规划；这里的 CSR 指 MMIO，不是 RISC-V CPU CSR。

启动顺序：官方Flash Instruction Memory→U4固件区XIP→硬ILM/DLM初始化→自主POST→UART payload loader；JTAG用于观察和恢复。启动协议见[bootloader](bootloader.md)。

## 1. 全局窗口

| 地址范围（含端点） | 用途 | 合同 |
| --- | --- | --- |
| 0x00000000–0x3fffffff | DDR，预期 1GiB | CPU/fabric/DMAC共享零基址物理字节地址；容量按实测发布 |
| 0x40000000–0x7fffffff | 未映射数据窗口 | 有界 ERROR |
| 0x80000000–0x809fffff | Flash 固件区 | 只读 XIP，对应 U4 物理 0x600000 至器件末尾 |
| 0x80a00000–0x8fffffff | 官方 Flash 指令窗口的其余部分 | 当前软件不使用，容量之外/回绕行为未验收 |
| 0xa0000000–0xa000ffff | 硬核 ILM | 启动/应用关键代码，显式复制；DMA 可达性待 P3 |
| 0xa0200000–0xa020ffff | 硬核 DLM | 工作数据、独立栈与保留诊断结果 |
| 0xc0000000、0xe0000000 等 | 厂商 BMC/译码保留区 | 不安排项目外设 |
| 0xe4000000 | 硬核 PLIC | 沿用厂商定义 |
| 0xe6000000 / 0xe6400000 / 0xe6800000 | PLMT / PLIC软件中断 / Debug Module | 沿用厂商定义 |
| 0xe8000000–0xefffffff | Extended AHB Slave，128MiB | fabric FIFO/数据口、诊断访问 |
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
| 0xf8007000 | 4KiB | 片内温度与实验栅极/测速接口。调速、停转暂缓，默认栅极持续拉低；温度为毫摄氏度 |
| 0xf8008000–0xf800ffff | 32KiB | 平台保留 |
| 0xf8010000 | 64KiB | audio TX：格式、时钟、FIFO状态、静音、DMA握手 |
| 0xf8020000 | 64KiB | mic RX：采样格式、FIFO状态、溢出、DMA握手 |
| 0xf8030000 | 64KiB | HDMI：模式、像素格式、framebuffer、stride、换帧和欠载 |
| 0xf8040000 | 64KiB | SD：软控制器CSR、数据/DMA状态 |
| 0xf8050000 | 64KiB | 用户 Flash U7：fabric QSPI 控制器 |
| 0xf8060000 | 64KiB | USB：host/设备控制、队列与错误，按实际后端定义 |
| 0xf8070000 | 64KiB | WS2812 控制器，前 4KiB；数字时序通过，物理观察单独留证 |
| 0xf8080000 | 64KiB | 板级 LED 输出、按键事件与诊断边沿，前 4KiB；按键电平接硬 GPIO 输入 |
| 0xf8090000 | 64KiB | Flash/XIP管理：能力、忙状态、地址模式、写擦命令及错误；不作为程序取指地址 |
| 0xf80a0000 | 4KiB | Ethernet：PHY 管理、MAC 状态与 DDR DMA 描述符环；实板 RX CRC 尚未解决 |
| 0xf80a1000–0xfbffffff | 其余空间 | 后续外设保留 |

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

当前 audio/mic 的 APB 控制页各实现前 4 KiB，AHB 仅实现表中固定的 32 位 DATA 地址，其他保留地址返回 ERROR。导出的 REQ 线号和 DMAC selector 是两个字段：loopback REQ[0]/[1]→selector 0/1，audio REQ[2]→selector 9，mic REQ[3]→selector 10。后两项已按请求门开关和真实传输核对，不能把 REQ 线号直接写入 SrcReqSel/DstReqSel；端点表末字与设备 CSR 发布实际 selector，参与机器 image ABI。

## 5. CSR、发现与兼容性

新项目CSR使用32位小端、4字节对齐访问；非法访问有界报错。64位地址/计数使用明确的lo/hi与提交/快照协议，不能靠两次独立读取假定原子性。FIFO和W1C寄存器明确标注副作用，reserved字段按各块合同处理。

项目自有块首32字节统一头：+0x00 DEVICE_ID、+0x04 ABI_VERSION（major/minor）、+0x08 CAPABILITIES、+0x0c CONTROL、+0x10 STATUS、+0x14 IRQ_STATUS（W1C）、+0x18 IRQ_ENABLE、+0x1c LAST_ERROR。具体control位在设备规范中冻结；第三方硬IP保持自身布局，在设备表中声明ABI类型，不强套公共头。

SYSINFO设备表发布：device ID/instance、是否存在、CSR与DATA物理基址/长度、ABI版本、IRQ、DMA请求/方向/宽度能力。表和字串位于静态只读ROM或固定快照存储，避免重新制造大型组合ROM mux。未实例化的设备不要求通过扫MMIO猜测；驱动以设备表为准。

minor只追加兼容能力/寄存器；移动地址、改变位语义、descriptor/所有权协议改变须提升相应major。构建ID区分二进制身份，ABI版本表达软件合同，二者不可互代。旧20K CSR顺序与LiteX bank编号不继承为新平台ABI。

实际 IRQ 路由由机器实现和导出定义。GPIO/fabric 的 PLIC→CPU ISR 合同见 [板级 I/O](io.md)，不采用早期阶段规划的逻辑槽号。

## 6. XIP与Flash地址合同

CPU复位与固件链接入口为 **0x80000000**，对应U4物理偏移 **0x600000**。固件可增长到Flash末尾，当前16 MiB器件的可用固件容量为10 MiB。采用官方 SPI Flash Instruction Memory + Flash Register R/W Mode。SDK指令孔径为0x80000000–0x8fffffff；软件按实际Flash容量限制镜像，孔径其余地址的回绕/错误行为以专项实测为准。

U4物理分区为配置位流[0,0x600000)、固件[0x600000,Flash容量末尾)。FR管理页位于0xf8090000，内部FR地址按0xf8000000+offset转发；SYSINFO使用独立页。U7接0xf0f00000硬SPI。

## 实现与验证范围

已实现的设备地址、通道与所有权见 [DMA](dma.md)、[I/O](io.md)、[音频](audio.md) 和 [视频](video.md)。其他预留槽不发布为已存在设备。地址范围不表示整段 DDR 容量已通过板测；验证边界见 [内存合同](memory-validation.md)。

独立 bootrom 的最小合同发现寄存器位于 SYSINFO `+0x0c/+0x10/+0x14`，分别为启动合同版本、sys/APB Hz、当前硬件 image ABI，均只读。安装和验收状态见 [bootloader](bootloader.md)。
