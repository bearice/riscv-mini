# Linux 最小系统移植

2026-10-10 已在 Tang Primer 20K 实板启动 Linux 6.12.112，进入 RV32 用户态 PID 1，并完成内存、进程、定时器和 RGB LCD framebuffer 自检。首次启动使用 v0.9.1 gateware；后续 USB 接收采样修正需要新的 gateware，见下文 USB 验证记录。启动路线为 XIP loader → DDR BIOS → OpenSBI FW_PAYLOAD 内的 U-Boot → Linux Image → initramfs 的 `/init`。

## 已验证的行为

CPU 为 60 MHz RV32IMA、单 hart、Sv32，不启用 C/F/D/V。初次串口启动使用 SBI DBCN；当前全外设候选已接入本机 supervisor IRQ 控制器及原生 32 位 CSR LiteUART，运行控制台为 `liteuart0`，设备名为 `ttyLXU0`。SBI TIME 继续提供内核定时器。LCD 同时提供 framebuffer console `tty0`；用户态绘图先用 KDSETMODE 切到 graphics，防止异步内核日志覆盖图形缓冲。

最终含 framebuffer 的实板记录：`build/operations/20261010T020448816574Z-linux/result.json` 和同目录 `linux-uart.log`。首次纯串口用户态验收另外保存在 `build/operations/20261010T015336860606Z-linux/`。记录关联了所选 bitstream、BIOS、OpenSBI/U-Boot 和 Linux 文件身份；U-Boot 对收到的 Image 检查长度和 CRC32，然后才执行 `booti`。gateware 身份通过选定文件和 SRAM 下载关联，不是配置区独立读回。

```text
Linux version 6.12.112
riscv: base ISA extensions aim
riscv-mini Linux init pid=1
Linux 6.12.112 riscv32
SELFTEST mmap/mprotect/munmap PASS
SELFTEST fork/exec/wait PASS
SELFTEST timer/nanosleep PASS
LINUX USERSPACE PASS
SELFTEST fb0 RGB565/mmap/readback/DMA/blank PASS
linux-mini>
```

进入控制台后再次执行 `test`，三组自检均通过；`uname`、`cpu`、`mem`、`uptime` 均返回结果。`/proc/cpuinfo` 报告 `rv32ima_zicsr_zifencei`、`sv32`；`/proc/meminfo` 报告 MemTotal 122188 kB，MemAvailable 119144 kB。初次内核启动时 Linux 排除 RAM 的前 4 MiB，并保留 OpenSBI 的 1 MiB 区域。

本轮新增镜像/启动回归 6 项通过，既有板端操作回归 32 项通过。之前板端入口把 Path 传给需要文件流的状态机，且被动读取可能消耗两秒 autoboot 窗口。已发起 SRAM/reset 的路径现在立即同步新启动横幅与菜单；处理未知现场状态仍保留状态机恢复。
设备树视频 ABI 检查 4 项和 Ethernet ABI 检查 3 项也通过。

当前范围：主 RGB LCD 保留 `/dev/fb0` 和 LCD shell；SPI 小屏不提供 framebuffer 驱动，已移除 SPI panel fb1 及对应设备自检，保留 SPI 控制器和 GPIO。下文 SPI fb1 的验收记录属于此前候选的历史结果。

## LCD framebuffer

`riscv-mini-fb` 是本机 LCD 控制驱动，负责分配 Linux 所有的连续 DMA 缓冲、停止旧 DMA、设置双基地址为同一缓冲、启用扫描，并注册 `/dev/fb0`。固定模式为 480×272、RGB565、960 B stride、261120 B。支持 fbdev read/write、`mmap`、模式查询和 blank/unblank；不支持变更分辨率。含回卷 CSR 的新 DMA 支持按扫描行的 YWRAP；旧 DMA 保持固定窗口。

驱动从 DT 的 `riscv-mini,linux-csr-offsets` 获取 enable/select/base0/base1/state/address_error/frames/completed/underflows，原 U-Boot 的六寄存器属性保持原顺序。原九寄存器属性保持兼容。可选 `riscv-mini,wrap-csr-offsets` 描述 wrap_start/wrap_end 两个新增 CSR，存在时扩展 aperture 并启用环形滚屏。DT `dma-coherent` 对应本机真实共享内存路径：CPU L1 写透，LCD 与 CPU 共同访问共享 writeback L2；不能将该声明直接用于其他非一致 gateware。

