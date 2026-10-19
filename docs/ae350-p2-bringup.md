# AE350 P2 当前运行合同

更新：2026-10-19。本文只保留当前操作；历史试验见 reports/ae350-p2.md，独立复核与修复见 reports/ae350-review-acceptance.md。旧的 `board ddr` 和旧低地址 SRAM 布局已退役。

地址、保留区与 ABI 由 `gateware/machines/ae350_map.py` 定义。构建在缓存查询之前把它纳入输入闭包，生成 C 头和链接片段。

| 区域 | 地址 | 用途 |
| --- | --- | --- |
| DDR | 0x00000000–0x3fffffff | 1 GiB 物理窗口，CPU 字节地址等于 DRAM 偏移 |
| SRAM | 0x40000000–0x40001fff | 启动数据、mailbox、结果、探针与独立启动栈 |
| ROM | 0x80000000–0x80001fff | 入口在 0x80000000；末尾 64 字节保留给状态和诊断 |
| XIP | 0x88000000–0x8fffffff | 仅预留，尚未实现 |

SRAM mailbox 为 0x40000800，结果区为 0x40000900，参数区为 0x40000f00，探针区为 0x40001000–0x400017ff，栈为 0x40001800–0x40001fff。DDR 参数为 0x2000，载荷入口为 0x3000，载荷启动栈顶为 0x80000。链接器与构建检查保留区和 ROM 最大长度 0x1fc0。

## 时钟、复位和跨域

P2 fabric、CPU 数据口、AHB/APB 使用地址合同中的 `FABRIC_HZ`，当前为 **80 MHz**；CPU 目标仍为 800 MHz，DDR CK 仍为 **400 MHz**，DDR UI 为 100 MHz。80 MHz 是包含最小脉宽检查的交付配置，不代表平台上限。先前 100 MHz 候选虽然 setup/hold 为 0/0，原始 STA 的 BSRAM minimum pulse width 仍失败，不能作为完整时序通过。

UART 分频、0.25 秒校准看门狗和构建元数据跟随 fabric 配置。DDR 校准与 PLL 状态经两级同步进入 fabric；CPU 在同步后的校准完成或看门狗超时后释放。诊断多位数据经 request/acknowledge 握手形成一致快照，不能直接读取 DDR 域的活动计数器。快照有更新延迟，观察测试必须等待跨域往返。

DDR IP 为 Gowin 1:4、32 位 DQ、BL8、CL6/CWL5 配置；两颗 H5TQ4G63EFR x16 合成 1 GiB。引脚与 IP 原始来源见 vendor 目录的 PROVENANCE.json。跨域请求/响应使用真实 AsyncFIFO。适配器仍是每笔 AHB beat 一次 32 字节 UI 命令，没有行缓存、burst 合并或多笔并行；这些性能限制独立于本次正确性修复。

未校准、越界、未对齐和不支持的传输尺寸返回两周期 ERROR。已提交的读若超时，UI 保留该事务的所有权并吞掉迟到数据；恢复期间不启动新的 DDR 命令。无法恢复时锁定到 DEAD，每笔后续事务仍返回有界两周期 ERROR，直到复位。互连已接受的下一地址不能在恢复期间丢失。

## 自主启动与 UART

复位后 ROM 自主完成 SRAM 图案、ROM 摘要、DDR 校准状态和少量 DDR 字检查，然后打印身份与 POST 结果并等待上传。ROM 摘要不是外部期望值比较，也不是全容量内存测试。DDR 不可用时保留 SRAM/JTAG 恢复能力并拒绝 DDR 上传。

loader 复用 20K 的 48 字节头、128 字节包、序号 ACK 和 CRC32，另校验 AE350 ABI、固定入口及大小范围。`SYSTEM READY` 表示 ROM 接收完成并交接；只有明确的执行证据才能报告执行成功。无 CPU JTAG 的普通上传仅报告 `uploaded-and-handoff`。带执行核验时要求 PC 在本次实际载荷字节范围内且 mcause=0。

## 正式操作

```powershell
.venv/Scripts/python.exe scripts/mini.py build gateware --machine mega138k_ae350 --ae350-profile p2 --synthesize
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --ae350-profile p2
.venv/Scripts/python.exe scripts/mini.py board run --machine mega138k_ae350 --build <gateware-result.json> --payload <payload-result.json> --port COM9 --location 6705
.venv/Scripts/python.exe scripts/mini.py board verify --machine mega138k_ae350 --build <gateware-result.json> --payload <payload-result.json> --port COM9 --location 6705 --cpu-jtag-location 107569 --suite firmware
.venv/Scripts/python.exe scripts/mini.py board ddr-suite --build <gateware-result.json> --payload <payload-result.json> --location 6705 --cpu-jtag-location 107569 --flags 15 --traversal-mb 1 --stress-seconds 2 --region-kb 64 --poll-interval 2 --timeout 180
.venv/Scripts/python.exe scripts/mini.py board recover --machine mega138k_ae350 --build <gateware-result.json> --port COM9 --location 6705 --cpu-jtag-location 107569
```

每次必须明确选择 payload；不会按跨机器的文件修改时间猜测“最新”。先验证 artifact 清单、文件哈希、machine、ABI 和参数，再获得硬件会话锁。串口在 SRAM 编程前打开，以捕获自主启动 banner。会话统一关闭串口、日志和 CPU JTAG，清理失败会使回执失败。这里只编程 FPGA SRAM，不写 Flash。

`ddr-suite` flags：1=容量标签、2=遍历、4=缓存观察器、8=遍历每块八字（否则每块首尾两字）。遍历从零基址开始，排除参数、整个运行镜像（含 data/bss）及保留栈；输出实际覆盖字节。1 MiB 或 64 MiB 的局部回归不能称为 1 GiB 全遍历。

汇总需要显式候选与载荷：

```powershell
.venv/Scripts/python.exe scripts/ae350_report.py --build <gateware-result.json> --payload <payload-result.json>
```

没有对应回执的项目保持 untested；不会从一次 22 项冒烟推断容量、遍历、缓存或长时间压力全部通过。原子指令支持、DMA、XIP 和 CPU 实测频率仍各自需要对应证据。


## 独立用户程序

`build payload` 不指定源码时构建 P2 诊断；指定 `--source` 或 `--source-dir` 时构建独立应用。公共启动代码、弱 trap handler 与链接脚本在 `firmware/bsp/mega138k_ae350_payload/`，不再由诊断目录拥有。支持声明的 include 目录、额外源文件、宏和优化级别；不支持的 libc 或 ISA/linker 覆盖会明确拒绝。

```powershell
.venv/Scripts/python.exe scripts/mini.py build payload --machine mega138k_ae350 --ae350-profile p2 --source firmware/examples/ae350_payload_demo.c --define AE350_DEMO_VALUE=0x24681357u
.venv/Scripts/python.exe scripts/mini.py board run --machine mega138k_ae350 --build <gateware-result.json> --payload <application-result.json> --port COM9 --location 6705 --cpu-jtag-location 107569
```

应用二进制由 artifact 的 `config.binary` 指定；主机不再固定打开 `tests.bin`。`ddr-suite` 只接受 purpose=P2-diagnostics，不能把普通应用当内存诊断执行。应用源码变化只改变 payload 输入键，ROM/gateware 保持复用；这已由源变更回归和实板示例证明。

性能采样输出原始 mcycle，不计入功能检查数；CPU 时钟没有独立标定时不把它换算成已验证带宽。`nonatomic.rmw` 只验证普通读改写，不表示 LR/SC/AMO 受到支持。
