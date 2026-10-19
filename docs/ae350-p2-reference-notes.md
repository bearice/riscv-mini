# AE350 P2 参考文献笔记：DDR3 IP 接入与 A25 cache 语义

日期：2026-10-18。性质：**参考资料笔记**（只读事实汇总），不代表任何 P2 检查已通过，也不构成实现。每节按“事实 / 证据 / 未知”组织。

## 0. 约定、证据文件与校验和

路径缩写（下文所有 `$…` 引用可据此还原为绝对路径）：

| 缩写 | 绝对路径 |
| --- | --- |
| `$DEMO` | `C:\Users\bearice\Workspace\TangPrimer-138K\example\ae350_customized_demo` |
| `$DDRT` | `C:\Users\bearice\Workspace\TangPrimer-138K\example\ddr_test` |
| `$PRO` | `C:\Users\bearice\Workspace\TangPrimer-138K\example\pro_ddr_test` |
| `$IPS` | `C:\Gowin\Gowin_V1.9.12.04_x64\IDE\ipcore\RiscV_AE350_SOC`（另见 `Gowin_V1.9.11.02_x64` 同名目录） |
| `$REF` | 仓库内 `build/work/ae350-p2-ref` |

已下载/提取的参考文件（均位于 `$REF`，SHA256 为实测）：

| 文件 | 字节 | SHA256 | 说明 |
| --- | --- | --- | --- |
| `ddr3_ipug_ja.pdf` | 1,440,981 | `7c26f300884f75ad3cd4950edf777b9ec82fb5ffc19e8cedd9814e27fa153a36` | Gowin DDR3 Memory Interface IP 用户指南 **IPUG281-2.6J**（日文版，47 页，2026-01-16）。英文 URL `…/2009/document/6820d9f57274d.pdf` 被挡（200 但 0 字节 text/html），可用 URL 为 `https://gowinsemi.com/upload/database_doc/2009/document_ja/696fd469f145d.pdf` |
| `ddr3_ipug_ja.txt` | 36,729 字符 | `cc170fed5c45c1760798167ad235c29e603cd860d0ffd84966fab132ef20a02f` | 上件的文本提取，页标记 `===== PAGE n =====` |
| `IPUG281-2.2E.pdf` | — | `8167cad897b9efeb74e3639148f9006b4fa3f9efa3ede0bd21965944e5196ad2` | **英文**旧版 IP 用户指南（36 页），取自 `$DDRT\docs\IPUG281-2.2E.pdf`；英文参数选项表用它引用 |
| `IPUG281-2.2E.txt` | 41,470 字符 | `53c48fc1df45ce005be657a305b65086722990ecdc82123498469c077b32b90b` | 上件文本提取 |
| `MUG1029_ae350_hw_ref.pdf` | 3,334,207 | `bbf1d6585253576b1616135838623faec94677b61b2e2d273a9deebb2873af61` | **Gowin RiscV_AE350_SOC 软件编程手册 MUG1029-1.3E**（381 页，2025-03-21）。URL `https://www.gowinsemi.com/upload/database_doc/2688/document/67dd35b63d214.pdf` |
| `MUG1029.txt` | 552,162 字符 | `a179fd5e692db4cc1fab613316824b6e45232ef6b39d5c279255bb99c41864ee` | 上件文本提取 |
| `MUG1031_ae350_hw_design_ja.pdf` | 2,210,020 | `1f89dd6b591e439bef1b148fe4ea00cea79d1afb362df3822782255d97e26c0e` | **Gowin RiscV_AE350_SOC 硬件设计手册 MUG1031-1.3.1E**（日文版，64 页；即 P2 任务书引用的表 4-1/4-2 来源）。URL `https://www.gowinsemi.com/upload/database_doc/2725/document_ja/69811021aaeed.pdf`（需浏览器 UA；`document/` 路径被挡） |
| `MUG1031.txt` | 65,963 字符 | `6be3dbd2fcb590edd8393113024f41acd45eae3d7fc3b120056897a19c9ea6c5` | 上件文本提取 |
| `AX45MP_datasheet.pdf` | 7,958,294 | `b08ba552d5a57a4196fd804e07865a8512c02210210c2a9a662e88a44e2832a0` | AndesCore AX45MP-1C UM248 V1.2；AES 加密，用 `$REF/pylib`（pypdf+cryptography）解出文本 |
| `AX45MP_datasheet.txt` | 461,767 字符 | `3de357b3781915a3a8bdcadf87207cd3a97eb449db3ff4724344a9f1f6eb8268` | 上件文本提取（CCTL/CSR 细节的英文交叉证据） |
| `asm/t.S`, `asm/t.o` | — | — | 用 Gowin MCU Designer 自带工具链实测的非标 CSR 指令编码（见 §3.5） |

引用格式：`文件:行` 或 `PDF#page`（PDF 页码；括注印刷页码）。MUG1029/MUG1031 用“PDF 页”和“印刷页(总页)”两个数，避免混淆。

---

## 1. Gowin DDR3 Memory Interface IP（`DDR3_Memory_Interface_Top`）

### 1.1 地址单位与单个 IP 的可寻址范围

**事实**

1. 用户接口 `addr` 的**单位不是字节**：在 1:4 时钟比、Dq Width=32 配置下，1 个 `addr` 递增对应 **4 字节**（= Dq Width/8）。证明链：
   - 手册 §7.2：AXI4 侧地址是字节地址，DDR3 侧“不一定”是字节地址；“Dq Width = 8 时 DDR3 地址等于字节地址；Dq Width = 16 时 1 个 DDR3 地址存 2 字节，因此两者宽度不同”（IPUG281-2.6J **p.41 / 印刷 34**）。
   - 用户接口数据宽度 = `Dq Width*8`（1:4），即 32×8 = **256 bit = 32 字节/拍**；一个 BL8 突发正好一拍（§7.1，p.40 / 印刷 33）。32 字节 ÷ BL8 的 8 个 DDR3 字 = **4 字节/字**。
   - 厂商自测引擎对每次突发把列地址加 8：`$DDRT\Hynix_400MHz\src\ddrtest.v:331` 与 `:339` 的 `col <= col + 4'd8`，而 `app_addr = {1'b0,bank,row,col}`（`ddrtest.v:277`）→ 8 个地址单位 = 32 字节，即 4 字节/单位。
2. `addr` 结构 = `{RANK(1), BANK(3), ROW(15), COL(10)}` = 29 位；Rank 是最高位且**不可用**，必须固定 0（§7.1，p.40 / 印刷 33；“IP 不支持 Rank 地址，此地址不起作用”）。
3. 生成的 IP 顶层端口为 `input [28:0] addr`（`$DEMO\src\ddr3_memory\ddr3_memory_interface\ddr3_memory_interface.v:28429`），但内部实例**只接 `addr[27:0]`**（同文件 `:28470` `.addr(addr[27:0])`）。
4. 因此单实例可寻址上限 = 2^28 个单位 × 4 字节 = **1 GiB**。这正好等于 2× H5TQ4G63EFR（4Gb x16）：每颗 = 2^15 行 × 2^10 列 × 2^3 bank × 2 字节 = 512 MiB，两颗并联成 32 位 → 1 GiB，且 2^28 个 4 字节字用满 `addr[27:0]`。
5. `addr` 宽度由 `Rank+Bank+Row+Column` 决定，**不可配置**（§7.1，p.40 / 印刷 33；“Address Width：配置不可”）。Bank 固定 3 位、Rank 固定 1 位。
6. 每次命令的 `addr` 相互独立，不必相邻（§4.4.5，p.21 / 印刷 13：“各 addr 之间无关，例如 addr1 与 addr2 无需是相邻地址”）。**一拍 256 位传输内部地址不递增**：一次命令 = 一次 BL8 突发 = 一拍用户数据，没有“突发中途换地址”的概念；连续突发由用户逐次给新 `addr`。

