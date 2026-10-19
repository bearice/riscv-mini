# AE350 P2 工具链可行性记录：DDR3 IP 无 GUI 生成、参考工程 PnR、构建入口

日期：2026-10-18。执行目录：`C:\Users\bearice\Workspace\TangPrimer-20K\riscv-mini`，探针与日志在
`build/work/ae350-p2-ipgen/`。参考树 `C:\Users\bearice\Workspace\TangPrimer-138K` 只读，本次未写入任何内容
（只复制到 scratch 后读取）。本记录全部来自只读证据与工具实验，不含实板结论；未编程、未写 Flash、未跑 PnR。

## 1. 结论

1. **Gowin IP 可以无 GUI 从 `.ipc` 生成：是。** 由 `gw_sh.exe` 的 Tcl 命令面驱动：`create_project` →
   `create_ipc` → `set_property`（参数名来自 `.ipspec`）→ `generate_target`。整个 DDR3 Memory Interface
   （含其内部 GowinSynthesis 与 `.vo` 生成）约 13–16 s，无需 IDE、无需鼠标。
2. **200 MHz CK 变体已生成成功，且与 400 MHz 变体的 RTL 差异只有 `tCK`**（2500 → 5000 ps）。
   CL/CWL 与全部 t\* 绝对值不变（t\* 是数据手册绝对 ps，不随时钟缩放；周期数由控制器内部换算）。
3. **与厂商预生成 Verilog 的 SHA256 不可能一致**，原因已定位（工具版本 + 时间戳 + 加密块随机填充），
   但**参数级等价已逐项证明**（`.ipc`、`gwmc_param.v`、例化模板、端口名集合全部相同）。
4. `read_ipc` 命令在本机两个 Gowin 版本上都会 **段错误**（exit 139），不可用；`create_ipc` + `set_property`
   是唯一可靠路径。
5. 厂商参考工程 PnR 实测：Hynix 400 MHz 示例 41 s、setup/hold 0/0；AE350 示例 74 s、setup 0 / hold 56
   （全部是 SDC 缺异步组约束导致的假违规）。Micron 示例目录**没有任何 impl 产物**，无证据可用。
6. 当前仓库 `mini.py build gateware --machine mega138k_ae350` 的计划仍是 P1 内容（ROM/SRAM/AE350），
   **尚不含 DDR**；选项里也没有任何 DDR/IP 生成开关。

## 2. 复现命令（可直接进 CI）

```
set GW=C:/Gowin/Gowin_V1.9.12.04_x64/IDE/bin/gw_sh.exe
%GW% build/work/ae350-p2-ipgen/gen_ddr3_ip.tcl <out_dir> <MEMORY_CLK_MHz> <module_name>

# 本次实际执行（bash，注意 Tcl 只认 C:/ 形式路径，不认 /c/ 形式）
ROOT=$(echo "$PWD" | sed 's#^/\([a-z]\)/#\U\1:/#')
"$GW" gen_ddr3_ip.tcl "$ROOT/out/ck400" 400 D3_400
"$GW" gen_ddr3_ip.tcl "$ROOT/out/ck200" 200 D3_200
```

`gen_ddr3_ip.tcl`（`build/work/ae350-p2-ipgen/gen_ddr3_ip.tcl`）内部固定了与厂商 Hynix 400 MHz 示例完全一致的
几何与时序，只让 `Memory_Clock` 作参数。生成物：

```
<out>/ip/<M>.v/<M>.v         加密的 GowinSynthesis 网表（"Written by GowinSynthesis"），这才是工程要列的文件
<out>/ip/<M>.v/<M>.v.vo      生成器输出的 .vo 网表
<out>/ip/<M>.v/<M>_tmp.v     例化模板
<out>/ip/<M>.v/<M>.v.ipc     已解析参数记录（可作下一次的输入基线）
<out>/ip/<M>.v/temp/DDR3/gwmc_param.v        控制器 RTL 参数（真正生效的 tCK/CL/CWL/t*）
<out>/ip/<M>.v/temp/DDR3/*_syn_resource.html IP 自身资源报告
```

