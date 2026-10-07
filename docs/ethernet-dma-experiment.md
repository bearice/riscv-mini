# 关闭音频后的 Ethernet DMA 实验

2026-10-07，基于 `da9a3d8` 的独立分支 `codex/eth-dma-no-audio`。用户要求提交保存以供日后参考，暂不合并；本次不是组件或系统发布，版本和 CHANGELOG 不递增。

当前提交保存 native DDR packet copy DMA：Ethernet 的读写端口与 SD 一样使用 32-bit MemoryPort，端口各有 16 B 缓冲，经 SharedL2 到 LiteDRAM；MAC packet SRAM 侧仍为 32-bit Wishbone。CPU 逐包提交，HAL 同步等待，没有自动 descriptor ring，也没有 MAC 直接访问 DDR。默认 eth_dma 关闭，实验显式启用并关闭音频输入和输出。

## 配置与复现

共同配置：RV32IMAFC + Sv32/FPU，复用 v0.7.1 的 VexRiscv_MmuFpu.v；sys 60 MHz、DDR CK 120 MHz，ROM/L2 各 4 KiB，SD lite、LCD、USB ultra、音频 DDS 选项。CPU RTL SHA256、完整参数、源码输入指纹、候选 FS / app image SHA256 和捕获 recipe 见 [机器记录](../reports/experiments/ethernet-dma.json)。

在仓库根目录、依赖与工具已配置的环境下，当前 native 候选的构建命令为：

```powershell
.venv/Scripts/python.exe scripts/build.py --profile full `
  --cpu-verilog <VexRiscv_MmuFpu.v> --rom-size 4096 --l2-size 4096 `
  --sd-profile lite --usb-backend ultra --audio-clock dds `
  --without-audio --without-mic --with-eth-dma `
  --place-option 2 --route-option 2 --synthesize --output-dir build/eth-native-final
```

CPU 输入须匹配机器记录中的 SHA256，不能用名称相同但内容不同的 CPU RTL 替代。当前 source 可通过 --without-eth-dma 构建 PIO；先前 Wishbone DMA 实现已被 native 实现替换，其复现需要先前捕获 recipe 的源码，不能仅切换当前 feature flag。

## 已保存结果

| 构建 | DDR 路径 | 音频输入/输出 | Logic / CLS / BSRAM | setup/hold | 实板 |
| --- | --- | --- | --- | --- | --- |
| no-audio-control | Ethernet PIO | 关闭 | 18656 / 10077 / 34 | 0/0 | 通过 |
| eth-dma-final | Ethernet Wishbone copy DMA | 关闭 | 19143 / 10159 / 34 | 0/0 | 通过，外部网络 payload 验证通过 |
| eth-native-final | Ethernet native copy DMA | 关闭 | 19539 / 10217 / 34 | 0/0 | 失败，SD 初始化未通过 |

每个 candidate 的完整硬件与镜像身份见机器记录，未将失败候选标为 board-qualified。native 相对无 DMA 的控制构建增加 883 Logic、559 Register、140 CLS，BSRAM 不变；CLS 剩余 151/10368。容量与 PnR 通过不能代替实板验收。

Wishbone 对照做了 30 秒外部验证，200 个 UDP echo payload（长度 0、1、31、32、63、64、255、511、1024、1472）通过；HAL DMA RX/TX 计数分别为 169/164。控制组与 Wishbone 组各三轮 bench all/cache 的身份核对与中位数对比见 [性能报告](../reports/performance/eth-wishbone-vs-no-audio-control.md)。该报告没有测量 native 候选，也不是网络吞吐 benchmark。

## native 实板失败与诊断

place=2 / route=2 候选两次重新下载 SRAM 后均在 SD 初始化 ACMD51 失败，DMA error/done 都为 1。CPU、MMU、FPU、ISA、L2、fence、UART、IRQ、DDR、Flash 检查先行通过；没有继续把这个候选当成完整通过。

另行构建的诊断 BIOS 仅通过 UART 装入 DDR，不改正式候选 app image，也不写 Flash。在 SD RX DMA 关闭时写入不同 base pattern，读回始终多出 bit 27：

```text
pattern 00000000 -> SD RX 08000000
pattern 40000000 -> SD RX 48000000
pattern 408135b0 -> SD RX 488135b0
pattern a5a55a5a -> SD RX ada55a5a
```

同次测试的 Ethernet memory CSR 和 SD TX base CSR 均正确读回。异常地址超出合法 DDR 范围，解释了当前 DMA 地址检查失败。生成 RTL 和综合后的 base[27] 触发器连接检查未发现明确错误；根因尚未定位，不能据此断言是 Gowin 工具或物理硬件问题，也未放宽地址检查掩盖问题。

同源码仅改为 place=3 / route=2 的尝试在 PnR 失败：`ERROR (PR0004): There are total 182 unrouted nets`。它没有产生通过验收的替代候选。

## 回归与保存边界

提交前重跑 feature config、真实共享内存控制器的 SD/ETH 联合测试、SD 仿真、生成 Verilog 的 SD / 内存控制器 / LCD / Ethernet DMA 测试及工作流回归。Ethernet 测试覆盖双向 payload、短尾字、1514/1536 B、延迟 ACK、非法描述符、取消排空和重启；真实控制器覆盖 SD 与 ETH 写缓冲的维护屏障。主机/RTL PASS 不改变上述实板失败状态。

提交是参考保存；不合并到 main，不创建发布 tag，不切换 current，不重新编程板子。已有构建、捕获 recipe、原始 UART 日志、对照组基准及诊断 BIOS 保留在本 worktree 的 build 目录，Git 中保存设计、复现参数、结果身份与失败摘要。当前 native 候选没有 all/cache 或外部网络验收结果。不得借用带音频 memory-ports 分支或 Wishbone 对照的成功结果证明它已稳定。