**证据**：上列文件行号 + IPUG281-2.6J p.40–41；IPUG281-2.2E 表 7-1（p.29 / 印刷 22）给出 Dq Width 选项 `8,16,24,32,…,72`。

**未知**
- `addr[28:0]` 的 29 位端口在内部被截成 28 位后，**顶层 `addr[28]=1` 的实际后果**（手册只说“必须固定 0”）未见文档说明。
- 手册 §3.3 的示例配置是“28 位用户地址、x16 颗粒、1:4”（p.12–13），对应 ROW=14 的 512 MiB 形态；本配置是 29 位（ROW=15、1 GiB）。两者都由 `Rank+Bank+Row+Col` 决定，不矛盾；但该示例的 LUT/REG 资源数（LUT 2173 等）不能直接套用到本配置。

### 1.2 `.ipc` 字段的枚举解码

`$DEMO\src\ddr3_memory\ddr3_memory_interface\ddr3_memory_interface.ipc`（51 行）里的数字是**生成器枚举下标**，不是物理值。解码依据 = 三份工程（Hynix 400、Micron 400、ae350 demo）的 `.ipc` 与它们各自生成的 `temp/DDR3/gwmc_param.v` 对照。生成参数文件路径：`$DEMO\src\ddr3_memory\ddr3_memory_interface\temp\DDR3\gwmc_param.v`（明文）。

| `.ipc` 字段 | 本工程值 | 生成参数实际值 | 解码规则 / 证据 |
| --- | --- | --- | --- |
| `DQ_WIDTH` | `3` | `DQ_WIDTH=32` | 下标 = 位数/8 − 1（0→8, 1→16, 2→24, 3→32…）。英文表 7-1 选项 8…72（IPUG281-2.2E p.29）。**32 = 两颗 x16 并联的总 DQ** |
| `DRAM_WIDTH` | `1` | `DRAM_WIDTH=16` | 实测 **1 → 16**（本工程与 Micron 一致）；仓库内 IP 生成探针另测得 **0 → 8**，字面量 `32` 被拒（`Value '32' is out of the range`），索引 2 未探针（`docs/ae350-p2-tooling-notes.md:97`） |
| `RANK_ADDR` | `1` | `RANK_WIDTH=1` | 固定 1 位；手册明确“固定为 1，不可用”（p.39 / 印刷 32） |
| `ROW_ADDR` | `3` | `ROW_WIDTH=15` | `ROW_WIDTH = 下标 + 12`：3→15（Hynix/pro），2→14（Micron） |
| `COL_ADDR` | `0` | `COL_WIDTH=10` | 0→10（三个工程一致） |
| `CAS_LATENCY` | `1` | `CL=6` | 选项 5/6/7/8（英文表 7-1）⇒ 0→5（Micron）、1→6（Hynix） |
| `CW_LATENCY` | `0` | `CWL=5` | 0→5 |
| `BURST_MODE` | `1` | `"8"`（BL8） | 选项 4/8/OTF；**1:4 只允许 BL8**（英文表 7-1 注：4 或 OTF 仅 1:2 支持） |
| `BURST_TYPE` | `0` | `"SEQ"` | Sequential / Interleaved |
| `ADDITIVE_LATENCY` | `0` | `AL="0"` | 0 / CL−1 / CL−2 |
| `ADDR_CMD_MODE` | `0` | `"1T"` | 1T（未见 2T 样本） |
| `OUTPUT_DRV` | `0` | `"LOW"` | LOW = 40 Ω（RZQ/6），HIGH = 34 Ω（RZQ/7）（§7.1 p.40） |
| `RTT_NOM` | `4` | `"60"` | 选项 OFF/20/30/40/60/120 ⇒ 4→60；Micron `3`→40 佐证 |
| `RTT_WR` | `0` | `"OFF"` | OFF/120/60（英文表 7-1 顺序 OFF,120,60） |
| `WRITE_RECOVERY` | `1` | （未出现在 gwmc_param.v 中） | **未解码**；gwmc_param.v 无 WRITE_RECOVERY 参数 |
| `MEMORY_CLK` | `400` | `tCK=2500`（ps） | 400 MHz ⇒ 2500 ps；见 §1.3 |
| `MEMORY_TYPE` | `0` | `DRAM_TYPE="DDR3"` | 0 = Components |
| `USER_INTERFACE` | `Controller` | （无直接对应参数） | Controller = 含 Controller+PHY；PHY = 仅 PHY（§7.1 p.39） |
| `PARAMETER1..4` | `5,6,40,40` | 无对应项 | 手册：工程调试参数，非用户配置项（§7.1 p.40） |
| `tCK` | 由 MEMORY_CLK 推出 | `2500` | 手册表 7-1：tCK = 内存接口时钟周期（ps） |
| `tCKE/tDLLK/tFAW/tRAS/tRC/tRCD/tREFI/tRFC/tRP/tRRD/tRTP/tWTR` | 见 `.ipc` | 与 `.ipc` **逐值相同** | 单位 ps，直接透传；`tREFI=7800000`（7.8 µs，JEDEC DDR3 标准） |

**注意（重要）**：上表里 `DRAM_WIDTH` 只确认了 `1 → 16`，`0`/`2` 未取到样本；`RANK_ADDR`、`COL_ADDR` 也只有单一取值样本。这些解码是“与已知物理一致”的推断，不是文档枚举表。`RANK_ADDR`、`COL_ADDR` 也只有单一取值样本，解码是“与已知物理一致”而非“由文档枚举表确认”。

### 1.3 时钟：`memory_clk` 到底等于什么

**事实**

1. IP 有三个时钟（§4.4.2，p.16–17 / 印刷 8–9）：
   - `clk`：低速连续时钟，建议 50 MHz（板载晶振直接进）；
   - `memory_clk`：IP 高速时钟，**驱动 PHY 并经 HCLK 资源输出到 DDR3**；**其频率必须等于 DDR3 接口速率对应的时钟频率**——“DDR3 速率需 800Mbps 时，此选项配置为 400MHz”（§7.1，p.39 / 印刷 32）；
   - `clk_out`：`memory_clk` 的分频（1:4 → ÷4，1:2 → ÷2，走 PCLK），**用户接口必须与 `clk_out` 同步**。
2. `memory_clk` = 外部 DDR3 **CK**（不是 CK/2、也不是 2×CK）：400 MHz CK ⇒ 800 MT/s。DDR3 IP 的 `tCK=2500 ps` 与 MEMORY_CLK=400 一致。
3. 本工程 PLL 实测参数（`$DEMO\src\gowin_pll_ddr3\gowin_pll_ddr3.v`）：`FCLKIN=50`、`IDIV_SEL=1`、`FBDIV_SEL=1`、`MDIV_SEL=16` ⇒ VCO = 50/1×16 = **800 MHz**；`ODIV0_SEL=16` ⇒ CLKOUT0 = **50 MHz**（送 IP 的 `clk`）；`ODIV2_SEL=2` ⇒ CLKOUT2 = **400 MHz**（送 `memory_clk`）；`CLKOUT2_EN="TRUE"`。同一设置见 `.ipc` 的 `ClkinClockFrequency=50`、`ClkfboutExpectedFrequency=400`、`Clkout2ExpectedFrequency=400`（`gowin_pll_ddr3.ipc`）。
4. `enclk2` 接 `pll_stop`：`$DEMO\src\gowin_pll_ddr3\gowin_pll_ddr3.v` 的 `.ENCLK2(enclk2)`；IP 要求 `pll_stop` 低有效并直接接 PLL 的 enclk2（§4.4.4，p.18）。
5. SDC 与 PLL 一致、**与 demo 顶层注释不一致**：`$DEMO\src\ae350_customized_demo.sdc:11` `ddr3_memory_clk` = ×8 × 50 MHz = 400 MHz；PnR 时钟表同样报 `ddr3_memory_clk 2.500 ns 400.000MHz`（`$DEMO\impl\pnr\ae350_customized_demo.tr`，2.2 Clock Summary 第 6 行）。而 `$DEMO\src\ae350_customized_demo.v:81` 的行内注释写“200MHz”——**注释错误**，应废弃。
6. `clk_out` = 100 MHz：SDC 第 9 行 `ddr3_sysclk` = ×2 × 50 MHz = 100 MHz，取自 PHY 内 `fclkdiv/CLKOUT`；PnR 时钟表同为 100 MHz。ddr_test 例程同样把测试引擎时钟 `clk_x1` 绑到 IP `clk_out`（`$DDRT\Hynix_400MHz\src\top.v:118`、`:168`；`ddr3_1v4_hs.sdc` 中 `clk_x1` = 400 ÷ 4 = 100 MHz）。

