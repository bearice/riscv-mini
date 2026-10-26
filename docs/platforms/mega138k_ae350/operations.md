# AE350 平台启动与内存操作

> 适用机器：`mega138k_ae350`。配置以机器 JSON 和所选硬件产物的导出为准。
当前源码使用U4官方0x600000固件分区独立8KiB XIP bootrom、sys50、mem100及DDR CK400。首次SRAM装载前须先安装bootrom，见[启动合同与安装](bootloader.md)。sys50镜像已通过完整STA、bootrom/JTAG/硬件ABI检查、配置Flash逐字节读回及Flash重载启动，见[安装回执](../../../reports/ae350-sys50-board-install.md)。本页既有DMA和外设实板结论对应原sys80组合，不自动延伸到新镜像。

硬核 DMAC 的公共 API、通道/缓存所有权限制及 `board dma-suite` 入口见 [DMA 服务](dma.md)。此前 80/100/400 MHz 运行点已完成轮询式 DMA 和[板级 I/O](io.md)交付；GPIO/fabric 的 PLIC 到 CPU ISR 已验收，DMAC 自身的 CPU ISR 尚未验收。

PT8211 和单只 I2S 麦克风的接线、端点、DMA 所有权及 `board audio-suite` 见[音频与麦克风](audio.md)，验收范围见[音频验收记录](../../../reports/ae350-audio.md)。Linux 的 CPU JTAG 支持 `serial:<FTDI interface serial>`；该选择器与 Gowin FPGA 编程器的 location 分开指定。

准确候选和证据见[最终验收报告](../../../reports/ae350-p2.md)，回归范围见[内存验证合同](memory-validation.md)。

## 内存分工

唯一地址合同为 `gateware/machines/mega138k_ae350/address_map.py`，构建生成 C 头和链接片段。

| 范围 | 实现与用途 |
| --- | --- |
| 0x00000000–0x3fffffff | DDR 零基址窗口，拟发布容量需最终全区间验证 |
| 0x80000000–0x80001fff | 官方Flash XIP bootrom，对应U4物理0x600000的独立8KiB槽，不由位流初始化 |
| 0xa0000000–0xa000ffff | 硬核 ILM；启动时加载 CRC 代码，交接后应用可加载自身 ILM 段 |
| 0xa0200000–0xa020ffff | 硬核 DLM，数据、独立栈和诊断结果；不是 fabric SRAM |
| 0xf8003000 起 | 平台 APB 的 DDR 状态/一致快照，脱离启动存储 |
| 0x80002000–0x8fffffff | SDK Flash其余孔径；有效NOR容量/越界行为未验收 |

没有 fabric 工作 SRAM、JTAG probe 区、magic mailbox 或 ROM launcher。DLM 私有数据为前 32 KiB，结果区起点 0xa0208000、scratch 为 0xa0208600；应用栈 0xa020a000–0xa020dfff，启动栈 0xa020e000–0xa020ffff。DDR 载荷入口 0x3000，最大镜像 0x7d000 字节。

## 自举与时钟

FPGA SRAM 编程后，Gowin DDR3 IP 自动初始化/校准。状态经同步器进入控制域和数据域，看门狗保证校准失败仍可进入启动恢复。CPU 从官方Flash XIP执行，检查 ILM/DLM 配置，做本地 RAM 双极性地址图案测试、清零和代码/data 装载，然后完成短 POST 并进入 UART loader。ECC 检查目前显式关闭，未宣称验证 ECC 纠错。

外设AHB/APB与Flash、数据AHB、DDR UI/CK的频率由机器 JSON 与生成时钟导出定义。运行点及失败边界见匹配验收报告，不从核心配置目标或某个单域最高频率推断整机通过。实现检查同时覆盖 setup、hold 和 minimum pulse width。

读适配将完整 32 B 通过响应 FIFO 一次返回 mem 域，同一合法 AHB burst 的后续 beat 在 mem 域选择数据，不再逐 beat 跨域；UI 域不保留另一套读行复用状态。NONSEQ 不复用前一次块。WRAP、跨块、BUSY、属性/尺寸及固定 burst 长度均有检查。写方向按实际 cache write-back burst 聚合同一 32 B 块，一次完整 32 B 写回对应一条 UI 写。独立写、提前结束会排空；排空期间到达的请求被保留，失败返回 ERROR。提前完成表示写缓冲已接收，不表示 DDR 已完成；通过 fence/排空后观察状态。错误、超时、迟到响应隔离与 CDC 回归继续适用。