关键点：`generate_target` 会调用 GowinSynthesis 把 IP 源码综合成加密网表，因此**需要有效 Gowin 许可**，
且**不需要**在建流时再综合 IP。当前 gw_sh 每次启动都打印 `The license expires in 6 days.`。

## 3. gw_sh 命令面（实测）

| 命令 | 结果 |
| --- | --- |
| `create_project -name/-dir/-pn/-device_version B -force` | 可用。`-device_version B` + `-pn GW5AST-LV138FPG676AES` → `<Device>gw5ast138b-002`，与 138K 参考工程一致。`002`/`C`/`D` 都被拒。 |
| `create_ipc -name ddr3 -module_name M -dir D -language Verilog -file_name M.v` | 可用。`-name` 是**小写 ip id**（`ddr3`）；`DDR3`、`DDR3 Memory Interface`、`ddr3_memory_interface` 全报 `IP0003`。`-module_name` 必填。 |
| `set_property -name CONFIG.<tclcommand> -value V [get_ips M]` | 可用，参数名取自 `IDE/ipcore/DDR3/ddr3.ipspec` 的 `tclcommand=`。未启用/派生选项会打印 `WARNING: ... disabled configuration option` 并被忽略。 |
| `list_property` / `report_property` | 直接向 stdout 打印 `CONFIG.*` 列表，**返回空字符串**，别当返回值用。 |
| `generate_target [get_ips M]` | 可用，13–16 s，产出上节文件。 |
| `write_ip_tcl`（`-ip_name`/`-multiple_files`） | 存在但未展开验证；`generate_target` 已足够。 |
| `read_ipc <file>` | **段错误**：1.9.11.02 与 1.9.12.04 均 `exit 139`（`Segmentation fault`），无项目/有项目都一样。不要用。 |
| `get_ips` 返回 | 打印的是字面量 `get_ips D3_400`，不是可操作对象；依此作属性目标即可。 |
| `add_file` / `import_files` / `run syn|pnr` | 存在；本次未用（任务要求不跑 PnR）。 |
| `GowinModGen.exe --help` | 无帮助输出，报 `Error (MG2000): Failed to read MODFILE.`；`.mod` 文件（如 `gowin_pll.mod`）是它的输入。DDR3 IP 未提供 `.mod`，故不走该路径。 |

路径坑：Tcl 侧 `[file normalize]` 对 `/c/Users/...` 会变成 `C:/c/Users/...`（曾把 IP 生成到错误目录）。
调用方必须把 bash 的 `$PWD` 转成 `C:/...` 再传给脚本。

## 4. 生成结果（`generation-summary.txt`）

| CK | module | tCK (ps) | CL/CWL | `.v` 大小 | `.v` SHA256 | `.vo` SHA256 |
| --- | --- | --- | --- | --- | --- | --- |
| 400 MHz | `D3_400` | 2500 | 6 / 5 | 2,533,881 | `64656dfb411e4b7179e13a3fd8cb2511365a80d21d69e46a385a45ab487716a6` | `8b41db31529cf347fdde425e792ecad3d2ab8202eaa73761c5ef29ef123f6a99` |
| 300 MHz | `D3_300` | 3333 | 6 / 5 | 2,562,585 | `31e582fcd0d326f7ddd3ffff5d4be6b1eee48f899da3ea4f9cc260922a1e3dbd` | `5c4c17ce5508f69dfc3a49c5363b5e6c797bc155fafbf016db35d36cc5615138` |
| 200 MHz | `D3_200` | 5000 | 6 / 5 | 2,530,573 | `44023ad537c6d7b24e33dc75d1de42098b9d5ed843bebcd13994a22778766fa5` | `7977de2f4cedde8968e73be5229ef7f189ffe9a7ad4b2b10e770954a3ef91653` |
| 100 MHz | `D3_100` | 10000 | 6 / 5 | 2,523,391 | `8d57542ac69f7491f1198d2e4dd66774dc405b9aa9ff9ec2806bc07be95e513e` | `7bb0bdf62f1dc6b8d057285b18fc404aefe6ad8d2c3c222fdcfb4a5be73a1560` |