**结论表（本工程实测）**

| 信号 | 来源 | 频率 | 依据 |
| --- | --- | --- | --- |
| `CLK`（板载） | 晶振 | 50 MHz | `sdc:6` |
| IP `clk` | PLL_DDR3 CLKOUT0 | 50 MHz | `gowin_pll_ddr3.v` ODIV0=16；`sdc:13` |
| IP `memory_clk` = DDR3 CK | PLL_DDR3 CLKOUT2 | **400 MHz**（800 MT/s） | `gowin_pll_ddr3.v` ODIV2=2；`sdc:11`；`.tr` 2.2 |
| IP `clk_out` = 用户接口 | PHY fclkdiv | **100 MHz**（CK/4） | `sdc:9`；`.tr` 2.2 |
| AE350 `DDR_CLK` | PLL_AE350 CLKOUT0 | 50 MHz | `.ipc` Clkout0ExpectedFrequency=50；`sdc:10`；`.tr` 2.2 |
| AE350 `CORE_CLK` | PLL_AE350 CLKOUT1 | 800 MHz | `.ipc` Clkout1=800；`.tr` 2.2 第 11 行 |
| AE350 `AHB_CLK` / `APB_CLK` | PLL_AE350 CLKOUT2/3 | 100 / 100 MHz | `.ipc`；`sdc:12,14` |
| AE350 `RTC_CLK` | PLL_AE350 CLKOUT4 | 10 MHz | `.ipc` Clkout4ExpectedFrequency=10 |

**未知 / 矛盾**
- MUG1031 表 4-1 明确 **RTC clock = 32.768 kHz**（p.19 / 印刷 11），而 demo 的 PLL 给 10 MHz。设备手册只规定“RTC 计数源分频前为 32.768 kHz”（MUG1031 p.17），因此 demo 的 10 MHz 是否合法/如何分频**未确认**；P2 若使用 mtime/RTC 需自行核对。
- PLL `Clkout0ExpectedFrequency=50` 但 `.ipc` 里 `EnableClkout0Divider=false`、`Clkout0VCODivideFactorStaticValue=16`，与实际 ODIV0=16 一致；`Clkout2` 的 `…StaticValue=2` 与 ODIV2=2 一致。`.ipc` 中大量 `*DynamicValue=8` 是未启用动态重配时的占位值，**不要**当实际分频比读。

### 1.4 用户接口协议

**事实**（页码为 IPUG281-2.6J）

| 信号 | 语义 | 证据 |
| --- | --- | --- |
| `cmd[2:0]` | Read = `3'b001`，Write = `3'b000` | 表 4-1，p.20 |
| `cmd_en` / `cmd_ready` | 二者同时为 1 的周期，`cmd` 与 `addr` 被 IP 接收；`cmd_ready`=1 表示控制器可接收命令（有背压） | p.20–21（图 4-9） |
| 1:4 下的命令时序 | 与 1:2 完全相同 | p.21（“1:4 时命令与地址时序同 1:2，见图 4-9”） |
| `wr_data[255:0]` | 一拍 = 一次 BL8 突发的全部写数据（1:4 + BL8） | p.22–24（图 4-11） |
| `wr_data_en` / `wr_data_end` | 1:4+BL8 情况与 1:2+BC4 相同：`en` 与 `end` **同一周期同时为 1** | p.23（图 4-11 说明） |
| `wr_data_rdy` | 为 1 时控制器可接收用户写数据 | p.22 |
| `wr_data_mask[31:0]` | **每 bit 对应 `wr_data` 的 1 个字节；0 = 该字节写使能（mask 无效），1 = 该字节被屏蔽** | 表 6-1，p.34–36 |
| mask 宽度 | 1:4 时 = `Dq Width*8/8` = 32 位；1:2 时 `Dq Width*4/8` | §7.1，p.40 |
| `rd_data[255:0]` / `rd_data_valid` / `rd_data_end` | 读数据按命令顺序返回；`rd_data_end` 标记该命令最后一拍 | p.25–27（图 4-17） |
| `burst` | 仅 1:2 支持 OTF；`burst=0`→BC4，`burst=1`→BL8。1:4 下 burst 模式由 IP 参数 BURST_MODE 固定 | p.23–24、p.34 |
| 刷新 | IP 自动按 `tREFI` 刷新，刷新优先级最高，刷新前先 precharge all banks | p.27 |
| `sr_req/ref_req/sr_ack/ref_ack` | 自刷新/刷新请求与应答；本工程（USER_REFRESH=OFF）未使用，`sr_req=0`、`ref_req=0` | `$DEMO\src\ddr3_memory\gw_ahb_ddr3.v` 实例化 |

**字节通道映射**：`wr_data_mask` 第 n 位 ↔ `wr_data` 的 `[8n+7:8n]`。表 6-1（PDF p.35 / 印刷 27）原文：

> `wr_data_mask`：`wr_data` 的掩码。每一位对应 `wr_data` 的 1 个字节。**0：对应 `wr_data` 的掩码无效；1：对应 `wr_data` 的掩码有效。**

即 **0 = 不屏蔽（该字节写入）**，1 = 屏蔽。厂商桥的字节写 mask 生成（`gw_ahb_ddr3.v:911`：`8'b1111_1110` 表示“只写第 0 字节”）与“0=写”一致。

**未知**：`wr_data_rdy` 持续为 1 时能否**背靠背连续提交**多个写数据拍（即每个 `cmd` 紧跟一拍、不留空隙）；手册只给单次突发时序图，未见背靠背限速说明。adapter 应假设“每个 `cmd` 对应一拍数据”，并在 `wr_data_end` 后再发下一个 `cmd`。

### 1.5 能否让 IP 跑在低于 `MEMORY_CLK` 的 CK 上

**事实**
- `MEMORY_CLK` 是**生成参数**，决定 `tCK`；`tCK` 同时决定所有以周期计的内部计数（刷新、tRCD 等）。证据：`.ipc MEMORY_CLK=400` → `gwmc_param.v tCK=2500`；其余 t* 参数以 ps 给出（表 7-1 / 表 7-2）。
- 手册要求 `memory_clk` 必须等于 DDR3 接口速率对应频率（§7.1，p.39），`clk_out` 只能是 `memory_clk` 的 1/4 或 1/2（§4.4.2，p.16）。
- 因此“降低 CK”= **重新生成 IP**（改 `MEMORY_CLK` 并重新核对 CL/CWL/t* 与颗粒 datasheet），不能只改 PLL 而沿用旧 IP。这与 P2 任务书 §4.D.1“每个点独立生成、PnR”一致。
- 1:4 模式的最高速率：**GW5AST-138 属“其他器件”，上限 800 Mbps**；只有 GW5A(R)(S)-25 / GW5A(T)-60 支持 1100 Mbps（§3.2，p.12）。1:2 上限 533 Mbps（同页）。
- 1:4 只在 **GW5AST-138** 上可用（GW5 家族）；1:2 与 1:4 都可用于 GW2A-18/GW2A-55/GW2AR-18（§3.1，p.11）。