DDR 状态页新增只读诊断：偏移 `0x44` 为被接收的读行命中数，`0x48` 为适配器 HREADY 低的 mem 周期数，均为自然回绕的 32 位计数，通过一致快照跨域导出，不在读取时清零，也不改变 image ABI。优化后的候选、三轮性能对照与增量验收范围见[读行优化报告](../../../reports/performance/ae350-read-line.md)。baseline 与 current 是独立显式引用，以本地目录的实际选择为准。

## 唯一装载路径

普通应用和诊断载荷都使用 20K 的 48 字节头、128 字节包、序号 ACK 与 CRC32，通过 UART loader 装载到 DDR。主机预先验证 machine/ABI/产物哈希。诊断参数块的位置由 ELF 导出并保存在 payload artifact 中，主机只填入已声明参数，再生成普通 CRC；不扩展 loader 命令，也不写 JTAG magic。

JTAG 用于 halt、寄存器/内存观察、身份核对及显式恢复。无 JTAG 上传报告 uploaded-and-handoff；明确的执行核验要求 PC 落在本次实际代码镜像内且 mcause=0。板载和独立调试器 location 会随 USB 枚举变化，不能照搬历史数字。

```powershell
.venv/Scripts/python.exe scripts/mini.py build gateware --machine mega138k_ae350 --synthesize
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --source firmware/examples/mega138k_ae350/ae350_payload_demo.c
.venv/Scripts/python.exe scripts/mini.py board run --machine mega138k_ae350 --build <gateware-result.json> --payload <application-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
.venv/Scripts/python.exe scripts/mini.py board ddr-suite --build <gateware-result.json> --payload <diagnostic-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION> --flags 15 --traversal-mb 1 --stress-seconds 2 --poll-interval 1 --timeout 180
.venv/Scripts/python.exe scripts/mini.py board jtag-test --build <gateware-result.json> --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
.venv/Scripts/python.exe scripts/mini.py board recover --machine mega138k_ae350 --build <gateware-result.json> --port COM9 --location <FPGA_LOCATION> --cpu-jtag-location <CPU_LOCATION>
```

日常候选测试使用FPGA SRAM，不写Flash。用户明确要求持久部署时，先用`board bootrom --install`验收bootloader，再用`board update --machine mega138k_ae350 --bootrom-install <成功回执> --cable-serial <序列号>`安装准确配置，二者均保留备份并读回保护区。`ddr-suite` 的 flags 为 1=容量标签、2=遍历、4=cache 观察、8=每块八字。遍历排除完整运行镜像含 data/bss；栈和结果位于 DLM，不必再排除 DDR 栈。局部遍历不冒充全容量验证。cache 观察段在 ILM 中执行，以免取指和栈污染 DDR 计数。

`jtag-test`核对bootrom镜像、非顺序读、本地内存配置、自主POST和实际硬件ABI/时钟，不注入程序、不设 DPC。`recover` 才是显式重启操作。普通应用使用公共 payload BSP 和弱 trap handler，二进制路径归 artifact 所有，不依赖诊断程序。原子指令在 fabric DDR 上不受支持。扩展 AHB 的数据口是 loopback FIFO。硬核 DMAC 已通过它和外部 DDR 口做 DDR 到 DDR、DDR 到 loopback 的传输；DMA_REQ/ACK 是四相握手，满、空、取消和软复位已在 SRAM 编程的位流上核对。官方XIP启动已通过安装/读回/Flash重载门禁，其余Flash孔径边界未验收；HDMI 与专用 scanout reader 的实现和验收见 [视频](video.md)。性能保留原始 cycle，未标定核心时钟前不发布已验证 MB/s。
平台启动固件统一位于 `firmware/bootloader/mega138k_ae350/`，构建身份为 `AE350-ROM-bootloader`；这是后续阶段共用的 ROM bootloader。应用 BSP 与可复用内存诊断为独立产物。恢复使用所选硬件的启动镜像。


## 性能与恢复边界

隔离带宽测试代码位于 ILM，栈和结果位于 DLM，工作集 64 KiB；uncached 和 write-back-cold 各三次 read/write/copy。每次先清理/失效，写与 copy 计时包含末尾 clean/fence。copy 的 logical bytes 是复制长度，实际内存流量另计读加写。UI 命令计数不受 DDR 取指或 DLM 栈污染。依赖加载另保存三个原始周期值。