`.v`/`.vo` 哈希是**该次运行**的：网表头部含运行时间戳，内部 `pragma protect` 块每次重新签名，同参数重跑
（`out/ck400` vs `out/ck400b`）哈希也不同（`64656dfb…` vs `07a5c2a4…`）。**只有 `.ipc` 哈希稳定**
（ck400：`fc8d0f899a5bc6dbbe71cc6ccff2f349ce66b0f6af97ae946fc05eaf021c1463`）。

生成器**接受 100/200/300/400 MHz**（无越界报错），但"工具接受"不代表 DDR PHY 在低 CK 下可校准，见第 9 节。

IP 自身综合资源（`*_syn_resource.html`，REG/ALU/LUT/DSP/BSRAM）：400 MHz = 5750/190/2945/–/16；
200 MHz = 5735/190/2943/–/16（低 CK 略省，因为时钟树缩放）。

## 5. `.ipc` 编码 ↔ RTL 参数映射（实测，不是猜测）

P2 任务书要求"确认 ROW_ADDR/COL_ADDR 等字段的枚举含义"。已实测（左侧为 `.ipc` 数值，右侧为
`temp/DDR3/gwmc_param.v` 中生效的 RTL 参数）：

| 属性（`set_property -name`） | `.ipc` 码 | RTL 参数 | 值 | 依据 |
| --- | --- | --- | --- | --- |
| `Memory_Clock` | `MEMORY_CLK` | `tCK` | 400→2500 / 300→3333 / 200→5000 / 100→10000 ps | 本次四档 |
| `Row_Address` | 2→3 | `ROW_WIDTH` | 14 → 15 | 默认 IP / ck400 |
| `Column_Address` | 0 | `COL_WIDTH` | 10 | 本次 |
| `Dq_Width` | 1→3 | `DQ_WIDTH` | 16 → 32 | 默认 IP / ck400 |
| `Dram_Width` | 0→1 | `DRAM_WIDTH` | 8 → 16（`32` 被拒：`Value '32' is out of the range`） | 探针 / ck400 |
| `Rank_Address` | 1 | `RANK_WIDTH` | 1 | 本次 |
| `CAS_Latency` | 0→1 | `CL` | 5 → 6 | 默认 IP / ck400 |
| `CW_Latency` | 0 | `CWL` | 5 | 本次 |
| `Burst_Mode` | 1 | `BURST_MODE` | "8" | 本次 |
| `Rtt_Nom` | 3→4 | `RTT_NOM` | "40" → "60" | 默认 IP / ck400 |
| `CLK_Ratio` | `1:4` | `nCK_PER_CLK` | 4 | 本次 |

即：**厂商 DDR3_400 配置为 32 bit DQ / x16 颗粒 / 1 rank / 15 行 / 10 列 / 8 bank**，
对应 2 颗 4Gb x16 = 1 GiB，与 P2 任务书预期的"1GiB、15 行、10 列、8 bank"一致（`DRAM_WIDTH=16` 说明颗粒是
x16，板上的 32 是总线宽度）。

## 6. 400 MHz 与厂商预生成件的等价性（`verify-400mhz-equivalence.log`）

- `.ipc` `[Config]`：除 6 个 1.9.9.03 时代**不存在的默认项**（`ARBITRATION_MODE/AXI4_INTERFACE/AXI_BSRAM/
  AXI_REG/AXI_SSRAM/ID_WIDTH/MC_*/SIM`）外，**逐键相同**（含 `CAS_LATENCY=1`、`ROW_ADDR=3`、`RTT_NOM=4`、全部 t\*）。
- `temp/DDR3/gwmc_param.v`：只多 4 行新增 AXI 默认参数（`S_AXI_AWID_WIDTH/S_AXI_ARID_WIDTH/
  Arbitration_Mode/BURST_LEN`），其余 **逐行相同**（`tCK=2500, CL=6, CWL=5, RTT_NOM="60"`）。
