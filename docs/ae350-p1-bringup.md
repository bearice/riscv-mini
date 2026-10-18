# AE350 P1 最小启动

更新：2026-10-18。ROM字节读取已修复；CPU JTAG扫描/暂停/单步/恢复及JTAG辅助的ROM启动、UART回显、ebreak和再次加载恢复已通过。timer/PLIC/IRQ、实际CPU频率与压力表征尚未完成，P1仍不标整体完成。验收记录见[报告](../reports/ae350-p1.md)。

## 当前配置

直接实例化AE350_SOC硬宏，32位ROM端口连接8KiB只读存储器，64位数据AHB连接8KiB SRAM。地址分别为0x80000000和0x00000000；不经过Wishbone桥、通用互连或位宽转换，也不接外部DDR。原生存储从设备包含一个等待周期，支持NONSEQ/SEQ和两周期ERROR响应。ROM没有HSIZE输出，必须对字节地址返回所在的32位字，由硬核选取字节；不能套用32位传输的地址对齐检查。SRAM保留HSIZE、字节使能和自然对齐检查。

PLL输入50MHz，CLKOUT0为100MHz数据内存/AHB/APB，CLKOUT1为800MHz core，位置固定PLL_R[0]。这是P1候选配置，尚未实测CPU频率或完成压力/上限表征；fabric时序通过不证明硬核core频率稳定。RTC由50MHz相位累加器产生标称32.768kHz，实际行为及量化抖动待验证。

U4/LVCMOS15按键为**高电平有效**，来自核心板原理图第7页的下拉及按键上接1.5V。按键、上电计数与PLL lock控制复位；不等待DDR训练。硬UART2接N16/P15，基址0xF0300000，按100MHz源时钟配置115200。编译采用rv32ima_zicsr_zifencei/ilp32；ROM monitor启动输出build ID，提供s状态、回显和e诊断ebreak。timer/PLIC/IRQ例程尚未实现。

CPU JTAG接Dock J23同一偶数列：2/4/6/8依次为TCK/TDI/TDO/TMS，12接GND；TRSTn内部固定为1。引脚避开全部摄像头保留资源。接线、电压、端口占用和OpenOCD目标配置见[CPU在线调试](ae350-jtag.md)。板载USB只接配置JTAG/UART，当前CPU调试需要连接独立调试器。风扇PWM固定高，实际转速/极性尚未验收。

硬UART2直接驱动TX，没有额外发送器或TX接管逻辑。故障定位使用CPU JTAG，正常UART只承担控制台和收发功能验收。

## 操作

所有构建和板测通过mini；Windows使用.venv/Scripts/python.exe。

```text
python scripts/mini.py build gateware --machine mega138k_ae350 --synthesize --purpose p1
python scripts/mini.py board run --machine mega138k_ae350 --build build/runs/<准确构建目录> --port COM9 --location <FPGA位置> --cpu-jtag-location <CPU调试器位置>
python scripts/mini.py board debug-scan --machine mega138k_ae350
```

省略--synthesize只生成RTL/约束。综合候选只有在位流存在、setup/hold均为0后发布；ready_for_execution表示可尝试下载，hardware_qualified仍为false。板测会核对准确产物闭包、FS hash和ROM build ID，运行UART状态、回显、ebreak和SRAM恢复，并独立记录通过/失败。带--cpu-jtag-location时，在每次SRAM下载结束后暂停CPU、完整读回ROM并与所选boot.bin比较，再从0x80000000重启并捕获控制台。ROM不匹配即拒绝重启；操作保留重启前PC和异常寄存器。该流程使用独立JTAG和现有D2XX驱动，不证明上电首轮横幅可由板载USB自动捕获。Flash操作不在本入口支持范围。

debug-scan只检查配置JTAG链及OpenOCD USB访问，不声称已经访问CPU TAP。Windows本次OpenOCD USB打开失败、Programmer可用，完整日志保存于build/runs。没有修改USB驱动。

完整system、HAL/BIOS/OS后端尚未接入。P1余项包括自主启动横幅捕获、重复硬件复位、timer/PLIC/IRQ、实际频率和规格内压力表征。ROM读回、正常主循环、ebreak报告、JTAG恢复和再次SRAM加载恢复已有实板证据。CPU、BSP、原生AHB实现、厂商输入、migen/LiteX实现字节及工具身份均进入构建闭包；138K候选与20K current/baseline隔离。