`/sys/class/graphics/fb0/device/dma_status` 提供只读硬件状态快照。blank 写 enable=0 后等待 CDC 和 busy 排空；probe 失败时若 DMA 无法排空则隔离缓冲，避免释放仍被硬件引用的内存。驱动仅 built-in，关闭手工 bind/unbind，shutdown 时停止扫描。

用户态 `fb` 检查模式、普通文件读写、共享映射、130560 个 RGB565 像素的逐字读回、DMA 完成数前进、无新增 underflow/地址错误、blank 后 busy=0 和 unblank 恢复，并拒绝错误分辨率设置。它绘制左红、中绿、右蓝三色条。`fbstat` 查询扫描计数；内核启动阶段可在 LCD 上输出 framebuffer console。关闭 console 光标自动闪烁，避免图形读回期间覆盖像素。

已通过 Linux framebuffer 操作和 DMA 实板验收；没有摄像头/人工视觉验收记录，因此不把 DMA 通过视为物理面板颜色、方向、完整画质都已确认。

最终镜像 `build/linux/framebuffer-final/minimal/Image` 为 2692132 B（约 2.57 MiB），内置静态 init 为 7792 B；Image SHA256 为 `d14c97996157f955b351cffb767eea34e69a914afb0e7d48dfded10be8bc04ac`。该轮重复 `fb` 时，completed 为 1062→1091，blank/unblank 后为 1113；underflows 和 address_error 全程为 0。fbdev framebuffer 物理地址为 `0x008c0000`。Linux 软件绘制所需的 `FBINFO_VIRTFB` 已设置，最终日志没有早期版本的 virtual-address-space 警告。

最终镜像登记命令（与下文首次纯串口记录分开）：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/mini.py build linux `
  --output-dir build/linux/framebuffer-final/minimal `
  --board-evidence build/operations/20261010T020448816574Z-linux/result.json
```

## 构建与重复运行

Windows 仓库根目录：

```powershell
./mini.ps1 build linux --output-dir build/linux/minimal
./mini.ps1 build uboot --hardware current --feature video=off --output-dir build/linux/uboot
./mini.ps1 build opensbi --hardware current --payload-path build/linux/uboot/u-boot.bin --osb-name UBOOT.OSB --output-dir build/linux/sbi
./mini.ps1 board linux --build current `
  --os-image build/linux/sbi/UBOOT.OSB `
  --kernel build/linux/minimal/Image `
  --tftp-python C:/Users/bearice/AppData/Local/Programs/Python/Python313/python.exe
```

先用 `--dry-run` 可以检查镜像和所选构建，不接触板子。统一 target 调度检查每个阶段的退出码，编译失败不能继续打包或把旧镜像登记为新结果。只重编内核时只执行 `build linux`；更换 SoC/CSR 布局后另外执行 `build uboot` 和 `build opensbi`，两者以 `--hardware` 选择同一输入，`board linux --build` 也必须选择该硬件构建。

内核源树和 object tree 在 WSL archlinux 原生 ext4 的 `~/riscv-mini-linux/`；Windows 的 `build/linux/` 接收最终文件、配置、构建日志和 validation。工具为 clang/lld 23.1.1；需要 make、flex、bison、bc、dtc、curl、tar 和 U-Boot 所需的 riscv64-elf GCC。内核源码固定在 6.12.112，下载 SHA256 与 kernel.org 公布值核对：

```text
linux-6.12.112.tar.xz
164dc9d1f6c93c61a15e1f071c48379b467f2b17c469cce7223471968208ed03
```

首次纯串口验收镜像 Image 为 2525372 B（约 2.41 MiB），包括 5076 B 的静态 `/init`；Image SHA256 为 `486669a99fd7fa0ccb12a3391019474dfffba3464d11063cd8e23d11c72a3725`。同一源码重编可能因内核构建时间/序号变化而产生不同 hash，必须以新镜像的实测收据验收。该首次镜像的登记命令：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/mini.py build linux `
  --board-evidence build/operations/20261010T015336860606Z-linux/result.json
```

