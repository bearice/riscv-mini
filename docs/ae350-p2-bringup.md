# AE350 P2 启动与内存操作

2026-10-19。当前工作按 [P2 内存系统交付合同](ae350-p2-memory-acceptance.md) 进行，进度与准确候选见 [内存结构整改报告](../reports/ae350-p2-memory-redesign.md)。旧候选的板测不自动证明新结构通过。

## 内存分工

唯一地址合同为 `gateware/machines/ae350_map.py`，构建生成 C 头和链接片段。

| 范围 | 实现与用途 |
| --- | --- |
| 0x00000000–0x3fffffff | DDR 零基址窗口，拟发布容量需最终全区间验证 |
| 0x80000000–0x80007fff | 厂商 ITCM，32 KiB BSRAM，位流初始化启动镜像；没有自定义 ROM AHB 桥 |
| 0xa0000000–0xa000ffff | 硬核 ILM；启动时加载 CRC 代码，交接后应用可加载自身 ILM 段 |
| 0xa0200000–0xa020ffff | 硬核 DLM，数据、独立栈和诊断结果；不是 fabric SRAM |
| 0xf8003000 起 | 平台 APB 的 DDR 状态/一致快照，脱离启动存储 |
| 0x88000000–0x8fffffff | XIP 预留，尚未实现 |

没有 P2 fabric 工作 SRAM、JTAG probe 区、magic mailbox 或 ROM launcher。DLM 私有数据为前 32 KiB，结果区起点 0xa0208000、scratch 为 0xa0208600；应用栈 0xa020a000–0xa020dfff，启动栈 0xa020e000–0xa020ffff。DDR 载荷入口 0x3000，最大镜像 0x7d000 字节。

## 自举与时钟

FPGA SRAM 编程后，Gowin DDR3 IP 自动初始化/校准。状态经同步器进入控制域和数据域，看门狗保证校准失败仍可进入启动恢复。CPU 从初始化 ITCM 执行，检查 ILM/DLM 配置，做本地 RAM 双极性地址图案测试、清零和代码/data 装载，然后完成短 POST 并进入 UART loader。ECC 检查目前显式关闭，未宣称验证 ECC 纠错。

CPU 目标 800 MHz（尚未独立标定）；数据 AHB 为独立 100 MHz；外设 AHB/APB 和 ITCM 80 MHz；DDR UI 100 MHz，物理 CK 400 MHz。最终候选要求 setup/hold 和 minimum pulse width 全通过。仅验收 DDR CK 400 MHz，不重新扫低频。

读适配保留完整 32 B，只对同一合法 AHB burst 分片返回；NONSEQ 不复用前一次块。WRAP、跨块、BUSY、属性/尺寸及固定 burst 长度均有检查。写方向仍逐 beat，写聚合与后续流水尚待实现。错误、超时、迟到响应隔离与 CDC 回归继续适用。

## 唯一装载路径

普通应用和诊断载荷都使用 20K 的 48 字节头、128 字节包、序号 ACK 与 CRC32，通过 UART loader 装载到 DDR。主机预先验证 machine/ABI/产物哈希。诊断参数块的位置由 ELF 导出并保存在 payload artifact 中，主机只填入已声明参数，再生成普通 CRC；不扩展 loader 命令，也不写 JTAG magic。

JTAG 用于 halt、寄存器/内存观察、身份核对及显式恢复。无 JTAG 上传报告 uploaded-and-handoff；明确的执行核验要求 PC 落在本次实际代码镜像内且 mcause=0。板载和独立调试器 location 会随 USB 枚举变化，不能照搬历史数字。

```powershell
.venv/Scripts/python.exe scripts/mini.py build gateware --machine mega138k_ae350 --ae350-profile p2 --synthesize
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --ae350-profile p2
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --ae350-profile p2 --source firmware/examples/ae350_payload_demo.c
.venv/Scripts/python.exe scripts/mini.py board run --machine mega138k_ae350 --build <gateware-result.json> --payload <application-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
.venv/Scripts/python.exe scripts/mini.py board ddr-suite --build <gateware-result.json> --payload <diagnostic-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION> --flags 15 --traversal-mb 1 --stress-seconds 2 --poll-interval 1 --timeout 180
.venv/Scripts/python.exe scripts/mini.py board jtag-test --build <gateware-result.json> --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
.venv/Scripts/python.exe scripts/mini.py board recover --machine mega138k_ae350 --build <gateware-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
```

所有候选只编程 FPGA SRAM，不写 Flash。`ddr-suite` 的 flags 为 1=容量标签、2=遍历、4=cache 观察、8=每块八字。遍历排除完整运行镜像含 data/bss；栈和结果位于 DLM，不必再排除 DDR 栈。局部遍历不冒充全容量验证。cache 观察段在 ILM 中执行，以免取指和栈污染 DDR 计数。

`jtag-test` 只核对 ITCM 镜像、非顺序读、本地内存配置与自主 POST，不注入程序、不设 DPC。`recover` 才是显式重启操作。普通应用使用公共 payload BSP 和弱 trap handler，二进制路径归 artifact 所有，不依赖诊断程序。原子指令在 fabric DDR 上不受支持；XIP、DMAC、HDMI 和共享仲裁后置。性能保留原始 cycle，未标定核心时钟前不发布已验证 MB/s。
平台启动固件统一位于 `firmware/bootloader/ae350/`，构建身份为 `AE350-ROM-bootloader`；这是后续阶段共用的 ROM bootloader。`p2` 仅为当前硬件集成 profile，应用 BSP 与阶段诊断载荷不属于启动固件。