**未知**
- 手册没有“IP 允许 `memory_clk` ≠ 生成值”的明文。但仓库内已做过生成级验证：**400/300/200/100 MHz 四档都能生成，`tCK` 分别为 2500/3333/5000/10000 ps，且 200 MHz 变体与 400 MHz 变体的 RTL 差异只有 `tCK`**（`docs/ae350-p2-tooling-notes.md:12,93`；证据目录 `build/work/ae350-p2-ipgen/{generation-summary.txt, out/…}`）。⇒ 改频的正确做法就是改 `MEMORY_CLK` 重新生成，`t*` 保持绝对 ps 不缩放。
- **仍属未知**：这些低 CK 变体在实板上的功能/刷新正确性（P2 尚未做板测）；以及 IP 内部如何把 ps 换算成 `memory_clk` 周期（无明文，只能由 `tCK` 推断）。

### 1.6 厂商 AHB→UI 桥 `gw_ahb_ddr3.v` 的实测缺陷（**不要照抄**）

**事实（静态分析 + 综合告警，均有行号）**
- 该桥内部写数据通路只有 **128 位**：`reg [127:0] wr_data;`（`$DEMO\src\ddr3_memory\gw_ahb_ddr3.v:183`），写 mask 只有 **16 位**：`reg [15:0] lpddr_wdata_mask;`（`:187`）。
- 实例化时接的是 x32 IP：`.wr_data(wr_data)`（`:351`）、`.wr_data_mask(lpddr_wdata_mask)`（`:354`）、`.rd_data(lpddr_rd_data)`（128 位，`:355`）、`.addr({3'b000,cmd_addr[25:1]})`（`:349`）。
- 综合器明确报宽度不匹配：`$DEMO\impl\gwsynthesis\ae350_customized_demo.log:20-23`：
  - `addr` 实际 28 / 形式 29；`wr_data` 实际 128 / 形式 256；`wr_data_mask` 实际 16 / 形式 32；`rd_data` 实际 128 / 形式 256（均 `EX3670`）。
- 地址映射按“1 个 IP 地址单位 = 2 字节”实现：`app_addr` 是 32 位字节地址对齐到 16 字节（`:804`、`:806`），再取 `cmd_addr[25:1]` 送 IP（`:349`）。**对 x32（4 字节/单位）而言等于把字节地址除以 2**，且 `addr[27:25]` 恒 0 → 地址窗口仅 2^25 个 IP 单位（x16 语义下 64 MiB，x32 语义下 128 MiB），更高位被丢弃并**回绕/别名**。
- mask 极性“0=写”会使 128→256 零扩展后的高 16 个字节（mask 位 31:16 为 0）**被写 0**；本工程若真按此实现，32 位宽的 AHB 写会污染额外 16 字节。

**结论**：`gw_ahb_ddr3.v` 是为 **16 位 DQ** 器件写的旧桥（Tang Primer 20K 系）；本 demo 的 IP 是 32 位 DQ，两者不匹配且综合器只给 warning。**P2 必须自己写 AHB→UI 适配器**：`addr = {1'b0, HADDR[27:2]}`（4 字节单位、rank 位固定 0），256 位数据 / 32 位 mask，按 AHB `HSIZE` 与 `HADDR[1:0]` 生成 mask，不能沿用该文件。

**未知**：该 demo 位流是否曾在板卡上跑通过 DDR（`$DEMO\impl\pnr` 有 2026-10-16 生成的 `.fs`，但无运行记录）。按静态证据判断其 DDR 数据通路不可用；这是**推断**，不是实测结论。

---

## 2. AE350 的存储映射与“定制数据内存”端口

### 2.1 地址映射（两份手册互相印证）

**事实（MUG1031-1.3.1E 表 3-2，PDF p.15 / 印刷 7）**

| 区域 | 地址范围 | 大小 | 总线/来源 | 频率 |
| --- | --- | --- | --- | --- |
| Data Memory | `0x00000000`–`0x7FFFFFFF` | **最大 2 GB** | AHB；**DDR3 / DTCM / 用户定制存储器** | **< 200 MHz** |
| Instruction Memory | `0x80000000`–`0x8FFFFFFF` | 最大 256 MB | AHB；SPI Flash / ITCM / 用户定制存储器 | < 200 MHz |
| ILM | `0xA0000000` | 64 KB | 本地存储，ECC SECDED | — |
| DLM | `0xA0200000` | 64 KB | 同上，可被 fabric 经 64 位 Extended AHB Master 访问 | — |

**事实（MUG1029-1.3E 表 3-1，PDF p.44–45 / 印刷 10–11；定义见 `bsp\ae350\ae350.h`）** — 更细的明细：

| 区域 | 地址 | 大小 |
| --- | --- | --- |
| DDR/SRAM Data Memory | `0x00000000`–`0x7FFFFFFF` | 2 GiB（译码范围） |
| ROM/Flash Instruction Memory | `0x80000000`–`0x8FFFFFFF` | 256 MiB |
| ILM / DLM | `0xA0000000` / `0xA0200000` | 各 64 KiB |
| BMC | `0xC0000000`–`0xC00FFFFF` | 1 MiB |
| AHB Decoder | `0xE0000000`–`0xE00FFFFF` | 1 MiB |
| PLIC | `0xE4000000`–`0xE5FFFFFF` | 32 MiB |
| Machine Timer (PLMT) | `0xE6000000` | — |
| PLIC-SWINT | `0xE6400000` | — |
| Debug Module | `0xE6800000` | — |
| **Extended AHB Slave** | `0xE8000000`–`0xEFFFFFFF` | **128 MiB**（MUG1031 表 3-3：32 位数据，AE350 作 master） |
| APB Bridge | `0xF0000000`–`0xF00FFFFF` | 1 MiB |
| SMU | `0xF0100000` | — |
| UART1 | `0xF0200000` | — |
| **UART2** | `0xF0300000` | — |
| PIT | `0xF0400000` | — |
| WDT | `0xF0500000` | — |
| RTC | `0xF0600000` | — |
| **GPIO** | `0xF0700000` | — |
| I2C | `0xF0A00000` | — |
| DMA | `0xF0C00000` | — |
| SPI | `0xF0F00000` | — |
| **Extended APB Slave** | `0xF8000000`–`0xFBFFFFFF` | **64 MiB**（MUG1031 表 3-3：32 位数据，AE350 作 master） |

（Extended AHB/APB 的 128 MiB / 64 MiB 与 MUG1031 表 3-3 的“128MB / 64MB”一致，地址跨度亦一致，可互证。）

中断号（MUG1029 表 4-2，PDF p.47 / 印刷 13）：RTC period=1、RTC alarm=2、PIT=3、GP0=4、SPI2=5、I2C=6、**GPIO=7**、UART1=8、UART2=9、DMA=10、GP1–15=11–25、SMU standby=26、SMU wakeup=27。

**关于“超过该大小是否还会到 AHB 端口”**
- 手册只声明 Data Memory 译码 `0x00000000`–`0x7FFFFFFF`、最大 2 GB，以及“DDR3/DTCM/用户定制存储器”共用该窗口（MUG1031 表 3-2）。**具体的译码位数与越界行为（拒绝应答/别名/挂起）没有任何文档说明 → UNCONFIRMED**。实测方案只能靠 JTAG 逐段读写验证（P2 的 alias 检查正是为此）。

### 2.2 GPIO APB 寄存器布局（可直接当 mailbox 用）

**事实（MUG1029 §8.2，表 8-1，PDF p.79–80 / 印刷 45–46；基址 `0xF0700000`）**

