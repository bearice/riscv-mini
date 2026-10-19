# AE350 平台分阶段移植与验收计划

更新：2026-10-18。状态：P0主机底座完成，DDR丝印已确认；[P1](ae350-p1-bringup.md)原生ROM/SRAM系统已完成PnR、CPU JTAG与JTAG辅助启动/控制台/trap/恢复验收。timer/IRQ、实际测频与上限压力仍待完成，详见[上板记录](../reports/ae350-p1.md)。FPGA完整part及板卡版本待确认，P0板级身份与P1整体验收尚未完成。目标为Tang Mega 138K Pro，复用构建框架及部分外围核心，替换CPU到总线/内存基础设施，首版不含SharedL2。

视频范围更新：用户明确要求138K使用HDMI输出，暂时搁置LCD相关移植。20K的RGB LCD实现继续保留；138K不迁移面板引脚、背光或触摸功能。P6改为单路HDMI连接器上的TMDS数字视频，首版先做DVI兼容视频，不把HDMI音频/CEC/完整HDMI协议列入通过条件；音频继续用PT8211。

硬核取舍补充见 [GW5AST硬核资源与使用取舍](gw5ast-hard-ip-usage.md)。P1优先硬UART/PLIC/PLMT/PIT和硬GPIO，P3接硬DMAC；不为复用旧代码再建立重复的软UART/timer/GPIO。P4通过BSP适配硬GPIO，必要时复用事件/去抖逻辑，WS2812/mic作为共享core验证点。DDR/HDMI/MAC/USB厂商IP不等于完整硅硬核，仍计入fabric资源和配置闭包。

本计划基于本地原理图、DDR/AE350/LCD/audio/USB/RGMII 示例、芯片数据手册、本地 LiteX 实现，以及 Gowin AE350 官方硬件手册 v1.3.1E。所有次数、时长和吞吐余量均为建议验收门槛，不是已测结果。

本文属于 riscv-mini 项目文档。来源索引中的 `docs/`、`example/` 指外部 [138K 板卡参考资料](../../../TangPrimer-138K/docs/) 与 [配套示例](../../../TangPrimer-138K/example/)，不表示项目内的 docs 目录。

## 1. 资源及约束

