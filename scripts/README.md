# 操作入口

在仓库根目录运行 `./mini.ps1`（自动选择本仓库 Python 和工作目录）。Python 等价入口是 `.venv/Scripts/python.exe scripts/mini.py`。从 `--help` 或 `guide` 开始，日常操作按任务选择命令；不需要先读底层脚本源码。

```powershell
./mini.ps1 --help
./mini.ps1 guide
./mini.ps1 doctor
./mini.ps1 builds show current
```

`current` 是 catalog 中明确固定的已验收构建，不是最近修改的目录。`--build` 也接受完整 catalog ID 或含 `validation.json` 的产物目录。入口验证应用、bitstream hash、ABI 与 PnR setup/hold 0/0；不会自动改变 `current`。

## 基本操作

| 想做什么 | 命令 | 执行结果与影响 |
| --- | --- | --- |
| 检查开发环境 | `./mini.ps1 doctor` | 主机依赖和工具检查，不接触板子 |
| 找当前版本或候选 | `./mini.ps1 builds list` / `builds show current` | 查询 catalog 和校验产物身份 |
| 按现役配置重建源码 | `./mini.ps1 build --from current` | 复用配置、CPU RTL 和应用路径；新建 run，不编程板子 |
| 从零构建最小系统 | `./mini.ps1 build --minimal` | 无需已有 full CPU RTL；增加 `--synthesize` 才做 PnR |
| 看板子目前有没有输出 | `./mini.ps1 board status` | 被动观察 UART，列出串口；静默显示 unknown，不推断为卡死 |
| 恢复到启动菜单 | `./mini.ps1 board recover --build current` | 下载选定 gateware 到 SRAM，验证 DDR 启动并查询镜像头 |
| 修复损坏的 XIP 启动区 | `./mini.ps1 board repair-xip --build current` | 外部下载器单独写目标 XIP，再 SRAM 重载、独立读回确认；会写 Flash，应用可仍不匹配 |
| 临时运行当前固件 | `./mini.ps1 board run --build current` | SRAM gateware + UART 应用到 DDR，验证就绪，不写 Flash |
| 持久更新完整系统 | `./mini.ps1 board update --build current` | 根据 ROM/XIP 选择流程，精确读回应用/XIP，再从 Flash 启动 |
| 验收 BIOS/monitor 功能 | `./mini.ps1 board verify --build current --suite firmware` | SRAM/UART 装载后执行固件命令测试；支持 `--soak-seconds 60` |
| 验收启动协议与错误边界 | `./mini.ps1 board verify --build current --suite boot` | SRAM/UART 执行协议测试，不安装 Flash |
| 确认 Flash 中是目标文件 | `./mini.ps1 board verify --build current --suite flash` | SRAM gateware，DDR 只读探针，应用/XIP 逐字节比较，然后 Flash 启动 |
| 验证 XIP 关闭及软件 SPI 交接 | `./mini.ps1 board verify --build current --suite spi` | SRAM/DDR 程序关闭 XIP、等待 busy=0、确认软件 SPI CS 空闲，再恢复 loader；不写 Flash，仅适用 XIP |
| 观察断电冷启动 | `./mini.ps1 board verify --build current --suite cold` | 等待用户外部断电重启；不发送复位，默认等待 300 秒。不属于默认流程，仅在用户主动要求时执行 |

表中命令可直接在 PowerShell 运行。板端默认 `COM4`、Gowin location `107569`；换板或下载器时使用 `--port` / `--location`。冷启动和 Flash 验收要求已经安装目标应用；它们不会替你安装。

板端命令（status 除外）和 build 支持 `--dry-run`。下面是完整、可复制的更新预检：

```powershell
./mini.ps1 board update --build current --dry-run
```

去掉 `--dry-run` 才执行更新。`run`、`update`、firmware/flash 验收支持 `--image <app.img>`，先检查目标 ABI。boot/cold 验收不接受被忽略的镜像参数。

## 更新与验证契约

