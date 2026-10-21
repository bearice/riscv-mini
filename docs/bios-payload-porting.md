# 给 BIOS 写二级程序：从移植到上板验收

这篇文档面向后来接手的人（或 agent）：把一个新程序做成常驻 BIOS 的 RPB1 二级程序，并在真板上跑起来、验完。里面每条“坑”都是实际踩过的，写明**症状 → 原因 → 正确做法**。具体应用实例见 [GW-BASIC](gwbasic.md) 和 [DOOM](doom.md)；ABI 与内存表见 [BIOS](bios.md)。

## 0. 先读什么、用什么入口

| 要做的事 | 入口 | 不要做的事 |
| --- | --- | --- |
| 找构建 / 跑板子 / 验收 | `./mini.ps1 --help`、`./mini.ps1 guide`、[操作入口](../scripts/README.md) | 自己拼 Gowin 参数或写临时串口脚本 |
| 板子当前在干什么 | `./mini.ps1 board status`（被动观察） | 直接往串口敲命令而不确认状态 |
| 恢复到 setup 提示符 | `./mini.ps1 board recover --build <ID>` | 猜测板子在跑什么 |
| 临时运行固件（不写 Flash） | `./mini.ps1 board run --image <app.img>` | 用 `board update` 写 Flash |
| BIOS 功能验收 | `./mini.ps1 board verify --build <ID> --suite firmware --image <app.img>` | 只看“能启动”就宣布通过 |
| 改动的验证矩阵 | [提交 SOP](pre-commit-sop.md) §3 | 纯软件改动却重跑 PnR/性能 |
| 板测身份/产物组织 | [构建产物](build-artifacts.md) | 把目录时间戳当身份 |

**RTL 未变时**（只改 BIOS/驱动/HAL/编译参数）：只需编译 + ABI/CRC + 主机回归 + 功能实板测试，不重跑 PnR/时序、不做性能基准。新增载荷属于同一档。

**动手前先扫一眼 §11「流程纪律」**：那一节记的是这次真正吃亏的地方（另写临时串口脚本、自己造状态机/TFTP、脚本憋着不输出、不确认板子状态就往串口写）。

## 1. 第一步：确认 CPU 能力（浮点）

```text
> status
CPU/sys=60 MHz DDR=120 MHz UART=115200
BIOS ABI=1 memory=128 MiB MMU=1 FPU=0        <-- 看这里
```

**坑 1：硬浮点载荷在没有 F 扩展的配置上以非法指令死掉。** 现役 full 配置是 `rv32ima`（`FPU=0`），所以载荷必须用 `--soft-float`（`rv32im/ilp32`，浮点走 libgcc 软实现）。`mstatus.FS` 只在 `MINI_FEATURE_FPU` 的构建里由 `firmware/bsp/primer20k_vexriscv/start.S` 置位，载荷自己无法补救。

- 硬浮点载荷 → `--feature fpu=on` 的 full 配置
- 软浮点载荷 → 任何配置

## 2. 平台层：只用 ecall 服务，别碰设备

