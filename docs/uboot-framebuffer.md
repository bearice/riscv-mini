# U-Boot RGB LCD framebuffer

U-Boot 的 `riscv_mini_lcd` 视频驱动使用当前 gateware 的可编程 RGB LCD DMA，
提供 480×272、RGB565、stride 960 的 framebuffer 和 8×16 字体控制台（60 列、17 行）。
`stdin` 仍为 UART，`stdout/stderr` 默认同时输出到 `serial,vidconsole`。

驱动 bind 阶段向 VIDEO uclass 申请 261120 字节、16 字节对齐的内存，
由 U-Boot 在重定位前预留，不使用 BIOS 的 framebuffer 或固定帧地址。
probe 先关闭并等待已有 DMA 排空，再将 `base0/base1` 都指向新 framebuffer，
清屏、选择 slot 0 并开启扫描。CPU L1 write-through、共享 L2 与 LCD DMA 一致；
视频同步使用 `fence rw,rw`，不调用本 CPU 不支持的 Zicbom 指令。

这是单缓冲控制台，绘制和扫描可同时进行，滚屏可能撕裂。
它不提供 Linux simple-framebuffer 交接：驱动在 OS prepare 阶段关闭并排空 DMA，
内核可以重新使用 U-Boot 的 framebuffer 内存。Linux 显示驱动属于后续工作。

## 设备树和构建

`CONFIG_OF_BOARD=y` 使用 OpenSBI 在 a1 传入的运行时 DTB。
`opensbi_build.py` 从选定 SoC 的 `csr.json` 生成 `riscv-mini,rgb-lcd` 节点；
`bootph-all` 保证重定位前绑定和内存预留。
`reg` 描述 CSR 空间，`riscv-mini,csr-offsets` 顺序为
enable、select、base0、base1、packed state、address_error，每个寄存器为一个 32-bit 字。
无 LCD 的 SoC 不生成节点；旧固定帧槽或未压缩 state ABI 会明确拒绝。

驱动源码、defconfig 和环境设置位于 `firmware/uboot/`，
上游 Kconfig/Makefile 接线包含在 `uboot-port.patch`，构建器自动复制驱动。
首次更新旧构建树的补丁时应使用新的 WSL 原生源码目录，保留之前的候选。

```powershell
.venv/Scripts/python.exe scripts/uboot_build.py --prepare-only `
  --source /home/bearice/uboot-fb-source --obj /home/bearice/uboot-fb-obj `
  --output-dir build/uboot/framebuffer
wsl -d archlinux -e bash /mnt/c/Users/bearice/Workspace/TangPrimer-20K/riscv-mini/build/uboot/framebuffer/build.sh
.venv/Scripts/python.exe scripts/opensbi_build.py --soc-dir <匹配的SoC目录> `
  --output-dir build/uboot/framebuffer/sbi --payload-path build/uboot/framebuffer/u-boot.bin `
  --osb-name UBOOTFB.OSB
wsl -d archlinux -e sh /mnt/c/Users/bearice/Workspace/TangPrimer-20K/riscv-mini/build/uboot/framebuffer/sbi/build.sh
.venv/Scripts/python.exe scripts/opensbi_build.py --soc-dir <匹配的SoC目录> `
  --output-dir build/uboot/framebuffer/sbi --pack-only --osb-name UBOOTFB.OSB
```

## 验证

`tests/uboot_video_test.py` 检查 CSR ABI、无视频配置和 UART 内存 dump 解析。
`uboot_verify.py --video --soc-dir <匹配的SoC目录>` 额外检查 LCD probe、
vidconsole 注册、stdout/stderr 镜像、连续文字滚屏、framebuffer 非零像素、
DMA frames/completed 递增、地址有效且无新增 underflow。
视频控制台回显/滚屏期间，验收脚本逐字发送命令，避免轮询 UART 的 RX FIFO 溢出。

板测使用匹配 gateware 的 SRAM 下载与 BIOS UART 装载，OSB 镜像通过 BIOS TFTP 进入 U-Boot。
显示计数和 DDR 像素检查不能代替面板文字、色彩和撕裂情况的目视验收。