ROM 更新通过下载器写配置并 reload，再由 bootloader UART 安装应用。XIP 更新需要配置、1 MiB 启动代码、2 MiB 应用三个产物；地址来自选定构建的元数据，应用分区遵循 `boot_image.py`。XIP 与应用由 UART 加载的 DDR 工具通过片上软件 SPI 写入；配置最后由 Gowin op 8 写入并 reload。入口不要求发布包包含 Gowin 中间文件 `project.bin`，通过 FS 的二进制行计算配置空间，并检查与启动分区不重叠。

更新前验证全部输入、分区、CSR ABI，编译 DDR 工具，暂存并核对文件，然后打开串口。XIP 分支先 SRAM 加载目标 gateware，上传 DDR 工具，在禁用中断的 DDR 代码中关闭 XIP，等待 busy=0 并确认软件 SPI CS 空闲。收不到 `XIP QUIESCED enable=0 busy=0 spi_cs=idle` 确认就禁止写入。写入版工具仅由 update 编译，范围和长度固定为选定构建的两个区域；每个区域擦除前独立读取 JEDEC 三次并要求本板 `0x0B4017`，检查 WEL/WIP，接收带序号和 CRC32 的 128 字节数据包，逐页片段写入并读回确认后才 ACK。随后写配置并 reload。只读验收工具不编译写入命令。

`--suite spi` 单独验证 XIP 关闭和恢复，不执行持久写入。连续外部 op 32 在交接通过后仍发生容量误识别，正式完整更新已避开该路径；外部下载器的根因未确定。`repair-xip` 保留单次外部写入作为启动区损坏的恢复入口，并要求 XIP 独立读回匹配。Gowin 地址参数使用完整 `0x000000` 格式，子进程临时目录写入本次 operation 目录。

读回应用（包含头）和 XIP 的每个字节，与选定文件比较，保存 SHA256。一个区域内容不匹配仍继续读取后续区域，完整收集后汇总失败并恢复 loader；数据流截断则停止，因为后续区域边界无法信任。只有全部匹配才继续 Flash 启动。仅有 `FLASH HEADER VALID`、CRC 合法或下载器 `Finished` 都不足以证明装入了目标文件。

SRAM 下载和复位后先同步完整的新启动横幅，再判断 DDR 状态；旧读回数据中嵌入的固件报错字符串保留在 UART 日志中，不作为本次启动报错。新横幅后的真实 DDR 初始化错误仍立即失败。

只允许有有效地址区间、零退出码且仅报 `SPI Verify failed!` 的已知 Gowin 警告继续到独立读回；其他错误、结束地址小于起始地址立即失败。警告本身永远不是验收成功。gateware 通过所选 bitstream 编程和启动关联，不宣称做过配置区独立读回。

更新涉及单份 Flash 分区，不是原子更新：断电或中途失败可能留下部分写入。每次操作在 `build/operations/<UTC时间>-<任务>/` 保存 `result.json`、UART、下载器、编译日志和原始读回，不覆盖前一轮。失败记录给出恢复命令；XIP 启动区损坏时，单独 SRAM 加载 gateware 无法修复启动代码，需要重新写入目标 XIP。`recover` 成功表示 DDR/菜单已恢复，不表示常驻系统更新完成。

探针从所选构建的 `csr.json` 生成地址。新发布包包含该文件；旧包只有在原始 run 的 bitstream/app hash 和 CSR ABI 全部匹配时才使用其 CSR。原始 run 丢失则更新预检失败，不从其他配置猜地址。

`board verify --suite firmware/boot/cold` 调用现有专项验收实现，将日志写入本次操作目录。固件验收支持当前 BIOS/monitor；物理屏幕、听音、键鼠输入等观察仍按各专项验收说明执行。冷启动检查是外部断电后的启动和状态检查，不替代精确 Flash 读回；冷启动不属于默认验收或发布流程，只在用户主动要求时执行。

boot/cold 专项验收要求完整外设配置，最小系统使用 firmware 验收。firmware 的 `--mic` 明确测试已连接的麦克风；`--soak-seconds` 要求文件系统、视频、USB、音频全部启用。不满足条件在板端操作前失败。