登记会拒绝失败收据或 Image hash 不匹配的收据。新的板端运行会生成新的 operation 目录，登记时使用该次实际路径。

TFTP 服务器只导出本轮明确选定的 OSB 和 Image，并由任务入口启动/结束。`--tftp-python` 选择防火墙已经放行的解释器；详见 [现场环境](lab-environment.md)。入口不修改防火墙或网卡配置。两次传输顺序进行，不导出整个目录，不写 SD 卡。实板操作下载 gateware 到 SRAM、BIOS 到 DDR，不写 Flash；成功后保持 Linux 控制台运行。

## 地址与 ABI

| 内容 | 物理地址 / 行为 |
| --- | --- |
| RAM | `0x00000000..0x07ffffff`，128 MiB |
| OpenSBI | `0x01000000`，DT reserved-memory 保留 1 MiB |
| 固件 DTB | `0x01080000`，由 U-Boot 处理并交给内核 |
| U-Boot 初始入口 | `0x01100000`，随后重定位到高端 RAM |
| TFTP Image 装载 | `0x02000000` |
| Linux 执行 | U-Boot 根据 RV32 Image header 移到 `0x00400000`，4 MiB 对齐 |

Linux 自 6.12 默认读取 `riscv,isa-base` 与 `riscv,isa-extensions`；设备树现在同时提供这些属性和原来的 `riscv,isa`，避免扩展探测为空。内置 `/init` 使用 ILP32、无浮点/压缩指令，直接发出 Linux ECALL。RV32 的当前调用表使用 `waitid`、`clock_gettime64`、`clock_nanosleep_time64`，不能沿用 RV64/旧表的 `wait4` 和 time32 编号。

U-Boot 默认 SD 启动配置修正为 `CONFIG_USE_BOOTCOMMAND=y` 和 `CONFIG_BOOTCOMMAND=...`；此前的 `CONFIG_BOOT` 被 Kconfig 忽略。Linux bring-up 的 bootstrap 使用 `mini.py build uboot --feature video=off`，并在加载 Image 前切换 stdout/stderr 到串口。初次启用视频的 bootstrap 没有到达预期提示；本轮同时修正 bootcommand 并关闭视频，尚未独立定位该现象，也未验收 Linux 视频交接。

## BusyBox 外部 initrd

`build/linux/busybox/initrd.cpio.gz` 是 gzip 压缩的 newc initramfs，使用 BusyBox 1.37.0 和 musl 1.2.5 静态链接。BusyBox 编译为 RV32IM，musl 的原子操作使用 RV32IMA；ABI 为 ILP32，无 C/FPU，无动态解释器。源码压缩包的 SHA256 固定在 `scripts/mini.py build busybox`，归档内 `/bin/busybox` 必须与独立 ELF 完全相同，并检查 ELF32/RISC-V/flags=0，防止安装阶段误用主机编译器。

先构建上述 Linux 内核以取得 UAPI headers 和 `gen_init_cpio`，然后运行：

```powershell
./mini.ps1 build busybox --output-dir build/linux/busybox
./mini.ps1 build uboot --hardware current --feature video=off --output-dir build/linux/busybox/uboot
./mini.ps1 build opensbi --hardware current --payload-path build/linux/busybox/uboot/u-boot.bin --osb-name UBOOT.OSB --output-dir build/linux/busybox/sbi
.venv/Scripts/python.exe -X utf8 scripts/mini.py board linux `
  --build 45398e9-full-rv32ima-rom0k-l24k-usb-enumeration-safe-ab5ad2e5-1c55f655 `
  --os-image build/linux/busybox/sbi/UBOOT.OSB `
  --kernel build/linux/usb-fixed/minimal/Image `
  --initrd build/linux/busybox/initrd.cpio.gz `
  --tftp-python C:/Users/bearice/AppData/Local/Programs/Python/Python313/python.exe
