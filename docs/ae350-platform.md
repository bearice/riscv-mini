# AE350 平台启动与内存操作

硬核 DMAC 的公共 API、通道/缓存所有权限制及 `board dma-suite` 入口见 [DMA 服务](ae350-dma.md)。当前 80/100/400 MHz 运行点已完成轮询式 DMA 和[板级 I/O](ae350-io.md)交付；GPIO/fabric 的 PLIC 到 CPU ISR 已验收，DMAC 自身的 CPU ISR 尚未验收。

准确候选和证据见[最终验收报告](../reports/ae350-p2.md)，回归范围见[内存验证合同](ae350-memory-validation.md)。

## 内存分工

唯一地址合同为 `gateware/machines/mega138k_ae350/address_map.py`，构建生成 C 头和链接片段。

| 范围 | 实现与用途 |
| --- | --- |
| 0x00000000–0x3fffffff | DDR 零基址窗口，拟发布容量需最终全区间验证 |
| 0x80000000–0x80007fff | 厂商 ITCM，32 KiB BSRAM，位流初始化启动镜像；没有自定义 ROM AHB 桥 |
| 0xa0000000–0xa000ffff | 硬核 ILM；启动时加载 CRC 代码，交接后应用可加载自身 ILM 段 |
| 0xa0200000–0xa020ffff | 硬核 DLM，数据、独立栈和诊断结果；不是 fabric SRAM |
| 0xf8003000 起 | 平台 APB 的 DDR 状态/一致快照，脱离启动存储 |
| 0x88000000–0x8fffffff | XIP 预留，尚未实现 |

没有 fabric 工作 SRAM、JTAG probe 区、magic mailbox 或 ROM launcher。DLM 私有数据为前 32 KiB，结果区起点 0xa0208000、scratch 为 0xa0208600；应用栈 0xa020a000–0xa020dfff，启动栈 0xa020e000–0xa020ffff。DDR 载荷入口 0x3000，最大镜像 0x7d000 字节。

## 自举与时钟

FPGA SRAM 编程后，Gowin DDR3 IP 自动初始化/校准。状态经同步器进入控制域和数据域，看门狗保证校准失败仍可进入启动恢复。CPU 从初始化 ITCM 执行，检查 ILM/DLM 配置，做本地 RAM 双极性地址图案测试、清零和代码/data 装载，然后完成短 POST 并进入 UART loader。ECC 检查目前显式关闭，未宣称验证 ECC 纠错。

外设 AHB/APB 和厂商 ITCM 为 80 MHz；数据 AHB 为独立 100 MHz；DDR UI 100 MHz，物理 CK 400 MHz。同一 P3 位流上 mcycle 约为 800.5 MHz。把 sys 提到 100 MHz 时，STA 给出 sys Fmax 84.8 MHz、451 个 setup 终点，并且 `u_vc_ilm_ram` 高脉冲只有 0.221 ns，要求 0.900 ns；同一次 PLL 下 mem 时钟 Fmax 也降到 86.7 MHz。因此 80 MHz 是当前 ITCM 块 RAM 的通过点，不只是已退役监视器的历史配置。数据口 133.333 MHz 曾在全时序上同时失败最小脉宽和 setup，不能把单域目标或该失败点说成组合上限。最终候选要求 setup/hold 和 minimum pulse width 全通过。仅验收 DDR CK 400 MHz，不重新扫低频。

读适配将完整 32 B 通过响应 FIFO 一次返回 mem 域，同一合法 AHB burst 的后续 beat 在 mem 域选择数据，不再逐 beat 跨域；UI 域不保留另一套读行复用状态。NONSEQ 不复用前一次块。WRAP、跨块、BUSY、属性/尺寸及固定 burst 长度均有检查。写方向按实际 cache write-back burst 聚合同一 32 B 块，一次完整 32 B 写回对应一条 UI 写。独立写、提前结束会排空；排空期间到达的请求被保留，失败返回 ERROR。提前完成表示写缓冲已接收，不表示 DDR 已完成；通过 fence/排空后观察状态。错误、超时、迟到响应隔离与 CDC 回归继续适用。

