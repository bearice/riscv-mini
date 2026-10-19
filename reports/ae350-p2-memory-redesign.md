# P2 内存结构整改进度

2026-10-19。当前源码从 snapshot `cf020fe` 开始，按用户新增的资源利用与带宽要求修改。验收合同是 [ae350-p2-memory-acceptance.md](../docs/ae350-p2-memory-acceptance.md)。本报告不继承旧候选的全容量或压力结论。

## 结构

- 启动向量仍为 `0x80000000`，由厂商可初始化 ITCM 承载。最低支持配置 32 KiB；初始化四个 byte-lane 文件进入构建依赖闭包。P2 不实例化自定义 `AE350ROM`。
- 硬核 ILM `0xa0000000` / DLM `0xa0200000`，各 64 KiB。启动代码检查真实基址/使能状态，初始化内容，复制 ILM CRC 代码和 DLM data，清零 bss。ROM 私有栈与应用栈使用不同的 DLM 保留区。
- 删除 P2 fabric 工作 SRAM、对应地址窗口和跨域桥。硬核 ILM/DLM 不经 fabric DDR 译码。
- 平台只读诊断寄存器放到 `0xf8003000` 的硬 APB 扩展，不复用启动镜像尾部；多位数据先形成一致快照。
- 删除 JTAG probe、ROM magic/ACK mailbox 与常驻 launcher。普通应用和 DDR 诊断统一通过 UART loader。JTAG 只用于 halt/寄存器/内存观察和显式恢复。
- 诊断参数由应用 ELF 导出位置，产物保存有边界的参数布局。主机只修改声明的字节，再生成普通上传 CRC；不通过 JTAG 写参数，不增加 loader 命令。
- 内存数据口独立 100 MHz，外设/启动 ITCM 80 MHz，核心配置目标 800 MHz，DDR UI 100 MHz、CK 400 MHz。核心实际频率尚未独立标定。
- DDR 读返回保留完整 32 B，仅服务同一经过地址/尺寸/属性/长度校验的 burst；NONSEQ、错误或上下文结束后不跨独立请求命中。写聚合把同一 32 B 行内的顺序写 burst 合成一条带掩码的 UI 写；独立 NONSEQ 写仍各发一条命令。

## 已取得的证据

| 项目 | 证据与边界 |
| --- | --- |
| 硬核本地内存配置 | `20261019T042800582987Z-board-ae350-jtag-test`：milmb=`a0000001`、mdlmb=`a0200001`、micm_cfg/mdcm_cfg=`439ada` |
| ILM/DLM + 独立数据时钟中间候选 | `b1fb5beb6206`，完整 STA 通过；该候选仍有过渡共享 SRAM，不能作为最终结构交付 |
| 厂商 ITCM 启动实板 | `7ba915c7f5fb`；`20261019T043914652562Z-board-ae350-jtag-test`：2976 字节初始化镜像逐字节一致，POST 0 错，sp=`a020ffa0`、mcause=0，平台 APB status=`203`。该候选仍含待删除的旧 launcher，因此只用于验证 ITCM/本地内存方案 |
| 读 burst 定向回归 | `tests/ae350_burst_test.py` 12 项：4×64/8×32 同块读由原 4/8 条命令降为 1 条；WRAP、跨块、非法延续、上下文到期、真实异步 FIFO、生产寄存级译码通过。尚非硬核实际 refill 性能证明 |
| 其他主机回归 | AE350 50 项、mini 操作 38 项、modular build 37 项、线缆 7 项通过；另外 adapter 协议 61 项和互连专项 15 项通过 |

## 写聚合

硬核 write-back 是 WRAP4、64-bit、HPROT=0xf，四拍覆盖同一 32 B 行。主机要等当前拍 HREADY 才会给出下一拍数据，寄存译码在 HREADY 为低时也不把下一地址送给从设备，因此不能攒齐四拍再应答。