- 例化模板正文：**完全相同**（`diff` 无输出）。
- 端口名集合（顺序无关）：**完全相同**。
- 头部差异：厂商 `V1.9.9.03 (64-bit)` / `Thu May 16 14:30:35 2024`，本次 `V1.9.12.04 (64-bit)(89079)` /
  `Sun Oct 18 ... 2026`，且新版多 `Device/Part Number/Device-Package` 三行。
- 结论：**功能/参数等价，字节不可能一致**；要字节级复刻必须装 V1.9.9.03 并伪造时间戳，不值得。

## 7. 换成 200 MHz 时必须连带改的东西

IP 只解决颗粒侧。同一工程还要动：

1. `memory_clk`（= 外部 CK）：厂商 AE350 示例里 `ddr3_memory_clk = PLL CLKOUT2 ×8 = 400MHz`，
   `ddr3_sysclk = fclkdiv = CK/4 = 100MHz`，`ddr3_clkin = CLKOUT0 = 50MHz`。200 MHz 点需改成
   memory_clk 200 / sysclk 50，`ddr3_clkin` 不变 → `gowin_pll_ddr3.ipc` 同步改。
2. SDC：`create_generated_clock` 的 `multiply_by` 与周期要跟着改；否则时序约束仍按 400 MHz 算。
3. 200 MHz CK = 400 MT/s，JEDEC 档位低；本次保留 CL=6/CWL=5（器件支持），但**DDR PHY 是否支持
   CK 低到 200 MHz 未验证**（见阻塞项）。

## 8. 参考工程 PnR 可行性（只读证据）