| 偏移 | 名称 | 说明 |
| --- | --- | --- |
| `0x00` | IDREV | ID[31:8]（GPIO = `0x020310`）、RevMajor[7:4]、RevMinor[3:0] |
| `0x10` | CFG | Pull[31]、Intr[30]、Debounce[29]、ChannelNum[5:0]（只读） |
| `0x20` | DATAIN | 输入数据 `N:0`，只读；firmware 从 bit n 读通道 n 电平 |
| `0x24` | DATAOUT | 输出数据 `N:0`，**只读** |
| `0x28` | CHANNELDIR | 方向 `N:0`，R/W，**0 = 输入，1 = 输出**，复位 0 |
| `0x2C` | DOUTCLEAR | 写 1 清对应输出位（WO） |
| `0x30` | DOUTSET | 写 1 置对应输出位（WO） |
| `0x40` / `0x44` | PULLEN / PULLTYPE | 上下拉使能与类型 |
| `0x50` | INTREN | 中断使能 |
| `0x54`–`0x60` | INTRMODE0..3 | 每 8 通道一组的中断触发模式 |
| `0x64` | INTRSTATUS | 中断状态 |
| `0x70` / `0x74` | DEBOUNCEEN / DEBOUNCECTRL | 去抖：DBClkSel[31]、DBPreScale[7:0] |

mailbox 用法：设 `CHANNELDIR` 对应位为 1 后用 `DOUTSET`/`DOUTCLEAR` 写，用 `DATAIN` 读对端。硬件侧 `RiscV_AE350_SOC_Top` 的 `GPIO[31:0]` 端口就是这 32 个通道（`$DEMO\src\riscv_ae350_soc\riscv_ae350_soc_tmp.v` 端口列表；demo 只接了 6 位，见 `ae350_customized_demo.v:210`，综合器报 `GPIO` 实际 6 位 / 形式 32 位）。

### 2.3 定制数据端口的位宽、时钟与跨域

**事实**
- AE350 IP 的定制数据端口是 **AHB 64 位**：`DDR_HRDATA[63:0]`、`DDR_HADDR[31:0]`、`DDR_HWDATA[63:0]`、`DDR_HCLK`、`DDR_HRSTN`（`$DEMO\src\riscv_ae350_soc\riscv_ae350_soc_tmp.v` 端口列表；demo 顶层 `ae350_customized_demo.v:134-170` 的实例化）。
- 该端口的时钟 `DDR_CLK`（MUG1031 表 4-1 称 “DDR clock，数据内存时钟，**< 200 MHz**”，PDF p.19 / 印刷 11）。**它与外部 DDR 颗粒的 CK 不是同一个时钟**——P2 任务书对此的强调与手册用词一致（表 4-1 的 DDR clock vs 表 4-2 的 `DDR3_MEMORY_CLK`）。
- MUG1031 表 4-1 附注：core / DDR / AHB-APB 三者**互相异步**；AHB 与 APB **1:1 同步**（p.19）。
- MUG1031 表 4-2（p.19）：`DDR3_MEMORY_CLK ≤ 400 MHz`、`DDR3_RW_CLK ≥ DDR3_MEMORY_CLK/4`、`DDR3_CLK_IN = 50 MHz`。

**证据补充（实测可达频率）**：ae350 demo 的 PnR 报出该端口的实际 Fmax——`ae350_ddr_clk` 约束 50 MHz、实测 **138.929 MHz**；`ae350_ahb_clk` 约束 100 MHz、实测 **106.942 MHz**（`$DEMO\impl\pnr\ae350_customized_demo.tr` 2.3 Max Frequency Summary）。**这组实测值可用于判断 DDR_CLK 的上限**：在 demo 的布局/约束下，AE350 侧 DDR/AHB 逻辑到不了 200 MHz（138.9 / 106.9 MHz），因此 P2 把 DDR_CLK 保持在 50 MHz 有余量；若要提高 DDR_CLK，必须单独做 PnR 表征。

**未知**
- MUG1031 表 4-2 的 `DDR3_RW_CLK` 指 IP `clk_out`（用户接口时钟）：表中只要求 ≥ CK/4，但**没有说明 1:4 模式必须恰好 = CK/4**；IP 手册说 `clk_out` 是 `memory_clk` 的 ÷4（1:4）。两者一致（100 MHz = 400/4），但不是同一份文档的同一句话。
- 定制数据端口的**突发/背压细节**（HBURST、HREADY 时序、最大突发长度、是否支持 early burst termination）在 MUG1029/MUG1031 中均未给出 → 只能按 AHB-Lite 通用协议实现，并用实板验证。**UNCONFIRMED**。

---

## 3. Andes A25 的 cache 语义

### 3.1 cache 几何与复位状态

**事实（MUG1029 第 6 章，PDF p.51–53 / 印刷 17–19；MUG1031 表 3-1，PDF p.14–15）**
- I-Cache 32 KB、256 行/路、4 路、**行 32 字节**、ECC（单错纠正/双错纠正）、LRU（MUG1029 表 6-1）。
- D-Cache 32 KB、256 行/路、4 路、**行 32 字节**、ECC（单错/双错检测）、LRU（表 6-2）。
- 容量公式（手册原式）：`Cache size = Cache lines per way × Ways × Line size` = 256×4×32 = 32 KB。
- 复位状态：控制寄存器 IC_EN/DC_EN 的 Initial Value 均为 **0**（表 6-3）⇒ **上电后 I/D cache 均为关闭**，必须由软件显式打开。

### 3.2 `mcache_ctl`（CSR `0x7ca`）

**事实**

| 位 | 名称 | 含义 | 复位 |
| --- | --- | --- | --- |
| 0 | IC_EN | I-Cache 使能（1=开） | 0 |
| 1 | DC_EN | D-Cache 使能（1=开） | 0 |
| 3:2 | IC_ECCEN | 0=关；1=保留；2=仅不可纠错时报异常；3=任何 ECC 错误都报异常 | 0 |
| 5:4 | DC_ECCEN | 同上（数据侧） | 0 |
| 6 | EC_RWECC | 允许 CCTL 访问 I-cache RAM 的 ECC 码（诊断） | 0 |
| 7 | DC_RWECC | 同上（D-cache） | 0 |
| 8 | CCTL_SUEN | 允许 S/U 态使用 `ucctlbeginaddr`/`ucctlcommand` | 0 |
| 9 / 10 | IPREF_EN / DPREF_EN | I/D 预取使能（仅见于 AX45MP 文档；A25 手册未列，见下） | — |
| 11 | IC_FIRST_WORD | 只读状态 | — |
| 19 / 20 | DC_COHEN / DC_COHSTA | 一致性使能/状态（AX45MP；A25 未列） | — |

- **AE350（A25）手册只列到 bit 8 + IC_FIRST_WORD[11]**（表 6-3，PDF p.52–54 / 印刷 18–20），与 OpenSBI 的 Andes 定义一致：`CSR_MCACHE_CTL 0x7ca`、`MCACHE_CTL_IC_EN_MASK BIT(0)`、`DC_EN BIT(1)`、`CCTL_SUEN BIT(8)`（`build/vendor/opensbi/platform/generic/include/andes/andes.h:14`、`:57-60`）。
- **不存在名为 `mcache_info` 的 CSR**。cache 几何的只读寄存器是 `micm_cfg`（`0xfc0`）与 `mdcm_cfg`（`0xfc1`）：`CSR_MICM_CFG 0xfc0`、`CSR_MDCM_CFG 0xfc1`、`ISZ = bits[8:6]`、`DSZ = bits[8:6]`（`build/vendor/opensbi/platform/generic/include/andes/andes.h:15-16`、`:47-51`）；AX45MP 手册同样把 `micm_cfg 0xfc0`/`mdcm_cfg 0xfc1` 列为只读配置（AX45MP 表 42 上方的配置寄存器表，文本行 3522–3523）。MUG1029 §27.18.3（PDF p.316–317 / 印刷 282–283）的 Slave Port demo 打印 `Check ILM: MICM_CFG = 0x439ada`、`Check DLM: MDCM_CFG = 0x439ada`、`ILM Size = 0x10000 (64KB), DLM Size = 0x10000 (64KB)`，证明这两个 CSR 在该 AE350 配置上可读（同一工程 ILM/DLM 各 64 KB）。