- 下一拍已经可见、并且是同一 burst、同一尺寸/保护属性、同一 32 B 行里的 SEQ 写时，当前拍在字节进入适配器缓冲后立即 OKAY。这些字节归适配器，尚未归 DRAM。BUSY 同样只缓冲，不发 UI 命令。
- 收尾行的那一拍（IDLE、NONSEQ、读、跨行或属性变化）等到这一条掩码 UI 写被接受或失败后才应答，HRESP 就是该命令的结果。这里等的是 DRAM，不是后续拍。
- BUSY 之后直接 IDLE、没有新的数据拍时，先把已应答的行提交。失败不能改写那些拍的 HRESP；`error_sticky` 保留，`write_fault` 让下一次 DDR 访问返回一次 ERROR。
- 后续读不从写缓冲转发。先提交写、作废读行缓存，再发读命令。独立 NONSEQ 写即使落在同一行也不合并。复位丢掉尚未被 UI 接受的缓冲。校准失效或提交超时不发新命令。迟到响应仍由原有恢复排空吃掉。
- `wr_data_mask` 为 1 的字节保持不变。部分行和尾块只打开实际写入的字节。

主机证据：`tests/ae350_burst_test.py` 的写聚合用例。WRAP4 与 8×32-bit 同块写各为 1 条 UI 命令且掩码/数据/读回正确；提前结束不改邻接字节；跨块分成 2 条；独立 NONSEQ 不合并；BUSY 期间命令数为 0，随后 IDLE 会排空已打开的行；UI 停顿时前三拍仍完成，收尾拍超时则整行不落 DRAM，下一笔写是干净的单拍。真实异步 FIFO 与寄存译码路径的 WRAP4 写也是 1 条命令。

时序：64-bit 写数据按 lane 选通放入 256-bit 行，地址比较用 XOR 树，HREADY 由独立等式驱动，诊断 APB 在 setup 锁存地址并插入一个 PREADY 等待周期后返回寄存数据。聚合 gateware `03c3768cd4aa`，回执 `20261019T103704769619Z-build-candidate-997c16`，setup/hold=0/0，最小脉宽 slack +0.183 ns。

实板：`20261019T105117821696Z-board-ae350-ddr-suite` 将该 bitstream 写入 FPGA SRAM（location `107569`，UART `COM9`，CPU JTAG `6673`），诊断经 UART 装载。12 checks、0 failures，mcause=0，`flash_written=false`。cache 观察写计数为 `48453, 48453, 48454`：D-cache 打开后的 store 不产生 UI 写，`L1D_WB_ALL` 之后写计数增加 1。读计数为 `45187, 45188, 45188, 45189`。`read_control=0x0f6b`、`write_control=0x1f6b`，按 `{HTRANS, HBURST, HSIZE, HPROT, write}` 解码为最后一拍 SEQ、WRAP4、HSIZE=3、HPROT=0xf。1 MiB 遍历覆盖 1,039,328 字节、32,479 块、0 错；2 秒压力 2 轮、0 错。墙钟 23.9 秒。写聚合前候选上同一次 write-back 为 4 条 UI 写。这次观察只覆盖正常 write-back。`reports/ae350-write-aggregation-review.md` 记录了该 bitstream 在后台排空时丢失已接收请求。后续 gateware `272f52128db0` 的 `20261019T113159146665Z-board-ae350-ddr-suite` 用同一观察复测，写计数仍是 `48453, 48453, 48454`。

器件 GW5AST-138（`GW5AST-LV138FPG676AC1/I0`），Gowin V1.9.12.04：

| 资源 | 用量 | 利用率 |
| --- | --- | --- |
| Logic（LUT 7375，ALU 455，ROM16 0） | 7830/138240 | 6% |
| Register（FF 10509，锁存器 0） | 10509/139140 | 8% |
| CLS | 8236/69120 | 12% |
| I/O Port | 80/312 | 26% |
| IOLOGIC（IDES8_MEM 32，OSER8 25，OSER8_MEM 40，IODELAY 32） | 97/300 | 33% |
| BSRAM（SP 16，SDPB 16，SDPX9B 32） | 64/340 | 19% |
| AE350_SOC | 1/1 | 100% |
| PRIMARY 时钟 | 7/8 | 88% |
| LW 时钟 | 2/8 | 25% |
| PLL | 2/12 | 17% |
| DDRDLL / DQS | 1/4，4/24 | 25%，17% |

I/O bank 6 为 44/50（88%），bank 7 为 28/50（56%），bank 2/3 分别为 5/50、3/50。BSRAM 仍是 64 个；删除 fabric 工作 SRAM 后，厂商 ITCM 的 32 KiB 配置使总量高于旧候选的 54 个。写聚合没有再增加 BSRAM。

## 尚待完成