| 资源 | 已核实内容 | 本项目使用策略 |
| --- | --- | --- |
| FPGA fabric | GW5AST-138 数据表：138240 LUT4、138240 REG、340 个 BSRAM 合计 6120 Kbit=765 KiB、1080 Kbit SSRAM、298 个 27x18 DSP；16 global clocks；PLL 表列上限 12 且受封装限制 | 资源统计以选定 part 的 PnR 为准；保留时钟/引脚/FIFO余量，不按系列最大值当实际可分配资源 |
| CPU | AE350/A25，官方最高800MHz，RV32含M/A/F/D/C、Sv32、PLIC、PLMT；32KiB I/D cache；ILM/DLM各64KiB | 初期整数ABI，F/D启用单独验收；CPU频率与总线/DDR分别记录，不把800MHz当内存频率 |
| 本地内存与TCM | 硬核ILM/DLM与可选fabric ITCM/DTCM不是同一资源；官方DTCM选项使用BlockRAM | 启动栈优先用已验证本地内存；fabric TCM必须计入BSRAM；禁止双重计算资源 |
| DDR | 用户2026-10-17照片识别两颗SK hynix H5TQ4G63EFR，第二行RDC 152V；对应两片x16组成x32、预期1GiB | 采用Hynix 15行位配置；Micron 512MiB仅保留为历史参考。寻址与时序待P2实测，软件发布容量仍未确定 |
| Flash | 两颗分别接线的128Mbit NOR，各16MiB；模式和用途不同 | 初期只识别/读取；不沿用20K的2MiB应用偏移，不按连续32MiB处理 |
| 时钟 | P16 50MHz；AE350示例PLL参数为core800/AHB100/APB100/RTC10MHz，DDR独立PLL参数和SDC对应400MHz；top存在200/400MHz矛盾注释 | 从PLL参数/约束验证，清理注释；CPU core必须遵守PLL_R[0]/CLKOUT1专用连线要求；按板卡/器件条件定最终频率 |
| 调试 | BL616 JTAG/UART；FPGA RX N16/TX P15 | 保留独立恢复通道；区分FPGA配置JTAG与CPU调试JTAG的路由/开关 |
| GPIO/风扇 | 底板6 LED、4按键、WS2812、PWM/tach风扇口；U4核心按键为1.5V，其他接口逐项确认 | GPIO是跨机器复用试点；阶段1起给风扇确定安全状态并记录供电/散热条件 |
| 音频 | PT8211-S，BCK E22/WS F24/DIN G24，PA_EN H17 | 复用发送/FIFO/静音功能；首个DMAC流外设 |
| 麦克风 | J28 MIC-ARRAY接口，D0..3+BCK+WS，与PMOD0/SSPI复用；接口不等于已装麦克风 | 先单数据线/单麦，再扩展；确认外接模块电源与IO电平，禁止与PMOD/SSPI争用 |
| HDMI视频 | `simple_video_out/svo`已有双路TMDS输出与640x480/60模式；`cam2hdmi_alt`已有DDR framebuffer与720p参考 | 首版只启用一路TX，先独立测试图，再DDR扫描；确认实际TX接口、差分对、时钟/serializer约束和与RX的复用 |
| LCD | 板上仍有RGB FPC与面板示例 | 138K移植暂缓，不作为首版验收项 |
| SD | 4位SDIO+detect，底板存储页有M.2/扩展信号复用 | 先原始扇区只读，再指定测试文件写读；不把SD等同于硬核内置外设 |
| Ethernet | RTL8211FI RGMII，PHY参考时钟由FPGA提供，strap含延迟与地址1 | 先双向PIO验证PHY/MAC，再接ring或DMAC；现有UDP发送示例不是完整验收 |
| USB | SOFT_USB为FPGA RC网络，配套为Device；BL616调试USB另一路 | Device与Host分开里程碑；键鼠Host必须确认PHY/供电/下拉/连接器能力后实施 |
| 扩展 | PCIe、2路SFP+、第二路HDMI输出/视频输入、MIPI/DVP、M.2、ADC、EEPROM、外部MS5351、PMOD/SDRAM排针 | 不纳入首个功能版本；未来逐接口立项，不因板上存在连接器而全启用 |

重要接口方向：官方 Extended AHB Master 允许 fabric 访问AE350内部ILM/DLM，不能把它视作通用DDR入口。FPGA访问DDR需要独立共享控制器端口/仲裁，或由硬核DMAC经外设FIFO搬运。厂商另有 Shared DDR3: Logic R/W Mode，默认关闭、用户数据宽32/64bit；与本地 customized external-DDR 示例属于不同集成方式。

## 2. 首版架构选择与资源预算

默认集成候选为AE350硬核适配 + 显式外设桥 + 独立Gowin DDR IP/AHB接入，参考本地customized demo，并由构建框架管理其完整配置。不先引入GW5DDRPHY训练迁移，也不保留旧L2。启动/调试优先SRAM/JTAG/可验证ROM stub；未获明确Flash授权不写NOR。

阶段3选择并冻结DDR共享实现：优先在独立DDR控制器前安排明确的CPU/fabric访问仲裁；若对照实验表明厂商Shared DDR模式更可靠或更简单，则显式变更机器配置并记录原因，不能混用两套参数/拓扑。阶段3以前不承诺专用视频reader已具备DDR通路。

BSRAM用于小ROM/必要TCM、FIFO、CDC、调色板和描述符；framebuffer、PCM环、采样块、网络数据放DDR。ILM/DLM的访问与初始化独立确认；DMAC可达性不能从CPU可访问性推断。现有LCD代码仅评估提取像素格式、调色板、framebuffer读取与换帧等通用逻辑，不直接搬入固定480x272的面板scanner。

