# U-Boot native Ethernet ring

2026-10-09，`codex/eth-ring-dma-no-fpu`。默认 full profile 保留 MMU、关闭 FPU，显式 `--with-fpu` 仍可用。Ethernet DMA 默认关闭，使用 `--with-eth-dma` 选择独占 ring。

U-Boot 使用与 HAL 相同的 16-byte DDR descriptors，RX/TX 各四个、1536-byte buffer。硬件通过 buffered native DDR 端口收发，MAC packet SRAM 是私有 staging，不映射到 CPU。RX 借用 DDR buffer，`free_pkt` 归还所有权；TX 复制到受驱动管理的 DDR buffer，发布 descriptor 并等待硬件完成。停止及移除设备前排空 DMA，超时保留缓冲，避免硬件访问已释放内存。

驱动通过编译选项选择唯一后端。DMA 版二进制不包含 PIO compatible 或搬包循环；关闭 DMA 的独立 PIO 配置保留原功能。不存在 DMA 运行失败后转 PIO 的路径。`uboot_build.py --soc-dir <构建目录>` 从 CSR 选择后端及早期 UART 地址；OpenSBI 从相同构建生成 Ethernet/USB 中断、ring register offsets 和实际 CPU ISA。

组件版本：RTL/HAL/BIOS 0.9.0、OpenSBI 0.10.0、U-Boot 0.4.0；bootloader 仍为 0.8.0。系统包及正式 current 仍是 v0.8.0。本分支作为独立参考，不合并或推送。

## U-Boot 候选验收

基于干净的 upstream v2026.07 ext4 checkout 应用 port，patch reverse-check 通过。ring 版 413259 bytes；PIO 对照 412379 bytes，二进制的 compatible 与编译配置互斥。

在先前已验收的无 FPU ring gateware 上，通过 BIOS TFTP 进入 OpenSBI/U-Boot。ping 成功；两次下载各 262145 bytes，block size 512/1428，CRC32 均为 `20a77f7c`，重传均为零。LCD framebuffer 位于 `0x07fc0400`，计数前进，289 个非零 framebuffer word，地址错误和 underflow 为零。独立初次运行还成功识别四位 SD 和三只 USB 设备。

第一次 CRC 验证将 `==> ` 当成提示符，提前结束读取；修正为 U-Boot 完整提示符后两次传输均通过。候选结果：`build/uboot-ring-candidate/verify-prompt-fixed2-uart.json`。正式提交后的产物与验收另存最终 archive，候选不能替代 clean tree 最终产物。

没有满 100 Mbps、长期突发压力或物理屏幕观察结论；TX descriptor 完成表示 MAC SRAM reader 已消费，不表示对端已经收到。

## 提交候选 gateware

`build/ring-uboot-qualified` 按默认 full 配置选择无 FPU，启用 Ethernet DMA；没有传 `--without-fpu`。ISA 为 RV32IMA/Zicsr/Zifencei，音频及双麦克风保持启用。源码指纹 `1ae7edaf2cbd7695408542c73b7ec18701df4d6752304e6bd21ff26ee7ace4be`，PnR setup/hold 0/0，Logic 18885、Register 10370、CLS 10120、BSRAM 36。50 项固件检查和 60 秒 soak、双麦克风通过；证据位于 `build/operations/20261009T084949110476Z-verify`。性能对照上一版已验收的独占 ring，在 `build/ring-uboot-performance` 保存 all/cache 各三轮。

缓存压力测试的 LCD underflow 为 24，基线同为 24，普通 all 测试为零。第一次直接接续外部并发检查在 `test lcd start` 失败，因为压力测试累计计数尚未清零；自主 ring hold 已通过。保留失败现场于 `build/ring-uboot-evidence/cache-carryover`，重新装载相同 FS/app 后验证普通并发，不能把重载后的零计数用于掩盖压力测试结果。

重载后的外部并发检查通过：CPU 暂停收包 1 秒，四帧自主入环并按序回包；ARP/ICMP、30.09 秒 110 个 UDP 包、37983 payload bytes、零超时，最大 RTT 6.77 ms，普通 LCD/audio 并发无新增错误。最终提交前的 FS/app 身份与上述源码匹配，正式 current 未变。