### 3.3 CCTL（cache 维护）寄存器与命令

**事实**

| CSR | 助记符 | 说明 |
| --- | --- | --- |
| `0x7cb` | `mcctlbeginaddr` | CCTL 起始地址 / 索引；M 态专用（AX45MP §16.12.9，PDF p.231） |
| `0x7cc` | `mcctlcommand` | **写该寄存器即触发一次 CCTL 操作**；命令值见下表（AX45MP §16.12.10，PDF p.232；A25 表 6-4 同值） |
| `0x7cd` | `mcctldata` | 部分 CCTL 命令的读写数据（§16.12.11，PDF p.234） |
| `0x80b` / `0x80c` | `ucctlbeginaddr` / `ucctlcommand` | S/U 态版本，需 `mcache_ctl.CCTL_SUEN=1` |

表 6-4 / AX45MP 表 58 命令值（A25 与 AX45MP **完全一致**，两份文档互证）：

| 值 | 命令 | 类型 |
| --- | --- | --- |
| 0 | `L1D_VA_INVAL` | 虚拟地址 |
| 1 | `L1D_VA_WB` | 虚拟地址 |
| 2 | `L1D_VA_WBINVAL` | 虚拟地址 |
| 3 / 4 | `L1D_VA_LOCK` / `L1D_VA_UNLOCK` | 虚拟地址 |
| **6** | **`L1D_WBINVAL_ALL`**（整 D-cache 写回+无效） | 全局 |
| 7 | `L1D_WB_ALL`（整 D-cache 写回） | 全局 |
| 8 | `L1I_VA_INVAL` | 虚拟地址 |
| 11 / 12 | `L1I_VA_LOCK` / `L1I_VA_UNLOCK` | 虚拟地址 |
| 16 / 17 / 18 | `L1D_IX_INVAL` / `_WB` / `_WBINVAL` | 索引 |
| 19–22 | `L1D_IX_RTAG/RDATA/WTAG/WDATA` | 索引（诊断） |
| 23 | **`L1D_INVAL_ALL`**（整 D-cache 仅无效） | 全局 |
| 24 / 27–30 | `L1I_IX_INVAL` / `RTAG` / `RDATA` / `WTAG` / `WDATA` | 索引 |

索引格式（AX45MP 表 13/14，PDF p.28）：D-cache `mcctlbeginaddr` = `{WAY[C-1:B], INDEX[B-1:A], OFFSET[A-1:3]}`，其中 `A = log2(每行双字数)+3`、`B = log2(CacheSize/#Ways)`、`C = ceil(log2(CacheSize))`；I-cache 的 OFFSET 用 `[A-1:2]`（按字）。对本配置（32 KB、4 路、32 B/行）：每行 4 个 64 位双字 → A=5；`B = log2(32768/4) = 13`；`C = 15` ⇒ `mcctlbeginaddr = {WAY[14:13], INDEX[12:5], OFFSET[4:3]}`，bit2:0 保留 0。

VA 类命令要求该行**确实在 cache 中**才执行（§7.6 表 12：“只有被寻址的 cache 行在对应 cache 中时才执行操作”）；IX 类直接按索引操作，不经地址翻译。

**A25 手册的对应说法**：MUG1029 §6.3 给出表 6-4（与上表同值）（PDF p.54 / 印刷 20），并注明“Cache CCTL 操作定义位于 `bsp\ae350\cache.c`”。

### 3.4 `fence` / `fence.i` 语义

**事实（AX45MP §7.5 表 11，PDF p.44）**

| Cache | `FENCE` | `FENCE.I` |
| --- | --- | --- |
| I-Cache | 无操作 | **整 I-cache 无效** |
| D-Cache | 无操作 | **整 D-cache 写回** |

附加条件：`mcache_ctl.IC_EN=0` 时 `FENCE.I` 对 I-cache 无操作；`DC_EN=0` 时对 D-cache 无操作（§7.5）。写缓冲的刷新条件包括 `FENCE`/`FENCE.I`/`SFENCE.VMA` 执行时、以及 `DC_EN` 被清 0 时（§7.3，PDF p.42–43）。

⇒ **`fence.i` 不是“flush D-cache”**：它只整刷 D-cache（写回）+ 整无效 I-cache；若要把 D 侧脏行“写回并无效”（例如给外部 master 看），必须用 CCTL 命令 6（`L1D_WBINVAL_ALL`）。

### 3.5 Gowin 自带工具链不支持 `cctl`；用 CSR 写实现（实测编码）

**事实**
- Gowin MCU Designer 自带工具链是 **上游 xPack GNU RISC-V Embedded GCC 14.2.0**（`C:\Gowin\GMD\toolchain\gowin\gcc_riscv\README.md`、`riscv-none-elf-gcc.exe --version`），**不识别 Andes 私有 `cctl` 汇编助记符**：
  `riscv-none-elf-as -march=rv32ima t.S` 对 `cctl t0,t1` 报 `Error: unrecognized opcode`。
- 因此在本仓库可用的工具链下，cache 维护只能写 CSR（Andes 自家工具链的 `cctl` 指令见 §3.7，本次未取得）。实测（`$REF/asm/t.S`，`-march=rv32ima_zicsr_zifencei -mabi=ilp32`，objdump 反汇编）：

| 汇编 | 机器码 | 说明 |
| --- | --- | --- |
| `csrw 0x7cc, a0` | `7cc51073` | 触发 CCTL 命令（`mcctlcommand`） |
| `csrs 0x7ca, t0` | `7ca2a073` | 置位使能（如 `DC_EN`） |
| `csrc 0x7ca, t1` | `7ca33073` | 清位（关 cache） |
| `csrw 0x7cb, a1` | `7cb59073` | 设 `mcctlbeginaddr` |
| `csrr t2, 0xfc1` | `fc1023f3` | 读 `mdcm_cfg` |
| `fence.i` | `0000100f` | — |

- **构建注意**：GCC 14 的 `-march=rv32ima` 默认不含 `zicsr`/`zifencei`，写 CSR 与 `fence.i` 会直接报“extension `zicsr`/`zifencei` required”。固件必须加 `_zicsr_zifencei`（或等效 `-march`）。
- 等价 C 写法：`csr_write(0x7cc, 6)` = 整 D-cache 写回+无效；`csr_set(0x7ca, 1<<1)` = 打开 D-cache；`csr_clear(0x7ca, 1<<1)` 后跟一次 `csr_write(0x7cc, 6)` = 关 D-cache 并写回（**这正是 OpenSBI 的做法**）。

**证据（开源实现，可直接照抄语义）**：`build/vendor/opensbi/platform/generic/andes/andes_sbi.c`、`platform/generic/include/andes/andes.h:14-16,56-68`（`MCCTLCOMMAND_L1D_WBINVAL_ALL 6`）。OpenSBI 版本：`6ad246a1494807ee91e36ffcf60e4c30bb309d45`（2026-09-28）。

**未知**：Andes 官方 BSP `bsp\ae350\cache.c` 的源码不在本机安装（`C:\Gowin\GMD` 内无 AE350 BSP 头文件/驱动），MUG1029 只给出该路径并说“CCTL 操作定义位于其中”。**官方 BSP 的具体指令序列未能核对**（UNCONFIRMED），但 CSR 号与命令值与两份 Andes 文档一致，风险低。

### 3.6 关/开 cache 的安全顺序（依据文档推导）

