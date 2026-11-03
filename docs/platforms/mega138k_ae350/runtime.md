# AE350 BIOS、文件系统与 GW-BASIC

`mini.py build bios --machine mega138k_ae350` 构建原生 AE350 BIOS，作为独立 UART 应用装入 DDR。U4 中的启动固件负责 POST、DDR 初始化和 UART 装载；BIOS 提供终端、FatFs 文件命令和 ABI v1 服务。GW-BASIC 按 AE350 的 RV32IMA/ILP32、软件浮点工具链重新编译，并通过相同 ecall 服务使用控制台、文件和图形。

```sh
nix develop --command python scripts/mini.py build bios --machine mega138k_ae350
nix develop --command python scripts/mini.py build payload --machine mega138k_ae350 --source firmware/diagnostics/mega138k_ae350/ae350_fs_suite.c --define AE350_WITH_BIOS=1
```

构建仍使用 default workspace。BIOS 的交付类型是 `payload`，供本机器的 UART loader 使用；构建不会更新 Flash 或 current。

## 终端和服务

终端同时输出 UART 与 HDMI TTY，使用共享 VT/ANSI 解析和字体。编辑器支持方向键、Home/End、Insert/Delete、退格及 Ctrl-A/E/K/U/W。命令包括 `help`、`status`、`ls [DIR]`、`cat FILE`、`size FILE`、`mkdir DIR`、`rm FILE`、`mv FROM TO`、`unmount`、`basic`；GW-BASIC 的 `SYSTEM` 返回终端。网络与启动命令：`set ip|server IP`、`tftp ADDR FILE`、`fetch FILE`（TFTP 到 SD，同名文件存在时失败，需先 `rm`）、`crc32 ADDR LEN`、`load FILE ADDR`（SD 文件装入合同区间，打印 `LOAD OK name bytes=N crc=X`）、`boot opensbi`、`boot sd`、`flash`、`flash crc OFFSET LEN`、`flash install ADDR LEN`；ADDR/LEN/OFFSET 为十六进制。

ABI 采用共享 [bios.h](../../../firmware/bios/include/bios.h)：M-mode ecall，`a7=BIOS_ECALL_MAGIC`，`a6=service`，结果位于 a0。入口保存调用者寄存器并推进 mepc；服务检查指针范围、长度、句柄、访问模式和枚举值。应用按 `bios_info.features` 查询能力，未支持的服务返回负值。这个接口按单个可信前台应用使用。

FatFs drive 0 使用 SD25 与 DMAC channel 4。六个文件句柄由 BIOS 持有；只读句柄拒绝写入，有打开句柄时拒绝卸载。创建、重命名和删除交给 FatFs，不提供格式化命令。错误关闭或 DMA 排空失败保留必要的所有权。

音频 ABI 的调用者 ring 由 CPU 管理，私有对齐 stage 通过硬 DMAC channel 0 送往音频 FIFO；`POLL` 服务检查完成并续传，尾部少量帧使用 PIO。`AUDIO_STOP` 排空后释放缓冲，fetched 记录已从调用者 ring 接收的帧。调用者应持续 poll；音视频/SD 实时并发另由专项压力测试验收。

## 持久启动

BIOS 镜像可写入 U7 用户 Flash 启动槽，由 ROM 在无 UART 输入时自动装载（见 [bootloader](bootloader.md#用户-flash-自动启动)）。

- `flash`：报告 `FLASH jedec=… bytes=… boot-slot=偏移+长度`，以及 `FLASH BOOT IMAGE abi= bytes= crc=`（载荷长度与 CRC）、`FLASH BOOT BLANK` 或 `FLASH BOOT OTHER`。
- `flash install ADDR LEN`：ADDR 处的镜像（通常先 `tftp` 到 `bios.staging`）先按 ROM 的规则校验（头、当前硬件 ABI、装载范围、载荷 CRC），不通过则 `FLASH INSTALL REJECTED <原因>`。随后擦除镜像覆盖的 4 KiB 扇区，从第二页起逐页编程，最后写含头的第一页（中途断电不会留下有效头），读回整段比较 CRC，成功打印 `FLASH INSTALL OK bytes=N crc=X`。只改动启动槽内镜像长度向上取整到扇区的范围。
- `boot sd`：从 SD 根目录读取 OpenSBI+U-Boot 的 `fw_payload.bin`，装入 os.sbi 起、覆盖 os.sbi+os.uboot 的范围（打印 `LOAD OK`），然后执行 `boot opensbi`；`Image` 与 `mega138k_ae350.dtb` 由 U-Boot 的 `sdboot` 从同一 SD 卷装载（见 [operations](operations.md#持久启动)）。失败打印 `BOOT SD FAIL` 并回到提示符。
- 自动启动：ROM 以用户 Flash 来源（a0=1）进入 BIOS 时，BIOS 打印 `AUTOBOOT SD in 3 s; press any key for the prompt`，3 秒内按任意键打印 `AUTOBOOT STOPPED` 并停在提示符，否则执行 `boot sd`。经 UART 上传的 BIOS 从不自动启动。

主机侧安装与验收入口见 [operations](operations.md#持久启动)。initramfs 编译进 Image（`firmware/linux/init.c`），因此 SD 上没有单独的 initrd。

## 内存与图形所有权

地址全部来自 [地址合同](address-map.md#7-ddr-软件布局与-os-交接)（`DDR_LAYOUT`），链接脚本的 `bss` 段由生成的 `ae350_map.ld` 提供。

| 区域 | 合同名 | 用途 |
| --- | --- | --- |
| DDR `0x3000` 起，装载镜像最多 512000 B | `bios.image` | 代码与已初始化数据 |
| DDR `0x08400000–0x08bfffff` | `bios.bss` | BSS，包含 libc 4 MiB heap 与 GW-BASIC 状态 |
| DLM `0xa020a000–0xa020dfff` | `dlm.payload-stack` | 应用栈 |
| DDR `0x08000000 / 0x08100000` | `bios.scanout0/1` | BIOS 私有 scanout 双缓冲 |
| DDR `0x08200000 / 0x08300000` | `bios.fb0/1` | 应用拥有的 RGB565 画布 |

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