DDR 状态页新增只读诊断：偏移 `0x44` 为被接收的读行命中数，`0x48` 为适配器 HREADY 低的 mem 周期数，均为自然回绕的 32 位计数，通过一致快照跨域导出，不在读取时清零，也不改变 image ABI。优化后的候选、三轮性能对照与增量验收范围见[读行优化报告](../reports/performance/ae350-read-line.md)。原性能 baseline 保留；该候选尚未提升 catalog 的 `current`。

## 唯一装载路径

普通应用和诊断载荷都使用 20K 的 48 字节头、128 字节包、序号 ACK 与 CRC32，通过 UART loader 装载到 DDR。主机预先验证 machine/ABI/产物哈希。诊断参数块的位置由 ELF 导出并保存在 payload artifact 中，主机只填入已声明参数，再生成普通 CRC；不扩展 loader 命令，也不写 JTAG magic。

JTAG 用于 halt、寄存器/内存观察、身份核对及显式恢复。无 JTAG 上传报告 uploaded-and-handoff；明确的执行核验要求 PC 落在本次实际代码镜像内且 mcause=0。板载和独立调试器 location 会随 USB 枚举变化，不能照搬历史数字。

```powershell
.venv/Scripts/python.exe scripts/mini.py build gateware --machine mega138k_ae350 --synthesize
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --source firmware/examples/ae350_payload_demo.c
.venv/Scripts/python.exe scripts/mini.py board run --machine mega138k_ae350 --build <gateware-result.json> --payload <application-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
.venv/Scripts/python.exe scripts/mini.py board ddr-suite --build <gateware-result.json> --payload <diagnostic-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION> --flags 15 --traversal-mb 1 --stress-seconds 2 --poll-interval 1 --timeout 180
.venv/Scripts/python.exe scripts/mini.py board jtag-test --build <gateware-result.json> --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
.venv/Scripts/python.exe scripts/mini.py board recover --machine mega138k_ae350 --build <gateware-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
```

所有候选只编程 FPGA SRAM，不写 Flash。`ddr-suite` 的 flags 为 1=容量标签、2=遍历、4=cache 观察、8=每块八字。遍历排除完整运行镜像含 data/bss；栈和结果位于 DLM，不必再排除 DDR 栈。局部遍历不冒充全容量验证。cache 观察段在 ILM 中执行，以免取指和栈污染 DDR 计数。

`jtag-test` 只核对 ITCM 镜像、非顺序读、本地内存配置与自主 POST，不注入程序、不设 DPC。`recover` 才是显式重启操作。普通应用使用公共 payload BSP 和弱 trap handler，二进制路径归 artifact 所有，不依赖诊断程序。原子指令在 fabric DDR 上不受支持。扩展 AHB 的数据口是 loopback FIFO。硬核 DMAC 已通过它和外部 DDR 口做 DDR 到 DDR、DDR 到 loopback 的传输；DMA_REQ/ACK 是四相握手，满、空、取消和软复位已在 SRAM 编程的位流上核对。XIP、HDMI 和专用 fabric reader 仍未验收。性能保留原始 cycle，未标定核心时钟前不发布已验证 MB/s。
平台启动固件统一位于 `firmware/bootloader/ae350/`，构建身份为 `AE350-ROM-bootloader`；这是后续阶段共用的 ROM bootloader。应用 BSP 与可复用内存诊断为独立产物。旧 P1 ROM/SRAM 平台已退役，不再作为恢复目标。


## 性能与恢复边界

隔离带宽测试代码位于 ILM，栈和结果位于 DLM，工作集 64 KiB；uncached 和 write-back-cold 各三次 read/write/copy。每次先清理/失效，写与 copy 计时包含末尾 clean/fence。copy 的 logical bytes 是复制长度，实际内存流量另计读加写。UI 命令计数不受 DDR 取指或 DLM 栈污染。依赖加载另保存三个原始周期值。

`--stress-seconds` 是按配置频率生成的 cycle budget，不直接等于真实秒数。回执通过 UART BEGIN/END 标记记录主机单调时间，并扣除 JTAG 暂停。未独立标定 counter 速率时只发布 cycles；本平台性能 baseline 不更新 20K baseline。

应用交接入口不返回；应用可覆盖 ILM。恢复通过启动入口重新初始化 ILM/DLM，不能直接调用已被应用覆盖的 CRC 热代码。任意单侧时钟域热复位不承诺透明恢复；协调复位及异步释放有回归，整个平台恢复使用重配置。
