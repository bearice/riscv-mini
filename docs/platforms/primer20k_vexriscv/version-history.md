# 20K 发布里程碑

## 合并版本时代（0.1.0–0.7.1）

0.7.1 及之前所有组件共享一个版本号。以下是 2026-10-06 从完整 Git log 建立的功能阶段映射，并已为每个阶段补建 annotated tag。v0.7.1 与 v0.7.0 都是带实板验收的正式代码发布，v0.7.1 是合并版本时代的最后一版；每个包额外保存完整 Git hash。

| 追溯版本 | 功能阶段末提交 | 代码能力依据 |
| --- | --- | --- |
| 0.1.0 | 88ece43 | RV32IM 最小系统、DDR/LCD 60/120 MHz 并发、Flash/UART boot |
| 0.2.0 | 60b42a4 | HAL、Dock IO、SD/FatFs、音频 DMA、Ethernet、USB HID、双麦克风、模块化配置 |
| 0.3.0 | f47e5f2 | full MMU/FPU、紧凑 SD/USB/DDS 音频、硬件 DDR boot、初始共享 L2 |
| 0.4.0 | ca78a2d | 常驻 BIOS、SD/TFTP boot、USB hub、演示、OpenSBI、benchmark、libc/SD 优化 |
| 0.5.0 | ff84bfb | 原生 crossbar、快速 L2 refill/burst、CPU fence handshake，write-through 阶段 |
| 0.6.0 | f0892da | CPU/DMA 共享一致性 writeback，L2 RAM boot stack；无 C、8 KiB ROM，历史 writeback 基线 |
| 0.7.0 | 6880dee | C 扩展、4 KiB ROM、原生 fence、移除外部 fence/write-combine、DDR 深度 1 与注册路径 |
| 0.7.1 | 功能提交（代码+VERSION bump+changelog 同提交）；v0.7.1 tag 指向其后的变更记录提交 | ROM/BIOS/`status` 打印构建身份（semver+commit+config 指纹、RTL 与 ROM 版本），`validation.json` 记录 `build_id`/`rtl_sha256`/`config_sha256` |

## 组件版本

后续版本按组件记录在仓库根目录 `VERSIONS.yaml`，系统包组合记录在 `RELEASES.yaml`，功能发布见 [CHANGELOG](../../../CHANGELOG.md)。版本标签不证明当前构建或板测通过；准确身份与证据通过构建 catalog、release 清单和报告查询。

历史实现使用 Git 中对应版本的源码与配方，不将旧构建目录或资源数字当作当前默认值。
