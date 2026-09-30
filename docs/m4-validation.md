# M4 并发稳定性验证

日期：2026-09-30 至 2026-10-01。状态：最终固件启动和五次软件复位通过，用户要求的 5 分钟自动并发验收通过（实际 390.344 秒 / 246 轮），两块 LCD 已由用户确认正常稳定。自动测试和人工画面确认分别记录，不能用启动成功代替长测。

## 当前验收配置

- VexRiscv lite RV32IM，sys/CPU 60 MHz，2 KiB I-cache，无 D-cache/L2，无 IRQ。
- H5TQ1G63EFR-PBC / 128 MiB，DDR CK 120 MHz（240 MT/s），DLL-off，CL6/CWL6，ODT disabled。M0–M3 保留历史 48/96 MHz 配置。
- RGB LCD 480×272 / RGB565 / 9 MHz / 59.94006 Hz，两个 DDR 帧槽、8 KiB DMA FIFO，HDMI 延后。
- ROM 48 KiB，SRAM 16 KiB；固件 33,136 字节，SRAM BSS 2,248 字节。Gowin BSRAM 映射已用满 46 块；扩大缓存/FIFO前需调整存储分配或将 monitor 移到 DDR。
- UART 115200 8N1。SPI LCD 与 SD 工作 6 MHz，SD 初始化 400 kHz；分频和超时由 CONFIG_CLOCK_FREQUENCY 计算。

CPU 和显示共享一个物理 128-bit LiteDRAM native 端口。调度器只允许一笔物理事务在途；读回数据锁存到独立寄存器，保留到对应消费者 ready。显示请求优先，FIFO 背压/帧结束时服务 CPU；事务之间留 8 个 sys 周期。CPU 保留 32→128-bit Wishbone 转换，不增加数据缓存。这个选择减少 72 MHz 下的仲裁组合路径；原双物理端口候选在 PnR 出现 43 个 setup 违例，未下载。

LCD enable 跨域后只在扫描帧边界启用像素消费；disable 立即禁止消费并排空已经发起的一帧。软件复位先停止并排空显示，再重新训练 DDR，避免使用初始化期间的内存内容。

## 负载和判定

memcopy 测试区为 0x40400000..0x40407fff 和 0x40408000..0x4040ffff，每区 32 KiB。每次以 timer 为 seed，写入 seed ^ (word_index * 0x9e3779b9)，先逐字检查源区，再复制到目标区，并以独立计算的预期值逐字检查目标区。错误报告源/目标阶段、偏移及预期和实际值；不覆盖 framebuffer、DDR 程序/栈区或 SPI LCD 图案区。

scripts/stress_test.py 默认运行 300 秒（用户于 2026-10-01 将本轮验收要求改为 5 分钟）。Windows 上测试期间调用 SetThreadExecutionState 阻止系统自动休眠，退出时释放请求，不永久修改电源计划。每轮四次内存复制，只读校验现有 RVTEST00.BIN（4096 字节，CRC32 08040e1e），重绘 SPI LCD，切换 RGB LCD 帧，再检查 DMA 整帧模和、active、frames、completed 和 underflows。RGB LCD DMA 持续独立运行；CPU 的复制、SD 和 SPI 命令轮流执行。每条命令截止时间 15 秒，失败立即停止并保存原始日志及失败状态。

每帧期望模和：槽 0 为 80d32504，槽 1 为 82c2e704。模和在 native DMA 输出处计算，是像素完整性的辅助检查，不是 CRC，也不检测像素重排。SD 测试另外逐字节核对图案和 CRC32；内存复制逐字核对独立预期值。欠载必须始终为 0，扫描/完成计数必须增长，帧槽必须交替，串口必须返回提示符。

logical_memory_access_bytes 是写源、验源、读源、写目标、验目标五次软件访问的总字节数，不是实测 DDR 总线带宽。日志中的命令时间包含 UART 传输和主机读取等待；不能直接当作纯 CPU 执行时间。copy_ticks 才是固件计数器耗时，以 60 MHz 换算。

## 排查结论与边界

96 MHz CK DLL-off 的 M3 配置在显示持续运行和 CPU 内存复制交错时复现数据错误：可读到色条像素值、相邻字或字节混合，同时 underflow 仍可为 0。停止并排空 DMA 后，错误有时仍持续。单独调整 PHY/crossbar 延迟、DQS 读窗口、tCCD/tWTR、视频限流和单端口调度，均未稳定消除 96 MHz 下的错误；不把这些试验当作修复。

144 MHz CK DLL-off 试验虽在短测中通过逐字复制、帧模和、SD CRC 和零欠载，但其 6.944 ns 周期低于 DLL-off 的 8 ns 下限，亦被排除。当前配置为 CK 120 MHz（8.333 ns），已完成用户要求的 5 分钟自动并发验收。保留原 DDR profile 的读写间隔，不把更大 tCCD/tWTR 当作解决方案。尚未确认底层采样失效的确切机制，也未证明它与此前人工观察到的复位后条纹完全相同。

