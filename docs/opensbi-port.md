# OpenSBI 平台与 OS 引导

BIOS 继续提供原有 M-mode 裸机服务；OSB1 镜像增加一次性的 OS 交接，由 OpenSBI 接管 M-mode，再进入 S-mode payload。
平台、兼容补丁和 S/U 探针分别位于 [platform](../firmware/opensbi/platform)、[patches](../firmware/opensbi/patches) 和 [probe](../firmware/opensbi/probe)。Rust 多任务内核尚未实现。

## CPU 与中断

full 使用 VexRiscv linux CSR 配置：RV32IMAF、M/S/U、Sv32、2 KiB I/D-cache、4 KiB L2、sys 60 MHz / DDR CK 120 MHz。
[CPU 生成器](../scripts/cpu_generate.py) 为 linux 配置初始化真实 `misa`：MMU+FPU 为 `0x40141121`，无 FPU 为 `0x40141101`；不宣称 C/B/D 扩展。
Linux cached DBus 支持 AMO 和 LR/SC；OpenSBI 编译 ISA 使用 `rv32ima_zicsr_zifencei`、ILP32。

CPU 不提供硬件 time/timeh，OpenSBI 按 60 MHz 机器定时器模拟它们。
生成器在本地 VexRiscv 副本中为 `mcounteren/scounteren.TM` 保留可读写的权限位，即使硬件 time 端口关闭。
原来的条件映射会让这两个位恒为零，导致 OpenSBI 拒绝模拟 `rdtime`；不能靠跳过权限检查来解决。

MMU 配置启用独立 64 位 CPU timer：计数器每个 sys 周期加一，latch CSR 获取一致的时间值并提交 comparator，输出接 MTIP。
平台使用 SBI TIME 清除/重设 STIP；timer0/1 仍作为应用外设，机器定时器不占外部 IRQ 编号。
Supervisor IRQ mask/pending CSR 分别为 `0x9c0` / `0xdc0`，machine 为 `0xbc0` / `0xfc0`。
外设 SEIP 直接交给 S-mode，没有新增 PLIC；IRQ 编号沿用系统映射。

## 上游兼容

固定 OpenSBI revision `6ad246a1494807ee91e36ffcf60e4c30bb309d45`（版本输出 v1.9），VexRiscv 固定 `b6118e5cc2a33323425df6455697139021d50c72`。

上游启动与 trap 入口的 `CLEAR_MDT` 在 RV32 访问 `mstatush`。本 CPU 没有 Smdbltrp/mstatush，首次启动在 `0x41000188` 触发非法指令后停在早期 trap loop。
`no-mdt.patch` 增加平台条件，只有声明 `FW_MDT_UNSUPPORTED` 的本平台跳过该操作；其他平台保留上游行为。
构建器检查补丁是否已经应用，拒绝与固定版本不符的源码。

LLVM 23 的 soft-float 构建会对上游 `sbi_trap_ldst.c` 的浮点分支发出 `sometimes-uninitialized` 告警。
本平台只将这一类告警降级，其他编译告警继续作为错误；未验收浮点非对齐访存的模拟路径。
内核需保存 FPU 上下文并自行定义浮点异常策略，探针不代表已完成 FPU 多任务切换。

## 平台服务

平台注册轮询 LiteX UART console、60 MHz timer 和 warm reboot。无 shutdown 电源控制，不宣称关机能力。
单 hart 不需要 IPI 硬件。S-mode 接管设备后，不能再调用 BIOS 的 M-mode ECALL ABI。
设备树描述 CPU/ISA/Sv32、128 MiB RAM、timebase、UART、supervisor IRQ 的自定义 CSR 接口和固件保留区。
`riscv-mini,vexriscv-supervisor-irq` 是本机接口，不是标准 PLIC 设备绑定。

## 镜像与交接

| 区域 | 地址 / 范围 |
| --- | --- |
| OpenSBI 入口 | `0x41000000` |
| 固件 RW 起点 | `0x41040000` |
| 嵌入 DTB | `0x41080000` |
| S-mode 探针入口 | `0x41100000` |
| OSB1 装载 / 清零窗口 | `0x41000000..0x411fffff` |
| DTB 固件保留区 | `0x41000000..0x410fffff` |
| 探针测试用 U-mode 物理页 | `0x41400000`、`0x41402000` |

OSB1 头沿用 32 B RPB1 布局与 CRC32，magic 为 `0x3142534f`；普通 RPB1 为 `0x31425052`。
BIOS 检查格式/版本/范围/CRC，复制与清零，维护 cache，然后停止音频/麦克风、USB、网络、视频和相关事件源。
[汇编入口](../firmware/bios/enter.S) 清理 M/S IRQ mask、mie 与 satp，执行 sfence，传入 hartid=0 后跳入 OpenSBI。
FW_PAYLOAD 内部提供 DTB 与下一阶段地址。OSB1 不支持返回 BIOS；RPB1 仍保留原来的可返回契约。