建议初始预算：audio FIFO 2–8KiB、mic FIFO 2–8KiB、视频预取FIFO 8–32KiB，网络packet FIFO/descriptor按实测增加；功能增量FIFO/表格先控制在128KiB以内，不含实际DDR IP/boot所用BSRAM。预算是设计起点，资源验收看真实PnR，无额外需求不扩大FIFO。

HDMI建议先640x480约60Hz跑通，再以1280x720/60作为提高分辨率的独立目标。RGB565 framebuffer：640x480单帧614400B/双帧1228800B，720p单帧1843200B/双帧3686400B；60Hz有效像素读取分别36.864MB/s与110.592MB/s。720p INDEX8为55.296MB/s，RGB888为165.888MB/s。内存RGB565/INDEX8可在输出前转为RGB888，TMDS编码宽度不强制framebuffer使用RGB888。上述不含总线/刷新开销，TMDS像素与串行时钟另按所选模式计算。48kHz stereo PCM16为192000B/s。FIFO大小按实测最长服务空窗核算，不能仅看DDR峰值带宽。

保留DMAC通道池：首轮仅使用1路memcpy，之后audio TX/mic RX各验证一路；视频/SD/网络是否占用通道由阶段3/7决定。8个扩展握手对不等于8个永远可独占的空闲通道，也不替代总线带宽和调度。

### 频率上限测试

最新实施交接见[统一地址与自主UART loader任务](ae350-next-stage-agent-task.md)：先复用20K ROM上传协议完成AE350自主短POST/UART payload加载，Flash/XIP实现后置；正常成功路径不依赖JTAG。

2026-10-18 最新决定：P2 的 DDR 仅以400MHz CK为强制交付频点；100/200/300MHz保留历史诊断，不再补测或要求通过。下文CPU/总线上限验证独立适用，DDR逐档扫描要求由本决定取代。下一版DDR零基址、fabric SRAM 0x40000000、ROM 0x80000000，见[P2任务书](ae350-p2-task.md)。

CPU与DDR不承担旧20K的60MHz sys/120MHz DDR CK兼容要求。新平台按规格上限建立候选profile并进行上限表征，低频profile用于故障定位和扫频对照，不作为移植完成的性能目标。规格上限、PnR通过上限、实板稳定上限、最终完整系统运行频率分别记录；未达到目标时保留失败证据和限制因素，不能用低频功能通过替代上限测试。

官方AE350硬件手册MUG1031 v1.3.1E表4-1/4-2给出的时钟边界如下；实际profile还需满足具体part/speed grade、PLL、DDR器件、控制器/PHY和板级条件的共同限制。

| 时钟 | 手册边界 | 测试及集成要求 |
| --- | --- | --- |
| CPU core | 最高800MHz | 以800MHz为规格内目标；遵守PLL_R[0]/CLKOUT1专用路径，使用本地ILM/DLM隔离DDR影响 |
| AE350 `DDR_CLK` | 严格低于200MHz | 这是数据内存总线时钟，不能当作外部DDR颗粒CK；独立登记实际连接与频率 |
| AHB/APB | 均严格低于200MHz；同步1:1 | 从工具/IP允许的PLL点向上测试，不把200MHz本身列为合法目标；不能任意独立分频AHB/APB |
| `DDR3_MEMORY_CLK` | 不高于400MHz | 外部DDR3 CK，目标400MHz对应800MT/s；更换控制器/PHY后须重新核定支持范围，不以系列宣传的更高速率替代该接口规格 |
| `DDR3_RW_CLK` | 不低于DDR3 CK的1/4 | 是fabric读写参考时钟，400MHz CK对应至少100MHz；合法上限及比例还需符合具体IP配置 |
| `DDR3_CLK_IN` | 50MHz | 按所选集成方式提供参考输入 |

手册允许core、数据内存时钟与AHB/APB组之间异步；硬核内部跨域能力不能替代fabric边界的CDC设计。RTC另按手册32.768kHz要求核对，不能把示例10MHz连线直接作为规格。timer、UART、SD、音频DDS与boot delay按各自真实源时钟配置，并生成软件可读取的频率描述，不能共用一个含糊的sys_hz。