载荷只能通过 `a7=0x42494f53`、`a6=功能号`、`a0..a5` 参数的 ecall 访问设备；BIOS 保有 `mtvec`、中断和驱动。需要新能力时**扩展 BIOS 服务并写进 [ABI 表](bios.md#程序格式和-abi)**，不要在载荷里直接摸 CSR。

两条通用经验：

- **文件请用标准接口，不要一个命令一个服务。** 现有 FS 层是 `FS_OPEN/CLOSE/READ/WRITE/SEEK/STAT/MKDIR/UNLINK/RENAME`（服务号 22–30，`open(2)` 风格 `BIOS_FS_O_*` 标志，句柄是 BIOS 侧 0..5）。`FILE_READ`/`FILE_SIZE` 只是它的兼容薄封装。只提供“按路径+偏移读一次”的服务，会让每个应用自己发明打开/定位/关闭，还会诱导出下面坑 5 那种缓存。
- **原始按键用 `KEY`（20），不要解析 TTY 字节流。** `KEY` 给 HID usage 的按下/松开事件（含修饰键、自动重复），游戏类程序需要 key-up；TTY 队列（`GETC`）是文字终端的字节流，仅供日志/命令行。

## 3. 运行时：标准 libc 与历史手写 shim 的教训

复杂 C app 使用 [静态 Newlib 运行库](payload-libc.md)，通过 `mini.py build payload --libc newlib` 构建；BIOS 不需要链接 Newlib。以下保留旧手写 shim 的历史教训。

旧的 `-nostdlib -ffreestanding` 模式下，编译器仍会要 `memcpy/memset/strlen` 等（仓库里 `firmware/drivers/string.c` 已经提供，`mini.py build payload --helper string`），其余要自己实现。真实踩过的坑：

**坑 2（printf 精度）：** 症状 `W_GetNumForName: STCFN33 not found!`（lump 明明存在）。原因是 `printf("%.3d")` 的**整数精度**没实现，`"STCFN%.3d"` 输出成 `STCFN33` 而不是 `STCFN033`。→ 整数精度 = 最少位数、左侧补零；给了精度时忽略 `0` 标志。

**坑 3（varargs 长度修饰符）：** `%d/%u/%x` 必须按 `l/ll` 修饰符取对应宽度（rv32 上 `int` 4 字节、`long long` 8 字节且对齐不同）；一律按 `long long` 取会读到垃圾。

**坑 4（退出路径）：** 没有 `exit()` 可用时，用自写 `setjmp/longjmp` 从深层调用回到 `payload_main`，再由 `payload_main` 正常返回 BIOS；返回后 BIOS 打印 `PAYLOAD RETURNED` 并回到 setup。

## 4. 主循环归属：库 + 平台 main 的契约

**坑 5（“跑一下就回 BIOS”）：** 移植 doomgeneric 时，`D_DoomMain()` 在 `D_DoomLoop()` 里只调用**一次** `doomgeneric_Tick()` 就返回。载荷若只调用 `doomgeneric_Create()` 就结束，表现是启动日志打到 `I_InitGraphics` 之后没有任何帧、进程直接返回 BIOS。→ 平台层必须自己持有主循环（`for(;;) doomgeneric_Tick();`）。通用规则：移植前先确认“库入口是否自己循环”，不要假设。

**坑 6（argv 边界）：** `M_CheckParmWithArgs()` 的 `for (i = 1; i < myargc - num_args; i++)` 是**精确**的（最后一个能带参数的选项下标就是 `myargc-num_args-1`）。把它“修”成 `<=` 会读 `myargv[myargc]`，在 `strcasecmp` 里触发 load access fault。同时按 C 约定给 `argv[argc] = NULL` 并传真实 `argc`。

## 5. 构建与内存约束

```powershell
& $MiniPython scripts/mini.py build <app> --soft-float  # 已注册的应用 target，内部调用 payload backend
```

- 镜像必须 RPB1：`magic=0x31425052`、`load=0x01000000`、entry 在文件内且 4 字节对齐、两个 CRC。`scripts/primer20k_vexriscv/bios_image.py` 是格式定义，`tests/*_test.py` 里做主机断言。
- 载荷窗口 `0x01000000..0x07deffff`；栈 `0x07df0000..0x07dfffff`，初始 `SP=0x07e00000`；**程序内存 `memory_bytes` 必须 ≤ `0x06000000`**，因为装载时 BIOS 一边从 `0x07000000` 的暂存区读、一边往程序内存写，超过就会自己覆盖自己。
- `.bss` 也算进 `memory_bytes`，BIOS 会清零：板上日志里 `BOOT zero/cache sync:` 就是这笔开销（约 50 MiB → 5.6 s），不是卡死。
- 载荷镜像装载上限 8 MiB（`BIOS_IMAGE_MAX`）；数据文件（IWAD 之类）不要塞进镜像，用 SD/TFTP 单独传。
- 优化等级由 `mini.py build <app> --optimize` 决定（DOOM 默认 `-O2`，`mini.py build payload` 默认仍是 `-Os`）。

**坑 15（优化等级改变的是行为，不只是速度）：** 两处实板测量。① `i_video.c` 的 `cmap_to_fb` 在 `-Os` 下每个像素重读一次 `s_Fb.bits_per_pixel`（对 `out` 的写可能与它别名，编译器不敢外提），一帧 blit 233 ms、占整帧 69%；改成"调色板预生成 16-bit 值 + 一次写两个像素"并开 `-O2` 后是 31 ms。② 反过来，`-O2` 让 C 写的 `setjmp` 包装不再尾调用，`longjmp` 于是回到一个返回地址早被游戏栈覆盖的栈帧：`exit()` 打完自己的日志，游戏照跑（`-Os` 恰好尾调用，所以看起来是好的）。→ 需要"捕获调用者上下文"的原语（setjmp）直接写成汇编，别赌编译器的尾调用；性能结论要带分段计时（DOOM 是调试通道的 `t` 命令），别拿一个总帧率定因。

## 6. 主机验证：先 QEMU，再上板

用 QEMU 跑**真实产物**能挡掉大部分逻辑错误（地址无关差异只有在板端链接地址不同）：

```powershell
& .\.venv\Scripts\python.exe tests/<app>_test.py -v
& $MiniPython scripts/<app>_sim.py --soft-float --sample 25
```

**坑 7（semihosting 不可靠）：** `-semihosting-config enable=on` 下 `ebreak` 有时仍被送进客户机 trap handler（表现为 `SIM TRAP cause=0x00000003`）。→ 别用 semihosting 传文件：把测试数据用 `riscv-none-elf-objcopy -I binary` 链进镜像（`objcopy` 要用 `cwd=<out>` + 裸文件名，否则 `_binary_*` 符号名带路径）。

**坑 8（抓帧）：** 想在 QEMU 里要画面，用 QEMU monitor 直接读客户机内存：`-monitor tcp:127.0.0.1:<port>,server,nowait` + `pmemsave <符号地址> <字节数> <raw>`，再用主机脚本把 RGB565 转 PNG。采样只抓帧，不能顺手结束运行——否则没跑到“帧数/状态”目标就退出了。

**坑 9（静默=看起来像卡死）：** 长阶段（资源加载、初始化）必须打进度日志：每读 N KiB、每 N 帧各一行，日志走 UART（`WRITE`）。没有进度输出的脚本在真板上和挂死无法区分；同理，验收脚本要边跑边打印，而不是最后一次性输出。

## 7. SD 与网络：准备板上的数据

**坑 10（`fetch` 只收程序）：** `fetch FILE` 会做 RPB1 校验，**不能**用它传数据文件；数据文件用 `fetchraw FILE`（原样转存），两者上限都是 8 MiB，命令语义都是“创建新文件、不覆盖已有文件”。

**坑 12（每次读 1 字节的 libc 循环）：** 一次 `FS_READ` 就是一次 ecall + 一次 FatFs 读，BIOS 侧还有块缓存兜着，但缓存救不了调用次数。DOOM 的 `I_ReadFile` 用 `fread(buf,1,size,handle)`，如果 shim 的 `fread` 按 item 逐字节循环，4 MiB WAD 就是 268 127 次 ecall——实测光这一项 72 s，加上每块一次 `BIOS_POLL` 的 147 s，`R_Init` 直接跑到 15 分钟。shim 必须把 `sz*n` 合并成 ≤4096 字节的整块请求（`shim.c` 的 `fread`），并且把 POLL 限流（8 ms 一次）。判断办法就是打点：`shim: reads N KiB fs=ms/calls poll=ms/calls`，`bytes/calls` 接近 1 即中招。

**坑 11（TFTP 解释器要防火墙放行）：** 独立进程导出 TFTP 时必须用主机防火墙已放行的解释器（仓库 `.venv` 不在放行列表，用它监听 UDP/69 收不到板端 RRQ）；或者像 `scripts/primer20k_vexriscv/bios_verify.py` 那样在**同一进程内**起 `bios_tftp.PayloadServer`。BIOS 端是 512 B 停等 TFTP、不支持 options，实测约 243 KiB/s（4.2 MB 约 17 s）。

```powershell
# 主机侧（放行的解释器）
& 'C:\Users\bearice\AppData\Local\Programs\Python\Python313\python.exe' `
    scripts/primer20k_vexriscv/bios_tftp.py --bind <host-ip> --file <本地文件> --name <板端文件名>
```

```text
> set server <host-ip>
> fetchraw DATA.BIN          # 数据文件
> fetch APP.RPB              # 程序（会校验 RPB1）
> boot sd APP.RPB            # 或 boot net APP.RPB（TFTP 直灌，不落 SD）
```

## 8. BIOS 侧：文件服务与 SD 挂载（这次踩得最深的两处）

**坑 12（不要在 BIOS 里跨调用缓存文件句柄）：** 我先给 `FILE_READ` 加了“同路径单槽句柄缓存”，板上症状是 DOOM 第一次读 WAD 头就报 `Wad file DOOM1.WAD doesn't have IWAD or PWAD id`，随后 `FAULT cause=00000004 pc=010238d8 value=f881e769`。原因是 `firmware/drivers/sd_native.c` 的 `disk_initialize()` 每次挂载都会**重新初始化整个卡**（CMD0/CMD8/ACMD41/CMD9/ACMD6），跨这种重初始化保留的 `FIL` 状态会读到过期数据。→ 打开/定位/读取/关闭要么自成一体，要么把挂载做成幂等的（见坑 13）后再长期持有句柄。

**坑 13（每次操作都挂载很贵）：** `firmware/drivers/filesystem.c` 的 `sd_mount()` 原本 `f_mount(0,"",0)` + `f_mount(&fs,"",1)`，每次调用都强制重新初始化卡：每次约 12 ms，并且刷一行 `SD init PASS: native4 DMA …`。载荷按 4 KiB 读 4 MB 文件时，UART 被这条日志淹没、启动被拖慢到分钟级。→ 挂载一次并保持（`mounted` 标志 + `disk_status(0)==0` 判定卡仍在位/驱动未失状态），卡被拔或驱动报错后下一次使用自动重新初始化。改完再跑一遍 SD 相关主机回归。

**坑 14（别只加“读一次”的服务）：** 见第 2 节。提供完整的 FS 层后，`fetch`/`fetchraw`/`boot sd`/`settings` 和已有载荷（GW-BASIC 的 `FILE_READ`）都变成这一层的客户，行为一致、可复用。

**坑 16（板子会悄悄退回 Flash 里的旧 BIOS）：** DDR 里跑的 BIOS 只要复位一下就没了，板子回到 Flash XIP 那份；如果新服务只存在于 DDR 那份里，载荷看到的是一个**没有文件服务的 BIOS**，而它报的错却像载荷自己的问题。实板症状：DOOM 直接 `IWAD file 'DOOM1.WAD' not found!` 退出，而 SD 卡上那个 WAD 是好的。查法两条：`help` 里有没有 `fetchraw`/`ls [DIR]`（旧 0.9.1 没有），或 `size DOOM1.WAD` 是不是 `ERR unknown command`。根因在 ABI：`firmware/bios/boot.c` 的 dispatch 以 `default:return -1;` 收尾，**未知调用和“文件不存在”返回同一个 -1**，载荷侧无法区分；`bios_info.features` 里也没有 FILESYSTEM 位（只有 SD/VIDEO/USB/ETH/AUDIO）。→ 上板前用 `scripts/mini.py board run --image <你的 app.img>` 把状态钉住，别假设 DDR 里还是你上一次加载的那份；结构性修法是给 `features` 加 FS 位、并让未知 `a6` 返回 `-ENOSYS`（改动 ABI 语义，需要单独一次提交）。

## 9. 上板运行与验收

先确认状态，再动手；**任何往串口写字符之前都要确认板子在 `> ` 提示符**，否则你的字节会被正在跑的载荷当成按键（或干脆没人应答）：

```powershell
./mini.ps1 board status                      # 被动看有没有输出
./mini.ps1 board recover --build current     # 不在 setup 就先恢复
./mini.ps1 board run --image <app.img>       # SRAM gateware + UART 应用，不写 Flash
```

串口上应看到（以 DOOM 为例，通用含义见括号）：

```text
LOAD SD (dot=64 KiB) ..... done <n> bytes, <ms>     (BIOS 装载镜像)
BOOT checking image... / BOOT CRC: / BOOT copy:      (校验与复制)
BOOT zero/cache sync: <ms>                           (BSS 清零，几十 MiB 量级)
BOOT payload entry=01000000
<载荷自己的启动日志与进度行>                          (必须能看出它在推进)
```

载荷专属的实板验收脚本要**复用**现成组件，别另写一套：

```python
from scripts.primer20k_vexriscv.boot_upload import BootSession       # 提示符/状态机、DDR 失败检测
from scripts.primer20k_vexriscv.bios_tftp import PayloadServer       # 进程内 TFTP，见 scripts/primer20k_vexriscv/bios_verify.py
from scripts.primer20k_vexriscv.bios_image import unpack             # RPB1 校验
```

验收脚本至少要：① 先要 `> ` 提示符，拿不到就提示用 `board recover`；② 检查所需数据文件在 SD 上；③ 断言启动里程碑与进度（帧数/字节数），而不是只看“有输出”；④ 检查没有 `FAULT cause=`；⑤ 把 JSON 与串口日志写到 `build/<app>-verify/`；⑥ 不写 Flash、不复位、不改主机网络。DOOM 的实现是 `scripts/primer20k_vexriscv/doom_verify.py`。

BIOS 自身改动仍走 `board verify --suite firmware --image <app.img>`；提交前按 [提交 SOP](pre-commit-sop.md) 记范围与检查。

## 10. 边界清单（写进你的验收报告）

- 模拟了什么、没模拟什么（QEMU 不模拟真实 LCD/USB/SD、DDR 训练、TTY 行为）
- 物理观察限制：屏幕颜色、听音、真实键鼠输入有没有人看过
- 未跑项与失败项照实记录，别用“能启动”代替功能验收
- 只写 FPGA SRAM/UART；Flash 只在用户明确要求时由 `board update` 写

## 11. 流程纪律：绕开现成入口的代价（本次实际犯过，留作反面清单）

这一节记录的失败比上面的技术坑更贵：**功能最后做出来了，但过程大量重复劳动、抢串口、还让用户以为脚本死掉了**。[操作入口](../scripts/README.md) 最后一句写得很清楚：**不要另写临时 UART/Gowin 探针；补充能力要进入 `mini_ops/` 并给任务入口加支持条件和回归**。以下每一条都是违反它的实例。

**反例 1：在 `build/` 里堆临时串口探针。** 我一口气写了 `bios_peek.py`、`bios_cmd.py`、`bios_ping.py`、`bios_help.py`、`bios_fetch.py`、`bios_fetchraw.py`、`bios_boot_doom.py`，还有一个临时载荷 `wadprobe.c` 和 `doom-sim/probe.py`——全部是“发一条命令、读几秒、打印”的一次性脚本，事后全部删除。
→ 正确做法：看板的用 `./mini.ps1 board status`（被动观察）、恢复到 setup 用 `board recover`、临时运行用 `board run`、功能验收用 `board verify --suite firmware`；要固定一条可复跑流程，就写成 `scripts/<app>_verify.py` 这种正式工具（`bios_verify.py`、`gwbasic_verify.py` 是模板），并配 `tests/<app>_test.py`。

**反例 2：自己写串口状态机。** 我另写了一个 `Console` 类（自己的 `until`/`drain`/超时），而仓库已经有 `boot_upload.BootSession`：它管提示符成帧、`fresh_banner()`、`menu()`、DDR 初始化失败即抛错。
→ 正确做法：`from boot_upload import BootSession`，只写本应用特有的断言。

**反例 3：自己拉起 TFTP 进程。** 我另起子进程跑 `bios_tftp.py`，还硬编码一个“防火墙已放行的解释器”路径；`scripts/primer20k_vexriscv/bios_verify.py` 早就是**同进程** `PayloadServer(...).serve_once()` 多线程导出的写法。
→ 正确做法：复用 `bios_tftp.PayloadServer`（进程内、可 `exercise_retry`）；只有在你确实要单独常驻服务时才用子进程，并且必须用放行的解释器（见坑 11）。

**反例 4：脚本把输出憋到最后。** 验收脚本先重定向到文件、最后才打印，跑的时候终端几分钟一个字都没有，用户合理地以为卡死并中止了我三次。
→ 正确做法：边读边打印（`note()` 里 `stdout.flush()`）、每步 `[app] 正在做什么`、等待时每 15 s 打一行“还在等 X、已收 N 字节、还剩 M 秒”；长阶段由载荷自己打进度（坑 9）。

**反例 5：不确认板子状态就往串口写。** 脚本直接 `boot net …`，既不看 `> ` 提示符，也不管上一次运行是不是还在跑（这时写进去的字节会被正在跑的载荷当按键吃掉）。
→ 正确做法：先发一个 `\r` 要提示符，拿不到就停下并提示 `./mini.ps1 board recover --build current`；详见 `scripts/primer20k_vexriscv/doom_verify.py` 现在的写法。

**反例 6：先加一次性服务，再补文档。** 为了把 4 MiB 的 IWAD 送上卡，我先塞了一个专门的 `fetchraw` 命令（以及早先“按路径+偏移读一次”的 `FILE_READ`）。
→ 正确做法：先把 BIOS 侧能力做成**标准形状**（这次最终落成 `FS_OPEN/CLOSE/READ/WRITE/SEEK/STAT/MKDIR/UNLINK/RENAME` 一层），控制台命令、ecall 服务、旧接口都只是它的客户；`fetch`/`fetchraw` 也只是这层的调用者。一次性补丁会让每个后续应用再发明一遍。

**反例 7：没先读文档就动手。** 我是在用户点名之后才读 `scripts/README.md`、`docs/gwbasic.md`、`.agents/skills/riscv-mini-test/SKILL.md`、`docs/pre-commit-sop.md`（RTL 未变只需编译 + ABI/CRC + 主机回归 + 功能实板，不重跑 PnR）。
→ 正确做法：动手前读对应的 operation skill 与 `scripts/README.md`；验证矩阵看 SOP §3。

**准备写新脚本前，先问自己三个问题**：① 现成入口（`mini.py` 操作、既有 `*_verify.py`、`mini.py build payload`）能不能完成？② 需要的新能力是不是应该进 `mini_ops/` 或 `scripts/` 并带主机回归？③ 它会抢串口/假设板子状态吗？三个问题有一个答不上来，就别写那个脚本。
