# AE350 CPU 在线调试

当前硬件使用Dock J23的SDRAM扩展排针接出CPU JTAG。引脚依据配套`TANG_MEGA-138K_Pro-Dock-4071f_Schematics.pdf`第16页；该页标题栏标注Dock-4071d Rev1.11。这是GPIO到AE350调试接口的连接，不新增JTAG控制器。板载USB继续负责FPGA配置和UART。

| 调试信号 | J23接点 | FPGA球位 | 相对FPGA方向 |
| --- | ---: | --- | --- |
| TCK | 2 | V16 | 输入 |
| TDI | 4 | V17 | 输入 |
| TDO | 6 | Y21 | 输出 |
| TMS | 8 | U17 | 输入 |
| GND | 12 | 板地 | 共地 |

信号采用LVCMOS33。需要VTref的调试器可从J24的1/2脚取3.3V参考；它是电平参考，不应由调试器向已供电板卡反向供电。**J23第11脚是5V，不能接3.3V JTAG的VTref。** 接点编号以原理图和板上1脚标记为准，不能凭排针朝向猜测。

全部五根线使用J23的同一偶数列，沿脚号递增按RV-Debugger Plus丝印顺序TCK、TDI、TDO、TMS排列，10脚留空，12脚接GND。这组信号占用SDRAM扩展模块的数据脚；调试时不装SDRAM扩展模块。它与核心板上的两颗DDR3独立。Dock实际摄像头接口的MIPI/DVP和控制/I2C引脚登记为保留资源，JTAG构建禁止占用。核心板上的MIPI_EXIO等网名不能直接作为摄像头占用依据，须追踪Dock实际走线；其中接到J23的SDRAM信号属于可分配扩展资源。TMS/TDI配置上拉，TCK配置下拉；TRSTn在内部固定为1，不分配排针，JTAG状态机通过TMS复位。

用户已有Sipeed RV-Debugger Plus。按表同名接TCK/TDI/TDO/TMS及GND，不按UART交叉。板卡与调试器各自供电，RV-Debugger Plus的3V3和5V供电输出均不接。

2026-10-17用户确认辅助UART按调试器丝印连接如下，继续沿J23同一偶数列排列：

| 调试器信号 | J23接点 | FPGA球位 | 相对FPGA方向 | I/O Bank | Dock原功能 |
| --- | ---: | --- | --- | ---: | --- |
| TX | 14 | R23 | 输入，FPGA RXD | 3 | SDRAM_D12 |
| RX | 16 | T25 | 输出，FPGA TXD | 3 | SDRAM_D10 |
| DTR | 18 | P25 | 输入 | 3 | SDRAM_D8 |
| RTS | 20 | V23 | 输入 | 2 | SDRAM_CLK |

这四脚不与上述CPU JTAG或Dock实际摄像头接口重叠。DTR/RTS均是调试器输出，只能接FPGA输入，尚未赋予复位或硬件流控功能。当前RTL仅接出N16/P15的硬UART2，上表辅助UART尚未实例化，接线核对不代表串口功能已验收。

核心板原理图第1页确认Bank3供电3.3V；Dock原理图第2页确认Bank2默认由R250（0Ω）接3.3V，R251（1.8V选路）不装。RV-Debugger Plus手册标注TX/RX/DTR/RTS默认3.3V，因此默认装配下四脚电平匹配，后续约束应使用LVCMOS33。Bank2可通过电阻改装为1.8V，以上结论基于默认装配，尚未测量实板电压。P25虽有VREF复用功能，此处作为普通数字I/O使用。JTAG的四根信号同属Bank2，也依赖其3.3V供电。

参考：[RV-Debugger Plus用户手册](https://dl.sipeed.com/fileList/Accessories/RV-Debugger%20Plus/RV%20Debugger-Plus_user_guide_v1.0.pdf)。UART与JTAG共用J23.12地线；J23.11为5V电源，不参与这些信号接线。

CPU TAP与FPGA配置TAP没有在当前网表中串链。当前资料尚未确认GW5AST有可用于CPU调试的用户扫描隧道；不能把工具目录里的GW_JTAG声明当作本器件已支持的证明。当前只接板载USB时，不能通过J23直接执行CPU halt/step。

目标配置为[debug/mega138k_ae350.cfg](../debug/mega138k_ae350.cfg)。它仅描述CPU TAP，使用本机Gowin OpenOCD的`target/nds32v5.cfg`中5位IR、0x1000563d作为待实测参考。外部调试器的型号、驱动和adapter配置需匹配实际设备。连通后的首轮验收：扫描CPU ID、halt、读PC/mcause/mepc/mtval/SP、比较ROM内容、单步、resume，并保留完整OpenOCD日志。

Windows当前板载下载器使用FTDI驱动；本机Gowin OpenOCD的FTDI后端实测报`LIBUSB_ERROR_NOT_FOUND`，而Gowin Programmer可以扫描和下载。没有修改USB驱动。该错误发生在USB打开阶段，不能据此判断CPU TAP是否正常。

2026-10-17按用户要求仅接入FPGA板载USB做身份基线扫描：VID:PID为0403:6010，USB序列号2025030317；通道A序列号2025030317A（COM7）为FPGA JTAG，通道B序列号2025030317B（COM9）为板载UART。Gowin在2MHz下扫描到单个ID 0x0001081B；该ID由GW5AS/GW5AST-138B/C共用，不确认具体封装和速度等级。收据为`build/runs/20261017T101457035844Z-board-ae350-fpga-usb-identify/result.json`，未写SRAM或Flash。本次location为6705，重插后可能变化；后续第二台调试器接入时按新增设备及独立序列号区分，不能仅凭0403:6010选择。

独立RV-Debugger Plus正常枚举的USB parent序列号为FACTORYAIOT_PRO，当前A/B串口为COM10/COM4，额外UART无需插入即可使用JTAG。其D2XX通道序列返回A/B，本轮A通道location107569；不可把它与FPGA下载通道6705混用，也不可固定location跨重插使用。现有FTDIBUS驱动已通过D2XX直接MPSSE测试：CPU ID连续三次0x1000563d、IR Capture=1、BYPASS正确，DTMCS=0x7071；halt、读取DPC/SP/mcause/mepc/mtval/misa、单步和resume已通过，见[当前板测](../reports/ae350-p1.md)。OpenOCD/GDB入口尚未打通，无需为使用当前D2XX路径而更换驱动。

2026-10-18已将D2XX ROM验证/重启纳入统一板测入口：`mini.py board run --machine mega138k_ae350 --build <准确run> --port COM9 --location <FPGA位置> --cpu-jtag-location <CPU位置>`。recover和verify同样支持该参数。后端为scripts/mini_ops/ae350_jtag.py，使用Programmer目录的ftd2xx.dll，检查CPU TAP身份，暂停并完整读回所选ROM，与boot.bin比较后才设DPC=0x80000000并恢复执行。每次SRAM加载分别记录ROM匹配、重启前异常寄存器和恢复状态；ROM不匹配不会改PC重启。正常UART用于功能验收，已移除额外UART诊断发送器。

板卡断电或重启后必须先通过FPGA配置JTAG重新加载上述J23接线对应的SRAM候选，再扫描CPU TAP。没有该设计时，J23未必连接AE350调试口，读到全1不能判断接线损坏。
