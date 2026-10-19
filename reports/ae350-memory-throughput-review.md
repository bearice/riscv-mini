# AE350 内存适配器位宽与 burst 审查

日期：2026-10-19。补充 `ae350-p2-engineering-review.md`。结论：当前适配器有明确的传输放大和事务串行化；AHB 端口可以表达 burst，但目前桥没有保存或利用 burst 语义。它应作为调通基线保留，不能作为高吞吐内存路径的最终设计。

本轮只读源码、厂商封装及官方协议文档，并运行接口模型，没有综合或访问板卡。

**实际传输链。**

```text
AE350 customized data port (64-bit AHB, configured 100 MHz)
  -> MemInterconnect
  -> DDR3AHB: one request per accepted AHB beat
  -> request AsyncFIFO: write + size + address + 64-bit data
  -> UI engine: one outstanding request
  -> DDR UI: 256-bit (32 B), configured 100 MHz
  -> DDR3: 32 DQ, CK 400 MHz, fixed BL8
```

`gateware/vendor/ae350_soc/riscv_ae350_top.v:104–115` 明确导出 DDR_HBURST[2:0]、DDR_HTRANS[1:0]、DDR_HSIZE[2:0]、DDR_HPROT[3:0] 和 64-bit 数据。`ae350_native.py` 接入这些信号，`ae350_interconnect.py:83–89` 向从设备传递。然而 `ddr3_ahb.py:22` 的请求格式没有 burst/trans/prot，接受逻辑只检查 trans[1]，即不区分 NONSEQ 与 SEQ 的事务归属。

**传输放大可直接复现。**

UI 每次读取 32 B，`ddr3_ahb.py:363` 只保留一个 64-bit beat；余下字节未被数据路径保留。recorder 虽有完整 256-bit 快照，但它不是可供后续读请求使用的缓冲。

| AHB 有效数据/beat | 每次 DDR/UI 传输 | 单次有效比例 | 遍历同一 32 B 块的 UI 读命令/数据量 |
| --- | --- | --- | --- |
| 1 B | 32 B | 3.125% | 取决于实际请求序列 |
| 2 B | 32 B | 6.25% | 同上 |
| 4 B | 32 B | 12.5% | 8 次，256 B |
| 8 B | 32 B | 25% | 4 次，128 B |

本轮用现有 DUT/Model 在零基址运行同块顺序读：8 个 32-bit 请求产生 8 个相同块地址的 DDR 读命令；4 个 64-bit 请求产生 4 个。结果在 `build/reports/ae350-engineering-review/width-analysis.json`。这是接口模型中的命令放大证明，不是硬核实际 cache-refill 波形测量，也不是实板吞吐测试。

写操作也有利用率损失：一个 4 B/8 B 写仅使能 32 B 中对应字节，余下被 mask。它没有破坏邻接数据，也不意味着必须额外 read-modify-write，但仍占用一次物理突发。CPU 32 位不等于 AHB 每笔一定 4 B；需要抓 HSIZE 才能确定具体负载的放大量。

**三个不同的 burst 概念不能混用。**