执行顺序与证据：

1. **P1 CPU上限**：先建立800MHz候选，必要时用工具支持的较低点定位，再逐级逼近规格上限；固定其余时钟，以本地内存中的整数/分支/访存压力和cycle counter对独立时间基准验证实际core频率。硬核core上限由硅规格及实测约束，fabric PnR通过本身不能证明core稳定。
2. **P2 DDR上限**：固定已验证CPU/总线条件，按控制器/DRAM支持的合法档位测试至400MHz CK；每档重新核算tCK、CL/CWL、刷新和PHY参数，执行本阶段复位/校准、容量/数据模式、cache及30分钟压力验收，记录校准失败和首个错误。断电冷启动仅在用户明确要求时附加。x32/800MT/s的原始峰值为3.2GB/s，实测吞吐另列，不能以原始峰值作有效带宽通过线。
3. **P3 总线与组合**：在严格低于200MHz的合法点表征数据内存总线、AHB/APB及fabric路径，保持AHB/APB 1:1。先固定其他域定位瓶颈，再将各域候选高频组合运行CPU/cache、DMAC与fabric并发压力，复验CDC、背压及缓存所有权。
4. **P9 全系统确认**：在候选最高稳定组合上加入HDMI、audio/mic、SD及已启用外设，完成60分钟混合负载与三轮性能测量。最终频率按完整组合证据确定；若需降频，写明失败域、失败条件与余量，不回退到旧板频率作为默认结论。

每个点保存完整时钟树、PLL/IP参数、DDR timing、SDC/CDC审查、源码与工具身份、PnR结果、实测频率、供电/温度/散热条件、冷启动次数、运行时长及错误/吞吐数据。受限于设备只能覆盖部分环境时，报告明确该环境范围；本计划不把未执行的扫频或稳定性测试标记为通过。

## 3. 分阶段交付与通过门槛

### P0：板卡身份与可复现构建底座

实现入口、引用隔离与当前未完成项见[P0构建底座](ae350-p0-build-foundation.md)。主机检查通过不等于实物身份验收通过。

交付：Board/Machine描述、具体part/修订/DDR装配、引脚冲突表、CPU/IP/工具锁、按机器隔离current/baseline；构建输入覆盖HDL/CST/SDC/IPC/JSON/init和实际修改依赖。

通过：

- 20K旧构建/plan/legacy兼容测试保持通过，现役引用不变。
- `mini.py plan/build`能描述AE350 hard CPU，不要求Vex RTL；不新增公开入口。
- 改AE350 CST/IP会使相关硬件BUILD；无改动REUSE；20K与138K产物不可互换。
- 包含准确板卡事实与实物待确认项；若实物DDR/part未知，可继续主机设计但不得把板级身份验收标为通过。

### P1：最小AE350启动与恢复通道

交付：固定硬核/PLL、UART、最小boot/trap/timer/PLIC、GPIO/风扇安全状态。优先用本地ILM/DLM加小ROM stub，不依赖DDR；本地memory启用与reset向量必须先确认。若厂商原始对照wrapper强制等待DDR，仅将其计为“带DDR的对照”，不冒充独立CPU验收。

通过：

- 目标part编译/PnR成功；setup/hold违例0，真实时钟有约束，无未解释CDC或大范围false path掩盖失败。
- UART 115200双向可靠，启动打印硬件/固件身份；JTAG能恢复、halt/resume、设置断点。
- 100次软复位无卡死；PLMT/PIT与参考时间测量误差在1%内；一次用户IRQ能正确claim/complete，错误访问有可诊断trap。
- 完成上述P1 CPU上限表征并确认core实际频率，不默认读取sys频率；800MHz失败需保存原因，低频通过只证明该profile可用，上限目标仍单列状态。

### P2：DDR与CPU缓存正确性