`03c3768cd4aa` 的正常 write-back 不能覆盖后台排空：寄存译码已经接收的下一请求会被静默完成并返回错误数据。`272f52128db0`（`20261019T112810962889Z-build-candidate-3a0c0d`）把该请求的地址、控制和写数据保留到事务结束。STA setup/hold=0/0，最小脉宽 slack +0.183 ns。`tests/ae350_burst_test.py` 覆盖 BUSY 后的读、独立写、跨块，以及排空成功、超时和校准失效。`20261019T113159146665Z-board-ae350-ddr-suite` 复测写计数 `48453, 48453, 48454`，读计数 `45187, 45188, 45188, 45189`，12 checks、0 failures。其后仍需数据时钟范围、全容量与 10 次重新校准、独立 P1 恢复及一致工作量性能比较。新标准 M1–M9 尚未整体闭合。

ECC 检查当前保持关闭，启动显式清除诊断/检查位并初始化 RAM；不得宣称已验证 ECC 纠错。硬核 ILM/DLM、厂商 fabric ITCM 和 DDR 控制器各自使用不同资源，不把 ITCM 当成额外硬核本地 RAM。没有 Flash 写入，也没有更改 20K current/baseline。

## 无 launcher 候选的实板闭环

`86ea3a8393df` 完整 STA setup/hold=0/0，最小脉宽 slack +0.183 ns。

- `20261019T050018648975Z-board-ae350-jtag-test`：ITCM 身份、非顺序读、硬核 ILM/DLM、自主 POST 通过，只读核验。
- `20261019T045843693507Z-board-ae350-upload`：UART 正常应用上传/执行和 ABI/header/range/truncated/CRC 拒绝路径通过。
- `20261019T045718228508Z-board-ae350-ddr-suite`：12 checks，0 failures，UART 装载诊断，JTAG 只观察。1 MiB 区间实际覆盖 1,039,328 字节、32,479 块，排除完整运行镜像，所有块内八字验证通过。不是全 1 GiB 验收。
- cache 观察代码运行于 ILM，栈位于 DLM：一次 refill=1 UI 读，cache hit=0 UI 读，invalidate 后 refill=1 UI 读；一次 write-back=4 UI 写。实际捕获 HTRANS=SEQ、HBURST=WRAP4、HSIZE=3（64-bit）、HPROT=0xf。读宽度利用已有实测改善，写仍逐 beat。
- 三次原始周期保存在回执中。新候选某些 uncached read 周期反而高于基线，尚无可支持整体加速的同条件比较。
- 短压力为配置的 2 秒核心周期预算、2 轮、0 错；整套载荷墙钟不是压力时长，不据此宣称已校准核心频率。

整机 BSRAM 为 64 个，旧候选为 54 个：虽然删除 fabric 工作 SRAM，厂商 ITCM 的最小 32 KiB 配置使总量增加。不能宣称净节省 BSRAM。硬核 ILM/DLM 全范围两种地址相关图案由启动汇编在设栈前检查；ECC 检查仍关闭。

## 启动固件的工程归属

启动固件已从阶段 BSP 移到 `firmware/bootloader/ae350/`，产物身份改为 `AE350-ROM-bootloader`，身份宏改为 `MINI_AE350_BOOT_BUILD_ID`。它供后续阶段共用；阶段诊断和应用 BSP 独立。上述 86ea 候选是更名前的功能证据，更名后的候选与验证将在下文单独记录。

### 平台 bootloader 更名后的候选

`6a2e1381d170`，bootloader `19b9035f1f56`，身份 `7ced7b131570`。构建回执 `20261019T092115491474Z-build-candidate-97cda7`；setup/hold=0/0，最小脉宽 slack +0.183 ns。再次执行相同命令时 bootloader/gateware 都 REUSE（`20261019T092358729718Z-build-candidate-06b146`），源目录调整已进入依赖闭包。

`20261019T092305840858Z-board-ae350-ddr-suite`：经 UART 正常上传，12 checks、0 failures；1 MiB 区间八字/块遍历、cache/refill/write-back 观察、容量标签、DDR 执行通过。结构化回执已保存实际 read/write control，JTAG 仅观察。更名后 50 项 AE350 主机/协议测试通过。

`20261019T092409744126Z-board-ae350-jtag-test`：更名后的 ITCM 镜像逐字节/非顺序读、CPU JTAG、ILM/DLM 与自主 POST 通过。最终驻留为 `6a2e1381d170`，CPU 恢复运行于 UART loader。