1. AHB burst：HBURST/HTRANS/HSIZE 表达 SINGLE、INCR、固定长度 INCR/WRAP 等相关 beat。该端口信号齐全，但当前适配器将其逐 beat 独立处理。[Arm AHB specification](https://documentation-service.arm.com/static/6141bf0d674a052ae36ca811)
2. DDR 物理 BL8：当前 32-bit DQ 的一个 BL8 对应 32 B。它已经存在；低效率来自重复发相同块命令和窄数据使用，不是 DDR 没有突发。
3. Gowin UI 的 burst 引脚：官方文档将它用于 1:2/OTF 配置下选择 BC4/BL8。当前 IP 是 1:4、固定 BL8，因此把 `ui.burst=0` 改成 1 不能让 AHB 请求自动合并。[Gowin IPUG281 §4.4.7–4.4.8](https://www.gowinsemi.com/upload/database_doc/2009/document/696a8c052f84e.pdf)

若“burst I/O”指批量内存传输，AHB 支持；若指固定地址 FIFO/MMIO，AHB 没有 AXI 的 FIXED burst 编码。DMAC 可按外围地址固定的模式重复访问数据寄存器，但具体到 AE350 DMAC 的事务形状须在 P3 验证。不能把 FIFO read-pop、多次寄存器写等副作用访问合并为一次预取或宽写。

**位宽损失之外还有延迟放大。**

AHB FSM 经过 DATA/CAPTURE/WAIT/COMPLETE；每次均经历请求/响应跨域。UI engine 只在 outstanding=0 时取下一请求，并在处理完响应、弹出旧请求后再回 IDLE。两个深度 8 的 FIFO 因而没有形成有效的流水线；增加深度本身不增加并发。写命令与写数据还被限制在 cmd_ready 与 wr_data_rdy 同拍时才同时提交，未利用独立通道的解耦机会。

按配置计算，DDR 物理总线与 256-bit/100 MHz UI 的原始上限都是 3.2 GB/s；64-bit/100 MHz AHB 单端口上限为 0.8 GB/s。实际需扣除刷新、行切换、读写转换和 wait states。即使解决重复读，CPU AHB 也不能成为 3.2 GB/s 的消费者。现有约 1–2 MB/s 汇总值存在前次 review 指出的计时问题，不能拿来承诺加速倍数。

**建议的优化顺序。**

1. 先修已有总线接受、超时、迟到响应和 CDC 缺陷。否则引入多个在途请求会扩大错配范围。
2. 获取硬核真实 trace：保存接受沿的 HADDR/HTRANS/HBURST/HSIZE/HPROT/HWRITE 和对应 UI 命令。分别测 uncached、I-cache refill、D-cache refill、dirty write-back、顺序拷贝。不能只靠全局读计数推断一次 refill 的 beat 数。
3. 实现 **burst 范围内的 32 B 返回缓冲与宽窄转换**：完整保存一个 DDR 返回块，对属于同一已识别 AHB burst、且命中此块的后续 beat 分片返回。处理 WRAP、非块对齐起点、跨块、BUSY/IDLE、提前结束和 ERROR。不是将单个 CPU 端口改成 256 位，也不是重新引入默认可重复命中的 SharedL2。
4. 写方向使用 32 B 数据加 byte-valid mask 收集相邻 beat，优先优化 cache write-back。不能等待收满块才确认第一笔，否则主机无法发下一笔而死锁；一旦允许提前确认，就必须明确读转发、顺序、排空、失败和复位语义。尾块及部分写按 mask 提交，不强制补读整块。
5. 再对 UI 做有界命令队列/响应队列和有序的多个在途读，命令与写数据各自握手。UI 无事务 ID 时仍需严格记账、取消与迟到响应隔离；不能只将 outstanding 计数器加大。
6. P3 为 HDMI reader、DMA 等流式客户提供独立的完整块内存接口及仲裁，不强制绕行 CPU 的窄 AHB 从设备。外围侧保持自身自然宽度，在内存端进行聚合/分片；先确认 AE350 DMAC 的实际 DDR 可达路径。

**一致性是优化接口的一部分。** 返回缓冲只在有明确生命周期的事务/burst 内使用；若允许跨独立请求命中，它已经具备缓存语义，必须处理其他 master 写入、DMA 所有权和失效。写合并缓冲也必须被 flush、读写顺序及 DMA 完成条件覆盖。MMIO 不进入这些合并/预取路径。与 20K 现有设计说明一致，burst 暂存、深 FIFO 和 SharedL2 分别解决位宽转换、速率差和可重复命中，不能互相替代。

**优化验收应约束行为而非只报 MB/s。**

- 在同一块、对齐的 32 B AHB 读 burst 中，记录逻辑 beat 数及精确 UI 命令数，目标由当前 4/8 条重复命令收敛到 1 条；跨块按实际涉及的块数计算。
- 写回与尾块记录有效字节、mask、UI 写命令数；保证未使能字节不变、读后写/写后读正确。
- 真实异步 FIFO 下覆盖背压、早结束、错误、复位及读写切换。
- 区分 logical_bytes、UI_command_bytes、mask_valid_bytes、总线占用周期和主机墙钟，不把掩码字节或重复返回字节算成有效带宽。
- 单 master 正确后再测 CPU、DMA、视频并发及公平性；不通过额外私有缓存掩盖外部写可见性缺陷。