执行任务、分工边界、量化矩阵与交付合同见 [P2 agent任务书](ae350-p2-task.md)。该任务书为待执行计划，不代表本阶段已经验收。

交付：匹配实物的x32 DDR配置、AHB memory path、校准状态/超时诊断；CPU可在DDR执行程序。暂时没有外设DMA。

通过：

- 按已发布RAM范围分块覆盖全部可用内存，排除并列出运行代码/保留区；walking bit、地址/反码、伪随机模式、byte/halfword/word mask和跨row/bank测试全部0错误。
- 重点测试128/256/512MiB边界及实际最高地址位，排除错误容量镜像/alias；未验证区域不发布为可用RAM。
- cache关闭/打开、数据写回/失效和装载代码后的I-cache同步均正确；整数/atomic最小用例通过。F/D单列后续能力，不凭misa就启用硬浮点应用。
- 至少10次复位/重新校准成功。按仓库SOP，默认不做断电冷启动；用户明确要求后另记冷启动次数。RAM下载试验中断电会清除SRAM位流，重新JTAG下载的启动与Flash独立启动必须区分，后者留待P9。
- 固定条件连续30分钟内存压力0错误，记录读写/copy带宽与latency，不设未测可达的理论吞吐要求。
- 完成上述P2 DDR扫频，分别登记400MHz CK目标、最高通过点及失败点；逐档证据不能由单一低频结果代替。

### P3：互连、硬核DMAC与缓存所有权

地址、CSR、DMA数据端点、共享buffer与兼容合同采用[AE350统一地址规划](ae350-address-map.md)作为待实施方案；沿用硬核DMAC原址，外设控制与数据口分离，不继承20K的CSR编号。

交付：扩展MMIO/IRQ路径、DMA buffer映射/同步接口、测试FIFO/loopback外设、已冻结的fabric DDR访问拓扑。

通过：

- DMAC做至少10000次混合传输：不同长度/对齐/边界、固定/递增地址、链表/循环、双通道竞争；支持组合逐字节匹配，不支持配置明确拒绝，guard区无破坏。
- 在cache开启且故意制造脏行/旧行时验证CPU→DMA与DMA→CPU，交还前后的clean/invalidate与对齐/所有权规则正确；不能仅用cache关闭测试。
- 外设FIFO真实接DMA_REQ/ACK，在背压、空/满、取消、超时、软复位时不重复/丢失已承诺数据；停止后未完成事务排空，内存方可复用。
- 若fabric reader使用独立DDR端口，CPU与其并发读写通过同一测试；若只验证了DMAC，必须把专用reader标为尚未支持。
- 完成上述P3总线扫频及CPU/DDR高频组合验证；独立上限不能直接宣称为组合稳定上限。
- 错误的机器/设备major、MMIO/IRQ绑定、DMA可达范围和required capability有拒绝测试；兼容的可选新增寄存器有接受测试。

P3是进入任何正式DMA外设的门槛，不因GPIO或CPU memcpy可用就跳过。

### P4：共享外设模块与基本BSP

交付：硬GPIO的BSP/driver Adapter、必要事件逻辑及可复用WS2812核心，板级输入/输出映射；最小HAL命令行。硬GPIO满足需求时，不强制实例化旧软GPIO来证明共享。

通过：

- 20K Adapter原有寄存器/事件语义回归通过；138K硬GPIO按HAL功能适配，WS2812/确有必要的事件模块不复制第二份状态机。
- LED逐灯、按键逐键按下/释放、去抖/IRQ清除无残留；WS2812红绿蓝和reset时序检查通过。
- 不存在的开关/设备返回明确能力或unsupported，禁止伪造20K接口读数；新GPIO电压/有效电平与原理图一致。
- 每个新增MMIO/IRQ实例可由机器描述生成软件绑定，驱动不硬编码板脚/PLIC source。

### P5：audio与mic，验证第一条实时DMA链

