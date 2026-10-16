# DOOM（BIOS 二级程序）

`firmware/apps/doom/` 是能在本机常驻 BIOS 下加载运行的 DOOM（doomgeneric 移植）。它是一个普通 RPB1 二级程序：ROM 把 BIOS 装进 DDR 并执行，BIOS 再从 SD 或 TFTP 把 `DOOM.RPB` 装到 `0x01000000` 并跳转；游戏只通过 BIOS 的 ecall 服务访问帧缓冲、文件和时钟。

视频不走复制：BIOS 的 480×272 RGB565 帧槽 stride 就是 960 B，`i_video.c` 每行正好推进 960 B，所以 `DG_ScreenBuffer` 直接指向非显示帧槽，画完 `VIDEO_PRESENT` 翻页（320×200 画面在 480×272 上居中，左右各 160 px、上下各 36 行黑边）。

## 构建与安装

```powershell
& $MiniPython scripts/mini.py build doom --soft-float      # 现役 rv32ima 配置必须软浮点
& $MiniPython scripts/mini.py build doom                   # 只在带 FPU 的 --feature fpu=on 配置上用
```

产物是 `build/doom-payload/BOOT.RPB`（约 366 KiB，入口 `0x01000000`，另声明约 48 MiB `.bss` 作为 DOOM 的堆）。

### 软浮点前提

现役 full 配置是 `rv32ima`（`status` 里 `FPU=0`），没有 F 扩展；硬浮点 payload 会以非法指令死在第一条浮点指令上。因此本机默认用 `--soft-float`（`-march=rv32im -mabi=ilp32`，浮点走 libgcc 软实现）：

- `--soft-float`：rv32im/ilp32，**现役配置用这个**
- 不带：rv32imaf/ilp32f，只在显式 `--feature fpu=on` 的 full 配置上跑

