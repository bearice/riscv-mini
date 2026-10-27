# AE350 BIOS、文件系统与 GW-BASIC

`mini.py build bios --machine mega138k_ae350` 构建原生 AE350 BIOS，作为独立 UART 应用装入 DDR。U4 中的启动固件负责 POST、DDR 初始化和 UART 装载；BIOS 提供终端、FatFs 文件命令和 ABI v1 服务。GW-BASIC 按 AE350 的 RV32IMA/ILP32、软件浮点工具链重新编译，并通过相同 ecall 服务使用控制台、文件和图形。

```sh
nix develop --command python scripts/mini.py build bios --machine mega138k_ae350
nix develop --command python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_fs_suite.c --define AE350_WITH_BIOS=1
```

构建仍使用 default workspace。BIOS 的交付类型是 `payload`，供本机器的 UART loader 使用；构建不会更新 Flash 或 current。

## 终端和服务

终端同时输出 UART 与 HDMI TTY，使用共享 VT/ANSI 解析和字体。编辑器支持方向键、Home/End、Insert/Delete、退格及 Ctrl-A/E/K/U/W。命令包括 `help`、`status`、`ls [DIR]`、`cat FILE`、`size FILE`、`mkdir DIR`、`rm FILE`、`mv FROM TO`、`unmount`、`basic`；GW-BASIC 的 `SYSTEM` 返回终端。

ABI 采用共享 [bios.h](../../../firmware/bios/include/bios.h)：M-mode ecall，`a7=BIOS_ECALL_MAGIC`，`a6=service`，结果位于 a0。入口保存调用者寄存器并推进 mepc；服务检查指针范围、长度、句柄、访问模式和枚举值。应用按 `bios_info.features` 查询能力，未支持的服务返回负值。这个接口按单个可信前台应用使用。

FatFs drive 0 使用 SD25 与 DMAC channel 4。六个文件句柄由 BIOS 持有；只读句柄拒绝写入，有打开句柄时拒绝卸载。创建、重命名和删除交给 FatFs，不提供格式化命令。错误关闭或 DMA 排空失败保留必要的所有权。

音频 ABI 的调用者 ring 由 CPU 管理，私有对齐 stage 通过硬 DMAC channel 0 送往音频 FIFO；`POLL` 服务检查完成并续传，尾部少量帧使用 PIO。`AUDIO_STOP` 排空后释放缓冲，fetched 记录已从调用者 ring 接收的帧。调用者应持续 poll；音视频/SD 实时并发另由专项压力测试验收。

## 内存与图形所有权

| 区域 | 用途 |
| --- | --- |
| DDR `0x3000` 起，装载镜像最多 512000 B | 代码与已初始化数据 |
| DDR `0x80000–0x7fffff` | BSS，包含 GW-BASIC 的 4 MiB heap |
| DLM `0xa020a000–0xa020dfff` | 应用栈 |
| DDR `0x08000000 / 0x08100000` | BIOS 私有 scanout 双缓冲 |
| DDR `0x08200000 / 0x08300000` | 应用拥有的 RGB565 画布 |

`BIOS_FEATURE_VIDEO_SHADOW` 表示应用画布由 CPU 持有。`VIDEO_PRESENT(slot)` 将所选画布复制到非活动 scanout 缓冲，清理缓存后在 vblank 提交；返回后应用继续修改自己的画布。TTY 与图形切换重建显示内容。尺寸为 640×480、stride 1280 B，图形能力和调色板能力分别查询。

## 验收入口

```sh
# 专用新目录中的 1/16/64 MiB 文件写读、ABI、音频调用与缓冲 guard。
nix develop --command python scripts/mini.py board p7-suite --build <gateware-result.json> --payload <fs-suite-result.json> --port <UART> --cpu-jtag-location <CPU-JTAG>
# 同一批测试文件上的终端命令、编辑、GW-BASIC TESTS 与 RGB565 调用。
nix develop --command python scripts/mini.py board p7-suite --bios --filesystem-result <fs-board-result.json> --build <gateware-result.json> --payload <bios-result.json> --port <UART> --cpu-jtag-location <CPU-JTAG>
```

使用已安装的兼容启动固件时，显式追加 `--bootrom-install <成功安装回执>`。操作先检查启动合同，再逐字节读取实物固件，之后才 UART 装载；不会因此重刷 U4。

文件测试使用 `FA_CREATE_NEW`，为本轮选择全新 `/P7Tnnn` 目录。写后卸载重挂，检查全部内容、文件大小、主机独立生成的 CRC、非对齐缓冲及 seek/句柄负例；测试文件保留供后续组合压力使用。通过范围和准确硬件/软件身份见 [P7 验收](../../../reports/ae350-p7.md)。

长窗口在 UART 完成后遇到调试链路故障时，可用 `board audio-suite --readback <原操作/result.json>`，同时提供原来的 `--build`、`--payload`、`--soak-seconds` 和 `--sd-stress`。此模式不复位或装载，先核对实物硬件 ABI、驻留代码和 ILM，再读取结果；原失败回执保留，恢复另记。