交付：复用PT8211 TX、I2S RX核心；CSR与数据搬运分离。audio先FIFO/PIO模式对照，再接DMAC；mic先snapshot，再流式采集。J28/PMOD0/SSPI只能由一个已选功能拥有。

通过：

- PT8211 WS左右声道、数据边沿、PCM符号和48kHz采样率正确；采样时钟误差目标<=0.1%，无法达到时明确报告实际值并修改规格，不能冒称48kHz。
- 连续播放30分钟，稳态underrun/error=0；主动断供时静音/计数符合接口，停止/重启100次无重复旧样本或未清DMA。
- mic使用已确认外接模块，单路样本计数/符号/声道正确，刺激能产生可观察响应；snapshot与stream规则分别验证，主动满FIFO能报告overflow并恢复。
- 音频数字时序通过与物理听音分开记录；未接麦克风或没有听音条件时保留对应验收项为待验证，不影响其他已过阶段。

### P6：HDMI视频与图形控制台

交付：单路TX、视频时序/TMDS encoder/serializer、scan/pixel/FIFO/reader/CSR拆分，DDR framebuffer、RGB565/INDEX8、换帧/调色板/回卷按能力启用；恢复BIOS终端显示。640x480/60是建议首模式，720p/60后续单独通过；二者不是用户已指定的分辨率要求。

通过：

- P6a先不依赖DDR，用测试图确认单路TMDS差分对/通道、pixel与serializer时钟及相位/reset、同步极性和完整边界；显示器连续锁定30分钟，无黑屏/闪点。支持HPD检测时验证拔插恢复；没有实现HPD时明确记录重新锁定/复位流程。
- 模拟图案的完整framebuffer与输出像素顺序一致；上下边界/stride/双缓冲/帧首提交不混帧；支持的YWRAP/INDEX8分别测试。
- P6b接入DDR后，目标模式连续30分钟，在CPU内存压力+audio+mic负载下video稳态underflow/address_error=0，audio无underrun；记录最长DDR服务空窗与FIFO最低水位。
- 独立可用读带宽先达到目标有效像素带宽的1.5倍作为准入参考；最终以组合负载下不饥饿为准。RGB565 640x480/60参考55.296MB/s，720p/60参考165.888MB/s；低分辨率通过不代表720p已通过。
- 注入一次长时DDR阻塞能按定义丢帧/恢复，无死锁；物理显示色彩、边界、撕裂/闪烁另做目视验收。TMDS视频通过不等于HDMI音频、EDID协商或CEC通过，这些能力需独立声明。

### P7：SD、文件系统与BIOS运行环境

交付：原始SD读写、FatFs、文件命令、BIOS服务和选定应用；软件按AE350 BSP重新编译，不默认沿用旧payload二进制。

通过：

- 先只读已知扇区与主机参考CRC一致，再仅对专用测试文件做1MiB/16MiB/64MiB写读校验；不默认写原始盘或破坏用户文件。
- 对齐/未对齐调用、跨块读写、超时、取消/卸载与DMA可达性正确；单bit/4bit及频率按已实现范围登记。
- BIOS终端编辑、文件操作、音频/图形调用通过；runtime ABI服务/结构/内存所有权检查通过，缺能力的应用明确拒绝。
- audio+HDMI video+SD连续30分钟，文件CRC正确、实时外围错误0；Doom/GW-BASIC等至少选一个形成可重复功能验收，性能另记。

### P8：网络、USB与Linux分支

这三项分别出具结果，不把全部捆绑为一个不可定位的开关。

- 网络：RTL8211 MDIO ID/协商/时钟/delay正确；PIO双向ARP/ping/UDP通过后接DMA或ring；包长/随机内容/环回收/断链重连通过，30分钟负载无内存损坏。记录吞吐/CPU占用/丢包，首版不要求线速千兆。
- USB：先单独验证Device连接以确认SoftPHY，再决定Host硬件与PHY/供电方案。Device通过不计键鼠Host通过；Host需真实键鼠枚举/输入/断开重连，Hub/LS/HS按实际目标分开。板载BL616能调试不计USB Host完成。
- Linux：沿用独立BSP/设备树/OpenSBI平台，先UART+timer+PLIC+Sv32+initramfs启动并有shell，再逐个启用fb/MMC/网络/audio等。每个驱动验证功能和内核DMA API；用户态内存/文件压力30分钟无oops。新硬件F/D/C在工具链和上下文保存测试后分别开启。