```

BusyBox/musl 在 WSL archlinux 的 `~/riscv-mini-linux/busybox-build` 编译，依赖 riscv64-elf GCC、make、tar、cpio 和前述内核构建目录。GCC 使用 `rv32im/ilp32` 的 libgcc/crt，通过 musl specs 替换裸机 newlib/libgloss 默认链接；链接器显式选择 `elf32lriscv`。`build uboot --feature video=off` 和 `build opensbi --payload-path <u-boot.bin>` 重建纯串口 bootstrap，U-Boot 必须有 `CONFIG_SUPPORT_RAW_INITRD=y` 才能接受 raw cpio.gz。

仅修改初始化脚本或 `/etc` 时可用 `./mini.ps1 build busybox --rootfs-only --output-dir build/linux/busybox` 复用已编译 ELF 并重新打包；它要求 BusyBox 配置未变且 WSL ELF 与输出文件一致。配置变化需要完整构建。当前内核开启 UNIX98 PTY，并挂载 `/dev/pts`；验收实际分配 `/dev/pts/0`，在子进程中验证控制终端。

`/init` 挂载 devtmpfs、proc、sysfs、tmpfs，设置主机名后 exec BusyBox init 成为 PID 1；inittab 同时在原生 `ttyLXU0` 和 LCD 的 `tty1` 上 respawn 登录 ash，提示符 `mini-linux#`。启动时清除 LCD 内核日志，显示 `BusyBox LCD console tty1`；内核日志级别调低，光标启用。验收检查活动 VT、终端 shell 和 `/dev/vcs1` 中的提示符。BusyBox `ps` 的 TT 栏显示 `4,1`，不能把这个设备号当成缺少 tty1。内置的 freestanding `/init` 被外部归档覆盖，未提供外部归档时仍运行原来的自检控制台。根文件系统驻留 RAM，`/tmp` 和 `/run` 为 tmpfs；SD 卡不自动挂载。

initrd 先装载在 `0x04000000`，入口会检查与内核/高端保留 RAM 的重叠，并核对 TFTP 长度、CRC32、SHA256。U-Boot 命令形如 `booti 2000000 4000000:<十六进制文件长度> 1080000`。`--initrd` 与 `--devices` 分开运行：前者记录 `linux_busybox` 的 PID 1、管道/子进程、临时文件 checksum 和 loopback 验收；后者运行内置 native driver 自检。BusyBox 通过不意味着 USB 外设枚举已通过。

每次构建保存静态 ELF、`.config`、源校验值、归档和 `validation.json`。板测后用匹配的收据登记：

```powershell
.venv/Scripts/python.exe -X utf8 scripts/mini.py build busybox `
  --board-evidence build/operations/<本次linux操作>/result.json