**事实基础**：D-cache 写缓冲在 `DC_EN` 清零时被 flush（§7.3）；`fence.i` 在 `DC_EN=0` 时对 D-cache 无操作（§7.5）；CCTL 命令 23 `L1D_INVAL_ALL` 只无效不写回。

**推荐序列（推导，非手册原文）**
- 开：`csrs 0x7ca, (1<<0)|(1<<1)` → 再 `fence.i`（同步 I 侧）。
- 关 D 前保数据：先 `csrw 0x7cc, 7`（`L1D_WB_ALL`）或 `6`（`WBINVAL_ALL`），再清 `DC_EN`。
- 给外部观察者（JTAG/DMA/PLIC 共享缓冲）：`csrw 0x7cc, 6`（整刷）或按行用 `2`（`L1D_VA_WBINVAL`，`mcctlbeginaddr` 填 VA）。
- 取指与代码替换：写完代码 → `csrw 0x7cc, 6`（或 `csrw 0x7cc, 7`）→ `fence.i`。

### 3.7 官方示例代码位置

**事实（MUG1029 §27.16，PDF p.314 / 印刷 280）**——原文摘录：

> "The Cache application program describes the hardware cache mechanism. It runs code modifications in memory that can be cached to achieve data consistency. First, it checks cache features, and if the features exist, it enables instruction and data caching. Then, it modifies a global variable named "g_selfmodify" in the data cache and uses the "fence.i" instruction for memory consistency. Finally, it checks the correctness of "g_selfmodify.""
>
> 运行结果：`MCU supports CCTL operation` / `MCU supports CCTL auto-increment` / `The L1C ICache sets = 256` / `ways = 4` / `line size = 32` / `The L1C DCache sets = 256` / `ways = 4` / `line size = 32` / `Enable L1C I cache` / `Enable L1C D cache` / `Execute original self modify code.` / `I/D cache flush` / `Run selfModifyCode Pass.`

示例目录 `…\ref_design\MCU_RefDesign\ae350_demo\src\demo\cache`。§27.17（PDF p.315 / 印刷 281）是 cache lock 示例（`demo/cache_lock`），其描述明确写 “uses **CCTL instructions** for memory consistency”，说明 **Andes 自家 BSP/工具链里确实有 CCTL 指令**；§27.18.3 的 slave port 示例则用 `MICM_CFG`/`MDCM_CFG` 读取 ILM/DLM 配置。**示例源码本身不随 Gowin EDA 安装包提供**（本机未找到 `ref_design` 目录）。

### 3.8 DDR 区域是否可缓存 / 如何做 uncached 访问

**事实**
- A25 在该宏中“支持 RISC-V 物理内存保护（PMP），16 个 PMP 条目”（MUG1031 表 3-1，PDF p.13–14）。**PMP 不改变 cache 属性**，只能做权限/地址过滤。
- Andes 的 cache 属性由 **PMA/PPMA** 控制：`CSR_PMACFG0 0xbc0`、`CSR_PMAADDR0 0xbd0`（`build/vendor/opensbi/platform/generic/include/andes/andes.h:41-42`），`ETYP[1:0]`（0=OFF，3=NAPOT）、`MTYP[3:2]`（`MEM_NON_CACHE_BUF = 3` 等）（`platform/generic/include/andes/andes_pma.h:11-22`）；OpenSBI 在 AE350 平台上保存/恢复 `PMACFG0`、`PMACFG0+2` 与 16 个 `PMAADDR`（`platform/generic/andes/ae350.c:33-37,64-67`），并在 `andes_sbi_probe_pma()` 中检查 `MMSC_CFG.PPMA`（bit30，`andes.h:50-51`）。
- 因此“把某段 DDR 设为 non-cacheable”在 Andes V5 架构上有明确机制（PPMA + MTYP）。

**未知**
- A25 是否实现 PPMA、`MMSC_CFG.PPMA` 在该宏上是否置位、`mcache_ctl` 是否有 A25 专用的 `DC_COHEN` —— 均**需实板读 CSR 确认（UNCONFIRMED）**。P2 的“cache 关闭路径”因此应先按“整 cache 关闭 + CCTL 刷写”实现，把 PMA 当作可选优化。

---

## 4. 预置工程的 PnR 证据（可行性参照）

### 4.1 `$DDRT\Hynix_400MHz`（32 位 DQ、400 MHz DDR3 自测）

| 项 | 值 | 证据 |
| --- | --- | --- |
| 器件 | `GW5AST-LV138FPG676AES`，Device GW5AST-138，Version **B** | `$DDRT\Hynix_400MHz\impl\pnr\ddr3_1v4_hs.rpt.txt:11-13` |
| 工具版本 | **V1.9.12.04 (64-bit)** | 同上 `:10` |
| 综合工具 | GowinSynthesis（网表 `ddr3_1v4_hs.vg`） | `impl\gwsynthesis\ddr3_1v4_hs.log` |
| Setup 违例端点 | **0** | `.tr`（`Numbers of Setup Violated Endpoints`） |
| Hold 违例端点 | **0** | 同上 |
| TNS | 全部时钟 setup/hold 均 **0.000** | `.tr` 2.4 |
| Fmax | `clk` 50 MHz 约束 → **258.323 MHz**；`mem400` 400 MHz 约束 → **2240.143 MHz**；`clk_x1` 100 MHz 约束 → **143.694 MHz** | `.tr` 2.3 |
| PnR 墙钟 | 布局 8 s + 布线 23 s + 输出 10 s；**总 0h 0m 41s**，峰值内存 1516 MB | `.rpt.txt` 第 2 节 |
| 资源 | LUT/ALU/ROM16 4196（3696 LUT + 500 ALU）；Register 6433；CLS 4880/69120（8%）；BSRAM 48/340；IOLOGIC 97/300（IDES8_MEM 32、OSER8 25、OSER8_MEM 40、IODELAY 32）；I/O 80/312 | `.rpt.txt` 第 3 节 |
| 时钟资源 | PRIMARY 2/8（25%）、LW 5/8、GCLK_PIN 8/24、CLKDIV 1/24、**DDRDLL 1/4**、DQS 4/24、**PLL 1/12**；`memory_clk` 走 `BANK6_HCLK0 BANK7_HCLK0` | `.rpt.txt` 第 5、6 节 |

**要点**：x32 @ 400 MHz 的 DDR3 PHY+控制器在 GW5AST-138（B 版、AES 生产料号）上**时序收敛且余量充足**；`mem400` 的 2240 MHz“Fmax”只统计该时钟域内的短路径，不能理解为 CK 可跑 2.2 GHz（PHY 内部逻辑以 CK 采样，真实上限由 IP/颗粒决定，手册给 800 Mbps）。

### 4.2 `$DEMO`（AE350 + 定制 DDR 端口 + 同一 DDR3 IP）