144 MHz DLL-on 曾通过短测，但本地 docs/07_Chip_manual/sk_hynix.pdf 第 28/31 页对 CL6/CWL5 的 tCK 范围为 2.5–3.3 ns；144 MHz 对应 6.944 ns，因此该试验被排除，日志不计入验收。低频候选继续使用 CL6/CWL6 的 DLL-off 路径，并按 8 ns 周期下限约束频率；[Hynix 同系列完整时序表](https://www.alldatasheet.net/html-pdf/534213/HYNIX/H5TQ1G63DFR/23994/150/H5TQ1G63DFR.html)和 JEDEC DDR3 表均列出该下限。本地 EFR 简版资料未列完整 DLL-off AC 表，因此保留实际颗粒训练/长测与温度边界限制。DLL-off 的 DQS 与控制器时域对齐必须上板验证，可参考 [Hynix DDR3 DLL-off 操作说明](https://www.alldatasheet.net/html-pdf/534213/HYNIX/H5TQ1G63DFR/8794/55/H5TQ1G63DFR.html)（系列操作规则，不能替代本板颗粒的实测）。

本次只覆盖软件复位和室内单板运行，未验证断电冷启动、物理按钮复位、温度/电压边界。SD 应用加载器、IRQ/RTOS 尚未实现；当前已完成 M4 的自动并发稳定性部分。SD 全程只读，FPGA 仅 SRAM 下载，未写 Flash。

## 复现和证据

```powershell
. .\scripts\env.ps1
& $MiniPython .\scripts\build.py --stage m4 --synthesize
& $MiniPython .\scripts\board_test.py --stage m4 --program --port COM4 --location 107569 --soft-resets 5
& $MiniPython .\scripts\stress_test.py --seconds 300
& $MiniPython .\sim\test_memory.py
& $MiniPython .\sim\test_spi.py
& $MiniPython .\sim\test_video.py
```

fboff / fbon 是 M4 的 DMA 停止/恢复对照命令；fbpattern 只替换输出像素来源，DMA 仍运行。最终验收使用 fbmemory。

build/m4/validation.json 保存构建哈希、资源和时序；hardware-validation.json 保存下载、启动和软件复位结果；stress-validation.json、stress-cycles.jsonl、stress-uart.log 保存正式压力结果。各结果关联同一 bitstream SHA256，脚本启动前核对现有位流与构建/上板验收报告，禁止把旧位流结果用于当前构建。

sim/test_memory.py 在真实调度器上验证双调用方、延迟的单周期返回、返回后立即变化的数据总线、消费者背压、写数据及字节使能和事务隔离；test_spi.py 验证 SPI 事务；test_video.py 覆盖正常扫描和一次欠载后的帧边界恢复。仿真不验证真实 DDR PHY 或 LCD 连接器电气时序。

## 主机休眠中断记录

第一次正式长测完成 293 轮后，Windows 因 System Idle 自动休眠。Kernel-Power 42 / Power-Troubleshooter 1 记录休眠为 2026-09-30 15:05:43 UTC、唤醒为 15:35:37 UTC。休眠前已复制 38,502,400 字节、读取 SD 1,200,128 字节，欠载为 0；唤醒后 fbinfo 仍可响应且欠载为 0。该次 UART 超时属于主机中断，不能把休眠时间计入 30 分钟验收。证据保存在 build/m4/host-sleep-interrupted-*。增加进程内防休眠后从零重跑。运行超过 5 分钟后，用户将要求调整为 5 分钟并要求停止，按最后完整轮的数据验收。原 1800 秒请求的报告快照另存 stress-original-1800-request-validation.json；最终报告保留 criterion_revision 和 stop_reason，不能描述为 30 分钟通过。

## 最终自动验收结果（2026-10-01）

连续完成 390.344 秒（6 分 30.344 秒），超过用户要求的 300 秒；246 完整轮全部通过。原测试进程按用户要求中止，最终报告基于中止前已经持久化的完整轮及原始 UART/JSONL 核对生成；未计入未完成命令。脚本后续默认 300 秒。

| 项目 | 结果 |
| --- | --- |
| DDR 拷贝及独立逐字校验 | 984 次 × 32 KiB = 32,243,712 字节（30.75 MiB），无错误 |
| 五次软件访问总字节数 | 161,218,560 字节（153.75 MiB）；不是实测 DDR 总线带宽 |
| SD 只读检查 | 246 × 4096 = 1,007,616 字节，全部 CRC32 08040e1e |
| SPI LCD 重绘 / RGB 换帧 / 整帧模和检查 | 各 246 次通过 |
| DMA 完成计数增长 | 23,396 帧；每轮抽查模和，不宣称逐帧验全部模和 |
| 显示欠载 | 0 |
| 启动 / 软件复位 | 启动通过，5/5 次软件复位通过 |
| Gowin 时序 | setup 0 / hold 0 违例 |
| Logic / Register / BSRAM | 7633/20736、3786/16173、46/46 |
| 最终人工画面确认 | 用户确认 RGB LCD 和 SPI LCD 均正常稳定 |

验收 bitstream SHA256：`898ad9ff8aa1fa4b04fe4f6077acb425a16867897204291e82f9a7759bd64ddf`。
固件 SHA256：`91186b13f58e0d4c33ace7a086d10274f37afa4461e92a05d6f0cd91a22f03e0`。
构建、上板和压力报告的位流哈希一致，且与当前 .fs 文件核对一致。
