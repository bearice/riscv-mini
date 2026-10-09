# 每端口缓冲重构实验

2026-10-07，在基于 `da9a3d8` 的 `codex/buffered-memory-ports` worktree 完成一轮实现和验收。主 checkout 保持原状，改动尚未提交。接口与边界见 [memory-controller.md](memory-controller.md)，完整系统图见 [gateware-block-diagram.md](gateware-block-diagram.md)。

SD 读写接口各为 32 bit，LCD 接口为 16 bit；三个流式端口各有一个 16 B 缓冲。读 burst 存入端口缓冲后释放共同后端，窄口背压只阻塞本端口。写 burst 收齐窄字和 byte mask 后才参与仲裁，显式 completion 表示提交完成。CPU/audio 保留 32-bit Wishbone，SharedL2 为共同可见性点。取消排空已提出的内存请求；flush/invalidate/cache disable 等待已接纳写入提交。

## 构建与资源

构建：`build/buffered-ports-final`。CPU 输入复用原 current 的 `VexRiscv_MmuFpu.v`，RV32IMAFC + Sv32/FPU；sys 60 MHz、DDR CK 120 MHz、ROM/L2 各 4 KiB、SD lite、USB ultra、DDS audio、place/route 2/2。

| 项目 | 原 current v0.7.1 | 本次 | 变化 |
| --- | ---: | ---: | ---: |
| Logic | 19,797 | 19,918 | +121 |
| Register | 11,251 | 11,452 | +201 |
| CLS | 10,295 | 10,302 | +7 |
| BSRAM | 42 | 43 | +1 |
| rPLL | 3 | 3 | 0 |
| setup / hold 违例 | 0 / 0 | 0 / 0 | 0 |

本次资源没有减少。CLS 仅剩 66/10,368；接口隔离收益不能解释为面积优化。PRIMARY/LW 仍为 8/8。

`validation.json` 记录 bitstream SHA256 `570609605ae7984068ee68a512525aac840a7c256348358c990804e1562f9d2c`，app image SHA256 `ad4dbd4338e3ed8b47596a5f933ff0bcb6ea192695d9f558a0088c67c69dc998`。最终源码输入指纹与构建配置指纹一致。

## 验证结果

- 主机/RTL：config、SD 多长度/尾包/取消/公平性、真实 L2 + 端口缓冲联合测试、生成 RTL 的 SD / 整个内存控制器 / LCD 双帧检查通过。LCD 测试覆盖独立 sys/video 时钟、启动排空后两帧像素、帧界与无 underflow。
- 原回归：L2 writeback 与生成 Verilog 的多配置测试、4/8 KiB boot RAM、DDR queue/structure、DDR boot/DFI、RTL blocks/defaults 通过。
- 构建覆盖：minimal RTL/固件编译通过；SD full 回退路径 RTL 生成通过。实板和完整 PnR 对象为上面的 full + SD lite 配置。
- 实板：`firmware_verify.py --output-dir build/buffered-ports-final --program --soak-seconds 60 --mic` 通过，49 次检查涵盖 L2、fence、DDR、SD、LCD 启停/清屏、native DMA、USB 重启、音频与双麦克风。gateware 使用 SRAM 下载，BIOS 使用 UART 装入 DDR。
- 60 秒并发 soak 完成 42 轮，SD CRC 与 LCD CRC 一致；LCD underflow、audio underrun/overrun/error、USB error 均为 0。证据在构建目录的 `firmware-verification.json` 与 `firmware-verification-uart.log`。
- 三轮 `bench all` 与三轮 `bench cache` 全部通过，结束后的 BIOS 与音频回归通过。原始数据在 `build/reports/performance/buffered-ports/{all,cache}`，完整 Markdown/JSON 对比见 [性能报告](../reports/performance/buffered-memory-ports.md)。

## 性能与边界

与 v0.7.1 已保存的同配置三轮中位数相比，SD 512 B 读 0.596 → 0.615 MiB/s，4 KiB 读 1.888 → 1.935 MiB/s；1 MiB 内存读 8.318 → 8.305 MiB/s，写 8.688 → 8.571 MiB/s。cache L2 读约 55.2 cycles/access、冲突写约 60.3 cycles/access，基本持平。这些小变化不构成显著吞吐提升结论。

基线 BIOS 为 v0.7.1，本次为 v0.7.2；benchmark.c 和 firmware/hal 无源码差异，但整体固件布局不同，且没有本次补测基线，因此只能作版本对比。端口目前每次最多一个 outstanding burst；接口仍以 16 B 对齐命令为单位，不支持任意未对齐起点。端口缓冲是已接受请求的快照，不作为可重复命中的缓存；CPU 私有 L1 仍需软件维护。

没有人工观察屏幕、主机听音、物理输入验收或网络吞吐测试；没有执行 SD 文件创建测试。验收当时使用 SRAM gateware / UART DDR BIOS，Flash 未改写，主仓库 current/baseline 引用未改变。后续 Ethernet 实验重新下载了板子，本记录不表示该候选当前仍在运行。

本页仅记录独立 `memory-ports` 分支的先前验收，不能用于证明本分支的 Ethernet native DMA 已验收；当前实验状态见 [Ethernet DMA 实验](ethernet-dma-experiment.md)。