当前使用 FW_PAYLOAD；也生成 FW_DYNAMIC，但 BIOS 尚未提供 dynamic-info 分别装载内核的入口。
二进制包括固定地址布局中的空洞，因此文件约 1 MiB，明显大于 OpenSBI 的运行时占用。
引导进度和各阶段计时见 [BIOS](bios.md)。

## 构建与测试

先按 [CPU 配置](configuration-profiles.md) 生成更新后的 MMU/FPU RTL，再构建匹配的 SoC；不能继续使用旧缓存的 CPU 文件。
构建路径可以自行选择，下例使用独立候选目录以保留 Flash 中的已验收版本：

```powershell
& $MiniPython scripts/build.py --cpu-rtl-dir build/opensbi/cpu --output-dir build/opensbi/soc-time --synthesize
./scripts/opensbi_build.ps1 -SocDir build/opensbi/soc-time
& $MiniPython scripts/boot_upload.py --output-dir build/opensbi/soc-time --program --mode uart
& $MiniPython scripts/opensbi_verify.py --host-ip 169.254.25.153 --reset-bios build/opensbi/soc-time/firmware/app.img
```

PowerShell 包装器负责准备文件、直接调用 WSL LLVM/make/dtc，再生成 OSB1。
当前 WSL archlinux 使用 clang/lld/llvm 23.1.1、dtc 1.8.1；Java/sbt、Gowin、BIOS 编译和硬件下载在 Windows 执行。
WSL 只是构建工具环境，SBI/内核没有运行时依赖。Python 子进程直接启动 WSL 在本机返回 E_ACCESSDENIED，故使用 PowerShell 包装器。
Windows checkout 的脚本/carray 会统一为 LF，避免 WSL 的 shebang 和生成代码受到 CRLF 影响。

`opensbi_verify.py` 实时输出 UART 进度并检查 Base、time CSR、AMO/LRSC、S timer/WFI、S external IRQ、U ECALL/time、Sv32 页故障。
`--reset-bios <app.img>` 在通过后请求 SBI warm reset，并经 UART 恢复匹配 BIOS。
`--sd` 先用 fetch 创建新的 `SBI1.OSB`，再从 SD 引导；文件已存在时明确失败，避免覆盖用户数据。
`--sd-existing SBI1.OSB` 使用卡上已有镜像，不运行 TFTP、不写卡，适用于驱动和 libc 的引导回归。
TFTP 服务器只导出一个镜像，不修改网卡、路由或防火墙。

## 当前验收状态

默认 full（MMU+FPU、SD lite、全部外设、L2 4 KiB）使用 place=4 / route=2，setup/hold 违例均为 0。
实板已完成网络及 SD 的 OSB1 引导、SBI Base、S/U time CSR、AMO/LRSC、10 次 S timer/WFI、10 次 S external IRQ、U ECALL 和三项 Sv32 拒绝访问检查。
累计 5 次完整 SBI warm reset 后重新训练 DDR、进入 ROM，并通过 UART 恢复匹配 BIOS；普通 RPB1 demo 连续两次运行并返回，POST/DDR 检查通过。

ROM 为 4092 B，BSRAM 43/46，PLL 3/4。新增失败状态输出不使用 DDR 栈；下载工具收到明确 DDR 失败消息立即结束等待。
libc 字访问优化和 SD 默认 15 MHz 读取后，实板读取 1,051,856 B 镜像约 1.33 秒；同次引导 CRC 约 0.77 秒，复制约 0.32 秒、清零/缓存同步约 0.22 秒。TFTP 实测约 7.3–9.5 秒，尚未针对协议吞吐优化。
CRC 标准向量、分段及边界长度通过主机测试；SD/TFTP 进度均已实际运行。

有些其他布局布线组合出现 DDR 初始化失败或无法布通，精简配置也曾在 SBI 复位后训练失败；其根因尚未完全查明。
本轮没有修改 PHY/DLL 复位逻辑，不能宣称所有布局和复位情况都已修复。验收仅适用于上述 full 配置，不能从静态时序通过推断 DDR 训练必然成功。
当前板上以 SRAM/UART 运行新配置并停在 BIOS TTY；Flash 尚保留此前的配置和 BIOS，需要单独更新。

## 保护与验收边界

CPU 无 PMP。Sv32 可以阻止 U-mode 访问 supervisor 页、MMIO 和写 RX 代码页；不能保护 OpenSBI 免受 S-mode 内核写入。
后续内核必须保留 OpenSBI、DTB、BIOS遗留工作区与 framebuffer，建立自己的内存所有权和设备驱动。
本机探针覆盖单 hart 的核心特权路径；它不证明 Rust 调度器、跨进程隔离、FPU 上下文保存、Linux 启动或所有 SBI 扩展可用。
硬件下载和固件验收使用 SRAM/UART，Flash 更新是独立操作。
