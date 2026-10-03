# RV32GCB 综合资源实验（2026-10-03）

本实验不改变默认 CPU。G 是 IMAFD + Zicsr/Zifencei，包含双精度 D；
B 是 Zba/Zbb/Zbs，C 是压缩指令。定义见
[RISC-V G](https://docs.riscv.org/reference/isa/v20260120/unpriv/rv-32-64g.html) 和
[RISC-V B](https://docs.riscv.org/reference/isa/unpriv/b-st-ext.html)。

保留 Sv32 MMU、2 KiB I/D cache、迭代乘除/移位、原流水线配置。
单精度候选为 RV32IMAFC + Zba/Zbb/Zbs；完整候选为 RV32GCB。
双精度要求 64-bit load/store 与缓存数据通路，本地实验使用 LiteX
Wishbone DownConverter 将 64-bit 数据总线接回现有 32-bit SoC 总线。
以下 GCB 结果包含这个转换器；没有把 64-bit 信号截断到原总线。

## 仅 CPU 综合

相同器件、同一 Gowin V1.9.12.04，`run syn`，未加入 SDC，也未做 PnR。
这些数值只用于面积对照，不是 60 MHz 时序验证。

| CPU | Logic | Register | BSRAM |
| --- | ---: | ---: | ---: |
| 当前 RV32IMAF + MMU | 7,389 | 4,151 | 12 |
| RV32GCB + MMU + 64→32 converter | 11,901 | 5,870 | 19 |
| 差值 | +4,512 | +1,719 | +7 |

证据：`build/rv32gcb/cpu-only/{current,gcb}/impl/gwsynthesis/project_syn.rpt.html`。

## 全部外设 SoC 综合

SD lite、USB ultra、全部其他外设、48k DDS，生成 SoC 块层级，
60/120 MHz 约束；均仅执行综合。不要与最终 PnR 占用混用。

| CPU 能力 | Logic | Register | BSRAM | 综合结果 |
| --- | ---: | ---: | ---: | --- |
| 当前 F + MMU | 18,534 | 10,743 | 43 | 完成 |
| F + C + MMU | 18,630 | 10,744 | 43 | 完成 |
| F + C + B + MMU | 19,980 | 10,757 | 43 | 完成 |
| G + C + B + MMU | 23,066 | 未生成最终报告 | 未生成最终报告 | Logic 超过 20,736 上限 |

C 单独增加 96 Logic；B 在 C 核上再增加 1,350 Logic；D 及其宽通路/
转换器在 FCB 上再增加 3,086 Logic。完整 GCB 超出上限 2,330 Logic。
FCB 虽在综合容量内，尚未做布局布线，不能据此承诺能放进或跑到 60 MHz。
GCB 的器件容量检查在最终资源报告前终止，不用推算值冒充 Register/BSRAM 实测。

证据汇总：`build/rv32gcb/resources.json`；各候选的 `qualification.json`、
`gateware/impl/gwsynthesis/project.log` 和资源 HTML。当前 F 对照的综合报告位于
`build/rtl-diagnosis/blocks-route2/gateware/impl/gwsynthesis/`。

## 插件与重现边界

生成入口是 `scripts/cpu_gcb_generate.py`，输出 `build/rv32gcb/generator/`。
基于现有 `build/cpu-features/` 生成器，不修改依赖安装包或默认核。
B 插件来自 [rdolbeau/VexRiscvBPluginGenerator](https://github.com/rdolbeau/VexRiscvBPluginGenerator)，
revision `462707295d44f8a5387b636974217fec525a4e26`；上游说明是早期草案且未优化面积。
仅生成 Zba/Zbb/Zbs；将 BCLRI/BSETI/BINVI/BEXTI/RORI 的高 7 位固定为 RV32
编码，限制立即数到 0..31；去掉没有使用的第三源寄存器动作模板。
29 条标准 B 指令、34 个操作数组合匹配当前 GCC 编码，记录为
`build/rv32gcb/opcode-audit.json`。这只证明编码覆盖，不证明指令运算或 ISA 合规。

上游 C parser 在 GCC 的隐式函数声明错误上失败，构建时使用
`CFLAGS="-O2 -Wno-error=implicit-function-declaration"`。初次 Scala 编译因
未使用的 SRC3/RS3 模板失败，去掉模板后通过。双精度初次使用 32-bit
memory bus 时 cache elaboration 失败，改为 64-bit 内部 bus 和显式 converter
后生成通过。日志为 `plugin-build.log`、`generator/generate.log`；最终核/插件
SHA256 与配置记录在 `generator/generator.json`。

没有修改固件的 ISA/ABI，也没有将这些实验核下载或写入 Flash。尚未进行
B/D/C 指令语义、NaN、舍入、异常、随机合规、MMU 隔离或 RTOS 浮点上下文测试。