`--stress-seconds` 是按配置频率生成的 cycle budget，不直接等于真实秒数。回执通过 UART BEGIN/END 标记记录主机单调时间，并扣除 JTAG 暂停。未独立标定 counter 速率时只发布 cycles；本平台性能 baseline 不更新 20K baseline。

应用交接入口不返回；应用可覆盖 ILM。恢复通过启动入口重新初始化 ILM/DLM，不能直接调用已被应用覆盖的 CRC 热代码。任意单侧时钟域热复位不承诺透明恢复；协调复位及异步释放有回归，整个平台恢复使用重配置。

## 专项命令

| 任务 | 命令 | 边界 |
| --- | --- | --- |
| 检查 AE350 CPU/启动镜像 | `./mini.ps1 board jtag-test --build <AE350-run/result.json> --cpu-jtag-location <CPU位置> --location <FPGA位置>` | SRAM 重配置；核对bootrom身份及硬件ABI、本地内存配置与自主 POST；不注入程序或写 magic |
| AE350 上传与执行 | `./mini.ps1 board run --machine mega138k_ae350 --build <gateware-result.json> --payload <payload-result.json> --port COM9` | 自主 POST 后走统一 UART loader；执行核验/重入需显式 CPU JTAG；`passed` 只表示载荷执行，不表示套件通过 |
| AE350 DMA 套件 | `./mini.ps1 board dma-suite --machine mega138k_ae350 --build <gateware-result.json> --payload <dma-suite-result.json> --port COM9 --cpu-jtag-location <CPU位置> --location <FPGA位置>` | 轮询完成字并按结果合同判定；不写 Flash |
| AE350 I/O 套件 | `./mini.ps1 board io-suite --machine mega138k_ae350 --build <gateware-result.json> --payload <io-suite-result.json> --port COM9 --cpu-jtag-location <CPU位置> --location <FPGA位置>` | 判定数字合同；灯、按键和 WS2812 颜色仍须单独观察；不写 Flash |
| AE350 audio/mic 套件 | `./mini.ps1 board audio-suite --build <gateware-result.json> --payload <audio-suite-result.json> --port COM9 --cpu-jtag-location <CPU位置> --soak-seconds 1800 --timeout 1900` | 实时双 DMA 与 CPU DDR 并发，结束后才 halt 读结果；可追加显式 `--capture-device`，见 [音频合同](audio.md)；不写 Flash |


AE350 平台操作合同见 [当前上板说明](operations.md)。普通工作使用 `board run/recover/verify`，诊断使用 `board ddr-suite`；`ae350-upload` 仅保留为兼容别名。上传必须明确传入 `--payload <payload-build-result.json>`，不再自动挑选其他构建的最新载荷。无 JTAG 的上传只证明 ROM 接收与交接，执行核验和拒绝路径测试使用显式 CPU JTAG。完整时序检查包含 setup、hold 和 minimum pulse width。

AE350 ROM bootloader 的源目录为 `firmware/bootloader/mega138k_ae350/`，独立于内存诊断。普通应用和 DDR suite 都经同一 UART loader 装载；`board jtag-test` 只读核验bootrom、本地内存、硬件ABI和POST，`board recover` 执行显式恢复。

## 原生 SD 只读检查

原生 4-bit PHY/Core、硬核 DMAC FIFO、接线与限制见 [SD](sd.md)。`board sd-suite --build <gateware-result.json> --payload <sd-payload-result.json> --port <UART> --cpu-jtag-location <CPU-JTAG>` 执行 SRAM/UART 装载与只读扇区检查，不写卡。独立主机 LBA0 CRC32 可通过 `--expected-sector-crc` 传入；未提供时只记录内部读一致性，不记为主机参考校验通过。


## 用户 Flash 与 TWI 管理总线

接线、硬核 BSP 与边界见 [管理总线](management.md)。`board bus-suite --build <gateware-result.json> --payload <bus-suite-result.json> --port <UART> --cpu-jtag-location <CPU-JTAG>` 只验证用户 Flash 读取与 TWI mux/EEPROM 连通性，UART 完成前不暂停 CPU；不写 Flash 或 EEPROM 数据。
