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
| 0xa0000000–0xa000ffff | 硬核 ILM | 启动/应用关键代码，显式复制；DMA 可达性待 P3。不可关闭、不可移动，按虚拟地址匹配，见第 7 节 |
| 0xa0200000–0xa020ffff | 硬核 DLM | 工作数据、独立栈与保留诊断结果。不可关闭、不可移动，按虚拟地址匹配，见第 7 节 |
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

## 7. DDR 软件布局与 OS 交接

所有固件、OS 装载和诊断使用的地址都定义在 `address_map.py` 的 `DDR_LAYOUT` 和 `OS_*` 常量中，`check()` 统一检查，不另写常量。生成物：C 头 `ae350_address_map.h`（BIOS、驱动、探针）、DTS 头 `ae350_dts.h`（无 C 整数后缀，供 dtc 预处理）、OpenSBI 的 `FW_TEXT_START/FW_JUMP_ADDR/FW_JUMP_FDT_ADDR`、Linux 配置检查和 `board linux` 的装载地址与 OpenSBI banner 核对。

| 名称 | 范围 | 存活阶段 | 用途 |
| --- | --- | --- | --- |
| bios.image | 0x00003000–0x0007ffff | bios, app, diag | ROM 装载镜像（BIOS 或诊断载荷）的代码与数据 |
| bios.bss | 0x08400000，8MiB | bios, app, diag | 同一镜像的 `.bss`（含 libc 4MiB heap），由生成的 `ae350_map.ld` 的 `bss` 段链接 |
| bios.net | 0x00100000，64KiB | bios, app, diag | TFTP 以太网描述符与收发缓冲 |
| bios.app | 0x01000000–0x07dfffff | app | BIOS 装载的应用 |
| bios.staging | 0x02000000，16MiB | bios | 持久更新暂存 |
| bios.scanout0/1 | 0x08000000 / 0x08100000，各 1MiB | bios, app, diag | BIOS 私有 scanout 双缓冲 |
| bios.fb0/fb1 | 0x08200000 / 0x08300000，各 1MiB | bios, app, diag | 应用画布 |
| diag.memory | 0x00200000，2MiB | diag | DDR 诊断：pattern/perf/stress 工作区、RMW 字、cache 线、观测线、代码替换桩 |
| diag.dma | 0x00400000，4MiB | diag | DMA 套件缓冲；按 4MiB 对齐以保留套件内跨页/跨线偏移 |
| diag.io | 0x00800000，1MiB | diag | I/O 套件 DMA/CPU 并发缓冲 |
| diag.audio | 0x00900000，1MiB | diag | 音频 TX/RX DMA 块（两侧 guard）与 64KiB CPU 并发缓冲 |
| diag.video | 0x00a00000，3MiB | diag | 视频夹具 FB0/FB1（`DIAG_VIDEO_FB0/FB1`，两侧 guard）与 CPU stress 缓冲 |
| diag.sd | 0x00d00000，2MiB | diag | SD 读速缓冲（两侧 guard） |
| diag.eth | 0x00f00000，1MiB | diag | 以太网/SFP 诊断描述符环与收发缓冲 |
| os.kernel | 0x00400000，12MiB | bios, os | Linux Image（4MiB 对齐） |
| os.sbi | 0x01000000，1MiB | bios, os | OpenSBI（fw_jump 或 fw_payload 的固件部分），自然对齐 2 的幂 |
| os.uboot | 0x01100000，4MiB | bios, os | fw_payload 内的 U-Boot（TEXT_BASE、初始栈在区间顶）；BIOS `boot sd` 把 fw_payload.bin 连续装入 os.sbi+os.uboot |
| os.dtb | 0x03200000，64KiB | bios, os | Linux DTB |
| os.firmware-fdt | 0x03210000，64KiB | os | OpenSBI 传给 U-Boot 的控制 FDT（`FW_FDT_PATH`，Next Arg1） |

规则：同一阶段存活的区间不得重叠，不同阶段可复用地址（如 bios.app 与 os.sbi）。diag 阶段是 ROM 直接装载的诊断载荷（代替 BIOS 运行；链接 BIOS 服务的载荷同时使用其缓冲），每个套件只用自己的 diag.* 区间，源码以 `AE350_DIAG_*` 加偏移寻址并用 `_Static_assert` 约束在区间内；主机判定（如视频 stress 的活动帧地址）也从同一合同取值。几何、容量和全遍历测试把 DDR 当作被测对象逐字访问，不属于预留。`ae350_software_layout_test` 会拒绝诊断与示例源码中的字面指针常量和合同已定义的外设基址。旧的载荷链接脚本把 `.bss` 硬编码在 0x80000–0x7fffff，未进入合同：BIOS 的 libc heap 因此与 bios.net 重叠，TFTP 装载 os.kernel 也会覆盖 heap 尾部、GW-BASIC 变量表和填充栈；现由 bios.bss 收口并参与重叠检查。OS 内存为 DDR 前 256MiB。`tftp` 只接受这些装载区间。`boot opensbi` / `boot linux` 交接前关闭 MIE、清 mie，并停止以太网 DMA（`ERR NET STOP` 即中止），防止 BIOS 拥有的 DMA 在 OS 运行后继续写内存；交接时关闭 cache。

Linux Sv32 虚拟布局：PAGE_OFFSET=0xc0000000（`CONFIG_PAGE_OFFSET`，构建时检查），线性映射 0xc0000000 起；vmalloc（含 ioremap、模块）为 0xa0000000–0xbfffffff；fixmap、PCI IO 和用户空间都在其下方。

**ILM/DLM 与内核虚拟地址**：A25 的 milmb/mdlmb 使能位和基址只读（实测 0xa0000001/0xa0200001，JTAG 写入不生效），本地存储器无法关闭或移动。它们按**虚拟地址**在地址转换前匹配：PLIC 曾被 ioremap 到 0xa0000000，Linux 写入的 priority/enable/threshold 全部落进 ILM/DLM，物理 PLIC 保持复位值，UART 中断从不触发（JTAG 回执 `20261102T103316896730Z-board-ae350-jtag`）。因此合同定义 `OS_RESERVED_VA` = [ILM 基址, DLM 末端) = 0xa0000000+0x210000，要求它位于 vmalloc 内并完整覆盖两个窗口；Linux 以 `CONFIG_RISCV_RESERVED_VA_START/SIZE`（`firmware/linux/ae350/patches/10-reserved-kernel-va.patch`）在 vmalloc 初始化前把该范围永久占用。线性映射或 vmalloc 以下（用户/fixmap）若与本地存储器窗口相交，检查直接报错，因为那部分无法保留。M 模式（BIOS、OpenSBI）不经转换，仍可按物理地址使用 ILM/DLM。

**PLIC 源号**：EXT_INT[n] 对应 PLIC 源 n+10（`PLIC_EXT_INT_SOURCE_BASE`），由板级 I/O 实测 EXT_INT[7]=17 推出；UART2 为源 9。除 UART 外，DTS 中使用 fabric 中断的节点仍为 disabled，各自的源号启用时逐个实板确认。

## 实现与验证范围

已实现的设备地址、通道与所有权见 [DMA](dma.md)、[I/O](io.md)、[音频](audio.md) 和 [视频](video.md)。其他预留槽不发布为已存在设备。地址范围不表示整段 DDR 容量已通过板测；验证边界见 [内存合同](memory-validation.md)。

独立 bootrom 的最小合同发现寄存器位于 SYSINFO `+0x0c/+0x10/+0x14`，分别为启动合同版本、sys/APB Hz、当前硬件 image ABI，均只读。安装和验收状态见 [bootloader](bootloader.md)。