| 工程 | 工具 | 器件 / device version | PnR 墙钟 | setup / hold 违规端点 | 资源 | DSP | PLL | Fmax（约束→实测） | 峰值内存 | `.fs` |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ddr_test/Hynix_400MHz` | V1.9.12.04 | GW5AST-LV138FPG676AES / gw5ast138b-002 | **41 s**（place 8 s + route 23 s + 输出 10 s） | **0 / 0** | LUT 3696、ALU 500、FF 6432+1 IOB、CLS 4880/69120、BSRAM 48/340、IOLOGIC 97/300、DDRDLL 1/4 | 0（报告未列） | 1/12 | clk 50→258.3；mem400 400→2240（level 1，无实际逻辑）；clk_x1 100→143.7 | 1516 MB | 有，39,324,694 B，sha256 `ba87bf374256682a8e3f5b5dff0699badcd51085a8e4fddfc67f2a409e2fcb9f` |
| `ae350_customized_demo` | V1.9.12.04 | GW5AST-LV138FPG676AC1/I0 / gw5ast138b-007 | **1 m 14 s**（place 19 s + route 47 s + 输出 8 s） | **0 / 56** | LUT 6151、ALU 341、FF 10223 + 1 latch、CLS 8367/69120、BSRAM 52/340、IOLOGIC 97/300、AE350_SOC 1/1 | 0 | 2/12 | clk50m 50→406.3；ddr3_sysclk 100→133.0；ae350_ddr_clk 50→138.9；ddr3_memory_clk 400→2598（level 1）；**ae350_ahb_clk 100→106.9（仅 6.9% 余量）**；ddr3_clkin 50→350.8 | 1626 MB | 有，37,772,717 B，sha256 `9f84b837a2c4c5fd3b9c7439a6f712538bd240ed35402996046c34b334e5dd49` |
| `ddr_test/Micron_400MHz` | — | 仅 `.gprj`（GW5AST-LV138FPG676AES / -002） | — | — | **无 impl 目录，无任何 PnR/时序/`.fs` 证据** | — | — | — | — | 无 |

证据路径：
- `TangPrimer-138K/example/ddr_test/Hynix_400MHz/impl/pnr/ddr3_1v4_hs.rpt.txt`（第 2 节耗时、第 3 节资源）、
  `.../ddr3_1v4_hs.tr`（2.1 Setup/Hold Violated Endpoints、2.3 Max Frequency、2.4 TNS）、
  `.../ddr3_1v4_hs.fs`。
- `TangPrimer-138K/example/ae350_customized_demo/impl/pnr/ae350_customized_demo.rpt.txt`、
  `.../ae350_customized_demo.tr`（同上）、`.../ae350_customized_demo.fs`。
- 两工程 rpt 的 `Created Time` 都是 2026-10-16（此前会话已重建过 impl）；工具版本一致，可比。

判读注意：
- AE350 示例的 56 个 hold 违规，`2.4 TNS` 表里各时钟却都是 0；`3.1.2 Hold Paths Table` 最差的 19 条全是
  `ddr3_sysclk:[R] → ae350_ddr_clk:[R]`，slack 约 -3.9 ns，位于厂商 `gw_ahb_ddr3` 的异步 FIFO/握手。
  该工程 SDC 只声明了 `ddr3_sysclk` 与 `ddr3_memory_clk`/`ddr3_clkin` 异步、`ae350_ddr_clk` 与 ahb/apb 互斥，
  **没有把 `ddr3_sysclk` 与 `ae350_ddr_clk` 归入异步组**——是约束缺失造成的假违规，不是物理违例。
  P2 必须补 `set_clock_groups -asynchronous` 后再复核，不能照抄厂商 SDC 就当"通过"。
- 器件档次不同：Hynix 示例用 `ES` 延时模型，AE350 示例用 `C2`（`set_operating_conditions -grade c -model slow
  -speed 2`）且 part 为 C1/I0（`gw5ast138b-007`），而**它自己的 `.ipc` 写的是 `target_device=gw5ast138b-002`**。
  跨工程比较 Fmax 无意义；本仓库平台是 `GW5AST-LV138FPG676AES`（`gw5ast138b-002`），以 `-002` 为准。

## 9. 当前仓库构建能力（`mini.py`）

- `mini.py doctor`：全部 OK（migen 0.9.2、litex/litex-boards/litedram/litesdcard/litespi/liteeth 2026.8、
  `pythondata_cpu_vexriscv` 等），RISC-V GCC `xPack 15.2.0`；环境清单写入 `build/environment.json`。
- `mini.py build` 子命令：`system,bios,bootloader,hal-sdk,cpu,soc-description,gateware,cpu-gcb,
  cpu-qualification,matrix,linux,busybox,uboot,opensbi,payload,doom,gwbasic,nyancat,machine-description`。
- `mini.py build gateware --help` 现有选项：`--machine {primer20k_vexriscv,mega138k_ae350}`、`--hardware`、
  `--output-dir`、`--dry-run`、`--purpose`、`--reference-root`、`--locked`、`--profile {minimal,full}`、
  `--cpu-verilog`、`--cpu-variant`、`--feature`、`--sd-profile`、`--usb-backend`、`--audio-clock`、
  `--boot-mode`、`--rom-size`、`--l2-size`、`--place-option`、`--route-option`、`--rtl-hierarchy`、
  `--refresh-hardware`、`--synthesize`。**没有任何 DDR / IP 生成 / 频点参数**。
- `mini.py build gateware --machine mega138k_ae350 --dry-run`：`machine-description REUSE`、`cpu REUSE`、
  `bootloader REUSE`、`gateware BUILD "P1 ROM/SRAM/AE350 RTL and implementation inputs"`
  （run `build/runs/20261018T020912405325Z-build-candidate-d007cb`）。即当前 AE350 后端仍是 P1 内容，
  P2 需要新增 DDR 配置/硬件描述与 IP 生成步骤，而不是往源码塞开关。
- 日志：`goal3/doctor.log`、`goal3/build_help.log`、`goal3/gateware_help.log`、`goal3/gateware_dryrun_ae350.log`。

## 10. 阻塞项与建议

**阻塞项（按影响排序）**

1. **Gowin 许可 6 天后到期**（gw_sh 每次启动打印）。`generate_target` 内部跑 GowinSynthesis，无许可的
   CI 机器能否生成未验证（不敢在唯一许可上做破坏性试验）。若许可失效，重生成路径整体失效。
2. **DDR PHY 在 200/100 MHz CK 下的合法性未知**。生成器接受，但硬 DDR PHY/DLL、校准窗口、`DDR3_RW_CLK
   ≥ CK/4` 是否满足，只有上板才能证明。P2 若把 200 MHz 当"合法低频 profile"，必须先做校准+读写冒烟。
3. **`read_ipc` 段错误**：任何"从现成 `.ipc` 直接装载"的设想都不可行；只能用 `set_property` 重建参数。
4. **字节级不可复刻**：网表带时间戳与随机签名块，且厂商件是 V1.9.9.03 产物（本机未安装）。
   需要"可校验构建"时，只能校验 `.ipc`/`gwmc_param.v`/例化模板，不能校验 `.v` 哈希。
5. **Micron_400MHz 无 impl 证据**：若 P2 要引用 Micron 控制器行为，需要先自行 PnR（本任务不允许）。
6. 若把 AE350 示例工程整体移植，注意它 part 为 `C1/I0`（`-007`）而其 `.ipc` 是 `-002`；本仓库应按
   `GW5AST-LV138FPG676AES`（`-002`）统一，避免 IP 目标器件与工程器件不一致。

**建议（vendored vs 重生成）**

- **采用"脚本 + `.ipc` 入库，产物按频点入库"的混合策略**：
  - 入库 `gen_ddr3_ip.tcl` + 每个频点的 `<M>.v.ipc`（几百字节，稳定、可 review、可 diff）。
  - 同时入库生成好的 `<M>.v`（约 2.5 MB/频点）作为构建输入，使日常构建**不依赖 Gowin 许可**；
    重生成仅作为审计/新增频点的动作，并用 `gwmc_param.v` 与 `.ipc` 做等价校验。
  - 不建议每次构建现场生成：多 13–16 s、且把许可变成构建硬依赖。
- 频点扫掠时按第 7 节连带改 PLL 与 SDC；每个频点独立生成、独立 PnR、独立记录（与 P2 任务书 D 节一致）。
- 不要直接改厂商预生成的 `DDR3_400.v`；它是加密网表，只能整份替换。

## 11. 证据文件（均在 `build/work/ae350-p2-ipgen/`）

| 文件 | 内容 |
| --- | --- |
| `gen_ddr3_ip.tcl` | 参数化无 GUI 生成器（本次交付物） |
| `out_ck400.log` / `out_ck300.log` / `out_ck200.log` / `out_ck100.log` / `out_ck400b.log` | 各频点 gw_sh 完整输出（含 `.ipc` 回显） |
| `generation-summary.txt` | 4 个频点的 tCK、CL/CWL、尺寸、SHA256（脚本生成） |
| `verify-400mhz-equivalence.log` | 与厂商 400 MHz 件的 ipc/gwmc/模板/端口/头部逐项 diff |
| `probe_cmds_1911.log`、`probe_help_1911.log`、`probe_usage_1911.log`、`probe_usage2_1912.log` | gw_sh 命令面与各命令 `-h` |
| `probe_open_1911.log`、`probe_open_1912.log`、`iso_read.log`、`t1/readonly.log` | `read_ipc` 段错误复现（exit 139） |
| `t1/`–`t5/` | 早期探针（设备版本枚举、`create_ipc -name` 取值、400/200 MHz 首次生成） |
| `out/ck400`、`out/ck300`、`out/ck200`、`out/ck100` | 生成产物（`.v`/`.vo`/`_tmp.v`/`.ipc`/`temp/DDR3/`） |
| `out/probe_dram*/` | `Dram_Width=32` 越界、`=8` 接受 的枚举探针 |
| `ref400/src/` | 厂商 Hynix 400 MHz 源码/`.ipc`/`temp` 的可读副本（`impl` 与 `.7z` 已删，原件仍在只读树） |
| `goal3/*.log` | doctor、build --help、gateware --help、ae350 dry-run 计划 |
