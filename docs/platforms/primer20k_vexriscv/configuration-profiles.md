# CPU、SD 和音频配置

> 适用机器：`primer20k_vexriscv`。配置以机器 JSON 和所选硬件产物的导出为准。

硬件参数统一编辑 `gateware/machines/primer20k_vexriscv/machine.json`。
CLI 只选择操作、机器、软件源文件和工作区；不再维护 profile/feature 覆盖。
严格 schema、生成流程与两个平台的共同接口见 [平台构建配置](../../common/platform-descriptions.md)。

## CPU

`ip.cpu.parameters` 保存 MMU、单精度 FPU、压缩指令、I/D-cache 大小、移位/乘除及
取指流水线参数。`mmu` 和 `fpu` 独立选择；前端据此生成 CPU、推导 LiteX variant
和软件 ISA。每次实际消费的 RTL 与生成元数据配对校验，不通过文件名推断能力。
应用使用机器 JSON 中的 ABI；硬件能力不代表所有软件都使用该扩展。

修改参数后，构建入口自动重新生成受影响 IP：

```sh
python scripts/mini.py build cpu --machine primer20k_vexriscv
python scripts/mini.py build system --machine primer20k_vexriscv --synthesize
```

Java 8、sbt 和上游源码自动发现并记录身份。历史提交的 vendor RTL 保留用于旧产物
与配方，不作为当前机器配置的隐式后备。

## SD

| `rtl.sd_profile` | 控制器 | DMA / 文件系统 |
| --- | --- | --- |
| `none` | 无 SD | `features.sd` 和 `features.filesystem` 必须关闭 |
| `spi` | SPI SD | 软件读写，可选文件系统 |
| `lite` | 精简原生四位 | 有界 DMA，最多 8 扇区/4 KiB 一次 |
| `full` | 通用原生四位 | 较宽地址/长度的 DMA |

非 `none` 配置须同时开启 `rtl.features.sd`。文件系统依赖 SD，不能留下相互矛盾
的功能组合。HAL 的分块策略与硬件 DMA 能力分别由其实现拥有。

## 音频和其他设备

`rtl.audio_clock` 选择 `dds` 或 `legacy`。DDS 根据系统时钟计算步进，DAC 和麦克风
共用相位，平均采样率 48 kHz。改变构建时钟须重新构建，不支持运行时动态调频。
`rtl.features` 控制设备与驱动；`mic_stereo` 依赖 `mic`，`eth_dma` 依赖 `eth`。

`rtl.l2_size_bytes`、启动 ROM 大小、时钟及 PnR 选项也只通过 JSON 调整。
所有修改仍须通过机器能力与物理资源校验。

## 配置矩阵

```sh
python scripts/mini.py build matrix --configuration-only
```

矩阵把每个用例写入并校验源 JSON，串行使用相同工作区，结束时恢复原文件。
用例记录保存实际配置。矩阵可检查 CPU×SD 和音频组合的生成/编译；结果不代表
所有组合都通过 PnR 或实板验收。当前实板资格查询明确的 `current`/baseline。
