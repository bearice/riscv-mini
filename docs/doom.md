# DOOM（BIOS 二级程序）

`firmware/apps/doom/` 是能在本机常驻 BIOS 下加载运行的 DOOM（doomgeneric 移植）。它是一个普通 RPB1 二级程序：ROM 把 BIOS 装进 DDR 并执行，BIOS 再从 SD 或 TFTP 把 `DOOM.RPB` 装到 `0x01000000` 并跳转；游戏只通过 BIOS 的 ecall 服务访问帧缓冲、文件和时钟。

视频不走复制：BIOS 的 480×272 RGB565 帧槽 stride 就是 960 B，`i_video.c` 每行正好推进 960 B，所以 `DG_ScreenBuffer` 直接指向非显示帧槽，画完 `VIDEO_PRESENT` 翻页（320×200 画面顶部对齐、左右和下方留黑）。

## 构建与安装

```powershell
& $MiniPython scripts/doom_image.py --soft-float      # 现役 rv32ima 配置必须软浮点
& $MiniPython scripts/doom_image.py                   # 只在带 FPU 的 --with-fpu 配置上用
```

产物是 `build/doom-payload/BOOT.RPB`（约 366 KiB，入口 `0x01000000`，另声明约 48 MiB `.bss` 作为 DOOM 的堆）。

### 软浮点前提

现役 full 配置是 `rv32ima`（`status` 里 `FPU=0`），没有 F 扩展；硬浮点 payload 会以非法指令死在第一条浮点指令上。因此本机默认用 `--soft-float`（`-march=rv32im -mabi=ilp32`，浮点走 libgcc 软实现）：

- `--soft-float`：rv32im/ilp32，**现役配置用这个**
- 不带：rv32imaf/ilp32f，只在显式 `--with-fpu` 的 full 配置上跑

硬浮点使能机制与 [GW-BASIC 的说明](gwbasic.md#硬浮点前提) 相同（`firmware/boot/start.S` 只在 `MINI_FEATURE_FPU` 构建里置 `mstatus.FS`）。

### BIOS 前提

游戏需要本次新增的 BIOS 服务（`docs/bios.md` 的 ABI 表）：**文件系统 22–30**（`FS_OPEN/CLOSE/READ/WRITE/SEEK/STAT/MKDIR/UNLINK/RENAME`）和 **20 `KEY`**（原始 USB HID 按键）。只跑了 `FS_*` 之前的固件时，`W_Init` 会报 ``Wad file DOOM1.WAD doesn't have IWAD or PWAD id``。文件读路径底下是 SD 块缓存（4 KiB×32，见 [原生 SD](native-sd.md#块缓存)），一次 `FS_READ` 上限 4096 B 正好一个缓存块。

构建并临时运行（SRAM gateware + UART 应用进 DDR，不写 Flash）：

```powershell
& $MiniPython scripts/mini.py build --from current --app firmware/bios/main.c --purpose doom-bios
& $MiniPython scripts/mini.py board run --image build/runs/<本次run>/firmware/app.img
```

`board run` 的收据写在 `build/operations/<UTC>-run/`。

### 把 WAD 和载荷放到 SD 卡

`DOOM1.WAD` 放在 SD 根目录（4,196,020 B，shareware，sha256 `BB449C7480E9A02A62012D041406E8E43DAA51CAA0650646D1307D8650B8F837`）。`build/doom-assets/` 不进 Git，可自行准备：

```powershell
Invoke-WebRequest https://www.gamers.org/pub/idgames/idstuff/doom/doom-1.8.wad.gz -OutFile build/doom-assets/doom18.wad.gz
# 解压得到 build/doom-assets/DOOM1.WAD（1.8 shareware；吸引模式会提示 demo 版本是 1.8）
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

## 边界与已知限制

- **鼠标不支持**：doomgeneric 的 `i_input.c` 从不投递 `ev_mouse`，只有键盘这条路；要加鼠标得改 `i_input.c`（BIOS 侧 `MOUSE` 服务本身是好的）。
- **没有声音**：`FEATURE_SOUND` 关闭（`doomfeatures.h`），`-nosound/-nomusic/-nosfx` 默认传入。
- **WAD 是 1.8 shareware**：吸引模式会打印 `Demo is from a different game version! (read 108, should be 109) … This appears to be v1.8.` 并跳过 demo；自己开新游戏不受影响。要消掉就换 1.9 的 `DOOM1.WAD`。
- **存档**：`FS_WRITE` 已经可用，`M_SaveDefaults`/存档会写 SD 根部（`.savegame/` 目录由 `FS_MKDIR` 创建），但**尚未在板上实际验证过存档往返**；`remove`/`rename` 走 `FS_UNLINK`/`FS_RENAME`。
- **性能**：2026-10-11 实板（v0.9.1 gateware、60 MHz rv32ima 无 FPU、DDR 120 MHz、L1+4 KiB L2）测得 **2.48 帧/秒**——`DOOM presents=100 ms=174958` 到 `presents=700 ms=416866`，600 帧 / 241 908 ms，约为 DOOM 标称 35 fps 的 7%。启动侧不再是瓶颈：`R_Init` 的 1282 KiB 只花 `fs=1642 ms/1007 calls`（逐字节读的年代是 261 KiB / 268 127 次 / 72 255 ms）。测量办法是被动收 UART：`scripts/mini.py board status --seconds 30`；**100 帧一行 ≈ 40 s 才有一条**，30 s 窗口收不到东西是正常的，不代表死机。渲染本身要提速需要动软件渲染路径或提高 CPU/DDR 频率，不在本次范围。
- 只写 FPGA SRAM 和 UART；Flash 只在用户明确要求时由 `board update` 写。
- 程序内存硬约束：镜像 `memory_bytes` 必须 ≤ `0x06000000`（装载时一边读暂存区一边写程序内存），本载荷约 50.4 MiB，满足。

## 代码结构

| 文件 | 职责 |
| --- | --- |
| `firmware/apps/doom/` | doomgeneric 源码（Chocolate Doom 派生）+ 本地补丁：声音模块移除、`i_video.c` 默认 `rgb565`、`i_system.c` 去掉 GUI 弹窗、`d_loop.c` 去掉网络头依赖、`m_argv.c` 保持原有参数边界 |
| `firmware/apps/doom/dg_bios.c` | 平台层：`DG_Init/DG_DrawFrame/DG_SleepMs/DG_GetTicksMs/DG_GetKey/DG_SetWindowTitle`、HID usage→doomkeys 映射、F12 退出、UART 心跳 |
| `firmware/apps/doom/shim.c`、`shim_include/` | 最小 libc：stdio/stdlib/string/ctype/errno/math 等，控制台走 `WRITE`，文件走 `FS_*`，48 MiB 静态堆，`exit()` 用自写 `setjmp/longjmp` 回到 `payload_main` |
| `scripts/doom_image.py` | 构建 `BOOT.RPB`（转调 `bios_payload.py`，固定 `DOOMGENERIC_RESX/RESY=480/272`） |
| `scripts/doom_sim.py` | QEMU 仿真（BIOS shim + 面板抓帧 + 脚本化按键） |
| `scripts/doom_verify.py` | 实板验收（复用 `BootSession`/`PayloadServer`） |
| `tests/doom_test.py` | 主机回归：RPB1 布局 + QEMU 跑真实载荷 |