新 XIP 更新与只读探针已有主机编译和模拟串口/下载器回归，实板验收状态记录在操作结果中；主机测试不能作为该新流程已经实板通过的证据。

2026-10-08 完整更新实板通过，收据 `build/operations/20261008T111029678667Z-update`：DDR 软件 SPI 写入 XIP 3708 字节和应用 104812 字节、逐页确认，Gowin 配置写入及 reload 成功，两个区域完整独立读回均精确匹配，Flash 启动到 SYSTEM READY。先前失败收据仍保留。DDR 工具每次读取 UART RXTX 后清除 RX pending，避免重复处理同一命令；SPI 8 位读回比较取寄存器低 8 位；退出工具通过 SRAM 重载恢复完整设计。此验收覆盖完整更新、读回及 Flash 启动，未执行物理断电冷启动。

## 专项操作导航

以下是开发和专项验收实现。日常板端操作使用上面的入口；改变硬件配置、网络或专项测试范围时，再读对应文档和工具 `--help`。

| 任务 | 实现或专项工具 | 使用条件 / 文档 |
| --- | --- | --- |
| 环境初始化 | `bootstrap.ps1`、`configure_tools.py`、`env.ps1`、`doctor.py` | [开发环境](../README.md)；`keep_awake.ps1` 仅为长任务保持主机唤醒 |
| FPGA 配置与 CPU 生成 | `build.py`、`build_matrix.py`、`cpu_generate.py`、`cpu_gcb_generate.py`、`cpu_qualify.py`、`test_cpu_fence.py` | [构建 skill](../.agents/skills/riscv-mini-build/SKILL.md)；参数扫描和 ISA 改动用这些专项工具 |
| 构建身份、复现和清理 | `builds.py`、`build_records.py`、`build_recipe.py` | [产物组织](../docs/build-artifacts.md)；records/recipe 为内部模块；clean 默认仅列计划，保护固定版本和发布包 |
| 启动镜像与串口协议 | `boot_upload.py`、`boot_image.py`、`boot_size.py` | [bootloader](../docs/bootloader.md)；旧 install 仅适用于 ROM，XIP 更新用正式入口 |
| 基础板测 | `firmware_verify.py`、`boot_verify.py`、`boot_repeat_verify.py`、`cold_boot.py` | [测试 skill](../.agents/skills/riscv-mini-test/SKILL.md)；分别为功能、协议、重复启动、外部冷启动 |
| BIOS 载荷与网络装载 | `bios_image.py`、`bios_payload.py`、`bios_tftp.py`、`bios_verify.py`、`monitor_external_verify.py` | [BIOS](../docs/bios.md)；RPB/OSB 载荷与 bootloader app.img 是不同格式；TFTP 解释器需要防火墙放行 |
| OpenSBI / U-Boot | `opensbi_build.py`、`opensbi_build.ps1`、`opensbi_verify.py`、`uboot_build.py`、`uboot_verify.py` | [OpenSBI 与 U-Boot 移植](../docs/opensbi-port.md)；需相应源树和工具链 |
| 外设专项验收 | `sd_verify.py`、`sd_clock_verify.py`、`ethernet_verify.py`、`raw_icmp.py`、`audio_verify.py`、`audio_capture.py` | 查工具 `--help`；raw_icmp 为网络验收实现；网络、音频、SD 的外部条件不会由一般板测推断 |
| 性能测量与比较 | `benchmark.py`、`performance_report.py` | [性能 skill](../.agents/skills/riscv-mini-benchmark/SKILL.md)；加载身份与测量身份须匹配，显式候选/基线 |
| 提交、版本与发布 | `prepare_commit.py`、`versions.py`、`release.py` | [提交 SOP](../docs/pre-commit-sop.md)、[版本历史](../docs/version-history.md)；与日常板子更新是不同任务 |
| Nyan Cat 素材 | `nyancat_assets.py` | 生成应用素材，非板子更新入口 |

`mini_ops/` 是正式入口的内部实现：产物解析、构建配置、板端流程和探针编译。不要另写临时 UART/Gowin 探针；补充能力时进入这些模块，给任务入口增加支持条件和回归。