网络/USB未完成不妨碍P7成为裸机首个功能版本；Linux内存启动分支可在P3后开始，但全外设验收依赖相应外围阶段。

### P9：完整组合、性能与持久化

交付：冻结机器配置、准确组件组合、最终bitstream/软件、验收报告、自包含复现包；Flash布局和恢复方案单独产物。

通过：

- 完整组合PnR setup/hold=0，审查时钟/CDC/约束覆盖；所有相关功能通过，固定配置混合负载60分钟无不可解释错误。
- 完成频率上限报告：对照规格上限、各域最高通过点、组合最高稳定点与最终运行点，列明失败边界和环境范围；旧20K频率不构成138K验收限制。
- CPU/cache/memory/io和实际应用各三轮，记录中位数、工具/频率/ISA/显示模式，不直接混比20K不同配置；性能提升不是功能正确性的替代证明。
- release/export/verify与独立路径replay通过；硬核/IP/constraint参数变更会失效正确构建键，软件与机器要求不符会拒绝；跨机器refs不串用。
- 只有用户明确授权后才执行Flash写入。写前核对两颗Flash用途/容量/配置模式/实际bitstream尺寸和partition；写后readback与镜像完全匹配。
- 获授权持久化后核对准确启动身份和短功能自检。用户另行明确要求断电冷启动验收时，执行10次Flash独立启动；未执行物理断电测试时只记录实际完成的复位/回读/启动检查。

## 4. 里程碑与依赖

```text
P0 构建与身份 → P1 硬核/恢复 → P2 DDR/cache → P3 总线/DMAC/共享访问
                                              ├→ P4 GPIO → P5 audio/mic → P6 HDMI → P7 SD/BIOS/应用
                                              ├→ P8 Linux最小启动 → 已验证外围驱动
                                              └→ P8 网络/USB独立分支
P7 + 选定P8目标 → P9 最终组合/性能/可复现归档 → 明确授权后Flash
```

M1=P3：可调试、有DDR、DMA正确的新机器；M2=P7：有图形/音频/存储的裸机系统；M3=选定P8目标：Linux或网络/USB扩展；M4=P9：经过完整组合验收的可交付版本。

所有阶段证据关联machine ID、硬核/IP配置、bitstream SHA、firmware SHA、PnR与测试run。仿真、PnR、数字回读、物理观察和Flash冷启动分别记录；硬件缺失为待验证项，不写成已通过。

## 5. 来源

- 本地 `docs/07_Datasheet/EN/DS1104-1.0E_GW5AST series of FPGA Products Data Sheet-Preliminary.pdf`，资源表1-1及封装限制。
- 本地核心板原理图第7/8页，底板原理图第6/13/14/15/16/17/18页；J28 MIC阵列明确在第16页。
- 本地 `example/ae350_customized_demo` 的顶层、PLL HDL/IPC/SDC和AHB-DDR桥；`ddr_test`两种装配参数、`rgb_screen/800_480_screen` PLL（35MHz，top的10MHz注释不能采用）。
- [Gowin AE350官方功能](https://www.gowinsemi.com/en/support/ip_detail/111/)。
- [Gowin硬件手册v1.3.1E](https://www.gowinsemi.com/upload/database_doc/2725/document_ja/69811021aaeed.pdf)：ILM/DLM与内存系统、DMAC扩展、DDR shared mode、Extended AHB方向、core专用时钟。
- [Sipeed 138K Pro官方资源表](https://wiki.sipeed.com/hardware/en/tang/tang-mega-138k/mega-138k-pro.html)。
- [多平台结构与兼容性提案](multi-platform-architecture-proposal.md)。