```

2026-10-10 首轮 BusyBox 串口实板收据为 `build/operations/20261010T070336349666Z-linux/result.json`，`linux_busybox` 与 operation 均通过。initrd 为 **813545 B**，SHA256 `a11dd106323d516dd9f3865799f476357a8f3063e906ae2d8ce6f48c9b843994`；静态 BusyBox **1415640 B**，归档解压后 **1475584 B**、417 个条目。匹配内核是 `build/linux/devices/minimal/Image`，SHA256 `dba8573b5c5a96d156567bf3ea59c450d98d1e2abfe4ba5abe082c29992d50a9`，bootstrap 是 `build/linux/busybox/sbi/UBOOT.OSB`。最终启动没有挂载错误；PID 1 的 comm 为 `init`，管道、子进程 wait、tmpfs 64 KiB 校验、loopback 两包零丢包通过；设备节点列出 fb0/fb1、mmcblk0/p1、ALSA playback/capture。shell 的 `free` 显示可用内存约 108 MiB。两轮 BusyBox 实板通过，主机 52 项检查通过。没有写入 Flash 或 SD，也未提交/更新 current。

源码入口：[BusyBox](https://busybox.net/)、[musl 1.2.5](https://musl.libc.org/releases/musl-1.2.5.tar.gz)。

## 驱动资格与限制

内置最小系统的 `linux-mini>` 是 freestanding init 的命令循环，支持 `help/uname/cpu/mem/uptime/test/fb/fbstat/devices/network HOST/reboot`。另有下述外部 BusyBox initramfs，提供真正的 ash shell。当前已实现原生 IRQ/UART、SD/MMC、Ethernet ring DMA、USB full-speed PIO host、ALSA 音频/麦克风、GPIO/input/LED/WS2812、SPI 小屏及只读 Flash MTD。各项实板资格需看对应 operation 的实际结果，不能把驱动编译或固件 TFTP 当成 Linux 设备验收。

全外设候选在 `build/linux/devices/`。`board linux --devices` 在基础用户态与 fb0 回归后运行 Linux IPv4/UDP echo 和设备自检；主机 echo 进程只监听指定地址的系统分配 UDP 端口，使用已放行的 Python。init 的 `network HOST [PORT]` 支持该端口；省略 PORT 默认 46666。`20261010T025950419659Z-linux` 已通过原生 UART/IRQ、SD 反复读和只读 FAT 挂载、Flash MTD 反复读、GPIO/LED、ALSA 播放和麦克风捕获、SPI fb1 deferred transfer，以及 32×1000 B Ethernet UDP 往返；该次整体结果失败于 USB 枚举，因此不能登记为完整通过。

USB 现场基线检查 `20261010T032518095194Z-verify` 使用同一套已验收 gateware/BIOS，也在 `test usb` 失败：PHY ready=1、ID=00060424、端口连线=1，但 connected/VID/PID/HID 均为 0。已请求重新拔插外设后复测；不能把该现场失败归因于 Linux，也不能据此认定 Linux USB 驱动已通过。候选尝试分别保留在 operation 日志中。

修复 USB 前的候选实板记录为 `build/operations/20261010T032704618618Z-linux/result.json` 与同目录 `linux-uart.log`。Image 为 5591228 B，SHA256 `dba8573b5c5a96d156567bf3ea59c450d98d1e2abfe4ba5abe082c29992d50a9`。该轮用户态/MMU/进程/定时器与 fb0 均重复通过；Ethernet 32×1000 B 往返通过，RX/TX errors/drops 为 0；SD/FAT 只读和 Flash MTD 通过；GPIO/LED 与 UART/IRQ 通过；ALSA played=3495、underruns=0、errors=0，capture=1845、mic_overruns=0；SPI fb1 mmap/deferred transfer 通过。整体仍失败于 `/sys/bus/usb/devices/1-1/idVendor` 不存在，validation 的 board_verified 与 board_devices_verified 均为 false。SPI 以 16 位硬件 beat 合并相邻字节，CSR 原子轮询不调用昂贵的 emulated-rdtime udelay；该轮约 25 秒进入 init，约 30 秒 uptime 时已完成基础回归。该候选没有晋升为正式合格版本。

Flash MTD 只读，不提供擦除/编程。SD 驱动实现读写 request，但本轮仅做读及只读 FAT 挂载。USB host 当前仅支持 full-speed control/bulk/interrupt，低速和 isochronous 请求明确拒绝；HID、USB storage/SCSI 已编入内核，实际外设绑定另行验收。麦克风硬件是 512 帧有限 snapshot，由驱动排空并重启采集，ALSA 捕获接口不等于连续、无缝的录音 DMA。音频播放测试使用静音并检查 DMA 前进、零 underrun/硬件错误，不构成听音验收；GPIO 输入注册不构成人工按钮操作验收。两个屏幕的传输结果不构成人工颜色/方向/画质验收。

`mprotect` 自检覆盖改为只读后读取及解除映射，尚未主动写只读页或验收所有权限/页故障组合。未进行长时间 Linux 压力测试、SD rootfs 挂载、断电冷启动或持久安装。内核配置未启用 strict kernel RWX；CPU 也没有 PMP，因此不是安全隔离验收。

构建脚本、配置和操作入口随移植代码提交；普通代码提交不发布新版本。恢复现役 BIOS 可运行 `./mini.ps1 board run --build current`，仅进行 SRAM/UART 临时装载。

上游依据：[RISC-V boot requirements](https://www.kernel.org/doc/html/latest/arch/riscv/boot.html)、[kernel.org 6.12.112 源码](https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.112.tar.xz)、[源码校验值](https://cdn.kernel.org/pub/linux/kernel/v6.x/sha256sums.asc)。

## USB 键盘修复与后续实板记录

USB 接收 trace `20261010T073321384536Z-linux` 捕获 hub 描述符从 `12 01 00 02 09 00 00 08` 错移成 `24 02 00 04 12 00 00 10`，PID 从 `4b` 变为 `97`，伴随 CRC/-71。同一旧 gateware 的 BIOS 也无法枚举。串行 PHY 改为接收边沿重同步、位单元中间采样，TX 保持固定节拍。48/60 MHz、84 个 1 ns 相位、0/8 ns 边沿抖动仿真在旧 RTL 失败，在修复后通过。

另一轮在 TinyUSB EP0 打开失败时触发 `ebreak`（`20261010T075027906011Z-linux`，PC `0080c308`），发生在 Linux 之前。该预期外设失败现在释放枚举锁并恢复 hub 轮询。真实 TinyUSB/Ultra HCD 主机回归覆盖低速拒绝及后续 full-speed EP0 可打开；测试中的低速设备不代表现场第二端口的设备类型。

修复候选 `45398e9-full-rv32ima-rom0k-l24k-usb-enumeration-safe-ab5ad2e5-1c55f655` 的 bitstream SHA256 为 `ab5ad2e5f02c7c6d973c071a235db87192f91aa1d3b864100583ba84107d1ab7`，PnR setup/hold 0/0，ABI `71da004d`。中间 `c6fece94` gateware 的 BIOS 全套检查 `20261010T074642286968Z-verify` 通过 hub `05e3:0606`、Logitech `046d:c52b` 三个 HID 与三次 USB stop/restart；新 BIOS 配合该中间 gateware 的检查 `20261010T075949877807Z-verify` 也通过。这些收据不冒充最终 bitstream 的同镜像验收。

最终候选 Linux 全外设收据 `20261010T080621373234Z-linux` 通过 IRQ/UART、SD/FAT 只读、Flash MTD、Ethernet 32×1000 B 往返、GPIO/LED、ALSA 播放/捕获、SPI fb1 和 USB 键盘注册。匹配 Image 为 `build/linux/usb-fixed/minimal/Image`，5599572 B，SHA256 `b38ad0c999dd448b604fe6a6dba3b58c680a6289ea0fc44e041c6ad0a5922823`。前一轮 `20261010T080301897959Z-linux` 失败于 ALSA capture read，同配置重试通过仍不证明偶发问题已消失。

当前 BusyBox initrd 为 813603 B，SHA256 `64940ac437accf38bdf1131f9a5d17635de740b857fed4516d5c3c2bd6318d7f`；UNIX98 PTY、devpts 和 LCD tty1 ash 已启用。`20261010T080903283255Z-linux` 的 LCD 文本包含交互的 `ps` 输出及正在输入的 `cat /proc/cpuinfo`；启动横幅已滚出屏幕，使旧检查误报。检查改为活动 tty1、控制终端 shell 与可见提示符，不要求横幅永久留在屏幕。记录中没有人工颜色/画质确认。

同轮注册 Logitech 键盘、鼠标和控制输入；第二端口实际是 full-speed USB storage，注册 `/dev/sda`、`sda1`（4.05 GB）。这里只确认绑定及分区发现，未写入或验收 USB 存储吞吐。启动期间仍有 root port enable 重试日志，随后成功枚举；不宣称已通过长期热插拔稳定性测试。

提交前修正后的 BusyBox 实板验收 `20261010T132255233230Z-linux` 全部通过（LCD tty1、PTY、USB 键盘注册、PID 1、管道/进程、tmpfs checksum、loopback）。对应 initrd 的 validation 已登记；此前横幅滚屏误报和 loader autoboot 失败日志保留。

提交前完整候选 `45398e9-full-rv32ima-rom0k-l24k-linux-usb-commit-31595fad-ee74c4ff` 从最终源码输入重新构建，PnR setup/hold 0/0，ABI 保持 `71da004d`；此候选与前述 BusyBox 的 ab5ad2e5 构建分别记录身份。

最终源码输入的候选 BIOS 验收 `20261010T132748459709Z-verify` 通过全部适用功能、双麦克风和 60 秒 soak，USB 三次重启与共享 PHY 检查通过；LCD underflows、音频 underruns/errors、IRQ drops/unhandled 均为 0。资格关联选定 FS/app hash，不是配置区独立读回。

## 根端口状态修正与 SPI 屏幕范围

Linux HCD 的 STATUS 是即时 PHY 线状态，不能直接把非 J 解释为掉线。根端口轮询现保留已连接设备的 K 状态，SE0/SE1 缺失样本需跨越至少 20 ms 才报告断开，并跳过自身 reset 与传输期间的采样；GetPortStatus 使用同一缓存状态。针对实际 root-hub callback 的回归覆盖 packet K、短 EOP、reset、transfer、拔出、重连及 tick 回绕，旧实现失败、新实现通过。此处的 20 ms 是采样间隔的消抖门限，并非硬件连续线状态监测；长期热插拔稳定性仍需单独验收。

SPI panel framebuffer 驱动和 fb1 自检已删除。内核构建保留 RGB LCD fb0、SPI 控制器与 GPIO，并检查 SPI panel 配置未启用、panel_probe 未链接。历史 SPI fb1 结果不代表当前提供该接口。

## LCD DMA 环形滚屏

2026-10-11 增加 `rgb_lcd_wrap_start`（偏移 0x24）和 `rgb_lcd_wrap_end`（偏移 0x28，不含末尾地址）。wrap_end=0 禁用回卷，保持旧连续扫描行为；启用时起止地址都需 16 字节对齐，范围位于 DDR，起始扫描地址落在环内。每帧锁存 selected base、wrap_start 和 wrap_end；每个 16 字节读请求到达末尾后从 wrap_start 继续，整帧仍输出 480×272 个 RGB565 像素。端口停顿期间请求地址保持，帧内 CSR 修改不改变已锁存的地址序列。回卷范围最小为一个 16 字节 burst；Linux 使用完整 261120 B 帧作为环形缓冲。

Linux fb0 保留 480×272、960 B stride 和单帧分配，DT 提供两个可选回卷偏移时公布 ywrapstep=1，并启用 fbcon 环形滚屏策略；滚动改变 scanout base，无需搬动整屏像素。没有该属性时仍使用旧 redraw 路径。非回卷模式只接受 yoffset=0；YWRAP 接受 0≤yoffset<272，横向偏移不支持。用户态图形自检切换为 graphics 后先清除 YWRAP 并复位显示起点。

前一高虚拟画布 pan 候选存在用户报告的显示错误，不能视为验收通过。地址级和完整像素 RTL 回归覆盖末尾回卷、跨边界帧、CSR 帧内更新、反压和范围拒绝；实板结果另以对应新 bitstream/kernel/bootstrap 收据记录。PTY/滚屏性能脚本为 `tests/linux_console_perf.py`，显式上板运行，比较 null、LCD 和 PTY-null，并核对终端文本连续；它会暂停 LCD 上的 top 并在测试后恢复。

回卷是默认关闭且保留全部旧寄存器语义的兼容扩展。启动镜像 ABI 仅对位于 LCD 基址 +0x24/+0x28、单字 RW 的这对精确追加寄存器保留旧签名；错位、缺失配对或其他 CSR 变化仍进入 ABI 校验。完整 CSR 表及 bitstream 仍保存各自身份。这样旧 Flash XIP 引导器可以接收匹配新硬件的 RAM 固件，无须为实验改写 Flash。

2026-10-11 候选 `a4d09c0-full-rv32ima-rom0k-l24k-lcd-ring-wrap-compatible-463f6c57-dd14c592` PnR setup/hold 为 0/0，完整固件回归（含 60 秒 soak、mic）通过。Linux native fb0 的 mmap/readback/DMA/blank 自检和 BusyBox/PTY/USB 键盘枚举通过，分别见 operation `20261011T032009556797Z-linux`、`20261011T032213138633Z-linux`。120 行滚屏后 yoffset=88，基地址=0x00d94a00，underflows/address_error=0；读取完整帧并按回卷起点重建后可见连续 88–120 行。帧缓冲回读不等同于物理屏幕信号观察。同负载 LCD 时间从旧内核 21.53 秒降到两轮 8.96/8.79 秒；5 秒性能阈值仍未通过，PTY-null 从 7.89 到 8.48/7.10 秒，不能宣称 PTY 本身已优化。详细结果保存在 `build/linux/scroll-perf/`。
