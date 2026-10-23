# OpenSBI 平台与 OS 引导

> 适用机器：`primer20k_vexriscv`。配置以机器 JSON 和所选硬件产物的导出为准。

Linux 6.12.112 已在 v0.9.1 RV32IMA/Sv32 实板进入用户态并通过内存、进程和定时器检查，最新路线与边界见 [Linux 最小系统移植](linux-port.md)。下文 SBI 探针验收仅证明其列出的检查，不能代替 Linux 的独立验收。

BIOS 继续提供原有 M-mode 裸机服务；OSB1 镜像增加一次性的 OS 交接，由 OpenSBI 接管 M-mode，再进入 S-mode payload。
平台、兼容补丁和 S/U 探针分别位于 [platform](../../../firmware/opensbi/platform)、[patches](../../../firmware/opensbi/patches) 和 [probe](../../../firmware/opensbi/probe)。Rust 多任务内核尚未实现。

## CPU 与中断

full 使用 VexRiscv linux CSR 配置：RV32IMAF、M/S/U、Sv32、2 KiB I/D-cache、4 KiB L2、sys 60 MHz / DDR CK 120 MHz。
[CPU 生成器](../../../scripts/builders/cpu_generator.py) 为 linux 配置初始化真实 `misa`：MMU+FPU 为 `0x40141121`，无 FPU 为 `0x40141101`；不宣称 C/B/D 扩展。
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
| OpenSBI 入口 | `0x01000000` |
| 固件 RW 起点 | `0x01040000` |
| 嵌入 DTB | `0x01080000` |
| S-mode 探针入口 | `0x01100000` |
| OSB1 装载 / 清零窗口 | `0x01000000..0x011fffff` |
| DTB 固件保留区 | `0x01000000..0x010fffff` |
| 探针测试用 U-mode 物理页 | `0x01400000`、`0x01402000` |

OSB1 头沿用 32 B RPB1 布局与 CRC32，magic 为 `0x3142534f`；普通 RPB1 为 `0x31425052`。
BIOS 检查格式/版本/范围/CRC，复制与清零，维护 cache，然后停止音频/麦克风、USB、网络、视频和相关事件源。
[汇编入口](../../../firmware/bios/enter.S) 清理 M/S IRQ mask、mie 与 satp，执行 sfence，传入 hartid=0 后跳入 OpenSBI。
FW_PAYLOAD 内部提供 DTB 与下一阶段地址。OSB1 不支持返回 BIOS；RPB1 仍保留原来的可返回契约。

当前使用 FW_PAYLOAD；也生成 FW_DYNAMIC，但 BIOS 尚未提供 dynamic-info 分别装载内核的入口。
二进制包括固定地址布局中的空洞，因此文件约 1 MiB，明显大于 OpenSBI 的运行时占用。
引导进度和各阶段计时见 [BIOS](bios.md)。

## 构建与测试

先在机器 JSON 启用匹配的 MMU/CPU 能力，生成硬件，再让 OpenSBI 消费同一硬件导出。

```sh
python scripts/mini.py build system --machine primer20k_vexriscv --synthesize
python scripts/mini.py build opensbi --hardware <20K硬件身份> --payload-path <应用bin>
```

工具环境与 native/WSL runner 由构建入口调度；重复构建保留同一工作区。镜像位置从 run 结果读取，不使用旧 `-SocDir` 包装参数。临时装载与恢复见 [板端操作](operations.md)。

`opensbi_verify.py` 实时输出 UART 进度并检查 Base、time CSR、AMO/LRSC、S timer/WFI、S external IRQ、U ECALL/time、Sv32 页故障。
`--reset-bios <app.img>` 在通过后请求 SBI warm reset，并经 UART 恢复匹配 BIOS。
`--sd` 先用 fetch 创建新的 `SBI1.OSB`，再从 SD 引导；文件已存在时明确失败，避免覆盖用户数据。
`--sd-existing SBI1.OSB` 使用卡上已有镜像，不运行 TFTP、不写卡，适用于驱动和 libc 的引导回归。
TFTP 服务器只导出一个镜像，不修改网卡、路由或防火墙。

## 验收范围

Base、time CSR、S timer/WFI、S external IRQ、U ECALL、Sv32 和 warm reset 分别检查；SD/TFTP、返回 BIOS 与 Linux 启动是不同资格。修改 CPU、布局、时钟或 PHY 后，必须匹配新镜像重新验证，不能从 PnR 通过推断 DDR 训练及复位恢复必然成功。

## 保护与验收边界

CPU 无 PMP。Sv32 可以阻止 U-mode 访问 supervisor 页、MMIO 和写 RX 代码页；不能保护 OpenSBI 免受 S-mode 内核写入。
后续内核必须保留 OpenSBI、DTB、BIOS遗留工作区与 framebuffer，建立自己的内存所有权和设备驱动。
本机探针覆盖单 hart 的核心特权路径；它不证明 Rust 调度器、跨进程隔离、FPU 上下文保存、Linux 启动或所有 SBI 扩展可用。
硬件下载和固件验收使用 SRAM/UART，Flash 更新是独立操作。