硬浮点使能机制与 [GW-BASIC 的说明](gwbasic.md#硬浮点前提) 相同（`firmware/boot/start.S` 只在 `MINI_FEATURE_FPU` 构建里置 `mstatus.FS`）。

### BIOS 前提

游戏需要本次新增的 BIOS 服务（`docs/bios.md` 的 ABI 表）：**文件系统 22–30**（`FS_OPEN/CLOSE/READ/WRITE/SEEK/STAT/MKDIR/UNLINK/RENAME`）和 **20 `KEY`**（原始 USB HID 按键）。只跑了 `FS_*` 之前的固件时，`W_Init` 会报 ``Wad file DOOM1.WAD doesn't have IWAD or PWAD id``。文件读路径底下是 SD 块缓存（4 KiB×32，见 [原生 SD](native-sd.md#块缓存)），一次 `FS_READ` 上限 4096 B 正好一个缓存块。

构建并临时运行（SRAM gateware + UART 应用进 DDR，不写 Flash）：

```powershell
& $MiniPython scripts/mini.py build system --hardware current --source firmware/bios/main.c --purpose doom-bios
& $MiniPython scripts/mini.py board run --image build/runs/<本次run>/firmware/app.img
```

`board run` 的收据写在 `build/operations/<UTC>-run/`。

### 把 WAD 和载荷放到 SD 卡

`DOOM1.WAD` 放在 SD 根目录。**要用 1.9 的 shareware WAD。** 1.8 的 `DEMO1/2/3` 和 1.9 引擎对不上：引擎会打印 `Demo is from a different game version! (read 108, should be 109)`，吸引模式里那个"玩家"其实是一条被带偏的录像，它会贴着墙走，屏幕上于是出现几十像素宽的纯色竖条——那是被放大几十倍的墙面贴图，看着像渲染坏了，渲染本身是对的。同一份代码换 1.9 的 WAD，告警消失，吸引模式画面正常（QEMU 同一条 150 s 时间线上，1.8 的帧右侧是灰色巨墙，1.9 的帧是正常走廊）。

两个都是 4,196,020 B，只差版本：

| 版本 | md5 | sha256 |
| --- | --- | --- |
| **1.9（用这个）** | `F0CEFCA49926D00903CF57551D901ABE` | `1D7D43BE501E67D927E415E0B8F3E29C3BF33075E859721816F652A526CAC771` |
| 1.8（会报录像版本不符） | `5F4EB849B1AF12887DEC04A2A12E5E62` | `BB449C7480E9A02A62012D041406E8E43DAA51CAA0650646D1307D8650B8F837` |

`build/doom-assets/` 不进 Git。1.9 的取法——Debian non-free 包里的就是原版 `doom1.wad`，实测无版本告警：

```powershell
Invoke-WebRequest http://mirror.volia.net/debian/pool/non-free/d/doom-wad-shareware/doom-wad-shareware_1.9.fixed-5_all.deb -OutFile build/doom-assets/doom-wad.deb
# .deb 是 ar 包：取出 data.tar.xz，解压拿 ./usr/share/games/doom/doom1.wad，改名 DOOM1.WAD，核对上表的 1.9 校验和
# 老的 1.8 来源 https://www.gamers.org/pub/idgames/idstuff/doom/doom-1.8.wad.gz 还在，但会撞上上面的录像版本问题
```

推上卡用 BIOS 的 `fetchraw`（TFTP 原样文件，≤8 MiB；`fetch` 只接受校验通过的 RPB1 程序）：

```powershell
# 主机侧导出，必须用防火墙已放行的解释器（仓库 .venv 不在放行列表）
& 'C:\Users\bearice\AppData\Local\Programs\Python\Python313\python.exe' `
    scripts/bios_tftp.py --bind 169.254.25.153 --file build/doom-assets/DOOM1.WAD --name DOOM1.WAD
```

```text
> set server 169.254.25.153
> fetchraw DOOM1.WAD          # 约 17 s（4.2 MB）
FETCHRAW SAVED DOOM1.WAD
```

载荷本身可以放卡（`fetch DOOM.RPB`，然后 `boot sd DOOM.RPB`），也可以每次走 TFTP（`boot net DOOM.RPB`）。

## 运行

```text
> set server 169.254.25.153
> boot sd DOOM.RPB          # 或 boot net DOOM.RPB
LOAD SD (dot=64 KiB) ..... done 364584 bytes, 829 ms
BOOT checking image...
BOOT CRC: 344 ms
BOOT copy: 66 ms
BOOT zero/cache sync: 5576 ms      # 48 MiB BSS 清零，正常
BOOT payload entry=01000000
DG_Init: 480x272 fb=0x0081ec80 stride=960
Z_Init: Init zone memory allocation daemon.
W_Init: Init WADfiles.
 adding DOOM1.WAD
I_Init: Setting up machine state.
M_Init: Init miscellaneous info.
R_Init: Init DOOM refresh daemon - ....
P_Init: Init Playloop state.
I_InitGraphics: framebuffer: x_res: 480, y_res: 272, ... bpp: 16
I_InitGraphics: Auto-scaling factor: 1
DOOM presents=100 ms=...           # 每 100 帧一行，说明在出画面
shim: reads 1282 KiB fs=1642 ms/1007 calls poll=131 ms/216 t=138573 ms
                                  # 每读 256 KiB 一行：fs= 累计 ecall 时间与次数，poll= 同理
```

`fs=` 的**次数**是判断瓶颈的第一线索：字节数除以调用次数接近 1，说明 shim 在逐字节发 `FS_READ`（见 [移植指南的坑 12](bios-payload-porting.md)），慢的不是 SD 也不是 CPU。

键位（USB 键盘，BIOS 的 `KEY` 服务给的是 HID usage）：

| 键 | 作用 |
| --- | --- |
| 方向键 | 前进/后退、转向（vanilla 默认） |
| Ctrl | 开火 |
| Space | 开门/使用 |
| Enter / Esc | 菜单（New Game、存读档、设置） |
| F12 | 退出 DOOM，回到 BIOS setup 提示符 |

退出时程序 `longjmp` 回 `payload_main`，切回文字模式并返回 BIOS（打印 `PAYLOAD RETURNED`）。

## 验证

主机回归（RPB1 布局 + QEMU 上跑真实产物）：

```powershell
& .\.venv\Scripts\python.exe tests/doom_test.py -v
& $MiniPython scripts/doom_sim.py --soft-float --sample 25
```

`scripts/doom_sim.py` 用一个 shim 顶替 BIOS：提供 `bios_info`、按 ABI 服务 ecall（UART、确定性时钟、面板、脚本化按键、把 `DOOM1.WAD` 作为唯一只读文件），异常打印成 `SIM TRAP cause=... mepc=...`。**它不模拟**真实 LCD/USB/SD、DDR 训练、BIOS TTY 行为，也不校验 RPB1 镜像本身（那由 `tests/doom_test.py` 的主机断言做）。payload 不是位置无关的，仿真把同一批目标文件重链接到 `0x80000000`（QEMU virt RAM 基址），只有地址常量不同。

实板验收：

```powershell
& $MiniPython scripts/doom_verify.py --host-ip 169.254.25.153
```

它**先确认板子在 BIOS setup 提示符**（看不到就停下，让你先跑 `scripts/mini.py board recover --build current`），然后复用 `boot_upload.BootSession` 驱动控制台、用进程内 `bios_tftp.PayloadServer` 导出载荷，检查：SD 上有 `DOOM1.WAD`、TFTP 收到 RRQ、`W_Init/adding DOOM1.WAD/R_Init/P_Init/I_InitGraphics`、200 帧心跳、无 `FAULT cause=`。结果写 `build/doom-verify/doom-verification.json` 与 `doom-console.log`。DOOM 不会自己返回 BIOS（F12），所以脚本跑完游戏仍在运行；它不写 Flash、不复位板子、不改主机网络与防火墙。

BIOS 自身的功能验收仍走既有入口（本机 RTL 未变时按 [提交 SOP](pre-commit-sop.md) 只需编译 + ABI/CRC + 主机回归 + 功能实板）：

```powershell
& $MiniPython scripts/mini.py board verify --build current --suite firmware --image <app.img> --soak-seconds 60
```

本机 RTL 未变时用 `--build current`（合格的 v0.9.1 gateware）+ `--image` 指到自己的 BIOS 镜像；`mini.py build` 产出的 run 目录没有综合/PnR，直接当 `--build` 会被拒（"Board operations require completed PnR with setup/hold 0/0"）。构建目录以日志里的 `Build directory:` 为准，别按 `--purpose` 名字去猜——失败的尝试也会留下同前缀的目录。

## 调试通道

载荷在 UART 上监听单字符命令（`firmware/apps/doom/dg_debug.c`），主机侧统一走 `scripts/doom_debug.py`。载荷运行期间 BIOS 不再读控制台，同一时刻只有一个进程能占着 COM 口，所以换载荷的调试要让它自己负责启动：

```powershell
# 一条命令：BIOS 提示符 → TFTP 启动 → 等 I_InitGraphics → 暂停 → 取帧 → PNG
& $MiniPython scripts/doom_debug.py --boot --host-ip 169.254.25.153 --settle 25 --command frame
& $MiniPython scripts/doom_debug.py --command source      # 8-bit 源像素 + 引擎调色板
& $MiniPython scripts/doom_debug.py --command timing      # 分段耗时
& $MiniPython scripts/doom_debug.py --compare             # 不上板，重算差异（必须紧跟 source，见下）
```

`--compare` 读的是 `--out-dir` 里**当前那份** `rgb565-*.png`/`index8-*.png`/`palette.raw`，每次取帧都会原地覆盖。所以它必须紧跟在 `frame`+`source` 之后、中间不夹任何别的取帧；否则比的是两个不同时刻的帧，会读出 80% 上下的假差异（我这么踩过一次）。要留多张，就每次换一个 `--out-dir`。

| 命令 | 作用 |
| --- | --- |
| `p` | 暂停/继续。冻结的是 `DG_GetTicksMs`，`i_timer.c` 的 `I_GetTime` 全从它导出，所以整个游戏时钟停住；恢复时把暂停时长扣掉，不会补帧 |
| `s` | 暂停状态下走一帧 |
| `f` / `F` | `f` 取面板正在显示那一格里的 320×200 画面区；`F` 把**两个帧槽都**取回来（各 480×272 含黑边），主机侧直接判 slot0/slot1 是否一致 |
| `g` | 取回 `I_VideoBuffer` 的 8-bit 源像素**和**引擎真正在用的 `colors[]` 表 |
| `t` | 分段耗时 tick/blit/draw/sleep/poll，带次数与均值；窗口每次打印后清零 |
| `i` | shim 读盘统计与累计 CRC32 |
| `v` | 面板地址、stride、信箱偏移、暂停状态 |
| `m` | 游戏状态一行：`gametic`、`lt`（leveltime，本关已跑 tic 数）、`ticdup`、`state`、`ep/map`、`demo`、`viewx/y/z`、`angle`、`health`；每次 dump 也会先打这一行 |
| `1`–`5` | 在面板上钉一张已知图案（竖条/1px 棋盘/4×4 棋盘/横条/渐变），游戏继续在后台跑；`0` 放开 |
| `c` | 循环切换每帧空白：黑（默认）→ 索引 `0xff`（取证）→ 关。切到 `0xff` 时，dump 里还是 `0xff` 的像素就是"这一帧根本没画过"的位置 |
| `x` | 退出载荷回 BIOS |

线格式是 `DBGDUMP <kind> <bytes> <w> <h> <bpp> <stride>` + 连续 base64 + `DBGEND <crc32>`，每条命令以 `DBG done` 收尾（`g` 连发两块，只读到第一个 `DBGEND` 会丢掉调色板）。base64 必须整条流连续、只在结尾补一次 `=`：按行补 `=` 会让解码器在第一行末尾就停下。

**为什么这套东西能定位问题**：`g` 给的是引擎画出来的 8-bit 像素，`f` 给的是面板里的 RGB565，`--compare` 用板子自己交上来的 `colors[]` 把前者换算成 RGB565 再逐字节比。源干净、面板脏 → 问题在 blit 或 DMA；源本身就脏 → 问题在数据或引擎。用 `colors[]` 而不是 WAD 里的 PLAYPAL，是因为 `I_SetPalette` 还过了一层 `gammatable[usegamma]`（不是恒等表），拿 PLAYPAL 比会看到 36.74% 的假差异。

暂停期间载荷仍在跑 `dbg_service()`，串口不会失联；取帧期间游戏时钟同样冻结，15 s 的传输不会变成 15 s 的世界跳变。这条"面板 = 引擎源图"的等式由 `tests/doom_test.py` 的 `DebugChannelTest` 锁住：它在主机侧把 `g` 取回的源图按板子的调色板重跑一遍 blit，和 `f` 取回的 RGB565 逐字节比，必须 `0/200 rows differ`。换 WAD、换优化等级都会改变具体像素，所以这里不记 CRC 的具体值——但这条 diff 必须一直是 0，实板上的读数同样是 0。

**闪烁要问两个帧槽，不是问一帧**：面板在 slot0/slot1 之间交替，"画面闪"只可能是两格内容不一样，所以单张截图永远证明不了。`F` 在暂停态把两格都取回来逐字节比——一致就说明面板交替的是同一张图的两份拷贝，帧缓冲这一层不可能闪；不一致，差异落在哪些行本身就是答案。QEMU 暂停态实测 `slot0 vs slot1: 0 bytes, 0/272 rows differ`。

**取帧期间串口是独占的**：base64 流里混进任何 `printf` 都会毁掉这一帧（DOOM 每 100 帧那条心跳就干过）。现在 dump 的数据体写完后 shim 的 `con_write()` 才恢复输出（判据是 `dbg_uart_busy()`），期间日志直接丢弃；头尾两行是整行，周围混进东西主机侧会跳过，所以只有数据体需要独占。`parse_dumps()` 另外会把出现在行中间的 `DBGDUMP`/`DBGEND` 重新锚回行首——DOOM 有些启动日志结尾不带换行，标题会粘在日志尾巴上。

**比对两边同一帧要认 `lt=`，不要认墙上时钟，也不要认 `gametic`**：吸引模式的录像回放是确定性的，同一个 `leveltime`（本关已经跑过的 tic 数）必然对应同一帧画面，所以 `lt` 才是实板与 QEMU 之间的锚。`gametic` 在标题画面那段时间照样往上涨，而录像什么时候开播取决于最后一次按键什么时候过期，两边差几百个 gametic 很正常；墙上时钟更没用，实板 3 fps、QEMU 40 fps。用法是让 dump 自己带状态行（`dump_area()` 在 `DBGDUMP` 头之前打 `DBG state ...`，放在头之前是因为主机把头的下一行就当 base64 正文），两边挑 `lt` 相同的那两张图比。**`lt`、相机状态相同而画面不同 → 移植有确定性问题；画面相同 → 仅能说明两边一致，不能证明渲染正确。** 2026-10-12 的竖条修复就是反例：板子和 QEMU 都使用同一个错误的 `abs` 宏，两边会逐字节画出相同的错误墙面。

**数据通路靠累计 CRC 一次性排除**：`i` 报的 `readcrc=` 是 shim 对"曾经 `fread` 返回过的每个字节"算的 CRC32，同一份 shim 也跑在 QEMU 里，所以把两边同检查点的值对上，就一次排除了 SD 块缓存 + FatFs + `FS_READ` + shim 整条链路。实板与 QEMU 在 261/522/768/1024/1282/1536 KiB 六个检查点上分别是 `0xd1fb97ef 0x656a036e 0xadd74aec 0xc231b549 0x262ddd42 0x46bc09e6`，逐值相同——"块缓存读坏数据"这条怀疑不需要再靠猜纹理来推翻。

`--command bios-status` 在 BIOS 提示符下取一次 `status`，重点是 `RGB LCD … underflows=`（LCD DMA 的欠载计数，复位以来累计，所以要看一次运行前后的差）。载荷占着串口时它会先发 `x` 让载荷退出再问，前提是载荷的 `exit()` 真能回到 BIOS（见 [bios-payload-porting.md 坑 15](bios-payload-porting.md)）。

**闪烁有客观证据，而且不在软件里**：跑一次 DOOM 前后各取一次 `bios-status`。空板待机 `underflows=00000000`（1202 个面板帧）；跑一轮吸引模式之后 `underflows=00000089`，同期 `frames` 从 `0x4b2` 走到 `0x2ac9`，即 **9751 个面板帧里欠载 137 次（1.4%）**——面板每 60 Hz 重扫一次，游戏只有 3 fps，所以每一次欠载都会在屏幕上留下可见的一闪。扫描器只有 8 KiB FIFO（`gateware/video.py` 的 `stream.SyncFIFO(depth=8192//2)`），而 `LCDScan` 是每个像素时钟消费一个像素、`underflow = draw & ~sink.valid`，8 KiB = 4096 像素 ≈ 450 µs 的余量，也就是说 DDR 有半毫秒没理 LCD DMA。**把它当成 SD 读卡的锅试过一次，结论是否**：把块缓存从 32 块（128 KiB）加到 512 块（2 MiB）之后，同样的 DOOM 流量还是同样的 1007 次 `FS_READ`（次数由调用方 4 KiB 合并决定，不由卡决定），欠载反而略多（6823 帧 148 次）。**所以欠载跟的是 CPU 的访存流量，不是读卡流量**——软件渲染器在 2 KiB 写直通 L1 上疯狂取纹理，把 LCD 的取数挤饿了。真正的解在 gateware 的 DDR 仲裁优先级或 FIFO 深度，属于 PnR 改动，不在移植范围内；软件侧能证明的都证明了：帧内容逐字节正确、两个帧槽一致、present 没有失败过。

**取到的是"完整的一帧"，不是"命令那一刻的缓冲"**：调试命令是从 shim 的轮询里进来的，可能落在这一帧的中间，于是 dump 会拍到半张画好的图。现在 `I_StartFrame` 调 `dbg_frame_began()` 把帧标记为未完成、`I_FinishUpdate` 末尾调 `dbg_frame_done()`；帧没画完时 `f`/`F`/`g` 进 `g_queue[]` 排队，等帧边界再按原顺序执行（其余命令照旧立刻跑，否则启动那几分钟里什么都问不出来）。这个队列是 `tests/doom_test.py` 的 `DebugChannelTest` 逼出来的——空白填充一上上去，`blit diff` 立刻从 0 变成 76 470，因为 dump 开始落在"已清空、还没画"的窗口里。

**但暂停会把排队的取帧永远饿死**：那个队列只在帧边界排空，而帧边界是 `payload_main` 循环里的 `dbg_service()`。引擎可能正卡在一个**等时钟**的循环里——`d_main.c:309-328` 的擦窗是 `do{ nowtime=I_GetTime(); tics=nowtime-wipestart; I_Sleep(1);}while(tics<=0);`——而暂停冻住的正是这个时钟，于是它永远到不了 `DG_DrawFrame`，边界服务再也不跑，排队的 `f` 就没了。实板上的现象：`--command frame` 什么都不返回（连 `DBG done` 都没有），不排队的 `m`/`t` 还能答，`t` 报 `frames=0`，恢复后游戏照跑 3.59 fps——看着像取帧坏了，其实是暂停把引擎钉死在帧中间。修法是 `DG_SleepMs` 在 `dbg_held()`（暂停或正在 dump）时调 `dbg_service_held()`：既然引擎在等一个被冻住的时间，这一帧不可能自己画完，就判它"最终"再跑边界服务；排队的 `f`/`F`/`g` 仍在同一次调用里按序跑完，所以它们描述的仍是同一块缓冲，`blit diff` 依旧 0。
> 别图省事在 `p` 那一支直接把帧标记为已完成——试过：`f` 立刻在帧中间跑掉、`g` 在别处跑，两次 dump 来自不同帧，`DebugChannelTest` 当场 `168/200 rows differ, 59.74%`。排队的意义就是"同一次服务里跑完"。

**每帧空白（`c`）治的是"上一屏残留"**：`I_StartFrame` 先把 `I_VideoBuffer` 的前 `viewheight` 行涂黑，DOOM 每个渲染帧都会重画整个画面（`d_main.c:222` 传 `ST_Drawer(true, true)`），所以涂掉是安全的。用 `c` 切到索引 `0xff` 量了一下到底有多少像素是引擎不写的：**关卡第 0 tic 全画面都没写，第 1 tic 剩 523 个像素（1.0%）没写，形状是第 101–120 行的一块三角形（第 101 行 55–60、第 110 行 0–44、第 119 行 0–0），从第 34 tic 起一个都没有。** 原始 DOOM 在 35 fps 下这一 tic 的残留根本看不见，本移植 3 fps 下它要在面板上摆三分之一秒——标题页、制作名单、吸引模式互相切换时看到的那一下"色块"就是这个。涂黑一次 64 KiB，约占一帧的 2%，所以默认开着。

**画面不确定 = 引擎读到了从没写过的 zone 内存**：把同一份源码分别用 `-O0` 和 `-O2` 编进 QEMU，相机状态逐比特相同（`gametic=172 lt=1 state=0 ep=1 map=5 demo=1 pos=(-224.00,-624.00) angle=0x40000000`），画面却有 **45 726/64 000 字节（71.45%）不一样**；`lt=65/589/1219` 又完全相同。这就是"渲染器读了它自己从没写过的内存"，读到的值取决于分配历史，所以换优化等级就换画面。修法在 `i_system.c` 的 `I_ZoneBase()`：拿到 zone 之后整体清零（vanilla 不清）。**清零后 `-O0` 与 `-O2` 在 `lt=1/16/30` 逐字节一致（`a59d4059`/`73362f8d`/`28be0e09`）**，实板（zone 在 BSS，本来就被 BIOS 清零）与 QEMU（zone 来自 shim 堆）也走同一条路。代价是一次 16 MiB memset，约 1.8 s，在 140 s 的启动里占 1.3%。

## 纯色竖条修复（2026-10-12）

根因是 `shim_include/stdlib.h` 的 `#define abs(x) ((x)<0?-(x):(x))`。`R_StoreWallRange` 调用 `abs(rw_normalangle-rw_angle1)`，两个参数都是无符号 `angle_t`。标准 `abs(int)` 先转换参数，宏却保留无符号类型；例如 `0xf0000000` 应解释为 `-0x10000000` 后取绝对值，宏却返回 `0xf0000000`，随后的 ANG90 钳位使墙面距离接近零、投影异常放大，形成整高纯色竖条。它不仅发生在演示开头：修前 E1M3 的 `lt=2552/3136/3381/3636` 实板截图仍有竖条。

修复为接受 `int` 参数且只求值一次的内联函数。`tests/doom_shim_test.py` 在 RV32 QEMU 上验证墙面调用模式、角度回绕、正反方向和参数单次求值；修前 `-O0/-O2` 都在第一个角度用例失败，修后都通过，已纳入 `tests/doom_test.py`（11 tests OK）。同一 E1M5 `lt=1`、逐位相同相机状态下，修前有竖条、修后为正常房间。实板新载荷的 E1M5 `lt=17/274/522/771` 间隔截图同样恢复正常，修后实板 frame/source 比对为 0/128000 字节差异。

证据：`build/doom-debug/series-20261012/`（修前间隔截图）、`build/doom-render-sim-fixed/`（QEMU）、`build/doom-debug/fixed-20261012/`（实板）。旧的「QEMU 与板上相同，所以属于原版行为」推断无效：两边共享相同的 shim，能共同重现同一个移植缺陷。

## 边界与已知限制

- **鼠标不支持**：doomgeneric 的 `i_input.c` 从不投递 `ev_mouse`，只有键盘这条路；要加鼠标得改 `i_input.c`（BIOS 侧 `MOUSE` 服务本身是好的）。
- **没有声音**：`FEATURE_SOUND` 关闭（`doomfeatures.h`），`-nosound/-nomusic/-nosfx` 默认传入。
- **WAD 版本必须是 1.9**：1.8 的 WAD 会让吸引模式打印 `Demo is from a different game version! (read 108, should be 109) … This appears to be v1.8.`，随后那条被带偏的录像会把镜头怼在墙上，画出满屏的放大贴图，很容易被当成渲染故障（见[上面](#把-wad-和载荷放到-sd-卡)）。自己开新游戏不受影响，但默认就该用 1.9。
- **"画面有色块/条带"查到哪一步了**：**帧缓冲的内容**已经排除干净——`g` 与 `f` 的逐字节 diff 是 0（blit 与主机模型完全一致）、暂停态两个帧槽逐字节一致、`readcrc` 与 QEMU 六个检查点逐值相同（0xd1fb97ef 0x656a036e 0xadd74aec 0xc231b549 0x262ddd42 0x46bc09e6）。真正修掉的原因有两个：1.8 的 WAD（上一条）和每帧残留像素（`c` 那一节）。**仍有一个硬件侧问题，另一个软件问题已经修复**：① 面板 DMA 在 CPU 满载时会欠载（1.4% 的面板帧），这是闪烁的来源，见[上面](#调试通道)；② 纯色竖条的旧结论已撤回：它是 shim 的 `abs` 宏破坏无符号角度差的有符号转换所致，2026-10-12 已修复，详见下面。
- **存档**：`FS_WRITE` 已经可用，`M_SaveDefaults`/存档会写 SD 根部（`.savegame/` 目录由 `FS_MKDIR` 创建），但**尚未在板上实际验证过存档往返**；`remove`/`rename` 走 `FS_UNLINK`/`FS_RENAME`。
- **性能**：2026-10-11 实板（v0.9.1 gateware、60 MHz rv32ima 无 FPU、DDR 120 MHz、L1 2 KiB 写直通 + 4 KiB L2 写回）用 `-O2` 构建（414 036 B）测得 **3.37 帧/秒**——`--command timing` 的 93 546 ms 窗口里 316 帧，`present_fails=0`。分段是 `tick` 平均 288 ms、`blit` 28 ms、`draw` 9 ms、`poll` 2528 ms/1917 次（摊到每帧 0.54 ms）：**blit 已经不是瓶颈，剩下的 ~266 ms/帧就是软件渲染本身**。两处提速是实打实的：`cmap_to_fb` 走 `rgb565_palette[]` 预表 + 两个像素合成一次 32-bit 写（原来逐像素重读 `s_Fb.bits_per_pixel`，写 `out` 可能别名到它，`-Os` 提不出来）把 blit 从 233 ms/帧打到 29 ms；`pump()` 的 `BIOS_POLL` 限流到 20 ms 一次，把每帧 15 ms 的轮询摊到 0.54 ms。`-O3` 只再快 2.4%（3.42 fps）却大 13%（466 348 B），所以留在 `-O2`。启动侧同样不再是瓶颈：`R_Init` 的 1282 KiB 只花 `fs=1642 ms/1007 calls`（逐字节读的年代是 261 KiB / 268 127 次 / 72 255 ms）。
  **测量口径**：用 `--command timing` 的窗口，别用启动窗口（60 s 窗口含启动只有 0.89 fps），也别只靠 `DOOM presents=` 心跳——100 帧一行 ≈ 40 s 才有一条，被动收 UART（`scripts/mini.py board status --seconds 30`，上限 30 s）经常一条收不到，那不是死机。历史数字 2.48 fps 是 `-Os` 时代由心跳算的（`presents=100 ms=174958` → `presents=700 ms=416866`）。再要提速就得动软件渲染路径或提高 CPU/DDR 频率，不在本次范围。
- 只写 FPGA SRAM 和 UART；Flash 只在用户明确要求时由 `board update` 写。
- 程序内存硬约束：镜像 `memory_bytes` 必须 ≤ `0x06000000`（装载时一边读暂存区一边写程序内存），本载荷约 50.4 MiB，满足。

## 静态 Newlib（2026-10-12）

Doom 现在通过 `mini.py build payload --libc newlib` 静态链接工具链标准库，使用 [app libc 运行库](payload-libc.md)。旧 `shim_include/` 和手写 stdio/allocator/math/setjmp 已移除，`shim.c` 只保留 CRC、进度和调试钩子。BIOS 固件与 ecall ABI 保持不变。`exit()` 先由 Newlib 清理，再通过共用 runtime 返回 `payload_main`；返回后的提示信息直接使用 BIOS_WRITE。

上面的 fread CRC 六检查点属于旧 shim 的历史证据。Newlib 会缓冲/预读，当前 `i` 统计的是底层 `_read` 字节；只能比较同一版本 runtime、同一工作负载的板子与 QEMU，不能沿用旧 CRC 常量判断失败。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `firmware/apps/doom/` | doomgeneric 源码（Chocolate Doom 派生）+ 本地补丁：声音模块移除、`i_video.c` 默认 `rgb565`、`i_system.c` 去掉 GUI 弹窗并在 `I_ZoneBase()` 里清零 zone（见上面的确定性一节）、`d_loop.c` 去掉网络头依赖、`m_argv.c` 保持原有参数边界 |
| `firmware/apps/doom/dg_bios.c` | 平台层：`DG_Init/DG_DrawFrame/DG_SleepMs/DG_GetTicksMs/DG_GetKey/DG_SetWindowTitle`、HID usage→doomkeys 映射、F12 退出、UART 心跳 |
| `firmware/apps/doom/shim.c` | CRC、进度和调试钩子；标准库实现由静态链接的 Newlib 提供 |
| `firmware/libc/` | 共用 app syscall runtime、48 MiB Doom 堆边界和退出返回；不编进 BIOS 固件 |
| `scripts/mini.py build doom` | 构建 `BOOT.RPB`（转调 `mini.py build payload`，固定 `DOOMGENERIC_RESX/RESY=480/272`） |
| `scripts/doom_sim.py` | QEMU 仿真（BIOS shim + 面板抓帧 + 脚本化按键） |
| `scripts/doom_verify.py` | 实板验收（复用 `BootSession`/`PayloadServer`） |
| `firmware/apps/doom/dg_debug.c`、`dg_debug.h` | UART 调试通道：暂停/单帧/取帧/取源像素与调色板/分段耗时/已知图案 |
| `scripts/doom_debug.py` | 调试通道主机侧：发命令、解 base64、验 CRC、出 PNG、`--compare` 逐字节比对 |
| `tests/doom_test.py` | 主机回归：RPB1 布局 + QEMU 跑真实载荷 |


## INDEX8 调色板实验

`scripts/mini.py build doom --indexed --soft-float --output-dir build/doom-palette-payload` 编译实验载荷。需要带调色板 CSR 与 BIOS features bit 5 的硬件/固件组合；BIOS 复位及文字模式使用 RGB565，旧服务编号和寄存器偏移保留。不要把实验载荷当作旧 BIOS 已支持的格式。

`I_SetPalette` 计算 gamma 后的 RGB565 表，`DG_SetPalette` 经 BIOS 上传非活动的 256 项调色板并逐项读回校验。`BIOS_VIDEO_PRESENT_INDEX8(slot,bank)` 用一个 CSR 提交帧缓冲和调色板；硬件在开始下一帧 DMA 时锁存，两组表共 512×16 bits，用一块 BSRAM。显示端每个 16-bit FIFO word 拆成先低后高的两个索引，再同步查表。

`BIOS_VIDEO_MODE(BIOS_GRAPHICS_INDEX8)` 返回 format=8、stride=480；RGB565 是 format=565、stride=960。`BIOS_VIDEO_WINDOW(x0,y0,x1,y1)` 的结束坐标为 exclusive，只在停止并排空旧帧后配置。Doom 的窗口是 (80,36)-(400,236)，外围由硬件输出黑色。离开载荷时恢复 RGB565 文字控制台。BIOS 仍然不链接 Newlib，应用继续各自静态链接。

480×272 的单帧 DMA 数据量由 261120 降至 130560 bytes，双缓冲实际扫描区从 510 降至 255 KiB。实验复用 BIOS 原有的 510 KiB 静态显存池，因此这一步不会回收整个 BIOS 预留池，也不会因为 DDR 显存减少而释放 BSRAM。INDEX8 仍按行复制 320×200 的引擎缓冲。

验证入口：`tests/lcd_palette_verilog_test.py --iverilog C:/msys64/ucrt64/bin/iverilog.exe` 检查索引顺序、256 项颜色、调色板读回、帧/表同步切换、窗口和每帧 8160 个 16-byte DMA burst；RGB565 是 16320 个。`--benchmark-checksum` 在计时完成后报告 source、palette、video 与 mapped565 校验，性能测试用不带此选项的独立载荷。实验布局、时序、实板验收和帧率见 `reports/performance/doom-palette-experiment.md`。


## 默认配置

已验收的默认硬件模板由 `scripts/mini.py builds show current` 查询；`scripts/mini.py build` 和 `board run` 默认使用该模板。当前配置为 RV32IMA/MMU、无F/C、60/120MHz、ROM8KiB、L2 8KiB、快速移位和流水乘法，支持 INDEX8 双调色板。CPU RTL 与完整参数保存在对应构建 recipe/提交快照，复现时使用模板输入，不能用未经验收的其它 CPU 变体替代。

`scripts/mini.py build doom` 默认生成 RV32IM/ILP32 的 INDEX8/Newlib 载荷。`--soft-float` 仍接受；只有另行验收的 FPU CPU 才使用 `--hard-float`。`--indexed` 仍接受；显式 `--rgb565` 用于旧 BIOS 或 RGB565 性能对照。BIOS 文字控制台仍使用 RGB565，应用退出时恢复文字模式。16KiB L2 调色板组合的时序失败记录保留；默认选择已经达到0/0并完成实板验收的8KiB配置。
