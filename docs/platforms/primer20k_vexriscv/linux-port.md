# 20K Linux 运行环境

适用机器：`primer20k_vexriscv`。启动路线为 ROM/XIP loader → DDR BIOS → OpenSBI FW_PAYLOAD 内的 U-Boot → Linux Image → initramfs。CPU 需具备匹配的 RV32IMA/Sv32 与 S-mode 中断/定时器；不将历史 gateware 的通过结论用于其他 CPU 组合。

内核移植位于 `firmware/linux/`，构建后端固定 Linux 6.12.112 的源码与校验值。原生 LiteUART 控制台为 `ttyLXU0`，SBI DBCN 用于早期控制台，SBI TIME 提供内核定时器。显示使用 RGB LCD framebuffer 和 fbcon。

## 构建与运行

```sh
python scripts/mini.py build linux
python scripts/mini.py build uboot --hardware <20K硬件身份>
python scripts/mini.py build opensbi --hardware <20K硬件身份> --payload-path <u-boot.bin>
python scripts/mini.py build busybox
python scripts/mini.py board linux --build <20K系统身份> --os-image <UBOOT.OSB> --kernel <Image> --tftp-python <可达的TFTP解释器>
```

所有目标由统一入口调度环境、runner 和打包，不手工执行历史阶段脚本。具体参数先查目标 `--help`；构建输出从 run 结果读取，重复构建保留原 workspace。源码和工具由后端选择 native/WSL runner，Windows 挂载盘不作为大型上游编译的推荐工作树。

`board linux` 使用 SRAM/UART 和 TFTP，不安装 Flash。主机接口、server 与 TFTP 解释器按实际连接选择；准确的 Image、OSB 和可选 initrd 需校验长度与哈希。验收覆盖 PID 1、内存、进程、定时器及请求的附加功能，不因上传成功认定用户态通过。

## 地址与显示

内核 Image 装入 `0x02000000`，外部 initrd 装入 `0x04000000`；设备树、暂存区与高端保留 RAM 的范围由加载流程核对。软件使用选定硬件的导出，不从当前源 JSON 重新推导旧硬件地址。

LCD framebuffer 为480×272 RGB565；图形程序用 KDSETMODE 与 fbcon 交接，避免内核日志覆盖画面。RGB LCD 驱动提供环形滚屏、pan/YWRAP、窗口及背光接口；SPI 小屏只提供 SPI 控制器，不注册第二个 framebuffer。用户态必须查询实际设备能力。

## BusyBox 外部 initrd

BusyBox 1.37.0 与 musl 1.2.5 静态链接，归档为 gzip/newc。BusyBox 使用 RV32IM、musl 原子操作需要 A，ABI 为 ILP32；构建核对 ELF32/RISC-V 和归档内可执行文件，避免误装主机产物。先构建 Linux 取得 UAPI headers 和 `gen_init_cpio`。

向 `board linux` 传入 `--initrd <initrd.cpio.gz>` 启动 BusyBox init/ash；U-Boot 必须支持 raw initrd。`/init` 挂载 devtmpfs、proc、sysfs、tmpfs，`inittab` 为串口和 LCD `tty1` 提供 shell，PTY 使用 `/dev/pts`。根文件系统驻留 RAM，SD 不自动挂载。

`--initrd` 与 `--devices` 分开运行：前者验证 PID 1、管道/子进程、临时文件和 loopback，后者验证 native 外设。修改 rootfs 可用 `build busybox --rootfs-only`，配置或二进制变化则完整构建。每次验收关联实际 kernel/initrd/OSB，不保存本页的固定临时路径或旧镜像哈希。

## 驱动资格与限制

内置最小系统的 `linux-mini>` 是 freestanding init 的命令循环，支持 `help/uname/cpu/mem/uptime/test/fb/fbstat/devices/network HOST/reboot`。另有下述外部 BusyBox initramfs，提供真正的 ash shell。当前已实现原生 IRQ/UART、SD/MMC、Ethernet ring DMA、USB full-speed PIO host、ALSA 音频/麦克风、GPIO/input/LED/WS2812、SPI 小屏及只读 Flash MTD。各项实板资格需看对应 operation 的实际结果，不能把驱动编译或固件 TFTP 当成 Linux 设备验收。

`board linux --devices` 在用户态与 fb0 回归后执行 Linux UDP echo 和设备自检。主机 echo 只监听指定接口地址与分配的 UDP 端口；固件 TFTP 或 BIOS 的 USB 通过不能替代 Linux 驱动验收。

Flash MTD 只读，不提供擦除/编程。SD 读写支持与已执行的只读测试分别记录。USB Host 支持 full-speed control/bulk/interrupt，低速与 isochronous 请求拒绝，实际 HID/storage 绑定独立验收。麦克风是512帧有限快照，由驱动排空并重启，不等同连续无缝 DMA 录音。静音播放检查不构成听音验收，GPIO 注册不构成人工按键验收，framebuffer 写入也不构成实际画质验收。

`mprotect` 自检覆盖改为只读后读取及解除映射，尚未主动写只读页或验收所有权限/页故障组合。未进行长时间 Linux 压力测试、SD rootfs 挂载、断电冷启动或持久安装。内核配置未启用 strict kernel RWX；CPU 也没有 PMP，因此不是安全隔离验收。

构建脚本、配置和操作入口随移植代码提交；普通代码提交不发布新版本。恢复现役 BIOS 可运行 `./mini.ps1 board run --build current`，仅进行 SRAM/UART 临时装载。

上游依据：[RISC-V boot requirements](https://www.kernel.org/doc/html/latest/arch/riscv/boot.html)、[kernel.org 6.12.112 源码](https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.112.tar.xz)、[源码校验值](https://cdn.kernel.org/pub/linux/kernel/v6.x/sha256sums.asc)。


## 验收依据

历史启动、USB/BusyBox 和显示修复的准确镜像、测试范围见 `reports/changes/` 与 `reports/performance/` 中的 Linux 报告。新组合独立检查中断、DMA/cache 所有权、根端口状态与显示交接，不能凭版本号继承通过标记。
