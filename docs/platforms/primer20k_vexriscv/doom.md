# DOOM（BIOS 二级程序）

适用机器：`primer20k_vexriscv`。应用位于 `firmware/apps/doom/`，使用 BIOS 的 RPB1、文件、输入、视频和计时服务；静态链接 Newlib，运行库见 [app libc](payload-libc.md)。

## 构建与装载

```sh
python scripts/mini.py build doom
```

默认生成 RV32IM/ILP32、INDEX8/Newlib 载荷；`--rgb565` 选择 RGB565，`--hard-float` 仅用于启用了 FPU 并已验证的硬件。硬件的 CPU、L2 与功能配置从所选产物查询，不在本页固定 current、ROM 容量或缓存大小。

产物位置与身份从本次 run 的 `result.json` 读取。将 RPB 载荷放到 SD 或由 TFTP 导出，再用 BIOS 的 `boot sd <文件>` / `boot net <文件>` 装载。BIOS 本身的构建和临时运行见 [20K 操作](operations.md)；不将 RPB 当作 bootloader 的 `app.img`。

### BIOS 前提

需要 BIOS 文件服务、KEY 输入及所选视频能力。INDEX8 需要 features bit 5、调色板与对应服务，旧 BIOS 需选择 RGB565。硬浮点的硬件能力与 `mstatus.FS` 初始化要求见 [移植指南](bios-payload-porting.md)。

### 把 WAD 和载荷放到 SD 卡

使用 DOOM 1.9 shareware `DOOM1.WAD`，大小4,196,020字节，SHA256：
`1d7d43be501e67d927e415e0b8f3e29c3bf33075e859721816f652a526cac771`。

1.8 的录像数据与此引擎不兼容，会报 `Demo is from a different game version`。WAD 是外部输入，不纳入 Git。可通过 BIOS `fetchraw DOOM1.WAD` 保存原始文件；RPB 载荷使用 `fetch` 或 `boot net`。TFTP 服务端地址与解释器按实际网络配置选择，见 [测试条件](lab-environment.md)。

## 显示与输入

面板为480×272，320×200引擎画面居中，窗口为 `(80,36)-(400,236)`。INDEX8 使用 stride=480 的帧缓冲和双调色板；RGB565 使用 stride=960。提交帧与调色板在同一帧边界生效，退出时恢复 RGB565 文字控制台。帧槽与调色板所有权见 [视频规格](video-spec.md)。

| 键 | 作用 |
| --- | --- |
| 方向键 | 移动与转向 |
| Ctrl / Space | 开火 / 使用 |
| Enter / Esc | 菜单 |
| F12 | 退出载荷并返回 BIOS |

鼠标事件和声音未启用。存档调用 BIOS 文件服务，但存档往返须单独实板验证，不能从文件写 API 存在推断通过。

## 验证

```sh
python tests/primer20k_vexriscv/doom_test.py -v
python scripts/primer20k_vexriscv/doom_sim.py --soft-float --sample 25
python scripts/primer20k_vexriscv/doom_verify.py --host-ip <主机板卡接口IPv4>
```

QEMU 用 BIOS shim 运行目标代码，检查应用与渲染行为，不模拟真实 LCD/USB/SD、DDR 训练或 BIOS TTY。板测先确认 BIOS 提示符，再复用 `BootSession` / `PayloadServer` 装载和检查心跳。应用运行、输入、显示和性能分别验收。

性能使用 `timing` 或 timedemo 的稳定窗口，保存硬件、软件、WAD、runtime 与显示格式；不把启动耗时折算为稳态帧率。具体对照见 [默认性能](../../../reports/performance/doom-default-performance.md) 和 [L2 / INDEX8 对照](../../../reports/performance/doom-l2-and-indexed-display.md)。

## 调试通道

使用 `scripts/primer20k_vexriscv/doom_debug.py`，支持 `frame`、`source`、`timing`、`bios-status` 和离线 `--compare`。同一时刻仅由一个进程占用串口。比较须使用同次暂停状态的源像素、实际调色板与帧槽；保留多次取证时分别指定输出目录，避免覆盖后误比。

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

线格式为 `DBGDUMP <kind> <bytes> <w> <h> <bpp> <stride>`、连续 base64、`DBGEND <crc32>`，命令以 `DBG done` 结束；源像素与调色板是两块数据，都须收齐。数据体期间不能混入日志，base64 只在整条流结尾补齐。

实板与 QEMU 比图须匹配 leveltime、相机状态、WAD 和 runtime；两边一致不证明共享实现没有缺陷。旧 shim 的固定 CRC 不用于 Newlib 路径。双帧槽内容、DMA 欠载计数与实际显示分别检查；`present` 成功不等于扫描没有欠载。

## 代码结构

平台层为 `dg_bios.c`，调试通道为 `dg_debug.c` / `dg_debug.h`，`shim.c` 只保留 CRC、进度和调试钩子；标准库由 `firmware/libc/` 的 syscall runtime 与 Newlib 提供。数学宏和角度运算须保留整数符号语义，避免用只支持整数的旧 libc shim 替换标准库。