| 项 | 值 | 证据 |
| --- | --- | --- |
| 器件 | **`GW5AST-LV138FPG676AC1/I0`**，Device GW5AST-138，Version B | `$DEMO\impl\pnr\ae350_customized_demo.rpt.txt:11-13` |
| 工具版本 | V1.9.12.04 (64-bit) | 同上 `:10` |
| Setup 违例端点 | **0** | `.tr` 2.1 |
| Hold 违例端点 | **56** | `.tr` 2.1 |
| TNS | 每个受约束时钟 setup/hold 均 **0.000** | `.tr` 2.4 |
| 56 条 hold 违例的性质 | 报告列出的最差 25 条路径中，**19 条为负 slack**，全部是 `ddr3_sysclk`（100 MHz，IP `clk_out`）→ `ae350_ddr_clk`（50 MHz）的跨域路径，位于 `u_gw_ahb_ddr3_top/u_gw_ahb_ddr3/u_ddr3_memory_and_ahb_bus`（含 `ahb_end_s1`、`ahb_req_s1`、`u_fifo_rd/u_fifo_wr` 的 gray 指针，如 `Equal.wptr_#_s0`、`Equal.rptr_#_s0`、`rbin_num_5_s0`）；最差 slack **−3.941 ns**。另 6 条为 `ddr3_sysclk`→`ddr3_sysclk` 的 PHY 命令 lane OSERDES 路径（+0.138…+0.147 ns，不构成违例） | `.tr` 3.1.2 表（前 25 条路径，`Path Slack` 与 `From/To Clock` 列） |
| 该跨域的约束状态 | SDC 已声明 `ddr3_sysclk`/`ae350_ddr_clk` 属于 `-exclusive` 组（`ae350_customized_demo.sdc:15`）与若干 `-asynchronous` 组（`:16-19`），但 STA 仍按实际跨域路径分析并报 hold 违例 | `.sdc:15-19` + `.tr` 3.1.2 |
| Fmax | `clk50m` 50 MHz→**406.330 MHz**；`ddr3_sysclk` 100 MHz→**132.956 MHz**；`ae350_ddr_clk` 50 MHz→**138.929 MHz**；`ddr3_memory_clk` 400 MHz→**2598.775 MHz**；`ae350_ahb_clk` 100 MHz→**106.942 MHz**；`ddr3_clkin` 50 MHz→**350.840 MHz** | `.tr` 2.3 |
| PnR 墙钟 | 布局 19 s + 布线 47 s + 输出 8 s；**总 0h 1m 14s**，峰值内存 1626 MB | `.rpt.txt` 第 2 节 |
| 资源 | LUT/ALU/ROM16 **6492**（6151 LUT + 341 ALU）；Register 10224；CLS 8367/69120（13%）；**AE350_SOC 1/1**；BSRAM 52/340；IOLOGIC 97/300（IDES8_MEM 32、OSER8 25、OSER8_MEM 40、IODELAY 32）；I/O 94/312 | `.rpt.txt` 第 3 节 |
| 时钟资源 | PRIMARY 8/8（100%）、LW 7/8、GCLK_PIN 8/24、CLKDIV 1/24、**DDRDLL 1/4**、DQS 4/24、**PLL 2/12**；`DDR3_MEMORY_CLK` 走 `BANK6_HCLK0 BANK7_HCLK0` | `.rpt.txt` 第 5、6 节 |

**要点**
1. 两个工程都在同一器件族上收敛；**ae350 的负 slack hold 路径集中在桥的跨域 FIFO/握手上**（56 个违例端点，最差 25 条中 19 条是该跨域），说明厂商桥的 CDC 未被正确约束（SDC 把它们标为 exclusive/asynchronous，STA 却仍报违例）。P2 自建桥时必须显式处理这组跨域（异步 FIFO + `set_false_path`/`set_max_delay`，并说明理由），否则会继承同样的告警甚至真实亚稳态风险。
2. `ae350_ddr_clk` 实测 Fmax 138.9 MHz、`ae350_ahb_clk` 106.9 MHz ⇒ P2 保持 50/100 MHz 的 AE350 时钟是合理且有余量的选择。
3. ae350 demo 用的料号是 **`…C1/I0`**（工程样片/工业温度标识），ddr_test 用 **`…AES`**；P2 的最终 part 必须与实物丝印一致，不能混用二者作为时序签核依据。

---

## 5. 对 P2 实现的直接结论（**推断**，不是手册事实）

1. **地址换算**：AHB（64 位、字节地址）→ IP `addr` 的映射必须是 `addr = {1'b0, HADDR[29:2]}`（4 字节单位、rank 位固定 0）；可访窗口上限 `0x3FFFFFFF`（1 GiB），更高位必须显式处理（报错或拒绝）。**不要**复制 `gw_ahb_ddr3.v` 的 `HADDR[25:1]` 映射。
2. **数据通路**：一个 AHB 64 位拍 = 8 字节；一次 IP 传输 = 32 字节。要么把 4 个 AHB 拍聚成 256 位（用 mask 表示部分写），要么对未覆盖的字节置 mask=1（屏蔽）。mask 位 n ↔ 字节 n，**0 = 写**。
3. **时钟**：`memory_clk` = 外部 CK（目标 400 MHz = 800 MT/s），`clk_out` = CK/4 = 100 MHz 作为用户/AHB 侧同步时钟；`DDR_CLK`（AE350 数据端口）保持 50 MHz，与 `clk_out` 之间做异步 FIFO。
4. **cache**：上电 cache 全关；DDR 访问默认按可缓存处理（A25 无文档化的 uncached 属性）；任何“外部观察者可见”的验收都必须先 `csrw 0x7cc, 6`（或按行 `2`），并用 `fence.i` 处理代码替换。构建加 `_zicsr_zifencei`。
5. **频率表征**：每个 CK 点重新生成 DDR3 IP（改 `MEMORY_CLK`、核对 CL/CWL/t*），不能只改 PLL；`DDR3_RW_CLK` 保持 = CK/4。

---

## 6. UNCONFIRMED 清单（无法从现有资料确认）

1. AE350 定制数据端口的**译码位数、越界行为、别名范围**：手册只给 `0x00000000`–`0x7FFFFFFF` 与“最大 2 GB”，没有任何越界/译码实现说明。
2. AE350 定制数据端口的 **AHB 突发/背压细节**（HBURST 支持、最大 burst、early termination、HREADY 时序）：MUG1029/MUG1031 均未描述。
3. **A25 是否实现 PPMA**（`0xbc0/0xbd0`）以及 `MMSC_CFG.PPMA` 位；`mcache_ctl` 的 `DC_COHEN/DC_COHSTA` 是否存在于本宏。需实板 `csrr` 验证。
4. **Andes 官方 BSP `bsp\ae350\cache.c` 的原始序列**：本机无该源码（GMD 内无 AE350 BSP），只有 MUG1029 的引用与 OpenSBI 的等价实现。
5. **`cctl` 指令编码**：Gowin 自带工具链（xPack GCC 14.2.0，上游）不支持该助记符，故本文只给 CSR 写法的实测编码；Andes 私有工具链的 `cctl` 编码未取得。
6. **`DRAM_WIDTH` 枚举的 0/2 取值**、`COL_ADDR`/`RANK_ADDR` 的其他取值：只有单一样本，解码靠与已知物理一致而非文档枚举表。
7. **`WRITE_RECOVERY`（`.ipc=1`）的物理含义**：生成的 `gwmc_param.v` 未包含该参数，手册表 7-1 也未列出该项。
8. **低 CK 变体的实板正确性**（刷新、校准、数据完整性）：生成已成功且只差 `tCK`（`docs/ae350-p2-tooling-notes.md:12`），但没有任何实板运行证据。
9. **1:4 下连续背靠背写（`wr_data_rdy` 保持 1）是否允许**：手册只有单次突发时序图。
10. **RTC_CLK 频率**：MUG1031 表 4-1 规定 32.768 kHz，demo 给 10 MHz；AE350 内部 RTC 分频设置未确认。
11. **`gw_ahb_ddr3.v` 是否在实板跑通过**：只有位流文件与综合告警，无运行记录；本文对其“不可用”的判断是静态推断。
12. **`ddr_test/Hynix_400MHz` 与 `pro_ddr_test` 的 `mem400` “Fmax 2240 MHz”**：只反映该时钟域内短路径，**不能**当作 CK 上限；真实 CK 上限 800 Mbps 来自 IP 手册（§3.2 p.12），非本报告实测。
13. **H5TQ4G63EFR 的 datasheet 级确认**：本笔记只用手册 + `.ipc`/`gwmc_param.v` 的 15 行/10 列/8 bank/x16 与 1 GiB 算术自洽来佐证，未在本地找到该颗粒 datasheet 原文（板卡 `docs/07_Datasheet` 未逐颗核对）。
