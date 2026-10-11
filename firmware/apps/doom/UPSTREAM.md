# 本目录的来源与本地改动

`firmware/apps/doom/` 是 **doomgeneric**（`PACKAGE_NAME "Doom Generic"`、`PACKAGE_VERSION 0.1`，启动横幅里的 `Doom Generic 0.1` 就来自 `config.h`）的扁平化源码副本，doomgeneric 本身是 **Chocolate Doom** 的最小可嵌入移植层。这里只保留编译需要的 `.c/.h`，**没有**上游 Git 元数据，也没有保留上游目录结构（`src/`、`textscreen/`、各平台驱动、构建脚本都被裁掉）。

- **许可证**：GPL-2.0-only（沿用上游各源文件头部的版权声明）。本目录**未附带许可证全文**；需要时取 <https://www.gnu.org/licenses/old-licenses/gpl-2.0.txt>。整个 `BOOT.RPB` 是这些源码的衍生作品，分发时要按 GPL-2.0 提供对应源码。
- **不含游戏数据**：`DOOM1.WAD` 不在仓库里，也不进镜像；准备与上卡方式见 [docs/doom.md](../../../docs/doom.md)。
- **上游精确版本未记录**：移植时没有保存上游 commit。要精确对齐请重新取上游后逐文件 diff，不要假设本目录等于某个 tag。

## 本地改动（相对上游）

| 文件 | 改动 | 原因 |
| --- | --- | --- |
| `dg_bios.c` | 新增：本平台层（`DG_Init/DG_DrawFrame/DG_SleepMs/DG_GetTicksMs/DG_GetKey`、HID usage→doomkeys、F12 退出、每 100 帧 UART 心跳） | BIOS 只提供 ecall 服务，没有 SDL/POSIX 显示与输入 |
| `shim.c`、`shim_private.h`、`shim_include/` | 新增：最小 libc（stdio/stdlib/string/ctype/errno/math 等），控制台走 `WRITE`，文件走 `FS_*`，48 MiB 静态堆，`exit()` 用自写 `setjmp/longjmp` 回到 `payload_main` | 裸机 `-nostdlib`，没有 libc |
| `doomfeatures.h` | `FEATURE_SOUND` 关闭；`i_sound.c` 里对 doomgeneric 的引用同步处理 | 板上没有声音后端（`-nosound/-nomusic/-nosfx` 也默认传入） |
| `i_video.c` | 默认色深改为 `rgb565` | 面板就是 480×272 RGB565，避免每次启动靠参数纠正 |
| `i_system.c` | 去掉 GUI 错误弹窗路径 | 裸机没有终端弹窗，错误只能走 UART |
| `d_loop.c` | `extern boolean net_client_connected; extern boolean drone;` 取代网络头文件 | 网络模块被裁掉，符号仍被引用 |
| `m_argv.c` | `M_CheckParmWithArgs` 的边界保持 `i < myargc - num_args`（原样，加注释说明） | 改成 `<=` 会读 `myargv[myargc]`，在板上是 load access fault |

平台侧的零拷贝约定（`I_FinishUpdate` 每行正好推进 960 B = 面板 stride，于是 `DG_ScreenBuffer` 直接指向 BIOS 帧槽）写在 `dg_bios.c` 的注释里，改动视频路径前先读它。
